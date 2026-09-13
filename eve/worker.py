"""The run worker: everything a run does with the radio, in its own process.

Why a process: a second uhd.usrp_source for the same B210 in one process crashed the
program in the constructor (access violation, 2026-09-12, twice on the desktop) however
carefully the first one was released, and reconfiguring one source across waveforms
gave a sample rate 0.37 % off and a retune 10 kHz low. A fresh process per run gets a
fresh UHD every time, and a crash in the radio code cannot take the window down.

The worker talks to the application over a multiprocessing pipe with small tuples:
  ("log", text)                     a line for the session log
  ("state", name)                   preparing | running | decoding | idle
  ("preview", text)                 the schedule description (preview_only)
  ("session", payload)              schedule dict, time offset, radio text: bind the panel
  ("frame", k, metric_bytes)        one frame's metric (float32[M]) for the live view
  ("status", dict)                  phase, keyed, radio text, GPS clock text, ephemeris text
  ("finished", result)              {"ok", "pdf", "session_id", "summary", "error", "aborted"}
and receives ("abort", reason).

`run_job(cfg, preview_only, conn)` is the process target; `Runner` is the same logic
usable in-process (the tools and tests).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from .params import EveParams
from . import doppler as D
from . import schedule as S
from .doppler import iso_utc

PREFIX = {"sim": "SIM", "bench": "BENCH", "interop": "INTEROP", "eme": "EME", "eve": "EVE"}
TARGET = {"sim": "sim", "bench": "bench", "interop": "partner", "eme": "moon", "eve": "venus"}


class Runner:
    def __init__(self, send, poll_abort=None):
        self.send = send                    # callable(tuple)
        self.poll_abort = poll_abort        # callable() -> Optional[str]
        self.session = None

    def say(self, s: str) -> None:
        self.send(("log", s))

    def phase(self, text: str, kind: str = "busy") -> None:
        """What the operator's badge says. kind: run | busy | ok | fail."""
        self.send(("status", {"phase": text, "kind": kind}))

    # ---- the job -------------------------------------------------------------------------
    def run(self, cfg: Dict, preview_only: bool = False) -> Dict:
        radio = None
        gps_text = ""
        result: Dict = {"ok": False, "pdf": None}
        try:
            self.send(("state", "preparing"))
            mode = cfg["mode"]
            p = EveParams.named(cfg["variant"])
            if not cfg["full_symbol"]:
                p = replace(p, n_frames=int(cfg["n_frames_test"]), pilot_frames=int(cfg["pilot_frames_test"]))
            f_dial = float(cfg["f_dial_mhz"]) * 1e6
            archive = Path(cfg["archive"])
            archive.mkdir(parents=True, exist_ok=True)
            site = D.DSES_HASWELL

            want_gps = mode != "sim" and cfg["gpsdo"] and cfg["clock"] == "external" and not preview_only

            # radio
            if mode == "sim":
                from .station import SimRadio
                radio = SimRadio(p, cn0_db=float(cfg["sim_cn0"]), seed=int(cfg["sim_seed"]))
                now = time.time()
            elif preview_only:
                now = time.time()
            else:
                from .radio import EveRadio, RadioConfig
                rc = RadioConfig(serial=cfg["serial"], f_dial_hz=f_dial, tx_gain_db=float(cfg["tx_gain"]),
                                 rx_gain_db=float(cfg["rx_gain"]), clock_source=cfg["clock"],
                                 time_source="host" if cfg["time_host"] else None,
                                 require_ref_lock=(cfg["clock"] != "internal"), lo_offset_hz=float(cfg["lo_offset_khz"]) * 1e3)
                radio = EveRadio(p, rc)
                self.send(("state", "opening the radio"))
                time.sleep(2.0)                 # let the USB stack settle after the previous worker's close
                radio.create()                  # USB device open first (see EveRadio.create)
                if want_gps:
                    from . import gpsdo
                    rep = gpsdo.preflight()
                    gps_text = f"{rep['config']} | locked {rep['locked']}"
                    self.say("GPS clock: " + gps_text)
                    if not rep["locked"]:
                        raise RuntimeError("GPS clock not locked; refusing to start")
                    time.sleep(0.5)
                st = radio.configure(p, rc)
                self.say(st.summary())
                self.say(f"rate error {radio.rate_error_ppm():+.3f} ppm; PPS verify {radio.verify_pps()}")
                now = radio.device_time()

            # start time and ephemeris
            lead = float(cfg["lead_s"])
            if mode in ("eme", "eve", "interop") and not cfg["sky_start_now"]:
                t_start = D.to_unix(cfg["sky_start_utc"])
                if t_start < now + 12.0:
                    raise RuntimeError(f"start {iso_utc(t_start, 0)} is in the past or too close; use 'start now' or a later time")
            else:
                t_start = now + lead
            sid = f"{PREFIX[mode]}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(t_start))}"
            table_name = ""
            if mode == "sim":
                model = D.DopplerModel(D.synthetic_table("sim", site, now - 60, now + 8 * 3600, range_km=float(cfg["sim_range_km"])))
            elif mode == "bench":
                model = D.DopplerModel(D.synthetic_table("bench", site, now - 60, now + 8 * 3600, range_km=float(cfg["bench_range_km"])))
            elif mode == "interop":
                model = D.DopplerModel(D.synthetic_table("partner", site, now - 60, now + 8 * 3600, range_km=0.001))
            else:
                target = TARGET[mode]
                table_path = archive / f"{sid}_{target}_haswell.csv"
                self.say(f"ephemeris for {target} from {cfg['sky_source']} ...")
                model = D.make_model(target, site, t_start - 1800, t_start + 8 * 3600, source=cfg["sky_source"],
                                     table_path=None if preview_only else table_path, step="1 m")
                table_name = table_path.name if not preview_only else ""
                el = model.elevation_deg(t_start)
                self.say(f"{target} at start: elevation {el:.1f} deg, RTT {model.rtt_s(t_start):.2f} s, "
                         f"Doppler {model.doppler_hz(t_start, f_dial):+.1f} Hz")
                if el < 0:
                    raise RuntimeError(f"{target} is below the horizon at the start time (elevation {el:.1f} deg)")

            # schedule
            common = dict(params=p, text=cfg["message"], repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"])
            if mode == "sim":
                if model.rtt_s(t_start) > 0.6:
                    sched = S.build_schedule(sid, "sim", model, t_start, f_dial, chunk_s=None, rtt_guard_s=0.1,
                                             t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0, **common)
                else:
                    sched = S.build_schedule(sid, "sim", model, t_start, f_dial, chunk_s=float(cfg["bench_chunk_s"]),
                                             rtt_guard_s=0.0, t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0,
                                             mode="bistatic_tx", **common)
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False,
                               start_margin_s=1.0, realtime_mode=False)
            elif mode == "bench":
                sched = S.build_schedule(sid, "bench", model, t_start, f_dial, chunk_s=float(cfg["bench_chunk_s"]),
                                         rtt_guard_s=0.0, t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0,
                                         mode="bistatic_tx", **common)
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False, start_margin_s=2.0)
            elif mode == "interop":
                rx_only = cfg["interop_dir"].startswith("Receive")
                sched = S.build_schedule(sid, "partner", model, t_start, f_dial, chunk_s=float(cfg["interop_chunk_s"]),
                                         rtt_guard_s=0.0, t_off_min_s=0.0, t_on_max_s=3600.0,
                                         mode="bistatic_rx" if rx_only else "bistatic_tx",
                                         notes=("receive only: partner transmits" if rx_only else "transmit only: partner receives"),
                                         **common)
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False, tx_enabled=not rx_only)
            elif mode == "eme":
                sched = S.build_schedule(sid, "moon", model, t_start, f_dial, mode=cfg["sky_mode"],
                                         chunk_s=float(cfg["eme_chunk_s"]), rtt_guard_s=float(cfg["eme_rtt_guard"]),
                                         t_on_max_s=float(cfg["eme_t_on_max"]), t_off_min_s=float(cfg["eme_t_off_min"]),
                                         doppler_table=table_name, **common)
                opts_kw = dict(pa_in_chain=bool(cfg["eme_pa"]), tx_precompensate=bool(cfg["sky_precomp"]),
                               rx_doppler_removal=bool(cfg["sky_rx_doppler"]))
            else:
                sched = S.build_schedule(sid, "venus", model, t_start, f_dial, mode=cfg["sky_mode"],
                                         chunk_s=float(cfg["sky_chunk_s"]), rtt_guard_s=float(cfg["sky_rtt_guard"]),
                                         t_on_max_s=float(cfg["sky_t_on_max"]), t_off_min_s=float(cfg["sky_t_off_min"]),
                                         doppler_table=table_name, **common)
                opts_kw = dict(pa_in_chain=bool(cfg["sky_pa"]), tx_precompensate=bool(cfg["sky_precomp"]),
                               rx_doppler_removal=bool(cfg["sky_rx_doppler"]))
            if sched.pilot_enabled and sched.chunks:
                n_min = min(c.n_frames for c in sched.chunks[:-1] or sched.chunks)
                need = p.pilot_frames + 2
                if n_min < need:
                    raise RuntimeError(f"a chunk of {n_min} frames cannot hold {p.pilot_frames} pilot frames plus data: "
                                       f"use a chunk of at least {need / p.r_bw:.1f} s ({need} frames of {p.t_frame:.3f} s) "
                                       f"or fewer pilot frames")
            desc = S.describe(sched, model)
            dur = sched.t_end - sched.t_start
            desc += (f"\nduration {dur / 60:.1f} min ({int(dur // 3600)}h {int(dur % 3600 // 60):02d}m); symbol {p.n_frames} frames = "
                     f"{p.t_sym:.1f} s; message {p.t_sym * p.n_sym / 60:.1f} min per pass")
            if preview_only:
                self.send(("preview", desc))
                return {"ok": True, "pdf": None, "preview": True}
            self.say(desc)
            sched.to_json(archive / f"{sid}.json")

            from .station import Session, SessionOptions
            opts = SessionOptions(out_dir=str(archive), live_decode=True, **opts_kw)
            from . import keyer as _keyer
            kind = {"B210 GPIO": "gpio", "USB relay board": "usb_relay", "gpio": "gpio", "usb_relay": "usb_relay"}.get(cfg.get("keyer_kind", "none"), "none")
            if mode == "sim" and kind == "gpio":
                kind = "none"
            if kind == "usb_relay":
                self.send(("state", "opening the USB relay keyer"))
            key = _keyer.make_keyer(kind, radio=radio, port=cfg.get("keyer_port", ""),
                                    channel=int(cfg.get("keyer_channel", 1)), log=self.say)
            key.open()                      # fails here, before any RF, if the port is wrong
            self.say(f"key line: {key.name}")
            sess = Session(sched, model, radio, opts, log=self.say, keyer=key)
            probs = sess.preflight()
            if probs:
                raise RuntimeError("preflight failed: " + "; ".join(probs))
            self.session = sess

            def on_frame(k, x, metric):
                try:
                    self.send(("frame", int(k), np.asarray(metric, dtype=np.float32).tobytes()))
                except Exception:
                    pass
            sess.listeners.append(on_frame)
            self.send(("session", {"schedule": sched.to_dict(), "t_offset": radio.device_time() - time.time(),
                                   "radio": getattr(radio, "status_text", lambda: "")(), "gps": gps_text,
                                   "sim": bool(getattr(radio, "sim", False))}))
            stop = threading.Event()
            th = threading.Thread(target=self._status_loop, args=(sess, radio, model, sched, stop, gps_text), daemon=True)
            th.start()
            self.send(("state", "running"))
            try:
                rep = sess.run()
            finally:
                stop.set()
                self.phase("last chunk done: draining and closing the archive")
                try:
                    sess.keyer.close()
                except Exception:
                    pass
                sess.release()
                if radio is not None:
                    self.phase("closing the radio")
                    try:
                        radio.close()
                    except Exception:
                        pass
                    radio = None
            self.say(f"session finished: aborted={rep.aborted} {rep.abort_reason}; chunks keyed {rep.chunks_keyed}; "
                     f"frames sent {rep.frames_sent}; live decode {rep.live_decode}")
            search = int(cfg["interop_search_frames"]) if mode == "interop" and cfg["interop_dir"].startswith("Receive") else 0
            result = self.decode_and_report(archive, sched, extra={"GPS clock": gps_text} if gps_text else None, search=search)
            result["aborted"] = rep.aborted
            if rep.aborted:
                self.phase(f"ABORTED: {rep.abort_reason}", "fail")
            elif result.get("ok"):
                self.phase(f"DECODED '{result['summary']['combined']['text']}'  -  report on the Report tab", "ok")
            else:
                self.phase("NOT DECODED  -  report on the Report tab", "fail")
            return result
        except Exception as e:      # noqa: BLE001
            self.say("ERROR: " + "".join(traceback.format_exception_only(type(e), e)).strip())
            tb = traceback.format_exc().splitlines()
            if len(tb) >= 3:
                self.say(tb[-3].strip())
            self.phase(f"FAILED: {e}", "fail")
            return {"ok": False, "error": str(e), "pdf": None}
        finally:
            if radio is not None:
                try:
                    radio.close()
                except Exception:
                    pass
            self.session = None
            self.send(("state", "idle"))

    def _status_loop(self, sess, radio, model, sched, stop: threading.Event, gps_text: str) -> None:
        """Half-second status for the panel: phase, key, radio text, ephemeris; abort polls."""
        from . import gpsdo as _gpsdo
        g = None
        g_absent = False
        n = 0
        while not stop.is_set():
            d = {"phase": sess.phase, "keyed": bool(sess.keyer.keyed), "key_text": sess.keyer.status_text()}
            try:
                if hasattr(radio, "status") and n % 4 == 0:
                    st = radio.status()
                    d["radio"] = (f"B210 {st.serial}  ref {st.clock_source}/{st.time_source}  "
                                  f"{'LOCKED' if st.ref_locked else 'not locked'}\n"
                                  f"rx {st.rx_rate:.2f} S/s  {st.rx_freq / 1e6:.6f} MHz  gain {st.rx_gain:.0f} dB  {st.rx_antenna}\n"
                                  f"tx {st.tx_rate:.2f} S/s  {st.tx_freq / 1e6:.6f} MHz  gain {st.tx_gain:.0f} dB  {st.tx_antenna}\n"
                                  f"LO offset rx {'ok' if st.rx_lo_offset_ok else 'FELL BACK'} / tx {'ok' if st.tx_lo_offset_ok else 'FELL BACK'}"
                                  f"   PPS set error {st.pps_error_s if st.pps_error_s is None else f'{st.pps_error_s:+.6f} s'}")
                elif not hasattr(radio, "status"):
                    d["radio"] = getattr(radio, "status_text", lambda: "radio")()
            except Exception as e:      # noqa: BLE001
                d["radio"] = f"radio status unavailable: {e}"
            if n % 4 == 0 and gps_text and not g_absent:
                try:
                    if g is None:
                        g = _gpsdo.LeoBodnarGPSDO()
                    st = g.status(300)
                    d["gps"] = (f"GPS clock {g.serial}: sat {'LOCK' if st.sat_lock else 'no lock'}, "
                                f"PLL {'LOCK' if st.pll_lock else 'no lock'}, signal losses {st.loss_count}")
                except Exception as e:      # noqa: BLE001
                    d["gps"] = f"GPS clock: {e}"
                    g_absent = True
            try:
                now = radio.device_time()
                if model is not None:
                    f = sched.f_dial_hz
                    d["eph"] = (f"{sched.target}: az {model.azimuth_deg(now):6.1f}  el {model.elevation_deg(now):5.1f}  "
                                f"RTT {model.rtt_s(now):7.2f} s  Doppler {model.doppler_hz(now, f):+8.1f} Hz  "
                                f"rate {model.rate_hz_s(now, f):+.3f} Hz/s")
            except Exception as e:      # noqa: BLE001
                d["eph"] = f"ephemeris: {e}"
            self.send(("status", d))
            if self.poll_abort is not None:
                why = self.poll_abort()
                if why:
                    sess.abort(why)
            n += 1
            stop.wait(0.5)
        if g is not None:
            try:
                g.close()
            except Exception:
                pass

    def decode_and_report(self, archive: Path, sched, extra=None, search: int = 0) -> Dict:
        from .decode import decode_archive, summarize
        from .report import write_report
        self.send(("state", "decoding"))
        self.say("offline decode (decision of record) ...")
        summary, windows = None, []
        n_files = len(list(Path(archive).glob(f"{sched.session_id}_*.eve.iq")))
        done = {"n": 0}

        def decode_log(s: str) -> None:
            self.say(s)
            if s.startswith(sched.session_id + "_"):
                done["n"] += 1
                self.phase(f"offline decode: window {done['n']} of {n_files}")
        self.phase(f"offline decode: {n_files} windows to file")
        try:
            if search == 0 and sched.mode == "bistatic_rx":
                search = 30
            acc, windows = decode_archive(archive, sched, log=decode_log, epoch_search_frames=search)
            summary = summarize(acc, sched)
            c = summary["combined"]
            self.say(f"OFFLINE DECODE: {'OK' if c['ok'] else 'FAIL'} '{c['text']}' symbols {c['symbols']} expected {c['expected']}; "
                     f"margins dB {[round(m, 1) for m in c['margins_db']]}")
        except Exception as e:      # noqa: BLE001
            self.say(f"offline decode failed: {e}")
        rep_path = archive / f"{sched.session_id}_session.json"
        rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}
        pdf = archive / f"{sched.session_id}_report.pdf"
        self.phase("writing the report")
        try:
            write_report(sched, rep, summary, windows, pdf, extra=extra)
            self.say(f"report written: {pdf}")
        except Exception as e:      # noqa: BLE001
            self.say(f"report failed: {e}")
            pdf = None
        ok = bool(summary and summary["combined"]["ok"])
        return {"ok": ok, "pdf": str(pdf) if pdf else None, "session_id": sched.session_id, "summary": summary}

    def redecode(self, session_json: Path) -> Dict:
        try:
            sched = S.Schedule.from_json(session_json)
            return self.decode_and_report(session_json.parent, sched)
        except Exception as e:      # noqa: BLE001
            self.say(f"ERROR: {e}")
            return {"ok": False, "error": str(e), "pdf": None}
        finally:
            self.send(("state", "idle"))


# ---- process target ------------------------------------------------------------------------
def run_job(cfg: Dict, preview_only: bool, conn, redecode: Optional[str] = None) -> None:
    import faulthandler
    try:
        from . import log_dir
        faulthandler.enable(file=open(os.path.join(str(log_dir()), "fault.log"), "a"), all_threads=True)
    except Exception:
        pass
    pending = {"abort": None}

    def send(msg):
        try:
            conn.send(msg)
        except Exception:
            pass

    def poll_abort():
        try:
            while conn.poll():
                m = conn.recv()
                if m and m[0] == "abort":
                    pending["abort"] = m[1] if len(m) > 1 else "operator abort"
        except Exception:
            pass
        why, pending["abort"] = pending["abort"], None
        return why

    r = Runner(send, poll_abort)
    if redecode:
        result = r.redecode(Path(redecode))
    else:
        result = r.run(cfg, preview_only)
    send(("finished", result))
    try:
        conn.close()
    except Exception:
        pass

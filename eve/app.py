"""The DSES EVE modem application: one window for every kind of run, with remembered
settings, the live operator display, and the session report.

Tabs:
  Setup   - the run mode (software simulation, B210 loopback bench, EME, Venus) and its
            settings; a schedule preview; Start / Abort. Settings persist in an INI file
            (QSettings, %APPDATA%\\DSES\\EVE_Modem.ini on Windows) so the desktop icon comes
            up ready for the last test.
  Run     - the operator display (eve.display.OperatorPanel), re-bound for every run; the
            program does not need restarting between runs.
  Report  - the session report PDF of the run just finished (or any earlier one in the
            archive folder), rendered in the window; open it externally or re-decode.

The command-line tools keep working; this window drives the same Session, EveRadio,
SimRadio, gpsdo preflight, decode, and report code they do.
"""
from __future__ import annotations

import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import json
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__
from .params import EveParams
from . import doppler as D
from . import schedule as S
from .doppler import iso_utc
from .display import OperatorPanel, NAVY, TEAL, MONO, ABORT_STYLE, wrap_tooltips

MODES = [("sim", "Software simulation (no radio)"),
         ("bench", "Bench loopback (one B210, no antenna)"),
         ("interop", "Interop with a partner station (ORI): transmit only or receive only"),
         ("eme", "EME — Earth-Moon-Earth"),
         ("eve", "EVE — Earth-Venus-Earth")]
PREFIX = {"sim": "SIM", "bench": "BENCH", "interop": "INTEROP", "eme": "EME", "eve": "EVE"}
TARGET = {"sim": "sim", "bench": "bench", "interop": "partner", "eme": "moon", "eve": "venus"}
FULL_FRAMES = {"A": 473, "B": 247, "MATLAB": 540}



# ------------------------------------------------------------------------------------------
# operator help: one tooltip per control (shown on hover; also on the row labels)
# ------------------------------------------------------------------------------------------
TIPS: Dict[str, str] = {
    "mode:sim": "No radio. The tone source feeds a software channel (delay + noise) straight into the receiver. "
                "Use it to learn the display, to check a schedule, or to prove the decoder at a chosen C/N0.",
    "mode:bench": "One B210, nothing on the antenna ports. Transmits at low gain and receives its own internal "
                  "TX-to-RX leakage on RX2; proves the radio, the reference, the archive, and the decoder before "
                  "anything goes on the air. Chunks follow the bench settings, not a round trip.",
    "mode:eme": "Moon bounce with the station. Ephemeris from JPL Horizons (astropy fallback), Doppler "
                "pre-compensated, 2.5 s round trip: transmit a chunk, listen for its echo, repeat. The proof test "
                "before Venus (design 4.6). Keying and the amplifier limits apply if a PA is in the chain.",
    "mode:eve": "Venus bounce. 272 s round trip on conjunction day: 240 s transmit chunks, then listen. A full "
                "message is 30.2 minutes per pass; five passes are planned (design 4.1, 4.2). The amplifier limits "
                "are enforced regardless of the schedule.",
    "variant": "A: the ORI air interface (2.87 Hz bins, 473 frames per symbol) - use it whenever another station "
               "must decode us. B: DSES 23 cm monostatic variant (1.5 Hz bins, 247 frames), +1.1 dB, DSES-only.",
    "f_dial_mhz": "Dial frequency in MHz. The 4096-tone comb sits 25 to 48.5 kHz ABOVE it. Venus 2026: 1299.5 "
                  "(23 cm package). Bench: 1296. 13 cm: 2304 or 2400. The LO parks 300 kHz below (LO offset).",
    "message": "Up to 11 ASCII characters (90 bits + CRC-16 -> BCH(127,106) -> 11 symbols of 12 bits). The Venus "
               "message is K0PRT K0PRT, the club call, never a personal call.",
    "repeat": "How many times the message is sent. The receiver adds the passes before deciding, so every extra pass "
              "buys margin (about +3 dB per doubling). Venus plan: 5 passes; bench: 2.",
    "pilot": "Send pilot frames before each pass. The receiver uses them to measure the frequency offset and to "
             "confirm the epoch to a whole frame. Leave on unless a partner's file format forbids it.",
    "full_symbol": "Full length = the air interface: 473 frames per symbol (Variant A), 164.8 s per symbol, "
                   "30.2 min per message pass. Untick for a short TEST symbol that runs in seconds; a test-length "
                   "signal has no on-air meaning and a partner could not decode it.",
    "n_frames_test": "Frames per symbol for a TEST run. Each frame is 0.348 s (Variant A). 6 frames = 2.1 s per symbol, "
                     "23 s per message pass; the bench default.",
    "pilot_frames_test": "Pilot frames per pass for a TEST run (the full-length pilot is 40 frames).",
    "serial": "B210 serial number, or blank for the first B2xx found on USB. The Haswell radio and the bench clone "
              "have different serials; set it when both are plugged in.",
    "tx_gain": "B210 transmit gain, 0 to 89.75 dB. 0 dB for the loopback bench (the leakage is enough). On the air the "
               "driver amplifier's input level sets it; +8 dBm out of the B210 at maximum. Never exceed what the "
               "driver's input pad expects (design 7.1, O4).",
    "rx_gain": "B210 receive gain. Set it so the receiver noise sits 10 to 15 dB above the B210's own floor: watch the "
               "tone strip's background on the Run tab. 30 dB on the bench; 40 to 50 dB behind the LNA.",
    "clock": "external: 10 MHz on REF IN and 1 PPS on PPS IN from the station GPS clock (the standard). gpsdo: an "
             "Ettus GPSDO board inside the B210. internal: the B210's own TCXO, bench only, no lock check, +/-2 ppm.",
    "time_host": "Tick when there is NO 1 PPS into the B210: the epoch then comes from this PC's NTP clock (tens of "
                 "milliseconds, inside the one-frame tolerance). With the Leo Bodnar clock's OUT2 on PPS IN leave "
                 "this unticked so the time is set on a real PPS edge.",
    "gpsdo": "Before opening the radio, program the Leo Bodnar GPS reference clock over USB to the station setting "
             "(OUT1 = 10 MHz at level 1 for REF IN; OUT2 disabled = 1 PPS for PPS IN) and wait for satellite and PLL "
             "lock. The run refuses to start unlocked. Untick only if that clock is not connected to this PC.",
    "lo_offset_khz": "Where the B210's local oscillator parks relative to the dial frequency. -300 kHz keeps its "
                     "leakage far outside the comb in both directions. Leave it unless the radio says the offset "
                     "could not be applied.",
    "archive": "Folder for everything a run produces: the schedule JSON, the ephemeris CSV, the archived receive "
               "windows (.eve.iq + sidecar), the session log JSON, and the report PDF. One folder per campaign.",
    "sim_cn0": "Carrier-to-noise density of the simulated echo in dB-Hz. The Venus design point is about 0 dB-Hz "
               "(a message decodes with margin at 473 frames); 20 dB-Hz is an easy check of the machinery.",
    "sim_range_km": "One-way range of the synthetic target. 375,000 km = the Moon's 2.5 s round trip (monostatic "
                    "chunking, transmit then listen). Under 100 km the run is chunked like the bench.",
    "sim_seed": "Random seed of the simulated noise; the same seed repeats a run exactly.",
    "bench_range_km": "Synthetic range for the bench schedule. The B210's leakage path has no delay, so keep this "
                      "tiny (0.15 km) and the receive window lands on the transmission itself.",
    "bench_chunk_s": "Length of each transmit chunk on the bench. 2.4 s mimics the Moon cadence.",
    "bench_t_off_min": "Minimum silence between bench chunks.",
    "eme_chunk_s": "Monostatic Moon: transmit at most the round trip (2.5 s) minus the guard, then listen for the "
                   "echo. 2.4 s. The station's transmit/receive switching must follow this cadence (7.2, O6); if it "
                   "cannot, use bistatic_tx with a partner receiver.",
    "eme_rtt_guard": "Time kept between the end of a chunk and the arrival of its echo (switching time).",
    "eme_t_off_min": "Minimum silence between Moon chunks.",
    "eme_t_on_max": "Longest chunk the schedule may plan for the Moon.",
    "eme_pa": "Tick when the amplifier is in the chain: the 300 s on / 240 s off limits of design 7.2 are then "
              "enforced whatever the schedule says. Untick for the bare-B210 EME test (design 4.6).",
    "sky_chunk_s": "Venus transmit chunk. 240 s against the 272 s round trip on conjunction day; the amplifier's "
                   "thermal limit is the other bound (design 4.2, O2).",
    "sky_rtt_guard": "Seconds kept between the end of a chunk and the earliest echo (30 s covers the switching and "
                     "the round-trip change during a session).",
    "sky_t_on_max": "Longest chunk the schedule may plan (the amplifier's on-limit, 300 s).",
    "sky_t_off_min": "Shortest silence between chunks (the amplifier's cooling limit, 240 s).",
    "sky_pa": "Tick when the amplifier is in the chain (it is, for Venus): the 7.2 limits are enforced regardless "
              "of the schedule and a schedule violating them is refused.",
    "sky_mode": "monostatic: DSES transmits and receives its own echo in the gaps. bistatic_tx: DSES transmits the "
                "whole message (chunked only by the amplifier limits) and a partner station receives.",
    "sky_start_now": "Start the first chunk this many seconds after START (the radio needs about 8 s to arm). "
                     "Untick to start at the UTC time on the right (for a planned session window).",
    "lead_s": "Seconds from START to the first chunk. At least 12; 15 is comfortable. Also used by the bench.",
    "sky_start_utc": "UTC start of the first chunk when 'start now' is unticked. The run refuses a time in the past.",
    "sky_source": "auto: JPL Horizons over the internet, astropy with the local DE440s if that fails. The two differ "
                  "by 10 Hz at 13 cm, so Horizons is primary (design 4.3). The table used is saved with the run.",
    "sky_precomp": "Monostatic: shift the transmit tones by minus the predicted two-way Doppler so our own echo "
                   "lands on the nominal comb. Untick only when transmitting for a partner that does its own removal.",
    "mode:interop": "Compatibility test with ORI's own hardware and software, on the bench (cable and attenuator) "
                    "or across the room. Transmit only: we send the agreed message at the agreed UTC time and the "
                    "partner's receiver decodes it. Receive only: the partner transmits (their generator has no pilot: "
                    "untick Pilot) and we archive and decode, searching for their start within the search range. No "
                    "Doppler, no round trip, no amplifier limits.",
    "interop_dir": "Which end we are. Transmit only keys nothing unless a sequencer is wired; receive only sends no RF at all.",
    "interop_chunk_s": "Length of each contiguous transmit/receive block. Blocks follow each other with no gap, so the "
                       "signal is continuous; the block only sets the archive file size (about 24 MB per 300 s).",
    "interop_search_frames": "Receive only: how far (in frames of 0.35 s) the partner's actual start may differ from "
                             "the agreed time. The decoder searches this range; 30 frames = +/-10 s. Larger costs time.",
    "sky_rx_doppler": "Remove the model Doppler on receive. Needed when this receiver is NOT the one the transmit was "
                      "pre-compensated for (receiving a partner's transmission).",
}


# ------------------------------------------------------------------------------------------
# settings
# ------------------------------------------------------------------------------------------
class Settings:
    """Typed access to the INI file. Keys are the form fields; defaults live here."""
    DEFAULTS: Dict[str, object] = {
        "mode": "bench", "variant": "A", "f_dial_mhz": 1296.0, "message": "K0PRT K0PRT", "repeat": 2,
        "pilot": True, "full_symbol": False, "n_frames_test": 6, "pilot_frames_test": 2,
        "serial": "", "tx_gain": 0.0, "rx_gain": 30.0, "clock": "external", "time_host": False,
        "gpsdo": True, "lo_offset_khz": -300.0, "archive": "archive_app",
        "bench_range_km": 0.15, "bench_chunk_s": 2.4, "bench_t_off_min": 0.5, "lead_s": 15.0,
        "sim_cn0": 20.0, "sim_range_km": 375000.0, "sim_seed": 1,
        "sky_mode": "monostatic", "sky_start_now": True, "sky_start_utc": "", "sky_chunk_s": 240.0,
        "sky_pa": True, "sky_t_on_max": 300.0, "sky_t_off_min": 240.0, "sky_rtt_guard": 30.0,
        "sky_source": "auto", "sky_rx_doppler": False, "sky_precomp": True,
        "eme_chunk_s": 2.4, "eme_pa": False, "eme_t_off_min": 0.5, "eme_rtt_guard": 0.1, "eme_t_on_max": 300.0,
        "interop_dir": "Transmit only (the partner receives)", "interop_chunk_s": 300.0, "interop_search_frames": 30,
        "report_zoom": 100,
    }

    def __init__(self):
        self.q = QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, "DSES", "EVE_Modem")

    @property
    def path(self) -> str:
        return self.q.fileName()

    def get(self, key: str):
        d = self.DEFAULTS[key]
        v = self.q.value(key, d)
        if isinstance(d, bool):
            return v in (True, "true", "True", 1, "1")
        if isinstance(d, int):
            try:
                return int(v)
            except Exception:
                return d
        if isinstance(d, float):
            try:
                return float(v)
            except Exception:
                return d
        return str(v) if v is not None else d

    def set(self, key: str, value) -> None:
        self.q.setValue(key, value)

    def sync(self) -> None:
        self.q.sync()


# ------------------------------------------------------------------------------------------
# run controller: builds radio, model, schedule, session; runs; decodes; reports
# ------------------------------------------------------------------------------------------
class RunController(QtCore.QObject):
    log = QtCore.Signal(str)
    session_ready = QtCore.Signal(object)          # Session, before it runs (GUI binds the panel)
    preview_ready = QtCore.Signal(str)
    finished = QtCore.Signal(dict)                 # {"ok", "pdf", "session_id", "summary", "error"}
    state = QtCore.Signal(str)                     # idle | preparing | running | decoding | done
    session_done = QtCore.Signal()                 # the GUI must let go of the session (panel.unbind)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self._thread: Optional[threading.Thread] = None
        self._bound = threading.Event()
        self.busy = False
        self._released = threading.Event()

    # ---- public --------------------------------------------------------------------------
    def start(self, cfg: Dict, preview_only: bool = False) -> bool:
        if self.busy:
            return False
        self.busy = True
        self._bound.clear()
        self._released.clear()
        self._thread = threading.Thread(target=self._work, args=(dict(cfg), preview_only), name="eve-app-run", daemon=True)
        self._thread.start()
        return True

    def redecode(self, session_json: Path) -> bool:
        if self.busy:
            return False
        self.busy = True
        self._thread = threading.Thread(target=self._redecode, args=(Path(session_json),), name="eve-app-decode", daemon=True)
        self._thread.start()
        return True

    def panel_bound(self) -> None:
        self._bound.set()

    def panel_released(self) -> None:
        self._released.set()

    def abort(self, reason: str = "operator abort") -> None:
        if self.session is not None:
            self.session.abort(reason)

    # ---- worker ----------------------------------------------------------------------------
    def _say(self, s: str) -> None:
        self.log.emit(s)

    def _work(self, cfg: Dict, preview_only: bool) -> None:
        radio = None
        gps_text = ""
        try:
            self.state.emit("preparing")
            mode = cfg["mode"]
            p = EveParams.named(cfg["variant"])
            if not cfg["full_symbol"]:
                p = replace(p, n_frames=int(cfg["n_frames_test"]), pilot_frames=int(cfg["pilot_frames_test"]))
            f_dial = float(cfg["f_dial_mhz"]) * 1e6
            archive = Path(cfg["archive"])
            archive.mkdir(parents=True, exist_ok=True)
            site = D.DSES_HASWELL

            # GPS clock
            if mode != "sim" and cfg["gpsdo"] and not preview_only:
                from . import gpsdo
                rep = gpsdo.preflight()
                gps_text = f"{rep['config']} | locked {rep['locked']}"
                self._say("GPS clock: " + gps_text)
                if not rep["locked"]:
                    raise RuntimeError("GPS clock not locked; refusing to start")

            # radio
            if mode == "sim":
                from .station import SimRadio
                radio = SimRadio(p, cn0_db=float(cfg["sim_cn0"]), seed=int(cfg["sim_seed"]))
                now = time.time()
            elif preview_only:
                radio = None
                now = time.time()
            else:
                from .radio import EveRadio, RadioConfig
                rc = RadioConfig(serial=cfg["serial"], f_dial_hz=f_dial, tx_gain_db=float(cfg["tx_gain"]),
                                 rx_gain_db=float(cfg["rx_gain"]), clock_source=cfg["clock"],
                                 time_source="host" if cfg["time_host"] else None,
                                 require_ref_lock=(cfg["clock"] != "internal"), lo_offset_hz=float(cfg["lo_offset_khz"]) * 1e3)
                radio = EveRadio(p, rc)
                st = radio.open()
                self._say(st.summary())
                self._say(f"rate error {radio.rate_error_ppm():+.3f} ppm; PPS verify {radio.verify_pps()}")
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
            if mode == "sim":
                tab = D.synthetic_table("sim", site, now - 60, now + 8 * 3600, range_km=float(cfg["sim_range_km"]))
                model = D.DopplerModel(tab)
                table_name = ""
            elif mode == "bench":
                tab = D.synthetic_table("bench", site, now - 60, now + 8 * 3600, range_km=float(cfg["bench_range_km"]))
                model = D.DopplerModel(tab)
                table_name = ""
            elif mode == "interop":
                tab = D.synthetic_table("partner", site, now - 60, now + 8 * 3600, range_km=0.001)
                model = D.DopplerModel(tab)
                table_name = ""
            else:
                target = TARGET[mode]
                table_path = archive / f"{sid}_{target}_haswell.csv"
                self._say(f"ephemeris for {target} from {cfg['sky_source']} ...")
                model = D.make_model(target, site, t_start - 1800, t_start + 8 * 3600, source=cfg["sky_source"],
                                     table_path=None if preview_only else table_path, step="1 m")
                table_name = table_path.name if not preview_only else ""
                el = model.elevation_deg(t_start)
                self._say(f"{target} at start: elevation {el:.1f} deg, RTT {model.rtt_s(t_start):.2f} s, "
                          f"Doppler {model.doppler_hz(t_start, f_dial):+.1f} Hz")
                if el < 0:
                    raise RuntimeError(f"{target} is below the horizon at the start time (elevation {el:.1f} deg)")

            # schedule
            if mode == "sim":
                rtt = model.rtt_s(t_start)
                if rtt > 0.6:       # a real round trip (the Moon's 2.5 s): monostatic, transmit then listen
                    sched = S.build_schedule(sid, "sim", model, t_start, f_dial, params=p, text=cfg["message"],
                                             repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"], chunk_s=None,
                                             rtt_guard_s=0.1, t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0)
                else:               # no round trip to speak of: chunk like the bench, receive on the transmission
                    sched = S.build_schedule(sid, "sim", model, t_start, f_dial, params=p, text=cfg["message"],
                                             repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"],
                                             chunk_s=float(cfg["bench_chunk_s"]), rtt_guard_s=0.0,
                                             t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0, mode="bistatic_tx")
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False,
                               start_margin_s=1.0, realtime_mode=False)
            elif mode == "bench":
                sched = S.build_schedule(sid, "bench", model, t_start, f_dial, params=p, text=cfg["message"],
                                         repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"], chunk_s=float(cfg["bench_chunk_s"]),
                                         rtt_guard_s=0.0, t_off_min_s=float(cfg["bench_t_off_min"]), t_on_max_s=3600.0,
                                         mode="bistatic_tx")
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False, start_margin_s=2.0)
            elif mode == "interop":
                rx_only = cfg["interop_dir"].startswith("Receive")
                sched = S.build_schedule(sid, "partner", model, t_start, f_dial, params=p, text=cfg["message"],
                                         repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"],
                                         chunk_s=float(cfg["interop_chunk_s"]), rtt_guard_s=0.0, t_off_min_s=0.0,
                                         t_on_max_s=3600.0, mode="bistatic_rx" if rx_only else "bistatic_tx",
                                         notes=("receive only: partner transmits" if rx_only else "transmit only: partner receives"))
                opts_kw = dict(pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False,
                               tx_enabled=not rx_only)
            elif mode == "eme":
                sched = S.build_schedule(sid, "moon", model, t_start, f_dial, params=p, text=cfg["message"],
                                         repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"], mode=cfg["sky_mode"],
                                         chunk_s=float(cfg["eme_chunk_s"]), rtt_guard_s=float(cfg["eme_rtt_guard"]),
                                         t_on_max_s=float(cfg["eme_t_on_max"]), t_off_min_s=float(cfg["eme_t_off_min"]),
                                         doppler_table=table_name)
                opts_kw = dict(pa_in_chain=bool(cfg["eme_pa"]), tx_precompensate=bool(cfg["sky_precomp"]),
                               rx_doppler_removal=bool(cfg["sky_rx_doppler"]))
            else:
                sched = S.build_schedule(sid, "venus", model, t_start, f_dial, params=p, text=cfg["message"],
                                         repeat_count=int(cfg["repeat"]), pilot=cfg["pilot"], mode=cfg["sky_mode"],
                                         chunk_s=float(cfg["sky_chunk_s"]), rtt_guard_s=float(cfg["sky_rtt_guard"]),
                                         t_on_max_s=float(cfg["sky_t_on_max"]), t_off_min_s=float(cfg["sky_t_off_min"]),
                                         doppler_table=table_name)
                opts_kw = dict(pa_in_chain=bool(cfg["sky_pa"]), tx_precompensate=bool(cfg["sky_precomp"]),
                               rx_doppler_removal=bool(cfg["sky_rx_doppler"]))
            # a chunk must hold the pilot frames plus at least two data frames, or the
            # pilot eats the chunk (Variant B with the 2.4 s bench chunk: 3 frames, 2 pilot)
            if sched.pilot_enabled and sched.chunks:
                n_min = min(c.n_frames for c in sched.chunks[:-1] or sched.chunks)
                need = p.pilot_frames + 2
                if n_min < need:
                    raise RuntimeError(f"a chunk of {n_min} frames cannot hold {p.pilot_frames} pilot frames plus data: "
                                       f"use a chunk of at least {need / p.r_bw:.1f} s ({need} frames of {p.t_frame:.3f} s) "
                                       f"or fewer pilot frames")
            desc = S.describe(sched, model)
            dur = sched.t_end - sched.t_start
            desc += f"\nduration {dur / 60:.1f} min ({int(dur // 3600)}h {int(dur % 3600 // 60):02d}m); symbol {p.n_frames} frames = {p.t_sym:.1f} s; message {p.t_sym * p.n_sym / 60:.1f} min per pass"
            if preview_only:
                self.preview_ready.emit(desc)
                return
            self._say(desc)
            sched.to_json(archive / f"{sid}.json")

            from .station import Session, SessionOptions
            opts = SessionOptions(out_dir=str(archive), live_decode=True, **opts_kw)
            sess = Session(sched, model, radio, opts)
            probs = sess.preflight()
            if probs:
                raise RuntimeError("preflight failed: " + "; ".join(probs))
            self.session = sess
            self.session_ready.emit(sess)
            self._bound.wait(5.0)
            self.state.emit("running")
            try:
                rep = sess.run()
            finally:
                # order matters: blocks first (Session.release), then the panel's grip on
                # the session, then the radio; a usrp_source that outlives close() makes
                # the next open crash the process
                self.session_done.emit()
                self._released.wait(3.0)
                sess.release()
                self.session = None
                if radio is not None:
                    try:
                        radio.close()
                    except Exception:
                        pass
                    radio = None
            self._say(f"session finished: aborted={rep.aborted} {rep.abort_reason}; chunks keyed {rep.chunks_keyed}; "
                      f"frames sent {rep.frames_sent}; live decode {rep.live_decode}")
            search = int(cfg["interop_search_frames"]) if mode == "interop" and cfg["interop_dir"].startswith("Receive") else 0
            result = self._decode_and_report(archive, sched, extra={"GPS clock": gps_text} if gps_text else None, search=search)
            result["aborted"] = rep.aborted
            self.finished.emit(result)
        except Exception as e:      # noqa: BLE001
            self._say("ERROR: " + "".join(traceback.format_exception_only(type(e), e)).strip())
            self._say(traceback.format_exc().splitlines()[-3] if traceback.format_exc() else "")
            self.finished.emit({"ok": False, "error": str(e), "pdf": None})
        finally:
            if radio is not None:
                try:
                    radio.close()
                except Exception:
                    pass
            self.session = None
            self.busy = False
            self.state.emit("idle")

    def _decode_and_report(self, archive: Path, sched, extra=None, search: int = 0) -> Dict:
        from .decode import decode_archive, summarize
        from .report import write_report
        self.state.emit("decoding")
        self._say("offline decode (decision of record) ...")
        summary, windows = None, []
        try:
            if search == 0 and sched.mode == "bistatic_rx":
                search = 30
            acc, windows = decode_archive(archive, sched, log=self._say, epoch_search_frames=search)
            summary = summarize(acc, sched)
            c = summary["combined"]
            self._say(f"OFFLINE DECODE: {'OK' if c['ok'] else 'FAIL'} '{c['text']}' symbols {c['symbols']} expected {c['expected']}; "
                      f"margins dB {[round(m, 1) for m in c['margins_db']]}")
        except Exception as e:      # noqa: BLE001
            self._say(f"offline decode failed: {e}")
        rep_path = archive / f"{sched.session_id}_session.json"
        rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}
        pdf = archive / f"{sched.session_id}_report.pdf"
        try:
            write_report(sched, rep, summary, windows, pdf, extra=extra)
            self._say(f"report written: {pdf}")
        except Exception as e:      # noqa: BLE001
            self._say(f"report failed: {e}")
            pdf = None
        ok = bool(summary and summary["combined"]["ok"])
        return {"ok": ok, "pdf": str(pdf) if pdf else None, "session_id": sched.session_id, "summary": summary}

    def _redecode(self, session_json: Path) -> None:
        try:
            self.state.emit("decoding")
            sched = S.Schedule.from_json(session_json)
            result = self._decode_and_report(session_json.parent, sched)
            self.finished.emit(result)
        except Exception as e:      # noqa: BLE001
            self._say(f"ERROR: {e}")
            self.finished.emit({"ok": False, "error": str(e), "pdf": None})
        finally:
            self.busy = False
            self.state.emit("idle")


# ------------------------------------------------------------------------------------------
# report viewer
# ------------------------------------------------------------------------------------------
class ReportPane(QtWidgets.QWidget):
    """One rendered report: a header naming it, the pages in a scroll area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current: Optional[Path] = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.header = QtWidgets.QLabel("no report selected")
        self.header.setStyleSheet(f"font-weight: bold; color: white; background: {TEAL}; padding: 4px 8px;")
        lay.addWidget(self.header)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.pages = QtWidgets.QWidget()
        self.pages_lay = QtWidgets.QVBoxLayout(self.pages)
        self.pages_lay.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self.scroll.setWidget(self.pages)
        lay.addWidget(self.scroll, 1)

    def clear(self) -> None:
        while self.pages_lay.count():
            w = self.pages_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)        # stop painting now; deleteLater alone leaves ghosts until the loop turns
                w.deleteLater()

    def render(self, pdf: Optional[Path], zoom: int) -> None:
        self.clear()
        self.current = pdf
        if pdf is None or not pdf.exists():
            self.header.setText("no report selected")
            self.pages_lay.addWidget(QtWidgets.QLabel("no report selected"))
            return
        self.header.setText(f"{pdf.name[:-len('_report.pdf')]}    written {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pdf.stat().st_mtime))}")
        try:
            import pymupdf
            doc = pymupdf.open(str(pdf))
            for page in doc:
                pix = page.get_pixmap(dpi=int(96 * zoom / 100), alpha=False)
                img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888).copy()
                lbl = QtWidgets.QLabel()
                lbl.setPixmap(QtGui.QPixmap.fromImage(img))
                lbl.setFrameShape(QtWidgets.QFrame.Box)
                self.pages_lay.addWidget(lbl)
            doc.close()
        except Exception as e:      # noqa: BLE001
            self.pages_lay.addWidget(QtWidgets.QLabel(f"cannot render {pdf.name}: {e}\nUse 'Open in PDF viewer'."))
        self.scroll.verticalScrollBar().setValue(0)


class ReportView(QtWidgets.QWidget):
    """The report list, one rendered report, and an optional second pane for comparing."""
    redecode_requested = QtCore.Signal(object)     # Path of the schedule JSON

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.archive = Path(settings.get("archive"))
        lay = QtWidgets.QHBoxLayout(self)
        left_w = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QtWidgets.QLabel("Session reports in the archive folder (newest first). Click one to show it."))
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumWidth(200)
        self.list.currentItemChanged.connect(self._pick)
        self.list.setToolTip("One entry per session report; click to show it in the main pane. With Compare ticked, "
                             "the second pane keeps its own choice so two runs sit side by side.")
        left.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_refresh.setToolTip("Re-read the archive folder for reports.")
        self.btn_open = QtWidgets.QPushButton("Open in PDF viewer")
        self.btn_open.clicked.connect(self._open_external)
        self.btn_open.setToolTip("Open the selected report in the system PDF viewer (for printing or sending).")
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_open)
        left.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        self.btn_folder = QtWidgets.QPushButton("Open archive folder")
        self.btn_folder.setToolTip("Open the archive folder in Explorer: schedules, ephemeris tables, receive windows, logs, reports.")
        self.btn_folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.archive.resolve()))))
        self.btn_redecode = QtWidgets.QPushButton("Re-decode this session")
        self.btn_redecode.setToolTip("run the offline decode again on the archived IQ and rebuild the report")
        self.btn_redecode.clicked.connect(self._redecode)
        row2.addWidget(self.btn_folder)
        row2.addWidget(self.btn_redecode)
        left.addLayout(row2)
        zrow = QtWidgets.QHBoxLayout()
        zrow.addWidget(QtWidgets.QLabel("Zoom"))
        self.zoom = QtWidgets.QComboBox()
        for z in (50, 75, 100, 125, 150, 200):
            self.zoom.addItem(f"{z} %", z)
        self.zoom.setCurrentIndex(max(0, self.zoom.findData(int(settings.get("report_zoom")))))
        self.zoom.currentIndexChanged.connect(lambda _i: self._rerender())
        self.zoom.setToolTip("Page rendering size (50 % fits two reports side by side on a laptop screen).")
        zrow.addWidget(self.zoom)
        self.compare = QtWidgets.QCheckBox("Compare")
        self.compare.setToolTip("Show a second pane on the right with its own report chooser, to compare two runs.")
        self.compare.toggled.connect(self._toggle_compare)
        zrow.addWidget(self.compare)
        zrow.addStretch(1)
        left.addLayout(zrow)

        self.pane = ReportPane()
        self.pane2 = ReportPane()
        self.pane2_pick = QtWidgets.QComboBox()
        self.pane2_pick.setToolTip("The report shown in the compare pane.")
        self.pane2_pick.currentIndexChanged.connect(self._pane2_changed)
        pane2_w = QtWidgets.QWidget()
        p2 = QtWidgets.QVBoxLayout(pane2_w)
        p2.setContentsMargins(0, 0, 0, 0)
        p2.addWidget(self.pane2_pick)
        p2.addWidget(self.pane2, 1)
        self.pane2_w = pane2_w
        self.pane2_w.hide()
        self.panes = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.panes.addWidget(self.pane)
        self.panes.addWidget(self.pane2_w)
        self.split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.split.addWidget(left_w)
        self.split.addWidget(self.panes)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 4)
        self.split.setSizes([330, 960])
        lay.addWidget(self.split)
        self.refresh()

    # ---- compatibility with the earlier single-pane API
    @property
    def current(self) -> Optional[Path]:
        return self.pane.current

    @property
    def pages_lay(self):
        return self.pane.pages_lay

    def set_archive(self, folder: str) -> None:
        self.archive = Path(folder)
        self.refresh()

    def _files(self):
        return sorted(self.archive.glob("*_report.pdf"), key=lambda f: f.stat().st_mtime, reverse=True) if self.archive.exists() else []

    def refresh(self) -> None:
        files = self._files()
        keep = self.pane.current
        self.list.blockSignals(True)
        self.list.clear()
        for f in files:
            it = QtWidgets.QListWidgetItem(f"{f.name[:-len('_report.pdf')]}   {time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))}")
            it.setData(QtCore.Qt.UserRole, str(f))
            self.list.addItem(it)
        self.list.blockSignals(False)
        keep2 = self.pane2.current
        self.pane2_pick.blockSignals(True)
        self.pane2_pick.clear()
        for f in files:
            self.pane2_pick.addItem(f.name[:-len("_report.pdf")], str(f))
        if keep2 is not None:
            i = self.pane2_pick.findData(str(keep2))
            if i >= 0:
                self.pane2_pick.setCurrentIndex(i)
        self.pane2_pick.blockSignals(False)
        if files and (keep is None or not keep.exists()):
            self.list.setCurrentRow(0)
        elif keep is not None:
            self.select(keep)

    def select(self, pdf: Path) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == str(pdf):
                if self.list.currentRow() == i:
                    self.pane.render(pdf, int(self.zoom.currentData()))
                else:
                    self.list.setCurrentRow(i)
                return
        self.pane.render(pdf, int(self.zoom.currentData()))

    def _pick(self, cur, _prev) -> None:
        if cur is None:
            return
        self.pane.render(Path(cur.data(QtCore.Qt.UserRole)), int(self.zoom.currentData()))

    def _pane2_changed(self, i: int) -> None:
        if i >= 0 and self.compare.isChecked():
            self.pane2.render(Path(self.pane2_pick.itemData(i)), int(self.zoom.currentData()))

    def _toggle_compare(self, on: bool) -> None:
        self.pane2_w.setVisible(on)
        if on:
            if self.pane2_pick.count() and self.pane2.current is None:
                # default: the run before the one in the main pane
                i = 0
                for j in range(self.pane2_pick.count()):
                    if self.pane.current is not None and self.pane2_pick.itemData(j) == str(self.pane.current):
                        i = min(j + 1, self.pane2_pick.count() - 1)
                        break
                self.pane2_pick.setCurrentIndex(i)
            self._pane2_changed(self.pane2_pick.currentIndex())
            self.panes.setSizes([1, 1])

    def _rerender(self) -> None:
        z = int(self.zoom.currentData())
        self.settings.set("report_zoom", z)
        self.pane.render(self.pane.current, z)
        if self.compare.isChecked():
            self.pane2.render(self.pane2.current, z)

    def _open_external(self) -> None:
        if self.pane.current is not None and self.pane.current.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.pane.current.resolve())))

    def _redecode(self) -> None:
        if self.pane.current is None:
            return
        sid = self.pane.current.name[:-len("_report.pdf")]
        sj = self.pane.current.parent / f"{sid}.json"
        if sj.exists():
            self.redecode_requested.emit(sj)


# ------------------------------------------------------------------------------------------
# help: the operator's guide (docs/DSES_EVE_Modem_Operators_Guide.md) in the window
# ------------------------------------------------------------------------------------------
DOCS = Path(__file__).resolve().parents[1] / "docs"
GUIDE_MD = DOCS / "DSES_EVE_Modem_Operators_Guide.md"
GUIDE_PDF = DOCS / "DSES_EVE_Modem_Operators_Guide.pdf"
DESIGN_PDF = DOCS / "DSES_EVE_Modem_Design_and_ICD.pdf"


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DSES EVE modem — Operator's guide")
        self.resize(900, 760)
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("find in the guide (Enter for next)")
        self.search.returnPressed.connect(self._find)
        row.addWidget(self.search, 1)
        b = QtWidgets.QPushButton("Open the guide PDF")
        b.setEnabled(GUIDE_PDF.exists())
        b.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(GUIDE_PDF))))
        row.addWidget(b)
        b = QtWidgets.QPushButton("Design document PDF")
        b.setEnabled(DESIGN_PDF.exists())
        b.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(DESIGN_PDF))))
        row.addWidget(b)
        lay.addLayout(row)
        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setSearchPaths([str(DOCS)])
        try:
            md = GUIDE_MD.read_text(encoding="utf-8")
            # the document header table and the width hints are for the PDF build
            md = "\n".join(ln for ln in md.splitlines() if not ln.startswith("<!-- widths"))
            self.view.setMarkdown(md)
        except Exception as e:      # noqa: BLE001
            self.view.setPlainText(f"The guide is not available: {e}\n(expected at {GUIDE_MD})")
        lay.addWidget(self.view, 1)

    def _find(self):
        q = self.search.text()
        if q and not self.view.find(q):
            self.view.moveCursor(QtGui.QTextCursor.Start)
            self.view.find(q)


# ------------------------------------------------------------------------------------------
# main window
# ------------------------------------------------------------------------------------------
class EveApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.ctl = RunController(self)
        self.ctl.log.connect(self._log)
        self.ctl.session_ready.connect(self._session_ready)
        self.ctl.session_done.connect(self._session_done)
        self.ctl.preview_ready.connect(self._preview_ready)
        self.ctl.finished.connect(self._finished)
        self.ctl.state.connect(self._state)
        self.w: Dict[str, QtWidgets.QWidget] = {}
        self.setWindowTitle(f"DSES EVE modem  {__version__}")
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.setup = self._build_setup()
        self.panel = OperatorPanel()
        self.report = ReportView(self.settings)
        self.report.redecode_requested.connect(self._redecode)
        self.tabs.addTab(self.setup, "Setup")
        self.tabs.addTab(self.panel, "Run")
        self.tabs.addTab(self.report, "Report")
        self.tabs.setTabToolTip(0, "Choose the run mode and its settings, preview the schedule, start and abort.")
        self.tabs.setTabToolTip(1, "The live session: tone strip, running decisions, chunk and key state, radio and GPS clock, ephemeris.")
        self.tabs.setTabToolTip(2, "Session reports (PDF) in the archive folder; the newest opens when a run ends.")
        self.status = QtWidgets.QLabel("idle")
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(QtWidgets.QLabel(f"settings: {self.settings.path}"))
        helpm = self.menuBar().addMenu("&Help")
        a = helpm.addAction("Operator's guide")
        a.setShortcut("F1")
        a.triggered.connect(self._help)
        a = helpm.addAction("Open the guide PDF")
        a.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(GUIDE_PDF))))
        a = helpm.addAction("Design description and ICD (PDF)")
        a.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(DESIGN_PDF))))
        helpm.addSeparator()
        a = helpm.addAction("About")
        a.triggered.connect(lambda: QtWidgets.QMessageBox.about(
            self, "DSES EVE modem",
            f"DSES Earth-Venus-Earth modem {__version__}\n\nORI 'Spiral #2' waveform by Pete Wyckoff, KA3WCA "
            f"(Open Research Institute, GPL-3.0).\nDSES implementation: Rick Hambly, K0GD.\n\n"
            f"Settings: {self.settings.path}"))
        self._help_dlg = None
        self._load()
        self._mode_changed()
        wrap_tooltips(self)
        for name in ("setup_split",):
            st = self.settings.q.value(name)
            if st is not None:
                getattr(self, name).restoreState(st)
        for name, obj in (("report_split", self.report.split), ("run_hsplit", self.panel.hsplit), ("run_vsplit", self.panel.vsplit)):
            st = self.settings.q.value(name)
            if st is not None:
                obj.restoreState(st)
        geo = self.settings.q.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1320, 880)

    # ---- setup tab ----------------------------------------------------------------------
    def _build_setup(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(w)
        outer.setContentsMargins(4, 4, 4, 4)
        left_w = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        left_scroll.setWidget(left_w)
        right_w = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        self.setup_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.setup_split.addWidget(left_scroll)
        self.setup_split.addWidget(right_w)
        self.setup_split.setStretchFactor(0, 3)
        self.setup_split.setStretchFactor(1, 2)
        self.setup_split.setSizes([780, 520])
        outer.addWidget(self.setup_split)

        # mode
        gb = QtWidgets.QGroupBox("Run mode")
        v = QtWidgets.QVBoxLayout(gb)
        self.mode_group = QtWidgets.QButtonGroup(self)
        for i, (key, label) in enumerate(MODES):
            rb = QtWidgets.QRadioButton(label)
            rb.setProperty("mode", key)
            self.mode_group.addButton(rb, i)
            v.addWidget(rb)
        self.mode_group.idClicked.connect(lambda _i: self._mode_changed())
        left.addWidget(gb)

        # waveform
        gb = QtWidgets.QGroupBox("Waveform and message")
        f = QtWidgets.QFormLayout(gb)
        self.w["variant"] = cb = QtWidgets.QComboBox()
        cb.addItems(["A", "B"])
        cb.setToolTip("A: ORI 2.87 Hz bins, 473 frames per symbol. B: DSES 23 cm monostatic, 1.5 Hz bins, 247 frames (design 6.1.1)")
        cb.currentTextChanged.connect(lambda _t: self._symbol_changed())
        f.addRow("Variant", cb)
        self.w["f_dial_mhz"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(70.0, 6000.0)
        sp.setDecimals(4)
        sp.setSuffix(" MHz")
        sp.setToolTip("dial frequency; the comb starts 25 kHz above it (1296 bench, 1299.5 Venus 2026, 2304 / 2400 13 cm)")
        f.addRow("Dial frequency", sp)
        self.w["message"] = le = QtWidgets.QLineEdit()
        le.setMaxLength(11)
        le.setToolTip("up to 11 ASCII characters (90 bits); the Venus message is K0PRT K0PRT")
        f.addRow("Message", le)
        self.w["repeat"] = sp = QtWidgets.QSpinBox()
        sp.setRange(1, 12)
        sp.setToolTip("passes of the message; the receiver combines them (design 4.5)")
        f.addRow("Repeat count", sp)
        self.w["pilot"] = ck = QtWidgets.QCheckBox("pilot frames before each pass")
        f.addRow("Pilot", ck)
        symrow = QtWidgets.QHBoxLayout()
        self.w["full_symbol"] = ck = QtWidgets.QCheckBox("full length")
        ck.setToolTip("473 frames per symbol (Variant A) = 164.8 s; a message is 30.2 min per pass. Untick for a short TEST symbol.")
        ck.toggled.connect(lambda _b: self._symbol_changed())
        symrow.addWidget(ck)
        symrow.addWidget(QtWidgets.QLabel("  test length:"))
        self.w["n_frames_test"] = sp = QtWidgets.QSpinBox()
        sp.setRange(2, 2000)
        sp.setSuffix(" frames")
        sp.setToolTip("frames per symbol for a short test (6 = 2.1 s per symbol, 23 s per message pass)")
        sp.valueChanged.connect(lambda _v: self._symbol_changed())
        symrow.addWidget(sp)
        symrow.addWidget(QtWidgets.QLabel("pilot"))
        self.w["pilot_frames_test"] = sp = QtWidgets.QSpinBox()
        sp.setRange(1, 200)
        sp.setSuffix(" frames")
        symrow.addWidget(sp)
        symrow.addStretch(1)
        f.addRow("Symbol length", symrow)
        self.lbl_sym = QtWidgets.QLabel()
        self.lbl_sym.setStyleSheet(f"color: {TEAL};")
        f.addRow("", self.lbl_sym)
        left.addWidget(gb)

        # radio
        gb = QtWidgets.QGroupBox("Radio and reference (B210)")
        f = QtWidgets.QFormLayout(gb)
        self.w["serial"] = le = QtWidgets.QLineEdit()
        le.setPlaceholderText("first B2xx found")
        f.addRow("B210 serial", le)
        self.w["tx_gain"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 89.75)
        sp.setSuffix(" dB")
        sp.setToolTip("0 dB for the loopback bench (internal leakage is enough); the driver's input requirement sets it for the air")
        f.addRow("TX gain", sp)
        self.w["rx_gain"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 76.0)
        sp.setSuffix(" dB")
        f.addRow("RX gain", sp)
        self.w["clock"] = cb = QtWidgets.QComboBox()
        cb.addItems(["external", "gpsdo", "internal"])
        cb.setToolTip("external = the station GPS clock on REF IN and PPS IN (7.1); internal = bench only, no lock check")
        f.addRow("Clock source", cb)
        self.w["time_host"] = ck = QtWidgets.QCheckBox("host-timed (no PPS available: epoch from the PC's NTP clock)")
        f.addRow("Time", ck)
        self.w["gpsdo"] = ck = QtWidgets.QCheckBox("program and check the Leo Bodnar GPS clock first (OUT1 10 MHz, OUT2 off = 1 PPS)")
        f.addRow("GPS clock", ck)
        self.w["lo_offset_khz"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(-2000.0, 2000.0)
        sp.setSuffix(" kHz")
        sp.setToolTip("LO parked off the dial frequency so its leakage stays out of the comb (7.1)")
        f.addRow("LO offset", sp)
        arow = QtWidgets.QHBoxLayout()
        self.w["archive"] = le = QtWidgets.QLineEdit()
        arow.addWidget(le, 1)
        b = QtWidgets.QPushButton("Browse…")
        b.clicked.connect(self._browse_archive)
        arow.addWidget(b)
        f.addRow("Archive folder", arow)
        left.addWidget(gb)

        # mode specific
        self.mode_stack = QtWidgets.QStackedWidget()
        # sim
        gb = QtWidgets.QGroupBox("Software simulation")
        f = QtWidgets.QFormLayout(gb)
        self.w["sim_cn0"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(-20.0, 60.0)
        sp.setSuffix(" dB-Hz")
        sp.setToolTip("C/N0 of the simulated echo; the Venus design point is about 0 dB-Hz, 20 is an easy check")
        f.addRow("C/N0", sp)
        self.w["sim_range_km"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 1e9)
        sp.setDecimals(1)
        sp.setSuffix(" km")
        sp.setToolTip("one-way range of the synthetic target (375,000 km = the Moon's 2.5 s round trip)")
        f.addRow("Range", sp)
        self.w["sim_seed"] = sp = QtWidgets.QSpinBox()
        sp.setRange(0, 999999)
        f.addRow("Noise seed", sp)
        self.mode_stack.addWidget(gb)
        # bench
        gb = QtWidgets.QGroupBox("Bench loopback (transmit at low gain, receive the B210's internal leakage on RX2)")
        f = QtWidgets.QFormLayout(gb)
        self.w["bench_range_km"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 1e9)
        sp.setDecimals(3)
        sp.setSuffix(" km")
        sp.setToolTip("synthetic range; the leakage path has no delay, so keep this tiny to put the receive window on the transmission")
        f.addRow("Synthetic range", sp)
        self.w["bench_chunk_s"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.5, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Chunk length", sp)
        self.w["bench_t_off_min"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Minimum off time", sp)
        self.mode_stack.addWidget(gb)
        # interop
        gb = QtWidgets.QGroupBox("Interop with a partner station")
        f = QtWidgets.QFormLayout(gb)
        self.w["interop_dir"] = cb = QtWidgets.QComboBox()
        cb.addItems(["Transmit only (the partner receives)", "Receive only (the partner transmits)"])
        f.addRow("Direction", cb)
        self.w["interop_chunk_s"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(10.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Block length", sp)
        self.w["interop_search_frames"] = sp = QtWidgets.QSpinBox()
        sp.setRange(0, 600)
        sp.setSuffix(" frames")
        f.addRow("Start search range", sp)
        lbl = QtWidgets.QLabel("Both ends use Variant A, full-length symbols, the same dial frequency, the same message, "
                               "and the same UTC start (below). The ORI generator sends no pilot: untick Pilot when receiving from it.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {TEAL};")
        f.addRow("", lbl)
        self.mode_stack.addWidget(gb)
        # eme
        gb = QtWidgets.QGroupBox("EME (Moon)")
        f = QtWidgets.QFormLayout(gb)
        self.w["eme_chunk_s"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.5, 3600.0)
        sp.setSuffix(" s")
        sp.setToolTip("monostatic Moon: transmit at most the round trip (2.5 s) minus the guard, then listen (design 4.6)")
        f.addRow("Chunk length", sp)
        self.w["eme_rtt_guard"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 60.0)
        sp.setDecimals(2)
        sp.setSuffix(" s")
        f.addRow("Round-trip guard", sp)
        self.w["eme_t_off_min"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Minimum off time", sp)
        self.w["eme_t_on_max"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(1.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Maximum on time", sp)
        self.w["eme_pa"] = ck = QtWidgets.QCheckBox("amplifier in the chain: enforce the 7.2 limits (300 s on / 240 s off)")
        f.addRow("PA", ck)
        self.mode_stack.addWidget(gb)
        # venus
        gb = QtWidgets.QGroupBox("Venus")
        f = QtWidgets.QFormLayout(gb)
        self.w["sky_chunk_s"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(1.0, 3600.0)
        sp.setSuffix(" s")
        sp.setToolTip("monostatic: 240 s chunks against the 272 s round trip (design 4.2)")
        f.addRow("Chunk length", sp)
        self.w["sky_rtt_guard"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 600.0)
        sp.setSuffix(" s")
        f.addRow("Round-trip guard", sp)
        self.w["sky_t_on_max"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(1.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Maximum on time", sp)
        self.w["sky_t_off_min"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.0, 3600.0)
        sp.setSuffix(" s")
        f.addRow("Minimum off time", sp)
        self.w["sky_pa"] = ck = QtWidgets.QCheckBox("amplifier in the chain: enforce the 7.2 limits")
        f.addRow("PA", ck)
        self.mode_stack.addWidget(gb)
        left.addWidget(self.mode_stack)

        # sky common (EME + Venus)
        self.sky_box = gb = QtWidgets.QGroupBox("Sky session (EME and Venus)")
        f = QtWidgets.QFormLayout(gb)
        self.w["sky_mode"] = cb = QtWidgets.QComboBox()
        cb.addItems(["monostatic", "bistatic_tx"])
        cb.setToolTip("monostatic: DSES transmits and receives its own echo. bistatic_tx: DSES transmits only, a partner receives (chunked by the PA limits)")
        f.addRow("Station mode", cb)
        srow = QtWidgets.QHBoxLayout()
        self.w["sky_start_now"] = ck = QtWidgets.QCheckBox("start now +")
        srow.addWidget(ck)
        self.w["lead_s"] = sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(12.0, 3600.0)
        sp.setSuffix(" s")
        sp.setToolTip("seconds from Start to the first chunk (the radio needs about 8 s to arm; also used by the bench)")
        srow.addWidget(sp)
        srow.addWidget(QtWidgets.QLabel("   or at UTC"))
        self.w["sky_start_utc"] = dt = QtWidgets.QDateTimeEdit()
        dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        dt.setTimeSpec(QtCore.Qt.UTC)
        dt.setCalendarPopup(True)
        srow.addWidget(dt)
        srow.addStretch(1)
        f.addRow("Start", srow)
        self.w["sky_source"] = cb = QtWidgets.QComboBox()
        cb.addItems(["auto", "horizons", "astropy"])
        cb.setToolTip("JPL Horizons (needs internet) is primary; astropy with the local DE440s is the fallback (design 4.3)")
        f.addRow("Ephemeris", cb)
        self.w["sky_precomp"] = ck = QtWidgets.QCheckBox("pre-compensate the transmit Doppler for our own receiver (monostatic)")
        f.addRow("Doppler", ck)
        self.w["sky_rx_doppler"] = ck = QtWidgets.QCheckBox("remove the model Doppler on receive (when this receiver is not the pre-compensated one)")
        f.addRow("", ck)
        left.addWidget(gb)
        left.addStretch(1)

        # right: preview + buttons
        right.addWidget(QtWidgets.QLabel("Schedule preview"))
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(MONO + " font-size: 11px;")
        right.addWidget(self.preview, 1)
        brow = QtWidgets.QHBoxLayout()
        self.btn_preview = QtWidgets.QPushButton("Preview schedule")
        self.btn_preview.setToolTip("build the schedule (and fetch the ephemeris for a sky session) without touching the radio")
        self.btn_preview.clicked.connect(self._preview)
        brow.addWidget(self.btn_preview)
        self.btn_start = QtWidgets.QPushButton("START")
        self.btn_start.setStyleSheet(f"background: {TEAL}; color: white; font-weight: bold; padding: 8px; font-size: 14px;")
        self.btn_start.clicked.connect(self._start)
        brow.addWidget(self.btn_start)
        self.btn_abort = QtWidgets.QPushButton("ABORT")
        self.btn_abort.setStyleSheet(ABORT_STYLE + " QPushButton { padding: 8px; font-size: 14px; }")
        self.btn_abort.clicked.connect(lambda: self.ctl.abort("operator abort from the Setup tab"))
        self.btn_abort.setEnabled(False)
        brow.addWidget(self.btn_abort)
        right.addLayout(brow)
        self.btn_save = QtWidgets.QPushButton("Save settings now")
        self.btn_save.clicked.connect(self._save)
        right.addWidget(self.btn_save)
        self.setup_log = QtWidgets.QPlainTextEdit()
        self.setup_log.setReadOnly(True)
        self.setup_log.setMaximumBlockCount(500)
        self.setup_log.setStyleSheet(MONO + " font-size: 11px;")
        right.addWidget(self.setup_log, 1)
        # operator help on every control, and on its row label
        for key, wd in self.w.items():
            tip = TIPS.get(key)
            if tip:
                wd.setToolTip(tip)
                lay = wd.parentWidget().layout() if wd.parentWidget() else None
                if isinstance(lay, QtWidgets.QFormLayout):
                    lbl = lay.labelForField(wd)
                    if lbl is not None:
                        lbl.setToolTip(tip)
        for b in self.mode_group.buttons():
            b.setToolTip(TIPS.get("mode:" + b.property("mode"), ""))
        self.btn_preview.setToolTip("Build the schedule with the current settings and show it here, without touching "
                                    "the radio. For EME and Venus this fetches the ephemeris, which takes a few seconds.")
        self.btn_start.setToolTip("Save the settings, program the GPS clock (if ticked), open the radio, build the "
                                  "schedule, and run it. The Run tab shows the session; the Report tab opens when it ends.")
        self.btn_abort.setToolTip("Release the key line at once, stop the streams, close the archive, and write the "
                                  "session log. The same as the ABORT button on the Run tab.")
        self.btn_save.setToolTip(f"Settings are saved on every Start and on exit; this saves them now. File: {self.settings.path}")
        self.preview.setToolTip("The schedule that Start would run: one line per chunk with its frames, transmit and "
                                "receive windows, round trip, elevation, and Doppler; then the total duration.")
        self.setup_log.setToolTip("What the controller did: GPS clock, radio open, schedule, session, offline decode, report.")
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.setup_log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        return w

    # ---- settings <-> form -------------------------------------------------------------
    def _load(self) -> None:
        s = self.settings
        mode = s.get("mode")
        for b in self.mode_group.buttons():
            b.setChecked(b.property("mode") == mode)
        for key, wd in self.w.items():
            v = s.get(key)
            if isinstance(wd, QtWidgets.QCheckBox):
                wd.setChecked(bool(v))
            elif isinstance(wd, QtWidgets.QComboBox):
                i = wd.findText(str(v))
                wd.setCurrentIndex(i if i >= 0 else 0)
            elif isinstance(wd, QtWidgets.QDoubleSpinBox):
                wd.setValue(float(v))
            elif isinstance(wd, QtWidgets.QSpinBox):
                wd.setValue(int(v))
            elif isinstance(wd, QtWidgets.QDateTimeEdit):
                dt = QtCore.QDateTime.fromString(str(v), QtCore.Qt.ISODate) if v else QtCore.QDateTime()
                if not dt.isValid():
                    dt = QtCore.QDateTime.currentDateTimeUtc().addSecs(600)
                wd.setDateTime(dt.toUTC())
            elif isinstance(wd, QtWidgets.QLineEdit):
                wd.setText(str(v))
        self._symbol_changed()

    def _values(self) -> Dict:
        cfg: Dict = {"mode": self.mode()}
        for key, wd in self.w.items():
            if isinstance(wd, QtWidgets.QCheckBox):
                cfg[key] = wd.isChecked()
            elif isinstance(wd, QtWidgets.QComboBox):
                cfg[key] = wd.currentText()
            elif isinstance(wd, (QtWidgets.QDoubleSpinBox, QtWidgets.QSpinBox)):
                cfg[key] = wd.value()
            elif isinstance(wd, QtWidgets.QDateTimeEdit):
                cfg[key] = wd.dateTime().toUTC().toString(QtCore.Qt.ISODate)
            elif isinstance(wd, QtWidgets.QLineEdit):
                cfg[key] = wd.text().strip()
        return cfg

    def _save(self) -> None:
        cfg = self._values()
        for k, v in cfg.items():
            self.settings.set(k, v)
        self.settings.set("geometry", self.saveGeometry())
        self.settings.set("setup_split", self.setup_split.saveState())
        self.settings.set("report_split", self.report.split.saveState())
        self.settings.set("run_hsplit", self.panel.hsplit.saveState())
        self.settings.set("run_vsplit", self.panel.vsplit.saveState())
        self.settings.sync()
        self.status.setText(f"settings saved to {self.settings.path}")

    def mode(self) -> str:
        b = self.mode_group.checkedButton()
        return b.property("mode") if b is not None else "bench"

    def _mode_changed(self) -> None:
        m = self.mode()
        self.mode_stack.setCurrentIndex([k for k, _ in MODES].index(m))
        self.sky_box.setVisible(m in ("eme", "eve", "interop"))
        for key in ("sky_mode", "sky_source", "sky_precomp", "sky_rx_doppler"):
            self.w[key].setEnabled(m in ("eme", "eve"))
        radio = m != "sim"
        for key in ("serial", "tx_gain", "rx_gain", "clock", "time_host", "gpsdo", "lo_offset_khz"):
            self.w[key].setEnabled(radio)
        self._symbol_changed()

    def _symbol_changed(self) -> None:
        v = self.w["variant"].currentText()
        p = EveParams.named(v)
        full = self.w["full_symbol"].isChecked()
        n = FULL_FRAMES.get(v, p.n_frames) if full else int(self.w["n_frames_test"].value())
        t_sym = n * p.n_fft / p.modem_rate
        self.lbl_sym.setText(f"{n} frames per symbol = {t_sym:.1f} s; one message pass = {t_sym * p.n_sym / 60:.1f} min"
                             + ("" if full else "   (TEST length: no on-air significance)"))
        for key in ("n_frames_test", "pilot_frames_test"):
            self.w[key].setEnabled(not full)

    def _browse_archive(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Archive folder", self.w["archive"].text() or ".")
        if d:
            self.w["archive"].setText(d)
            self.report.set_archive(d)

    # ---- actions -------------------------------------------------------------------------
    def _log(self, s: str) -> None:
        self.setup_log.appendPlainText(f"{iso_utc(time.time(), 0)[11:19]}  {s}")
        self.panel.append_log(s)

    def _preview(self) -> None:
        self._save()
        self.preview.setPlainText("building the schedule ...")
        if not self.ctl.start(self._values(), preview_only=True):
            self.preview.setPlainText("busy")

    def _preview_ready(self, text: str) -> None:
        self.preview.setPlainText(text)

    def _start(self) -> None:
        self._save()
        cfg = self._values()
        if cfg["mode"] in ("eme", "eve", "interop") and cfg["full_symbol"] is False:
            r = QtWidgets.QMessageBox.question(self, "Test-length symbols on the air",
                                               "The symbol length is a TEST length, not the 473-frame air interface. "
                                               "A partner station could not decode this. Start anyway?")
            if r != QtWidgets.QMessageBox.Yes:
                return
        if cfg["mode"] in ("eme", "eve") and float(cfg["tx_gain"]) > 0 and cfg["mode"] == "eve":
            pass
        self.report.set_archive(cfg["archive"])
        self.preview.setPlainText("")
        if self.ctl.start(cfg):
            self.tabs.setCurrentWidget(self.panel)
        else:
            self.status.setText("a run is already in progress")

    def _session_ready(self, sess) -> None:
        self.panel.bind(sess)
        self.ctl.panel_bound()
        self.preview.setPlainText(S.describe(sess.sched, sess.model))

    def _session_done(self) -> None:
        self.panel.unbind()          # keeps what is drawn; drops the references
        self.ctl.panel_released()

    def _state(self, st: str) -> None:
        self.status.setText(st)
        running = st != "idle"
        self.btn_start.setEnabled(not running)
        self.btn_preview.setEnabled(not running)
        self.btn_abort.setEnabled(st in ("preparing", "running"))
        self.report.btn_redecode.setEnabled(not running)

    def _finished(self, result: Dict) -> None:
        pdf = result.get("pdf")
        if result.get("error"):
            self.status.setText("failed: " + result["error"])
            QtWidgets.QMessageBox.warning(self, "Run failed", result["error"])
        self.report.refresh()
        if pdf:
            self.report.select(Path(pdf))
            self.tabs.setCurrentWidget(self.report)
            self.status.setText(("DECODED  " if result.get("ok") else "not decoded  ") + result.get("session_id", ""))

    def _redecode(self, session_json: Path) -> None:
        if not self.ctl.redecode(session_json):
            self.status.setText("busy")

    def _help(self) -> None:
        if self._help_dlg is None:
            self._help_dlg = HelpDialog(self)
        self._help_dlg.show()
        self._help_dlg.raise_()

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        if self.ctl.busy:
            r = QtWidgets.QMessageBox.question(self, "A run is in progress", "Abort the run and quit?")
            if r != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
            self.ctl.abort("window closed")
            time.sleep(1.0)
        self._save()
        ev.accept()


def main(argv=None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("DSES EVE modem")
    icon = Path(__file__).resolve().parents[1] / "icons" / "eve_modem.ico"
    if icon.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    win = EveApp()
    win.show()
    return app.exec()

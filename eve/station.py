"""Session controller: key-down and cooldown interlocks, PA duty enforcement, abort, the
flowgraph, the session log (design document 5.1, 7.2, 8.4).

One flowgraph for the whole session (monostatic Venus and EME alike): the schedule-driven
EveToneSource streams from t0 into the usrp_sink (zeros between chunks keep the sample
clock running, so sample n is device time t0 + n / fs exactly), the usrp_source streams
from the same t0 into the decimator and the EveRxSink, which files only the receive
windows. A keyer thread raises the sequencer line T_lead before each chunk and drops it
T_lag after the RF stops; a watchdog drops it if a chunk overruns. Abort (Ctrl-C or a
fault) releases the key first, then stops the flowgraph, then closes the archive.

SimRadio stands in for the B210 for the software bench: the tone source feeds an AWGN
adder whose output is the receive stream, device time is the wall clock, keying is
logged. Everything else in the session is identical, which is the point.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
from gnuradio import gr, blocks, analog

from . import __version__
from .params import EveParams
from .schedule import Schedule
from .doppler import DopplerModel, iso_utc
from . import gr_blocks, modem, sync

PA_T_ON_MAX_S = 300.0       # 7.2: enforced regardless of the schedule when an amplifier is in the chain
PA_T_OFF_MIN_S = 240.0


@dataclass
class SessionOptions:
    out_dir: str = "archive"
    t_lead_s: float = 0.2
    t_lag_s: float = 0.1
    pa_in_chain: bool = True            # enforce the 7.2 hard limits
    rx_doppler_removal: bool = False    # True when this receiver is not the pre-compensated one
    tx_precompensate: bool = True       # monostatic: pre-compensate for our own receiver
    live_decode: bool = True
    start_margin_s: float = 2.0         # flowgraph starts this long before the first chunk
    end_margin_s: float = 2.0
    arm_lead_s: float = 8.0             # start the flowgraph this long before t0: tb.start() with two USRP
                                        # streamers took 3.8 s on the Windows bench (2026-09-11), and a late timed
                                        # command drops the whole TX stream ('L' storm) and the RX start (LATE_COMMAND)
    realtime_mode: bool = True


class SimRadio:
    """Software stand-in for EveRadio: AWGN channel from TX to RX, wall-clock device time."""

    def __init__(self, params: EveParams, cn0_db: Optional[float] = 20.0, rate: Optional[float] = None, seed: int = 1):
        self.p = params
        self.rate = float(rate or params.radio_rate)
        self.rx_rate = self.tx_rate = self.rate
        self.cn0_db = cn0_db
        self.seed = seed
        self.key_log: List[tuple] = []
        self._keyed = False
        self.sim = True

    def open(self):
        return self

    def close(self):
        pass

    def device_time(self) -> float:
        return time.time()

    def key(self, on: bool) -> None:
        self._keyed = bool(on)
        self.key_log.append((time.time(), bool(on)))

    @property
    def keyed(self):
        return self._keyed

    def build_channel(self, tb: gr.top_block, tone_source, rtt_s: float = 0.0):
        """Connect the tone source through a round-trip delay and AWGN; return the block
        whose output is the RX stream."""
        amp = self.p.amplitude
        src = tone_source
        if rtt_s > 0:
            self.delay = blocks.delay(gr.sizeof_gr_complex, int(round(rtt_s * self.rate)))
            tb.connect(tone_source, self.delay)
            src = self.delay
        if self.cn0_db is None:
            return src
        sigma = np.sqrt(amp ** 2 / (10 ** (self.cn0_db / 10.0)) * self.rate)
        # GR's complex Gaussian source puts `ampl` std on each component: total variance 2 ampl^2
        noise = analog.noise_source_c(analog.GR_GAUSSIAN, sigma / np.sqrt(2.0), self.seed)
        adder = blocks.add_cc()
        tb.connect(src, (adder, 0))
        tb.connect(noise, (adder, 1))
        return adder

    def status_text(self) -> str:
        return f"SimRadio: rate {self.rate:.2f} S/s, C/N0 {self.cn0_db} dB-Hz"


@dataclass
class SessionReport:
    session_id: str
    started_utc: str = ""
    finished_utc: str = ""
    radio: str = ""
    chunks_keyed: int = 0
    key_events: List[Dict] = field(default_factory=list)
    tone_samples_on: int = 0
    frames_sent: int = 0
    rx: Dict = field(default_factory=dict)
    live_decode: Dict = field(default_factory=dict)
    aborted: bool = False
    abort_reason: str = ""
    notes: List[str] = field(default_factory=list)


class Session:
    def __init__(self, schedule: Schedule, model: Optional[DopplerModel], radio, opts: Optional[SessionOptions] = None,
                 log: Callable[[str], None] = None):
        self.sched = schedule
        self.model = model
        self.radio = radio
        self.opts = opts or SessionOptions()
        self.log = log or (lambda s: print(s, file=sys.stderr, flush=True))
        self.p = schedule.params
        self.report = SessionReport(schedule.session_id)
        self._abort = threading.Event()
        self.tb: Optional[gr.top_block] = None
        self.tone = None
        self.sink = None
        self.acc: Optional[modem.SymbolAccumulator] = None
        self.listeners: List[Callable[[int, np.ndarray, np.ndarray], None]] = []   # (frame k, samples, metric)
        self.phase = "idle"

    # ---- interlocks (7.2) ------------------------------------------------------------------
    def preflight(self) -> List[str]:
        problems = []
        s = self.sched
        try:
            s.validate()
        except ValueError as e:
            problems.append(f"schedule: {e}")
        if self.opts.pa_in_chain:
            for c in s.chunks:
                if c.tx_stop - c.tx_start > PA_T_ON_MAX_S + 1e-6:
                    problems.append(f"chunk {c.index}: {c.tx_stop - c.tx_start:.0f} s on exceeds the PA limit {PA_T_ON_MAX_S:.0f} s")
            for a, b in zip(s.chunks, s.chunks[1:]):
                if b.tx_start - a.tx_stop < PA_T_OFF_MIN_S - 1e-6:
                    problems.append(f"chunk {b.index}: off time {b.tx_start - a.tx_stop:.0f} s under the PA minimum {PA_T_OFF_MIN_S:.0f} s")
        if self.opts.rx_doppler_removal and self.model is None:
            problems.append("receiver-side Doppler removal needs an ephemeris model")
        if self.opts.tx_precompensate and self.model is None and self.sched.mode != "sim":
            problems.append("transmit pre-compensation needs an ephemeris model")
        return problems

    def abort(self, reason: str) -> None:
        if not self._abort.is_set():
            self.report.aborted = True
            self.report.abort_reason = reason
            self._abort.set()
            try:
                self.radio.key(False)
                self.report.key_events.append({"t": time.time(), "on": False, "why": f"abort: {reason}"})
            except Exception:
                pass

    # ---- keyer ---------------------------------------------------------------------------------
    def _keyer(self) -> None:
        for c in self.sched.chunks:
            t_on = c.tx_start - self.opts.t_lead_s
            t_off = c.tx_stop + self.opts.t_lag_s
            if not self._sleep_until(t_on):
                return
            self.radio.key(True)
            self.phase = f"TX chunk {c.index + 1} of {len(self.sched.chunks)}"
            self.report.key_events.append({"t": self.radio.device_time(), "on": True, "chunk": c.index})
            self.report.chunks_keyed += 1
            limit = t_off + (PA_T_ON_MAX_S if self.opts.pa_in_chain else 3600.0)
            ok = self._sleep_until(t_off, watchdog=limit)
            self.radio.key(False)
            self.phase = f"listening for chunk {c.index + 1} of {len(self.sched.chunks)}"
            self.report.key_events.append({"t": self.radio.device_time(), "on": False, "chunk": c.index})
            if not ok:
                return

    def _sleep_until(self, t_device: float, watchdog: Optional[float] = None) -> bool:
        while not self._abort.is_set():
            now = self.radio.device_time()
            if now >= t_device:
                return True
            if watchdog is not None and now > watchdog:
                self.abort("keyer watchdog: chunk overran the PA limit")
                return False
            time.sleep(min(0.05, max(0.001, t_device - now)))
        return False

    # ---- flowgraph ----------------------------------------------------------------------------
    def _doppler_fn(self):
        if self.model is None:
            return None
        f = self.sched.f_dial_hz
        cache: Dict[int, float] = {}

        def fD(t):
            t = np.atleast_1d(np.asarray(t, dtype=np.float64))
            out = np.empty(t.shape)
            for i, ti in enumerate(t):
                key = int(ti)                      # 1 s resolution: rate <= 0.5 Hz/s
                v = cache.get(key)
                if v is None:
                    v = cache[key] = self.model.doppler_hz(float(key), f)
                out[i] = v
            return out
        return fD

    def build(self, t0: float) -> gr.top_block:
        opts, s, p = self.opts, self.sched, self.p
        tb = gr.top_block(f"eve_session_{s.session_id}")
        fD = self._doppler_fn()
        sim = getattr(self.radio, "sim", False)
        rate = self.radio.tx_rate
        self.tone = gr_blocks.EveToneSource(s, rate, t0, doppler_hz=fD if opts.tx_precompensate else None,
                                            include_if=True, t_end_device=max(c.rx_stop for c in s.chunks) + opts.end_margin_s,
                                            tag_time=not sim)
        windows = [(c.index, c.rx_start, c.rx_stop, c.frame_first) for c in s.chunks]
        f_shift = p.f_if
        self.decim = gr_blocks.make_rx_decimator(p, self.radio.rx_rate, f_shift)
        self.acc = modem.SymbolAccumulator(p, s.frame_map())
        bank = modem.FrameBank(p)
        def on_frame(k, x):
            metric = bank.frame_metric(x)
            if opts.live_decode:
                self.acc.add(k, metric)
            for fn in self.listeners:
                try:
                    fn(k, x, metric)
                except Exception:
                    pass
        meta = {"f_dial_hz": s.f_dial_hz, "f_if_hz": p.f_if, "doppler_applied_on_rx": bool(opts.rx_doppler_removal),
                "tx_precompensated": bool(opts.tx_precompensate), "radio": getattr(self.radio, "status_text", lambda: "B210")()}
        self.sink = gr_blocks.EveRxSink(p, opts.out_dir, s.session_id, windows, self.radio.rx_rate / p.radio_decim,
                                        meta=meta, on_frame=on_frame, t0_if_untagged=t0 if sim else None)
        if sim:
            rtt = self.model.rtt_s(s.t_start) if self.model is not None else 0.0
            rx_in = self.radio.build_channel(tb, self.tone, rtt)
        else:
            tb.connect(self.tone, self.radio.tx)
            rx_in = self.radio.rx.block
            from gnuradio import uhd
            self.radio.rx.block.set_start_time(uhd.time_spec(t0))
        tb.connect(self.decim.connect(tb, rx_in), self.sink)
        self.tb = tb
        return tb

    def _steer_doppler(self):
        """Receiver-side removal: step the decimator's shift with the model once a second."""
        fD = self._doppler_fn()
        base = self.p.f_if
        while not self._abort.is_set() and not (self.tone is not None and self.tone.done):
            t = self.radio.device_time()
            try:
                self.decim.set_center_freq(base + float(fD(t)[0]))
            except Exception:
                pass
            time.sleep(1.0)

    # ---- run -----------------------------------------------------------------------------------
    def run(self) -> SessionReport:
        probs = self.preflight()
        if probs:
            raise ValueError("preflight failed:\n  " + "\n  ".join(probs))
        s, opts = self.sched, self.opts
        self.report.started_utc = iso_utc(time.time(), 0)
        self.report.radio = getattr(self.radio, "status_text", lambda: "")()
        t0 = s.t_start - opts.start_margin_s
        now = self.radio.device_time()
        if now > s.t_start:
            raise ValueError(f"schedule starts in the past (device time {iso_utc(now, 0)}, start {iso_utc(s.t_start, 0)})")
        self.log(f"session {s.session_id}: {len(s.chunks)} chunks, first at {iso_utc(s.t_start, 0)}, "
                 f"flowgraph at {iso_utc(t0, 0)} (in {t0 - now:.1f} s)")
        tb = self.build(t0)
        rt = None
        if opts.realtime_mode:
            try:
                from ._workbench import import_dses_radio
                rt = import_dses_radio().RealtimeMode()
                rt.begin()
            except Exception:
                rt = None
        keyer = threading.Thread(target=self._keyer, name="eve-keyer", daemon=True)
        steer = threading.Thread(target=self._steer_doppler, name="eve-doppler", daemon=True) if opts.rx_doppler_removal else None
        old_handler = None
        try:
            try:
                old_handler = signal.signal(signal.SIGINT, lambda *_: self.abort("operator interrupt"))
            except Exception:
                pass
            # start the flowgraph before t0 so the timed streams are armed (arm_lead_s)
            self._sleep_until(t0 - opts.arm_lead_s)
            if self._abort.is_set():
                return self._finish(rt, old_handler)
            tb.start()
            self.phase = "armed"
            keyer.start()
            if steer:
                steer.start()
            t_end = max(c.rx_stop for c in s.chunks) + opts.end_margin_s
            while not self._abort.is_set():
                if self.tone.done and (getattr(self.radio, "sim", False) or self.radio.device_time() >= t_end):
                    break
                time.sleep(0.2)
            # drain: let the decimator and sink finish the samples already in flight
            last, quiet = -1, 0
            for _ in range(120):
                if self._abort.is_set():
                    break
                n = self.sink.samples_in
                quiet = quiet + 1 if n == last else 0
                last = n
                if quiet >= 3:
                    break
                time.sleep(0.25)
            self.phase = "draining"
            tb.stop()
            tb.wait()
        finally:
            self.phase = "finished" if not self._abort.is_set() else "aborted"
            self.report = self._finish(rt, old_handler)
        return self.report

    def _finish(self, rt, old_handler) -> SessionReport:
        try:
            self.radio.key(False)
        except Exception:
            pass
        if self.sink is not None:
            self.report.rx = self.sink.close()
        if self.tone is not None:
            self.report.tone_samples_on = int(self.tone.samples_on)
            self.report.frames_sent = len(self.tone.frames_sent)
        if self.acc is not None and self.opts.live_decode and self.acc.acc:
            out = self.acc.decode()
            self.report.live_decode = {"ok": out.ok, "text": out.text, "symbols": out.symbols,
                                       "bits_corrected": out.bits_corrected, "frames": self.acc.frames_seen,
                                       "repetitions": self.acc.repetitions}
        if rt is not None:
            rt.end()
        if old_handler is not None:
            try:
                signal.signal(signal.SIGINT, old_handler)
            except Exception:
                pass
        self.report.finished_utc = iso_utc(time.time(), 0)
        self.write_log()
        return self.report

    def write_log(self) -> Path:
        path = Path(self.opts.out_dir) / f"{self.sched.session_id}_session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self.report)
        d["schedule"] = self.sched.to_dict()
        d["options"] = asdict(self.opts)
        d["generated_by"] = f"dses-eve {__version__}"
        path.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
        return path

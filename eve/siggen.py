"""Signal generator: the B210 as a bench source for the RF package (design document 5.8,
decision D25, 2026-09-23).

The station's RF package (the driver, the kilowatt amplifiers, the LNA, the coax and power
switching) is integrated and tuned on the bench before it goes into the feed. For that the
modem has to do three things it already does on the air, without any of the air's risk:
run the sequencer exactly as in a session (LNA off, guard, TX on; TX off, release, LNA on;
the amplifier duty limits when an amplifier is in the chain), send a continuous signal
whose kind the operator chooses, and let the operator set the drive level while it runs
so power-out and compression can be read off the bench instruments step by step.

Signals: **CW** (one tone at the dial frequency plus an offset), **two-tone** (two equal
tones spaced by `spacing_hz` around the offset, the intermodulation test), and the
**EVE waveform** itself (the message's tone hops at the design frame rate, i.e. the real
comb the amplifier will see; `EveToneSource`). The level has two handles: the B210 TX
gain (0 to 89.75 dB, the coarse control) and a digital scale in dB below full scale (fine
steps, no relock of the radio). Both change live from the Setup page while the generator
runs, and an optional timed sweep steps the TX gain from start to stop by `step_db` every
`hold_s` seconds, time-stamping every step in the log and the report so meter readings
line up with steps. The bench meter is the truth; the program records what it commanded.

`GeneratorSession` is a `Session` with the receive side removed: the same keyer thread,
watchdog, abort, preflight and report machinery, a schedule of one on-window (or several
under the amplifier limits) built from the EVE waveform for the requested duration so the
chunk table, the panel and the sequencer's timing work unchanged. In the software
simulation the samples go to a null sink and the sequencer still switches the real relay
board, so the bench wiring can be exercised with nothing on the air.
"""
from __future__ import annotations

import dataclasses
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
from gnuradio import gr, blocks

from . import gr_blocks, schedule as S
from .doppler import iso_utc
from .station import PA_T_OFF_MIN_S, PA_T_ON_MAX_S, Session, SessionOptions

SIGNALS = ("cw", "two_tone", "eve")


class GeneratorSource(gr.sync_block):
    """A phase-continuous CW or two-tone source at the radio rate `fs`, gated by the
    schedule's chunk windows (on inside [tx_start, tx_stop), zero outside), from device
    time `t0` for n_total samples. `offset_hz` places the CW tone (or the two-tone pair's
    center) relative to the dial frequency; `spacing_hz` separates the two tones, each at
    half amplitude so the peak envelope equals the CW case. Mirrors EveToneSource's
    interface (done, samples_on, frames_sent) so GeneratorSession can reuse Session.run."""

    def __init__(self, sched, fs: float, t0: float, kind: str = "cw", offset_hz: float = 0.0,
                 spacing_hz: float = 10e3, amplitude: float = 1.0, t_end: Optional[float] = None,
                 block_len: int = 65536):
        gr.sync_block.__init__(self, name="eve_generator_source", in_sig=None, out_sig=[np.complex64])
        if kind not in ("cw", "two_tone"):
            raise ValueError("GeneratorSource kind is 'cw' or 'two_tone'")
        self.kind = kind
        self.fs = float(fs)
        self.t0 = float(t0)
        self.windows = [(c.tx_start, c.tx_stop) for c in sched.chunks]
        self.t_end = float(t_end) if t_end is not None else max(w[1] for w in self.windows) + 1.0
        self.n_total = int(math.ceil((self.t_end - self.t0) * self.fs))
        self.freqs = [offset_hz] if kind == "cw" else [offset_hz - spacing_hz / 2.0, offset_hz + spacing_hz / 2.0]
        self.amp = float(amplitude) / len(self.freqs)
        self.n_out = 0
        self.samples_on = 0
        self.frames_sent: set = set()
        self.done = False
        self._block_len = block_len
        self.set_min_output_buffer(1 << 22)

    def _gate(self, t: np.ndarray) -> np.ndarray:
        on = np.zeros(t.shape, dtype=bool)
        for a, b in self.windows:
            on |= (t >= a) & (t < b)
        return on

    def work(self, input_items, output_items):
        out = output_items[0]
        if self.done:
            return -1
        n = min(len(out), self._block_len, self.n_total - self.n_out)
        if n <= 0:
            self.done = True
            return -1
        idx = np.arange(self.n_out, self.n_out + n, dtype=np.float64)
        y = np.zeros(n, dtype=np.complex64)
        for f in self.freqs:
            y += (self.amp * np.exp(2j * np.pi * f * idx / self.fs)).astype(np.complex64)
        on = self._gate(self.t0 + idx / self.fs)
        y[~on] = 0
        out[:n] = y
        self.n_out += n
        self.samples_on += int(on.sum())
        if self.n_out >= self.n_total:
            self.done = True
        return n


class _NoSink:
    """Stands in for EveRxSink: the generator receives nothing."""
    samples_in = 0

    def close(self) -> Dict:
        return {}


@dataclass
class SweepSpec:
    """A timed TX-gain sweep: from start_db to stop_db in steps of step_db, holding each
    for hold_s seconds, starting once the transmitter is keyed."""
    start_db: float
    stop_db: float
    step_db: float
    hold_s: float

    def levels(self) -> List[float]:
        if self.step_db <= 0:
            return [self.start_db]
        n = int(math.floor(abs(self.stop_db - self.start_db) / self.step_db + 1e-9)) + 1
        sign = 1.0 if self.stop_db >= self.start_db else -1.0
        return [round(self.start_db + sign * i * self.step_db, 3) for i in range(n)]


def build_generator_schedule(session_id: str, t_start: float, f_dial_hz: float, params, text: str,
                             duration_s: float, pa_in_chain: bool, pilot: bool = True) -> S.Schedule:
    """A schedule whose on-windows cover `duration_s` of transmission: one chunk, or chunks
    of at most the amplifier's on limit separated by its off minimum when `pa_in_chain`.
    Built from the EVE waveform (so the frame table, the panel and the sequencer's timing
    are the session's own) with enough message passes to fill the duration, then cut to the
    chunks that start inside it."""
    from . import doppler as D
    now = t_start - 60.0
    model = D.DopplerModel(D.synthetic_table("siggen", D.DSES_HASWELL, now, now + duration_s + 7200.0, range_km=0.15))
    t_pass = params.t_sym * params.n_sym
    # on-windows of whole frames, rounded UP so a short request is not split by its remainder
    frames = lambda s: math.ceil(s / params.t_frame - 1e-9) * params.t_frame + 1e-6
    chunk_s = frames(min(duration_s, PA_T_ON_MAX_S)) if pa_in_chain else frames(duration_s)
    t_off = PA_T_OFF_MIN_S if pa_in_chain else 0.0
    n_chunks = int(math.ceil(duration_s / chunk_s))
    repeat = max(1, int(math.ceil((duration_s + n_chunks * t_off) / t_pass)) + 1)
    sched = S.build_schedule(session_id, "siggen", model, t_start, f_dial_hz, params=params, text=text,
                             repeat_count=repeat, pilot=pilot, chunk_s=chunk_s, rtt_guard_s=0.0,
                             t_off_min_s=t_off, t_on_max_s=chunk_s, mode="bistatic_tx",
                             notes="signal generator: the frames are timing only unless the EVE waveform is chosen")
    # keep chunks until their on-time adds up to the duration; cut the last one to whole frames
    keep, on_s = [], 0.0
    for c in sched.chunks:
        left = duration_s - on_s
        if left < params.t_frame / 2:
            break
        if c.tx_stop - c.tx_start > left + 1e-6:
            n = max(1, int(round(left / params.t_frame)))
            c = dataclasses.replace(c, frame_last=c.frame_first + n - 1, tx_stop=c.tx_start + n * params.t_frame,
                                    rx_stop=c.rx_start + n * params.t_frame)
        keep.append(c)
        on_s += c.tx_stop - c.tx_start
    sched.chunks = keep or sched.chunks[:1]
    return sched


class GeneratorSession(Session):
    """`Session` with the receive side removed and the transmit source replaced by the
    chosen signal; the level is adjustable live and an optional sweep steps the TX gain.
    `steps` records every level change as {t, utc, tx_gain_db, scale_db, why} for the
    report; `on_step` is called with the same dict (the worker forwards it to the panel)."""

    def __init__(self, schedule, radio, opts: SessionOptions, signal: str = "cw", offset_hz: float = 0.0,
                 spacing_hz: float = 10e3, scale_db: float = -3.0, tx_gain_db: float = 0.0,
                 sweep: Optional[SweepSpec] = None, log: Callable[[str], None] = None, keyer=None,
                 on_step: Optional[Callable[[Dict], None]] = None):
        super().__init__(schedule, None, radio, opts, log=log, keyer=keyer)
        if signal not in SIGNALS:
            raise ValueError(f"signal must be one of {SIGNALS}")
        self.signal = signal
        self.offset_hz = float(offset_hz)
        self.spacing_hz = float(spacing_hz)
        self.scale_db = float(scale_db)
        self.tx_gain_db = float(tx_gain_db)
        self.sweep = sweep
        self.on_step = on_step
        self.steps: List[Dict] = []
        self.scale = None
        self._sweep_thread: Optional[threading.Thread] = None

    # ---- flowgraph ------------------------------------------------------------------------
    def build(self, t0: float) -> gr.top_block:
        """The chosen source through a live digital scale into the transmit sink (a null
        sink in the simulation); nothing on the receive side."""
        s, p = self.sched, self.p
        tb = gr.top_block(f"eve_generator_{s.session_id}")
        sim = getattr(self.radio, "sim", False)
        rate = self.radio.tx_rate
        t_end = max(c.tx_stop for c in s.chunks) + self.opts.end_margin_s
        if self.signal == "eve":
            self.tone = gr_blocks.EveToneSource(s, rate, t0, doppler_hz=None, include_if=True,
                                                t_end_device=t_end, tag_time=not sim)
        else:
            self.tone = GeneratorSource(s, rate, t0, kind=self.signal, offset_hz=self.offset_hz,
                                        spacing_hz=self.spacing_hz, amplitude=p.amplitude, t_end=t_end)
        self.scale = blocks.multiply_const_cc(complex(10 ** (self.scale_db / 20.0)))
        tb.connect(self.tone, self.scale)
        if sim:
            tb.connect(self.scale, blocks.null_sink(gr.sizeof_gr_complex))
        else:
            tb.connect(self.scale, self.radio.tx)
        self.sink = _NoSink()
        self.decim = None
        self.acc = None
        self.tb = tb
        return tb

    # ---- live level -----------------------------------------------------------------------
    def _record(self, why: str) -> None:
        d = {"t": self.radio.device_time(), "utc": iso_utc(self.radio.device_time(), 1),
             "tx_gain_db": self.tx_gain_db, "scale_db": self.scale_db, "why": why}
        self.steps.append(d)
        self.log(f"level: TX gain {self.tx_gain_db:.2f} dB, scale {self.scale_db:+.1f} dB ({why})")
        if self.on_step is not None:
            try:
                self.on_step(d)
            except Exception:
                pass

    def set_level(self, tx_gain_db: Optional[float] = None, scale_db: Optional[float] = None, why: str = "operator") -> None:
        """Apply a new TX gain (B210, dB) and/or digital scale (dB below full scale) at once,
        while running or before; records the step."""
        if tx_gain_db is not None:
            self.tx_gain_db = float(tx_gain_db)
            tx = getattr(self.radio, "tx", None)
            if tx is not None and hasattr(tx, "set_gain"):
                tx.set_gain(self.tx_gain_db, 0)
        if scale_db is not None:
            self.scale_db = float(scale_db)
            if self.scale is not None:
                self.scale.set_k(complex(10 ** (self.scale_db / 20.0)))
        self._record(why)

    def _run_sweep(self) -> None:
        sw = self.sweep
        # wait for the transmitter to be keyed (the first chunk) before stepping
        while not self._abort.is_set() and not self.keyer.keyed:
            time.sleep(0.05)
        for lvl in sw.levels():
            if self._abort.is_set():
                return
            self.set_level(tx_gain_db=lvl, why="sweep")
            t_stop = time.monotonic() + sw.hold_s
            while not self._abort.is_set() and time.monotonic() < t_stop:
                time.sleep(0.05)
        self.log("sweep done")

    def run(self):
        """Session.run with the sweep thread beside it and the starting level recorded."""
        if self.sweep is not None:
            self._sweep_thread = threading.Thread(target=self._run_sweep, name="eve-sweep", daemon=True)
            self._sweep_thread.start()
        self._record("start")
        return super().run()

    def status_line(self) -> str:
        """One line for the panel: signal, level, sweep state."""
        sig = {"cw": f"CW at dial {self.offset_hz / 1e3:+.3f} kHz", "two_tone": f"two tones {self.spacing_hz / 1e3:.3f} kHz apart",
               "eve": "EVE waveform"}[self.signal]
        sw = ""
        if self.sweep is not None:
            sw = f"  sweep {self.sweep.start_db:g} -> {self.sweep.stop_db:g} dB by {self.sweep.step_db:g} every {self.sweep.hold_s:g} s"
        return f"GENERATOR: {sig}; TX gain {self.tx_gain_db:.2f} dB, scale {self.scale_db:+.1f} dB{sw}"


def write_generator_report(sess: GeneratorSession, rep, out_pdf: Path, extra: Optional[Dict[str, str]] = None) -> Path:
    """A one-page PDF: the settings, every level step with its UTC time, and the key events,
    so bench readings can be tied to what the B210 was commanded to do."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    lines = [f"DSES EVE modem signal generator - {sess.sched.session_id}",
             f"started {rep.started_utc}   finished {rep.finished_utc}   aborted={rep.aborted} {rep.abort_reason}",
             f"dial {sess.sched.f_dial_hz / 1e6:.4f} MHz   {sess.status_line()}",
             f"radio: {rep.radio}",
             f"chunks keyed {rep.chunks_keyed}; on-windows: " + ", ".join(f"{iso_utc(c.tx_start, 0)[11:19]}-{iso_utc(c.tx_stop, 0)[11:19]}" for c in sess.sched.chunks),
             ""]
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    lines += ["", "level steps (UTC, TX gain dB, scale dB, why):"]
    for d in sess.steps:
        lines.append(f"  {d['utc'][11:21]}   {d['tx_gain_db']:7.2f}   {d['scale_db']:+6.1f}   {d['why']}")
    lines += ["", "key events:"]
    for e in rep.key_events[:60]:
        lines.append(f"  {iso_utc(e['t'], 1)[11:21]}  {'ON ' if e.get('on') else 'off'}  chunk {e.get('chunk', '')} {e.get('why', '')}")
    with PdfPages(str(out_pdf)) as pdf:
        for start in range(0, len(lines), 58):
            fig = Figure(figsize=(8.5, 11))
            fig.text(0.06, 0.96, "\n".join(lines[start:start + 58]), va="top", ha="left", family="monospace", fontsize=8)
            pdf.savefig(fig)
    return out_pdf

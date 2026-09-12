"""Offline decode of a session archive (design document 5.3, 8.2, 8.4): the decision of
record. Used by tools/eve_decode.py, tools/eve_bench.py, and the application after every
run.

Reads every <session_id>_<chunk>.eve.iq + .json in the archive folder, runs each receive
window through sync.WindowReceiver (pilot for frequency/presence, known-symbol grid check,
block-accumulating tracker), and files frames into one SymbolAccumulator per repetition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import modem, sync
from .schedule import Schedule


def decode_archive(archive_dir, schedule: Schedule, use_pilot: bool = True, track: bool = True,
                   verbose: bool = True, max_windows: Optional[int] = None,
                   log=None) -> Tuple[modem.SymbolAccumulator, List[Dict]]:
    """Returns (accumulator, per-window results). Each result dict has the file name,
    sample count, seconds, first frame, frames filed, pilot detection (bool), frame shift,
    pilot frequency offset, contrast, residual frequency, gap events, and a `line` string."""
    p = schedule.params
    fm = schedule.frame_map()
    acc = modem.SymbolAccumulator(p, fm)
    files = sorted(Path(archive_dir).glob(f"{schedule.session_id}_*.eve.iq"))
    if max_windows:
        files = files[:max_windows]
    if not files:
        raise FileNotFoundError(f"no {schedule.session_id}_*.eve.iq files in {archive_dir}")
    results: List[Dict] = []
    say = log or (print if verbose else (lambda s: None))
    for f in files:
        side = json.loads(Path(str(f)[:-len(".eve.iq")] + ".json").read_text(encoding="utf-8"))
        x = np.fromfile(f, dtype=np.complex64)
        fs = float(side["sample_rate"])
        k_first = int(side["frame_first"])
        # the first archived sample may sit a fraction of a frame into the window
        off_frames = float(side.get("first_sample_frame_offset", 0.0))
        k0 = k_first + int(np.floor(off_frames + 1e-9))
        frac = off_frames - np.floor(off_frames + 1e-9)
        # a grid error under half a frame is harmless (the tone is constant within a
        # symbol); beyond that, skip to the next frame boundary
        if frac > 0.5:
            x = x[int(round((1.0 - frac) * p.n_fft)):]
            k0 += 1
        rx = sync.WindowReceiver(p, fm, acc, use_pilot=use_pilot, track=track)
        r = rx.process(x, k0, fs=fs)
        det = bool(r.sync and r.sync.detected)
        d = {"file": f.name, "samples": int(x.size), "seconds": float(x.size / fs), "frame_first": int(k0),
             "frames_filed": int(r.frames_filed), "pilot_detected": det,
             "frame_shift": int(r.sync.frame_shift) if r.sync else 0,
             "f_offset_hz": float(r.sync.f_offset_hz) if r.sync else 0.0,
             "contrast": float(r.sync.contrast) if r.sync else 0.0,
             "f_residual_hz": float(r.f_residual_hz), "gap_events": int(side.get("gap_events", 0)),
             "gap_samples": int(side.get("gap_samples", 0))}
        d["line"] = (f"{f.name}: {x.size} samples ({x.size / fs:.1f} s), frames {k0}.. filed {r.frames_filed}, "
                     f"pilot {'det' if det else 'none'}"
                     + (f" shift {r.sync.frame_shift:+d} f {r.sync.f_offset_hz:+.2f} Hz contrast {r.sync.contrast:.1f}" if r.sync else "")
                     + f", residual {r.f_residual_hz:+.2f} Hz, gaps {d['gap_events']}")
        results.append(d)
        say(d["line"])
    return acc, results


def summarize(acc: modem.SymbolAccumulator, schedule: Schedule) -> Dict:
    """Per-pass and combined decisions with margins, as plain data for reports."""
    out = {"frames_seen": int(acc.frames_seen), "repetitions": list(acc.repetitions), "passes": []}
    for r in acc.repetitions:
        o = acc.decode([r])
        out["passes"].append({"pass": int(r), "ok": bool(o.ok), "text": o.text, "symbols": [int(s) for s in o.symbols],
                              "bits_corrected": int(o.bits_corrected),
                              "margins_db": [float(m) for m in acc.margin_db([r])]})
    o = acc.decode()
    out["combined"] = {"ok": bool(o.ok), "text": o.text, "symbols": [int(s) for s in o.symbols],
                       "expected": [int(s) for s in schedule.symbols], "bits_corrected": int(o.bits_corrected),
                       "bch_ok": bool(getattr(o, "bch_ok", o.ok)), "crc_ok": bool(getattr(o, "crc_ok", o.ok)),
                       "margins_db": [float(m) for m in acc.margin_db()]}
    return out

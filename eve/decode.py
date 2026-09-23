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
                   log=None, epoch_search_frames: int = 0) -> Tuple[modem.SymbolAccumulator, List[Dict]]:
    """Decode every archived receive window of the schedule's session found in archive_dir
    (design document 5.3, 8.2): the decision of record. Each <session_id>_<NN>.eve.iq
    (complex64 at the modem rate, baseband) is aligned to the frame grid from its .json
    sidecar (first frame number and the first sample's offset into it, in frames; a window
    a few samples short of whole frames is zero-padded), run through sync.WindowReceiver
    with use_pilot / track, and filed into one SymbolAccumulator. log (a callable taking one
    string) or verbose chooses where the per-window lines go; max_windows limits the files.

    epoch_search_frames > 0: the transmitter is a partner whose start may differ from
    our nominal epoch by up to that many frames (interop tests). With a pilot the detector
    searches that range in every window; without one the known message symbols are
    matched over the range on the first window and the shift is applied to all.

    Returns (accumulator, per-window results). Each result dict has the file name,
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
    detector = sync.PilotDetector(p, max_frames=max(3, epoch_search_frames)) if epoch_search_frames > 0 else None
    known_shift: Optional[int] = None
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
        # a window is bounded by device time, so its last frame can come up a few samples
        # short (2026-09-12: 49,151 of 49,152); pad it rather than drop it
        rem = x.size % p.n_fft
        if rem and rem >= 0.9 * p.n_fft:
            x = np.concatenate([x, np.zeros(p.n_fft - rem, dtype=np.complex64)])
        if epoch_search_frames > 0 and not (use_pilot and fm.pilot):
            if known_shift is None:
                known_shift = _epoch_shift(x, k0, schedule, epoch_search_frames)
                say(f"epoch search (known symbols, +/-{epoch_search_frames} frames): partner is {known_shift:+d} frames from our nominal")
            if known_shift > 0:
                x = x[known_shift * p.n_fft:]
            elif known_shift < 0:
                k0 += -known_shift
        rx = sync.WindowReceiver(p, fm, acc, use_pilot=use_pilot, track=track, pilot_detector=detector)
        r = rx.process(x, k0, fs=fs)
        det = bool(r.sync and r.sync.detected)
        d = {"file": f.name, "samples": int(x.size), "seconds": float(x.size / fs), "frame_first": int(k0),
             "frames_filed": int(r.frames_filed), "pilot_detected": det,
             "frame_shift": int(r.sync.frame_shift) if r.sync else 0,
             "f_offset_hz": float(r.sync.f_offset_hz) if r.sync else 0.0,
             "contrast": float(r.sync.contrast) if r.sync else 0.0,
             "f_residual_hz": float(r.f_residual_hz), "gap_events": int(side.get("gap_events", 0)),
             "epoch_shift": int(known_shift or 0),
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


def _epoch_shift(x: np.ndarray, k_first: int, schedule: Schedule, n: int) -> int:
    """Whole-frame shift of a partner's transmission relative to our nominal epoch, by
    matching the known message symbols over -n..+n frames (positive = the partner started
    late: their frame k_first begins `shift` frames into our window)."""
    p = schedule.params
    fm = schedule.frame_map()
    bank = modem.FrameBank(p)
    best, best_s = 0, -np.inf
    span = min(len(x), (p.n_sym * p.n_frames + p.pilot_frames + 2 * n) * p.n_fft)
    for shift in range(-n, n + 1):
        acc = modem.SymbolAccumulator(p, fm)
        if shift >= 0:
            seg = x[shift * p.n_fft:span]
            k0 = k_first
        else:
            seg = x[:span]
            k0 = k_first - shift
        if len(seg) < p.n_fft:
            continue
        acc.add_block(k0, bank.frames_metric(seg))
        c = acc.combined()
        s = float(sum(c[m, d] for m, d in enumerate(schedule.symbols) if c[m].any()))
        if s > best_s:
            best, best_s = shift, s
    return best

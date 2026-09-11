#!/usr/bin/env python3
"""Offline decode of a session archive (design document 5.3, 8.2, 8.4).

    python tools/eve_decode.py ARCHIVE_DIR --schedule SESSION.json [--no-pilot] [--track]
                               [--doppler-table CSV] [--combine-only]

Reads every <session_id>_<chunk>.eve.iq + .json in ARCHIVE_DIR, runs each receive
window through sync.WindowReceiver (pilot for frequency/presence, known-symbol grid
check, block-accumulating tracker), files frames into one SymbolAccumulator per
repetition, and prints the per-pass and combined decisions with margins. The decision of
record is this offline decode, not the live view.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve import modem, sync  # noqa: E402
from eve.schedule import Schedule  # noqa: E402
from eve.doppler import iso_utc  # noqa: E402


def decode_archive(archive_dir, schedule: Schedule, use_pilot=True, track=True, verbose=True, max_windows=None):
    p = schedule.params
    fm = schedule.frame_map()
    acc = modem.SymbolAccumulator(p, fm)
    files = sorted(Path(archive_dir).glob(f"{schedule.session_id}_*.eve.iq"))
    if max_windows:
        files = files[:max_windows]
    if not files:
        raise SystemExit(f"no {schedule.session_id}_*.eve.iq files in {archive_dir}")
    results = []
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
        line = (f"{f.name}: {x.size} samples ({x.size / fs:.1f} s), frames {k0}.. filed {r.frames_filed}, "
                f"pilot {'det' if (r.sync and r.sync.detected) else 'none'}"
                + (f" shift {r.sync.frame_shift:+d} f {r.sync.f_offset_hz:+.2f} Hz contrast {r.sync.contrast:.1f}" if r.sync else "")
                + f", residual {r.f_residual_hz:+.2f} Hz, gaps {side.get('gap_events', 0)}")
        results.append(line)
        if verbose:
            print(line)
    return acc, results


def main(argv=None):
    ap = argparse.ArgumentParser(description="offline decode of an EVE session archive")
    ap.add_argument("archive_dir")
    ap.add_argument("--schedule", required=True, help="session schedule JSON")
    ap.add_argument("--no-pilot", action="store_true")
    ap.add_argument("--no-track", action="store_true")
    ap.add_argument("--max-windows", type=int, default=None)
    a = ap.parse_args(argv)
    sched = Schedule.from_json(a.schedule)
    acc, _ = decode_archive(a.archive_dir, sched, use_pilot=not a.no_pilot, track=not a.no_track)
    p = sched.params
    print(f"\n{sched.session_id}: {acc.frames_seen} frames filed, repetitions {acc.repetitions}")
    for r in acc.repetitions:
        out = acc.decode([r])
        print(f"  pass {r}: {'OK ' if out.ok else 'FAIL'} '{out.text}'  symbols {out.symbols}  "
              f"corrected {out.bits_corrected}  margin dB {np.round(acc.margin_db([r]), 1).tolist()}")
    out = acc.decode()
    print(f"  COMBINED ({len(acc.repetitions)} passes): {'OK ' if out.ok else 'FAIL'} '{out.text}'  symbols {out.symbols}  "
          f"expected {sched.symbols}  corrected {out.bits_corrected}  margin dB {np.round(acc.margin_db(), 1).tolist()}")
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(main())

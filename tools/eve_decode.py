#!/usr/bin/env python3
"""Offline decode of a session archive (design document 5.3, 8.2, 8.4).

    python tools/eve_decode.py ARCHIVE_DIR --schedule SESSION.json [--no-pilot] [--no-track]
                               [--max-windows N]

Reads every <session_id>_<chunk>.eve.iq + .json in ARCHIVE_DIR, runs each receive
window through sync.WindowReceiver (pilot for frequency/presence, known-symbol grid
check, block-accumulating tracker), files frames into one SymbolAccumulator per
repetition, and prints the per-pass and combined decisions with margins. The decision of
record is this offline decode, not the live view. The work is in eve/decode.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve.decode import decode_archive  # noqa: E402,F401  (re-exported for eve_bench.py)
from eve.schedule import Schedule  # noqa: E402


def main(argv=None):
    """Command line: decode ARCHIVE_DIR with the schedule JSON and print the per-pass and
    combined decisions with their margins; returns 0 when the combined decode is ok, else 1."""
    ap = argparse.ArgumentParser(description="offline decode of an EVE session archive")
    ap.add_argument("archive_dir")
    ap.add_argument("--schedule", required=True, help="session schedule JSON")
    ap.add_argument("--no-pilot", action="store_true")
    ap.add_argument("--no-track", action="store_true")
    ap.add_argument("--max-windows", type=int, default=None)
    a = ap.parse_args(argv)
    sched = Schedule.from_json(a.schedule)
    acc, _ = decode_archive(a.archive_dir, sched, use_pilot=not a.no_pilot, track=not a.no_track,
                            max_windows=a.max_windows)
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

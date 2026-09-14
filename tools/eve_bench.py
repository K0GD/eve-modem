#!/usr/bin/env python3
"""B210 loopback bench (design document 5.4 stage 4): run a short EVE schedule on one B210
with the transmitter at (or near) minimum gain, receive the internal TX-to-RX leakage on
RX2 (the Workbench self test proved it is enough at minimum gain), archive it, and decode
offline. Reports underruns/overflows (from the archive gap count), reference lock, rate
readback, keying events, and the decode.

    python tools/eve_bench.py [--n-frames 6] [--repeat 2] [--tx-gain 0] [--rx-gain 30]
                              [--clock internal] [--f-dial 1296e6] [--archive archive_bench]

No antenna is needed and none should be connected to TX/RX for this test.
"""
from __future__ import annotations

import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve import EveParams  # noqa: E402
from eve import doppler as D  # noqa: E402
from eve import schedule as S  # noqa: E402
from eve.station import Session, SessionOptions  # noqa: E402
from eve.radio import EveRadio, RadioConfig  # noqa: E402

from eve.decode import decode_archive  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="EVE B210 loopback bench")
    ap.add_argument("--variant", default="A")
    ap.add_argument("--n-frames", type=int, default=6)
    ap.add_argument("--pilot-frames", type=int, default=2)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--range-km", type=float, default=0.15,
                    help="synthetic one-way range; the leakage path has no delay, so the default puts the receive window on the transmission")
    ap.add_argument("--t-off-min", type=float, default=0.5)
    ap.add_argument("--chunk", type=float, default=2.4, help="chunk length s (EME cadence by default)")
    ap.add_argument("--tx-gain", type=float, default=0.0)
    ap.add_argument("--rx-gain", type=float, default=30.0)
    ap.add_argument("--clock", default="internal")
    ap.add_argument("--serial", default="")
    ap.add_argument("--f-dial", type=float, default=1296e6)
    ap.add_argument("--lo-offset", type=float, default=-300e3)
    ap.add_argument("--tx-port", default="A", choices=["A", "B"], help="transmit frontend (its TX/RX port)")
    ap.add_argument("--rx-port", default="A : RX2", help="receive port: 'A : RX2', 'B : RX2', or the other frontend's TX/RX")
    ap.add_argument("--message", default="K0PRT K0PRT")
    ap.add_argument("--archive", default="archive_bench")
    ap.add_argument("--lead", type=float, default=15.0, help="seconds from now to the first chunk (>= arm lead 8 s + margin)")
    ap.add_argument("--display", action="store_true", help="operator display (PySide6)")
    ap.add_argument("--gpsdo", action="store_true", help="set up and check the Leo Bodnar GPS clock first")
    a = ap.parse_args(argv)

    p = replace(EveParams.named(a.variant), n_frames=a.n_frames, pilot_frames=a.pilot_frames)
    site = D.DSES_HASWELL
    cfg = RadioConfig(serial=a.serial, f_dial_hz=a.f_dial, tx_gain_db=a.tx_gain, rx_gain_db=a.rx_gain,
                      clock_source=a.clock, require_ref_lock=(a.clock != "internal"), lo_offset_hz=a.lo_offset,
                      tx_frontend=a.tx_port, rx_antenna=a.rx_port)
    if a.gpsdo:
        from eve import gpsdo
        rep = gpsdo.preflight()
        print("GPS clock:", rep["config"], "| locked", rep["locked"])
        if not rep["locked"]:
            print("GPS clock not locked; refusing to start", file=sys.stderr)
            return 3
    radio = EveRadio(p, cfg)
    st = radio.open()
    print(st.summary())
    print(f"rate error {radio.rate_error_ppm():+.3f} ppm; PPS verify {radio.verify_pps()}")
    now = radio.device_time()
    tab = D.synthetic_table("bench", site, now - 60, now + 3600, range_km=a.range_km)
    model = D.DopplerModel(tab)
    sid = f"BENCH-{time.strftime('%Y%m%d-%H%M%S')}"
    sched = S.build_schedule(sid, "bench", model, now + a.lead, a.f_dial, params=p, text=a.message,
                             repeat_count=a.repeat, pilot=True, chunk_s=a.chunk, rtt_guard_s=0.0,
                             t_off_min_s=a.t_off_min, t_on_max_s=3600.0, mode="bistatic_tx")
    print(S.describe(sched))
    Path(a.archive).mkdir(parents=True, exist_ok=True)
    sched.to_json(Path(a.archive) / f"{sid}.json")
    opts = SessionOptions(out_dir=a.archive, pa_in_chain=False, tx_precompensate=False, rx_doppler_removal=False,
                          live_decode=True, start_margin_s=2.0)
    sess = Session(sched, model, radio, opts)
    try:
        if a.display:
            from eve.display import run_with_display
            rep = run_with_display(sess, exit_when_done=False)   # window stays up until closed
        else:
            rep = sess.run()
    finally:
        radio.close()
    print(f"\nsession: aborted={rep.aborted} {rep.abort_reason}; chunks keyed {rep.chunks_keyed}; "
          f"frames sent {rep.frames_sent}; rx {rep.rx}")
    print(f"live decode: {rep.live_decode}")
    acc, _ = decode_archive(a.archive, sched)
    out = acc.decode()
    print(f"\nOFFLINE DECODE: {'OK' if out.ok else 'FAIL'} '{out.text}' symbols {out.symbols} expected {sched.symbols}; "
          f"passes {acc.repetitions}; margins dB {[round(m, 1) for m in acc.margin_db()]}")
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Leo Bodnar GPS clock support without the hardware: divider planner, report codec,
plan validation. (The live checks are in docs/bench/gpsdo_2026-09-12.md.)"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import gpsdo  # noqa: E402

FIN = 4_687_500
BENCH_REPORT = bytes.fromhex("0903018c864704000007ff0100072f00002f0000000f32"
                             "00" "320001000000400d030000000000d9dcfe47ffffffff00730000000000000000000000000000")


def test_vendor_plan_and_report_decode():
    cfg = gpsdo.parse_config(BENCH_REPORT)
    assert cfg.out1_on and cfg.out2_on and cfg.level == 1
    p = cfg.plan
    assert (p.fin, p.n3, p.n2_hs, p.n2_ls, p.n1_hs, p.nc1_ls, p.nc2_ls, p.skew, p.bw) == (FIN, 5, 11, 512, 11, 48, 48, 0, 15)
    assert p.f3 == Fraction(937_500) and p.fosc == 5_280_000_000
    assert p.fout1 == 10_000_000 and p.fout2 == 10_000_000
    assert p.check() == []
    assert gpsdo.Plan(FIN, **gpsdo.DEFAULT_PLAN_10MHZ) == p


def test_encode_decode_roundtrip():
    p = gpsdo.Plan(FIN, 5, 11, 512, 11, 48, 96, 0, 15)
    b = gpsdo.encode_plan(p)
    assert len(b) == 61 and b[0] == 0 and b[1] == 4
    # the device echoes the same layout back in report 9 (flags/level in bytes 0-1)
    echo = bytes([9, 0x03, 2]) + b[2:21] + bytes(39)
    back = gpsdo.parse_config(echo)
    assert back.plan == p and back.level == 2 and back.out1_on and back.out2_on


def test_planner_exact_and_fast():
    import time
    t = time.time()
    p = gpsdo.plan_outputs(FIN, 10e6)
    assert p.fout1 == 10_000_000 and p.fout2 == 10_000_000 and p.check() == []
    q = gpsdo.plan_outputs(FIN, 10e6, 5e6)
    assert q.fout1 == 10_000_000 and q.fout2 == 5_000_000 and q.check() == []
    r = gpsdo.plan_outputs(FIN, 10e6, 1e6)
    assert r.fout2 == 1_000_000 and r.check() == []
    assert time.time() - t < 5.0
    try:
        gpsdo.plan_outputs(FIN, 10e6, 1.0)          # 1 PPS is below the 450 Hz floor
        assert False, "1 Hz should be impossible"
    except ValueError:
        pass


def test_plan_checks():
    bad = gpsdo.Plan(FIN, 5, 11, 511, 11, 48, 48)      # odd N2_LS
    assert any("N2_LS" in m for m in bad.check())
    bad = gpsdo.Plan(FIN, 5, 12, 512, 11, 48, 48)      # N2_HS out of range
    assert any("N2_HS" in m for m in bad.check())
    bad = gpsdo.Plan(FIN, 1, 11, 512, 11, 48, 48)      # f3 too high
    assert any("f3" in m for m in bad.check())


if __name__ == "__main__":
    for n in [k for k in dir() if k.startswith("test_")]:
        globals()[n]()
        print("PASS", n)

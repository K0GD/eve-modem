"""Leo Bodnar GPS reference clock (classic dual-output "Precision GPS Reference Clock",
USB VID 0x1dd2 PID 0x2210; the single-output mini is PID 0x2211) over USB HID.

Why: the Haswell HP5065A rubidium failed (2026-09-11); this clock supplies the 10 MHz
reference the B210 needs (design document 4.3, 7.1, O16). The modem sets it up and
checks it before a session.

Protocol (from the open-source lbgpsdo tool, hamarituc; verified on the bench 2026-09-12
against unit G44009): a 2-byte input report carries the signal-loss count and the lock
bits; feature report 9 (60 bytes after the report id) holds the configuration; writes are
feature reports with a command byte. The output frequencies are a divider chain on the
GPS-derived reference fin:

    f3 = fin / N3           (10 kHz .. 2 MHz)
    fosc = f3 * N2_HS * N2_LS   (4.85 .. 5.67 GHz)
    fout1 = fosc / (N1_HS * NC1_LS),  fout2 = fosc / (N1_HS * NC2_LS)   (450 Hz .. 808 MHz)

with N3 in 1..2^19, N2_HS and N1_HS in 4..11, N2_LS even in 2..2^20, NC*_LS 1 or even in
2..2^20. The chain cannot reach 1 Hz, so a 1 PPS output is not something this protocol
can ask for. Settings persist in the clock's flash.

Bench facts (2026-09-12): fin 4,687,500 Hz; factory plan for 10 MHz on both outputs is
N3 5, N2_HS 11, N2_LS 512, N1_HS 11, NC 48 (fosc 5.28 GHz); drive level 1 = 16 mA gives
3.3 V CMOS, about +11 dBm into 50 ohms, in the B210's REF IN range.

CLI:  python -m eve.gpsdo status | config | plan F1 [F2] | set --out1 F1 [--out2 F2]
      [--level 0-3] [--off2] | restore-default
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass, asdict
from fractions import Fraction
from typing import List, Optional, Tuple

VID = 0x1DD2
PIDS = (0x2210, 0x2211)
OUTPUT1, OUTPUT2 = 0x01, 0x02
LEVELS_MA = (8, 16, 24, 32)
LEVELS_DBM = (7.7, 11.4, 12.7, 13.3)          # into 50 ohms, per the product page
_CMD_OUTPUT, _CMD_IDENTIFY, _CMD_LEVEL, _CMD_PLL = 1, 2, 3, 4
_REPORT_CONFIG = 9

F3_MIN, F3_MAX = 10_000, 2_000_000
FOSC_MIN, FOSC_MAX = 4_850_000_000, 6_200_000_000    # the vendor's own program uses 6.1 GHz (bench 2026-09-12)
FOUT_MIN, FOUT_MAX = 450, 808_000_000
DEFAULT_PLAN_10MHZ = dict(n3=5, n2_hs=11, n2_ls=512, n1_hs=11, nc1_ls=48, nc2_ls=48, skew=0, bw=15)


@dataclass
class Plan:
    fin: int
    n3: int
    n2_hs: int
    n2_ls: int
    n1_hs: int
    nc1_ls: int
    nc2_ls: int
    skew: int = 0
    bw: int = 15

    @property
    def f3(self) -> Fraction:
        return Fraction(self.fin, self.n3)

    @property
    def fosc(self) -> Fraction:
        return self.f3 * self.n2_hs * self.n2_ls

    @property
    def fout1(self) -> Fraction:
        return self.fosc / (self.n1_hs * self.nc1_ls)

    @property
    def fout2(self) -> Fraction:
        return self.fosc / (self.n1_hs * self.nc2_ls)

    def check(self) -> List[str]:
        p = []
        if not 1 <= self.n3 <= 1 << 19: p.append("N3 out of range")
        if not 4 <= self.n2_hs <= 11: p.append("N2_HS out of range")
        if not (2 <= self.n2_ls <= 1 << 20 and self.n2_ls % 2 == 0): p.append("N2_LS must be even in 2..2^20")
        if not 4 <= self.n1_hs <= 11: p.append("N1_HS out of range")
        for name, v in (("NC1_LS", self.nc1_ls), ("NC2_LS", self.nc2_ls)):
            if not (v == 1 or (2 <= v <= 1 << 20 and v % 2 == 0)): p.append(f"{name} must be 1 or even in 2..2^20")
        if not F3_MIN <= self.f3 <= F3_MAX: p.append(f"f3 {float(self.f3):.0f} Hz outside 10 kHz..2 MHz")
        if not FOSC_MIN <= self.fosc <= FOSC_MAX: p.append(f"fosc {float(self.fosc)/1e9:.4f} GHz outside 4.85..5.67 GHz")
        for name, v in (("fout1", self.fout1), ("fout2", self.fout2)):
            if not FOUT_MIN <= v <= FOUT_MAX: p.append(f"{name} {float(v):.1f} Hz outside 450 Hz..808 MHz")
        return p

    def summary(self) -> str:
        return (f"fin {self.fin} Hz, N3 {self.n3} (f3 {float(self.f3):.1f} Hz), N2 {self.n2_hs}x{self.n2_ls} "
                f"(fosc {float(self.fosc)/1e9:.6f} GHz), N1_HS {self.n1_hs}, NC1 {self.nc1_ls}, NC2 {self.nc2_ls} -> "
                f"out1 {float(self.fout1):.6f} Hz, out2 {float(self.fout2):.6f} Hz; skew {self.skew}, bw {self.bw}")


def plan_outputs(fin: int, f1: float, f2: Optional[float] = None, skew: int = 0, bw: int = 15) -> Plan:
    """Find an exact divider plan for out1 = f1 and out2 = f2 (default f1).

    fosc must be an integer multiple of f1 * N1_HS and of f2 * N1_HS (so the NC dividers
    are integers) and equal f3 * N2_HS * N2_LS with N2_LS even; so for each N1_HS the
    candidate oscillator frequencies are the multiples of lcm(f1, f2) * N1_HS inside the
    4.85..5.67 GHz window, and for each candidate the input side is solved for the
    smallest N3 that makes N2_LS an even integer. Exact in rational arithmetic."""
    from math import gcd
    f2 = f1 if f2 is None else f2
    F1, F2 = Fraction(f1).limit_denominator(1000), Fraction(f2).limit_denominator(1000)
    # lcm of two fractions
    num = F1.numerator * F2.numerator // gcd(F1.numerator, F2.numerator)
    den = gcd(F1.denominator, F2.denominator)
    L = Fraction(num, den)
    best = None
    for n1_hs in range(4, 12):
        step = L * n1_hs
        k_lo = int(Fraction(FOSC_MIN) / step)
        k_hi = int(Fraction(FOSC_MAX) / step) + 1
        for k in range(max(1, k_lo), k_hi + 1):
            fosc = step * k
            if not FOSC_MIN <= fosc <= FOSC_MAX:
                continue
            nc1, nc2 = fosc / (n1_hs * F1), fosc / (n1_hs * F2)
            if nc1.denominator != 1 or nc2.denominator != 1:
                continue
            nc1, nc2 = int(nc1), int(nc2)
            if not all(v == 1 or (2 <= v <= 1 << 20 and v % 2 == 0) for v in (nc1, nc2)):
                continue
            for n2_hs in range(4, 12):
                # fosc = fin / n3 * n2_hs * n2_ls  ->  n3 * fosc = fin * n2_hs * n2_ls
                for n3 in range(1, 2048):
                    f3 = Fraction(fin, n3)
                    if f3 > F3_MAX:
                        continue
                    if f3 < F3_MIN:
                        break
                    n2_ls = fosc / (f3 * n2_hs)
                    if n2_ls.denominator != 1:
                        continue
                    n2_ls = int(n2_ls)
                    if not (2 <= n2_ls <= 1 << 20 and n2_ls % 2 == 0):
                        continue
                    plan = Plan(fin, n3, n2_hs, n2_ls, n1_hs, nc1, nc2, skew, bw)
                    if plan.check():
                        continue
                    score = (-float(f3), n2_ls, n3)     # high phase-detector rate, small dividers
                    if best is None or score < best[0]:
                        best = (score, plan)
                    break                                # smallest n3 for this (fosc, n2_hs)
    if best is None:
        raise ValueError(f"no exact divider plan for {f1} / {f2} Hz from fin {fin} Hz")
    return best[1]


def parse_config(r: bytes) -> "Config":
    """Decode feature report 9 (with or without its leading report-id byte)."""
    b = r[1:] if r and r[0] == _REPORT_CONFIG else r
    u24 = lambda x: struct.unpack("<I", bytes(x) + bytes(1))[0]
    plan = Plan(fin=u24(b[2:5]), n3=u24(b[5:8]) + 1, n2_hs=b[8] + 4, n2_ls=u24(b[9:12]) + 1,
                n1_hs=b[12] + 4, nc1_ls=u24(b[13:16]) + 1, nc2_ls=u24(b[16:19]) + 1, skew=b[19], bw=b[20])
    return Config(bool(b[0] & OUTPUT1), bool(b[0] & OUTPUT2), int(b[1]), plan, bytes(b[21:]).hex())


def encode_plan(plan: "Plan") -> bytes:
    """The 61-byte PLL command (report id 0, command 4) for a plan."""
    b = bytearray(61)
    b[1] = _CMD_PLL
    b[2:5] = struct.pack("<I", plan.fin)[:3]
    b[5:8] = struct.pack("<I", plan.n3 - 1)[:3]
    b[8] = plan.n2_hs - 4
    b[9:12] = struct.pack("<I", plan.n2_ls - 1)[:3]
    b[12] = plan.n1_hs - 4
    b[13:16] = struct.pack("<I", plan.nc1_ls - 1)[:3]
    b[16:19] = struct.pack("<I", plan.nc2_ls - 1)[:3]
    b[19] = plan.skew
    b[20] = plan.bw
    return bytes(b)


@dataclass
class Status:
    loss_count: int
    sat_lock: bool
    pll_lock: bool

    @property
    def locked(self) -> bool:
        return self.sat_lock and self.pll_lock


@dataclass
class Config:
    out1_on: bool
    out2_on: bool
    level: int
    plan: Plan
    raw_tail: str = ""

    def summary(self) -> str:
        return (f"out1 {'ON' if self.out1_on else 'off'}, out2 {'ON' if self.out2_on else 'off'}, "
                f"level {self.level} ({LEVELS_MA[self.level]} mA, about +{LEVELS_DBM[self.level]} dBm); {self.plan.summary()}")


class LeoBodnarGPSDO:
    def __init__(self, serial: Optional[str] = None):
        import hid                      # hidapi (pip); imported here so the modem runs without it
        self._hid = hid
        devs = [d for d in hid.enumerate(VID, 0) if d["product_id"] in PIDS
                and (serial is None or d["serial_number"] == serial)]
        if not devs:
            raise RuntimeError("no Leo Bodnar GPS reference clock on USB")
        self.info = devs[0]
        self.dev = hid.device()
        self.dev.open_path(self.info["path"])
        self.dev.set_nonblocking(0)

    @property
    def serial(self) -> str:
        return self.info.get("serial_number", "")

    @property
    def product(self) -> str:
        return self.info.get("product_string", "")

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    # ---- read ------------------------------------------------------------------------------
    def status(self, timeout_ms: int = 1500) -> Status:
        buf = self.dev.read(2, timeout_ms)
        if len(buf) < 2:
            raise RuntimeError("no status report from the clock")
        return Status(int(buf[0]), not bool(buf[1] & 0x01), not bool(buf[1] & 0x02))

    def config(self) -> Config:
        r = bytes(self.dev.get_feature_report(_REPORT_CONFIG, 61))
        return parse_config(r)

    # ---- write -----------------------------------------------------------------------------
    def _send(self, buf: bytes):
        assert len(buf) == 61
        n = self.dev.send_feature_report(list(buf))
        if n < 0:
            raise RuntimeError("feature report write failed")
        time.sleep(0.05)

    def set_plan(self, plan: Plan) -> None:
        probs = plan.check()
        if probs:
            raise ValueError("; ".join(probs))
        self._send(encode_plan(plan))

    def set_level(self, level: int) -> None:
        if not 0 <= level <= 3:
            raise ValueError("level 0..3")
        b = bytearray(61); b[1] = _CMD_LEVEL; b[2] = level
        self._send(bytes(b))

    def set_outputs(self, out1: bool, out2: bool) -> None:
        b = bytearray(61); b[1] = _CMD_OUTPUT; b[2] = (OUTPUT1 if out1 else 0) | (OUTPUT2 if out2 else 0)
        self._send(bytes(b))

    def identify(self) -> None:
        """Blink the LEDs (harmless; useful to find the right unit)."""
        b = bytearray(61); b[1] = _CMD_IDENTIFY
        self._send(bytes(b))

    # ---- the modem's setup -------------------------------------------------------------------
    def apply(self, f1: float = 10e6, f2: Optional[float] = None, level: int = 1, out2: bool = True,
              verify: bool = True) -> Config:
        """Program out1 (and out2) and the drive level, then read back and compare."""
        cur = self.config()
        plan = None
        # keep the clock's own oscillator plan (the vendor's jitter-optimized choice) when the
        # requested outputs divide it; otherwise search for a new exact plan
        F1 = Fraction(f1).limit_denominator(1000)
        F2 = F1 if f2 is None else Fraction(f2).limit_denominator(1000)
        nc1 = cur.plan.fosc / (cur.plan.n1_hs * F1)
        nc2 = cur.plan.fosc / (cur.plan.n1_hs * F2)
        if nc1.denominator == 1 and nc2.denominator == 1:
            cand = Plan(cur.plan.fin, cur.plan.n3, cur.plan.n2_hs, cur.plan.n2_ls, cur.plan.n1_hs,
                        int(nc1), int(nc2), cur.plan.skew, cur.plan.bw)
            if not cand.check():
                plan = cand
        if plan is None:
            plan = plan_outputs(cur.plan.fin, f1, f2, skew=cur.plan.skew, bw=cur.plan.bw)
        self.set_plan(plan)
        self.set_level(level)
        self.set_outputs(True, out2)
        time.sleep(0.2)
        new = self.config()
        if verify:
            want = (plan.fout1, plan.fout2 if out2 else None, level, True, out2)
            got = (new.plan.fout1, new.plan.fout2 if out2 else None, new.level, new.out1_on, new.out2_on)
            if want != got:
                raise RuntimeError(f"readback differs: wanted {want}, got {got}")
        return new

    def wait_lock(self, timeout_s: float = 60.0) -> Status:
        deadline = time.monotonic() + timeout_s
        st = self.status()
        while not st.locked and time.monotonic() < deadline:
            time.sleep(1.0)
            st = self.status()
        return st


def preflight(serial: Optional[str] = None, f1: float = 10e6, level: int = 1, apply: bool = True,
              lock_timeout_s: float = 60.0) -> dict:
    """What the session runs before opening the radio: find the clock, make sure out1 is
    10 MHz at the wanted level, wait for GPS and PLL lock. Returns a dict for the log."""
    g = LeoBodnarGPSDO(serial)
    try:
        cfg = g.config()
        changed = False
        want = Fraction(f1).limit_denominator(1000)
        # The modem's standard setup (Rick, 2026-09-12): OUT1 = 10 MHz at level 1 (16 mA,
        # about +11 dBm into the B210's REF IN, whose maximum is +15 dBm), OUT2 OFF. The
        # oscillator plan is left alone whenever OUT1 already reads 10 MHz exactly (the
        # vendor program picks its own plan, e.g. fin 97.6 kHz / 6.1 GHz); only a wrong
        # OUT1 frequency triggers a new plan. This unit cannot make 1 PPS.
        if apply and cfg.plan.fout1 != want:
            cfg = g.apply(f1, None, level, out2=False)
            changed = True
        elif apply and (cfg.level != level or not cfg.out1_on or cfg.out2_on):
            g.set_level(level)
            g.set_outputs(True, False)
            time.sleep(0.2)
            cfg = g.config()
            changed = True
        st = g.wait_lock(lock_timeout_s)
        return {"serial": g.serial, "product": g.product, "changed": changed, "config": cfg.summary(),
                "out1_hz": float(cfg.plan.fout1), "out2_hz": float(cfg.plan.fout2), "level_ma": LEVELS_MA[cfg.level],
                "sat_lock": st.sat_lock, "pll_lock": st.pll_lock, "loss_count": st.loss_count, "locked": st.locked}
    finally:
        g.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Leo Bodnar GPS reference clock over USB HID")
    ap.add_argument("--serial", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("config")
    pl = sub.add_parser("plan"); pl.add_argument("f1", type=float); pl.add_argument("f2", type=float, nargs="?")
    st = sub.add_parser("set"); st.add_argument("--out1", type=float, default=10e6); st.add_argument("--out2", type=float, default=None)
    st.add_argument("--level", type=int, default=None); st.add_argument("--off2", action="store_true")
    sub.add_parser("restore-default", help="the plan read from the unit as delivered: both outputs 10 MHz, level 1 (16 mA)")
    sub.add_parser("identify")
    sub.add_parser("preflight")
    a = ap.parse_args(argv)
    if a.cmd == "preflight":
        print(json.dumps(preflight(a.serial), indent=2)); return 0
    g = LeoBodnarGPSDO(a.serial)
    try:
        if a.cmd == "status":
            s = g.status(); print(f"{g.product} {g.serial}: sat_lock {s.sat_lock} pll_lock {s.pll_lock} loss_count {s.loss_count}")
        elif a.cmd == "config":
            c = g.config(); print(f"{g.product} {g.serial}: {c.summary()}\n  raw tail {c.raw_tail}")
        elif a.cmd == "plan":
            print(plan_outputs(g.config().plan.fin, a.f1, a.f2).summary())
        elif a.cmd == "set":
            cur = g.config()
            new = g.apply(a.out1, a.out2, cur.level if a.level is None else a.level, out2=not a.off2)
            print("applied:", new.summary())
        elif a.cmd == "restore-default":
            cur = g.config()
            g.set_plan(Plan(cur.plan.fin, **DEFAULT_PLAN_10MHZ)); g.set_level(1); g.set_outputs(True, True)
            print("restored:", g.config().summary())
        elif a.cmd == "identify":
            g.identify(); print("LEDs blinking")
    finally:
        g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

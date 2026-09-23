"""Monte Carlo: success rate vs C/N0 and vs frames per symbol (design document 5.1, 5.4
stage 2).

Three estimators, from cheapest to most faithful:
  ser_chi2      ORI's abstract AWGN model (eve_link_check.py): power combining, true
                bin 0.5*noncentral_chi2(2N, 2N gamma_f), others 0.5*chi2(2N).
  ser_frames    Pete's channel.m frame model through the real FrameBank: magnitude or
                power combining, optional Rayleigh, random phase per frame.
  fer_stream    Whole messages through the streaming synthesizer, StreamChannel and
                the receiver (Doppler ramp, gaps, timing offset) -> frame error rate.

Frame error rate from symbol error rate: FER = 1 - (1 - SER)^n_sym (a wrong M-ary
symbol flips ~6 bits, beyond t = 3), the same approximation ORI uses.

Run as a script for the ORI-style table:  python -m eve.montecarlo [--variant B]
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Optional, Sequence

import numpy as np

from .params import EveParams
from . import modem, channel


def fer_from_ser(ser: float, n_sym: int) -> float:
    """Frame (message) error rate from the symbol error rate, 1 - (1 - SER)^n_sym: ORI's
    approximation that any wrong 4096-ary symbol defeats the t = 3 BCH code."""
    return 1.0 - (1.0 - ser) ** n_sym


def ser_chi2(cn0_db: float, params: EveParams, n_frames: Optional[int] = None,
             trials: int = 2000, coh_loss: float = 1.0, rng=None) -> float:
    """ORI's chi-square model (power combining, AWGN, tone exactly on a bin)."""
    rng = rng or np.random.default_rng(0)
    nfr = params.n_frames if n_frames is None else n_frames
    gamma_f = coh_loss * 10.0 ** (cn0_db / 10.0) / params.r_bw
    df = 2 * nfr
    z_true = 0.5 * rng.noncentral_chisquare(df, 2 * nfr * gamma_f, size=trials)
    errs = 0
    chunk = max(1, min(trials, int(2e7 // (params.m - 1))))
    for i in range(0, trials, chunk):
        m = min(chunk, trials - i)
        z_false = 0.5 * rng.chisquare(df, size=(m, params.m - 1)).max(axis=1)
        errs += int(np.sum(z_false >= z_true[i:i + m]))
    return errs / trials


def ser_frames(cn0_db: float, params: EveParams, n_frames: Optional[int] = None,
               trials: int = 200, rayleigh: bool = True, combine: str = "magnitude",
               rng=None) -> float:
    """Pete's runTest.m: one symbol, N frames through channel.m, FrameBank combine,
    argmax. EsNo per frame = C/N0 - 10 log10(R_bw)."""
    rng = rng or np.random.default_rng(1)
    nfr = params.n_frames if n_frames is None else n_frames
    esno = channel.gamma_f_from_cn0(cn0_db, params)
    bank = modem.FrameBank(params, combine)
    errs = 0
    for _ in range(trials):
        d = int(rng.integers(0, params.m))
        frames = channel.pete_tone_frames(d, params, nfr)
        r = channel.pete_channel(frames, esno, rayleigh, rng)
        metric = bank.frames_metric(r.reshape(-1))
        errs += int(np.argmax(metric.sum(axis=0)) != d)
    return errs / trials


def fer_stream(cn0_db: float, params: EveParams, n_frames: Optional[int] = None,
               trials: int = 20, text: str = "K0PRT K0PRT", combine: str = "magnitude",
               f_offset_hz: float = 0.0, f_rate_hz_s: float = 0.0, delay_samples: int = 0,
               gaps: Sequence = (), rayleigh: bool = False, frame_phase: bool = True,
               seed: int = 0, restrict_last: bool = False) -> float:
    """Whole-message frame error rate through the streaming chain: the text is encoded and
    synthesized at the modem rate, passed `trials` times through a StreamChannel at cn0_db
    (dB-Hz) with the Doppler offset (Hz) and rate (Hz/s), timing offset (samples), gaps
    ((t_start, t_stop) seconds), per-frame phase and Rayleigh options (seed + trial index
    per run), and demodulated on the nominal grid. A trial fails unless the decode is ok
    and the text matches; returns the failed fraction."""
    p = params if n_frames is None else replace(params, n_frames=n_frames)
    _, _, syms = modem.encode_message(text, p)
    x = modem.synthesize_message(syms, p)
    fails = 0
    for i in range(trials):
        ch = channel.StreamChannel(p, cn0_db, f_offset_hz=f_offset_hz, f_rate_hz_s=f_rate_hz_s,
                                   delay_samples=delay_samples, gaps=gaps, rayleigh=rayleigh,
                                   frame_phase=frame_phase, seed=seed + i)
        out = modem.demodulate(ch.apply(x), p, combine=combine, restrict_last=restrict_last)
        fails += int(not out.ok or out.text != text)
    return fails / trials


def table(params: EveParams, cn0_list: Sequence[float], trials_chi2: int = 3000,
          trials_frames: int = 0, rayleigh: bool = True) -> str:
    """ORI-style text table: for each C/N0 in dB-Hz the chi-square SER, the FER and a verdict
    (closes below 0.1 FER, marginal below 0.5, otherwise fails), with Pete Wyckoff's frame
    channel (Rayleigh or AWGN) in two more columns when trials_frames > 0."""
    lines = [f"{params.summary()}",
             f"{'C/N0 dB-Hz':>10} {'SER chi2':>9} {'FER':>7}  verdict" +
             (f"  {'SER frames':>10} {'FER':>7} ({'Rayleigh' if rayleigh else 'AWGN'})" if trials_frames else "")]
    for c in cn0_list:
        s = ser_chi2(c, params, trials=trials_chi2)
        f = fer_from_ser(s, params.n_sym)
        verdict = "closes" if f < 0.1 else ("marginal" if f < 0.5 else "fails")
        line = f"{c:>10.2f} {s:>9.4f} {f:>7.3f}  {verdict:8s}"
        if trials_frames:
            s2 = ser_frames(c, params, trials=trials_frames, rayleigh=rayleigh)
            line += f"  {s2:>10.4f} {fer_from_ser(s2, params.n_sym):>7.3f}"
        lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    """Command-line entry (python -m eve.montecarlo): --variant, --cn0 list, --trials,
    --frames-trials and --awgn, then prints table()."""
    ap = argparse.ArgumentParser(description="EVE link Monte Carlo (ORI model + Pete's channel)")
    ap.add_argument("--variant", default="A")
    ap.add_argument("--cn0", type=float, nargs="*", default=[-3, -2, -1.33, -1, 0, 0.65, 1, 2])
    ap.add_argument("--trials", type=int, default=3000)
    ap.add_argument("--frames-trials", type=int, default=0, help="also run Pete's frame channel")
    ap.add_argument("--awgn", action="store_true", help="frame channel without Rayleigh")
    a = ap.parse_args(argv)
    p = EveParams.named(a.variant)
    print(table(p, a.cn0, a.trials, a.frames_trials, rayleigh=not a.awgn))


if __name__ == "__main__":
    main()

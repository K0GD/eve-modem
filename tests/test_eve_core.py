"""Stage 0-2 tests for the EVE modem core (design document 5.4).

Run:  .conda/python.exe -m pytest tests -q        (or: python tests/test_eve_core.py)

Stage 0: reference vectors (Appendix C) and BCH round trip, cross-checked against galois
         when it is installed.
Stage 1: the DSES streaming synthesizer against ORI's equation (tones and phase).
Stage 2: receiver in simulation: ORI's chi-square model and Pete's frame channel agree;
         the streaming chain decodes; frequency offset behaves as the tone map predicts.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import EveParams, bch, message, modem, channel, montecarlo  # noqa: E402

TEXT = "K0PRT K0PRT"
APPX_C_SYMBOLS = [1203, 80, 1317, 1056, 1203, 80, 1317, 1031, 289, 3894, 3168]
APPX_C_CRC = 0x1C48
APPX_C_CODEWORD = ("01001011001100000101000001010010" "01010100001000000100101100110000"
                   "01010000010100100101010000000111" "0001001000011111001101101100011")
APPX_C_MSG_BITS = ("01001011" "00110000" "01010000" "01010010" "01010100" "00100000"
                   "01001011" "00110000" "01010000" "01010010" "01010100" "00")

try:
    import galois  # noqa: F401
    HAVE_GALOIS = True
except Exception:
    HAVE_GALOIS = False


def bits(s: str) -> np.ndarray:
    return np.array([int(c) for c in s], dtype=np.uint8)


# ---- params ----------------------------------------------------------------------------
def test_params_derived():
    a = EveParams.variant_a()
    assert abs(a.modem_rate - 47022.08) < 1e-6
    assert abs(a.radio_rate - 1504706.56) < 1e-6
    assert abs(a.t_sym - 473 / 2.87) < 1e-9
    assert a.samples_per_symbol == 16384 * 473
    assert abs(a.spacing - 5.74) < 1e-12 and a.tone_bin(1203) == 2406
    assert a.msg_bits == 90 and a.coded_bits == 132 and a.bits_per_symbol == 12
    b = EveParams.variant_b()
    assert b.modem_rate == 24576.0 and b.radio_rate == 786432.0 and b.n_frames == 247
    assert abs(b.t_sym - 247 / 1.5) < 1e-9 and b.spacing == 3.0
    m = EveParams.matlab()
    assert m.n_fft == 8192 and m.bit_order == "lsb_first" and m.f_if == 0.0


def test_params_schedule_roundtrip():
    for p in (EveParams.variant_a(), EveParams.variant_b(), replace(EveParams.variant_a(), f_if=30000.0)):
        q = EveParams.from_schedule_dict(p.to_schedule_dict())
        assert q == p


# ---- stage 0: BCH and payload ------------------------------------------------------------
def test_bch_generator():
    assert bch.GEN_POLY == 0o11554743
    assert bch.GEN_POLY.bit_length() - 1 == 21


def test_payload_appendix_c():
    p = message.build_payload(TEXT)
    assert "".join(map(str, p[:90])) == APPX_C_MSG_BITS
    assert message.crc16_ccitt(p[:90]) == APPX_C_CRC
    assert message.bits_to_int(p[90:]) == APPX_C_CRC
    chk = message.verify_payload(p)
    assert chk.ok and chk.text == TEXT
    # a 12th character is truncated: 90 bits = 11.25 characters
    assert np.array_equal(message.build_payload(TEXT + "X")[:88], p[:88])


def test_bch_appendix_c_codeword():
    cw = bch.encode(message.build_payload(TEXT))
    assert "".join(map(str, cw)) == APPX_C_CODEWORD
    assert np.array_equal(cw[:106], message.build_payload(TEXT))


def test_bch_corrects_up_to_three_errors():
    rng = np.random.default_rng(1)
    p = message.build_payload(TEXT)
    cw = bch.encode(p)
    for ne in range(0, 4):
        for _ in range(20):
            rx = cw.copy()
            rx[rng.choice(127, ne, replace=False)] ^= 1
            r = bch.decode(rx)
            assert r.ok and r.n_corrected == ne and np.array_equal(r.message, p)
    # four errors: never silently returns the original as if fine
    caught = 0
    for _ in range(50):
        rx = cw.copy()
        rx[rng.choice(127, 4, replace=False)] ^= 1
        r = bch.decode(rx)
        caught += int((not r.ok) or (not np.array_equal(r.message, p)))
    assert caught == 50
    n, fails = bch.min_distance_check(200, rng)
    assert fails == 0


def test_bch_matches_galois():
    if not HAVE_GALOIS:
        return  # cross-check skipped (galois not installed in this interpreter)
    import galois
    g = galois.BCH(127, 106)
    rng = np.random.default_rng(7)
    for _ in range(100):
        m = rng.integers(0, 2, 106, dtype=np.uint8)
        cw_g = np.array(g.encode(galois.GF2(m)), dtype=np.uint8)
        assert np.array_equal(cw_g, bch.encode(m))
        rx = cw_g.copy()
        rx[rng.choice(127, int(rng.integers(0, 4)), replace=False)] ^= 1
        dec_g = np.array(g.decode(galois.GF2(rx)), dtype=np.uint8)
        r = bch.decode(rx)
        assert r.ok and np.array_equal(r.message, dec_g)


# ---- stage 0/1: symbols and synthesis ----------------------------------------------------
def test_symbols_appendix_c():
    a = EveParams.variant_a()
    _, cw, syms = modem.encode_message(TEXT, a)
    assert syms == APPX_C_SYMBOLS
    assert np.array_equal(modem.unpack_symbols(syms, a), cw)
    assert [round(a.tone_freq(d), 2) for d in syms][:3] == [6905.22, 459.20, 7559.58]
    b = EveParams.variant_b()
    assert modem.encode_message(TEXT, b)[2] == APPX_C_SYMBOLS
    assert [b.tone_freq(d) for d in syms][:3] == [3609.0, 240.0, 3951.0]
    # the MATLAB set packs LSB first: different symbols, same codeword
    m = EveParams.matlab()
    _, cw_m, syms_m = modem.encode_message(TEXT, m)
    assert np.array_equal(cw_m, cw) and syms_m != syms
    assert np.array_equal(modem.unpack_symbols(syms_m, m), cw)


def test_decode_symbols_roundtrip():
    a = EveParams.variant_a()
    out = modem.decode_symbols(APPX_C_SYMBOLS, a)
    assert out.ok and out.text == TEXT and out.bits_corrected == 0
    wrong = list(APPX_C_SYMBOLS)
    wrong[3] ^= 0x001                     # one bit flipped in one symbol: BCH fixes it
    out = modem.decode_symbols(wrong, a)
    assert out.ok and out.text == TEXT and out.bits_corrected == 1
    wrong[3] = APPX_C_SYMBOLS[3] ^ 0xFFF   # a whole wrong symbol (12 bits): beyond t = 3
    out = modem.decode_symbols(wrong, a)
    assert not out.ok


def _short(p: EveParams, n_frames: int) -> EveParams:
    return replace(p, n_frames=n_frames)


def test_loopback_all_variants():
    for base in (EveParams.variant_a(), EveParams.variant_b(), EveParams.matlab()):
        p = _short(base, 3)
        _, _, syms = modem.encode_message(TEXT, p)
        x = modem.synthesize_message(syms, p)
        assert x.size == p.n_frames_msg * p.n_fft
        assert np.allclose(np.abs(x), p.amplitude, atol=1e-5)          # constant envelope
        for combine in ("magnitude", "power"):
            out = modem.demodulate(x, p, combine=combine)
            assert out.ok and out.text == TEXT and out.symbols == syms, (base.variant, combine)


def test_phase_continuous_at_hops():
    p = _short(EveParams.variant_a(), 2)
    _, _, syms = modem.encode_message(TEXT, p)
    x = modem.synthesize_message(syms, p).astype(np.complex128)
    nps = p.samples_per_symbol
    for m in range(1, p.n_sym):
        nb = m * nps
        dphi_old = np.angle(x[nb - 1] * np.conj(x[nb - 2]))
        assert abs(x[nb] - x[nb - 1] * np.exp(1j * dphi_old)) < 1e-5   # no phase step at the hop
        dphi_new = np.angle(x[nb + 2] * np.conj(x[nb + 1]))
        f_new = dphi_new / (2 * np.pi) * p.modem_rate
        assert abs(f_new - p.tone_freq(syms[m])) < 1e-3                 # new tone is on frequency


def test_synthesizer_matches_ori_equation():
    """Stage 1 gate: same tones, phase constant within every symbol (ORI restarts phase at
    each hop, DSES carries it; a constant offset per symbol is the documented difference)."""
    p = _short(EveParams.variant_a(), 4)
    _, _, syms = modem.encode_message(TEXT, p)
    fs = 287000.0                                   # fs / R_bw integer: exact frame boundaries
    t_sym = p.n_frames / p.r_bw
    ori = modem.ori_synthesize(syms, fs, t_sym, p, freq_offset=p.f_if, amplitude=p.amplitude)
    syn = modem.ToneSynthesizer(modem.FrameMap(syms, p), fs, p, include_if=True)
    mine = syn.generate(ori.size)
    assert np.allclose(np.abs(mine), 0.8, atol=1e-5)
    nsps = int(round(t_sym * fs))
    for m in range(p.n_sym):
        seg = mine[m * nsps:(m + 1) * nsps] * np.conj(ori[m * nsps:(m + 1) * nsps])
        assert np.ptp(np.unwrap(np.angle(seg))) < 2e-3
    # block-wise generation is identical to one-shot generation
    syn2 = modem.ToneSynthesizer(modem.FrameMap(syms, p), fs, p, include_if=True)
    parts = [syn2.generate(n) for n in (1000, 12345, ori.size - 13345)]
    assert np.allclose(np.concatenate(parts), mine, atol=1e-4)


def test_frame_map_windows_and_pilot():
    p = _short(EveParams.variant_a(), 5)
    _, _, syms = modem.encode_message(TEXT, p)
    fm = modem.FrameMap(syms, p, on_windows=[(0, 9), (20, 29)], pilot=True)
    pp = replace(p, pilot_frames=2)
    fm = modem.FrameMap(syms, pp, on_windows=[(0, 9), (20, 29)], pilot=True)
    assert fm.tone(0) == pp.pilot_tone and fm.tone(1) == pp.pilot_tone
    assert fm.tone(2) == syms[0] and fm.tone(5) == syms[1]
    assert fm.tone(10) == modem.OFF and fm.tone(19) == modem.OFF
    assert fm.tone(20) == pp.pilot_tone and fm.tone(22) == syms[4]
    syn = modem.ToneSynthesizer(fm, pp.modem_rate, pp, include_if=False)   # baseband, comb at 0
    x = syn.generate(30 * pp.n_fft)
    assert np.all(x[10 * pp.n_fft:20 * pp.n_fft] == 0)                 # silent between windows
    assert np.allclose(np.abs(x[:10 * pp.n_fft]), pp.amplitude, atol=1e-5)
    # the receiver excludes pilot frames from the symbol accumulators and decodes symbols 0-1, 4-5
    bank = modem.FrameBank(pp)
    acc = modem.SymbolAccumulator(pp, fm)
    acc.add_block(0, bank.frames_metric(x))
    assert len(acc.pilot_frames) == 4
    d = acc.decide()
    assert d[0] == syms[0] and d[1] == syms[1] and d[4] == syms[4] and d[5] == syms[5]
    assert acc.count[(0, 0)] == 3 and acc.count[(0, 1)] == 5                # 2 pilot frames of 5


def test_restrict_last_symbol():
    p = EveParams.variant_a()
    acc = modem.SymbolAccumulator(p)
    c = acc.combined(restrict_last=True)
    # 127 - 120 = 7 data bits in the last symbol; MSB first => 5 low bits must be zero
    allowed = np.isfinite(c[-1])
    assert allowed.sum() == 128 and allowed[3168] and not allowed[3169]


# ---- stage 2: channel and Monte Carlo ------------------------------------------------------
def test_stream_channel_cn0_calibration():
    """The noise the channel adds gives the requested C/N0 (measured from an FFT)."""
    p = _short(EveParams.variant_a(), 1)
    d = 1203
    fm = modem.FrameMap([d] * p.n_sym, p)
    x = modem.ToneSynthesizer(fm, p.modem_rate, p, include_if=False).generate(8 * p.n_fft)
    cn0 = 20.0
    ch = channel.StreamChannel(p, cn0, seed=3)
    y = ch.apply(x)
    X = np.abs(np.fft.fft(y.reshape(8, p.n_fft), axis=1)) ** 2
    tone = X[:, p.tone_bin(d)].mean()
    noise_bins = np.delete(np.arange(p.n_fft), [p.tone_bin(d) - 1, p.tone_bin(d), p.tone_bin(d) + 1])
    noise_per_bin = X[:, noise_bins].mean()
    # tone bin power = (A n)^2 ; noise per bin = sigma^2 n ; C/N0 = A^2 / (sigma^2 / fs)
    cn0_meas = 10 * np.log10((tone - noise_per_bin) / noise_per_bin * p.r_bw)
    assert abs(cn0_meas - cn0) < 0.5, cn0_meas


def test_chi2_model_agrees_with_frame_channel():
    p = EveParams.variant_a()
    s1 = montecarlo.ser_chi2(0.0, p, n_frames=30, trials=2000)
    s2 = montecarlo.ser_frames(0.0, p, n_frames=30, trials=150, rayleigh=False, combine="power")
    assert abs(s1 - s2) < 0.06, (s1, s2)
    # at the design point with the full 473 frames the model says the link closes
    s = montecarlo.ser_chi2(0.0, p, trials=600)
    assert montecarlo.fer_from_ser(s, p.n_sym) < 0.1
    # and Variant B is at least as good at the same C/N0 (the +1.1 dB claim)
    sb = montecarlo.ser_chi2(-1.5, EveParams.variant_b(), trials=600)
    sa = montecarlo.ser_chi2(-1.5, p, trials=600)
    assert sb <= sa


def test_stream_chain_decodes_and_frequency_offsets():
    p = EveParams.variant_a()
    assert montecarlo.fer_stream(10.0, p, n_frames=20, trials=3) == 0.0
    # half a bin off: scalloping loss but still decodes at high C/N0
    assert montecarlo.fer_stream(15.0, p, n_frames=20, trials=2, f_offset_hz=0.5 * p.r_bw) == 0.0
    # exactly one bin off: every tone lands on the guard bin, decode must fail
    assert montecarlo.fer_stream(15.0, p, n_frames=20, trials=2, f_offset_hz=p.r_bw) == 1.0
    # a gap of a few frames inside one symbol does not break the decode
    gaps = [(2 * 20 * p.t_frame + 1.0, 2 * 20 * p.t_frame + 2.0)]
    assert montecarlo.fer_stream(10.0, p, n_frames=20, trials=2, gaps=gaps) == 0.0
    # Pete's per-frame random phase and Rayleigh envelope
    assert montecarlo.fer_stream(12.0, p, n_frames=20, trials=2, rayleigh=True) == 0.0


if __name__ == "__main__":
    import time
    names = [n for n in dir() if n.startswith("test_")]
    failed = 0
    for n in names:
        t = time.time()
        try:
            globals()[n]()
            print(f"PASS {n} ({time.time() - t:.1f}s)")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {n}: {e!r}")
    print(f"{len(names) - failed}/{len(names)} passed")
    sys.exit(1 if failed else 0)

"""Modem core: symbol packing, tone map, streaming synthesizer, receiver core
(design document 5.1, 5.2, 5.3, 6.1 to 6.4).

Faithful to the ORI Python conventions. No Doppler model and no synchronization live
here: the synthesizer takes a frequency-offset function and a frame-to-tone map, the
receiver takes frames already aligned to the frame grid. Those come from schedule.py,
doppler.py and sync.py.

Frame/symbol arithmetic (section 6.4): frame k carries symbol m(k) = floor(k / N_frames)
mod N_sym of repetition r(k) = floor(k / (N_frames x N_sym)), unless the schedule says
the frame is off (no output) or a pilot frame (tone d_pilot).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .params import EveParams, MSB_FIRST, LSB_FIRST
from . import bch, message

OFF = -1          # frame-to-tone map value: transmitter silent


# ---- bits <-> symbols -------------------------------------------------------------
def pack_symbols(codeword_bits, params: EveParams) -> List[int]:
    """127 coded bits -> zero-pad to 132 -> 11 symbols of 12 bits (MSB first for ORI,
    LSB first for the MATLAB set)."""
    cw = np.asarray(codeword_bits, dtype=np.uint8).ravel()
    if cw.size != params.bch_n:
        raise ValueError(f"codeword must be {params.bch_n} bits, got {cw.size}")
    b = params.bits_per_symbol
    padded = np.zeros(params.coded_bits, dtype=np.uint8)
    padded[:cw.size] = cw
    syms = []
    for i in range(params.n_sym):
        chunk = padded[i * b:(i + 1) * b]
        if params.bit_order == MSB_FIRST:
            d = message.bits_to_int(chunk)
        elif params.bit_order == LSB_FIRST:
            d = message.bits_to_int(chunk[::-1])
        else:
            raise ValueError(params.bit_order)
        syms.append(int(d))
    return syms


def unpack_symbols(symbols: Sequence[int], params: EveParams) -> np.ndarray:
    """11 symbols -> 132 bits -> the first 127 (the codeword)."""
    b = params.bits_per_symbol
    bits = np.zeros(params.coded_bits, dtype=np.uint8)
    for i, d in enumerate(symbols[:params.n_sym]):
        chunk = message.int_to_bits(int(d), b)
        if params.bit_order == LSB_FIRST:
            chunk = chunk[::-1]
        bits[i * b:(i + 1) * b] = chunk
    return bits[:params.bch_n]


def encode_message(text: str, params: EveParams) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """Text -> (payload 106, codeword 127, symbols)."""
    payload = message.build_payload(text)
    codeword = bch.encode(payload)
    return payload, codeword, pack_symbols(codeword, params)


@dataclass
class DecodeOutcome:
    symbols: List[int]
    codeword_rx: np.ndarray
    bch_ok: bool
    bits_corrected: int
    crc_ok: bool
    text: str
    payload: np.ndarray

    @property
    def ok(self) -> bool:
        return self.bch_ok and self.crc_ok


def decode_symbols(symbols: Sequence[int], params: EveParams) -> DecodeOutcome:
    cw_rx = unpack_symbols(symbols, params)
    res = bch.decode(cw_rx)
    chk = message.verify_payload(res.message)
    return DecodeOutcome(list(int(s) for s in symbols), cw_rx, res.ok, res.n_corrected,
                         bool(chk.ok and res.ok), chk.text, res.message)


# ---- frame-to-tone map (section 6.4) ------------------------------------------------
def frame_symbol_index(k: int, params: EveParams) -> Tuple[int, int]:
    """(repetition r, symbol m) carried by frame k."""
    return k // params.n_frames_msg, (k // params.n_frames) % params.n_sym


class FrameMap:
    """Maps a frame index to the tone it carries: message symbol, pilot, or OFF.

    on_windows: list of (frame_first, frame_last) inclusive; None = always on.
    pilot: if enabled, the first pilot_frames frames of every on-window carry
    pilot_tone instead of the message symbol (section 6.4).
    """

    def __init__(self, symbols: Sequence[int], params: EveParams,
                 on_windows: Optional[List[Tuple[int, int]]] = None,
                 pilot: bool = False, repeat_count: Optional[int] = None):
        self.symbols = [int(s) for s in symbols]
        if len(self.symbols) != params.n_sym:
            raise ValueError(f"need {params.n_sym} symbols, got {len(self.symbols)}")
        self.p = params
        self.on_windows = sorted(on_windows) if on_windows else None
        self.pilot = pilot
        self.repeat_count = repeat_count

    def window_of(self, k: int) -> Optional[Tuple[int, int]]:
        if self.on_windows is None:
            return (0, np.iinfo(np.int64).max)
        for w in self.on_windows:
            if w[0] <= k <= w[1]:
                return w
        return None

    def is_pilot(self, k: int) -> bool:
        if not self.pilot:
            return False
        w = self.window_of(k)
        return w is not None and (k - w[0]) < self.p.pilot_frames

    def tone(self, k: int) -> int:
        """Tone index for frame k, or OFF."""
        w = self.window_of(k)
        if w is None:
            return OFF
        r, m = frame_symbol_index(k, self.p)
        if self.repeat_count is not None and r >= self.repeat_count:
            return OFF
        if self.pilot and (k - w[0]) < self.p.pilot_frames:
            return self.p.pilot_tone
        return self.symbols[m]

    def tones(self, k_first: int, k_last: int) -> np.ndarray:
        return np.array([self.tone(k) for k in range(k_first, k_last + 1)], dtype=np.int64)

    @property
    def n_frames_total(self) -> Optional[int]:
        if self.on_windows is None:
            return None if self.repeat_count is None else self.repeat_count * self.p.n_frames_msg
        return self.on_windows[-1][1] + 1


# ---- streaming synthesizer (section 5.2) --------------------------------------------
class ToneSynthesizer:
    """Phase-continuous, frame-gated NCO at any sample rate.

    For sample n at time t_n = n / fs (t = 0 is frame 0 of the schedule), the output is
    A * exp(j * phi_n) with phi advancing by 2*pi*(f_if + d(k(t_n)) * spacing +
    f_extra(t_n)) / fs per sample, phase carried across symbol hops. Outside an
    on-window the output is zero and the phase is held. f_extra is the caller's
    frequency function (e.g. minus the Doppler pre-compensation; default 0).

    generate(n) returns the next n samples; the object keeps the running sample index.
    """

    def __init__(self, frame_map: FrameMap, fs: float, params: Optional[EveParams] = None,
                 f_extra: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                 amplitude: Optional[float] = None, include_if: bool = True,
                 dtype=np.complex64):
        self.map = frame_map
        self.p = params or frame_map.p
        self.fs = float(fs)
        self.f_extra = f_extra
        self.amp = self.p.amplitude if amplitude is None else float(amplitude)
        self.f_base = self.p.f_if if include_if else 0.0
        self.dtype = dtype
        self.n = 0                  # next sample index
        self.phase = 0.0            # radians, carried
        self.samples_on = 0

    @property
    def t(self) -> float:
        return self.n / self.fs

    def frame_of_samples(self, n: np.ndarray) -> np.ndarray:
        return np.floor(n / self.fs * self.p.r_bw + 1e-9).astype(np.int64)

    def generate(self, n_samples: int) -> np.ndarray:
        n = np.arange(self.n, self.n + n_samples, dtype=np.float64)
        k = self.frame_of_samples(n)
        # tone per frame within this block (frames are long: few distinct values)
        k0, k1 = int(k[0]), int(k[-1])
        tones = self.map.tones(k0, k1)
        d = tones[k - k0]
        on = d != OFF
        f = self.f_base + np.where(on, d, 0) * self.p.spacing
        if self.f_extra is not None:
            f = f + self.f_extra(n / self.fs)
        dphi = 2.0 * np.pi * f / self.fs
        dphi = np.where(on, dphi, 0.0)          # hold phase while off
        phi = self.phase + np.cumsum(dphi)
        out = np.where(on, self.amp * np.exp(1j * (phi - dphi)), 0.0)   # phase at sample start
        self.phase = float(np.mod(phi[-1], 2.0 * np.pi))
        self.n += n_samples
        self.samples_on += int(on.sum())
        return out.astype(self.dtype)

    def generate_frames(self, n_frames: int) -> np.ndarray:
        """Convenience at the modem rate: exactly n_frames x n_fft samples."""
        return self.generate(int(round(n_frames * self.fs / self.p.r_bw)))


def synthesize_message(symbols: Sequence[int], params: EveParams, fs: Optional[float] = None,
                       n_frames: Optional[int] = None, include_if: bool = False,
                       amplitude: Optional[float] = None) -> np.ndarray:
    """One message (all n_sym symbols, n_frames frames each) at fs (default modem rate)."""
    fs = params.modem_rate if fs is None else fs
    p = params if n_frames is None else replace(params, n_frames=n_frames)
    fm = FrameMap(symbols, p)
    syn = ToneSynthesizer(fm, fs, p, include_if=include_if, amplitude=amplitude)
    return syn.generate(int(round(p.n_frames_msg / p.r_bw * fs)))


def ori_synthesize(symbols: Sequence[int], fs: float, t_sym: float, params: EveParams,
                   freq_offset: float = 0.0, amplitude: float = 0.8) -> np.ndarray:
    """ORI's eve_tx_sigmf.synthesize (Pete's equation, global index n, phase restarts at
    each hop): s_n = A exp(j 2 pi (d * spacing + offset) n / fs). Kept for interop tests."""
    nsps = int(round(t_sym * fs))
    total = nsps * len(symbols)
    n = np.arange(total, dtype=np.float64)
    d = np.repeat(np.array(symbols, dtype=np.float64), nsps)[:total]
    f = d * params.spacing + freq_offset
    return (amplitude * np.exp(1j * 2.0 * np.pi * f * n / fs)).astype(np.complex64)


# ---- receiver core (section 6.3) ------------------------------------------------------
class FrameBank:
    """Per-frame FFT and magnitude (or power) at the M candidate tone bins."""

    def __init__(self, params: EveParams, combine: str = "magnitude", window: Optional[np.ndarray] = None):
        self.p = params
        if combine not in ("magnitude", "power"):
            raise ValueError(combine)
        self.combine = combine
        self.bins = params.tone_bin_step * np.arange(params.m)
        self.window = window

    def frame_metric(self, x_frame: np.ndarray) -> np.ndarray:
        if x_frame.size != self.p.n_fft:
            raise ValueError(f"frame must be {self.p.n_fft} samples, got {x_frame.size}")
        x = x_frame if self.window is None else x_frame * self.window
        X = np.fft.fft(x)
        v = np.abs(X[self.bins])
        return v if self.combine == "magnitude" else v * v

    def frames_metric(self, x: np.ndarray, n_frames: Optional[int] = None) -> np.ndarray:
        """x contiguous at the modem rate from a frame boundary -> (n_frames, M)."""
        nf = x.size // self.p.n_fft if n_frames is None else n_frames
        X = np.fft.fft(x[: nf * self.p.n_fft].reshape(nf, self.p.n_fft) *
                       (1.0 if self.window is None else self.window), axis=1)
        v = np.abs(X[:, self.bins])
        return v if self.combine == "magnitude" else v * v


class SymbolAccumulator:
    """Files frame metrics into (repetition, symbol) accumulators and decides."""

    def __init__(self, params: EveParams, frame_map: Optional[FrameMap] = None):
        self.p = params
        self.map = frame_map
        self.acc: Dict[Tuple[int, int], np.ndarray] = {}
        self.count: Dict[Tuple[int, int], int] = {}
        self.frames_seen = 0
        self.pilot_frames: List[Tuple[int, np.ndarray]] = []

    def add(self, k: int, metric: np.ndarray) -> None:
        self.frames_seen += 1
        if self.map is not None:
            if self.map.window_of(k) is None:
                return
            if self.map.is_pilot(k):
                self.pilot_frames.append((k, metric))
                return
        key = frame_symbol_index(k, self.p)
        if key in self.acc:
            self.acc[key] += metric
            self.count[key] += 1
        else:
            self.acc[key] = metric.astype(np.float64).copy()
            self.count[key] = 1

    def add_block(self, k_first: int, metrics: np.ndarray) -> None:
        for i in range(metrics.shape[0]):
            self.add(k_first + i, metrics[i])

    @property
    def repetitions(self) -> List[int]:
        return sorted({r for r, _ in self.acc})

    def combined(self, reps: Optional[Iterable[int]] = None, restrict_last: bool = False) -> np.ndarray:
        """(n_sym, M) metric summed over the chosen repetitions (all by default)."""
        reps = set(self.repetitions if reps is None else reps)
        out = np.zeros((self.p.n_sym, self.p.m))
        for (r, m), v in self.acc.items():
            if r in reps:
                out[m] += v
        if restrict_last:
            # symbol n_sym-1 carries only (bch_n - 12*(n_sym-1)) data bits; pad bits are 0
            data_bits = self.p.bch_n - self.p.bits_per_symbol * (self.p.n_sym - 1)
            pad = self.p.bits_per_symbol - data_bits
            allowed = np.zeros(self.p.m, dtype=bool)
            if self.p.bit_order == MSB_FIRST:
                allowed[(np.arange(self.p.m) & ((1 << pad) - 1)) == 0] = True
            else:
                allowed[np.arange(self.p.m) < (1 << data_bits)] = True
            out[-1] = np.where(allowed, out[-1], -np.inf)
        return out

    def decide(self, reps: Optional[Iterable[int]] = None, restrict_last: bool = False) -> List[int]:
        c = self.combined(reps, restrict_last)
        return [int(np.argmax(c[m])) for m in range(self.p.n_sym)]

    def decode(self, reps: Optional[Iterable[int]] = None, restrict_last: bool = False) -> DecodeOutcome:
        return decode_symbols(self.decide(reps, restrict_last), self.p)

    def margin_db(self, reps: Optional[Iterable[int]] = None) -> np.ndarray:
        """Per-symbol ratio (dB) of the winning bin to the runner-up: a confidence view."""
        c = self.combined(reps)
        out = np.zeros(self.p.n_sym)
        for m in range(self.p.n_sym):
            s = np.sort(c[m])
            out[m] = 20 * np.log10(s[-1] / max(s[-2], 1e-30)) if s[-2] > 0 else np.inf
        return out


def demodulate(x: np.ndarray, params: EveParams, frame_map: Optional[FrameMap] = None,
               k_first: int = 0, combine: str = "magnitude", restrict_last: bool = False) -> DecodeOutcome:
    """Contiguous modem-rate samples starting at frame k_first -> decode outcome."""
    bank = FrameBank(params, combine)
    acc = SymbolAccumulator(params, frame_map)
    acc.add_block(k_first, bank.frames_metric(x))
    return acc.decode(restrict_last=restrict_last)

"""Synchronization: everything the reference design does not have, kept out of the
modem core (design document 4.3 to 4.5, 5.1, 6.4).

  mix()               apply a frequency function (e.g. -Doppler) to a stream, phase-continuous.
  PilotDetector       find the pilot tone in the first frames of a receive window: recovers
                      the frame-grid offset (samples) and the frequency offset (Hz).
  grid_search()       frame-grid search without a pilot: choose the sample offset that
                      maximizes the decision contrast (blind) or the matched metric
                      (known symbols, monostatic).
  FrequencyTracker    residual frequency estimate from the decided tone's neighbors,
                      smoothed over frames; feeds the receive NCO.
  WindowReceiver      one receive window end to end: Doppler removal, sync, frame
                      accumulation into a SymbolAccumulator (repeat-and-combine happens
                      there, across windows and sessions via merge()).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .params import EveParams
from . import modem


# ---- NCO ----------------------------------------------------------------------------------
def mix(x: np.ndarray, fs: float, f_of_t: Callable[[np.ndarray], np.ndarray], t0: float = 0.0,
        sign: float = -1.0, phase0: float = 0.0) -> Tuple[np.ndarray, float]:
    """Multiply x by exp(j * sign * phi(t)), phi the integral of 2 pi f(t); returns the
    mixed samples and the end phase so the next block continues without a step. sign = -1
    removes a positive frequency offset f(t)."""
    n = x.size
    t = t0 + np.arange(n) / fs
    f = np.asarray(f_of_t(t), dtype=np.float64)
    if f.ndim == 0:
        f = np.full(n, float(f))
    dphi = 2.0 * np.pi * f / fs
    phi = phase0 + np.concatenate([[0.0], np.cumsum(dphi[:-1])])
    y = x * np.exp(1j * sign * phi)
    return y, float(np.mod(phi[-1] + dphi[-1], 2 * np.pi))


def shift_hz(x: np.ndarray, fs: float, f_hz: float) -> np.ndarray:
    """Move the spectrum UP by f_hz (multiply by exp(+j 2 pi f t)); to remove a
    measured offset f_res, call shift_hz(x, fs, -f_res)."""
    return mix(x, fs, lambda t: np.full(t.size, f_hz), sign=+1.0)[0]


# ---- spectrum helpers ------------------------------------------------------------------
def frame_spectra(x: np.ndarray, params: EveParams, offset: int = 0, n_frames: Optional[int] = None,
                  zoom: int = 1) -> np.ndarray:
    """|FFT| of consecutive frames starting at sample offset; zoom > 1 zero-pads for
    finer frequency resolution (bin = R_bw / zoom)."""
    nfft = params.n_fft
    avail = (x.size - offset) // nfft
    nf = avail if n_frames is None else min(n_frames, avail)
    if nf <= 0:
        return np.zeros((0, nfft * zoom))
    blk = x[offset:offset + nf * nfft].reshape(nf, nfft)
    return np.abs(np.fft.fft(blk, n=nfft * zoom, axis=1))


# ---- pilot -------------------------------------------------------------------------------
@dataclass
class SyncEstimate:
    sample_offset: int          # window start on the frame grid (whole-frame steps from nominal)
    f_offset_hz: float          # residual frequency offset (positive = received high)
    metric: float               # pilot power sum at the best hypothesis
    contrast: float             # sigma of the pilot bin above the neighborhood noise (presence)
    detected: bool
    frame_shift: int = 0        # whole frames the epoch was found off from nominal


class PilotDetector:
    """Pilot: frequency offset, presence, and a whole-frame epoch check.

    What the waveform allows. A tone that is constant for hundreds of frames carries
    timing information only at its edges, so no receiver can fix sub-frame timing from
    it at low SNR; nor does it need to: within a symbol every frame is the same tone, a
    one-frame grid error costs 1/N_frames of the energy, and GPS time plus the ephemeris
    place the grid to milliseconds (design document 4.3). What the pilot can do well is
    (1) measure the frequency offset, by summing the power of Np frames in a zero-padded
    FFT around the pilot bin (resolution R_bw / zoom), (2) confirm the signal is there
    (contrast = sigma of the pilot bin's power sum above the neighborhood noise), and
    (3) catch a gross epoch error: the whole-frame shift in +/- max_frames that
    maximizes the pilot sum plus, when first_symbol is given, the following frames'
    sum at that symbol's tone (the transition is the only timing edge there is).

    Statistics for (2): the pilot cell is ~ Np (1 + g) against Np +/- sqrt(Np), g the
    per-frame bin SNR. At the 0 dB-Hz design point (g = 0.35 for Variant A) 40 frames
    give ~2 sigma: at Venus the pilot is a live sanity check and the ephemeris is the
    sync of record; at EME strength (+10 dB-Hz and up) it is decisive.
    """

    def __init__(self, params: EveParams, max_frames: int = 3, max_hz: float = 10.0,
                 zoom: int = 8, detect_sigma: float = 4.0, edge_frames: int = 4):
        self.p = params
        self.max_frames = max_frames
        self.max_hz = max_hz
        self.zoom = zoom
        self.detect_sigma = detect_sigma
        self.edge_frames = edge_frames
        self.half_bins = int(np.ceil(max_hz / params.r_bw))

    def _frame_pow(self, x: np.ndarray, u: int) -> Optional[np.ndarray]:
        p = self.p
        if u < 0 or u + p.n_fft > x.size:
            return None
        return np.abs(np.fft.fft(x[u:u + p.n_fft])) ** 2

    def _sum_frames(self, x: np.ndarray, u0: int, n_frames: int, bins: np.ndarray) -> np.ndarray:
        acc = np.zeros(bins.size)
        for i in range(n_frames):
            P = self._frame_pow(x, u0 + i * self.p.n_fft)
            if P is not None:
                acc += P[bins % self.p.n_fft]
        return acc

    def presence(self, x: np.ndarray, offset: int) -> Tuple[float, float, int]:
        """(power sum at the best bin near the pilot, contrast in sigma, best bin index
        relative to the pilot bin) over the pilot frames starting at offset."""
        p = self.p
        wide = 8 * self.half_bins + 32
        bins = p.tone_bin(p.pilot_tone) + np.arange(-wide, wide + 1)
        acc = self._sum_frames(x, offset, p.pilot_frames, bins)
        near = np.abs(np.arange(-wide, wide + 1)) <= self.half_bins
        noise = acc[~near]
        med, sd = float(np.median(noise)), float(np.std(noise)) or 1.0
        j = int(np.argmax(np.where(near, acc, -np.inf)))
        return float(acc[j]), (float(acc[j]) - med) / sd, j - wide

    def frequency(self, x: np.ndarray, offset: int) -> float:
        p = self.p
        S = frame_spectra(x, p, offset, p.pilot_frames, self.zoom)
        if S.shape[0] == 0:
            return 0.0
        acc = (S ** 2).sum(axis=0)
        center = p.tone_bin(p.pilot_tone) * self.zoom
        half = int(round(self.max_hz / p.r_bw * self.zoom))
        idx = np.arange(center - half, center + half + 1) % acc.size
        j = int(np.argmax(acc[idx]))
        return (j - half) * p.r_bw / self.zoom

    def search(self, x: np.ndarray, nominal_offset: int = 0, first_symbol: Optional[int] = None) -> SyncEstimate:
        p = self.p
        n = p.n_fft
        # 1. frequency offset at the nominal grid, then work on the exact bins
        f_off = self.frequency(x, nominal_offset)
        xc = shift_hz(x, p.modem_rate, -f_off) if f_off else x
        pbin = np.array([p.tone_bin(p.pilot_tone)])
        sbin = None if first_symbol is None else np.array([p.tone_bin(first_symbol)])
        # 2. whole-frame epoch check: the two edges of the pilot are the only timing marks
        best = None
        for shift in range(-self.max_frames, self.max_frames + 1):
            o = nominal_offset + shift * n
            if o < 0:
                continue
            m = float(self._sum_frames(xc, o, p.pilot_frames, pbin)[0])
            m -= float(self._sum_frames(xc, o - self.edge_frames * n, self.edge_frames, pbin)[0])
            if sbin is not None:
                m += float(self._sum_frames(xc, o + p.pilot_frames * n, self.edge_frames, sbin)[0])
                m -= float(self._sum_frames(xc, o + (p.pilot_frames - self.edge_frames) * n, self.edge_frames, sbin)[0])
            if best is None or m > best[0]:
                best = (m, o, shift)
        m, o, shift = best
        # 3. presence and a refined frequency at the chosen grid
        if shift:
            f_off = self.frequency(x, o)
        power, contrast, _ = self.presence(x, o)
        return SyncEstimate(o, f_off, power, contrast, bool(contrast >= self.detect_sigma), shift)


# ---- frame-grid search without a pilot ------------------------------------------------------
def grid_search(x: np.ndarray, params: EveParams, k_first: int, offsets: Sequence[int],
                frame_map: Optional[modem.FrameMap] = None, known_symbols: Optional[Sequence[int]] = None,
                combine: str = "magnitude") -> Tuple[int, np.ndarray]:
    """Score each candidate sample offset; return (best offset, scores). With known
    symbols the score is the accumulated metric at the expected tones (matched); blind,
    it is the summed decision contrast (winner minus mean, in units of the spread)."""
    bank = modem.FrameBank(params, combine)
    scores = np.zeros(len(offsets))
    for i, o in enumerate(offsets):
        if o < 0:
            scores[i] = -np.inf
            continue
        acc = modem.SymbolAccumulator(params, frame_map)
        acc.add_block(k_first, bank.frames_metric(x[o:]))
        c = acc.combined()
        if known_symbols is not None:
            scores[i] = float(sum(c[m, d] for m, d in enumerate(known_symbols)))
        else:
            s = 0.0
            for m in range(params.n_sym):
                row = c[m]
                if not row.any():
                    continue
                mu, sd = row.mean(), row.std() or 1.0
                s += (row.max() - mu) / sd
            scores[i] = s
    return int(offsets[int(np.argmax(scores))]), scores


# ---- residual frequency tracking -------------------------------------------------------------
class FrequencyTracker:
    """Residual frequency from zoomed spectra accumulated over blocks of frames.

    Per-frame decisions are unreliable at Venus SNR (the noise maximum over 4096 bins
    beats a tone at a few dB per frame), so for each frame the tracker evaluates the
    spectrum exactly at n_zoom points spanning +/- span_bins around the expected tone
    (or the strongest candidate when the tone is unknown), sums the power over `block`
    frames, and takes the peak with a parabolic refinement: resolution R_bw / zoom,
    unbiased, and the guard bins' noise does not pull it toward zero the way three-bin
    interpolation does. update(X_time, ...) takes the TIME-DOMAIN frame, already
    corrected by f_offset_hz; each block adds gain x residual to the running correction.
    """

    def __init__(self, params: EveParams, block: int = 20, gain: float = 0.8, min_snr: float = 1.3,
                 zoom: int = 8, span_bins: float = 1.0):
        self.p = params
        self.block = block
        self.gain = gain
        self.min_snr = min_snr
        self.zoom = zoom
        self.f_total_hz = 0.0
        self.n_blocks = 0
        self.history: List[float] = []
        n = params.n_fft
        self._deltas = np.arange(-span_bins, span_bins + 1e-9, 1.0 / zoom)     # bins
        idx = np.arange(n)
        self._E = np.exp(-2j * np.pi * np.outer(self._deltas, idx) / n)        # (n_zoom, n)
        self._base = np.exp(-2j * np.pi * idx / n)                             # one-bin rotation
        self._acc = np.zeros(self._deltas.size)
        self._noise = 0.0
        self._n = 0
        self._cand = np.zeros(params.m)

    @property
    def f_offset_hz(self) -> float:
        return self.f_total_hz

    def zoom_power(self, frame: np.ndarray, tone: int) -> np.ndarray:
        """|spectrum|^2 at the zoom points around tone's bin."""
        b = self.p.tone_bin(tone)
        demod = frame * (self._base ** b)
        return np.abs(self._E @ demod) ** 2

    def update(self, frame: np.ndarray, tone: Optional[int] = None, X_abs: Optional[np.ndarray] = None) -> Optional[float]:
        p = self.p
        if X_abs is None:
            X_abs = np.abs(np.fft.fft(frame))
        cand = X_abs[p.tone_bin_step * np.arange(p.m)] ** 2
        if tone is None:
            self._cand += cand
            d = int(np.argmax(self._cand))
        else:
            d = int(tone)
        self._acc += self.zoom_power(frame, d)
        self._noise += float(np.median(cand))
        self._n += 1
        if self._n < self.block:
            return None
        acc, noise = self._acc.copy(), self._noise
        self._acc[:] = 0.0
        self._noise = 0.0
        self._n = 0
        self._cand[:] = 0.0
        j = int(np.argmax(acc))
        if acc[j] / max(noise, 1e-30) < self.min_snr:
            return None
        delta = float(self._deltas[j])
        if 0 < j < acc.size - 1:
            y0, y1, y2 = acc[j - 1], acc[j], acc[j + 1]
            den = 2 * y1 - y0 - y2
            if den > 0:
                delta += float(np.clip(0.5 * (y2 - y0) / den, -0.5, 0.5)) / self.zoom
        self.n_blocks += 1
        self.history.append(delta)
        self.f_total_hz += self.gain * delta * p.r_bw
        return delta


# ---- one receive window ----------------------------------------------------------------------
@dataclass
class WindowResult:
    k_first: int
    n_frames: int
    sync: Optional[SyncEstimate]
    f_residual_hz: float
    frames_filed: int


class WindowReceiver:
    """Process one receive window of modem-rate samples.

    x: samples whose first sample nominally coincides with the expected arrival of frame
    k_first (schedule epoch + tx offset + RTT). doppler_hz(t) is the two-way Doppler the
    receiver must remove (None if the transmitter pre-compensated for this receiver, the
    monostatic default). The pilot, when present, corrects the frame grid and the
    residual frequency; the tracker follows what is left.
    """

    def __init__(self, params: EveParams, frame_map: Optional[modem.FrameMap], accumulator: modem.SymbolAccumulator,
                 combine: str = "magnitude", use_pilot: bool = True, track: bool = True,
                 pilot_detector: Optional[PilotDetector] = None, tracker_block: int = 20):
        self.p = params
        self.map = frame_map
        self.acc = accumulator
        self.bank = modem.FrameBank(params, combine)
        self.use_pilot = use_pilot and frame_map is not None and frame_map.pilot
        self.track = track
        self.pilot_detector = pilot_detector or PilotDetector(params)
        self.tracker_block = tracker_block

    def process(self, x: np.ndarray, k_first: int, fs: Optional[float] = None,
                doppler_hz: Optional[Callable[[np.ndarray], np.ndarray]] = None, t0: float = 0.0,
                nominal_offset: int = 0) -> WindowResult:
        p = self.p
        fs = p.modem_rate if fs is None else fs
        if doppler_hz is not None:
            x, _ = mix(x, fs, doppler_hz, t0=t0, sign=-1.0)
        est = None
        offset = nominal_offset
        f_res = 0.0
        if self.use_pilot:
            first = None
            if self.map is not None:
                t0_sym = self.map.tone(k_first + p.pilot_frames)
                first = None if t0_sym in (modem.OFF, p.pilot_tone) else int(t0_sym)
            est = self.pilot_detector.search(x, nominal_offset, first_symbol=first)
            if est.detected:
                offset, f_res = est.sample_offset, est.f_offset_hz
        if f_res:
            x = shift_hz(x, fs, -f_res)
        tracker = FrequencyTracker(p, block=self.tracker_block) if self.track else None
        nfft = p.n_fft
        nf = (x.size - offset) // nfft
        filed = 0
        phase = 0.0
        for i in range(nf):
            frame = x[offset + i * nfft: offset + (i + 1) * nfft]
            k = k_first + i
            if tracker is not None and tracker.f_offset_hz:
                frame, phase = mix(frame, fs, lambda t, f=tracker.f_offset_hz: np.full(t.size, f),
                                   sign=-1.0, phase0=phase)
            X = np.abs(np.fft.fft(frame))
            metric = X[self.bank.bins] if self.bank.combine == "magnitude" else X[self.bank.bins] ** 2
            self.acc.add(k, metric)
            filed += 1
            if tracker is not None:
                expected = self.map.tone(k) if self.map is not None else None
                tracker.update(frame, expected if (expected is not None and expected != modem.OFF) else None, X_abs=X)
        return WindowResult(k_first, nf, est, f_res + (tracker.f_offset_hz if tracker else 0.0), filed)


def merge_accumulators(accs: Sequence[modem.SymbolAccumulator], params: EveParams) -> modem.SymbolAccumulator:
    """Repeat-and-combine across windows or sessions: sum the (repetition, symbol)
    accumulators; repetitions from different sessions are re-indexed so they add."""
    out = modem.SymbolAccumulator(params)
    r_base = 0
    for a in accs:
        reps = a.repetitions
        for (r, m), v in a.acc.items():
            key = (r_base + reps.index(r), m)
            if key in out.acc:
                out.acc[key] += v
                out.count[key] += a.count[(r, m)]
            else:
                out.acc[key] = v.copy()
                out.count[key] = a.count[(r, m)]
        r_base += len(reps)
        out.frames_seen += a.frames_seen
    return out

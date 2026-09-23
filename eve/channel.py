"""Channel models (design document 5.1).

pete_channel: Pete Wyckoff's channel.m per frame: AWGN, an independent uniformly random
phase per frame (each frame is one coherence time apart), and optionally Rayleigh
envelope fading with mean power 1 (sigma = sqrt(2/pi)) and Pete's 1.05 dB correction.
The operating point is EsNo_dB, the SNR within one FFT resolution bandwidth (per-frame
bin SNR gamma_f); gamma_f = C/N0 / R_bw.

StreamChannel: the streaming extension for stage 3 of the validation plan: C/N0 set at
the sample level, a Doppler ramp f0 + rate * t, a timing offset in samples, gaps
(stretches replaced by noise only), and optional per-frame phase/Rayleigh.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .params import EveParams

RAYLEIGH_SIGMA = np.sqrt(2.0 / np.pi)     # Kay, Detection Theory p. 30: mean envelope 1
RAYLEIGH_CORR_DB = 1.05                   # Pete's "adjustment to exponential distribution"


def gamma_f_from_cn0(cn0_db: float, params: EveParams) -> float:
    """Per-frame bin SNR (dB) from C/N0 (dB-Hz): coherent over one frame of 1/R_bw s."""
    return cn0_db - 10.0 * np.log10(params.r_bw)


def noise_sigma_per_sample(cn0_db: float, fs: float, amplitude: float) -> float:
    """Complex noise std per sample so that a tone of the given amplitude has C/N0."""
    cn0 = 10.0 ** (cn0_db / 10.0)
    n0 = amplitude ** 2 / cn0             # W/Hz for carrier power A^2
    return float(np.sqrt(n0 * fs))        # total noise variance in fs Hz


def pete_channel(frames: np.ndarray, esno_db: float, rayleigh: bool = True,
                 rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """frames: (n_frames, n_fft) unit-magnitude-spectrum tones as Pete's modulate.m
    produces (ifft of a unit spike: |FFT| = 1 at the tone bin). Returns the channel
    output with unit-variance complex AWGN per sample, as channel.m."""
    rng = rng or np.random.default_rng()
    nf, nfft = frames.shape
    w = (rng.standard_normal((nf, nfft)) + 1j * rng.standard_normal((nf, nfft))) / np.sqrt(2.0)
    phase = np.exp(1j * 2.0 * np.pi * rng.random(nf))
    if rayleigh:
        fade = np.sqrt((RAYLEIGH_SIGMA * rng.standard_normal(nf)) ** 2 +
                       (RAYLEIGH_SIGMA * rng.standard_normal(nf)) ** 2)
        coef = 10.0 ** ((esno_db - RAYLEIGH_CORR_DB) / 20.0) * np.sqrt(nfft) * phase * fade
    else:
        coef = 10.0 ** (esno_db / 20.0) * np.sqrt(nfft) * phase
    return coef[:, None] * frames + w


def pete_tone_frames(d: int, params: EveParams, n_frames: int) -> np.ndarray:
    """modulate.m equivalent: ifft of a unit spike at bin 2d, repeated n_frames times."""
    x = np.zeros(params.n_fft, dtype=np.complex128)
    x[params.tone_bin(d)] = 1.0
    y = np.fft.ifft(x)
    return np.tile(y, (n_frames, 1))


@dataclass
class StreamChannel:
    """Streaming channel at sample rate fs (default the modem rate) for a tone of amplitude
    `amplitude` (default the waveform's): complex AWGN sized so that tone has C/N0 = cn0_db
    (dB-Hz), a Doppler ramp f_offset_hz + f_rate_hz_s x t, a delay in samples, gaps in
    seconds where the signal is blanked, and Pete Wyckoff's per-frame random phase and
    Rayleigh fading (his 1.05 dB correction is applied here as extra noise). Stage 3 of the
    validation plan (design document 5.4); frames are counted from the start of each
    apply() block."""
    params: EveParams
    cn0_db: float
    fs: Optional[float] = None
    amplitude: Optional[float] = None
    f_offset_hz: float = 0.0            # Doppler residual at t = 0
    f_rate_hz_s: float = 0.0            # Doppler rate
    delay_samples: int = 0              # timing offset (positive = signal arrives late)
    gaps: Sequence[Tuple[float, float]] = ()   # (t_start, t_stop) seconds: signal absent
    frame_phase: bool = False           # random phase per frame (Pete)
    rayleigh: bool = False              # Rayleigh per frame (Pete)
    seed: Optional[int] = None

    def __post_init__(self):
        self.fs = self.params.modem_rate if self.fs is None else float(self.fs)
        self.amplitude = self.params.amplitude if self.amplitude is None else float(self.amplitude)
        self.rng = np.random.default_rng(self.seed)
        self.sigma = noise_sigma_per_sample(self.cn0_db, self.fs, self.amplitude)
        if self.rayleigh:
            self.sigma *= 10.0 ** (RAYLEIGH_CORR_DB / 20.0)

    def apply(self, x: np.ndarray, t0: float = 0.0) -> np.ndarray:
        """x: clean modem samples starting at time t0. Returns the channel output of the
        same length (delay shifts the signal within the block; the first delay samples
        are noise only)."""
        n = x.size
        t = t0 + np.arange(n) / self.fs
        s = x.astype(np.complex128)
        if self.delay_samples:
            s = np.concatenate([np.zeros(self.delay_samples, dtype=s.dtype), s])[:n]
        if self.f_offset_hz or self.f_rate_hz_s:
            phi = 2.0 * np.pi * (self.f_offset_hz * t + 0.5 * self.f_rate_hz_s * t * t)
            s = s * np.exp(1j * phi)
        for (ga, gb) in self.gaps:
            s[(t >= ga) & (t < gb)] = 0.0
        if self.frame_phase or self.rayleigh:
            nfft = self.params.n_fft
            nf = int(np.ceil(n / nfft))
            coef = np.exp(1j * 2.0 * np.pi * self.rng.random(nf)) if self.frame_phase else np.ones(nf)
            if self.rayleigh:
                coef = coef * np.sqrt((RAYLEIGH_SIGMA * self.rng.standard_normal(nf)) ** 2 +
                                      (RAYLEIGH_SIGMA * self.rng.standard_normal(nf)) ** 2)
            s = s * np.repeat(coef, nfft)[:n]
        w = (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n)) * (self.sigma / np.sqrt(2.0))
        return s + w

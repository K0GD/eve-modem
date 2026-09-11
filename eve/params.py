"""EveParams: the waveform parameter set (design document section 6.1).

The defaults are ORI's Python implementation (Variant A, the air-interface
specification). Variant B is the DSES 23 cm monostatic set (section 6.1.1). The
MATLAB set reproduces Pete Wyckoff's May 2026 simulation for curve comparison only.

Derived quantities follow the DSES definitions: the modem sample rate is exactly
N_fft x R_bw so a frame is exactly N_fft samples and tone d is exactly FFT bin 2d; a
symbol is an integer number of frames (473 for Variant A: 164.808 s, not ORI's
164.794 s).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from typing import Dict, Any

MSB_FIRST = "msb_first"
LSB_FIRST = "lsb_first"


@dataclass(frozen=True)
class EveParams:
    variant: str = "A"
    r_bw: float = 2.87             # Hz: FFT bin width = frame rate
    n_fft: int = 16384             # samples per frame at the modem rate
    m: int = 4096                  # alphabet size (12 bits per symbol)
    n_frames: int = 473            # frames per symbol (non-coherent combining depth)
    n_sym: int = 11                # symbols per message
    bch_n: int = 127
    bch_k: int = 106
    crc_bits: int = 16
    f_if: float = 25000.0          # Hz: comb origin above the dial frequency (ORI: 25 kHz)
    radio_decim: int = 32          # radio rate = radio_decim x modem rate
    amplitude: float = 0.8         # constant envelope, ORI files
    bit_order: str = MSB_FIRST     # bits-to-symbol packing
    tone_bin_step: int = 2         # tone d at FFT bin tone_bin_step * d (one guard bin)
    pilot_tone: int = 2048
    pilot_frames: int = 40

    # ---- derived ----------------------------------------------------------------
    @property
    def bits_per_symbol(self) -> int:
        b = self.m.bit_length() - 1
        assert 1 << b == self.m, "M must be a power of two"
        return b

    @property
    def spacing(self) -> float:
        """Tone spacing in Hz (2 x R_bw for one guard bin)."""
        return self.tone_bin_step * self.r_bw

    @property
    def modem_rate(self) -> float:
        return self.n_fft * self.r_bw

    @property
    def radio_rate(self) -> float:
        return self.radio_decim * self.modem_rate

    @property
    def t_frame(self) -> float:
        return 1.0 / self.r_bw

    @property
    def samples_per_symbol(self) -> int:
        return self.n_fft * self.n_frames

    @property
    def t_sym(self) -> float:
        return self.n_frames * self.t_frame

    @property
    def n_frames_msg(self) -> int:
        return self.n_frames * self.n_sym

    @property
    def t_msg(self) -> float:
        return self.n_frames_msg * self.t_frame

    @property
    def bandwidth(self) -> float:
        return self.m * self.spacing

    @property
    def coded_bits(self) -> int:
        return self.n_sym * self.bits_per_symbol   # 132

    @property
    def msg_bits(self) -> int:
        return self.bch_k - self.crc_bits          # 90

    def tone_bin(self, d: int) -> int:
        return self.tone_bin_step * d

    def tone_freq(self, d: int) -> float:
        """Baseband tone frequency above the comb origin (Hz)."""
        return d * self.spacing

    def tone_freq_if(self, d: int) -> float:
        """Tone frequency above the dial frequency (Hz): f_IF + d x spacing."""
        return self.f_if + self.tone_freq(d)

    # ---- named sets ---------------------------------------------------------------
    @classmethod
    def variant_a(cls) -> "EveParams":
        return cls()

    @classmethod
    def variant_b(cls) -> "EveParams":
        """DSES 23 cm monostatic: 1.5 Hz bins, 3.0 Hz spacing, 247 frames (164.67 s)."""
        return cls(variant="B", r_bw=1.5, n_frames=247)

    @classmethod
    def matlab(cls) -> "EveParams":
        """Pete Wyckoff's May 2026 MATLAB simulation set (curve reproduction only)."""
        return cls(variant="MATLAB", r_bw=2.67, n_fft=8192, n_frames=540,
                   f_if=0.0, bit_order=LSB_FIRST, amplitude=1.0)

    @classmethod
    def named(cls, name: str) -> "EveParams":
        key = name.strip().upper()
        if key == "A":
            return cls.variant_a()
        if key == "B":
            return cls.variant_b()
        if key == "MATLAB":
            return cls.matlab()
        raise ValueError(f"unknown waveform variant {name!r}")

    # ---- schedule file interchange (design document section 8.1) -------------------
    def to_schedule_dict(self) -> Dict[str, Any]:
        return {
            "variant": self.variant, "r_bw_hz": self.r_bw, "n_fft": self.n_fft,
            "m": self.m, "n_frames": self.n_frames, "n_sym": self.n_sym,
            "bch": [self.bch_n, self.bch_k], "crc": "CRC-16-CCITT",
            "bit_order": self.bit_order, "amplitude": self.amplitude,
            "phase": "continuous", "f_if_hz": self.f_if, "radio_decim": self.radio_decim,
        }

    @classmethod
    def from_schedule_dict(cls, d: Dict[str, Any]) -> "EveParams":
        base = cls.named(d.get("variant", "A"))
        kw = {}
        if "r_bw_hz" in d: kw["r_bw"] = float(d["r_bw_hz"])
        if "n_fft" in d: kw["n_fft"] = int(d["n_fft"])
        if "m" in d: kw["m"] = int(d["m"])
        if "n_frames" in d: kw["n_frames"] = int(d["n_frames"])
        if "n_sym" in d: kw["n_sym"] = int(d["n_sym"])
        if "bch" in d: kw["bch_n"], kw["bch_k"] = int(d["bch"][0]), int(d["bch"][1])
        if "bit_order" in d: kw["bit_order"] = str(d["bit_order"])
        if "amplitude" in d: kw["amplitude"] = float(d["amplitude"])
        if "f_if_hz" in d: kw["f_if"] = float(d["f_if_hz"])
        if "radio_decim" in d: kw["radio_decim"] = int(d["radio_decim"])
        return replace(base, **kw)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.update(modem_rate=self.modem_rate, radio_rate=self.radio_rate,
                 t_frame=self.t_frame, t_sym=self.t_sym, t_msg=self.t_msg,
                 spacing=self.spacing, bandwidth=self.bandwidth)
        return d

    def summary(self) -> str:
        return (f"Variant {self.variant}: R_bw {self.r_bw} Hz, spacing {self.spacing} Hz, "
                f"M {self.m}, {self.n_frames} frames/symbol ({self.t_sym:.3f} s), "
                f"{self.n_sym} symbols ({self.t_msg:.1f} s), modem rate {self.modem_rate:.2f} S/s, "
                f"radio rate {self.radio_rate:.2f} S/s, comb {self.f_if:.0f}..{self.f_if + self.bandwidth:.0f} Hz "
                f"above dial")

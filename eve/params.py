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
    """The waveform parameter set of design document 6.1, frozen: derive a variant with
    dataclasses.replace(params, ...). The defaults are Variant A (ORI's Python
    implementation, the air-interface specification); variant_b() and matlab() give the
    other named sets. Frequencies are in Hz, times in seconds, sizes in samples or frames."""
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
        """Bits per symbol, log2(M) (12 for M = 4096); M must be a power of two."""
        b = self.m.bit_length() - 1
        assert 1 << b == self.m, "M must be a power of two"
        return b

    @property
    def spacing(self) -> float:
        """Tone spacing in Hz (2 x R_bw for one guard bin)."""
        return self.tone_bin_step * self.r_bw

    @property
    def modem_rate(self) -> float:
        """Modem sample rate in S/s, defined as N_fft x R_bw so that a frame is exactly N_fft
        samples (design document 6.3): 47,022.08 for Variant A, 24,576 for Variant B."""
        return self.n_fft * self.r_bw

    @property
    def radio_rate(self) -> float:
        """Radio (USRP) sample rate in S/s: radio_decim x modem_rate (32 x for the B210)."""
        return self.radio_decim * self.modem_rate

    @property
    def t_frame(self) -> float:
        """Frame length in seconds, 1 / R_bw (0.34843 s for Variant A)."""
        return 1.0 / self.r_bw

    @property
    def samples_per_symbol(self) -> int:
        """Modem-rate samples per symbol, N_fft x N_frames (7,749,632 for Variant A)."""
        return self.n_fft * self.n_frames

    @property
    def t_sym(self) -> float:
        """Symbol length in seconds, N_frames x T_frame: a whole number of frames (164.808 s
        for Variant A, not ORI's 164.794 s; design document 6.1)."""
        return self.n_frames * self.t_frame

    @property
    def n_frames_msg(self) -> int:
        """Frames per message, N_frames x N_sym (5,203 for Variant A)."""
        return self.n_frames * self.n_sym

    @property
    def t_msg(self) -> float:
        """Message length in seconds, n_frames_msg x T_frame (1,812.9 s for Variant A)."""
        return self.n_frames_msg * self.t_frame

    @property
    def bandwidth(self) -> float:
        """Occupied bandwidth of the comb in Hz, M x spacing (23.5 kHz for Variant A)."""
        return self.m * self.spacing

    @property
    def coded_bits(self) -> int:
        """Bit positions carried by one message's symbols, N_sym x bits_per_symbol (132);
        the 127-bit codeword is zero-padded to this before packing."""
        return self.n_sym * self.bits_per_symbol   # 132

    @property
    def msg_bits(self) -> int:
        """Message text bits, BCH k minus the CRC (90)."""
        return self.bch_k - self.crc_bits          # 90

    def tone_bin(self, d: int) -> int:
        """FFT bin of tone d in an N_fft-point FFT of one frame at the modem rate:
        tone_bin_step x d, i.e. bin 2d with one guard bin between tones (design document 6.3)."""
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
        """Variant A: the values of ORI's Python implementation (the defaults), the air interface."""
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
        """The named set for "A", "B" or "MATLAB" (case and surrounding blanks ignored);
        raises ValueError for any other name."""
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
        """The "waveform" block of the schedule file (design document 8.1), keyed as the JSON
        schema names them (r_bw_hz, f_if_hz, bch as [n, k], ...). The pilot is not part of
        it (the schedule carries a separate pilot block) and neither is tone_bin_step."""
        return {
            "variant": self.variant, "r_bw_hz": self.r_bw, "n_fft": self.n_fft,
            "m": self.m, "n_frames": self.n_frames, "n_sym": self.n_sym,
            "bch": [self.bch_n, self.bch_k], "crc": "CRC-16-CCITT",
            "bit_order": self.bit_order, "amplitude": self.amplitude,
            "phase": "continuous", "f_if_hz": self.f_if, "radio_decim": self.radio_decim,
        }

    @classmethod
    def from_schedule_dict(cls, d: Dict[str, Any]) -> "EveParams":
        """Inverse of to_schedule_dict: start from the named variant (default "A") and
        override every waveform key present in the dictionary; keys it does not know
        (crc, phase) are ignored."""
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
        """Every field plus the derived quantities (modem_rate, radio_rate, t_frame, t_sym,
        t_msg, spacing, bandwidth) as a plain dictionary, for logs, sidecars and reports."""
        d = asdict(self)
        d.update(modem_rate=self.modem_rate, radio_rate=self.radio_rate,
                 t_frame=self.t_frame, t_sym=self.t_sym, t_msg=self.t_msg,
                 spacing=self.spacing, bandwidth=self.bandwidth)
        return d

    def summary(self) -> str:
        """One-line human-readable summary: bin width, spacing, alphabet, frames per symbol,
        symbol and message lengths, modem and radio rates, and the comb's place above the dial."""
        return (f"Variant {self.variant}: R_bw {self.r_bw} Hz, spacing {self.spacing} Hz, "
                f"M {self.m}, {self.n_frames} frames/symbol ({self.t_sym:.3f} s), "
                f"{self.n_sym} symbols ({self.t_msg:.1f} s), modem rate {self.modem_rate:.2f} S/s, "
                f"radio rate {self.radio_rate:.2f} S/s, comb {self.f_if:.0f}..{self.f_if + self.bandwidth:.0f} Hz "
                f"above dial")

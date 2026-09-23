"""Message text <-> bits, CRC-16-CCITT, payload assembly (design document 5.1, 6.1).

Conventions follow ORI's eve_tx_sigmf.py exactly: 8-bit ASCII, MSB first, zero-padded
or truncated to 90 bits; CRC-16-CCITT (poly 0x1021, init 0xFFFF, no reflection)
computed over the 90 bits packed MSB-first into bytes (the last byte carries 6 zero pad
bits); payload = 90 message bits + 16 CRC bits = 106 = the BCH message length.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

MSG_BITS = 90
CRC_BITS = 16
PAYLOAD_BITS = MSG_BITS + CRC_BITS   # 106
CRC_POLY = 0x1021
CRC_INIT = 0xFFFF


def crc16_ccitt(bits) -> int:
    """CRC-16-CCITT over bits packed MSB-first into bytes (zero-padded to a byte)."""
    b = np.packbits(np.asarray(bits, dtype=np.uint8))
    crc = CRC_INIT
    for byte in b:
        crc ^= int(byte) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def int_to_bits(value: int, width: int) -> np.ndarray:
    """Unsigned integer -> `width` bits as a uint8 array, most significant bit first."""
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


def bits_to_int(bits) -> int:
    """Bit sequence, most significant bit first -> unsigned integer; inverse of int_to_bits."""
    v = 0
    for b in np.asarray(bits, dtype=np.uint8):
        v = (v << 1) | int(b)
    return v


def text_to_bits(text: str, n_bits: int = MSG_BITS) -> np.ndarray:
    """ASCII text -> n_bits, MSB first, zero-padded; extra characters are truncated."""
    raw = np.unpackbits(np.frombuffer(text.encode("ascii", "replace"), dtype=np.uint8))
    out = np.zeros(n_bits, dtype=np.uint8)
    n = min(raw.size, n_bits)
    out[:n] = raw[:n]
    return out


def bits_to_text(bits) -> str:
    """Inverse of text_to_bits: whole bytes only, trailing NULs stripped."""
    b = np.asarray(bits, dtype=np.uint8)
    nbytes = b.size // 8
    if nbytes == 0:
        return ""
    raw = np.packbits(b[: nbytes * 8]).tobytes()
    return raw.rstrip(b"\x00").decode("ascii", "replace").replace(chr(0xFFFD), "?")


def build_payload(text: str) -> np.ndarray:
    """90 message bits + 16 CRC bits = 106-bit BCH message."""
    msg = text_to_bits(text)
    crc = int_to_bits(crc16_ccitt(msg), CRC_BITS)
    return np.concatenate([msg, crc])


@dataclass
class PayloadCheck:
    """What verify_payload() returns: ok is True when the received CRC equals the CRC
    computed over the 90 message bits; text is the decoded message; both CRC values are
    kept for the log."""
    ok: bool
    text: str
    crc_received: int
    crc_computed: int


def verify_payload(payload) -> PayloadCheck:
    """Split a 106-bit payload (the message bits bch.decode() returns) into 90 message bits
    and 16 CRC bits, recompute the CRC-16-CCITT over the message bits and compare (design
    document 6.3, last step). Raises ValueError for any other length."""
    p = np.asarray(payload, dtype=np.uint8).ravel()
    if p.size != PAYLOAD_BITS:
        raise ValueError(f"payload must be {PAYLOAD_BITS} bits, got {p.size}")
    msg, crc_rx = p[:MSG_BITS], bits_to_int(p[MSG_BITS:])
    crc_calc = crc16_ccitt(msg)
    return PayloadCheck(crc_rx == crc_calc, bits_to_text(msg), crc_rx, crc_calc)


def bits_str(bits, group: Optional[int] = 8) -> str:
    """Bits as a string of 0/1 characters in space-separated groups of `group` bits (8 by
    default; None or 0 for one unbroken string), the form used in the design document's
    Appendix C test vector."""
    s = "".join(str(int(b)) for b in np.asarray(bits).ravel())
    if not group:
        return s
    return " ".join(s[i:i + group] for i in range(0, len(s), group))

"""BCH(127,106), t = 3: encoder and decoder, pure Python/NumPy (design document 5.1).

Field: GF(2^7) with primitive polynomial x^7 + x^3 + 1 (MATLAB's and galois' default).
Generator: g(x) = lcm of the minimal polynomials of alpha, alpha^3, alpha^5 =
x^21 + x^18 + x^17 + x^15 + x^14 + x^12 + x^11 + x^8 + x^7 + x^6 + x^5 + x + 1,
octal 11554743 (Lin & Costello table; identical in MATLAB bchenc and galois).

Bit conventions (same as galois and the ORI generator): a bit vector is a polynomial
with index 0 the HIGHEST degree; the codeword is systematic, message bits first, then
n - k parity bits. Position j in the received word corresponds to locator alpha^(n-1-j).

Decoder: syndromes S1..S6, Berlekamp-Massey (binary form), Chien search, verification
by recomputing the syndromes. Returns the corrected message, the number of bits
corrected, and whether the decode is trustworthy (a residual syndrome means more than
t errors and the output is not to be believed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

N = 127
K = 106
T = 3
M_FIELD = 7
PRIM_POLY = 0b10001001          # x^7 + x^3 + 1
GEN_POLY_OCTAL = 0o11554743      # Lin & Costello

# ---- GF(2^7) tables --------------------------------------------------------------
_EXP = np.zeros(2 * N, dtype=np.int64)
_LOG = np.full(N + 1, -1, dtype=np.int64)
_x = 1
for _i in range(N):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & (1 << M_FIELD):
        _x ^= PRIM_POLY
_EXP[N:] = _EXP[:N]
assert _x == 1, "x^7+x^3+1 is not primitive?"


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return int(_EXP[_LOG[a] + _LOG[b]])


def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("GF inverse of 0")
    return int(_EXP[(N - _LOG[a]) % N])


def gf_pow_alpha(e: int) -> int:
    return int(_EXP[e % N])


# ---- generator polynomial -------------------------------------------------------
def _poly_mul_gf2(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        b >>= 1
    return r


def _minimal_poly(alpha_exp: int) -> int:
    """Minimal polynomial of alpha^alpha_exp over GF(2), as an int (bit i = coeff x^i)."""
    conj = set()
    e = alpha_exp % N
    while e not in conj:
        conj.add(e)
        e = (2 * e) % N
    # product of (x + alpha^e) over the conjugacy class, coefficients in GF(2^7)
    poly = [1]                                    # coefficient list, index = degree
    for e in sorted(conj):
        root = gf_pow_alpha(e)
        new = [0] * (len(poly) + 1)
        for i, c in enumerate(poly):
            new[i + 1] ^= c                       # x * c
            new[i] ^= gf_mul(c, root)             # root * c
        poly = new
    out = 0
    for i, c in enumerate(poly):
        assert c in (0, 1), "minimal polynomial not over GF(2)"
        out |= c << i
    return out


def generator_poly() -> int:
    g = 1
    for e in (1, 3, 5):
        g = _poly_mul_gf2(g, _minimal_poly(e))
    return g


GEN_POLY = generator_poly()
assert GEN_POLY == GEN_POLY_OCTAL, f"generator {oct(GEN_POLY)} != {oct(GEN_POLY_OCTAL)}"
assert GEN_POLY.bit_length() - 1 == N - K


# ---- encoder ----------------------------------------------------------------------
def encode(message_bits) -> np.ndarray:
    """Systematic BCH(127,106): returns 127 bits = message (106) then parity (21)."""
    msg = np.asarray(message_bits, dtype=np.uint8).ravel()
    if msg.size != K:
        raise ValueError(f"message must be {K} bits, got {msg.size}")
    # m(x) * x^(n-k) mod g(x); polynomial as int with bit i = coefficient of x^i,
    # message index 0 = highest degree.
    mx = 0
    for b in msg:
        mx = (mx << 1) | int(b)
    mx <<= (N - K)
    rem = mx
    gdeg = N - K
    for deg in range(N - 1, gdeg - 1, -1):
        if (rem >> deg) & 1:
            rem ^= GEN_POLY << (deg - gdeg)
    parity = [(rem >> (gdeg - 1 - i)) & 1 for i in range(gdeg)]
    return np.concatenate([msg, np.array(parity, dtype=np.uint8)])


# ---- decoder ----------------------------------------------------------------------
@dataclass
class DecodeResult:
    message: np.ndarray        # 106 bits (the first K of the corrected word)
    codeword: np.ndarray       # corrected 127-bit word
    n_corrected: int           # bits flipped
    ok: bool                   # syndromes zero after correction (trustworthy)
    error_positions: List[int]


def syndromes(word: np.ndarray) -> List[int]:
    """S_i = r(alpha^i) for i = 1..2t, with r index 0 the highest degree."""
    idx = np.nonzero(word)[0]
    degs = (N - 1 - idx).tolist()
    out = []
    for i in range(1, 2 * T + 1):
        s = 0
        for d in degs:
            s ^= gf_pow_alpha(i * d)
        out.append(s)
    return out


def _berlekamp_massey(S: List[int]) -> List[int]:
    """Error-locator polynomial sigma(x) (coeffs index = degree) from syndromes S1..S2t."""
    sigma = [1]
    B = [1]
    L = 0
    m = 1
    b = 1
    for n in range(len(S)):
        # discrepancy
        d = S[n]
        for i in range(1, L + 1):
            if i < len(sigma):
                d ^= gf_mul(sigma[i], S[n - i])
        if d == 0:
            m += 1
            continue
        T_ = sigma[:]
        coef = gf_mul(d, gf_inv(b))
        # sigma = sigma - coef * x^m * B
        need = len(B) + m
        if len(sigma) < need:
            sigma += [0] * (need - len(sigma))
        for i, bi in enumerate(B):
            sigma[i + m] ^= gf_mul(coef, bi)
        if 2 * L <= n:
            L = n + 1 - L
            B = T_
            b = d
            m = 1
        else:
            m += 1
    # trim
    while len(sigma) > 1 and sigma[-1] == 0:
        sigma.pop()
    return sigma


def _chien(sigma: List[int]) -> List[int]:
    """Positions j (word index) whose locator alpha^(n-1-j) is a root reciprocal of sigma."""
    positions = []
    for j in range(N):
        loc_exp = N - 1 - j                     # error at index j has locator alpha^(N-1-j)
        # sigma(X) with X = alpha^(-loc_exp): sum sigma_i * alpha^(-i*loc_exp)
        v = 0
        for i, c in enumerate(sigma):
            if c:
                v ^= gf_mul(c, gf_pow_alpha((-i * loc_exp) % N))
        if v == 0:
            positions.append(j)
    return positions


def decode(word_bits) -> DecodeResult:
    r = np.asarray(word_bits, dtype=np.uint8).ravel().copy()
    if r.size != N:
        raise ValueError(f"received word must be {N} bits, got {r.size}")
    S = syndromes(r)
    if not any(S):
        return DecodeResult(r[:K].copy(), r, 0, True, [])
    sigma = _berlekamp_massey(S)
    deg = len(sigma) - 1
    positions = _chien(sigma) if 1 <= deg <= T else []
    ok = False
    if len(positions) == deg and deg >= 1:
        for j in positions:
            r[j] ^= 1
        ok = not any(syndromes(r))
    return DecodeResult(r[:K].copy(), r, len(positions) if ok else 0, ok, positions if ok else [])


def min_distance_check(n_trials: int = 200, rng=None) -> Tuple[int, int]:
    """Cheap self-test: random messages, random <= t errors, count failures."""
    rng = rng or np.random.default_rng(0)
    fails = 0
    for _ in range(n_trials):
        msg = rng.integers(0, 2, K, dtype=np.uint8)
        cw = encode(msg)
        ne = int(rng.integers(0, T + 1))
        pos = rng.choice(N, ne, replace=False)
        rx = cw.copy()
        rx[pos] ^= 1
        res = decode(rx)
        if not (res.ok and np.array_equal(res.message, msg)):
            fails += 1
    return n_trials, fails

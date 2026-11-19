"""gorbcode: a small erasure code for sending one Gorb reply to many listeners.

Systematic Random Linear Network Coding (RLNC) over GF(256):

* A reply blob is cut into K source chunks of L bytes (the last one is padded).
* Coded symbol ``seq < K`` is source chunk ``seq`` itself (the "systematic" part:
  identical to a plain Gorb DATA packet, so old receivers can use it).
* Coded symbol ``seq >= K`` is a "repair" packet: a GF(256) linear mix of ALL
  source chunks. Its K coefficients are NOT sent: both sides regenerate them
  from ``(seq, K)`` with a tiny xorshift32 generator (see ``coefficients``).
  So a repair packet costs only a 4-byte code header on top of a DATA packet.
* Any K (very rarely K+1 or K+2) distinct symbols, in any order, rebuild the
  blob. Different listeners can lose different packets: one repair packet
  fixes a different hole for each of them.

Wire format of the code header (first 4 bytes of a repair packet payload):

    byte 0   scheme (high nibble, 1 = RLNC-GF256-xorshift) | reserved (low nibble)
    byte 1   length of the last source chunk (1..L)
    byte 2-3 content tag: CRC-16/CCITT of the whole blob (detects a changed page)

The Gorb packet header already carries ``seq`` (u16 symbol id) and ``total``
(K), so nothing else is needed. Chunk size L is the size of the systematic
chunks (all but the last), i.e. the repair payload length.

Also here, for comparison only: an LT (Luby Transform) fountain code with a
robust-soliton degree distribution and a peeling decoder.

Run ``python gorbcode.py`` for the self-test.
"""

from __future__ import annotations

import math
import random
import struct
import zlib

import numpy as np

# ---------------------------------------------------------------- GF(256)
# Field polynomial x^8+x^4+x^3+x^2+1 (0x11D), generator 2 (same as most RS codecs).
_EXP = np.zeros(512, dtype=np.uint8)
_LOG = np.zeros(256, dtype=np.int32)
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
_EXP[255:510] = _EXP[0:255]
del _x, _i

# Full 256x256 multiplication table (64 KB). On an ESP32 one would keep only
# EXP/LOG (768 bytes) or a 64 KB table in flash; see the report.
MUL = np.zeros((256, 256), dtype=np.uint8)
_a = np.arange(1, 256)
MUL[1:, 1:] = _EXP[(_LOG[_a][:, None] + _LOG[_a][None, :]) % 255]
INV = np.zeros(256, dtype=np.uint8)
INV[1:] = _EXP[(255 - _LOG[_a]) % 255]
del _a

SCHEME_RLNC = 1
HEADER_LEN = 4


def gf_mul(a: int, b: int) -> int:
    return int(MUL[a, b])


# ---------------------------------------------------------------- coefficients
def _xorshift32(x: int) -> int:
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17
    x ^= (x << 5) & 0xFFFFFFFF
    return x & 0xFFFFFFFF


def seed_for(seq: int, k: int) -> int:
    s = ((seq * 0x9E3779B1) ^ (k * 0x85EBCA77) ^ 0x6A09E667) & 0xFFFFFFFF
    return s or 1


_COEF_CACHE: dict[tuple[int, int], np.ndarray] = {}


def coefficients(seq: int, k: int) -> np.ndarray:
    """The K GF(256) coefficients (all non-zero) of repair symbol ``seq``.

    Reference algorithm (easy to port to C):
        x = seed_for(seq, K)
        for j in 0..K-1: x = xorshift32(x); c[j] = 1 + (x >> 8) % 255
    """
    if seq < k:  # systematic symbol = unit vector
        v = np.zeros(k, dtype=np.uint8)
        v[seq] = 1
        return v
    key = (seq, k)
    v = _COEF_CACHE.get(key)
    if v is None:
        x = seed_for(seq, k)
        out = np.empty(k, dtype=np.uint8)
        for j in range(k):
            x = _xorshift32(x)
            out[j] = 1 + (x >> 8) % 255
        v = out
        if len(_COEF_CACHE) > 200_000:
            _COEF_CACHE.clear()
        _COEF_CACHE[key] = v
    return v


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


# ---------------------------------------------------------------- encoder
class Encoder:
    """Cut a blob into K chunks of L bytes and make coded symbols."""

    def __init__(self, blob: bytes, chunk: int = 180):
        if not blob:
            blob = b"\x00"
        self.blob = blob
        self.L = chunk
        self.K = math.ceil(len(blob) / chunk)
        self.last_len = len(blob) - (self.K - 1) * chunk
        pad = blob + b"\x00" * (self.K * chunk - len(blob))
        self.src = np.frombuffer(pad, dtype=np.uint8).reshape(self.K, chunk)
        self.tag = crc16(blob)

    def header(self) -> bytes:
        return bytes([SCHEME_RLNC << 4, self.last_len]) + struct.pack(">H", self.tag)

    def symbol(self, seq: int) -> bytes:
        """Payload for symbol ``seq``: plain chunk if seq < K, else header + repair."""
        if seq < self.K:
            n = self.last_len if seq == self.K - 1 else self.L
            return self.src[seq, :n].tobytes()
        return self.header() + self.repair(seq).tobytes()

    def repair(self, seq: int) -> np.ndarray:
        c = coefficients(seq, self.K)
        # XOR-sum of c[j] * src[j]: K*L table look-ups.
        return np.bitwise_xor.reduce(MUL[c[:, None], self.src], axis=0)


# ---------------------------------------------------------------- decoder
def _gauss_solve(A: np.ndarray, B: np.ndarray, ops: list | None = None):
    """Solve A X = B over GF(256) (A: r x m with r >= m). Returns X (m x L) or None
    if rank(A) < m. Gauss-Jordan with row operations on [A | B]."""
    A = A.copy()
    B = B.copy()
    r, m = A.shape
    row = 0
    pivrow = []
    for col in range(m):
        nz = np.nonzero(A[row:, col])[0]
        if len(nz) == 0:
            return None
        p = row + nz[0]
        if p != row:
            A[[row, p]] = A[[p, row]]
            B[[row, p]] = B[[p, row]]
        inv = INV[A[row, col]]
        A[row] = MUL[inv][A[row]]
        B[row] = MUL[inv][B[row]]
        others = np.nonzero(A[:, col])[0]
        others = others[others != row]
        if len(others):
            f = A[others, col]
            A[others] ^= MUL[f[:, None], A[row][None, :]]
            B[others] ^= MUL[f[:, None], B[row][None, :]]
            if ops is not None:
                ops[0] += len(others) * (A.shape[1] + B.shape[1])
        pivrow.append(row)
        row += 1
    return B[:m]


def rank_gf256(A: np.ndarray) -> int:
    A = A.copy()
    r, m = A.shape
    row = 0
    for col in range(m):
        if row >= r:
            break
        nz = np.nonzero(A[row:, col])[0]
        if len(nz) == 0:
            continue
        p = row + nz[0]
        if p != row:
            A[[row, p]] = A[[p, row]]
        inv = INV[A[row, col]]
        A[row] = MUL[inv][A[row]]
        below = row + 1 + np.nonzero(A[row + 1:, col])[0]
        if len(below):
            f = A[below, col]
            A[below] ^= MUL[f[:, None], A[row][None, :]]
        row += 1
    return row


class Decoder:
    """Collect symbols in any order; ``done`` once the blob can be rebuilt.

    Memory: received source chunks (K x L) + received repair payloads (R x L).
    Coefficients are regenerated from the seed, so they are never stored.
    Work is done lazily, once at least K distinct symbols are in: subtract the
    known source chunks from each repair payload, then solve the small
    m x m system for the m missing chunks.
    """

    def __init__(self, k: int, chunk: int = 180):
        self.K = k
        self.L = chunk
        self.src: dict[int, bytes] = {}
        self.rep: dict[int, np.ndarray] = {}
        self.last_len: int | None = None
        self.tag: int | None = None
        self.blob: bytes | None = None
        self.ops = 0  # GF(256) multiply-adds (one byte each) spent decoding
        self._failed_at = -1

    @property
    def received(self) -> int:
        return len(self.src) + len(self.rep)

    @property
    def done(self) -> bool:
        return self.blob is not None

    def add(self, seq: int, payload: bytes) -> bool:
        """Feed one symbol. Returns True once the blob is complete."""
        if self.done:
            return True
        if seq < self.K:
            if seq not in self.src:
                self.src[seq] = bytes(payload)
                if seq == self.K - 1:
                    self.last_len = len(payload)
        else:
            if seq in self.rep:
                return False
            if len(payload) < HEADER_LEN or payload[0] >> 4 != SCHEME_RLNC:
                return False
            self.last_len = payload[1]
            self.tag = struct.unpack(">H", payload[2:4])[0]
            body = np.frombuffer(payload[HEADER_LEN:], dtype=np.uint8)
            if len(body) != self.L:
                return False
            self.rep[seq] = body
        return self._try()

    def needed(self) -> int:
        """How many more symbols we need at least (what a NEED packet reports)."""
        return 0 if self.done else max(1, self.K - self.received)

    def _try(self) -> bool:
        if self.received < self.K or self.received == self._failed_at:
            return self.done
        missing = [j for j in range(self.K) if j not in self.src]
        if missing:
            seqs = sorted(self.rep)
            C = np.stack([coefficients(s, self.K) for s in seqs])  # r x K
            P = np.stack([self.rep[s] for s in seqs]).copy()  # r x L
            known = [j for j in range(self.K) if j in self.src]
            if known:
                S = np.stack([self._padded(j) for j in known])  # k x L
                for i in range(len(seqs)):  # subtract what we already know
                    P[i] ^= np.bitwise_xor.reduce(MUL[C[i, known][:, None], S], axis=0)
                self.ops += len(seqs) * len(known) * self.L
            ops = [0]
            X = _gauss_solve(C[:, missing], P, ops)
            self.ops += ops[0]
            if X is None:
                self._failed_at = self.received
                return False
            for idx, j in enumerate(missing):
                self.src[j] = X[idx].tobytes()
        if self.last_len is None:  # only possible if we never saw the last chunk or a repair
            self.last_len = self.L
        parts = [self._padded(j).tobytes() for j in range(self.K)]
        parts[-1] = parts[-1][: self.last_len]
        blob = b"".join(parts)
        if self.tag is not None and crc16(blob) != self.tag:
            self._failed_at = self.received
            return False
        self.blob = blob
        return True

    def _padded(self, j: int) -> np.ndarray:
        b = self.src[j]
        a = np.zeros(self.L, dtype=np.uint8)
        a[: len(b)] = np.frombuffer(b, dtype=np.uint8)
        return a


# ---------------------------------------------------------------- LT code (comparison)
def robust_soliton(k: int, c: float = 0.1, delta: float = 0.5) -> np.ndarray:
    rho = np.zeros(k + 1)
    rho[1] = 1 / k
    d = np.arange(2, k + 1)
    rho[2:] = 1 / (d * (d - 1))
    R = c * math.log(k / delta) * math.sqrt(k)
    tau = np.zeros(k + 1)
    spike = max(1, min(k, int(round(k / R))))
    for i in range(1, spike):
        tau[i] = R / (i * k)
    tau[spike] = R * math.log(R / delta) / k if R > delta else 0
    mu = rho + tau
    return mu / mu.sum()


class LTCode:
    """Non-systematic LT fountain code; symbol ``seq`` picks its degree and
    neighbours from a PRNG seeded by seq. Peeling (belief propagation) decoder."""

    def __init__(self, k: int, c: float = 0.1, delta: float = 0.5):
        self.k = k
        self.cdf = np.cumsum(robust_soliton(k, c, delta))

    def neighbours(self, seq: int) -> list[int]:
        rng = random.Random(seq * 7919 + self.k)
        d = int(np.searchsorted(self.cdf, rng.random()))
        d = max(1, min(self.k, d))
        return rng.sample(range(self.k), d)

    def symbols_to_decode(self, rng: random.Random, loss: float, start: int | None = None) -> int:
        """Feed a lossy stream; return how many symbols were received when the
        peeling decoder finished (structure only, no payload)."""
        known = [False] * self.k
        nknown = 0
        pending: list[set] = []
        by_src: dict[int, list[int]] = {}
        got = 0
        seq = rng.randrange(1 << 30) if start is None else start
        while nknown < self.k:
            seq += 1
            if rng.random() < loss:
                continue
            got += 1
            s = set(j for j in self.neighbours(seq) if not known[j])
            if not s:
                continue
            idx = len(pending)
            pending.append(s)
            for j in s:
                by_src.setdefault(j, []).append(idx)
            stack = [idx] if len(s) == 1 else []
            while stack:
                i = stack.pop()
                if len(pending[i]) != 1:
                    continue
                j = next(iter(pending[i]))
                if known[j]:
                    pending[i].clear()
                    continue
                known[j] = True
                nknown += 1
                for t in by_src.get(j, []):
                    pending[t].discard(j)
                    if len(pending[t]) == 1:
                        stack.append(t)
        return got


# ---------------------------------------------------------------- self-test
def _selftest() -> None:
    rng = random.Random(1)
    # field sanity
    for a in range(1, 256):
        assert gf_mul(a, int(INV[a])) == 1
    for _ in range(200):
        a, b, c = rng.randrange(256), rng.randrange(256), rng.randrange(256)
        assert gf_mul(a, b ^ c) == gf_mul(a, b) ^ gf_mul(a, c)
    # round trips with random loss and random order
    for size in [1, 179, 180, 181, 6144, 40960, 72000]:
        blob = zlib.compress(bytes(rng.randrange(256) for _ in range(size)), 0)[:size] or b"x"
        enc = Encoder(blob)
        dec = Decoder(enc.K, enc.L)
        loss = rng.choice([0.0, 0.1, 0.3, 0.5])
        seqs = [s for s in range(enc.K) if rng.random() >= loss]
        seqs += list(range(enc.K, enc.K + 3 * enc.K + 10))
        order = seqs[: len(seqs)]
        rng.shuffle(order)
        for s in order:
            if dec.add(s, enc.symbol(s)):
                break
        assert dec.done and dec.blob == blob, size
    # pure repair (a listener that heard none of the originals)
    blob = bytes(rng.randrange(256) for _ in range(5000))
    enc = Encoder(blob)
    dec = Decoder(enc.K)
    s = enc.K + 1000
    while not dec.done:
        dec.add(s, enc.symbol(s))
        s += 1
    assert dec.blob == blob
    # a wrong page (different tag) is rejected
    enc2 = Encoder(bytes(len(blob)))
    dec = Decoder(enc.K)
    for s in range(1, enc.K):
        dec.add(s, enc.symbol(s))
    dec.add(enc.K + 5, enc2.symbol(enc.K + 5))
    assert not dec.done
    print("gorbcode self-test OK")


if __name__ == "__main__":
    _selftest()

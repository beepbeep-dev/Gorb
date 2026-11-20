"""Gorb Modem 2: an OFDM modem with forward error correction for audio radios.

This is the candidate replacement for the AFSK1200 modem in gorb/modem.py.
It is self-contained (numpy only) so it can later be wired into Gorb:

    audio = modulate(payload_bytes, mode="16qam-3/4")   # float32, 48 kHz, |x| <= 0.9
    frames = demodulate(audio)                           # list[bytes]

How it works, in one paragraph: the audio band 422-2859 Hz is split into 53
narrow sub-carriers (46.875 Hz apart, FFT size 1024 at 48 kHz). Every 24 ms
(one OFDM symbol, including a 2.67 ms cyclic prefix) each carrier sends one
QPSK/16QAM/64QAM point, so many slow streams run side by side instead of one
fast one. 5 carriers are known "pilots" that let the receiver follow timing
drift between sound cards. The bits are protected by a rate 1/2 convolutional
code (K=7, the NASA/Voyager code, decoded with a soft-decision Viterbi
decoder), optionally punctured to rate 2/3 or 3/4, and shuffled by an
interleaver so a short burst of noise is spread out into scattered,
correctable errors. A CRC32 still checks every frame.

On-air frame:

    leader   repeated known "sync" symbol (default 250 ms; keys VOX, AGC,
             and gives the receiver its timing and per-carrier channel estimate)
    marker   one sync symbol with inverted sign: "the frame starts here"
    header   QPSK rate 1/2, sent twice, in OFDM symbols 0 and 3 (payload
             symbols fill 1, 2 and 4 onwards):
             mode (1 byte) | length (2 bytes) | CRC16 (2 bytes)
    payload  N OFDM symbols in the chosen mode:
             scrambled(payload + CRC32) -> conv code -> puncture -> interleave
    tail     10 ms fade-out
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

FS = 48000
NFFT = 1024
CP = 128
SYM_LEN = NFFT + CP
K_FIRST, K_LAST = 9, 61  # active FFT bins: 421.9 .. 2859.4 Hz
BINS = np.arange(K_FIRST, K_LAST + 1)
N_ACT = len(BINS)  # 53
PILOT_IDX = np.arange(0, N_ACT, 13)  # 5 pilots: bins 9, 22, 35, 48, 61
DATA_IDX = np.setdiff1d(np.arange(N_ACT), PILOT_IDX)  # 48 data carriers
N_DATA = len(DATA_IDX)
PILOT_SPACING = 13
EARLY = 24  # receiver starts each FFT window this many samples into the CP
# TX clips the OFDM waveform at 3x RMS (9.5 dB crest factor). Clipping harder (2x, 1.6x) was
# tried and did not help on the FM path: the radio's pre-emphasis regrows the peaks.
TX_CREST = 3.0
TX_PEAK = 0.9
N_EST = 5  # leader copies averaged for the channel estimate (5 x 21 ms)
SG_WIN, SG_ORDER = 7, 2  # channel estimate smoothing across carriers (local quadratic fit)
MAX_PAYLOAD = 4096
HEADER_SYMS = 2
HEADER_POS = (0, 3)  # the two header copies sit in OFDM symbols 0 and 3, so one dropout can't take both

# mode id -> (name, bits per QAM axis, code rate)
MODES = {
    1: ("qpsk-1/2", 1, (1, 2)),
    2: ("qpsk-3/4", 1, (3, 4)),
    3: ("16qam-1/2", 2, (1, 2)),
    4: ("16qam-3/4", 2, (3, 4)),
    5: ("64qam-2/3", 3, (2, 3)),
    6: ("64qam-3/4", 3, (3, 4)),
}
MODE_BY_NAME = {v[0]: k for k, v in MODES.items()}

# ---------------------------------------------------------------------------
# Convolutional code, K=7, generators 133/171 (octal), with puncturing
# ---------------------------------------------------------------------------
_G = (0o133, 0o171)
_K = 7
_NS = 64
_PUNCT = {(1, 2): np.array([1, 1]), (2, 3): np.array([1, 1, 1, 0]), (3, 4): np.array([1, 1, 1, 0, 0, 1])}


def _parity(x: np.ndarray) -> np.ndarray:
    x = x.copy()
    p = np.zeros_like(x)
    while np.any(x):
        p ^= x & 1
        x >>= 1
    return p


# Register r = (prev_state << 1) | input: bit 0 = newest input, bit 6 = oldest.
_REG = np.arange(128)
_OUT = np.stack([_parity(_REG & g) for g in _G], axis=1)  # (128, 2)
# Trellis: next state ns has predecessors p = (ns >> 1) | (x << 5), x in {0,1}
_NSARR = np.arange(_NS)
_PREV = np.stack([(_NSARR >> 1) | (x << 5) for x in (0, 1)], axis=1)  # (64, 2)
_REG_NS = (_PREV << 1) | (_NSARR[:, None] & 1)  # register value for each branch
_SGN = (1.0 - 2.0 * _OUT[_REG_NS]) * 0.5  # (64, 2 preds, 2 outputs): +1/2 for bit 0


def conv_encode(bits: np.ndarray, rate=(1, 2)) -> np.ndarray:
    """Encode bits (6 zero tail bits are appended), then puncture."""
    u = np.concatenate([np.asarray(bits, dtype=np.int64), np.zeros(_K - 1, dtype=np.int64)])
    reg = np.zeros(len(u), dtype=np.int64)
    for i in range(_K):
        reg[i:] |= u[: len(u) - i] << i
    coded = _OUT[reg].reshape(-1)  # c0_0 c1_0 c0_1 c1_1 ...
    mask = np.resize(_PUNCT[tuple(rate)], len(coded)).astype(bool)
    return coded[mask].astype(np.uint8)


def coded_len(n_info: int, rate=(1, 2)) -> int:
    n = 2 * (n_info + _K - 1)
    return int(np.resize(_PUNCT[tuple(rate)], n).sum())


def depuncture(llr: np.ndarray, n_info: int, rate=(1, 2)) -> np.ndarray:
    """llr: (B, coded_len) -> (B, n_info+6, 2) with zeros where punctured."""
    llr = np.atleast_2d(llr)
    n = 2 * (n_info + _K - 1)
    mask = np.resize(_PUNCT[tuple(rate)], n).astype(bool)
    full = np.zeros((llr.shape[0], n))
    full[:, mask] = llr
    return full.reshape(llr.shape[0], -1, 2)


def viterbi(llr2: np.ndarray, n_info: int) -> np.ndarray:
    """Batched soft-decision Viterbi. llr2: (B, T, 2), LLR>0 means bit 0.

    Returns hard decisions (B, n_info). The trellis is terminated in state 0.
    """
    B, T, _ = llr2.shape
    pm = np.full((B, _NS), -1e9)
    pm[:, 0] = 0.0
    dec = np.empty((T, B, _NS), dtype=bool)
    s0 = _SGN[..., 0][None]  # (1, 64, 2)
    s1 = _SGN[..., 1][None]
    for t in range(T):
        l0 = llr2[:, t, 0][:, None, None]
        l1 = llr2[:, t, 1][:, None, None]
        cand = pm[:, _PREV] + s0 * l0 + s1 * l1  # (B, 64, 2)
        ch = cand[:, :, 1] > cand[:, :, 0]
        pm = np.where(ch, cand[:, :, 1], cand[:, :, 0])
        dec[t] = ch
        if t % 64 == 63:
            pm -= pm.max(axis=1, keepdims=True)
    state = np.zeros(B, dtype=np.int64)
    bits = np.empty((B, T), dtype=np.uint8)
    ar = np.arange(B)
    for t in range(T - 1, -1, -1):
        bits[:, t] = state & 1
        x = dec[t, ar, state].astype(np.int64)
        state = (state >> 1) | (x << 5)
    return bits[:, :n_info]


# ---------------------------------------------------------------------------
# Small helpers: CRC16, scrambler, interleaver, QAM
# ---------------------------------------------------------------------------
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


_SCRAMBLE = np.random.default_rng(0x6077B).integers(0, 2, 8 * (MAX_PAYLOAD + 8), dtype=np.uint8)
_PERM_CACHE: dict[int, np.ndarray] = {}


def interleaver(n: int) -> np.ndarray:
    if n not in _PERM_CACHE:
        _PERM_CACHE[n] = np.random.default_rng(1000 + n).permutation(n)
    return _PERM_CACHE[n]


def _gray_levels(m: int):
    """Per-axis Gray-coded PAM: returns (levels in index order, bits[level_index, m])."""
    M = 1 << m
    idx = np.arange(M)
    gray = idx ^ (idx >> 1)
    bits = ((gray[:, None] >> np.arange(m - 1, -1, -1)) & 1).astype(np.uint8)
    levels = (2 * idx - (M - 1)).astype(float)
    return levels, bits


def qam_map(bits: np.ndarray, m: int) -> np.ndarray:
    """bits (n*2m,) -> unit-power complex points. Axis bits: I first, then Q."""
    levels, lbits = _gray_levels(m)
    lookup = {tuple(b): lv for b, lv in zip(lbits, levels)}
    weights = 1 << np.arange(m - 1, -1, -1)
    code_to_level = np.zeros(1 << m)
    for b, lv in lookup.items():
        code_to_level[int(np.dot(b, weights))] = lv
    b = bits.reshape(-1, 2, m)
    i = code_to_level[b[:, 0] @ weights]
    q = code_to_level[b[:, 1] @ weights]
    M = 1 << m
    return (i + 1j * q) / np.sqrt(2 * (M * M - 1) / 3)


def qam_llr(z: np.ndarray, nvar: np.ndarray, m: int) -> np.ndarray:
    """Max-log LLRs (bit 0 positive). z unit-power points, nvar complex noise var."""
    levels, lbits = _gray_levels(m)
    M = 1 << m
    scale = np.sqrt(2 * (M * M - 1) / 3)
    out = []
    for axis in (z.real, z.imag):
        y = (axis * scale)[..., None]
        d = (y - levels) ** 2  # (..., M)
        for j in range(m):
            d0 = np.where(lbits[:, j] == 0, d, np.inf).min(-1)
            d1 = np.where(lbits[:, j] == 1, d, np.inf).min(-1)
            out.append((d1 - d0) / (nvar * scale * scale))
    return np.stack(out, axis=-1)  # (..., 2m): I bits then Q bits, as in qam_map


# ---------------------------------------------------------------------------
# Waveform pieces
# ---------------------------------------------------------------------------
_zc = np.exp(-1j * np.pi * 7 * np.arange(N_ACT) ** 2 / N_ACT)  # Zadoff-Chu-like, low crest factor
SYNC_FREQ = _zc
_PILOTS = np.array([1, -1, 1, 1, -1], dtype=complex)
_TO_TIME = NFFT / np.sqrt(2 * N_ACT)  # makes each OFDM symbol unit RMS


def _ofdm_time(X: np.ndarray) -> np.ndarray:
    """X: (nsym, N_ACT) complex -> (nsym, NFFT) real time samples (unit RMS)."""
    full = np.zeros((X.shape[0], NFFT // 2 + 1), dtype=complex)
    full[:, BINS] = X
    return np.fft.irfft(full, n=NFFT, axis=1) * _TO_TIME


SYNC_TIME = _ofdm_time(SYNC_FREQ[None])[0]


def _symbols_from_bits(coded: np.ndarray, m: int) -> np.ndarray:
    per_sym = N_DATA * 2 * m
    nsym = -(-len(coded) // per_sym)
    pad = nsym * per_sym - len(coded)
    bits = np.concatenate([coded, np.random.default_rng(len(coded)).integers(0, 2, pad, dtype=np.uint8)])
    pts = qam_map(bits, m).reshape(nsym, N_DATA)
    X = np.zeros((nsym, N_ACT), dtype=complex)
    X[:, DATA_IDX] = pts
    X[:, PILOT_IDX] = _PILOTS
    return X


def _header_bits(mode: int, length: int) -> np.ndarray:
    h = bytes([mode]) + length.to_bytes(2, "big")
    h += crc16(h).to_bytes(2, "big")
    return np.unpackbits(np.frombuffer(h, dtype=np.uint8))


_HDR_INFO = 40
_HDR_CODED = coded_len(_HDR_INFO, (1, 2))  # 92
_HDR_CAP = N_DATA * 2  # 96 bits: one QPSK symbol per header copy


def _header_coded(mode: int, length: int) -> np.ndarray:
    c = conv_encode(_header_bits(mode, length), (1, 2))
    c = c[interleaver(len(c))]
    return np.resize(c, _HDR_CAP)  # 92-bit codeword, padded to one symbol


def payload_coded_len(nbytes: int, mode: int) -> int:
    return coded_len(8 * (nbytes + 4), MODES[mode][2])


def payload_symbols(nbytes: int, mode: int) -> int:
    m = MODES[mode][1]
    return max(2, -(-payload_coded_len(nbytes, mode) // (N_DATA * 2 * m)))


def airtime(nbytes: int, mode="16qam-3/4", leader_ms: float = 250.0) -> float:
    mode = MODE_BY_NAME[mode] if isinstance(mode, str) else mode
    n_leader = max(3, int(round(leader_ms / 1000 * FS / NFFT)))
    samples = (n_leader + 1) * NFFT + (HEADER_SYMS + payload_symbols(nbytes, mode)) * SYM_LEN + FS // 100
    return samples / FS


def bitrate(mode) -> float:
    """Net coded data rate during the payload part (bits/s), ignoring overheads."""
    mode = MODE_BY_NAME[mode] if isinstance(mode, str) else mode
    _, m, (a, b) = MODES[mode]
    return N_DATA * 2 * m * a / b * FS / SYM_LEN


def modulate(payload: bytes, mode="16qam-3/4", leader_ms: float = 250.0, crest: float | None = None) -> np.ndarray:
    """Return float32 audio at 48 kHz (peak <= 0.9) for one frame.

    `crest` is the TX clipping level in multiples of RMS (default TX_CREST).
    """
    mode = MODE_BY_NAME[mode] if isinstance(mode, str) else mode
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    _, m, rate = MODES[mode]
    body = payload + zlib.crc32(payload).to_bytes(4, "big")
    bits = np.unpackbits(np.frombuffer(body, dtype=np.uint8)) ^ _SCRAMBLE[: 8 * len(body)]
    coded = conv_encode(bits, rate)
    coded = coded[interleaver(len(coded))]
    Xp = _symbols_from_bits(coded, m)
    Xh = _symbols_from_bits(_header_coded(mode, len(payload)), 1)
    if len(Xp) < 2:
        Xp = np.vstack([Xp, _symbols_from_bits(np.zeros(1, np.uint8), m)])
    t = _ofdm_time(np.vstack([Xh, Xp[:2], Xh, Xp[2:]]))
    t = np.hstack([t[:, -CP:], t]).reshape(-1)  # cyclic prefix
    n_leader = max(3, int(round(leader_ms / 1000 * FS / NFFT)))
    audio = np.concatenate([np.tile(SYNC_TIME, n_leader), -SYNC_TIME, t, np.zeros(FS // 100)])
    crest = TX_CREST if crest is None else crest
    audio = np.clip(audio, -crest, crest) * (TX_PEAK / crest)
    ramp = FS // 200
    audio[:ramp] *= np.linspace(0, 1, ramp)
    return audio.astype(np.float32)


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    start: int  # sample index of the marker symbol
    mode: int
    length: int
    llr: np.ndarray  # deinterleaved payload LLRs (coded_len,)
    end: int
    snr_db: float  # estimated per-carrier SNR (pilot/data residual based)


def _fft_syms(x: np.ndarray, starts: np.ndarray) -> np.ndarray:
    idx = starts[:, None] + np.arange(NFFT)[None]
    idx = np.clip(idx, 0, len(x) - 1)
    return np.fft.rfft(x[idx], axis=1)[:, BINS] / _TO_TIME


def _find_markers(x: np.ndarray, thresh: float = 0.45) -> list[int]:
    from numpy.fft import irfft, rfft

    n = len(x) + NFFT
    nf = 1 << int(np.ceil(np.log2(n)))
    c = irfft(rfft(x, nf) * np.conj(rfft(SYNC_TIME, nf)), nf)[: len(x)]  # c[n] = sum x[n+i] s[i]
    e = np.sqrt(np.convolve(x * x, np.ones(NFFT), "full")[NFFT - 1 :][: len(x)] * np.dot(SYNC_TIME, SYNC_TIME)) + 1e-12
    prev = np.zeros_like(c)
    prev[NFFT:] = c[:-NFFT] / e[:-NFFT]
    prev2 = np.zeros_like(c)
    prev2[2 * NFFT :] = c[: -2 * NFFT] / e[: -2 * NFFT]
    metric = 0.5 * (0.5 * (prev + prev2) - c / e)
    out: list[int] = []
    for k in np.nonzero(metric >= thresh)[0]:
        if out and k < out[-1] + 4 * NFFT:
            continue
        pk = int(np.argmax(metric[k : k + NFFT // 2])) + int(k)
        out.append(pk)
    return out


def _smooth(h: np.ndarray) -> np.ndarray:
    """Savitzky-Golay smoothing across carriers (keeps the curvature of band-edge filters)."""
    if SG_WIN <= 1:
        return h
    half = SG_WIN // 2
    out = np.empty_like(h)
    n = np.arange(len(h))
    for i in range(len(h)):
        lo, hi = max(0, i - half), min(len(h), i + half + 1)
        if hi - lo < SG_WIN:  # at the edges use a one-sided window of full length
            lo, hi = (0, SG_WIN) if lo == 0 else (len(h) - SG_WIN, len(h))
        c = np.polyfit(n[lo:hi] - i, h[lo:hi], SG_ORDER)
        out[i] = c[-1]
    return out


def _track(Z: np.ndarray):
    """Per-symbol timing (tau, samples) and common phase from pilots. Z: (nsym, N_ACT)."""
    zp = Z[:, PILOT_IDX] * np.conj(_PILOTS)
    s = np.sum(zp[:, 1:] * np.conj(zp[:, :-1]), axis=1)
    tau = -np.angle(s) * NFFT / (2 * np.pi * PILOT_SPACING)
    w = np.abs(s)
    return tau, w


def _fit_line(y: np.ndarray, w: np.ndarray):
    n = np.arange(len(y), dtype=float)
    if len(y) < 3:
        return np.full(len(y), np.average(y, weights=w + 1e-12))
    # robust: weighted LS, then drop outliers and refit
    for _ in range(2):
        A = np.stack([np.ones_like(n), n], 1) * np.sqrt(w)[:, None]
        coef = np.linalg.lstsq(A, y * np.sqrt(w), rcond=None)[0]
        fit = coef[0] + coef[1] * n
        r = np.abs(y - fit)
        w = w * (r < 3 * np.median(r) + 0.5)
    return fit


def _bad_symbols(Z: np.ndarray, ref: float) -> np.ndarray:
    """Symbols whose received power is far off the leader's: hit by a dropout or noise burst."""
    p = np.mean(np.abs(Z) ** 2, axis=1)
    med = ref
    return (p > 2.0 * med) | (p < 0.3 * med)


def _demod_symbols(x: np.ndarray, first: int, nsym: int, H: np.ndarray, ref: float):
    """Equalize and phase-correct symbols. Returns (Z, bad) where bad flags clobbered symbols."""
    starts = first + np.arange(nsym) * SYM_LEN + CP - EARLY
    Z = _fft_syms(x, starts) / H
    bad = _bad_symbols(Z, ref)
    tau, w = _track(Z)
    w = np.where(bad, 0.0, w)
    if not np.any(w > 0):
        w = np.ones(nsym)
    tau_fit = _fit_line(tau, w)
    shift = np.round(tau_fit).astype(int)
    if np.any(shift != 0):  # re-time windows so drift never eats the cyclic prefix
        Z = _fft_syms(x, starts + shift) / H
        tau, w2 = _track(Z)
        w2 = np.where(bad, 0.0, w2)
        # fit the line to the TOTAL offset, then remove the integer part we already applied
        tau_fit = _fit_line(tau + shift, w2 if np.any(w2 > 0) else w) - shift
    rot = np.exp(2j * np.pi * BINS[None] * tau_fit[:, None] / NFFT)
    Z = Z * rot
    zp = Z[:, PILOT_IDX] * np.conj(_PILOTS)
    cph = np.where(bad, 0, np.sum(zp, axis=1))
    # smooth common phase/gain over +-3 symbols (channel is slow), skipping bad symbols
    k = np.ones(7)
    cph_s = np.convolve(cph, k)[3 : 3 + nsym]
    good = (~bad).sum()
    g = np.abs(np.sum(cph)) / (max(good, 1) * len(PILOT_IDX))  # mean pilot amplitude
    Z = Z * np.exp(-1j * np.angle(cph_s))[:, None] / max(g, 1e-9)
    return Z, bad


def _noise_and_llr(Zd: np.ndarray, H2: np.ndarray, Zp: np.ndarray, m: int, bad: np.ndarray):
    """Zd: (nsym, N_DATA) equalized data, H2: |H|^2 per data carrier, Zp pilots."""
    # decision-directed residual (in the pre-equalisation domain, i.e. times |H|^2)
    levels, _ = _gray_levels(m)
    M = 1 << m
    scale = np.sqrt(2 * (M * M - 1) / 3)

    def hard(v):
        return levels[np.argmin(np.abs(v[..., None] * scale - levels), axis=-1)] / scale

    dec = hard(Zd.real) + 1j * hard(Zd.imag)
    res_d = np.abs(Zd - dec) ** 2 * H2[None]
    res_p = np.abs(Zp - _PILOTS) ** 2 * H2.mean()
    sym_var = (res_d.sum(1) + res_p.sum(1)) / (Zd.shape[1] + Zp.shape[1])
    frame_var = np.median(sym_var[~bad]) if np.any(~bad) else np.median(sym_var)
    used = np.maximum(sym_var, frame_var)
    used = np.where(sym_var > 4 * frame_var, sym_var * 4, used)  # clobbered symbol: near-erase
    nvar = used[:, None] / H2[None]
    llr = qam_llr(Zd, nvar, m)  # (nsym, N_DATA, 2m) ordering: I bits, Q bits
    llr[bad] = 0.0  # erase: let the code fill these bits in from the others
    snr = 10 * np.log10(H2.mean() / max(frame_var, 1e-12))
    return llr, snr


def receive(audio: np.ndarray) -> list[Candidate]:
    """Front end: find frames, decode headers, return payload LLRs (no Viterbi yet)."""
    x = np.asarray(audio, dtype=np.float64)
    out = []
    for mk in _find_markers(x):
        # channel estimate from the last N_EST leader copies before the marker
        starts = mk - NFFT * np.arange(1, N_EST + 1) - EARLY
        if starts.min() < 0:
            continue
        Y = _fft_syms(x, starts)
        p = np.mean(np.abs(Y) ** 2, axis=1)
        keep = (p < 2.0 * np.median(p)) & (p > 0.5 * np.median(p))  # skip leader copies hit by a dropout
        Hraw = Y[keep].mean(0) / SYNC_FREQ
        # smooth across carriers: remove linear phase, moving average, put back
        slope = np.angle(np.sum(Hraw[1:] * np.conj(Hraw[:-1])))
        lin = np.exp(1j * slope * np.arange(N_ACT))
        Hs = Hraw / lin
        Hs = _smooth(Hs)
        H = Hs * lin
        if not np.all(np.abs(H) > 1e-9):
            continue
        first = mk + NFFT
        # header
        if first + 4 * SYM_LEN > len(x):
            continue
        ref = float(np.mean(np.abs(Y[keep] / H) ** 2))  # expected symbol power (signal + noise)
        Z, bad = _demod_symbols(x, first, 4, H, ref)
        hp = list(HEADER_POS)
        H2 = np.abs(H[DATA_IDX]) ** 2
        llr, _ = _noise_and_llr(Z[hp][:, DATA_IDX], H2, Z[hp][:, PILOT_IDX], 1, bad[hp] & ~np.all(bad[hp]))
        llr = llr.reshape(-1)
        comb = np.zeros(_HDR_CODED)
        np.add.at(comb, np.tile(np.arange(_HDR_CAP) % _HDR_CODED, 2), llr)
        deint = np.empty(_HDR_CODED)
        deint[interleaver(_HDR_CODED)] = comb
        hb = viterbi(depuncture(deint, _HDR_INFO), _HDR_INFO)[0]
        hdr = np.packbits(hb).tobytes()
        if crc16(hdr[:3]) != int.from_bytes(hdr[3:5], "big"):
            continue
        mode, length = hdr[0], int.from_bytes(hdr[1:3], "big")
        if mode not in MODES or length > MAX_PAYLOAD:
            continue
        _, m, rate = MODES[mode]
        nsym = payload_symbols(length, mode)
        pfirst = first + HEADER_SYMS * SYM_LEN
        end = pfirst + nsym * SYM_LEN
        if end > len(x) + CP:
            continue
        # demodulate header+payload together so timing tracking spans the whole frame
        Z, bad = _demod_symbols(x, first, HEADER_SYMS + nsym, H, ref)
        sel = np.setdiff1d(np.arange(HEADER_SYMS + nsym), HEADER_POS)
        Z, bad = Z[sel], bad[sel]
        llr, snr = _noise_and_llr(Z[:, DATA_IDX], H2, Z[:, PILOT_IDX], m, bad)
        llr = llr.reshape(-1)
        n = payload_coded_len(length, mode)
        deint = np.empty(n)
        deint[interleaver(n)] = llr[:n]
        out.append(Candidate(mk, mode, length, deint, end, snr))
    return out


def finish(cands: list[Candidate]) -> list[bytes | None]:
    """Viterbi-decode candidates (batched by mode/length); None where CRC fails."""
    res: list[bytes | None] = [None] * len(cands)
    groups: dict[tuple[int, int], list[int]] = {}
    for i, c in enumerate(cands):
        groups.setdefault((c.mode, c.length), []).append(i)
    for (mode, length), idx in groups.items():
        rate = MODES[mode][2]
        n_info = 8 * (length + 4)
        llr = np.stack([cands[i].llr for i in idx])
        bits = viterbi(depuncture(llr, n_info, rate), n_info)
        bits ^= _SCRAMBLE[:n_info][None]
        for j, i in enumerate(idx):
            body = np.packbits(bits[j]).tobytes()
            if zlib.crc32(body[:-4]).to_bytes(4, "big") == body[-4:]:
                res[i] = body[:-4]
    return res


def demodulate(audio: np.ndarray) -> list[bytes]:
    """Find and decode every valid frame in a buffer of 48 kHz audio."""
    return [p for p in finish(receive(audio)) if p is not None]


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time

    rng = np.random.default_rng(1)
    print(f"{'mode':10s} {'rate b/s':>9s} {'airtime 220B':>13s}  result")
    for mid, (name, _, _) in MODES.items():
        payload = rng.integers(0, 256, 220, dtype=np.uint8).tobytes()
        tx = modulate(payload, name)
        # loopback with a little noise, a delay, and 60 ppm sample-rate offset
        sig = np.concatenate([np.zeros(3000), tx, np.zeros(3000)])
        from scipy.signal import resample

        sig = resample(sig, int(round(len(sig) * (1 + 60e-6))))
        p = np.mean(tx.astype(float) ** 2)
        sig = sig + rng.normal(0, np.sqrt(p / 10 ** (35 / 10)), len(sig))
        t0 = time.time()
        got = demodulate(sig)
        ok = got == [payload]
        print(f"{name:10s} {bitrate(name):9.0f} {airtime(220, name):12.3f}s  {'OK' if ok else 'FAIL'} ({time.time() - t0:.2f}s)")
        assert ok, name
    # two frames back to back in one buffer
    a, b = b"hello gorb" * 10, b"second frame"
    sig = np.concatenate([np.zeros(500), modulate(a, "qpsk-1/2"), modulate(b, "64qam-2/3"), np.zeros(500)])
    assert demodulate(sig) == [a, b]
    print("self-test passed")

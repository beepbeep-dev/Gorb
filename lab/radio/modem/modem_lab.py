"""Gorb modem lab: AFSK1200 vs 4-FSK+FEC vs OFDM+FEC over simulated audio radio paths.

    python lab/radio/modem/modem_lab.py            # full run (~5-10 min on 4 cores)
    python lab/radio/modem/modem_lab.py --quick    # smoke test (~1 min)
    python lab/radio/modem/modem_lab.py --plot     # redraw charts from results.json

Everything runs at 48 kHz on real-valued audio. Results go to
lab/radio/modem/results/ (results.json + PNG charts).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from scipy.signal import bilinear, butter, lfilter, resample_poly, sosfilt
from scipy.special import i0e, logsumexp

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

import gorb_modem2 as m2  # noqa: E402
from gorb.modem import Demodulator, Modulator  # noqa: E402  (the baseline, unmodified)

FS = 48000
PAYLOAD = 220  # bytes: a G.O.R.B DATA packet (180-byte chunk + ~40 bytes of header)
RESULTS = os.path.join(HERE, "results")

# ---------------------------------------------------------------------------
# Channel models
# ---------------------------------------------------------------------------
_NOISE_BP = butter(8, [300, 3000], "bandpass", fs=FS, output="sos")
_RX_BP = butter(4, [300, 3000], "bandpass", fs=FS, output="sos")  # radio + sound card audio filter
_SPLATTER = butter(4, 3300, "lowpass", fs=FS, output="sos")  # post-limiter filter in the TX


def _shelf(tau_zero: float, tau_pole: float):
    """First-order analog (1 + s*tz)/(1 + s*tp), bilinear, normalised to 0 dB at 1 kHz."""
    b, a = bilinear([tau_zero, 1.0], [tau_pole, 1.0], fs=FS)
    w = np.exp(-2j * np.pi * 1000 / FS)
    g = abs(np.polyval(b[::-1], w) / np.polyval(a[::-1], w))
    return b / g, a


PRE_TX = _shelf(750e-6, 1 / (2 * np.pi * 12000))  # TX pre-emphasis (750 us, US standard)
DE_RX = _shelf(0.0, 300e-6)  # RX de-emphasis with the WRONG time constant -> residual tilt


def band_noise(n: int, power: float, rng) -> np.ndarray:
    w = sosfilt(_NOISE_BP, rng.standard_normal(n + 2000))[2000:]
    return w * np.sqrt(power / np.mean(w * w))


def sro(x: np.ndarray, ppm: float) -> np.ndarray:
    """Resample by (1 + ppm*1e-6): what a receiving sound card with a different clock sees."""
    up = resample_poly(x, 4, 1)
    pos = np.arange(int(len(x) * (1 + ppm * 1e-6))) * 4 / (1 + ppm * 1e-6)
    return np.interp(pos, np.arange(len(up)), up)


def ch_awgn(tx: np.ndarray, snr_db: float, rng) -> np.ndarray:
    """Channel 1: in-band (300-3000 Hz) white noise; SNR relative to the signal's own mean power."""
    lead, tail = rng.integers(2400, 4800), 2400
    x = np.concatenate([np.zeros(lead), tx, np.zeros(tail)])
    p = np.mean(tx.astype(float) ** 2)
    return x + band_noise(len(x), p / 10 ** (snr_db / 10), rng)


FM_DEFAULT = dict(drive_db=0.0, ppm=80.0, keyup_ms=100.0, dropouts_per_s=0.0)


def drive_gain(tx: np.ndarray) -> float:
    """Sound-card level an operator would set: pre-emphasised peaks (99.9th pct) just reach the limiter."""
    pe = lfilter(*PRE_TX, tx.astype(float))
    return 1.0 / np.percentile(np.abs(pe), 99.9)


def ch_fm(tx: np.ndarray, snr_db: float, rng, drive_db=0.0, ppm=80.0, keyup_ms=100.0, dropouts_per_s=0.0):
    """Channel 2/3: an FM voice radio audio path.

    TX: keyup delay -> pre-emphasis -> deviation limiter (hard clip) -> splatter filter.
    RX: de-emphasis (mismatched: net ~+4 dB tilt across the band) -> 300-3000 Hz band-pass
        -> sound-card clock offset (+-ppm) -> [dropouts] -> in-band noise.
    SNR here is relative to a FULL-DEVIATION 1 kHz tone (power 0.5), the way an FM link
    really behaves: the noise is set by the RF signal, and a peaky waveform must back off.
    """
    x = tx.astype(float) * drive_gain(tx) * 10 ** (drive_db / 20)
    x[: int(keyup_ms / 1000 * FS)] = 0.0  # radio is not yet on the air
    lead, tail = rng.integers(2400, 4800), 2400
    x = np.concatenate([np.zeros(lead), x, np.zeros(tail)])
    x = lfilter(*PRE_TX, x)
    x = np.clip(x, -1.0, 1.0)
    x = sosfilt(_SPLATTER, x)
    x = lfilter(*DE_RX, x)
    x = sosfilt(_RX_BP, x)
    x = sro(x, ppm * rng.choice([-1, 1]))
    noise = band_noise(len(x), 0.5 / 10 ** (snr_db / 10), rng)
    if dropouts_per_s > 0:  # short fades: signal gone, squelch-noise burst at 0 dB
        n_drop = rng.poisson(dropouts_per_s * len(x) / FS)
        for _ in range(n_drop):
            d = int(rng.uniform(0.005, 0.030) * FS)
            s = rng.integers(0, max(1, len(x) - d))
            x[s : s + d] = 0.0
            noise[s : s + d] = band_noise(d, 0.5, rng)
    return x + noise


# ---------------------------------------------------------------------------
# Modem 0: the existing G.O.R.B AFSK1200 (imported unchanged from gorb/modem.py)
# ---------------------------------------------------------------------------
class AFSK:
    name = "AFSK1200 (current)"

    def __init__(self):
        self.mod, self.demod = Modulator(), Demodulator()

    def modulate(self, payload):
        return self.mod.modulate(payload)

    def receive(self, audio):
        return [p for p, _ in self.demod.decode(audio)]

    def finish(self, cands):
        return cands


# ---------------------------------------------------------------------------
# Modem 1: 4-FSK, 600 baud, non-coherent, K=7 convolutional code + interleaver
# ---------------------------------------------------------------------------
class MFSK4:
    SPS = 80  # 600 baud
    TONES = 900.0 + 600.0 * np.arange(4)  # orthogonal for non-coherent detection
    GRAY = np.array([0, 1, 3, 2])  # symbol -> 2-bit gray value
    RATES = {1: (1, 2), 2: (3, 4)}
    _rng = np.random.default_rng(42)
    LEADER = _rng.integers(0, 4, 32)
    SYNC = _rng.integers(0, 4, 32)
    HDR_REP = 2

    def __init__(self, rate=(1, 2), leader_ms=250.0):
        self.rate = tuple(rate)
        self.mode = {v: k for k, v in self.RATES.items()}[self.rate]
        self.name = f"4FSK-600 r{rate[0]}/{rate[1]}"
        self.n_leader = int(leader_ms / 1000 * 600)
        self._sym_bits = np.array([[(g >> 1) & 1, g & 1] for g in self.GRAY])  # (4,2)
        self._bits_to_sym = {(int(b[0]), int(b[1])): s for s, b in enumerate(self._sym_bits)}

    def _bits_to_syms(self, bits):
        b = bits.reshape(-1, 2)
        lut = np.zeros(4, dtype=int)
        for (b0, b1), s in self._bits_to_sym.items():
            lut[2 * b0 + b1] = s
        return lut[2 * b[:, 0] + b[:, 1]]

    def _header_coded(self, length):
        hb = m2._header_bits(self.mode, length)
        c = m2.conv_encode(hb, (1, 2))
        return np.tile(c[m2.interleaver(len(c))], self.HDR_REP)

    def modulate(self, payload):
        body = payload + m2.zlib.crc32(payload).to_bytes(4, "big")
        bits = np.unpackbits(np.frombuffer(body, dtype=np.uint8)) ^ m2._SCRAMBLE[: 8 * len(body)]
        c = m2.conv_encode(bits, self.rate)
        c = c[m2.interleaver(len(c))]
        if len(c) % 2:
            c = np.append(c, 0)
        syms = np.concatenate(
            [np.resize(self.LEADER, self.n_leader), self.SYNC, self._bits_to_syms(self._header_coded(len(payload))), self._bits_to_syms(c)]
        )
        f = np.repeat(self.TONES[syms], self.SPS)
        audio = 0.6 * np.sin(2 * np.pi * np.cumsum(f) / FS)
        audio = np.concatenate([audio, np.zeros(FS // 100)])
        r = FS // 200
        audio[:r] *= np.linspace(0, 1, r)
        return audio.astype(np.float32)

    def _energies(self, x):
        t = np.arange(len(x)) / FS
        A = []
        for f in self.TONES:
            cs = np.concatenate([[0], np.cumsum(x * np.exp(-2j * np.pi * f * t))])
            a = np.zeros(len(x), dtype=complex)
            a[self.SPS - 1 :] = cs[self.SPS :] - cs[: -self.SPS]
            A.append(a)
        return np.abs(np.array(A))  # (4, n): amplitude of window ENDING at sample n

    def receive(self, audio):
        x = np.asarray(audio, dtype=float)
        R = self._energies(x)
        E = R * R
        tot = E.sum(0) + 1e-12
        n_sync = len(self.SYNC)
        L = len(x) - (n_sync + 1) * self.SPS
        if L <= 0:
            return []
        num = np.zeros(L)
        den = np.zeros(L)
        for i, s in enumerate(self.SYNC):
            o = (i + 1) * self.SPS - 1
            num += E[s, o : o + L]
            den += tot[o : o + L]
        metric = num / den
        out = []
        k0 = 0
        while True:
            above = np.nonzero(metric[k0:] > 0.6)[0]
            if not len(above):
                break
            k = above[0] + k0
            p = int(np.argmax(metric[k : k + n_sync * self.SPS])) + k
            cand = self._decode_at(R, E, p)
            k0 = p + n_sync * self.SPS
            if cand is not None:
                out.append(cand)
                k0 = max(k0, cand["end"])
        return out

    def _decode_at(self, R, E, p):
        sps, n_sync = self.SPS, len(self.SYNC)
        ends = p + (np.arange(n_sync) + 1) * sps - 1
        sent = E[:, ends]  # (4, n_sync)
        mask = np.zeros_like(sent, dtype=bool)
        mask[self.SYNC, np.arange(n_sync)] = True
        N0 = sent[~mask].mean() + 1e-12
        Es = np.array([max(sent[mm][mask[mm]].mean() - N0, 1e-3 * N0) if mask[mm].any() else np.nan for mm in range(4)])
        Es = np.where(np.isnan(Es), np.nanmean(Es), Es)
        start = p + n_sync * sps
        nmax = (E.shape[1] - start) // sps

        def llrs(first_sym, nsym):
            idx = start + (first_sym + np.arange(nsym) + 1) * sps - 1
            # timing tracking: best offset per 64-symbol block, line fit (handles clock offset)
            Emax = E.max(0)
            offs = np.arange(-12, 13)
            nb = max(1, nsym // 64)
            blk = np.array_split(np.arange(nsym), nb)
            best, wts, centers = [], [], []
            for b in blk:
                sc = np.array([Emax[np.clip(idx[b] + d, 0, len(Emax) - 1)].sum() for d in offs])
                best.append(offs[np.argmax(sc)])
                wts.append(1.0)
                centers.append(b.mean())
            if nb >= 3:
                c = np.polyfit(centers, best, 1)
                adj = np.round(np.polyval(c, np.arange(nsym))).astype(int)
            else:
                adj = np.full(nsym, int(np.median(best)))
            idx = np.clip(idx + adj, 0, E.shape[1] - 1)
            r = R[:, idx]  # (4, nsym)
            xarg = 2 * np.sqrt(Es)[:, None] * r / N0
            metric = np.log(i0e(xarg)) + xarg - (Es / N0)[:, None]
            out = np.empty((nsym, 2))
            for j in range(2):
                s0 = self._sym_bits[:, j] == 0
                out[:, j] = logsumexp(metric[s0], axis=0) - logsumexp(metric[~s0], axis=0)
            return np.clip(out.reshape(-1), -50, 50)

        nh = m2._HDR_CODED * self.HDR_REP // 2
        if nh > nmax:
            return None
        lh = llrs(0, nh).reshape(self.HDR_REP, -1).sum(0)
        de = np.empty(m2._HDR_CODED)
        de[m2.interleaver(m2._HDR_CODED)] = lh
        hb = m2.viterbi(m2.depuncture(de, m2._HDR_INFO), m2._HDR_INFO)[0]
        hdr = np.packbits(hb).tobytes()
        if m2.crc16(hdr[:3]) != int.from_bytes(hdr[3:5], "big") or hdr[0] not in self.RATES:
            return None
        rate, length = self.RATES[hdr[0]], int.from_bytes(hdr[1:3], "big")
        n = m2.coded_len(8 * (length + 4), rate)
        npay = (n + 1) // 2
        if nh + npay > nmax:
            return None
        lp = llrs(0, nh + npay)[2 * nh : 2 * nh + n]  # track over header+payload together
        de = np.empty(n)
        de[m2.interleaver(n)] = lp
        return dict(rate=rate, length=length, llr=de, end=start + (nh + npay) * sps)

    def finish(self, cands):
        res = []
        groups = {}
        for i, c in enumerate(cands):
            groups.setdefault((c["rate"], c["length"]), []).append(i)
        res = [None] * len(cands)
        for (rate, length), idx in groups.items():
            n_info = 8 * (length + 4)
            bits = m2.viterbi(m2.depuncture(np.stack([cands[i]["llr"] for i in idx]), n_info, rate), n_info)
            bits ^= m2._SCRAMBLE[:n_info][None]
            for j, i in enumerate(idx):
                body = np.packbits(bits[j]).tobytes()
                if m2.zlib.crc32(body[:-4]).to_bytes(4, "big") == body[-4:]:
                    res[i] = body[:-4]
        return res


# ---------------------------------------------------------------------------
# Modem 2: Gorb Modem 2 (OFDM), implemented in gorb_modem2.py
# ---------------------------------------------------------------------------
class OFDM:
    def __init__(self, mode):
        self.mode = mode
        self.name = f"OFDM {mode}"

    def modulate(self, payload):
        return m2.modulate(payload, self.mode)

    def receive(self, audio):
        return m2.receive(audio)

    def finish(self, cands):
        return m2.finish(cands)


OFDM_MODES = ["qpsk-1/2", "qpsk-3/4", "16qam-1/2", "16qam-3/4", "64qam-2/3", "64qam-3/4"]


def make_modem(key):
    if key == "afsk":
        return AFSK()
    if key.startswith("4fsk"):
        return MFSK4((1, 2) if key.endswith("1/2") else (3, 4))
    return OFDM(key)


MODEM_KEYS = ["afsk", "4fsk-1/2", "4fsk-3/4"] + OFDM_MODES

# ---------------------------------------------------------------------------
# Monte-Carlo
# ---------------------------------------------------------------------------


def run_point(args):
    key, chan, snr, nframes, seed, extra = args
    rng = np.random.default_rng(seed)
    modem = make_modem(key)
    flat, owner, payloads = [], [], []
    airtime = None
    t0 = time.time()
    for f in range(nframes):
        payload = rng.integers(0, 256, PAYLOAD, dtype=np.uint8).tobytes()
        tx = modem.modulate(payload)
        airtime = len(tx) / FS
        if chan == "awgn":
            rx = ch_awgn(tx, snr, rng)
        else:
            rx = ch_fm(tx, snr, rng, **{**FM_DEFAULT, **extra})
        c = modem.receive(rx)
        flat += c
        owner += [f] * len(c)
        payloads.append(payload)
    got = modem.finish(flat)
    ok = np.zeros(nframes, dtype=bool)
    false_frames = 0
    for f, g in zip(owner, got):
        if g is None:
            continue
        if g == payloads[f]:
            ok[f] = True
        else:
            false_frames += 1
    fer = 1 - ok.mean()
    return dict(
        modem=key, channel=chan, snr=snr, frames=nframes, ok=int(ok.sum()), fer=float(fer),
        airtime=airtime, goodput=float(ok.sum() * PAYLOAD * 8 / (nframes * airtime)),
        false_frames=false_frames, extra=extra, secs=time.time() - t0,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="few frames, coarse SNR grid")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--plot", action="store_true", help="only redraw charts from results.json")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "results_quick.json" if a.quick else "results.json")
    if a.plot:
        import modem_plots

        modem_plots.plot_all(json.load(open(path)), RESULTS)
        return

    nf = a.frames or (16 if a.quick else 150)
    snrs = np.arange(0, 41, 10 if a.quick else 2.5)
    tasks = []
    seed = 1
    for key in MODEM_KEYS:
        for chan in ("awgn", "fm"):
            for s in snrs:
                tasks.append((key, chan, float(s), nf, seed, {}))
                seed += 1
        # channel 3: FM path + short dropouts (1 per second on average, 5-30 ms)
        for s in ([20, 30] if a.quick else [15, 20, 25, 30, 35, 40]):
            tasks.append((key, "fm+dropouts", float(s), nf, seed, {"dropouts_per_s": 1.0}))
            seed += 1
    # drive-level sweep on the FM channel (how hard to hit the limiter)
    for key in ["afsk", "4fsk-1/2", "qpsk-1/2", "16qam-3/4", "64qam-2/3"]:
        for d in ([-6, 0, 6] if a.quick else [-9, -6, -3, 0, 3, 6]):
            tasks.append((key, "fm-drive", 30.0, nf, seed, {"drive_db": float(d)}))
            seed += 1
    # heavier clock offset (cheap USB sound cards can be 200+ ppm apart)
    for key in ["afsk", "4fsk-1/2", "16qam-3/4", "64qam-2/3"]:
        for p in ([0, 300] if a.quick else [0, 100, 200, 300, 500]):
            tasks.append((key, "fm-ppm", 30.0, nf, seed, {"ppm": float(p)}))
            seed += 1
    # slow tasks first for better load balancing
    tasks.sort(key=lambda t: (t[0] != "afsk", not t[0].startswith("4fsk")))
    t0 = time.time()
    print(f"{len(tasks)} points x {nf} frames on {a.workers} workers")
    results = []
    with Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(run_point, tasks)):
            results.append(r)
            if i % 20 == 0 or i == len(tasks) - 1:
                print(f"  [{i + 1}/{len(tasks)}] {time.time() - t0:6.0f}s  {r['modem']:10s} {r['channel']:12s} "
                      f"snr={r['snr']:4.1f} FER={r['fer']:.3f} goodput={r['goodput']:6.0f} b/s", flush=True)
    meta = dict(frames_per_point=nf, payload_bytes=PAYLOAD, runtime_s=time.time() - t0, fm_default=FM_DEFAULT,
                names={k: make_modem(k).name for k in MODEM_KEYS})
    json.dump(dict(meta=meta, results=results), open(path, "w"), indent=1)
    print(f"done in {time.time() - t0:.0f}s -> {path}")
    import modem_plots

    modem_plots.plot_all(dict(meta=meta, results=results), RESULTS, suffix="_quick" if a.quick else "")


if __name__ == "__main__":
    main()

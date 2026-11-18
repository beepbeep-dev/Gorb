"""AFSK modem: turns bytes into audio tones and audio back into bytes.

This is the part of G.O.R.B that actually "speaks radio". It uses the same
tones as classic Bell 202 / APRS packet radio (1200 Hz = 1, 2200 Hz = 0 at
1200 baud), so any radio with an audio input (mic jack, VOX, a sound-card
interface like a SignaLink/Digirig, or an SDR) can carry it.

On-air frame layout (every byte sent MSB first):

    preamble   0x55 0x55 ...        lets the receiver lock on / keys up VOX
    sync       0x7E 'G' 'R' 'B'     marks the start of a frame
    -- everything below is whitened with a PRBS so there are no long runs --
    length     2 bytes, big endian  payload length
    payload    <length> bytes
    crc32      4 bytes               over length + payload
    tail       0x00 0x00             lets the last bit ring out
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

SYNC = b"\x7eGRB"
MAX_PAYLOAD = 1024


def _bits_from_bytes(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def _whiten_mask(nbytes: int) -> np.ndarray:
    """Deterministic 9-bit LFSR (x^9 + x^5 + 1) byte stream, same on TX and RX."""
    state = 0x1FF
    out = np.empty(nbytes, dtype=np.uint8)
    for i in range(nbytes):
        byte = 0
        for _ in range(8):
            bit = ((state >> 8) ^ (state >> 4)) & 1
            state = ((state << 1) | bit) & 0x1FF
            byte = (byte << 1) | bit
        out[i] = byte
    return out


_MASK_CACHE = _whiten_mask(MAX_PAYLOAD + 6)
_BIT_CHARS = bytes.maketrans(b"\x00\x01", b"01")


def _whiten(data: bytes) -> bytes:
    arr = np.frombuffer(data, dtype=np.uint8)
    return (arr ^ _MASK_CACHE[: len(arr)]).tobytes()


@dataclass
class ModemConfig:
    sample_rate: int = 48000
    baud: int = 1200
    mark_hz: float = 1200.0  # bit 1
    space_hz: float = 2200.0  # bit 0
    preamble_ms: float = 250.0  # long enough to trip a radio's VOX
    amplitude: float = 0.6

    @property
    def samples_per_bit(self) -> int:
        spb = self.sample_rate / self.baud
        if abs(spb - round(spb)) > 1e-9:
            raise ValueError("sample_rate must be a whole multiple of baud")
        return int(round(spb))


class Modulator:
    def __init__(self, config: ModemConfig | None = None):
        self.cfg = config or ModemConfig()

    def frame_bits(self, payload: bytes) -> np.ndarray:
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"payload too large ({len(payload)} > {MAX_PAYLOAD})")
        body = len(payload).to_bytes(2, "big") + payload
        body += zlib.crc32(body).to_bytes(4, "big")
        n_pre = max(4, int(self.cfg.preamble_ms / 1000 * self.cfg.baud / 8))
        raw = b"\x55" * n_pre + SYNC + _whiten(body) + b"\x00\x00"
        return _bits_from_bytes(raw)

    def modulate(self, payload: bytes) -> np.ndarray:
        """Return float32 audio samples in [-1, 1] for one frame."""
        cfg = self.cfg
        bits = self.frame_bits(payload)
        spb = cfg.samples_per_bit
        freqs = np.where(np.repeat(bits, spb) == 1, cfg.mark_hz, cfg.space_hz)
        # Phase-continuous FSK: integrate frequency so tones join smoothly.
        phase = 2 * np.pi * np.cumsum(freqs) / cfg.sample_rate
        audio = cfg.amplitude * np.sin(phase)
        # Short fade in/out to avoid clicks.
        ramp = min(len(audio) // 2, cfg.sample_rate // 200)
        if ramp:
            win = np.linspace(0.0, 1.0, ramp)
            audio[:ramp] *= win
            audio[-ramp:] *= win[::-1]
        return audio.astype(np.float32)

    def airtime(self, payload_len: int) -> float:
        """Seconds of airtime needed to send a payload of this length."""
        n_pre = max(4, int(self.cfg.preamble_ms / 1000 * self.cfg.baud / 8))
        nbytes = n_pre + len(SYNC) + 2 + payload_len + 4 + 2
        return nbytes * 8 / self.cfg.baud


class Demodulator:
    """Non-coherent FSK demodulator with frame sync and CRC check.

    Works on a one-off chunk of audio (`decode`) or on a live stream
    (`feed`), where it keeps a rolling buffer so frames split across
    sound-card callbacks are still found.
    """

    def __init__(self, config: ModemConfig | None = None):
        self.cfg = config or ModemConfig()
        self._buf = np.zeros(0, dtype=np.float32)
        self._new = 0
        spb = self.cfg.samples_per_bit
        self._max_frame_samples = (len(SYNC) + 2 + MAX_PAYLOAD + 6) * 8 * spb
        self._sync_bits = "".join(str(b) for b in _bits_from_bytes(SYNC))

    # -- soft bit stream -------------------------------------------------
    def _soft(self, audio: np.ndarray) -> np.ndarray:
        """Per-sample soft decision: >0 means mark (1), <0 means space (0).

        Value at index n reflects the energy of each tone over the bit-long
        window ending at n.
        """
        cfg = self.cfg
        spb = cfg.samples_per_bit
        t = np.arange(len(audio)) / cfg.sample_rate
        kernel = np.ones(spb)

        def tone_energy(freq: float) -> np.ndarray:
            ref = np.exp(-2j * np.pi * freq * t)
            mixed = audio * ref
            acc = np.convolve(mixed, kernel)[: len(audio)]
            return np.abs(acc) ** 2

        return tone_energy(cfg.mark_hz) - tone_energy(cfg.space_hz)

    # -- frame search ----------------------------------------------------
    def decode(self, audio: np.ndarray) -> list[tuple[bytes, int]]:
        """Find every valid frame in `audio`.

        Returns a list of (payload, end_sample_index).
        """
        audio = np.asarray(audio, dtype=np.float64)
        spb = self.cfg.samples_per_bit
        if len(audio) < spb * 64:
            return []
        peak = np.max(np.abs(audio))
        if peak <= 1e-6:
            return []
        soft = self._soft(audio / peak)

        found: dict[int, tuple[bytes, int]] = {}
        # Try each sampling phase; keep frames by their start position.
        for phase in range(spb - 1, spb - 1 + spb, max(1, spb // 8)):
            idx = np.arange(phase, len(soft), spb)
            bits = (soft[idx] > 0).astype(np.uint8)
            bitstr = bits.tobytes().translate(_BIT_CHARS).decode()
            start = 0
            while True:
                pos = bitstr.find(self._sync_bits, start)
                if pos < 0:
                    break
                start = pos + 1
                data_bit = pos + len(self._sync_bits)
                result = self._try_frame(bits, data_bit)
                if result is None:
                    continue
                payload, nbits = result
                start_sample = int(idx[pos])
                if any(abs(start_sample - s) < spb * 8 for s in found):
                    break  # already decoded this frame at another phase
                end_bit = data_bit + nbits
                end_sample = int(idx[min(end_bit, len(idx) - 1)])
                found[start_sample] = (payload, end_sample)
                start = end_bit
        return [found[k] for k in sorted(found)]

    @staticmethod
    def _try_frame(bits: np.ndarray, data_bit: int) -> tuple[bytes, int] | None:
        if data_bit + 16 > len(bits):
            return None
        hdr = np.packbits(bits[data_bit : data_bit + 16]).tobytes()
        hdr = _whiten(hdr)
        length = int.from_bytes(hdr, "big")
        if length > MAX_PAYLOAD:
            return None
        nbits = (2 + length + 4) * 8
        if data_bit + nbits > len(bits):
            return None
        body = _whiten(np.packbits(bits[data_bit : data_bit + nbits]).tobytes())
        content, crc = body[:-4], body[-4:]
        if zlib.crc32(content).to_bytes(4, "big") != crc:
            return None
        return content[2:], nbits

    # -- streaming -------------------------------------------------------
    def feed(self, samples: np.ndarray) -> list[bytes]:
        """Push live audio in; get back any complete frames."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._buf = np.concatenate([self._buf, samples])
        self._new += len(samples)
        if self._new < self.cfg.sample_rate // 4:  # decode ~4x per second
            return []
        self._new = 0
        frames = self.decode(self._buf)
        keep_from = max(0, len(self._buf) - self._max_frame_samples)
        if frames:
            keep_from = max(keep_from, frames[-1][1] + 1)
        self._buf = self._buf[keep_from:]
        return [p for p, _ in frames]

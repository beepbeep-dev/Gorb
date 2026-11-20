"""Ways to get modem audio on and off the air.

* AudioTransport  - a real radio through your sound card (mic/speaker jack,
                    VOX, or a USB radio interface). Optional serial PTT.
* SimTransport    - a pretend radio band on your computer. Every simulated
                    station sends real modem audio over local UDP multicast,
                    with a position and a range, so you can try relaying
                    without owning a radio.
"""

from __future__ import annotations

import math
import os
import random
import socket
import struct
import threading
import time
from typing import Callable

import numpy as np

from .modem import Demodulator, ModemConfig, Modulator

FrameHandler = Callable[[bytes], None]


class Transport:
    config: ModemConfig

    def __init__(self, config: ModemConfig):
        self.config = config
        self.mod = Modulator(config)
        self._busy_until = 0.0

    def start(self, on_frame: FrameHandler) -> None:
        raise NotImplementedError

    def send(self, frame: bytes) -> None:
        raise NotImplementedError

    def busy(self) -> bool:
        """True while someone else is (probably) transmitting."""
        return time.monotonic() < self._busy_until

    def airtime(self, nbytes: int) -> float:
        return self.mod.airtime(nbytes)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Simulated band
# ---------------------------------------------------------------------------

_SIM_GROUP = "239.71.79.82"
_SIM_BASE_PORT = 47100
_SIM_HEAD = struct.Struct(">4s8sfffI")  # magic, station id, x, y, range, n samples


class SimTransport(Transport):
    """A fake radio channel shared by every G.O.R.B process on this machine.

    Stations only hear each other when within `range` of the sender, which
    is what makes relaying (rule 2) necessary in the demo.
    """

    def __init__(
        self,
        channel: int = 0,
        x: float = 0.0,
        y: float = 0.0,
        range_: float = 10.0,
        noise: float = 0.05,
        loss: float = 0.0,
        realtime: bool = False,
        speed: float = 1.0,
    ):
        # 9600 Hz keeps simulated frames small enough for one UDP datagram.
        super().__init__(ModemConfig(sample_rate=9600, preamble_ms=30))
        self.port = _SIM_BASE_PORT + channel
        self.x, self.y, self.range = x, y, range_
        self.noise, self.loss, self.realtime = noise, loss, realtime
        self.speed = speed  # with realtime: airtime runs this many times faster (10 ~ LoRa)
        self.station_id = os.urandom(8)
        self.demod = Demodulator(self.config)
        self._on_frame: FrameHandler | None = None
        self._closed = False

        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        # Big receive buffer: without --realtime, stations fire packets back to
        # back and a small buffer would drop them (a real radio can't do that).
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        self.rx.bind(("", self.port))
        mreq = struct.pack("4s4s", socket.inet_aton(_SIM_GROUP), socket.inet_aton("127.0.0.1"))
        self.rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.rx.settimeout(0.5)

        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)

    def start(self, on_frame: FrameHandler) -> None:
        self._on_frame = on_frame
        threading.Thread(target=self._rx_loop, daemon=True, name="sim-rx").start()

    def send(self, frame: bytes) -> None:
        audio = self.mod.modulate(frame)
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        head = _SIM_HEAD.pack(b"GSIM", self.station_id, self.x, self.y, self.range, len(audio))
        self.tx.sendto(head + pcm, (_SIM_GROUP, self.port))
        if self.realtime:
            time.sleep(len(audio) / self.config.sample_rate / self.speed)
        else:
            time.sleep(0.002)  # keep the receivers from being flooded

    def airtime(self, nbytes: int) -> float:
        # Without --realtime the simulated band delivers instantly.
        return super().airtime(nbytes) / self.speed if self.realtime else 0.005

    def _rx_loop(self) -> None:
        while not self._closed:
            try:
                data = self.rx.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) < _SIM_HEAD.size:
                continue
            magic, sid, x, y, rng, n = _SIM_HEAD.unpack_from(data)
            if magic != b"GSIM" or sid == self.station_id:
                continue
            if math.hypot(x - self.x, y - self.y) > rng:
                continue  # out of radio range: we can't hear them
            audio = np.frombuffer(data[_SIM_HEAD.size :], dtype="<i2").astype(np.float32) / 32767
            duration = len(audio) / self.config.sample_rate
            self._busy_until = time.monotonic() + (duration / self.speed if self.realtime else 0.02)
            if random.random() < self.loss:
                continue
            if self.noise:
                audio = audio + np.random.normal(0, self.noise, len(audio)).astype(np.float32)
            for payload, _ in self.demod.decode(audio):
                if self._on_frame:
                    self._on_frame(payload)

    def close(self) -> None:
        self._closed = True
        for s in (self.rx, self.tx):
            try:
                s.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Real radio via sound card
# ---------------------------------------------------------------------------


class AudioTransport(Transport):
    """Send and receive through a sound card wired to a radio.

    Wiring: sound-card output -> radio mic input, radio speaker -> sound-card
    input. Keying the transmitter is done by the radio's VOX, or by toggling
    RTS/DTR on a serial port (`ptt_port`) as most USB radio interfaces expect.
    """

    def __init__(
        self,
        input_device: str | int | None = None,
        output_device: str | int | None = None,
        sample_rate: int = 48000,
        ptt_port: str | None = None,
        ptt_line: str = "rts",
        busy_threshold: float = 0.0,
    ):
        super().__init__(ModemConfig(sample_rate=sample_rate))
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on host
            raise SystemExit(
                "Radio mode needs the 'sounddevice' package: pip install sounddevice"
            ) from e
        self.sd = sd
        self.input_device = input_device
        self.output_device = output_device
        self.demod = Demodulator(self.config)
        self.busy_threshold = busy_threshold
        self._transmitting = False
        self._stream = None
        self._ptt = None
        self._ptt_line = ptt_line
        if ptt_port:
            try:
                import serial  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise SystemExit("Serial PTT needs 'pyserial': pip install pyserial") from e
            self._ptt = serial.Serial(ptt_port)
            self._set_ptt(False)

    def _set_ptt(self, on: bool) -> None:
        if self._ptt is None:
            return
        if self._ptt_line == "dtr":
            self._ptt.dtr = on
        else:
            self._ptt.rts = on

    def start(self, on_frame: FrameHandler) -> None:  # pragma: no cover - hardware
        lock = threading.Lock()

        def callback(indata, frames, t, status):
            if self._transmitting:
                return  # don't decode our own transmission
            mono = indata[:, 0].copy()
            if self.busy_threshold and float(np.sqrt(np.mean(mono**2))) > self.busy_threshold:
                self._busy_until = time.monotonic() + 0.3
            with lock:
                for payload in self.demod.feed(mono):
                    on_frame(payload)

        self._stream = self.sd.InputStream(
            device=self.input_device, channels=1, samplerate=self.config.sample_rate,
            dtype="float32", blocksize=self.config.sample_rate // 20, callback=callback,
        )
        self._stream.start()

    def send(self, frame: bytes) -> None:  # pragma: no cover - hardware
        audio = self.mod.modulate(frame)
        self._transmitting = True
        try:
            self._set_ptt(True)
            time.sleep(0.05)
            self.sd.play(audio, self.config.sample_rate, device=self.output_device, blocking=True)
            time.sleep(0.05)
        finally:
            self._set_ptt(False)
            self._transmitting = False

    def close(self) -> None:  # pragma: no cover - hardware
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._ptt is not None:
            self._set_ptt(False)
            self._ptt.close()

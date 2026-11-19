"""G.O.R.B Small: license-free G.O.R.B over LoRa on ISM bands.

Regular G.O.R.B uses ham radio, which needs a license. G.O.R.B Small uses
cheap LoRa radio modules (an ESP32 board with an SX1262 / SX1276 chip, like
a Heltec WiFi LoRa 32 or a LilyGo T3/T-Beam) on bands anyone may use without
a license, as long as they stick to the power and airtime limits.

The ESP32 runs the firmware in firmware/gorb-small. It either plugs into
your computer over USB (the computer runs the browser or host and the ESP32
is its radio) or runs on its own from a battery as a relay.

The two networks never mix: G.O.R.B Small uses its own LoRa sync word, so
ham G.O.R.B and G.O.R.B Small stations can't hear each other.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass

from .transport import Transport

LORA_SYNC_WORD = 0x47  # 'G'; keeps G.O.R.B Small separate from LoRaWAN/Meshtastic
LORA_MAX_FRAME = 250  # LoRa takes 255, but 250 also fits ESP-NOW when a board bridges


@dataclass(frozen=True)
class Region:
    """A license-free band preset. Firmware has the same table (main.cpp)."""
    id: int
    name: str
    freq_mhz: float
    bw_khz: float
    sf: int
    cr: int  # coding rate 4/cr
    max_dbm: int  # legal limit (ERP/EIRP, before antenna gain)
    duty: float  # max fraction of time we may transmit
    note: str

    def airtime(self, nbytes: int, preamble: int = 8) -> float:
        """LoRa time on air in seconds (Semtech formula, explicit header, CRC on)."""
        tsym = (2 ** self.sf) / (self.bw_khz * 1000)
        de = 1 if tsym > 0.016 else 0
        num = 8 * nbytes - 4 * self.sf + 28 + 16
        payload_syms = 8 + max(math.ceil(num / (4 * (self.sf - 2 * de))) * self.cr, 0)
        return (preamble + 4.25 + payload_syms) * tsym

    @property
    def bitrate(self) -> float:
        return self.sf * (self.bw_khz * 1000) / (2 ** self.sf) * 4 / self.cr


REGIONS: dict[str, Region] = {r.name: r for r in [
    Region(1, "EU868", 869.525, 250, 8, 5, 27, 0.10,
           "Europe 869.4-869.65 MHz sub-band: 500 mW ERP, 10% duty cycle"),
    Region(2, "EU433", 433.875, 125, 8, 5, 10, 0.10,
           "Europe 433.05-434.79 MHz: 10 mW ERP, 10% duty cycle"),
    Region(3, "US915", 915.000, 500, 8, 5, 30, 0.50,
           "US/Canada 902-928 MHz (FCC 15.247 wideband): 1 W, max 6 dBi antenna"),
    Region(4, "AU915", 917.000, 500, 8, 5, 30, 0.50,
           "Australia/NZ 915-928 MHz: 1 W EIRP"),
    Region(5, "IN865", 866.000, 250, 8, 5, 30, 0.50,
           "India 865-867 MHz: 1 W ERP"),
]}

# --- serial link to the ESP32 (KISS framing) --------------------------------
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
# PC -> ESP32
CMD_TX = 0x00  # payload: gorb packet
CMD_CONFIG = 0x10  # region id, power dBm (int8), relay (0/1); saved on the board
CMD_PING = 0x11  # board answers with HELLO
CMD_NAMES = 0x12  # comma separated names the board must not relay (they're ours)
# ESP32 -> PC
EV_RX = 0x01  # rssi int16 BE, snr*4 int8, gorb packet
EV_TXDONE = 0x02  # status: 0 sent, 2 radio error, 3 no region set
EV_HELLO = 0x03  # JSON status text
EV_LOG = 0x04  # debug text


def kiss_encode(cmd: int, data: bytes = b"") -> bytes:
    out = bytearray([FEND, cmd])
    for b in data:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


class KissDecoder:
    def __init__(self):
        self._buf = bytearray()
        self._esc = False
        self._in_frame = False

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        frames = []
        for b in data:
            if b == FEND:
                if self._in_frame and self._buf:
                    frames.append((self._buf[0], bytes(self._buf[1:])))
                self._buf.clear()
                self._in_frame = True
                self._esc = False
            elif not self._in_frame:
                continue
            elif self._esc:
                self._buf.append(FEND if b == TFEND else FESC if b == TFESC else b)
                self._esc = False
            elif b == FESC:
                self._esc = True
            else:
                self._buf.append(b)
        return frames


class LoRaSerialTransport(Transport):
    """Use an ESP32 + LoRa board running the gorb-small firmware as the radio.

    The board does listen-before-talk (LoRa channel activity detection), the
    duty-cycle limit and relaying itself, so it keeps following the rules
    even if the computer misbehaves.
    """

    relays_in_hardware = True

    def __init__(self, port, region: str, power: int | None = None, relay: bool = True,
                 baud: int = 115200):
        if region not in REGIONS:
            raise SystemExit(f"unknown region {region!r}; pick one of: {', '.join(REGIONS)}")
        self.region = REGIONS[region]
        self.max_frame = LORA_MAX_FRAME
        self.max_duty = self.region.duty
        self._busy_until = 0.0
        self.power = self.region.max_dbm if power is None else min(power, self.region.max_dbm)
        self.relay = relay
        self.hello: dict = {}
        self.last_rssi: int | None = None
        self._dec = KissDecoder()
        self._txdone = threading.Event()
        self._tx_status = 0
        self._hello_evt = threading.Event()
        self._names: list[str] = []
        self._on_frame = None
        self._closed = False
        self._wlock = threading.Lock()
        if isinstance(port, str):
            try:
                import serial  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise SystemExit("G.O.R.B Small needs pyserial: pip install pyserial") from e
            self.ser = serial.Serial(port, baud, timeout=0.2)
        else:
            self.ser = port  # anything with read/write (tests use a fake board)

    def airtime(self, nbytes: int) -> float:
        return self.region.airtime(nbytes)

    def set_local_names(self, names: list[str]) -> None:
        self._names = names
        self._write(CMD_NAMES, ",".join(names).encode())

    def _write(self, cmd: int, data: bytes = b"") -> None:
        with self._wlock:
            self.ser.write(kiss_encode(cmd, data))
            if hasattr(self.ser, "flush"):
                self.ser.flush()

    def start(self, on_frame) -> None:
        self._on_frame = on_frame
        threading.Thread(target=self._rx_loop, daemon=True, name="lora-rx").start()
        self.configure()

    def configure(self, wait: float = 8.0) -> dict:
        """Send region/power/relay settings and wait for the board to answer."""
        cfg = bytes([self.region.id, self.power & 0xFF, 1 if self.relay else 0])
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:  # boards often reboot when the port opens
            self._hello_evt.clear()
            self._write(CMD_CONFIG, cfg)
            if self._names:
                self._write(CMD_NAMES, ",".join(self._names).encode())
            self._write(CMD_PING)
            if self._hello_evt.wait(1.0):
                return self.hello
        raise SystemExit("no answer from the ESP32 - is the gorb-small firmware flashed "
                         "and is this the right serial port?")

    def _rx_loop(self) -> None:
        while not self._closed:
            try:
                data = self.ser.read(256)
            except Exception:
                if self._closed:
                    return
                time.sleep(0.2)
                continue
            for cmd, body in self._dec.feed(data or b""):
                if cmd == EV_RX and len(body) > 3:
                    self.last_rssi = int.from_bytes(body[:2], "big", signed=True)
                    if self._on_frame:
                        self._on_frame(body[3:])
                elif cmd == EV_TXDONE:
                    self._tx_status = body[0] if body else 0
                    self._txdone.set()
                elif cmd == EV_HELLO:
                    try:
                        self.hello = json.loads(body.decode(errors="replace"))
                    except ValueError:
                        self.hello = {"raw": body.decode(errors="replace")}
                    self._hello_evt.set()

    def send(self, frame: bytes) -> None:
        if len(frame) > self.max_frame:
            raise ValueError(f"packet too big for LoRa ({len(frame)} > {self.max_frame})")
        self._txdone.clear()
        self._write(CMD_TX, frame)
        # The board may hold the packet while the channel is busy or the duty
        # cycle budget refills, so allow plenty of time.
        if not self._txdone.wait(30 + self.airtime(len(frame)) / max(self.max_duty, 0.01)):
            raise IOError("ESP32 never confirmed the transmission")
        if self._tx_status == 3:
            raise IOError("ESP32 has no region set; it refuses to transmit")
        if self._tx_status != 0:
            raise IOError(f"ESP32 radio error {self._tx_status}")

    def close(self) -> None:
        self._closed = True
        try:
            self.ser.close()
        except Exception:
            pass

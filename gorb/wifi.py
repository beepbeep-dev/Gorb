"""Gorb Wi-Fi: G.O.R.B over Wi-Fi radios, with no ISP and no internet.

Wi-Fi is radio too. The ISP only connects a network to the internet; devices
on the same Wi-Fi can talk to each other directly. Gorb Wi-Fi stations shout
their packets to everyone on the local network (UDP broadcast), exactly like
radio stations sharing a band, so all of G.O.R.B works unchanged: hosting,
browsing, Echo, the rules.

What counts as "the same Wi-Fi":
  * any router or mesh with its internet cable unplugged (or plugged in:
    G.O.R.B doesn't care, it never uses the internet),
  * a laptop's or phone's hotspot (no mobile data needed),
  * the hotspot of a G.O.R.B ESP32 board. The board bridges Gorb Wi-Fi to
    ESP32s nearby (ESP-NOW) and to G.O.R.B Small (LoRa) if it has a LoRa chip.

Compared with LoRa it is very fast (whole pictures and videos in about a
second) but short range: tens of metres indoors, kilometres with directional
antennas between rooftops.

Wire format: b"GWIF" + 8-byte station id + G.O.R.B packet, UDP port 47200.
"""

from __future__ import annotations

import os
import socket
import threading
import time

from .transport import Transport

WIFI_PORT = 47200
MAGIC = b"GWIF"
WIFI_MAX_FRAME = 1400  # stays under the Wi-Fi/Ethernet MTU so nothing is fragmented
WIFI_CHUNK = 1200


def local_broadcasts() -> list[str]:
    """Broadcast addresses worth sending to (best effort, no extra packages)."""
    out = ["255.255.255.255"]
    try:
        names = {socket.gethostname(), "localhost"}
        ips = set()
        for n in names:
            try:
                for info in socket.getaddrinfo(n, None, socket.AF_INET):
                    ips.add(info[4][0])
            except OSError:
                pass
        # The address used for the default route, if any (no packet is sent).
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.255.255", 9))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        for ip in ips:
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            a, b, c, _ = ip.split(".")
            out.append(f"{a}.{b}.{c}.255")  # assumes the usual /24 home network
    except Exception:
        pass
    return list(dict.fromkeys(out))


class WifiTransport(Transport):
    """G.O.R.B over a local Wi-Fi network (UDP broadcast)."""

    # Everyone on the same Wi-Fi hears everyone, so relaying would only send
    # each packet twice. (Boards relay between Wi-Fi, ESP-NOW and LoRa.)
    relay_useful = False
    max_frame = WIFI_MAX_FRAME
    chunk_size = WIFI_CHUNK
    max_duty = 1.0

    def __init__(self, port: int = WIFI_PORT, broadcast: str | list[str] | None = None):
        self._busy_until = 0.0
        self.port = port
        if broadcast is None:
            self.targets = local_broadcasts()
        elif isinstance(broadcast, str):
            self.targets = [broadcast]
        else:
            self.targets = list(broadcast)
        self.station_id = os.urandom(8)
        self._on_frame = None
        self._closed = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.sock.bind(("", port))
        self.sock.settimeout(0.5)
        self._seen_ids: dict[bytes, float] = {}

    def airtime(self, nbytes: int) -> float:
        return 0.002  # Wi-Fi: effectively instant at G.O.R.B sizes

    def start(self, on_frame) -> None:
        self._on_frame = on_frame
        threading.Thread(target=self._rx_loop, daemon=True, name="wifi-rx").start()

    def send(self, frame: bytes) -> None:
        if len(frame) > self.max_frame:
            raise ValueError(f"packet too big for Gorb Wi-Fi ({len(frame)} > {self.max_frame})")
        data = MAGIC + self.station_id + frame
        sent = False
        for dst in self.targets:
            try:
                self.sock.sendto(data, (dst, self.port))
                sent = True
            except OSError:
                continue  # e.g. no network on that address right now
        if not sent:
            raise OSError("not connected to any Wi-Fi network")
        time.sleep(0.001)  # don't flood slow Wi-Fi broadcast rates

    def _rx_loop(self) -> None:
        while not self._closed:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) < 12 or data[:4] != MAGIC or data[4:12] == self.station_id:
                continue  # not G.O.R.B, or our own broadcast coming back
            if self._on_frame:
                self._on_frame(data[12:])

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass

"""G.O.R.B Small tests, using a fake ESP32 that speaks the firmware's serial
protocol and shares a pretend LoRa band with other fake boards."""

import json
import math
import queue
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gorb.node import Node
from gorb.small import (CMD_CONFIG, CMD_NAMES, CMD_PING, CMD_TX, EV_HELLO, EV_RX, EV_TXDONE,
                        REGIONS, KissDecoder, LoRaSerialTransport, kiss_encode)


class FakeAir:
    def __init__(self, range_=10.0):
        self.boards = []
        self.range = range_

    def transmit(self, sender, frame):
        for b in self.boards:
            if b is not sender and math.dist(b.pos, sender.pos) <= self.range:
                b.hear(frame)


class FakeBoard:
    """Mimics firmware/gorb-small: dedup, relaying, CONFIG/PING/NAMES."""

    def __init__(self, air, pos):
        self.air, self.pos = air, pos
        air.boards.append(self)
        self.to_pc = queue.Queue()
        self.dec = KissDecoder()
        self.region = None
        self.relay = True
        self.names = set()
        self.seen = {}
        self.lock = threading.Lock()
        self.sent = 0

    # serial side -------------------------------------------------------
    def write(self, data):
        for cmd, body in self.dec.feed(data):
            if cmd == CMD_CONFIG:
                self.region, self.power, self.relay = body[0], body[1], bool(body[2])
            elif cmd == CMD_PING:
                hello = {"fw": "fake", "region": self.region}
                self.to_pc.put(kiss_encode(EV_HELLO, json.dumps(hello).encode()))
            elif cmd == CMD_NAMES:
                self.names = set(body.decode().split(","))
            elif cmd == CMD_TX:
                if not self.region:
                    self.to_pc.put(kiss_encode(EV_TXDONE, b"\x03"))
                    continue
                self._mark(body)
                self._tx(body)
                self.to_pc.put(kiss_encode(EV_TXDONE, b"\x00"))

    def read(self, n):
        try:
            return self.to_pc.get(timeout=0.2)
        except queue.Empty:
            return b""

    def close(self):
        pass

    # radio side --------------------------------------------------------
    @staticmethod
    def _key(f):
        return f[:4] + f[6:]

    def _mark(self, f):
        with self.lock:
            k = self._key(f)
            self.seen[k] = self.seen.get(k, 0) + 1
            return self.seen[k]

    def _tx(self, f):
        self.sent += 1
        self.air.transmit(self, f)

    def hear(self, f):
        if self._mark(f) > 1:
            return
        self.to_pc.put(kiss_encode(EV_RX, b"\xff\xb0\x20" + f))
        i = 15
        src = f[i + 1:i + 1 + f[i]].decode()
        i += 1 + f[i]
        dst = f[i + 1:i + 1 + f[i]].decode()
        if self.relay and f[4] > 0 and src not in self.names and dst not in self.names:
            relayed = f[:4] + bytes([f[4] - 1, f[5] + 1]) + f[6:]
            threading.Timer(random.uniform(0.01, 0.05), self._tx, [relayed]).start()


class KissTest(unittest.TestCase):
    def test_roundtrip_with_escapes(self):
        data = bytes([0xC0, 0xDB, 1, 2, 0xC0])
        dec = KissDecoder()
        wire = kiss_encode(5, data) + kiss_encode(6)
        self.assertEqual(dec.feed(wire[:3]) + dec.feed(wire[3:]), [(5, data), (6, b"")])

    def test_airtime(self):
        eu = REGIONS["EU868"]
        self.assertAlmostEqual(eu.airtime(235), 0.328, places=2)
        self.assertLess(REGIONS["US915"].airtime(235), eu.airtime(235))


class SmallNetworkTest(unittest.TestCase):
    def test_fetch_through_standalone_relay(self):
        air = FakeAir()
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<h1>tiny radio</h1>" + "z" * 4000)
            host_board, relay_board, my_board = (FakeBoard(air, (0, 0)), FakeBoard(air, (8, 0)),
                                                 FakeBoard(air, (16, 0)))
            # The middle board has no computer at all: it was configured once
            # and now relays on its own.
            relay_board.region, relay_board.relay = 1, True

            host = Node(LoRaSerialTransport(host_board, "EU868"), "Host",
                        {"tiny.gorb": d}, beacon_interval=0).start()
            me = Node(LoRaSerialTransport(my_board, "EU868"), "ME-1", beacon_interval=0).start()
            try:
                self.assertFalse(me.relay)  # the board does it instead
                page = me.fetch("gorb://tiny.gorb", timeout=30)
                self.assertEqual(page.status, 200)
                self.assertTrue(page.text.startswith("<h1>tiny radio</h1>"))
                self.assertGreaterEqual(page.hops, 1)
                self.assertGreater(relay_board.sent, 0)
                self.assertIn("tiny.gorb", host_board.names)
                # nothing the host sends can exceed one LoRa frame
                self.assertEqual(host.transport.max_frame, 250)
            finally:
                host.stop()
                me.stop()

    def test_unconfigured_board_refuses(self):
        board = FakeBoard(FakeAir(), (0, 0))
        t = LoRaSerialTransport(board, "US915")
        t._on_frame = None
        threading.Thread(target=t._rx_loop, daemon=True).start()
        with self.assertRaises(IOError):
            t.send(b"GB" + bytes(20))
        t.close()


if __name__ == "__main__":
    unittest.main()

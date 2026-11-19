"""Gorb Wi-Fi tests: stations on the same (here: local) network, no internet."""

import tempfile
import time
import unittest
from pathlib import Path

from gorb.node import Node
from gorb.wifi import WIFI_PORT, WifiTransport


class WifiTest(unittest.TestCase):
    _port = WIFI_PORT + 100

    def setUp(self):
        WifiTest._port += 1
        self.dir = tempfile.TemporaryDirectory()
        (Path(self.dir.name) / "index.html").write_text("<h1>wifi</h1>" + "lorem ipsum " * 4000)
        self.nodes = []

    def tearDown(self):
        for n in self.nodes:
            n.stop()
        self.dir.cleanup()

    def node(self, name, **kw):
        t = WifiTransport(port=self._port, broadcast="127.255.255.255")
        n = Node(t, name, beacon_interval=0, **kw).start()
        self.nodes.append(n)
        return n

    def test_fetch_big_page_fast_with_big_chunks(self):
        self.node("HOST-W", site_dirs={"wifi.gorb": self.dir.name})
        me = self.node("ME-W")
        self.assertFalse(me.relay)  # same Wi-Fi: everyone already hears everyone
        t0 = time.monotonic()
        page = me.fetch("gorb://wifi.gorb/", timeout=20)
        self.assertEqual(page.status, 200)
        self.assertTrue(page.text.startswith("<h1>wifi</h1>"))
        self.assertLess(time.monotonic() - t0, 5)
        self.assertGreater(me._chunk_size("wifi.gorb", "ME-W"), 1000)

    def test_directory_and_echo(self):
        host = self.node("HOST-W", site_dirs={"wifi.gorb": self.dir.name})
        a = self.node("ALICE-W")
        b = self.node("BOB-W")
        host.beacon()
        deadline = time.time() + 5
        while "wifi.gorb" not in b.known_domains() and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("wifi.gorb", b.known_domains())
        a.fetch("gorb://wifi.gorb/", timeout=20)
        time.sleep(0.2)
        self.assertTrue(b.fetch("gorb://wifi.gorb/", timeout=20).from_echo)
        self.assertEqual(host.stats["served"], 1)

    def test_ignores_own_and_foreign_packets(self):
        t = WifiTransport(port=self._port, broadcast="127.255.255.255")
        got = []
        t.start(got.append)
        t.send(b"GB" + bytes(20))  # our own broadcast comes back and must be ignored
        t.sock.sendto(b"not gorb at all", ("127.255.255.255", self._port))
        time.sleep(0.3)
        t.close()
        self.assertEqual(got, [])

    def test_narrow_requests_get_small_pieces(self):
        """A request from behind LoRa/ESP-NOW (bridged by a board) gets replies
        that fit those radios, even from a Wi-Fi host."""
        from gorb.packet import Packet, PacketType
        host = self.node("HOST-W", site_dirs={"wifi.gorb": self.dir.name})
        self.assertGreater(host._chunk_size("wifi.gorb", "FAR-AWAY"), 1000)
        self.assertLessEqual(host._chunk_size("wifi.gorb", "FAR-AWAY", narrow=True), 180)
        p = Packet(PacketType.GET, "FAR-AWAY", "wifi.gorb", b"/", 7, hops=3, narrow=True)
        q = Packet.decode(p.encode())
        self.assertTrue(q.narrow)
        self.assertEqual(q.hops, 3)
        self.assertTrue(q.relayed().narrow)
        self.assertEqual(q.relayed().hops, 4)


if __name__ == "__main__":
    unittest.main()

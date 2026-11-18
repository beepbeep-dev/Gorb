import threading
import time
import unittest
import zlib

import numpy as np

from gorb import rules
from gorb.browser import rewrite_links
from gorb.modem import Demodulator, ModemConfig, Modulator
from gorb.node import GorbError, Node, parse_url
from gorb.packet import Packet, PacketType
from gorb.transport import SimTransport


class ModemTest(unittest.TestCase):
    def test_roundtrip_with_noise(self):
        cfg = ModemConfig(sample_rate=9600)
        audio = np.concatenate([
            np.zeros(777), Modulator(cfg).modulate(b"first frame" * 10),
            np.zeros(100), Modulator(cfg).modulate(b"second"),
        ])
        audio = audio + np.random.default_rng(1).normal(0, 0.2, len(audio))
        self.assertEqual([p for p, _ in Demodulator(cfg).decode(audio)],
                         [b"first frame" * 10, b"second"])

    def test_streaming_48k(self):
        cfg = ModemConfig()
        data = bytes(range(256)) * 2
        audio = np.concatenate([np.zeros(12345), Modulator(cfg).modulate(data), np.zeros(24000)])
        d = Demodulator(cfg)
        out = []
        for i in range(0, len(audio), 2400):
            out += d.feed(audio[i:i + 2400])
        out += d.feed(np.zeros(24000))
        self.assertEqual(out, [data])

    def test_corrupted_frame_rejected(self):
        cfg = ModemConfig(sample_rate=9600)
        audio = Modulator(cfg).modulate(b"x" * 50)
        audio[len(audio) // 2: len(audio) // 2 + 200] = 0.9  # burst of interference
        self.assertEqual(Demodulator(cfg).decode(audio), [])


class PacketTest(unittest.TestCase):
    def test_roundtrip(self):
        p = Packet(PacketType.DATA, "cats.gorb", "N0CALL", b"hi", 42, 3, 9, ttl=2, hops=1, round=4)
        self.assertEqual(Packet.decode(p.encode()), p)
        self.assertEqual(p.relayed().ttl, 1)
        self.assertEqual(p.relayed().key, p.key)


class RulesTest(unittest.TestCase):
    def test_filter(self):
        self.assertIsNotNone(rules.check_content("<h1>NSFW stuff</h1>"))
        self.assertIsNotNone(rules.check_content("adults only club"))
        self.assertIsNone(rules.check_content("<h1>My cat blog</h1> gorgeous cats"))

    def test_url(self):
        self.assertEqual(parse_url("gorb://Cats.gorb/a/b.html"), ("cats.gorb", "/a/b.html"))
        self.assertEqual(parse_url("cats"), ("cats.gorb", "/"))
        with self.assertRaises(GorbError):
            parse_url("gorb://bad domain")

    def test_rewrite(self):
        out = rewrite_links('<a href="about.html">x</a><a href="gorb://dogs.gorb/">d</a>'
                            '<script>alert(1)</script><a href="https://example.com">w</a>',
                            "gorb://cats.gorb/index.html")
        self.assertIn('/go?url=gorb%3A%2F%2Fcats.gorb%2Fabout.html', out)
        self.assertIn('/go?url=gorb%3A%2F%2Fdogs.gorb%2F', out)
        self.assertIn('https://example.com', out)
        self.assertNotIn("alert", out)


class NetworkTest(unittest.TestCase):
    """Three simulated stations in a line: the browser can only reach the
    host through the relay."""

    _channel = 40

    def setUp(self):
        import tempfile, pathlib
        self.dir = tempfile.TemporaryDirectory()
        site = pathlib.Path(self.dir.name)
        rng = np.random.default_rng(0)
        # Big, poorly-compressible page so it needs many chunks.
        filler = "".join(chr(97 + int(c)) for c in rng.integers(0, 26, 3000))
        (site / "index.html").write_text(f"<h1>hello radio</h1><p>{filler}</p>")
        (site / "bad.html").write_text("<h1>NSFW</h1>")
        NetworkTest._channel += 1
        ch = NetworkTest._channel
        self.host = Node(SimTransport(ch, 0, 0, 10, loss=0.1), "HOST-1",
                         {"test.gorb": site}, beacon_interval=0).start()
        self.relay = Node(SimTransport(ch, 8, 0, 10, loss=0.1), "RELAY-1",
                          beacon_interval=0).start()
        self.me = Node(SimTransport(ch, 16, 0, 10, loss=0.1), "ME-1", beacon_interval=0).start()

    def tearDown(self):
        for n in (self.host, self.relay, self.me):
            n.stop()
        self.dir.cleanup()

    def test_fetch_through_relay_with_loss(self):
        page = self.me.fetch("gorb://test.gorb/", timeout=60)
        self.assertEqual(page.status, 200)
        self.assertTrue(page.text.startswith("<h1>hello radio</h1>"))
        self.assertGreaterEqual(page.hops, 1)
        self.assertGreater(self.relay.stats["relayed"], 0)

    def test_missing_and_blocked(self):
        self.assertEqual(self.me.fetch("gorb://test.gorb/nope.html", timeout=60).status, 404)
        self.assertEqual(self.me.fetch("gorb://test.gorb/bad.html", timeout=60).status, 451)

    def test_beacon_directory(self):
        self.host.beacon()
        deadline = time.time() + 10
        while "test.gorb" not in self.me.known_domains() and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(self.me.known_domains()["test.gorb"].callsign, "HOST-1")


if __name__ == "__main__":
    unittest.main()

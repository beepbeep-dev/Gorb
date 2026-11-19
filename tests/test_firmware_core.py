"""Checks the ESP32 firmware's protocol code (gorb_core.h) against the Python
implementation, by compiling it for this computer. Skipped without g++."""

import os
import random
import shutil
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from gorb import rules
from gorb.packet import Packet, PacketType

ROOT = Path(__file__).resolve().parent.parent / "firmware" / "gorb-small"


@unittest.skipUnless(shutil.which("g++"), "g++ not installed")
class FirmwareCoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = os.path.join(cls.tmp.name, "host_test")
        subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", "-o", cls.exe,
                        str(ROOT / "test" / "host_test.cpp")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_exe(self, *args, data=b""):
        r = subprocess.run([self.exe, *args], input=data, capture_output=True)
        return r.returncode, r.stdout

    def test_inflate_matches_python_zlib(self):
        rng = random.Random(3)
        words = [b"radio", b"gorb", b"<p>", b"</p>", b"station", b"\n", b"wave"]
        samples = [b"", b"x", bytes(rng.randrange(256) for _ in range(5000))]
        samples += [b" ".join(rng.choice(words) for _ in range(n)) for n in (10, 500, 9000)]
        for data in samples:
            for level in (0, 1, 6, 9):
                code, out = self.run_exe("inflate", data=zlib.compress(data, level))
                self.assertEqual(code, 0)
                self.assertEqual(out, data)

    def test_inflate_rejects_corruption(self):
        blob = bytearray(zlib.compress(b"hello radio world" * 50, 9))
        blob[len(blob) // 2] ^= 0x55
        self.assertNotEqual(self.run_exe("inflate", data=bytes(blob))[0], 0)

    def test_store_is_valid_zlib(self):
        for n in (0, 10, 70000):
            data = bytes(random.Random(n).randrange(256) for _ in range(n))
            code, out = self.run_exe("store", data=data)
            self.assertEqual(zlib.decompress(out), data)

    def test_packet_roundtrip(self):
        p = Packet(PacketType.DATA, "cats.gorb", "ME-1", b"\x00\xc0hi", 0xDEADBEEF, 7, 9,
                   ttl=2, hops=1, round=3)
        code, out = self.run_exe("packet", data=p.encode())
        self.assertEqual(Packet.decode(out), p)

    def test_filter_matches_python(self):
        for text in ["My cat blog", "NSFW pics", "for adults only!", "gorgeous", "18+ club",
                     "a118+b", "gore", "Gory", "nothing here"]:
            want = rules.check_content(text)
            got = self.run_exe("filter", data=text.encode())[1].decode()
            self.assertEqual(got != "ok", want is not None, text)

    def test_rewrite_and_url(self):
        html = b'<a href="/rules.html">r</a> <a href="gorb://dogs.gorb/x">d</a> <a href="a.html">a</a>'
        out = self.run_exe("rewrite", "cats.gorb", data=html)[1]
        self.assertIn(b'href="/g/cats.gorb/rules.html"', out)
        self.assertIn(b'href="/g/dogs.gorb/x"', out)
        self.assertIn(b'href="a.html"', out)
        self.assertEqual(self.run_exe("url", data=b"gorb://Cats.gorb/a.html")[1], b"cats.gorb /a.html")
        self.assertEqual(self.run_exe("url", data=b"cats")[1], b"cats.gorb /")
        self.assertEqual(self.run_exe("url", data=b"bad domain")[1], b"bad")

    def test_media_on_board(self):
        page = (b'<p>hi</p><IMG src="a.webp"><img alt=x src="/pics/b.jpg">'
                b'<video controls width="300"><source src="../clips/c.mp4"></video>'
                b'<img src="https://example.com/x.png"><img src="gorb://dogs.gorb/d.png">')
        out = self.run_exe("media", "cats.gorb", "/blog/post.html", data=page)[1].decode().split("\n")
        self.assertNotIn("<video", out[0])
        self.assertIn('<img src="../clips/c.mp4"', out[0])
        self.assertEqual(out[1:6], ["gorb://cats.gorb/blog/a.webp", "gorb://cats.gorb/pics/b.jpg",
                                    "gorb://cats.gorb/clips/c.mp4", "", "gorb://dogs.gorb/d.png"])


if __name__ == "__main__":
    unittest.main()

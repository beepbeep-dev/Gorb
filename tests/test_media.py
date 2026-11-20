"""Gorb Pixels 2, Gorb Motion 2 and Gorb Echo tests."""

import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gorb import media
from gorb.browser import rewrite_links
from gorb.node import Node
from gorb.transport import SimTransport

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "site" / "media"
HAVE_VIDEO = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(data: bytes, suffix: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name:format=duration", "-of", "json", f.name],
                           capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@unittest.skipUnless(Image, "Pillow not installed")
class PixelsTest(unittest.TestCase):
    def test_photo_fits_budget(self):
        data = (EXAMPLES / "tower.jpg").read_bytes()
        for budget in (2000, 6000, 24000):
            ctype, out = media.encode_image(data, budget)
            self.assertLessEqual(len(out), budget)
            self.assertIn(ctype, ("image/avif", "image/webp"))
            Image.open(io.BytesIO(out)).load()

    def test_more_bytes_look_better(self):
        data = (EXAMPLES / "tower.jpg").read_bytes()
        ref = Image.open(io.BytesIO(data)).convert("RGB")
        small = Image.open(io.BytesIO(media.encode_image(data, 2000)[1])).convert("RGB")
        big = Image.open(io.BytesIO(media.encode_image(data, 24000, max_width=1280)[1])).convert("RGB")
        self.assertGreater(media.ssim(ref, big), media.ssim(ref, small))

    def test_ssim(self):
        im = Image.open(EXAMPLES / "tower.jpg").convert("RGB")
        self.assertAlmostEqual(media.ssim(im, im), 1.0, places=5)
        self.assertLess(media.ssim(im, im.rotate(180)), 0.6)

    def test_preview_is_tiny(self):
        out = media.encode_preview((EXAMPLES / "tower.jpg").read_bytes())
        self.assertLess(len(out), 600)
        self.assertEqual(Image.open(io.BytesIO(out)).width, media.PREVIEW_WIDTH)

    def test_bad_picture(self):
        with self.assertRaises(media.MediaError):
            media.encode_image(b"not a picture")

    def test_animated_gif(self):
        frames = [Image.new("RGB", (120, 80), (i * 20, 100, 255 - i * 20)) for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            gif = Path(d) / "a.gif"
            frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
            out = media.encode_animation(str(gif), 8000)
        self.assertLessEqual(len(out), 8000)
        self.assertGreater(Image.open(io.BytesIO(out)).n_frames, 1)


@unittest.skipUnless(Image and HAVE_VIDEO, "needs Pillow and ffmpeg")
class MotionTest(unittest.TestCase):
    def test_av1_with_sound(self):
        ctype, out = media.encode_video(str(EXAMPLES / "waves.mp4"), "av1", 20000)
        self.assertEqual(ctype, "video/webm")
        self.assertLessEqual(len(out), 20000)
        streams = {s["codec_type"]: s["codec_name"] for s in probe(out, ".webm")["streams"]}
        self.assertEqual(streams.get("video"), "av1")

    def test_h264_and_lite(self):
        ctype, out = media.encode_video(str(EXAMPLES / "waves.mp4"), "h264", 30000)
        self.assertEqual(ctype, "video/mp4")
        self.assertLessEqual(len(out), 30000)
        self.assertEqual(probe(out, ".mp4")["streams"][0]["codec_name"], "h264")

    def test_long_videos_are_cut_not_mushed(self):
        plan = media.video_plan(40000, 600, True, "av1")
        self.assertLess(plan["seconds"], 30)
        self.assertGreaterEqual(plan["vkbps"], media.MIN_VIDEO_KBPS - 0.01)
        short = media.video_plan(40000, 5, True, "av1")
        self.assertEqual(short["seconds"], 5)
        self.assertGreater(short["width"], plan["width"])

    def test_poster(self):
        ctype, poster = media.video_preview(str(EXAMPLES / "waves.mp4"))
        self.assertTrue(ctype.startswith("image/"))
        self.assertLessEqual(len(poster), media.POSTER_BUDGET)


class RewriteTest(unittest.TestCase):
    def test_picture_layers(self):
        out = rewrite_links('<img src="pic.jpg" style="border:0" alt="x">',
                            "gorb://cats.gorb/blog/index.html")
        u = "/media?url=gorb%3A%2F%2Fcats.gorb%2Fblog%2Fpic.jpg"
        self.assertIn(f'src="{u}&amp;v=hd"', out)
        # standard picture stacked above the preview, behind the HD one
        self.assertLess(out.index(f"{u}&amp;v=standard"), out.index(f"{u}&amp;v=preview"))
        self.assertIn("border:0", out)

    def test_video_becomes_player_with_fallback(self):
        out = rewrite_links('<video width="300"><source src="clip.mp4"></video><a href="o.html">o</a>',
                            "gorb://cats.gorb/")
        self.assertIn("<video controls", out)
        self.assertIn("v=av1", out)
        self.assertIn("v=h264", out)
        self.assertLess(out.index("v=av1"), out.index("v=h264"))
        self.assertIn("v=preview", out)  # poster
        self.assertIn('width="300"', out)
        self.assertIn("/go?url=gorb%3A%2F%2Fcats.gorb%2Fo.html", out)


@unittest.skipUnless(Image, "Pillow not installed")
class HostMediaTest(unittest.TestCase):
    def test_host_sends_each_layer(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(EXAMPLES / "tower.jpg", d)
            host = Node(SimTransport(90, 0, 0, 10), "HOST-9", {"pics.gorb": d}, beacon_interval=0,
                        image_budget=4000, hd_budget=12000).start()
            me = Node(SimTransport(90, 5, 0, 10), "ME-9", beacon_interval=0, echo=False).start()
            try:
                std = me.fetch("gorb://pics.gorb/tower.jpg", timeout=60)
                hd = me.fetch("gorb://pics.gorb/tower.jpg?gorb=hd", timeout=60)
                prev = me.fetch("gorb://pics.gorb/tower.jpg?gorb=preview", timeout=60)
            finally:
                host.stop()
                me.stop()
        self.assertEqual(std.status, 200)
        self.assertTrue(std.content_type.startswith("image/"))
        self.assertLessEqual(len(std.body), 4000)
        self.assertLessEqual(len(hd.body), 12000)
        self.assertGreater(len(hd.body), len(std.body))
        self.assertLess(len(prev.body), 600)

    def test_small_replies_go_first(self):
        """A preview asked for after a big picture still arrives first."""
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(EXAMPLES / "tower.jpg", d)
            host = Node(SimTransport(91, 0, 0, 10, realtime=True), "HOST-8",
                        {"pics.gorb": d}, beacon_interval=0, image_budget=3000).start()
            host.media.get(Path(d) / "tower.jpg")  # encode ahead so timing is about radio only
            host.media.get(Path(d) / "tower.jpg", "preview")
            me = Node(SimTransport(91, 5, 0, 10, realtime=True), "ME-8", beacon_interval=0).start()
            done = {}

            def get(name, url):
                me.fetch(url, timeout=120)
                done[name] = time.monotonic()

            try:
                t1 = threading.Thread(target=get, args=("full", "gorb://pics.gorb/tower.jpg"))
                t1.start()
                time.sleep(0.3)
                t2 = threading.Thread(target=get,
                                      args=("preview", "gorb://pics.gorb/tower.jpg?gorb=preview"))
                t2.start()
                t1.join()
                t2.join()
            finally:
                host.stop()
                me.stop()
        self.assertLess(done["preview"], done["full"])


class EchoTest(unittest.TestCase):
    """Gorb Echo: replies overheard on the air are kept and reused."""

    _ch = 120

    def setUp(self):
        EchoTest._ch += 1
        self.dir = tempfile.TemporaryDirectory()
        (Path(self.dir.name) / "index.html").write_text("<h1>news</h1>" + "x" * 3000)
        self.nodes = []

    def tearDown(self):
        for n in self.nodes:
            n.stop()
        self.dir.cleanup()

    def node(self, name, x, **kw):
        n = Node(SimTransport(self._ch, x, 0, 10), name, beacon_interval=0, **kw).start()
        self.nodes.append(n)
        return n

    def test_neighbour_gets_overheard_page_instantly(self):
        host = self.node("HOST-1", 0, site_dirs={"news.gorb": self.dir.name})
        alice = self.node("ALICE", 5)
        bob = self.node("BOB-1", 5)
        alice.fetch("gorb://news.gorb/", timeout=30)
        time.sleep(0.3)
        tx_before = host.stats["tx"] + bob.stats["tx"]
        page = bob.fetch("gorb://news.gorb/", timeout=30)
        self.assertTrue(page.from_echo)
        self.assertTrue(page.text.startswith("<h1>news</h1>"))
        self.assertEqual(host.stats["served"], 1)
        self.assertEqual(host.stats["tx"] + bob.stats["tx"], tx_before)  # no airtime at all

    def test_relay_answers_from_echo(self):
        host = self.node("HOST-1", 0, site_dirs={"news.gorb": self.dir.name})
        relay = self.node("RELAY-1", 8)
        alice = self.node("ALICE", 16)
        alice.fetch("gorb://news.gorb/", timeout=30)
        time.sleep(0.5)
        carol = self.node("CAROL", 16)  # arrives later, heard nothing
        page = carol.fetch("gorb://news.gorb/", timeout=30)
        self.assertTrue(page.text.startswith("<h1>news</h1>"))
        self.assertFalse(page.from_echo)
        self.assertEqual(host.stats["served"], 1)  # the host was never asked again
        self.assertEqual(relay.stats["echo_served"], 1)

    def test_crowd_loading_same_page(self):
        """Five people ask for the same page at once over a slow radio. Before
        the lab's fixes most of them failed and the host queued hundreds of
        duplicate packets; now they all finish from one overheard copy."""
        import random
        import threading
        rnd = random.Random(1)
        text = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4000))
        (Path(self.dir.name) / "index.html").write_text("<h1>news</h1>" + text)
        host = Node(SimTransport(self._ch, 0, 0, 10, realtime=True, speed=4), "HOST-1",
                    {"news.gorb": self.dir.name}, beacon_interval=0).start()
        people = [Node(SimTransport(self._ch, 3, i, 10, realtime=True, speed=4), f"USER-{i}",
                       beacon_interval=0).start() for i in range(5)]
        self.nodes += [host, *people]
        got = {}

        def go(n):
            try:
                got[n.callsign] = n.fetch("gorb://news.gorb/", timeout=120).text[:13]
            except Exception as e:  # noqa: BLE001
                got[n.callsign] = str(e)

        threads = [threading.Thread(target=go, args=(n,)) for n in people]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(got.values()), ["<h1>news</h1>"] * 5)
        # The others' copies get cancelled ("I have it") instead of sent.
        deadline = time.time() + 15
        while host._txq.qsize() and time.time() < deadline:
            time.sleep(0.5)
        self.assertEqual(host._txq.qsize(), 0)
        self.assertLess(host.stats["tx"], 2 * 22)  # about one copy went out, not five

    def test_echo_can_be_switched_off(self):
        host = self.node("HOST-1", 0, site_dirs={"news.gorb": self.dir.name})
        alice = self.node("ALICE", 5)
        bob = self.node("BOB-1", 5, echo=False)
        alice.fetch("gorb://news.gorb/", timeout=30)
        page = bob.fetch("gorb://news.gorb/", timeout=30)
        self.assertFalse(page.from_echo)
        self.assertEqual(host.stats["served"], 2)


if __name__ == "__main__":
    unittest.main()

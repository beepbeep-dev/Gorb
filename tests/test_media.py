"""Gorb Pixels / Gorb Motion tests."""

import io
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from gorb import media
from gorb.browser import rewrite_links
from gorb.node import Node
from gorb.transport import SimTransport

try:
    import PIL  # noqa: F401
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "site" / "media"


@unittest.skipUnless(Image, "Pillow not installed")
class EncodeTest(unittest.TestCase):
    def test_photo_fits_budget(self):
        data = (EXAMPLES / "tower.jpg").read_bytes()
        for budget in (1500, 6000, 20000):
            out = media.encode_image(data, budget)
            self.assertLessEqual(len(out), budget)
            self.assertEqual(Image.open(io.BytesIO(out)).format, "WEBP")
        big = Image.open(io.BytesIO(media.encode_image(data, 20000)))
        small = Image.open(io.BytesIO(media.encode_image(data, 1500)))
        self.assertGreaterEqual(big.width, small.width)

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
            out = media.encode_motion(str(gif), 8000)
        clip = Image.open(io.BytesIO(out))
        self.assertLessEqual(len(out), 8000)
        self.assertGreater(clip.n_frames, 1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_video(self):
        out = media.encode_motion(str(EXAMPLES / "waves.mp4"), 15000)
        clip = Image.open(io.BytesIO(out))
        self.assertLessEqual(len(out), 15000)
        self.assertGreater(clip.n_frames, 4)
        self.assertLess(len(media.motion_preview(str(EXAMPLES / "waves.mp4"))), 600)


class RewriteTest(unittest.TestCase):
    def test_pictures_and_video(self):
        out = rewrite_links('<img src="pic.jpg" style="border:0" alt="x">'
                            '<video controls width="300"><source src="clip.mp4"></video>'
                            '<a href="other.html">o</a>', "gorb://cats.gorb/blog/index.html")
        self.assertIn('src="/media?url=gorb%3A%2F%2Fcats.gorb%2Fblog%2Fpic.jpg"', out)
        self.assertIn("v=preview", out)  # blurry stand-in behind the picture
        self.assertIn("border:0", out)
        self.assertNotIn("<video", out)
        self.assertIn("clip.mp4", out)
        self.assertIn('/go?url=gorb%3A%2F%2Fcats.gorb%2Fblog%2Fother.html', out)


@unittest.skipUnless(Image, "Pillow not installed")
class HostMediaTest(unittest.TestCase):
    def test_host_sends_shrunk_picture_and_preview(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(EXAMPLES / "tower.jpg", d)
            host = Node(SimTransport(90, 0, 0, 10), "HOST-9", {"pics.gorb": d},
                        beacon_interval=0, image_budget=4000).start()
            me = Node(SimTransport(90, 5, 0, 10), "ME-9", beacon_interval=0).start()
            try:
                full = me.fetch("gorb://pics.gorb/tower.jpg", timeout=30)
                prev = me.fetch("gorb://pics.gorb/tower.jpg?gorb=preview", timeout=30)
            finally:
                host.stop()
                me.stop()
        self.assertEqual((full.status, full.content_type), (200, "image/webp"))
        self.assertLessEqual(len(full.body), 4000)
        self.assertLess(len(prev.body), 600)
        self.assertEqual(Image.open(io.BytesIO(full.body)).format, "WEBP")

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
            import threading

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


if __name__ == "__main__":
    unittest.main()

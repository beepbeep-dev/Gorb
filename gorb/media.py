"""Gorb Pixels and Gorb Motion: pictures and video small enough for radio.

Radio is slow (G.O.R.B Small: ~1 KB/s, ham G.O.R.B: ~0.1 KB/s), so a normal
2 MB photo would take half an hour. Instead of sending the original file, a
host squeezes every picture and video to fit a byte budget before it goes on
the air:

Gorb Pixels (images)
    * full:    WebP, shrunk until it fits the budget (default 6 KB). The
               encoder searches for the best quality and size that fit, so
               small pictures stay sharp and big ones get smaller instead of
               turning into mush.
    * preview: a ~300 byte, 32 px wide version. The browser shows it blurred
               and stretched straight away, then the full picture replaces
               it when it arrives. You see something after one radio packet.

Gorb Motion (video and animated GIFs)
    * a short silent animated WebP clip: a few frames per second, small,
      looping, and also shrunk to a budget (default 24 KB). It plays in any
      browser as an image, and fits on an ESP32 board's browser too.
    * preview: the first frame as a Gorb Pixels preview.

Why not base64? Base64 turns 3 bytes into 4, so it makes files a third
*bigger*. G.O.R.B already sends raw bytes, so the savings have to come from
encoding the picture smarter, not from encoding the bytes differently.

Encoding needs Pillow (pip install pillow); video also needs ffmpeg.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
}

DEFAULT_IMAGE_BUDGET = 6000
DEFAULT_MOTION_BUDGET = 24000
PREVIEW_WIDTH = 32


class MediaError(Exception):
    pass


def is_media(path: str | os.PathLike) -> bool:
    ext = Path(path).suffix.lower()
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise MediaError("pictures need Pillow: pip install pillow") from e
    return Image, ImageOps


def _prepare(img):
    Image, ImageOps = _pil()
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    return img


def _resize(img, width: int):
    Image, _ = _pil()
    if img.width <= width:
        return img
    h = max(1, round(img.height * width / img.width))
    return img.resize((width, h), Image.LANCZOS)


def _webp(img, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def _fit(encode, budget: int, lo: int = 5, hi: int = 85) -> bytes | None:
    """Highest quality in [lo, hi] whose output fits the budget."""
    best = None
    while lo <= hi:
        q = (lo + hi) // 2
        data = encode(q)
        if len(data) <= budget:
            best, lo = data, q + 1
        else:
            hi = q - 1
    return best


def encode_image(data: bytes, budget: int = DEFAULT_IMAGE_BUDGET, max_width: int = 640) -> bytes:
    """Squeeze a picture into a WebP of at most `budget` bytes."""
    Image, _ = _pil()
    try:
        src = _prepare(Image.open(io.BytesIO(data)))
    except Exception as e:
        raise MediaError(f"can't read picture: {e}") from e
    width = min(max_width, src.width)
    while width >= 16:
        img = _resize(src, width)
        # Stay above quality ~30 at this size before shrinking further:
        # a smaller sharp picture looks better than a big blurry one.
        out = _fit(lambda q: _webp(img, q), budget, lo=30 if width > 96 else 5)
        if out:
            return out
        width = int(width * 0.75)
    raise MediaError(f"picture can't fit in {budget} bytes")


def encode_preview(data: bytes) -> bytes:
    """Tiny blurry stand-in shown while the real picture is on its way."""
    Image, _ = _pil()
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)
        img = _resize(_prepare(img), PREVIEW_WIDTH)
    except Exception as e:
        raise MediaError(f"can't read picture: {e}") from e
    return _webp(img, 40)


# ---------------------------------------------------------------- motion
def _frames_from_gif(data: bytes, fps: float, seconds: float):
    Image, _ = _pil()
    im = Image.open(io.BytesIO(data))
    frames, t, next_t = [], 0.0, 0.0
    try:
        while t < seconds:
            dur = im.info.get("duration", 100) / 1000 or 0.1
            if t >= next_t:
                frames.append(_prepare(im.copy().convert("RGBA")).convert("RGB"))
                next_t += 1 / fps
            t += dur
            im.seek(im.tell() + 1)
    except EOFError:
        pass
    return frames


def _frames_from_video(path: str, fps: float, seconds: float, width: int):
    Image, _ = _pil()
    if not have_ffmpeg():
        raise MediaError("video needs ffmpeg installed")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["ffmpeg", "-v", "error", "-i", path, "-t", str(seconds),
               "-vf", f"fps={fps},scale='min({width},iw)':-2", os.path.join(tmp, "f%04d.png")]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0:
            raise MediaError(f"ffmpeg couldn't read the video: {r.stderr.decode()[-200:]}")
        names = sorted(n for n in os.listdir(tmp) if n.endswith(".png"))
        return [Image.open(os.path.join(tmp, n)).convert("RGB") for n in names]


def _anim_webp(frames, fps: float, quality: int) -> bytes:
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:],
                   duration=int(1000 / fps), loop=0, quality=quality, method=4,
                   minimize_size=True)
    return buf.getvalue()


def encode_motion(path: str, budget: int = DEFAULT_MOTION_BUDGET, max_seconds: float = 8.0,
                  max_width: int = 200) -> bytes:
    """Turn a video or animated GIF into a short silent looping clip."""
    ext = Path(path).suffix.lower()
    fps = 4.0
    if ext == ".gif":
        source = _frames_from_gif(Path(path).read_bytes(), fps, max_seconds)
    else:
        source = _frames_from_video(path, fps, max_seconds, max_width)
    if not source:
        raise MediaError("no frames in that video")
    # Try the nicest settings first, then give up smoothness, then size.
    for width in (max_width, int(max_width * 0.75), max_width // 2, max_width // 3):
        sized = [_resize(f, width) for f in source]
        for step in (1, 2, 4):  # 4 fps, 2 fps, 1 fps
            frames = sized[::step]
            out = _fit(lambda q: _anim_webp(frames, fps / step, q), budget, lo=20, hi=75)
            if out:
                return out
    raise MediaError(f"video can't fit in {budget} bytes; try a shorter clip")


def motion_preview(path: str) -> bytes:
    ext = Path(path).suffix.lower()
    if ext == ".gif":
        return encode_preview(Path(path).read_bytes())
    frames = _frames_from_video(path, 1, 0.5, 128)
    if not frames:
        raise MediaError("no frames in that video")
    buf = io.BytesIO()
    frames[0].save(buf, "PNG")
    return encode_preview(buf.getvalue())


# ---------------------------------------------------------------- hosting
class MediaCache:
    """Encodes each file once per (variant, budget) and remembers the result."""

    def __init__(self, image_budget: int = DEFAULT_IMAGE_BUDGET,
                 motion_budget: int = DEFAULT_MOTION_BUDGET):
        self.image_budget = image_budget
        self.motion_budget = motion_budget
        self._cache: dict[tuple, tuple[str, bytes]] = {}
        self._lock = threading.Lock()

    def get(self, path: Path, variant: str = "full") -> tuple[str, bytes]:
        """Return (content_type, bytes) ready to send over the air."""
        st = path.stat()
        key = (str(path), st.st_mtime, variant, self.image_budget, self.motion_budget)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = self._encode(path, variant)
        with self._lock:
            self._cache[key] = result
            while len(self._cache) > 200:
                self._cache.pop(next(iter(self._cache)))
        return result

    def _encode(self, path: Path, variant: str) -> tuple[str, bytes]:
        ext = path.suffix.lower()
        moving = ext in VIDEO_EXTS or (ext == ".gif" and _is_animated(path))
        if not have_pillow():
            # No encoder: send small pictures as they are, refuse the rest.
            if ext in MEDIA_TYPES and st_size(path) <= self.image_budget and variant == "full":
                return MEDIA_TYPES[ext], path.read_bytes()
            raise MediaError("host can't shrink media: pip install pillow")
        if variant == "preview":
            data = motion_preview(str(path)) if moving else encode_preview(path.read_bytes())
        elif moving:
            data = encode_motion(str(path), self.motion_budget)
        else:
            data = encode_image(path.read_bytes(), self.image_budget)
        return "image/webp", data


def st_size(path: Path) -> int:
    return path.stat().st_size


def _is_animated(path: Path) -> bool:
    try:
        Image, _ = _pil()
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return False

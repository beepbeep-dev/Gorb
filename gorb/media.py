"""Gorb Pixels 2 and Gorb Motion 2: pictures and video small enough for radio.

Radio is slow (G.O.R.B Small on US915: ~1.2 KB/s, ham G.O.R.B: ~0.1 KB/s),
so hosts never send the original file. What they send was chosen by the
simulations in lab/ (see lab/README.md for the numbers):

Gorb Pixels 2 (pictures), three layers the browser stacks on screen:
    preview   ~150 B WebP, 32 px. Shown blurred after one radio packet.
    standard  <= 6 KB. AVIF or WebP, whichever looks better for this
              picture (the host measures both). Recognisable and decent.
    hd        <= 24 KB AVIF. Close to the original (SSIM ~0.95 in the lab).
              Replaces the standard layer when it lands.
Hosts send short replies first, so the layers arrive in that order.

Gorb Motion 2 (video), real video with sound instead of a silent slideshow:
    av1       WebM, AV1 video + Opus voice-quality audio (6 kbps), <= 40 KB.
              The lab found AV1 gives the best picture per byte by far.
    h264      MP4, H.264 + AAC, same budget, for browsers without AV1.
    lite      MP4 H.264 + AAC <= 24 KB, for ESP32 boards' small memory.
    preview   first frame as a ~2.5 KB picture, shown before you press play.
Videos longer than the budget allows are cut (by default to about 20 s).

Animated GIFs in <img> become small animated WebPs, as before.

Why not base64? Base64 turns 3 bytes into 4, which makes files bigger. The
savings have to come from encoding the picture itself better.

Needs Pillow with AVIF support (pip install pillow) and, for video, ffmpeg
with libaom-av1, libopus and libx264.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif",
}

DEFAULT_IMAGE_BUDGET = 6000
DEFAULT_HD_BUDGET = 24000
DEFAULT_MOTION_BUDGET = 40000
LITE_MOTION_BUDGET = 24000
PREVIEW_WIDTH = 32
MIN_VIDEO_KBPS = 14  # below this AV1 video turns to mush, so cut the clip instead
AUDIO_KBPS = 6  # Opus at 6 kbps: clear speech, rough music

IMAGE_VARIANTS = ("preview", "standard", "hd")
VIDEO_VARIANTS = ("preview", "av1", "h264", "lite")


class MediaError(Exception):
    pass


def is_media(path: str | os.PathLike) -> bool:
    ext = Path(path).suffix.lower()
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def is_video(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def have_avif() -> bool:
    try:
        from PIL import features
        return bool(features.check("avif"))
    except Exception:
        return False


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise MediaError("pictures need Pillow: pip install pillow") from e
    return Image, ImageOps


def _open(data: bytes):
    Image, ImageOps = _pil()
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise MediaError(f"can't read picture: {e}") from e
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    return img


def _resize(img, width: int):
    Image, _ = _pil()
    if img.width <= width:
        return img
    return img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)


def _encode(img, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "avif":
        img.save(buf, "AVIF", quality=quality, speed=4)
    else:
        img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def _fit(encode, budget: int, lo: int = 5, hi: int = 90) -> bytes | None:
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


def ssim(a, b) -> float:
    """Structural similarity of two pictures (grey, 8x8 windows), 1.0 = identical.
    Small numpy version so hosts don't need scikit-image."""
    import numpy as np
    Image, _ = _pil()
    if b.size != a.size:
        b = b.resize(a.size, Image.LANCZOS)
    x = np.asarray(a.convert("L"), dtype=np.float64)
    y = np.asarray(b.convert("L"), dtype=np.float64)

    def box(m, k=8):
        c = np.cumsum(np.cumsum(np.pad(m, ((1, 0), (1, 0))), 0), 1)
        return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)

    if min(x.shape) < 8:
        return 1.0 if np.allclose(x, y) else 0.0
    mx, my = box(x), box(y)
    vx, vy, cxy = box(x * x) - mx ** 2, box(y * y) - my ** 2, box(x * y) - mx * my
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2))
    return float(s.mean())


def encode_image(data: bytes, budget: int = DEFAULT_IMAGE_BUDGET, max_width: int = 720,
                 formats: tuple[str, ...] | None = None) -> tuple[str, bytes]:
    """Best-looking picture that fits `budget` bytes. Returns (content_type, bytes).

    Tries AVIF and WebP at several sizes and keeps whichever measures closest
    to the original. The lab found AVIF usually wins (~25% fewer bytes for the
    same look), but not on every picture.
    """
    if formats is None:
        formats = ("avif", "webp") if have_avif() else ("webp",)
    src = _open(data)
    ref = _resize(src, max_width)
    best = None  # (score, fmt, bytes)
    width = ref.width
    tried_any = False
    while width >= 16:
        img = _resize(ref, width)
        for fmt in formats:
            out = _fit(lambda q: _encode(img, fmt, q), budget, lo=25 if width > 96 else 1)
            if out:
                tried_any = True
                Image, _ = _pil()
                score = ssim(ref, Image.open(io.BytesIO(out)).convert(ref.mode))
                if best is None or score > best[0]:
                    best = (score, fmt, out)
        # A smaller sharp picture often beats a big blurry one, so keep going
        # one step down after the first fit, then stop.
        if tried_any and width < ref.width * 0.6:
            break
        width = int(width * 0.75)
    if best is None:
        raise MediaError(f"picture can't fit in {budget} bytes")
    return f"image/{best[1]}", best[2]


def encode_preview(data: bytes) -> bytes:
    """Tiny blurry stand-in (WebP: AVIF's fixed overhead is bigger than this)."""
    return _encode(_resize(_open(data), PREVIEW_WIDTH), "webp", 40)


# ---------------------------------------------------------------- animations
def encode_animation(path: str, budget: int = LITE_MOTION_BUDGET, max_seconds: float = 8.0,
                     max_width: int = 240) -> bytes:
    """Animated GIF -> small looping animated WebP (stays an <img>)."""
    Image, _ = _pil()
    im = Image.open(path)
    fps, frames, t, next_t = 6.0, [], 0.0, 0.0
    try:
        while t < max_seconds:
            dur = (im.info.get("duration", 100) or 100) / 1000
            if t >= next_t:
                frames.append(im.copy().convert("RGB"))
                next_t += 1 / fps
            t += dur
            im.seek(im.tell() + 1)
    except EOFError:
        pass
    if not frames:
        raise MediaError("no frames in that animation")
    for width in (max_width, int(max_width * 0.75), max_width // 2, max_width // 3):
        sized = [_resize(f, width) for f in frames]
        for step in (1, 2, 3):
            sub = sized[::step]

            def enc(q, sub=sub, step=step):
                buf = io.BytesIO()
                sub[0].save(buf, "WEBP", save_all=True, append_images=sub[1:],
                            duration=int(1000 * step / fps), loop=0, quality=q, method=4)
                return buf.getvalue()

            out = _fit(enc, budget, lo=20, hi=80)
            if out:
                return out
    raise MediaError(f"animation can't fit in {budget} bytes")


# ---------------------------------------------------------------- video
def _probe_duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise MediaError("can't read that video") from None


def _has_audio(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())


def video_plan(budget: int, duration: float, audio: bool, codec: str) -> dict:
    """Pick clip length, picture size and frame rate for a byte budget.

    Settings come from lab experiment 3: at radio bitrates, a smaller,
    smoother picture looks better than a big one, and below ~14 kbps the
    clip should get shorter rather than blurrier.
    """
    akbps = (AUDIO_KBPS if codec == "av1" else 12) if audio else 0
    overhead_kbps = 1.0
    seconds = min(duration, budget * 8 / 1000 / (MIN_VIDEO_KBPS + akbps + overhead_kbps))
    seconds = max(1.0, seconds)
    vkbps = budget * 8 / 1000 / seconds - akbps - overhead_kbps
    if codec == "av1":  # lab: AV1 holds up at larger sizes than you'd expect
        table = [(10, 160, 10), (13, 224, 12), (30, 320, 12), (60, 384, 15)]
        width, fps = 448, 15
    else:  # lab: H.264 needs about 1.5x the bits for the same look
        table = [(12, 160, 10), (18, 192, 12), (30, 256, 15), (60, 320, 15)]
        width, fps = 384, 15
    for limit, w, f in table:
        if vkbps < limit:
            width, fps = w, f
            break
    return {"seconds": round(seconds, 2), "vkbps": vkbps, "akbps": akbps, "width": width,
            "fps": fps}


def encode_video(path: str, codec: str = "av1", budget: int = DEFAULT_MOTION_BUDGET) -> tuple[str, bytes]:
    """Video -> (content_type, bytes): AV1+Opus WebM or H.264+AAC MP4 under `budget`."""
    if not have_ffmpeg():
        raise MediaError("video needs ffmpeg installed")
    duration = _probe_duration(path)
    audio = _has_audio(path)
    plan = video_plan(budget, duration, audio, codec)
    vkbps = plan["vkbps"]
    for _ in range(6):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "pass")
            out = os.path.join(tmp, "out." + ("webm" if codec == "av1" else "mp4"))
            if codec == "av1":
                v = ["-c:v", "libaom-av1", "-b:v", f"{vkbps:.1f}k", "-cpu-used", "5",
                     "-row-mt", "1", "-g", str(plan["fps"] * 10), "-lag-in-frames", "35"]
                a = ["-c:a", "libopus", "-b:a", f"{plan['akbps']}k", "-ac", "1",
                     "-application", "voip"]
                fmt = "webm"
            else:
                v = ["-c:v", "libx264", "-b:v", f"{vkbps:.1f}k", "-preset", "veryslow",
                     "-profile:v", "baseline", "-g", str(plan["fps"] * 10), "-pix_fmt", "yuv420p"]
                a = ["-c:a", "aac", "-b:a", f"{plan['akbps']}k", "-ac", "1", "-ar", "16000"]
                fmt = "mp4"
            if not audio:
                a = ["-an"]
            base = ["ffmpeg", "-v", "error", "-y", "-i", path, "-t", str(plan["seconds"]),
                    "-vf", f"fps={plan['fps']},scale={plan['width']}:-2"]
            r1 = subprocess.run([*base, *v, "-pass", "1", "-passlogfile", log, "-an", "-f", fmt,
                                 os.devnull], capture_output=True)
            r2 = subprocess.run([*base, *v, "-pass", "2", "-passlogfile", log, *a,
                                 "-movflags", "+faststart", "-f", fmt, out], capture_output=True)
            if r1.returncode or r2.returncode:
                err = (r1.stderr or r2.stderr).decode(errors="replace")[-300:]
                raise MediaError(f"ffmpeg failed: {err}")
            data = Path(out).read_bytes()
        if len(data) <= budget:
            ctype = "video/webm" if codec == "av1" else "video/mp4"
            return ctype, data
        vkbps *= 0.88 * budget / len(data)
        if vkbps < 2:
            break
    raise MediaError(f"video can't fit in {budget} bytes")


POSTER_BUDGET = 2500


def video_preview(path: str) -> tuple[str, bytes]:
    """The picture shown on the player before you press play: the first
    frame as a small but proper picture (a blurry 32 px one looks broken)."""
    if not have_ffmpeg():
        raise MediaError("video needs ffmpeg installed")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-vf", "scale=384:-2",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if r.returncode or not r.stdout:
        raise MediaError("can't read that video")
    return encode_image(r.stdout, POSTER_BUDGET, max_width=384)


# ---------------------------------------------------------------- hosting
class MediaCache:
    """Encodes each file once per variant and remembers the result."""

    def __init__(self, image_budget: int = DEFAULT_IMAGE_BUDGET,
                 motion_budget: int = DEFAULT_MOTION_BUDGET, hd_budget: int = DEFAULT_HD_BUDGET):
        self.image_budget = image_budget
        self.motion_budget = motion_budget
        self.hd_budget = hd_budget
        self._cache: dict[tuple, tuple[str, bytes]] = {}
        self._lock = threading.Lock()
        self._busy: dict[tuple, threading.Lock] = {}

    def get(self, path: Path, variant: str = "standard") -> tuple[str, bytes]:
        """Return (content_type, bytes) ready to send over the air."""
        st = path.stat()
        key = (str(path), st.st_mtime, variant, self.image_budget, self.motion_budget,
               self.hd_budget)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            busy = self._busy.setdefault(key, threading.Lock())
        with busy:  # two requests for the same thing: encode once
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
            result = self._encode(path, variant)
            with self._lock:
                self._cache[key] = result
                self._busy.pop(key, None)
                while len(self._cache) > 200:
                    self._cache.pop(next(iter(self._cache)))
            return result

    def _encode(self, path: Path, variant: str) -> tuple[str, bytes]:
        ext = path.suffix.lower()
        if not have_pillow():
            # No encoder: send small pictures as they are, refuse the rest.
            if ext in MEDIA_TYPES and path.stat().st_size <= self.image_budget:
                return MEDIA_TYPES[ext], path.read_bytes()
            raise MediaError("host can't shrink media: pip install pillow")
        if ext in VIDEO_EXTS:
            if variant == "preview":
                return video_preview(str(path))
            if variant == "h264":
                return encode_video(str(path), "h264", self.motion_budget)
            if variant == "lite":
                return encode_video(str(path), "h264", min(self.motion_budget, LITE_MOTION_BUDGET))
            return encode_video(str(path), "av1", self.motion_budget)
        if variant == "preview":
            return "image/webp", encode_preview(path.read_bytes())
        if ext == ".gif" and _is_animated(path):
            return "image/webp", encode_animation(str(path))
        if variant == "hd" and self.hd_budget > self.image_budget:
            return encode_image(path.read_bytes(), self.hd_budget, max_width=1280,
                                formats=("avif",) if have_avif() else ("webp",))
        return encode_image(path.read_bytes(), self.image_budget)


def _is_animated(path: Path) -> bool:
    try:
        Image, _ = _pil()
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return False

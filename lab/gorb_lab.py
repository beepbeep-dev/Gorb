"""Gorb Lab: simulations that decide how G.O.R.B sends pictures and video.

Every experiment works at a fixed number of bytes, because over radio bytes
are time: on G.O.R.B Small (US915) about 1.2 KB/s, on EU868 about 0.1 KB/s
once the 10% duty cycle kicks in, on ham G.O.R.B about 0.1 KB/s.

    python lab/gorb_lab.py images        which picture format looks best per byte
    python lab/gorb_lab.py progressive   which loading strategy shows a good picture soonest
    python lab/gorb_lab.py video         which video format looks best per byte, with sound
    python lab/gorb_lab.py echo          how much Gorb Echo saves when neighbours share pages
    python lab/gorb_lab.py all

Quality is measured with SSIM (structural similarity, 1.0 = identical to the
original, ~0.9 = good, ~0.8 = clearly soft, <0.7 = mush), computed at the
size the picture is shown on screen, so shrinking a picture is penalised
fairly. Results go to lab/results/.

Needs: pillow (with AVIF), numpy, scikit-image (test photos + SSIM),
matplotlib (charts), ffmpeg with libaom-av1, libvpx-vp9, libx264, libopus.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"
sys.path.insert(0, str(ROOT.parent))

from gorb.small import REGIONS  # noqa: E402

DISPLAY_WIDTH = 640  # pictures are judged at this width on screen


# ---------------------------------------------------------------- helpers
def test_photos() -> dict[str, Image.Image]:
    from skimage import data
    photos = {
        "astronaut (portrait)": data.astronaut(),
        "coffee (still life)": data.coffee(),
        "chelsea (cat, fur)": data.chelsea(),
        "rocket (outdoor)": data.rocket(),
        "hubble (stars)": data.hubble_deep_field()[:600, :900],
    }
    tower = ROOT.parent / "examples" / "site" / "media" / "tower.jpg"
    if tower.exists():
        photos["tower (drawing)"] = np.asarray(Image.open(tower).convert("RGB"))
    out = {}
    for name, arr in photos.items():
        im = Image.fromarray(arr).convert("RGB")
        h = round(im.height * DISPLAY_WIDTH / im.width)
        out[name] = im.resize((DISPLAY_WIDTH, h), Image.LANCZOS)
    return out


def ssim(a: Image.Image, b: Image.Image) -> float:
    from skimage.metrics import structural_similarity
    if b.size != a.size:
        b = b.resize(a.size, Image.LANCZOS)
    return float(structural_similarity(np.asarray(a.convert("L")), np.asarray(b.convert("L")),
                                       data_range=255))


def encode(img: Image.Image, codec: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if codec == "jpeg":
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    elif codec == "webp":
        img.save(buf, "WEBP", quality=quality, method=6)
    elif codec == "avif":
        img.save(buf, "AVIF", quality=quality, speed=4)
    else:
        raise ValueError(codec)
    return buf.getvalue()


def best_fit(img: Image.Image, codec: str, budget: int, widths=(640, 480, 352, 256, 176)):
    """Best SSIM reachable within `budget` bytes: try sizes, binary-search quality."""
    best = (0.0, None, None)
    for w in widths:
        small = img.resize((w, round(img.height * w / img.width)), Image.LANCZOS) if w < img.width else img
        lo, hi, fit = 1, 95, None
        while lo <= hi:
            q = (lo + hi) // 2
            data = encode(small, codec, q)
            if len(data) <= budget:
                fit, lo = (q, data), q + 1
            else:
                hi = q - 1
        if fit:
            score = ssim(img, Image.open(io.BytesIO(fit[1])).convert("RGB"))
            if score > best[0]:
                best = (score, w, fit[1])
    return best


def lora_seconds(nbytes: int, region: str = "US915", hops: int = 0) -> float:
    """Time to deliver nbytes as G.O.R.B chunks over LoRa (incl. duty cycle)."""
    r = REGIONS[region]
    chunks = max(1, -(-nbytes // 180))
    air = chunks * r.airtime(180 + 40)
    gaps = chunks * 0.05  # listen-before-talk and turnaround
    budget_free = r.duty * 360  # board starts with 6 minutes' worth of allowance
    t = air + gaps
    if air > budget_free:  # then limited to the duty cycle
        t = budget_free + gaps + (air - budget_free) / r.duty
    return t * (1 + hops)


def ensure_out():
    OUT.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------ experiment 1
def exp_images():
    ensure_out()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    photos = test_photos()
    budgets = [1500, 3000, 6000, 12000, 24000]
    codecs = ["jpeg", "webp", "avif"]
    results = {c: {b: [] for b in budgets} for c in codecs}
    samples = {}
    t0 = time.time()
    for name, img in photos.items():
        for b in budgets:
            for c in codecs:
                score, w, data = best_fit(img, c, b)
                results[c][b].append(score)
                if name.startswith("chelsea") and b == 6000:
                    samples[c] = (data, score, w)
        print(f"  {name} done ({time.time() - t0:.0f}s)")

    lines = ["# Experiment 1: which picture format looks best per byte\n",
             f"{len(photos)} test photos, each judged at {DISPLAY_WIDTH}px wide. "
             "Number = average SSIM (1.0 = identical, 0.9 = good, 0.8 = soft). "
             "For each budget the lab tried several picture sizes and every quality "
             "level, and kept the best result.\n",
             "| budget | " + " | ".join(c.upper() for c in codecs) + " | LoRa US915 time |",
             "|---|" + "---|" * (len(codecs) + 1)]
    for b in budgets:
        row = [f"{np.mean(results[c][b]):.3f}" for c in codecs]
        lines.append(f"| {b // 1000 if b % 1000 == 0 else b / 1000} KB | " + " | ".join(row) +
                     f" | {lora_seconds(b):.0f}s |")
    # how many bytes AVIF needs to match WebP @ 6 KB
    target = np.mean(results["webp"][6000])
    avif_curve = [(b, np.mean(results["avif"][b])) for b in budgets]
    match = None
    for (b0, s0), (b1, s1) in zip(avif_curve, avif_curve[1:]):
        if s0 <= target <= s1:  # interpolate on a log-bytes scale
            match = b0 * (b1 / b0) ** ((target - s0) / (s1 - s0))
    lines.append("")
    if match:
        lines.append(f"WebP at 6 KB scores {target:.3f}; AVIF matches that with about "
                     f"{match / 1000:.1f} KB ({100 * (1 - match / 6000):.0f}% fewer bytes).")
    per_photo = ["", "Per photo at 6 KB (WebP → AVIF):", ""]
    for i, name in enumerate(photos):
        per_photo.append(f"* {name}: {results['webp'][6000][i]:.3f} → {results['avif'][6000][i]:.3f}")
    lines += per_photo
    (OUT / "1-images.md").write_text("\n".join(lines) + "\n")

    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=130)
    for c, col in zip(codecs, ["#c0392b", "#2980b9", "#16a085"]):
        ax.plot([b / 1000 for b in budgets], [np.mean(results[c][b]) for b in budgets], "o-",
                label=c.upper(), color=col, lw=2)
    ax.set_xscale("log")
    ax.set_xticks([b / 1000 for b in budgets])
    ax.set_xticklabels([f"{b / 1000:g} KB" for b in budgets])
    ax.set_ylabel("quality (SSIM, higher is better)")
    ax.set_title("Picture quality vs bytes sent over radio")
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "1-images.png")

    # side by side: the same cat at 6 KB in each format
    if samples:
        tiles = []
        for c in codecs:
            data, score, w = samples[c]
            im = Image.open(io.BytesIO(data)).convert("RGB").resize(photos["chelsea (cat, fur)"].size,
                                                                    Image.LANCZOS)
            tiles.append((c, im, score))
        W, H = tiles[0][1].size
        sheet = Image.new("RGB", (W * len(tiles), H + 34), "white")
        from PIL import ImageDraw
        d = ImageDraw.Draw(sheet)
        for i, (c, im, score) in enumerate(tiles):
            sheet.paste(im, (i * W, 34))
            d.text((i * W + 10, 10), f"{c.upper()}  6 KB  SSIM {score:.3f}", fill="black")
        sheet.save(OUT / "1-images-cat-6kb.jpg", quality=88)
    print("\n".join(lines))
    return results


# ------------------------------------------------------------ experiment 2
def exp_progressive():
    """Strategies for showing a picture while it arrives.

    A: one 6 KB WebP (Gorb Pixels v1, with nothing shown until it lands)
    B: v1 + 150 byte blurred preview first
    C: WebP preview (~150 B) -> AVIF 6 KB -> AVIF 18 KB  ("Gorb Pixels 2" layers)
    For each, quality on screen over time on US915 and EU868.
    """
    ensure_out()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    photos = test_photos()
    layers = {
        "A: WebP 6 KB": [("webp", 6000)],
        "B: preview + WebP 6 KB": [("webp", 160), ("webp", 6000)],
        # AVIF carries ~250 bytes of fixed overhead, so the tiny preview stays WebP.
        "C: Gorb Pixels 2 (AVIF layers)": [("webp", 160), ("avif", 6000), ("avif", 18000)],
    }
    curves = {}
    for strat, steps in layers.items():
        pts = []  # (bytes so far, ssim) averaged over photos
        for name, img in photos.items():
            total, row = 0, []
            for codec, budget in steps:
                widths = (32, 24) if budget < 400 else (640, 480, 352, 256, 176)
                score, w, data = best_fit(img, codec, budget, widths)
                total += len(data)
                row.append((total, score))
            pts.append(row)
        avg = [(int(np.mean([r[i][0] for r in pts])), float(np.mean([r[i][1] for r in pts])))
               for i in range(len(steps))]
        curves[strat] = avg

    lines = ["# Experiment 2: what's on screen while a picture arrives\n",
             "Time until the picture shown reaches each quality step (one relay hop "
             "doubles these). Before the first step the reader sees an empty box.\n"]
    for region in ("US915", "EU868"):
        lines.append(f"\n**{region}**\n")
        lines.append("| strategy | steps (time → SSIM) |")
        lines.append("|---|---|")
        for strat, avg in curves.items():
            steps = ", ".join(f"{lora_seconds(b, region):.1f}s → {s:.2f}" for b, s in avg)
            lines.append(f"| {strat} | {steps} |")
    (OUT / "2-progressive.md").write_text("\n".join(lines) + "\n")

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), dpi=130, sharey=True)
    for ax, region in zip(axes, ("US915", "EU868")):
        for (strat, avg), col in zip(curves.items(), ["#c0392b", "#e67e22", "#16a085"]):
            xs, ys = [0], [0]
            for b, s in avg:
                t = lora_seconds(b, region)
                xs += [t, t]
                ys += [ys[-1], s]
            end = lora_seconds(max(b for b, _ in curves["C: Gorb Pixels 2 (AVIF layers)"]), region) * 1.15
            xs.append(end)
            ys.append(ys[-1])
            ax.plot(xs, ys, label=strat, color=col, lw=2)
        ax.set_title(f"LoRa {region}")
        ax.set_xlabel("seconds since the picture was requested")
        ax.grid(alpha=.3)
    axes[0].set_ylabel("quality on screen (SSIM)")
    axes[0].legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "2-progressive.png")
    print("\n".join(lines))
    return curves


# ------------------------------------------------------------ experiment 3
def make_test_clip(path: Path, seconds: int = 12):
    """A 12 s narrated clip: slow pans over photos (like a real video) + speech."""
    from skimage import data
    with tempfile.TemporaryDirectory() as tmp:
        for i, arr in enumerate([data.coffee(), data.rocket(), data.chelsea()]):
            Image.fromarray(arr).resize((1280, 854), Image.LANCZOS).save(f"{tmp}/p{i}.png")
        text = ("Welcome to gorb, the global open radio browser. "
                "This video travelled over the air as radio waves. "
                "In gorb we speak, in gorb we use.")
        seg = seconds / 3
        frames = int(seg * 30)
        filt = ";".join(
            f"[{i}:v]zoompan=z='1+0.0015*on':x='iw/2-(iw/zoom/2)+on*{(-1) ** i}':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s=640x360:fps=30[v{i}]" for i in range(3))
        filt += ";[v0][v1][v2]concat=n=3:v=1:a=0[v]"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-loop", "1", "-i", f"{tmp}/p0.png", "-loop", "1", "-i", f"{tmp}/p1.png",
             "-loop", "1", "-i", f"{tmp}/p2.png",
             "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
             "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
             "-filter_complex", filt + ";[3:a]apad[s];[4:a]volume=0.05[m];[s][m]amix=inputs=2:duration=first[a]",
             "-map", "[v]", "-map", "[a]", "-t", str(seconds),
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             str(path)], check=True)


def ffmpeg_size(args: list[str], out: Path) -> int:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True,
                   capture_output=True)
    return out.stat().st_size


def encode_video(src: Path, out: Path, codec: str, budget: int, width: int, fps: int,
                 seconds: float, audio_kbps: int) -> int:
    """Two-pass encode aimed at `budget` bytes total; shrinks bitrate until it fits."""
    total_kbps = budget * 8 / seconds / 1000
    v_kbps = max(2.0, total_kbps - audio_kbps - 1.0)  # ~1 kbps container overhead
    vf = f"fps={fps},scale={width}:-2"
    for _ in range(6):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "pass")
            if codec == "av1":
                v = ["-c:v", "libaom-av1", "-b:v", f"{v_kbps:.1f}k", "-cpu-used", "5", "-row-mt", "1",
                     "-g", str(fps * 10), "-lag-in-frames", "35"]
                a = ["-c:a", "libopus", "-b:a", f"{audio_kbps}k", "-ac", "1", "-application", "voip"]
                fmt = "webm"
            elif codec == "vp9":
                v = ["-c:v", "libvpx-vp9", "-b:v", f"{v_kbps:.1f}k", "-deadline", "good", "-cpu-used", "2",
                     "-g", str(fps * 10)]
                a = ["-c:a", "libopus", "-b:a", f"{audio_kbps}k", "-ac", "1", "-application", "voip"]
                fmt = "webm"
            elif codec == "h264":
                v = ["-c:v", "libx264", "-b:v", f"{v_kbps:.1f}k", "-preset", "veryslow",
                     "-g", str(fps * 10), "-pix_fmt", "yuv420p"]
                a = ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "1", "-ar", "16000"]
                fmt = "mp4"
            else:
                raise ValueError(codec)
            base = ["-i", str(src), "-t", str(seconds), "-vf", vf]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *base, *v, "-pass", "1", "-passlogfile", log,
                            "-an", "-f", fmt, os.devnull], check=True, capture_output=True)
            size = ffmpeg_size([*base, *v, "-pass", "2", "-passlogfile", log, *a,
                                "-movflags", "+faststart", "-f", fmt], out)
        if size <= budget:
            return size
        v_kbps *= 0.85 * budget / size
        if v_kbps < 3:
            break  # too little left for any picture: doesn't fit
    return size


def video_ssim(ref: Path, test: Path, seconds: float) -> float:
    """SSIM of a received clip vs the original, both shown at 640x360, 30 fps
    (so a low frame rate counts against a clip, as it does to a viewer)."""
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-t", str(seconds), "-i", str(test), "-t", str(seconds), "-i", str(ref),
         "-filter_complex",
         "[0:v]scale=640:360:flags=lanczos,fps=30,format=yuv420p[a];"
         "[1:v]scale=640:360,fps=30,format=yuv420p[b];[a][b]ssim",
         "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "SSIM" in line and "All:" in line:
            return float(line.split("All:")[1].split()[0])
    raise RuntimeError(r.stderr[-400:])


def webp_anim_to_mp4(data: bytes, out: Path, fps: float):
    """ffmpeg can't read animated WebP, so unpack it for measuring."""
    im = Image.open(io.BytesIO(data))
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(im.n_frames):
            im.seek(i)
            im.convert("RGB").save(f"{tmp}/f{i:04d}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", f"{tmp}/f%04d.png",
                        "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", str(out)], check=True)


def motion_v1(src: Path, budget: int, seconds: float) -> bytes:
    """Gorb Motion v1 (the old format), kept here as the baseline: a silent
    animated WebP at up to 4 fps, 200 px wide, squeezed to the budget."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-t", str(seconds),
                        "-vf", "fps=4,scale=200:-2", f"{tmp}/f%04d.png"], check=True)
        frames = [Image.open(f"{tmp}/{n}").convert("RGB") for n in sorted(os.listdir(tmp))]
    for width in (200, 150, 100, 66):
        sized = [f.resize((width, round(f.height * width / f.width)), Image.LANCZOS) for f in frames]
        for step in (1, 2, 4):
            sub = sized[::step]
            lo, hi, best = 20, 75, None
            while lo <= hi:
                q = (lo + hi) // 2
                buf = io.BytesIO()
                sub[0].save(buf, "WEBP", save_all=True, append_images=sub[1:],
                            duration=int(250 * step), loop=0, quality=q, method=4)
                if buf.tell() <= budget:
                    best, lo = buf.getvalue(), q + 1
                else:
                    hi = q - 1
            if best:
                return best
    raise RuntimeError("v1 could not fit")


def exp_video():
    ensure_out()
    from gorb import media
    seconds = 12
    src = OUT / "test-clip-original.mp4"
    if not src.exists():
        make_test_clip(src, seconds)
    budgets = [16000, 32000, 48000]
    # (label, codec, width, fps, audio kbps)
    candidates = [
        ("AV1 + Opus", "av1", [(256, 15), (320, 12), (192, 15)], 6),
        ("VP9 + Opus", "vp9", [(256, 15), (192, 12)], 6),
        ("H.264 + AAC", "h264", [(256, 15), (192, 12)], 12),
    ]
    rows = []
    best_files = {}
    for b in budgets:
        # Gorb Motion v1: silent animated WebP
        data = motion_v1(src, b, seconds)
        tmpmp4 = OUT / "_webp.mp4"
        im = Image.open(io.BytesIO(data))
        fps_v1 = round(im.n_frames / seconds, 1)
        webp_anim_to_mp4(data, tmpmp4, im.n_frames / seconds)
        s_v1 = video_ssim(src, tmpmp4, seconds)
        tmpmp4.unlink()
        rows.append((b, "Motion v1: animated WebP (silent)", len(data), s_v1, f"{im.width}px", fps_v1, "none"))
        if b == 32000:
            (OUT / "3-video-v1-32kb.webp").write_bytes(data)
        for label, codec, sizes, akbps in candidates:
            best = None
            for w, fps in sizes:
                ext = "webm" if codec != "h264" else "mp4"
                out = OUT / f"_cand.{ext}"
                try:
                    size = encode_video(src, out, codec, b, w, fps, seconds, akbps)
                except subprocess.CalledProcessError:
                    continue
                if size > b:
                    continue
                s = video_ssim(src, out, seconds)
                if best is None or s > best[1]:
                    best = (size, s, w, fps, out.read_bytes(), ext)
            if best:
                size, s, w, fps, blob, ext = best
                rows.append((b, label, size, s, f"{w}px", fps, f"{akbps} kbps"))
                if b == 32000:
                    best_files[label] = (blob, ext)
            print(f"  {b} {label}: {best[1] if best else 'did not fit':}")
        # what Gorb actually ships (media.encode_video with its own settings)
        for codec, label in (("av1", "Motion 2 as shipped (AV1+Opus)"), ("h264", "Motion 2 as shipped (H.264+AAC)")):
            try:
                ctype, blob = media.encode_video(str(src), codec, b)
            except media.MediaError:
                rows.append((b, label, 0, 0.0, "-", "-", "did not fit"))
                continue
            ext = "webm" if codec == "av1" else "mp4"
            out = OUT / f"_ship.{ext}"
            out.write_bytes(blob)
            plan = media.video_plan(b, seconds, True, codec)
            s_ship = video_ssim(src, out, plan["seconds"])
            out.unlink()
            rows.append((b, label, len(blob), s_ship, f"{plan['width']}px", plan["fps"],
                         f"{plan['akbps']} kbps, {plan['seconds']:.0f}s kept"))
    for f in OUT.glob("_cand.*"):
        f.unlink()
    for label, (blob, ext) in best_files.items():
        name = label.split()[0].lower().replace(".", "")
        (OUT / f"3-video-{name}-32kb.{ext}").write_bytes(blob)

    lines = ["# Experiment 3: video per byte\n",
             f"A {seconds} s narrated test clip (slow pans over photos plus synthetic speech). "
             "SSIM is measured at 640x360 and 30 frames per second, so jerky low frame rates "
             "count against a clip, as they do for a viewer. Higher is better.\n",
             "| budget | format | bytes | SSIM | size | fps | sound |", "|---|---|---|---|---|---|---|"]
    for b, label, size, s, w, fps, snd in rows:
        lines.append(f"| {b // 1000} KB | {label} | {size:,} | {s:.3f} | {w} | {fps} | {snd} |")
    lines.append("")
    lines.append(f"LoRa US915 download time: 16 KB ≈ {lora_seconds(16000):.0f}s, "
                 f"32 KB ≈ {lora_seconds(32000):.0f}s, 48 KB ≈ {lora_seconds(48000):.0f}s.")
    (OUT / "3-video.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return rows


# ------------------------------------------------------------ experiment 4
def exp_echo():
    """Gorb Echo: five neighbours, one relay hop from the host, each open the
    same page (3 KB of HTML, two pictures with all three layers, one video).

    Without Echo every visit is a full download through the relay.
    With Echo:
      * neighbours who are on the air at the time overhear the first visit and
        keep it, so their own visit is instant;
      * neighbours who turn up later ask, and the relay answers from what it
        overheard, so the request no longer goes all the way to the host.
    """
    ensure_out()
    from gorb import media
    page = 3000 + 2 * (160 + media.DEFAULT_IMAGE_BUDGET + media.DEFAULT_HD_BUDGET) + media.DEFAULT_MOTION_BUDGET
    lines = ["# Experiment 4: Gorb Echo\n",
             f"Five neighbours each open the same page: about {page / 1000:.0f} KB (HTML, two "
             "pictures with preview + standard + HD layers, one video). The host is one relay "
             "hop away, so without Echo every byte is sent twice (host → relay → you).\n",
             "| region | case | radio time used | wait for visitor 2-5 |", "|---|---|---|---|"]
    for region in ("US915", "EU868"):
        one_hop = lora_seconds(page, region, hops=1)
        direct = lora_seconds(page, region, hops=0)
        cases = [
            ("no Echo", 5 * one_hop, one_hop),
            ("Echo, neighbours on the air at the time", one_hop, 0.0),
            ("Echo, neighbours turn up later", one_hop + 4 * direct, direct),
        ]
        for name, total, wait in cases:
            lines.append(f"| {region} | {name} | {total / 60:.1f} min | "
                         f"{'instant' if wait == 0 else f'{wait:.0f}s'} |")
    lines.append("")
    lines.append("Radio is shared, so radio time saved is time everyone else can use (rule 3). "
                 "With Echo, a popular page costs about one download per neighbourhood instead "
                 "of one per person, which is what makes the big HD layer affordable.")
    (OUT / "4-echo.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("experiment", choices=["images", "progressive", "video", "echo", "all"])
    a = p.parse_args()
    if a.experiment in ("images", "all"):
        exp_images()
    if a.experiment in ("progressive", "all"):
        exp_progressive()
    if a.experiment in ("video", "all"):
        exp_video()
    if a.experiment in ("echo", "all"):
        exp_echo()


if __name__ == "__main__":
    main()

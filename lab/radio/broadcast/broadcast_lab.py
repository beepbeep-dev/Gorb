"""Gorb broadcast lab: do fountain codes / network coding save airtime?

    python broadcast_lab.py          # full run (~5-8 minutes)
    python broadcast_lab.py --quick  # fewer trials (~1 minute)

Writes results.json, results.md (tables) and *.png charts next to this file.
Parts:
  1. codec: decode overhead and CPU cost of systematic RLNC/GF(256) vs LT
  2. delivery simulation (radiosim.py): ARQ variants vs shared coded stream
  3. carousel (Gorb Cast): plain vs coded loop for receive-only listeners
  4. frame budget: seed vs coefficient vector, RESEND list limits
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gorbcode as gc  # noqa: E402
import radiosim as rs  # noqa: E402

PAGES = {"6 KB": 6144, "40 KB": 40960}
COLORS = {"arq": "#2a78d6", "arq_echo": "#eb6834", "arq_fixed": "#1baf7a",
          "mc_list": "#eda100", "rlnc": "#e87ba4", "rlnc_echo": "#008300",
          "plain": "#2a78d6", "coded": "#e87ba4", "lt": "#eda100", "mixed": "#1baf7a"}
LABELS = {"arq": "Gorb today (ARQ)", "arq_echo": "Gorb today + Echo",
          "arq_fixed": "ARQ, fixed (1 session/listener)", "mc_list": "Shared stream + RESEND lists",
          "rlnc": "Shared stream + RLNC (NEED)", "rlnc_echo": "RLNC + Echo (keep partial)"}


# ====================================================================== part 1: codec
def rlnc_overhead(k, loss, trials, rng):
    """Extra symbols beyond K needed by the systematic RLNC decoder."""
    out = []
    for _ in range(trials):
        have = [j for j in range(k) if rng.random() >= loss]
        missing = [j for j in range(k) if j not in set(have)]
        m = len(missing)
        rows = []
        seq = k
        got = len(have)
        while True:
            if rng.random() >= loss:
                rows.append(gc.coefficients(seq, k)[missing] if m else None)
                got += 1
                if m == 0:
                    break
                if len(rows) >= m and gc.rank_gf256(np.stack(rows)) >= m:
                    break
            seq += 1
            if m == 0:
                break
        out.append(max(0, got - k) if m else 0)
    return out


def lt_overhead(k, trials, rng):
    code = gc.LTCode(k)
    return [code.symbols_to_decode(rng, 0.0) - k for _ in range(trials)]


def cpu_cost(k, loss, rng):
    blob = bytes(rng.randrange(256) for _ in range(k * 180 - 7))
    enc = gc.Encoder(blob)
    t = time.perf_counter()
    n_enc = 20
    for s in range(enc.K + 500, enc.K + 500 + n_enc):
        enc.symbol(s)
    t_enc = (time.perf_counter() - t) / n_enc
    dec = gc.Decoder(enc.K)
    for s in range(enc.K):
        if rng.random() >= loss:
            dec.add(s, enc.symbol(s))
    m = enc.K - len(dec.src)
    syms = [(s, enc.symbol(s)) for s in range(enc.K, enc.K + 3 * m + 20)]
    t = time.perf_counter()
    for s, p in syms:
        if dec.add(s, p):
            break
    t_dec = time.perf_counter() - t
    assert dec.blob == blob
    return {"K": enc.K, "missing": m, "py_encode_ms": t_enc * 1e3, "py_decode_ms": t_dec * 1e3,
            "decode_ops": dec.ops, "encode_ops_per_pkt": enc.K * enc.L,
            # memory (bytes) a careful C decoder needs beyond the page itself:
            "dec_extra_bytes": (m + 2) * 180 + m * m}


def part1(quick):
    rng = random.Random(42)
    ks = [10, 20, 35, 50, 100, 228, 400]
    res = {"rlnc": {}, "lt": {}, "cpu": []}
    for loss in (0.1, 0.3):
        for k in ks:
            tr = (60 if quick else 1000) if k <= 100 else (15 if quick else 200) if k <= 228 else (6 if quick else 60)
            o = rlnc_overhead(k, loss, tr, rng)
            res["rlnc"][f"{k}@{loss}"] = o
    for k in ks:
        tr = (40 if quick else 200) if k <= 100 else (10 if quick else 60)
        res["lt"][str(k)] = lt_overhead(k, tr, rng)
    for k in (35, 228, 400):
        res["cpu"].append(cpu_cost(k, 0.3, rng))
    return res


# ====================================================================== part 2: delivery
def pct(a, q):
    v = float(np.percentile(np.where(np.isfinite(a), a, 1e15), q))
    return math.inf if v >= 1e14 else v


def agg(runs):
    def m(key):
        v = [r[key] for r in runs]
        return float(np.mean(v))

    done = np.concatenate([r["done"] for r in runs])
    fin = done[np.isfinite(done)]
    return {
        "trials": len(runs), "air": m("air_final"), "air_total": m("air"), "ctrl_n": m("ctrl_n"),
        "air_ctrl": m("air_ctrl"), "delivered": float(np.isfinite(done).mean()),
        "median": pct(done, 50), "p90": pct(done, 90),
        "median_ok": float(np.median(fin)) if len(fin) else math.inf,
        "collisions": m("collisions"), "queue_drops": m("queue_drops"),
        "from_echo": m("from_echo"), "done_all": done.tolist(),
    }


def trials_for(n, quick):
    t = {1: 40, 5: 15, 20: 8, 100: 3}[n]
    return max(1, t // 3) if quick else t


def part2(quick, extra_dist):
    out = {}
    t0 = time.time()

    def go(name, scheme, page, losses, ntr, **kw):
        runs = [rs.run(scheme, page, losses, seed=1000 + i, extra_dist=extra_dist, **kw)
                for i in range(ntr)]
        out.setdefault(name, {})[scheme] = agg(runs)

    allsch = ["arq", "arq_echo", "arq_fixed", "mc_list", "rlnc"]
    # A/B: identical loss, everyone asks at once
    for pname, pb in PAGES.items():
        for p in (0.05, 0.15, 0.30):
            go(f"N=1 loss={p:.0%} {pname}", "arq", pb, [p], trials_for(1, quick))
            go(f"N=1 loss={p:.0%} {pname}", "rlnc", pb, [p], trials_for(1, quick))
            for n in (5, 20, 100):
                for sch in allsch:
                    if n == 100 and sch in ("arq", "arq_echo") and pb > 10000:
                        ntr = 1
                    else:
                        ntr = trials_for(n, quick)
                    go(f"N={n} loss={p:.0%} {pname}", sch, pb, [p] * n, ntr)
        print(f"  same-loss {pname} done ({time.time() - t0:.0f}s)", flush=True)
    for pname, pb in PAGES.items():
        het = [0.03] * 10 + [0.30] * 10
        for sch in allsch:
            go(f"hetero N=20 {pname}", sch, pb, het, trials_for(20, quick))
            go(f"bursty N=20 {pname}", sch, pb, [0.15] * 20, trials_for(20, quick), burst=True)
            go(f"relay N=20 {pname}", sch, pb, [0.15] * 20, trials_for(20, quick), relay=True)
        for sch in ("arq_fixed", "mc_list", "rlnc"):
            go(f"relay N=20 paced {pname}", sch, pb, [0.15] * 20, trials_for(20, quick),
               relay=True, pace=True, timeout=900.0)
        for sch in ("arq", "rlnc"):
            go(f"bursty N=1 {pname}", sch, pb, [0.15], trials_for(1, quick), burst=True)
            go(f"relay N=1 {pname}", sch, pb, [0.15], trials_for(1, quick), relay=True)
            go(f"relay N=1 paced {pname}", sch, pb, [0.15], trials_for(1, quick), relay=True,
               pace=True, timeout=900.0)
        for sch in ("arq", "arq_echo", "arq_fixed", "rlnc", "rlnc_echo"):
            go(f"staggered N=20 {pname}", sch, pb, [0.15] * 20, trials_for(20, quick),
               arrivals="spread", window=1800.0)
        for sch in ("arq", "arq_fixed", "mc_list", "rlnc"):
            go(f"EU868 N=20 {pname}", sch, pb, [0.15] * 20, max(1, trials_for(20, quick) - 2),
               region="EU868")
            go(f"EU868 N=20 patient {pname}", sch, pb, [0.15] * 20,
               max(1, trials_for(20, quick) - 2), region="EU868", timeout=3600.0, t_max=8 * 3600.0)
        for sch in ("arq", "rlnc"):
            go(f"EU868 N=1 {pname}", sch, pb, [0.15], trials_for(1, quick), region="EU868")
            go(f"EU868 N=1 patient {pname}", sch, pb, [0.15], trials_for(1, quick),
               region="EU868", timeout=3600.0)
        print(f"  other scenarios {pname} done ({time.time() - t0:.0f}s)", flush=True)
    return out


# ====================================================================== part 3: carousel
def carousel(k, loss, n_listeners, rng, mode, extra=None, burst=False, lt_over=None):
    """Packets a receive-only listener must wait (after tuning in) to hold the page.
    mode: plain (seq 0..K-1 on a loop), coded (fresh repair symbols only after
    the first pass), mixed (alternate one plain pass and one coded pass of K)."""
    res = np.zeros(n_listeners)
    for i in range(n_listeners):
        off = rng.integers(0, 2 * k)
        have = np.zeros(k, bool)
        nsys, nrep = 0, 0
        need = k + (rng.choice(extra) if extra is not None else 0)
        if lt_over is not None:
            need = k + rng.choice(lt_over)
        bad = rng.random() < 0.191
        t = 0
        while True:
            idx = off + t
            if burst:
                lost = rng.random() < (0.70 if bad else 0.02)
                bad = (rng.random() >= 0.25) if bad else (rng.random() < 0.059)
            else:
                lost = rng.random() < loss
            t += 1
            if lost:
                continue
            if mode == "plain":
                s = idx % k
                if not have[s]:
                    have[s] = True
                    nsys += 1
                if nsys == k:
                    break
            elif mode in ("coded", "lt"):
                nrep += 1
                if nrep >= need:
                    break
            else:  # mixed: even passes plain, odd passes coded
                if (idx // k) % 2 == 0:
                    s = idx % k
                    if not have[s]:
                        have[s] = True
                        nsys += 1
                else:
                    nrep += 1
                if nsys + nrep >= need:
                    break
        res[i] = t
    return res


def part3(quick, extra_dist, lt_dist):
    rng = np.random.default_rng(7)
    nl = 150 if quick else 600
    out = {}
    for pname, pb in PAGES.items():
        k = math.ceil(pb / 180)
        for loss in (0.05, 0.15, 0.30, "GE"):
            key = f"{pname} loss={loss if loss == 'GE' else f'{loss:.0%}'}"
            row = {}
            for mode in ("plain", "mixed", "coded", "lt"):
                v = carousel(k, 0 if loss == "GE" else loss, nl, rng, mode, extra_dist,
                             burst=(loss == "GE"),
                             lt_over=lt_dist.get(str(k)) if mode == "lt" else None)
                row[mode] = {"mean": float(v.mean()), "p90": float(np.percentile(v, 90)),
                             "passes_mean": float(v.mean() / k)}
            out[key] = row
    return out


# ====================================================================== part 4: frame budget
def part4():
    hdr_data = rs.HDR + rs.HOST_NAME + rs.CALL_NAME  # 32
    rows = []
    for k in (10, 35, 100, 228, 400):
        seed_frame = hdr_data + gc.HEADER_LEN + 180
        vec_payload = 250 - hdr_data - gc.HEADER_LEN - k  # bytes of data left with full vector
        rows.append({"K": k, "data_frame": hdr_data + 180, "seed_frame": seed_frame,
                     "vector_payload": vec_payload,
                     "vector_eff": max(0, vec_payload) / 180})
    # RESEND capacity: how many seq numbers fit in one RESEND (180 bytes)
    resend = {}
    for k in (35, 228, 364):
        miss = list(range(k))
        rng = random.Random(1)
        worst = len(rs.resend_payload([s for s in miss if s >= 100] or miss)[0])
        resend[str(k)] = {"max_seqs_per_resend": len(rs.resend_payload(sorted(rng.sample(miss, k)))[0]),
                          "max_seqs_if_all_3digit": worst, "bitmap_bytes": math.ceil(k / 8)}
    return {"frames": rows, "resend": resend, "need_payload": 4,
            "air_data": rs.airtime("US915", hdr_data + 180),
            "air_coded": rs.airtime("US915", hdr_data + 184)}


# ====================================================================== charts
def charts(r1, r2, r3):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#52514e", "axes.labelcolor": "#0b0b0b",
                         "xtick.color": "#52514e", "ytick.color": "#52514e",
                         "axes.grid": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.6,
                         "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
                         "lines.linewidth": 2})
    # 1. codec overhead
    ks = [10, 20, 35, 50, 100, 228, 400]
    fig, ax = plt.subplots(figsize=(7, 4))
    for name, key, col in (("RLNC GF(256), 30% loss", lambda k: f"{k}@0.3", COLORS["rlnc"]),
                           ("LT fountain", lambda k: str(k), COLORS["lt"])):
        src = r1["rlnc"] if name.startswith("RLNC") else r1["lt"]
        y = [100 * np.mean(src[key(k)]) / k for k in ks]
        ax.plot(ks, y, marker="o", markersize=7, color=col, label=name)
        ax.annotate(name, (ks[-1], y[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", color="#0b0b0b", fontsize=9)
    ax.set_xscale("log")
    ax.set_xticks(ks, [str(k) for k in ks])
    ax.set_xlabel("source chunks K (180 bytes each)")
    ax.set_ylabel("extra packets needed beyond K (%)")
    ax.set_title("Decode overhead: RLNC needs ~K packets, LT needs 20-55% more", loc="left")
    ax.set_xlim(8, 900)
    fig.tight_layout()
    fig.savefig(HERE / "1-codec-overhead.png", dpi=130)
    plt.close(fig)

    # 2. airtime vs listeners
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=False)
    for ax, pname in zip(axes, PAGES):
        for sch in ("arq", "arq_echo", "arq_fixed", "mc_list", "rlnc"):
            xs, ys, bad = [], [], []
            for n in (1, 5, 20, 100):
                key = f"N={n} loss=15% {pname}"
                d = r2.get(key, {}).get(sch if n > 1 or sch in ("arq", "rlnc") else None)
                if n == 1:
                    d = r2[key]["arq" if sch.startswith("arq") or sch == "mc_list" else "rlnc"]
                if d:
                    xs.append(n)
                    ys.append(d["air"] / 60)
                    bad.append(d["delivered"] < 0.9)
            ax.plot(xs, ys, color=COLORS[sch], label=LABELS[sch])
            for x, y, b in zip(xs, ys, bad):  # hollow marker = under 90% got the page
                ax.plot([x], [y], marker="o", markersize=8, color=COLORS[sch],
                        markerfacecolor="#fcfcfb" if b else COLORS[sch], markeredgewidth=2)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks([1, 5, 20, 100], ["1", "5", "20", "100"])
        ax.set_xlabel("listeners asking for the page at once")
        ax.set_ylabel("channel time used (minutes)")
        ax.set_title(f"{pname} page, 15% loss, US915", loc="left")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.text(0.01, 0.01, "Hollow marker: fewer than 90% of listeners got the page "
             "(channel time then counts until they gave up).", fontsize=8, color="#52514e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(HERE / "2-airtime-vs-listeners.png", dpi=130)
    plt.close(fig)

    # 3. completion time CDF
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, pname in zip(axes, PAGES):
        key = f"N=20 loss=15% {pname}"
        for sch in ("arq", "arq_echo", "arq_fixed", "mc_list", "rlnc"):
            d = np.array(r2[key][sch]["done_all"])
            d = np.sort(np.where(np.isfinite(d), d, 1e9))
            y = np.arange(1, len(d) + 1) / len(d)
            ax.step(d, y, where="post", color=COLORS[sch], label=LABELS[sch])
        ax.set_xscale("log")
        ax.set_xlim(5, 5000)
        ax.set_ylim(0, 1.02)
        ax.set_xlabel("seconds until the listener has the page (log)")
        ax.set_ylabel("share of the 20 listeners")
        ax.set_title(f"20 listeners, {pname}, 15% loss", loc="left")
        g = r2[key]["arq"]["delivered"], r2[key]["arq_echo"]["delivered"]
        ax.text(6, 0.5, f"Gorb today: {100 * g[0]:.0f}% got it\n(+Echo: {100 * g[1]:.0f}%)",
                fontsize=8, color="#52514e")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(HERE / "3-time-to-page.png", dpi=130)
    plt.close(fig)

    # 4. scenarios bar chart (airtime, 40 KB)
    scen = ["N=20 loss=15% 40 KB", "hetero N=20 40 KB", "bursty N=20 40 KB",
            "relay N=20 paced 40 KB"]
    names = ["same 15% loss", "near 3% / far 30%", "bursty (avg 15%)", "behind 1 relay\n(host paced)"]
    schs = ["arq_fixed", "mc_list", "rlnc"]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    w = 0.26
    for i, sch in enumerate(schs):
        ys = [r2[s][sch]["air"] / 60 for s in scen]
        xs = np.arange(len(scen)) + (i - 1) * w
        ax.bar(xs, ys, w * 0.92, color=COLORS[sch], label=LABELS[sch])
        for x, y, s in zip(xs, ys, scen):
            ax.text(x, y * 1.08, f"{100 * r2[s][sch]['delivered']:.0f}%", ha="center",
                    fontsize=7, color="#52514e")
    ax.text(0.99, 0.97, "label above bar = share of listeners that got the page\n"
            "(Gorb today: 0% in all four cases)", transform=ax.transAxes, ha="right", va="top",
            fontsize=8, color="#52514e")
    ax.set_xticks(range(len(scen)), names)
    ax.set_yscale("log")
    ax.set_ylabel("channel time used (minutes, log)")
    ax.set_title("20 listeners want a 40 KB page at once (US915)", loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_ylim(0.3, 300)
    fig.tight_layout()
    fig.savefig(HERE / "4-scenarios.png", dpi=130)
    plt.close(fig)

    # 5. carousel
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, pname in zip(axes, PAGES):
        labels = ["5%", "15%", "30%", "GE"]
        keys = [f"{pname} loss={x}" for x in labels]
        for i, mode in enumerate(("plain", "mixed", "coded", "lt")):
            ys = [r3[k][mode]["passes_mean"] for k in keys]
            ax.bar(np.arange(4) + (i - 1.5) * 0.2, ys, 0.18, color=COLORS[mode],
                   label={"plain": "plain loop (today's DATA)", "mixed": "plain + coded passes",
                          "coded": "coded loop (RLNC)", "lt": "coded loop (LT)"}[mode])
        ax.set_xticks(range(4), ["5% loss", "15% loss", "30% loss", "bursty 15%"])
        ax.set_ylabel("page lengths of airtime until done")
        ax.set_title(f"Gorb Cast carousel, {pname} page", loc="left")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(HERE / "5-carousel.png", dpi=130)
    plt.close(fig)


# ====================================================================== tables
def fmt_t(x):
    if not math.isfinite(x):
        return "never"
    return f"{x:.0f} s" if x < 600 else f"{x / 60:.0f} min"


def tables(r1, r2, r3, r4):
    L = []
    L.append("## Part 1: codec\n")
    L.append("| K | RLNC extra pkts, mean (10% loss) | RLNC P(extra>0) | RLNC extra, mean (30% loss) | LT extra, mean | LT extra, p95 |")
    L.append("|---|---|---|---|---|---|")
    for k in (10, 20, 35, 50, 100, 228, 400):
        a = np.array(r1["rlnc"][f"{k}@0.1"])
        b = np.array(r1["rlnc"][f"{k}@0.3"])
        lt = np.array(r1["lt"][str(k)])
        L.append(f"| {k} | {a.mean():.3f} | {np.mean(np.concatenate([a, b]) > 0):.3f} | {b.mean():.3f} | "
                 f"{lt.mean():.1f} ({100 * lt.mean() / k:.0f}%) | {np.percentile(lt, 95):.0f} |")
    L.append("\n| K | chunks missing (30% loss) | Python encode / repair pkt | Python decode | GF mul-adds to decode | extra RAM for decode |")
    L.append("|---|---|---|---|---|---|")
    for c in r1["cpu"]:
        L.append(f"| {c['K']} | {c['missing']} | {c['py_encode_ms']:.2f} ms | {c['py_decode_ms']:.0f} ms | "
                 f"{c['decode_ops'] / 1e6:.2f} M | {c['dec_extra_bytes'] / 1024:.1f} KB |")
    L.append("\n## Part 2: delivery (channel time = airtime of every packet anyone sent until all listeners had the page or gave up)\n")
    for key, row in r2.items():
        L.append(f"\n### {key}\n")
        L.append("| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |")
        L.append("|---|---|---|---|---|---|---|---|")
        for sch, d in row.items():
            extra = ""
            if d["air_total"] > d["air"] * 1.05:
                extra = f" (+{fmt_t(d['air_total'] - d['air'])} still queued)"
            L.append(f"| {LABELS[sch]} | {fmt_t(d['air'])}{extra} | {100 * d['delivered']:.0f}% | "
                     f"{fmt_t(d['median'])} | {fmt_t(d['p90'])} | {d['ctrl_n']:.0f} | {d['collisions']:.0f} | {d['trials']} |")
    L.append("\n## Part 3: carousel (packets a listener waits after tuning in; 'passes' = multiples of K)\n")
    L.append("| page / loss | plain loop | plain+coded passes | RLNC loop | LT loop |")
    L.append("|---|---|---|---|---|")
    for key, row in r3.items():
        cells = [f"{row[m]['passes_mean']:.2f} (p90 {row[m]['p90'] / (row[m]['mean'] / row[m]['passes_mean']):.2f})"
                 for m in ("plain", "mixed", "coded", "lt")]
        L.append(f"| {key} | " + " | ".join(cells) + " |")
    L.append("\n## Part 4: frame budget\n")
    L.append("| K | DATA frame | coded frame (seed) | data bytes left if the full coefficient vector is sent |")
    L.append("|---|---|---|---|")
    for f in r4["frames"]:
        L.append(f"| {f['K']} | {f['data_frame']} B | {f['seed_frame']} B | {f['vector_payload']} B |")
    L.append("\n| K | seq numbers per RESEND (180 B) | bitmap of all K |")
    L.append("|---|---|---|")
    for k, v in r4["resend"].items():
        L.append(f"| {k} | {v['max_seqs_per_resend']} | {v['bitmap_bytes']} B |")
    L.append(f"\nairtime US915: DATA {r4['air_data'] * 1000:.1f} ms, coded {r4['air_coded'] * 1000:.1f} ms")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print("part 1: codec", flush=True)
    r1 = part1(a.quick)
    extra = [x for k in (35, 228) for x in r1["rlnc"][f"{k}@0.3"] + r1["rlnc"][f"{k}@0.1"]]
    print(f"part 2: delivery simulation ({time.time() - t0:.0f}s)", flush=True)
    r2 = part2(a.quick, extra)
    print(f"part 3: carousel ({time.time() - t0:.0f}s)", flush=True)
    r3 = part3(a.quick, extra, r1["lt"])
    r4 = part4()
    (HERE / "results.json").write_text(json.dumps(
        {"codec": r1, "delivery": r2, "carousel": r3, "frames": r4,
         "quick": a.quick}, default=float))
    (HERE / "results.md").write_text(tables(r1, r2, r3, r4))
    charts(r1, r2, r3)
    print(f"done in {time.time() - t0:.0f}s -> results.md, results.json, *.png")


if __name__ == "__main__":
    main()

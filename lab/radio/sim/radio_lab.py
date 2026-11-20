#!/usr/bin/env python3
"""Gorb virtual radio lab - command line entry point.

    python lab/radio/sim/radio_lab.py --quick        # ~1-2 minutes, 3 seeds
    python lab/radio/sim/radio_lab.py                # full run, 20 seeds (~5 min on 4 cores)
    python lab/radio/sim/radio_lab.py --only relay esp32 --seeds 4
    python lab/radio/sim/radio_lab.py --check        # self-checks only

Writes to lab/radio/sim/results/:
    checks.json / checks.md   airtime, ALOHA and range self-checks
    <experiment>.md           results table (mean +- std over seeds)
    <experiment>.png          bar charts (success, load time, airtime)
    results.json              every number from every run
    phy.png, aloha.png, map.png

Using the lab from your own code:

    import sys; sys.path.insert(0, "lab/radio")
    from sim import scenario, phy
    from sim.proto import Cfg
    scn = scenario.make(seed=1, n=40, size=2000)
    print(scenario.run(scn, Cfg(relay="route", use_wifi=False), seed=1))
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sim import experiments as ex  # noqa: E402
from sim import phy, scenario  # noqa: E402
from sim.world import link_range  # noqa: E402

OUT = ex.RESULTS
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": INK2,
    "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9,
})


def fmt(m, s, nd=1, pct=False):
    if m is None or (isinstance(m, float) and math.isnan(m)):
        return "-"
    if pct:
        return f"{100 * m:.0f}% ±{100 * s:.0f}"
    return f"{m:.{nd}f} ±{s:.{nd}f}"


# ---------------------------------------------------------------- checks
def run_checks(log):
    air = ex.check_airtime()
    aloha = ex.check_aloha()
    csma = ex.check_aloha((0.25, 0.5, 1.0, 2.0), csma=True)
    ranges = ex.check_ranges()
    with open(os.path.join(OUT, "checks.json"), "w") as f:
        json.dump({"airtime": air, "aloha": aloha, "csma": csma, "ranges": ranges}, f, indent=1)
    lines = ["# Self-checks", "", "## LoRa airtime (Semtech formula) vs. published calculator values", "",
             "| mode | payload bytes | expected ms | lab ms |", "|---|---|---|---|"]
    for r in air:
        lines.append(f"| {r['mode']} | {r['bytes']} | {r['expected_ms'] or '-'} | {r['sim_ms']} |")
    lines += ["", "## Pure ALOHA throughput (20 senders, no capture, no carrier sense)", "",
              "| offered load G | lab S | theory G·e^-2G |", "|---|---|---|"]
    for r in aloha:
        lines.append(f"| {r['G']} | {r['S_sim']} | {r['S_theory']} |")
    lines += ["", "With carrier sense (CAD) on: " + ", ".join(f"G={r['G']}: S={r['S_sim']}" for r in csma), "",
              "## Modes: sensitivity and median range (path-loss exponent 3.3, no shadowing)", "",
              "| mode | sensitivity dBm | kbit/s | airtime of a 215-byte frame (ms) | outdoor-outdoor m | one end indoors m | both indoors m |",
              "|---|---|---|---|---|---|---|"]
    for r in ranges:
        lines.append(f"| {r['mode']} | {r['sens_dBm']} | {r['kbit_s']} | {r['air_215B_ms']} | "
                     f"{r['range_out_m']} | {r['range_1in_m']} | {r['range_2in_m']} |")
    with open(os.path.join(OUT, "checks.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    log("\n".join(lines))

    # ALOHA chart
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    G = np.linspace(0.02, 2.2, 100)
    ax.plot(G, G * np.exp(-2 * G), color=INK2, lw=2, label="theory, pure ALOHA")
    ax.plot([r["G"] for r in aloha], [r["S_sim"] for r in aloha], "o", color=BLUE, ms=7, label="lab, no carrier sense")
    ax.plot([r["G"] for r in csma], [r["S_sim"] for r in csma], "s", color=ORANGE, ms=7, label="lab, with CAD")
    ax.set_xlabel("offered load G (frames per frame-time)")
    ax.set_ylabel("throughput S")
    ax.set_title("Self-check: ALOHA throughput", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "aloha.png"), dpi=130)
    plt.close(fig)

    # PER curves + range chart
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6))
    modes = [phy.US915, phy.lora(7, 500), phy.EU868, phy.ESPNOW, phy.WIFI_LR250, phy.wifi("g24")]
    cols = [BLUE, AQUA, YELLOW, ORANGE, "#6250d6", "#e87ba4"]
    for m, c in zip(modes, cols):
        x = np.linspace(m.sens_dbm - 8, m.sens_dbm + 8, 200)
        a1.plot(x, m.p_ok(x - m.noise_dbm, 215), color=c, lw=2, label=m.name)
    a1.set_xlabel("received power (dBm)")
    a1.set_ylabel("P(215-byte frame received)")
    a1.set_title("Packet success vs signal (model)", loc="left")
    a1.legend(frameon=False, fontsize=7)
    rr = [(m.name, link_range(m, 3.3, 1)) for m in modes]
    a2.barh([n for n, _ in rr], [r for _, r in rr], color=BLUE, height=0.6)
    for i, (_, r) in enumerate(rr):
        a2.text(r, i, f" {r:.0f} m", va="center", color=INK2, fontsize=8)
    a2.set_xlabel("median range, one end indoors (m)")
    a2.set_title("Range by mode (median, n = 3.3)", loc="left")
    a2.invert_yaxis()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "phy.png"), dpi=130)
    plt.close(fig)


def draw_map():
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6))
    for ax, (name, kw) in zip(axes, [("suburb (LoRa)", ex.SCN["suburb1"]), ("mixed town", ex.SCN["mixed"])]):
        scn = scenario.make(1, **kw)
        w = scn.world
        pos = w.pos()
        modes = kw.get("modes", (phy.US915,))
        for m, c in zip(modes, [BLUE, ORANGE]):
            adj = scenario.hop_graph(w, [m])
            for i, j in zip(*np.nonzero(np.triu(adj))):
                ax.plot(pos[[i, j], 0], pos[[i, j], 1], color=c, lw=0.5, alpha=0.35)
        lo = w.has("sub")
        ax.scatter(pos[lo, 0], pos[lo, 1], s=22, color=BLUE, label="LoRa + Wi-Fi board", zorder=3)
        ax.scatter(pos[~lo, 0], pos[~lo, 1], s=22, color=ORANGE, label="plain ESP32", zorder=3)
        for p, (h, _) in scn.pages.items():
            ax.scatter(pos[h, 0], pos[h, 1], s=110, facecolor="none", edgecolor=INK, lw=1.5, zorder=4)
        ax.set_title(f"{name}: links with 3 dB median margin (rings = hosts)", loc="left", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlabel("m")
        ax.legend(frameon=False, fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "map.png"), dpi=130)
    plt.close(fig)


# ------------------------------------------------------------ experiments
SCN_TEXT = {
    "suburb1": "1 load of a 6 KB page",
    "suburb4": "4 loads of 6 KB pages within 20 s",
    "suburb40k": "1 load of a 40 KB page",
    "busy8": "8 hosts, 8 loads within 10 s",
    "popular": "20 loads of one page over 5 min",
    "mixed": "mixed town, 3 loads",
    "esp32": "plain ESP32s, 3 loads",
}


def tables_and_charts(E, out, seeds, log):
    allres = {}
    for name, d in out.items():
        rows = []
        for (label, scn), runs in d.items():
            a = ex.aggregate(runs)
            tall = [r["t_med_all"] for r in runs]
            w60 = [r["within60"] for r in runs]
            rows.append((label, scn, a, float(np.median(tall)), float(np.mean(w60))))
            allres.setdefault(name, []).append({"label": label, "scenario": scn, "runs": runs})
        lines = [f"# {E[name]['title']}", "", f"{seeds} random seeds per row; mean ± standard deviation across seeds.",
                 "Load time = median over the loads that finished (failures excluded); "
                 "'within 60 s' = share of all loads finished within 60 s.", ""]
        for scn in dict.fromkeys(r[1] for r in rows):
            lines += [f"## {scn}: {SCN_TEXT.get(scn, '')}", "",
                      "| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |",
                      "|---|---|---|---|---|---|---|"]
            for label, s, a, tall, w60 in rows:
                if s != scn:
                    continue
                lines.append(f"| {label} | {fmt(*a['success'], pct=True)} | {fmt(*a['t_median'])} | "
                             f"{100 * w60:.0f}% | {fmt(*a['air_sub'], nd=0)} | {fmt(*a['air_2g4'], nd=1)} | "
                             f"{fmt(*a['energy_j'], nd=0)} |")
            lines.append("")
        with open(os.path.join(OUT, f"{name}.md"), "w") as f:
            f.write("\n".join(lines))
        log("\n".join(lines))
        chart(name, E[name]["title"], rows)
    path = os.path.join(OUT, "results.json")
    old = {}
    if os.path.exists(path):  # keep experiments not rerun this time (--only)
        try:
            with open(path) as f:
                old = json.load(f)
        except ValueError:
            old = {}
    old.update(allres)
    with open(path, "w") as f:
        json.dump(old, f, default=float)


def chart(name, title, rows):
    scns = list(dict.fromkeys(r[1] for r in rows))
    metrics = [("success", "pages delivered (%)", 100), ("t_median", "load time (s), finished loads", 1),
               ("air", "total airtime (s)", 1)]
    nrow = len(scns)
    maxv = max(sum(1 for r in rows if r[1] == s) for s in scns)
    fig, axes = plt.subplots(nrow, 3, figsize=(13, 0.9 + nrow * (0.32 * maxv + 0.8)), squeeze=False)
    for i, s in enumerate(scns):
        rs = [r for r in rows if r[1] == s]
        labels = [r[0] for r in rs]
        y = np.arange(len(rs))
        for j, (key, xl, mult) in enumerate(metrics):
            ax = axes[i, j]
            if key == "air":
                m = [r[2]["air_sub"][0] + r[2]["air_2g4"][0] for r in rs]
                sd = [math.hypot(r[2]["air_sub"][1], r[2]["air_2g4"][1]) for r in rs]
            else:
                m = [r[2][key][0] * mult for r in rs]
                sd = [r[2][key][1] * mult for r in rs]
            m = [0 if (isinstance(v, float) and math.isnan(v)) else v for v in m]
            sd = [0 if (isinstance(v, float) and math.isnan(v)) else v for v in sd]
            colors = [ORANGE if k == 0 else BLUE for k in range(len(rs))]
            hi = [min(s_, 100 - v) if key == "success" else s_ for v, s_ in zip(m, sd)]
            lo = [min(s_, v) for v, s_ in zip(m, sd)]
            ax.barh(y, m, xerr=[lo, hi], color=colors, height=0.62, error_kw={"ecolor": INK2, "lw": 1, "capsize": 2})
            ax.set_yticks(y)
            ax.set_yticklabels(labels if j == 0 else [""] * len(labels), fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel(xl)
            ax.grid(axis="y", visible=False)
            if j == 0:
                ax.set_title(f"{s}: {SCN_TEXT.get(s, '')}", loc="left", fontsize=9)
    fig.suptitle(title + "   (orange = today's Gorb / reference; bars = mean, lines = ±1 sd)",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f"{name}.png"), dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="3 seeds (about 1-2 minutes)")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--only", nargs="*", help="experiments: " + " ".join(ex.experiments()))
    ap.add_argument("--check", action="store_true", help="self-checks only")
    ap.add_argument("--procs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    run_checks(print)
    draw_map()
    if a.check:
        return
    seeds = a.seeds or (3 if a.quick else 20)
    E, out = ex.run_experiments(a.only, seeds=seeds, procs=a.procs)
    tables_and_charts(E, out, seeds, print)
    print(f"\nall done in {time.time() - t0:.0f} s; results in {OUT}")


if __name__ == "__main__":
    main()

"""Charts for modem_lab.py (matplotlib, PNG)."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STYLE = {
    "afsk": dict(color="#52514e", lw=2.6, marker="s"),
    "4fsk-1/2": dict(color=SERIES[1], lw=1.8, ls="--", marker="v"),
    "4fsk-3/4": dict(color=SERIES[3], lw=1.8, ls="--", marker="^"),
    "qpsk-1/2": dict(color=SERIES[0], lw=2, marker="o"),
    "qpsk-3/4": dict(color=SERIES[2], lw=2, marker="o"),
    "16qam-1/2": dict(color=SERIES[4], lw=2, marker="D"),
    "16qam-3/4": dict(color=SERIES[6], lw=2, marker="D"),
    "64qam-2/3": dict(color=SERIES[5], lw=2, marker="P"),
    "64qam-3/4": dict(color=SERIES[7], lw=2, marker="P"),
}
OFDM = ["qpsk-1/2", "qpsk-3/4", "16qam-1/2", "16qam-3/4", "64qam-2/3", "64qam-3/4"]
CHAN_TITLE = {
    "awgn": "Channel 1: clean audio + noise (SNR vs the signal's own power)",
    "fm": "Channel 2: FM radio audio path (SNR vs a full-deviation tone)",
    "fm+dropouts": "Channel 3: FM path + short dropouts (1/s, 5-30 ms)",
}


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2)


def _series(res, chan, key, field):
    pts = sorted((r["snr"], r[field]) for r in res if r["channel"] == chan and r["modem"] == key)
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def adaptive(res, chan, keys=OFDM):
    snrs = sorted({r["snr"] for r in res if r["channel"] == chan})
    best = []
    for s in snrs:
        best.append(max((r["goodput"] for r in res if r["channel"] == chan and r["snr"] == s and r["modem"] in keys), default=0))
    return np.array(snrs), np.array(best)


def plot_all(data, out, suffix=""):
    res, names = data["results"], data["meta"]["names"]
    keys = list(STYLE)
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 11, "text.color": INK, "axes.labelcolor": INK})
    for chan in ("awgn", "fm", "fm+dropouts"):
        if not any(r["channel"] == chan for r in res):
            continue
        tag = chan.replace("+", "_")
        # goodput
        fig, ax = plt.subplots(figsize=(9, 5.4), facecolor=SURFACE)
        _style(ax)
        for k in keys:
            x, y = _series(res, chan, k, "goodput")
            if len(x):
                ax.plot(x, y, label=names[k], ms=5, **STYLE[k])
        x, y = adaptive(res, chan)
        ax.plot(x, y, color=INK, lw=1.4, ls=":", label="Modem 2 adaptive (best OFDM mode)", zorder=0)
        x2, y2 = adaptive(res, chan, OFDM + ["4fsk-1/2", "4fsk-3/4"])
        if np.any(y2 > y):
            ax.plot(x2, y2, color=INK2, lw=1.0, ls="-.", label="adaptive incl. 4FSK fallback", zorder=0)
        ax.set_xlabel("SNR in 300-3000 Hz (dB)")
        ax.set_ylabel("goodput, 220-byte packets (bit/s)")
        ax.set_title(CHAN_TITLE[chan] + "\ngoodput = payload delivered / airtime incl. 250 ms leader", loc="left")
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=8, frameon=False, loc="upper left")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"goodput_{tag}{suffix}.png"), dpi=120)
        plt.close(fig)
        # FER
        fig, ax = plt.subplots(figsize=(10.5, 5.4), facecolor=SURFACE)
        _style(ax)
        for k in keys:
            x, y = _series(res, chan, k, "fer")
            if len(x):
                y = np.where(y > 0, y, np.nan)  # 0 errors cannot be drawn on a log axis
                ax.semilogy(x, y, label=names[k], ms=5, **STYLE[k])
        nf = data["meta"]["frames_per_point"]
        ax.axhline(1 / nf, color=INK2, lw=0.8, ls=":")
        ax.text(ax.get_xlim()[0] + 0.3, 1 / nf * 1.15, f"1 lost frame in {nf} (points below = no errors seen)", fontsize=8, color=INK2)
        ax.set_ylim(0.5 / nf, 1.3)
        ax.set_xlabel("SNR in 300-3000 Hz (dB)")
        ax.set_ylabel("frame error rate")
        ax.set_title(CHAN_TITLE[chan], loc="left")
        ax.legend(fontsize=8, frameon=False, loc="center left", bbox_to_anchor=(1.0, 0.5))
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"fer_{tag}{suffix}.png"), dpi=120)
        plt.close(fig)
    # robustness: drive level and clock offset at 30 dB on the FM path
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.4), facecolor=SURFACE)
    for ax, chan, field, xl in ((axs[0], "fm-drive", "drive_db", "drive level vs 'peaks just reach limiter' (dB)"),
                                (axs[1], "fm-ppm", "ppm", "sound-card clock offset (ppm)")):
        _style(ax)
        for k in keys:
            pts = sorted((r["extra"][field], r["goodput"]) for r in res if r["channel"] == chan and r["modem"] == k)
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], label=names[k], ms=5, **STYLE[k])
        ax.set_xlabel(xl)
        ax.set_ylabel("goodput (bit/s)")
        ax.set_ylim(bottom=0)
        ax.set_title("FM path, SNR 30 dB", loc="left")
    axs[0].legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(out, f"robustness{suffix}.png"), dpi=120)
    plt.close(fig)

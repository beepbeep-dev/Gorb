"""Self-checks and the idea experiments used in REPORT.md."""

from __future__ import annotations

import json
import math
import os
import time
import warnings
from dataclasses import replace
from multiprocessing import Pool

import numpy as np

from . import phy, scenario
from .engine import Iface, Sim
from .proto import Cfg, Pkt
from .world import build, link_range

warnings.filterwarnings("ignore", category=RuntimeWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

# --------------------------------------------------------------------------
# Self-checks
# --------------------------------------------------------------------------


def check_airtime():
    """LoRa airtime against values from the Semtech LoRa calculator / TTN
    airtime calculator (explicit header, CRC on, CR 4/5, 8-symbol preamble)."""
    known = [  # (sf, bw kHz, payload bytes, expected ms)
        (7, 125, 20, 56.6), (12, 125, 20, 1318.9), (9, 125, 20, 185.3), (10, 125, 51, 616.4),
    ]
    rows = []
    for sf, bw, n, ms in known:
        got = phy.lora_airtime(n, sf, bw * 1e3) * 1e3
        rows.append({"mode": f"SF{sf}/{bw}k", "bytes": n, "expected_ms": ms, "sim_ms": round(got, 1)})
    for m in (phy.US915, phy.EU868):
        rows.append({"mode": m.name + " (Gorb)", "bytes": 215, "expected_ms": None,
                     "sim_ms": round(m.airtime(215) * 1e3, 1)})
    return rows


class _Dummy:
    def __init__(self):
        self.ok = 0
        self.air = 0.0

    def on_rx(self, pkt, tx, rssi, snr):
        self.ok += 1
        self.air += tx.t1 - tx.t0


def check_aloha(G_values=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0), csma=False, seed=0, T=400.0):
    """Throughput of N senders around one receiver. Pure ALOHA theory:
    S = G e^{-2G}, max 0.184 at G = 0.5."""
    out = []
    n = 21
    rng = np.random.default_rng(seed)
    pos = np.column_stack([rng.uniform(-30, 30, n), rng.uniform(-30, 30, n)])
    pos[0] = (0, 0)
    w = build(positions=pos, sigma=0.0, indoor_frac=0.0, rician_k=-1, seed=seed)
    mode = replace(phy.US915, capture_db=99.0)  # no capture: textbook ALOHA
    size = 50
    air = mode.airtime(size)
    for G in G_values:
        sim = Sim(w, seed, bg={"sub": (0.0, 1e12), "2g4": (0.0, 1e12)}, fading=False)
        sink = _Dummy()
        for i in range(n):
            sim.nodes[i] = sink if i == 0 else _Dummy()
        ifs = [Iface(sim, i, "sub", mode) for i in range(n)]
        rate = G / air / (n - 1)
        for i in range(1, n):
            t = 0.0
            while t < T:
                t += rng.exponential(1 / rate)
                sim.at(t, _fire, sim, ifs[i], mode, size, csma)
        sim.run(until=T)
        out.append({"G": G, "S_sim": round(sink.air / T, 3),
                    "S_theory": round(G * math.exp(-2 * G), 3) if not csma else None})
    return out


def _fire(sim, ifc, mode, size, csma):
    p = Pkt(3, ifc.sid, -1, 0, size=size)
    if csma:
        ifc.send(p)
    elif ifc.state != "tx":
        ifc.transmit_now(p)


def check_ranges():
    rows = []
    for m in [phy.lora(7, 125), phy.lora(12, 125), phy.lora(5, 500), phy.lora(7, 500), phy.US915,
              phy.EU868, phy.WIFI_LR250, phy.WIFI_LR500, phy.ESPNOW, phy.wifi("b11"),
              phy.wifi("g54")]:
        rows.append({"mode": m.name, "sens_dBm": round(m.sens_dbm, 1),
                     "kbit_s": round(m.bitrate / 1e3, 1),
                     "air_215B_ms": round(m.airtime(215) * 1e3, 2),
                     "range_out_m": round(link_range(m, 3.3, 0)),
                     "range_1in_m": round(link_range(m, 3.3, 1)),
                     "range_2in_m": round(link_range(m, 3.3, 2))})
    return rows


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------

LORA_ONLY = dict(use_wifi=False)
SCN = {
    # LoRa suburb: 60 stations over 3 x 3 km, all with LoRa boards
    "suburb1": dict(n=60, size=3000, n_hosts=4, n_loads=1),
    "suburb4": dict(n=60, size=3000, n_hosts=4, n_loads=4, window=20.0),
    "suburb40k": dict(n=60, size=3000, n_hosts=4, n_loads=1, page_bytes=40 * 1024),
    # many hosts, many simultaneous short loads (multi-channel test)
    "busy8": dict(n=80, size=3000, n_hosts=8, n_loads=8, window=10.0, hop_range=(1, 2)),
    # popular page: 20 people load the same page over 5 minutes
    "popular": dict(n=60, size=3000, n_hosts=1, n_loads=20, window=300.0, same_page=True),
    # mixed town: 80 stations over 2 x 2 km, 30% have LoRa boards, the rest plain ESP32
    "mixed": dict(n=80, size=2000, lora_frac=0.3, n_hosts=3, n_loads=3, window=15.0,
                  modes=(phy.US915, phy.ESPNOW), host_lora=False),
    # plain ESP32 only (the user's case): 50 stations in a 700 m neighbourhood
    "esp32": dict(n=50, size=700, layout="city", lora_frac=0.0, n_hosts=3, n_loads=3,
                  window=15.0, modes=(phy.ESPNOW,), host_lora=False),
}


def V(label, scn, cfg=None, sim=None, **kw):
    return {"label": label, "scn": scn, "cfg": cfg or {}, "sim": sim or {}, **kw}


def experiments():
    L = LORA_ONLY
    E = {}
    E["relay"] = {
        "title": "Idea 1/2: how relays repeat packets (LoRa-only suburb)",
        "variants": [],
    }
    for scn in ("suburb1", "suburb4", "suburb40k"):
        to = 900.0
        L = dict(LORA_ONLY, retries=20) if scn == "suburb40k" else LORA_ONLY
        E["relay"]["variants"] += [
            V("today's Gorb (flood, node.py fixes)", scn, dict(L, timeout=to)),
            V("Gorb v0.1 flood (before fixes)", scn, dict(L, timeout=120.0 if scn != "suburb40k" else to,
                                                          v2=False)),
            V("RSSI timer (Meshtastic-like)", scn, dict(L, relay="rssi", suppress=1, timeout=to)),
            V("gradient (ETX, closest first)", scn, dict(L, relay="gradient", timeout=to)),
            V("route (unicast reverse path)", scn, dict(L, relay="route", timeout=to)),
            V("CT flood (Glossy-style)", scn, dict(L, relay="ct", timeout=to)),
            V("CT flood, pipelined (3 slots)", scn, dict(L, relay="ct", ct_spacing=3, timeout=to)),
            V("route + host pacing", scn, dict(L, relay="route", pace=1.0, timeout=to)),
        ]
    L = LORA_ONLY
    E["ct"] = {
        "title": "Idea 2: how sensitive concurrent-transmission flooding is to timing",
        "variants": [
            V("CT, 30 us jitter", "suburb1", dict(L, relay="ct")),
            V("CT, 150 us jitter", "suburb1", dict(L, relay="ct", ct_jitter=150e-6)),
            V("CT, 1 ms jitter (> 1 symbol)", "suburb1", dict(L, relay="ct", ct_jitter=1e-3)),
            V("CT, 30 us, 30% beating loss", "suburb1", dict(L, relay="ct"), {"ct_penalty": 0.3}),
            V("CT, tolerance 0.25 symbol", "suburb1", dict(L, relay="ct"), {"ct_tol": 128e-6}),
        ],
    }
    E["repair"] = {
        "title": "Ideas 7/8: repairing lost chunks (combining, coding, local repair, fast NACK)",
        "variants": [],
    }
    for scn in ("suburb1", "suburb4"):
        E["repair"]["variants"] += [
            V("today's Gorb (flood)", scn, dict(L)),
            V("+ packet combining", scn, dict(L, combining=True)),
            V("+ fountain coding 10%", scn, dict(L, coding=True, redundancy=0.1)),
            V("+ local repair", scn, dict(L, local_repair=True)),
            V("+ fast repair", scn, dict(L, fast_repair=True)),
            V("gradient + fast repair", scn, dict(L, relay="gradient", fast_repair=True)),
            V("gradient + fast + coding", scn, dict(L, relay="gradient", fast_repair=True,
                                                    coding=True, redundancy=0.1)),
            V("gradient + fast + combining", scn, dict(L, relay="gradient", fast_repair=True,
                                                       combining=True)),
            V("route + coding", scn, dict(L, relay="route", coding=True, redundancy=0.1)),
            V("route + coding + pacing", scn, dict(L, relay="route", coding=True, redundancy=0.1,
                                                   pace=1.0)),
        ]
    E["sf"] = {
        "title": "Idea 4: LoRa data rate (network-wide preset, and per-link adaptive)",
        "variants": [],
    }
    for sf in (8, 6, 7, 9):
        m = phy.lora(sf, 500)
        E["sf"]["variants"].append(V(f"flood, SF{sf}/500k", "suburb4", dict(L, lora_mode=m)))
    for sf in (8, 6, 7, 9):
        m = phy.lora(sf, 500)
        E["sf"]["variants"].append(V(f"gradient, SF{sf}/500k", "suburb4",
                                     dict(L, lora_mode=m, relay="gradient")))
    E["sf"]["variants"] += [
        V("route, SF8 fixed", "suburb4", dict(L, relay="route")),
        V("route, per-hop SF5-8 (multi-SF rx, upper bound)", "suburb4",
          dict(L, relay="route", adapt=True, lora_rates=(5, 6, 7)),
          {"multi_sf_rx": True}),
    ]
    E["multiradio"] = {
        "title": "Idea 3: LoRa + Wi-Fi together (mixed town, 30% LoRa boards)",
        "variants": [
            V("baseline: flood on every radio (firmware)", "mixed", {}),
            V("LoRa only (Wi-Fi off)", "mixed", dict(use_wifi=False)),
            V("ESP-NOW only (LoRa off)", "mixed", dict(use_lora=False)),
            V("flood, Wi-Fi in LR 250k", "mixed", dict(wifi_mode=phy.WIFI_LR250)),
            V("route, best radio per hop", "mixed", dict(relay="route")),
            V("route, best radio + Wi-Fi rate per hop", "mixed",
              dict(relay="route", adapt=True,
                   wifi_rates=("b1", "b2", "b5.5", "b11", "g6", "g12", "g24", "g54"))),
            V("gradient (both radios)", "mixed", dict(relay="gradient")),
        ],
    }
    big = dict(chunk=1400)
    E["esp32"] = {
        "title": "Plain ESP32 only (no LoRa): ESP-NOW options",
        "variants": [
            V("baseline: ESP-NOW 1M flood, 180 B chunks", "esp32", dict(use_lora=False)),
            V("Wi-Fi LR 250k flood", "esp32", dict(use_lora=False, wifi_mode=phy.WIFI_LR250)),
            V("Wi-Fi LR 500k flood", "esp32", dict(use_lora=False, wifi_mode=phy.WIFI_LR500)),
            V("ESP-NOW v2 1400 B chunks, flood", "esp32", dict(use_lora=False, **big)),
            V("LR 250k + 1400 B chunks, flood", "esp32",
              dict(use_lora=False, wifi_mode=phy.WIFI_LR250, **big)),
            V("gradient", "esp32", dict(use_lora=False, relay="gradient")),
            V("route + per-hop rate + ACK", "esp32",
              dict(use_lora=False, relay="route", adapt=True,
                   wifi_rates=("lr250", "lr500", "b1", "b2", "b5.5", "b11", "g6", "g12", "g24", "g54"))),
            V("route + per-hop rate + 1400 B", "esp32",
              dict(use_lora=False, relay="route", adapt=True,
                   wifi_rates=("lr250", "lr500", "b1", "b2", "b5.5", "b11", "g6", "g12", "g24", "g54"),
                   **big)),
        ],
    }
    E["channels"] = {
        "title": "Idea 5: several LoRa channels (8 hosts, 8 loads at once)",
        "variants": [
            V("flood, 1 channel", "busy8", dict(L)),
            V("flood, 4 channels (host cells)", "busy8", dict(L, lora_channels=4)),
            V("flood, 8 channels (host cells)", "busy8", dict(L, lora_channels=8)),
            V("gradient, 1 channel", "busy8", dict(L, relay="gradient")),
            V("gradient, 4 channels", "busy8", dict(L, relay="gradient",
                                                        lora_channels=4)),
        ],
    }
    E["popular"] = {
        "title": "Idea 6: a popular page (20 loads of one page in 5 minutes)",
        "variants": [
            V("today's Gorb: pull + Echo", "popular", dict(L)),
            V("pull, no Echo", "popular", dict(L, echo=False)),
            V("page-addressed + coalescing", "popular", dict(L, page_addr=True)),
            V("page-addressed + coding", "popular", dict(L, page_addr=True, coding=True, redundancy=0.1)),
            V("push carousel every 60 s", "popular", dict(L, page_addr=True), carousel=60.0),
            V("page-addressed + coding + route", "popular",
              dict(L, page_addr=True, coding=True, redundancy=0.1, relay="route")),
        ],
    }
    E["stack"] = {
        "title": "Best combination vs. today's Gorb",
        "variants": [],
    }
    best = dict(relay="route", coding=True, redundancy=0.1, pace=1.0)
    for scn in ("suburb1", "suburb4", "suburb40k", "mixed", "popular"):
        to = 900.0
        base = dict(LORA_ONLY) if scn != "mixed" else {}
        if scn == "suburb40k":
            base["retries"] = 20
        E["stack"]["variants"] += [
            V("today's Gorb", scn, dict(base, timeout=to)),
            V("Smart stack: route + coding + pacing", scn, dict(base, timeout=to, **best)),
        ]
        if scn in ("suburb4", "popular"):
            E["stack"]["variants"] += [
                V("Smart stack + page-addressed", scn, dict(base, timeout=to, page_addr=True, **best)),
            ]
            if scn == "suburb4":
                E["stack"]["variants"].append(
                    V("Smart stack + 4 LoRa channels", scn, dict(base, timeout=to, lora_channels=4, **best)))
            E["stack"]["variants"] += [
                V("Smart stack + SF6", scn, dict(base, timeout=to, lora_mode=phy.lora(6, 500), **best)),
            ]
    return E


def _run_one(args):
    var, seed = args
    kw = SCN[var["scn"]]
    scn = scenario.make(seed, **kw)
    cfg_kw = dict(var["cfg"])
    cfg = Cfg(**cfg_kw)
    extra = None
    if var.get("carousel"):
        period = var["carousel"]

        def extra(sim, net, period=period):
            host = net.pages[0][0]
            sim.at(0.5, net.nodes[host].carousel, 0, period, scn.loads[-1][0])
    t = time.time()
    r = scenario.run(scn, cfg, seed, var["sim"], extra)
    r["wall"] = time.time() - t
    return var["label"], var["scn"], seed, r


def run_experiments(names=None, seeds=6, procs=4, log=print):
    E = experiments()
    names = names or list(E)
    jobs = []
    for name in names:
        for v in E[name]["variants"]:
            for s in range(seeds):
                jobs.append((name, v, s))
    log(f"running {len(jobs)} simulations on {procs} processes ...")
    t0 = time.time()
    with Pool(procs) as pool:
        res = pool.map(_run_one, [(v, s) for _, v, s in jobs], chunksize=1)
    log(f"done in {time.time() - t0:.0f} s")
    out = {}
    for (name, v, s), (label, scn, seed, r) in zip(jobs, res):
        out.setdefault(name, {}).setdefault((label, scn), []).append(r)
    return E, out


def aggregate(runs):
    keys = ["success", "t_median", "air_sub", "air_2g4", "collisions", "energy_j", "tx"]
    agg = {}
    for k in keys:
        a = np.array([r[k] for r in runs], float)
        agg[k] = (float(np.nanmean(a)), float(np.nanstd(a)))
    # load time with failures counted at the timeout (so failing is not "fast")
    return agg

"""Scenarios (who is where, who hosts what, who loads what when) and the
runner that plays one scenario with one protocol configuration."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace

import numpy as np

from . import phy
from .engine import Sim
from .proto import Cfg, Net
from .world import build


@dataclass
class Scenario:
    world: object
    pages: dict  # page -> (host sid, bytes)
    loads: list  # (t, requester, page)
    home: dict  # sid -> LoRa home channel (multi-channel idea)
    meta: dict


def hop_graph(world, modes, margin=3.0):
    """Median-signal adjacency: a link exists if the average received power is
    `margin` dB above sensitivity on at least one shared radio."""
    n = world.n
    adj = np.zeros((n, n), bool)
    for m in modes:
        has = world.has(m.band)
        ok = (m.tx_dbm + world.gain[m.band]) >= m.sens_dbm + margin
        adj |= ok & has[:, None] & has[None, :]
    np.fill_diagonal(adj, False)
    return adj


def hops_from(adj, src):
    n = len(adj)
    d = np.full(n, 99)
    d[src] = 0
    q = deque([src])
    while q:
        u = q.popleft()
        for v in np.nonzero(adj[u])[0]:
            if d[v] == 99:
                d[v] = d[u] + 1
                q.append(v)
    return d


def make(seed, n=60, size=3000.0, layout="uniform", lora_frac=1.0, n_hosts=4, n_loads=6,
         page_bytes=6 * 1024, window=20.0, hop_range=(1, 3), modes=(phy.US915,),
         exponent=3.3, sigma=7.0, rician_k=3.0, lora_channels=1, same_page=False,
         indoor_frac=0.7, host_lora=True):
    world = build(n, size, layout, seed, exponent, sigma, rician_k, indoor_frac, lora_frac)
    rng = np.random.default_rng(seed * 31 + 5)
    adj = hop_graph(world, modes)
    cand_hosts = [i for i in range(n) if (not host_lora or "sub" in world.stations[i].radios
                                          or not any(m.band == "sub" for m in modes))]
    hosts, dist = [], {}
    rng.shuffle(cand_hosts)
    for h in cand_hosts:
        d = hops_from(adj, h)
        if np.sum((d >= hop_range[0]) & (d <= hop_range[1])) >= 3:
            hosts.append(h)
            dist[h] = d
        if len(hosts) >= n_hosts:
            break
    pages = {i: (h, page_bytes) for i, h in enumerate(hosts)}
    loads = []
    for k in range(n_loads):
        page = 0 if same_page else k % len(pages)
        h = pages[page][0]
        d = dist[h]
        ok = np.nonzero((d >= hop_range[0]) & (d <= hop_range[1]))[0]
        r = int(rng.choice(ok))
        loads.append((float(rng.uniform(0, window)), r, page))
    loads.sort()
    # multi-channel: hosts get channels round robin; others join the cell of
    # the host they hear best
    home = channel_cells(world, pages, lora_channels)
    return Scenario(world, pages, loads, home, {"seed": seed, "n": n, "size": size,
                                                "layout": layout, "lora_frac": lora_frac})


def channel_cells(world, pages, k):
    """Multi-channel idea: hosts get LoRa channels round robin; every other
    station listens on the channel of the host it hears best ("cells")."""
    home = {}
    if k <= 1:
        return home
    hl = list(pages.values())
    for i, (h, _) in enumerate(hl):
        home[h] = i % k
    g = world.gain["sub"]
    for s in range(world.n):
        if s not in home:
            best = max(hl, key=lambda hp: g[s, hp[0]])
            home[s] = home[best[0]]
    return home


def run(scn: Scenario, cfg: Cfg, seed=0, sim_kw=None, extra=None):
    sim = Sim(scn.world, seed, **(sim_kw or {}))
    home = channel_cells(scn.world, scn.pages, cfg.lora_channels) if cfg.lora_channels > 1 else {}
    net = Net(sim, cfg, scn.pages, home)
    for t, r, page in scn.loads:
        sim.at(t, net.nodes[r].start_fetch, page)
    if extra:
        extra(sim, net)
    last = scn.loads[-1][0] if scn.loads else 0.0
    want = len(scn.loads)

    def stop():
        return sim.now > last and len(net.fetches) >= want and all(f.done for _, f in net.fetches)

    sim.run(until=last + cfg.timeout + 30, stop=stop)
    return summarize(sim, net, scn)


def summarize(sim, net, scn):
    times, ok, echo = [], 0, 0
    for _, f in net.fetches:
        if f.ok:
            ok += 1
            times.append(f.t_done - f.t0)
            echo += f.echo
    n = max(len(net.fetches), 1)
    allt = [f.t_done - f.t0 if f.ok else float("inf") for _, f in net.fetches]
    t = np.array(times) if times else np.array([np.nan])
    return {
        "success": ok / n,
        "t_median": float(np.nanmedian(t)),
        "t_mean": float(np.nanmean(t)),
        "t_p90": float(np.nanpercentile(t, 90)) if times else float("nan"),
        "t_med_all": float(np.median(allt)) if allt else float("nan"),  # failures = infinite
        "within60": float(np.mean([t <= 60 for t in allt])) if allt else 0.0,
        "air_sub": sim.airtime["sub"],
        "air_2g4": sim.airtime["2g4"],
        "tx": sim.stats["tx_sub"] + sim.stats["tx_2g4"],
        "collisions": sim.stats["collisions"],
        "energy_j": sim.energy,
        "relayed": sum(nd.stats["relayed"] for nd in net.nodes),
        "echo_hits": echo,
        "sim_time": sim.now,
    }

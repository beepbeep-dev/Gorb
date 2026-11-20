"""A simplified G.O.R.B protocol for the simulator, with switchable ideas.

Baseline (what gorb/node.py + firmware/gorb-small do today):
  * GET floods out (ttl 3). The host answers with DATA chunks (180 bytes
    each, 35-byte header) addressed to the requester, sent back to back.
  * Every station relays every packet it has not seen, after a random wait
    of 50-400 ms (+ half the LoRa airtime), unless it has meanwhile heard the
    packet 3 times (itself + 2 relays). A board with LoRa and Wi-Fi relays on
    both radios (firmware: mediumsAvailable()).
  * The requester waits while chunks trickle in; after an idle period it
    sends RESEND with the missing chunk numbers (or GET again), up to 6
    rounds.
  * Echo: stations keep a reply they overheard completely (having also heard
    the GET), and answer a later GET for that page after a short wait unless
    somebody else answers first.

Ideas (Cfg fields) are described next to each field.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import phy
from .engine import Iface

GET, DATA, RESEND = 2, 3, 4


@dataclass
class Cfg:
    relay: str = "flood"  # flood | rssi | gradient | ct | route
    suppress: int = 2  # flood/rssi: cancel relay once heard more than this many times
    ttl: int = 3
    echo: bool = True
    chunk: int = 180
    header: int = 35
    retries: int = 6
    timeout: float = 120.0  # gorb/node.py fetch() default
    lora_mode: object = phy.US915
    wifi_mode: object = phy.ESPNOW
    duty: float = 0.5  # rules.MAX_TX_DUTY (US); EU868 0.1
    use_lora: bool = True
    use_wifi: bool = True
    # route mode: unicast along the reverse path of the GET
    adapt: bool = False  # pick the fastest mode per hop from measured signal
    margin_db: float = 8.0
    wifi_rates: tuple = ("b1",)  # rates route mode may use on Wi-Fi
    lora_rates: tuple = ()  # extra LoRa SFs route mode may use (needs multi-SF receivers)
    # coding / content ideas
    coding: bool = False  # rateless (fountain) chunks: any K of them rebuild the page
    redundancy: float = 0.0  # extra coded chunks sent up front (fraction of K)
    page_addr: bool = False  # DATA addressed to the page, so every listener collects it
    combining: bool = False  # combine corrupted copies of a frame from different relays
    combine_loss_db: float = 1.0
    local_repair: bool = False  # neighbours that hold a missing chunk answer RESEND
    lora_channels: int = 1  # >1: each host has a home channel; requesters tune to it
    # concurrent transmission flooding
    ct_spacing: int = 4  # slots between chunk floods (pipelining when < ttl + 1)
    ct_jitter: float = 30e-6  # trigger jitter of the relay radio (s, std dev)
    pace: float = 0.0  # host leaves pace * airtime * (hops-1, max 2) of silence after each chunk
    fast_repair: bool = False  # ask for missing chunks after a short silence (not ~5 s)
    carousel: float = 0.0  # >0: host pushes its popular page every this many seconds
    name: str = "baseline"


def _etx_table():
    """Expected transmissions (1 / P_ok) of a 215-byte LoRa frame versus the
    measured margin above sensitivity, averaged over Rician (K=3) fading."""
    rng = np.random.default_rng(1)
    from .world import rician_db
    fade = rician_db(rng, 3.0, 4000)
    m = phy.US915
    out = []
    for margin in range(-10, 41):
        snr = m.snr_req + margin + fade
        p = float(np.mean(m.p_ok(snr, 215)))
        out.append(min(1.0 / max(p, 1e-3), 6.0))
    return out


_ETX = _etx_table()


def link_cost(margin_db):
    """ETX-like cost of one hop (De Couto et al. 2003): ~1 for a strong
    link, growing as the measured margin above sensitivity shrinks."""
    i = int(round(min(max(margin_db, -10), 40))) + 10
    return _ETX[i] + 0.1  # small per-hop penalty: each hop also costs airtime


class Pkt:
    __slots__ = ("type", "src", "dst", "msg", "seq", "total", "ttl", "hops", "rnd", "size",
                 "page", "nh", "h", "need", "chan", "c", "wide", "gap")

    def __init__(self, type, src, dst, msg, seq=0, total=0, ttl=3, hops=0, rnd=0, size=40,
                 page=-1, nh=-1, h=99, need=0, chan=0):
        self.type, self.src, self.dst, self.msg, self.seq = type, src, dst, msg, seq
        self.total, self.ttl, self.hops, self.rnd, self.size = total, ttl, hops, rnd, size
        self.page, self.nh, self.h, self.need, self.chan = page, nh, h, need, chan
        self.c = 0.0  # sender's path cost back to where this packet started
        self.wide = False  # "the narrow path failed last round: spread out"
        self.gap = 0.0  # pacing: the sender stays quiet this long after sending it

    @property
    def key(self):
        return (self.type, self.src, self.dst, self.msg, self.seq, self.rnd)

    def relayed(self, **kw):
        p = Pkt(self.type, self.src, self.dst, self.msg, self.seq, self.total, self.ttl - 1,
                self.hops + 1, self.rnd, self.size, self.page, self.nh, self.h, self.need,
                self.chan)
        p.c = self.c
        p.wide = self.wide
        for k, v in kw.items():
            setattr(p, k, v)
        return p


class Fetch:
    def __init__(self, page, host, msg, t0):
        self.page, self.host, self.msg, self.t0 = page, host, msg, t0
        self.chunks = set()
        self.total = 0
        self.rnd = 0
        self.last = t0
        self.done = False
        self.ok = False
        self.t_done = None
        self.echo = False
        self.hops = 0


class Net:
    """Holds the pages, the stations and the results of one run."""

    def __init__(self, sim, cfg, pages, hosts_chan=None):
        self.sim, self.cfg = sim, cfg
        self.pages = pages  # page -> (host sid, size bytes)
        self.fetches = []
        self.msg = 1000
        self.home = hosts_chan or {}
        w = sim.w
        self.nodes = []
        for s in w.stations:
            node = Node(self, s)
            sim.nodes[s.sid] = node
            self.nodes.append(node)

    def new_msg(self):
        self.msg += 1
        return self.msg

    def page_chunks(self, page):
        size = self.pages[page][1]
        return max(1, math.ceil(size / self.cfg.chunk))


class Node:
    def __init__(self, net, st):
        self.net, self.sim, self.cfg = net, net.sim, net.cfg
        cfg = self.cfg
        self.sid = st.sid
        self.ifaces = {}
        chan = net.home.get(st.sid, 0)
        if "sub" in st.radios and cfg.use_lora:
            self.ifaces["sub"] = Iface(self.sim, st.sid, "sub", cfg.lora_mode, cfg.duty, chan)
        if "2g4" in st.radios and cfg.use_wifi:
            self.ifaces["2g4"] = Iface(self.sim, st.sid, "2g4", cfg.wifi_mode, 1.0, 0)
        if not cfg.use_lora and "sub" in self.sim.chan:
            self.sim.chan["sub"][st.sid] = -1
        if not cfg.use_wifi:
            self.sim.chan["2g4"][st.sid] = -1
        self.combining = cfg.combining
        self.seen = {}
        self.minh = {}
        self.sites = {p for p, (h, _) in net.pages.items() if h == st.sid}
        self.responses = {}  # (req, msg) -> [total, next coded symbol]
        self.sending = {}  # page -> time the current page-addressed send ends
        self.fetch = {}  # msg -> Fetch ; page-addressed: page -> Fetch
        self.echo_pages = {}
        self.heard_gets = {}
        self.over = {}  # (dst, msg) -> set of seqs overheard
        self.answered = set()
        self.d_req, self.d_host, self.parent, self.creq = {}, {}, {}, {}
        self.up = {}
        self.link = {}  # (nbr, band) -> last rssi
        self.corrupt = {}
        self.page_heard = {}
        self.stats = {"relayed": 0, "served": 0, "echo_served": 0}

    # ------------------------------------------------------------ helpers
    def air(self, size):
        if "sub" in self.ifaces:
            return self.cfg.lora_mode.airtime(size)
        return self.cfg.wifi_mode.airtime(size) + Iface.WIFI_SW

    def remember(self, key):
        n = self.seen.get(key, 0) + 1
        self.seen[key] = n
        return n

    def emit(self, pkt, prio=0, drop=None):
        """Send on every radio (baseline / flooding behaviour)."""
        for band, ifc in self.ifaces.items():
            if band == "sub" and pkt.chan != ifc.chan and self.net.cfg.lora_channels > 1:
                continue
            ifc.send(pkt, prio, drop=drop)

    def originate(self, pkt, prio=0):
        self.remember(pkt.key)
        if self.cfg.relay == "ct" and pkt.type != DATA:
            self.ct_send(pkt)
        else:
            self.emit(pkt, prio)

    # ----------------------------------------------------------- receiving
    def on_corrupt(self, pkt, tx, rssi, snr):
        if pkt.key in self.seen:
            return
        lst = self.corrupt.setdefault(pkt.key, {})
        lst[tx.node] = max(lst.get(tx.node, -99), snr)
        if len(lst) >= 2:
            comb = 10 * math.log10(sum(10 ** (s / 10) for s in lst.values())) - self.cfg.combine_loss_db
            if self.sim.rng.random() < float(tx.mode.p_ok(comb, pkt.size)):
                self.sim.stats["combined"] += 1
                del self.corrupt[pkt.key]
                self.on_rx(pkt, tx, rssi, comb)
        if len(self.corrupt) > 400:
            self.corrupt.clear()

    def on_rx(self, pkt, tx, rssi, snr):
        band = tx.mode.band
        self.link[(tx.node, band)] = rssi
        key = pkt.key
        n = self.remember(key)
        mh = self.minh.get(key)
        self.minh[key] = pkt.h if mh is None else min(mh, pkt.h)
        cfg = self.cfg
        if cfg.relay in ("gradient", "route"):
            # learn the way back: path cost = sum of per-link costs (1 for a
            # strong link, up to 3 for a link at the edge of sensitivity), an
            # ETX-like metric learnt from every copy we hear
            c = pkt.c + link_cost(rssi - tx.mode.sens_dbm)
            if pkt.type == GET or pkt.type == RESEND:
                cur = self.creq.get(pkt.msg)
                if cur is None or pkt.rnd > cur[0] or (pkt.rnd == cur[0] and c < cur[1]):
                    if pkt.type == GET or cfg.relay == "route" or cur is None:
                        self.creq[pkt.msg] = (pkt.rnd, c, tx.node)
                        self.d_req[pkt.msg] = c
                        self.parent[pkt.msg] = tx.node
            elif pkt.type == DATA:
                if c < self.d_host.get(pkt.msg, 99):
                    self.d_host[pkt.msg] = c
                    self.up[pkt.msg] = tx.node  # next hop towards the host
        if n > 1:
            return
        if cfg.echo:
            self.echo_listen(pkt)
        for_me = self.handle(pkt, tx, rssi)
        if not for_me and pkt.ttl > 0:
            self.schedule_relay(pkt, tx, rssi)

    def handle(self, pkt, tx, rssi):
        cfg = self.cfg
        t = pkt.type
        if t == GET and pkt.dst == self.sid:
            self.serve(pkt)
            return True
        if t == RESEND and pkt.dst == self.sid:
            self.resend(pkt)
            return True
        if t == RESEND and cfg.local_repair:
            self.maybe_repair(pkt, tx, rssi)
        if t == GET and cfg.echo and pkt.page in self.echo_pages and pkt.page not in self.sites:
            self.echo_answer(pkt)
            return True
        if t == DATA:
            if cfg.page_addr:
                f = self.fetch.get(("p", pkt.page))
                if f is not None and not f.done:
                    self.got_chunk(f, pkt)
                return False
            if pkt.dst == self.sid:
                f = self.fetch.get(pkt.msg)
                if f is not None and not f.done:
                    self.got_chunk(f, pkt)
                return True
        return False

    # -------------------------------------------------------------- relays
    def schedule_relay(self, pkt, tx, rssi):
        cfg = self.cfg
        mode = cfg.relay
        if mode == "ct":
            return self.ct_relay(pkt, tx)
        if mode == "route" and pkt.type == RESEND and pkt.nh >= 0:
            if pkt.nh != self.sid:
                return
            nxt = self.up.get(pkt.msg)
            if nxt is None:  # lost the way: flood it from here
                return self.sim.after(0.05, self._relay_fire, pkt.relayed(nh=-1), cfg.suppress, None)
            self.stats["relayed"] += 1
            return self.route_send(pkt.relayed(nh=nxt, c=self.d_req.get(pkt.msg, 0)), nxt)
        if mode == "route" and pkt.type == DATA and pkt.nh < 0:
            mode = "gradient"  # no single path known / it failed: spread like gradient
        elif mode == "route" and pkt.type == DATA:
            if pkt.nh != self.sid:
                return
            nxt = self.parent.get(pkt.msg)
            if nxt is None:
                return
            self.stats["relayed"] += 1
            return self.route_send(pkt.relayed(nh=nxt, c=self.d_host.get(pkt.msg, 0)), nxt,
                                   prio=pkt.total)
        sens = tx.mode.sens_dbm
        if mode == "flood" or (mode == "route" and pkt.type != DATA):
            delay = self.sim.rng.uniform(0.05, 0.4)
            if "sub" in self.ifaces:
                delay += 0.5 * cfg.lora_mode.airtime(pkt.size)
            limit = cfg.suppress
            self.sim.after(delay, self._relay_fire, pkt, limit, None)
            return
        # RSSI-ordered timer: the weaker we heard it, the further we probably
        # are from the sender, so the sooner we repeat it (others then stay quiet)
        x = min(max((rssi - sens) / 30.0, 0.0), 1.0)
        slot = 0.5 * cfg.lora_mode.airtime(pkt.size) if "sub" in self.ifaces else 0.01
        delay = 0.02 + x * (0.35 + 2 * slot) + self.sim.rng.uniform(0, 0.04)
        if mode == "rssi" or pkt.type == GET:
            self.sim.after(delay, self._relay_fire, pkt, cfg.suppress, None)
            return
        # gradient: only stations closer to the destination take part
        if pkt.type == DATA:
            mine = self.d_req.get(pkt.msg)
        else:
            mine = self.d_host.get(pkt.msg)
        if mine is None:
            if pkt.type == RESEND:  # no gradient known yet: plain flood
                self.sim.after(delay, self._relay_fire, pkt, cfg.suppress, None)
            return
        if mine >= pkt.h - 0.25:
            return
        # closest to the destination goes first; the others hear it and stay quiet
        air = cfg.lora_mode.airtime(pkt.size) if "sub" in self.ifaces else 0.004
        delay = 0.02 + 0.6 * air * mine + self.sim.rng.uniform(0, 0.5 * air)
        self.sim.after(delay, self._relay_fire, pkt, 99, mine)

    def _relay_fire(self, pkt, limit, mine):
        key = pkt.key
        if self.seen.get(key, 0) > limit:
            return
        if mine is not None and self.minh.get(key, 99) <= mine and not pkt.wide:
            return  # someone as close as us already repeated it
        self.stats["relayed"] += 1
        h = mine if mine is not None else pkt.h
        if pkt.type == DATA:
            c = self.d_host.get(pkt.msg, pkt.c + 1)
        else:
            c = self.d_req.get(pkt.msg, pkt.c + 1)
            if pkt.type == RESEND:
                h = mine if mine is not None else self.d_host.get(pkt.msg, pkt.h)
        if pkt.wide:
            h = 99.0
        if mine is None:
            drop = lambda: self.seen.get(key, 0) > limit
        elif pkt.wide:
            drop = lambda: self.seen.get(key, 0) > 2
        else:
            drop = lambda: self.minh.get(key, 99) <= mine
        self.emit(pkt.relayed(h=h, c=c), pkt.total if pkt.type == DATA else 0, drop)

    # ---- concurrent transmission (Glossy-style) flooding
    def ct_relay(self, pkt, tx):
        if pkt.hops >= self.cfg.ttl:
            return
        ifc = self.ifaces.get("sub")
        if ifc is None:
            return
        rp = pkt.relayed()
        dt = 1e-3 + self.sim.rng.normal(0, self.cfg.ct_jitter)
        self.stats["relayed"] += 1
        self.sim.at(tx.t1 + max(dt, 1e-4), self._ct_fire, ifc, rp)

    def _ct_fire(self, ifc, rp):
        ifc.transmit_now(rp, ct_id=(rp.key, rp.hops))

    def ct_send(self, pkt, tries=0):
        """Originate a flood: listen first, then send with the CT id."""
        ifc = self.ifaces.get("sub")
        if ifc is None:
            return self.emit(pkt)
        if ifc.state != "idle" or (tries < 40 and self.sim.busy(self.sid, "sub", ifc.chan, ifc.mode)):
            self.sim.after(self.sim.rng.uniform(0.05, 0.3), self.ct_send, pkt, tries + 1)
            return
        ifc.transmit_now(pkt, ct_id=(pkt.key, pkt.hops))

    def ct_slot(self, size):
        return self.cfg.lora_mode.airtime(size) + 1e-3 + 3 * self.cfg.ct_jitter + 2e-3

    def ct_stream(self, pkts, i=0):
        if i >= len(pkts):
            return
        p = pkts[i]
        self.remember(p.key)
        ifc = self.ifaces.get("sub")
        if ifc is None:
            return
        if ifc.state != "idle" or self.sim.busy(self.sid, "sub", ifc.chan, ifc.mode):
            self.sim.after(self.ct_slot(p.size) * 0.5, self.ct_stream, pkts, i)
            return
        ifc.transmit_now(p, ct_id=(p.key, p.hops))
        self.sim.after(self.cfg.ct_spacing * self.ct_slot(p.size), self.ct_stream, pkts, i + 1)

    # ---- unicast along the path (route idea), choosing radio + rate per hop
    def route_send(self, pkt, nxt, prio=0):
        cfg = self.cfg
        best = None
        for band, ifc in self.ifaces.items():
            rssi = self.link.get((nxt, band))
            if rssi is None:
                continue
            if band == "sub":
                modes = [cfg.lora_mode] + ([phy.lora(sf, cfg.lora_mode.bw_hz / 1e3)
                                            for sf in cfg.lora_rates] if cfg.adapt else [])
            else:
                modes = [phy.wifi(r) for r in cfg.wifi_rates] if cfg.adapt else [cfg.wifi_mode]
            base = ifc.mode
            for m in modes:
                pred = rssi + (m.tx_dbm - base.tx_dbm)
                margin = cfg.margin_db if cfg.adapt else 0.0
                if pred < m.sens_dbm + margin and m is not base:
                    continue
                if pred < m.sens_dbm - 3:
                    continue
                cost = ifc.backlog() + m.airtime(pkt.size) + (Iface.WIFI_SW if m.kind == "wifi" else 0)
                if best is None or cost < best[0]:
                    best = (cost, ifc, m)
        if best is None:
            return self.emit(pkt, prio)  # no idea how to reach it: broadcast
        _, ifc, m = best
        dst = nxt if m.kind == "wifi" else None
        ifc.send(pkt, prio, mode=m, dst=dst)

    # ---------------------------------------------------------------- Echo
    def echo_listen(self, pkt):
        if pkt.type == GET:
            self.heard_gets[(pkt.src, pkt.msg)] = pkt.page
            return
        if pkt.type != DATA or not pkt.total:
            return
        if self.cfg.page_addr:
            key = ("p", pkt.page)
            self.page_heard[pkt.page] = self.sim.now
        else:
            key = (pkt.dst, pkt.msg)
            self.answered.add(key)
            if self.heard_gets.get(key) != pkt.page:
                return
        s = self.over.setdefault(key, set())
        s.add(pkt.seq)
        if self.complete(s, pkt.total):
            self.echo_pages[pkt.page] = self.sim.now
            self.over.pop(key, None)

    def complete(self, s, total):
        if not self.cfg.coding:
            return len(s) >= total
        src = sum(1 for q in s if q < total)
        return src >= total or len(s) >= total + 1

    def echo_answer(self, pkt):
        delay = self.sim.rng.uniform(0.2, 0.5) + 3 * self.air(self.cfg.chunk + self.cfg.header)
        self.sim.after(delay, self._echo_go, pkt)

    def _echo_go(self, pkt):
        req = (pkt.src, pkt.msg)
        if req in self.answered or (req in self.responses and pkt.rnd == 0):
            return
        if self.cfg.page_addr and (self.sending.get(pkt.page, -1) > self.sim.now
                                   or self.sim.now - self.page_heard.get(pkt.page, -9) < 1.5):
            return
        self.stats["echo_served"] += 1
        self.start_send(pkt, self.net.pages[pkt.page][0])

    # ------------------------------------------------------------- hosting
    def serve(self, pkt):
        key = (pkt.src, pkt.msg)
        if self.cfg.page_addr and self.sending.get(pkt.page, -1) > self.sim.now:
            return  # already sending this page: the new requester just listens
        if key in self.responses and pkt.rnd == 0:
            return
        self.stats["served"] += 1
        # route/gradient: wait a moment so the better paths' GET copies arrive too
        wait = 0.6 if self.cfg.relay in ("route", "gradient") else 0.05
        self.sim.after(wait, self.start_send, pkt, self.sid)

    def data_pkts(self, req, src_id, seqs, rnd, total):
        cfg = self.cfg
        size = self.net.pages[req.page][1]
        out = []
        ttl = max(cfg.ttl, req.hops + 1)
        h = self.d_req.get(req.msg, req.hops + 1)
        for s in seqs:
            nbytes = cfg.chunk if (cfg.coding or s < total - 1) else size - cfg.chunk * (total - 1)
            if cfg.page_addr:
                dst, msg = -1 - req.page, req.page
            else:
                dst, msg = req.src, req.msg
            wide = req.type == RESEND and req.wide
            out.append(Pkt(DATA, src_id, dst, msg, s, total, ttl, 0, rnd, cfg.header + nbytes,
                           req.page, -1 if wide else self.parent.get(req.msg, -1),
                           99.0 if wide else h, chan=req.chan))
            if cfg.pace:
                hops = min(max(req.hops, 0), 2)  # relays the GET went through
                out[-1].gap = cfg.pace * cfg.lora_mode.airtime(out[-1].size) * hops
        return out

    def start_send(self, req, src_id, seqs=None):
        cfg = self.cfg
        total = self.net.page_chunks(req.page)
        key = ("p", req.page) if cfg.page_addr else (req.src, req.msg)
        st = self.responses.get(key)
        if st is None:
            st = self.responses[key] = [total, total]
        if seqs is None:
            seqs = list(range(total))
            if cfg.coding and cfg.redundancy > 0:
                extra = math.ceil(cfg.redundancy * total)
                seqs += list(range(st[1], st[1] + extra))
                st[1] += extra
        rnd = req.rnd
        if cfg.page_addr:
            self.page_round = getattr(self, "page_round", 0) + 1
            rnd = self.page_round
        pk = self.data_pkts(req, src_id, seqs, rnd, total)
        if cfg.page_addr:
            est = sum(self.air(p.size) for p in pk) * (1.2 if cfg.relay != "ct" else cfg.ct_spacing)
            self.sending[req.page] = self.sim.now + est
        if cfg.relay == "ct":
            return self.ct_stream(pk)
        for p in pk:
            self.remember(p.key)
            if cfg.relay == "route" and p.nh >= 0:
                self.route_send(p, p.nh, prio=p.total)
            else:
                self.emit(p, p.total)

    def resend(self, pkt):
        key = ("p", pkt.page) if self.cfg.page_addr else (pkt.src, pkt.msg)
        st = self.responses.get(key)
        if pkt.page not in self.sites and st is None:
            return
        if st is None:
            return self.serve(pkt)
        if self.cfg.coding:
            n = pkt.need + 1 + math.ceil(0.1 * pkt.need)
            seqs = list(range(st[1], st[1] + n))
            st[1] += n
        else:
            seqs = pkt.need
        self.start_send(pkt, self.sid, seqs)

    def maybe_repair(self, pkt, tx, rssi):
        """Local repair: we overheard the chunks the requester is missing."""
        if pkt.hops != 0:
            return
        have = self.over.get((pkt.src, pkt.msg))
        if pkt.page in self.echo_pages:
            have = None
            seqs = list(pkt.need)
        elif have:
            seqs = [s for s in pkt.need if s in have]
        else:
            return
        if not seqs:
            return
        x = min(max((rssi - tx.mode.sens_dbm) / 30.0, 0.0), 1.0)
        delay = 0.05 + (1 - x) * 0.3 + self.sim.rng.uniform(0, 0.03)
        self.sim.after(delay, self._repair_go, pkt, seqs)

    def _repair_go(self, pkt, seqs):
        host = self.net.pages[pkt.page][0]
        total = self.net.page_chunks(pkt.page)
        pk = self.data_pkts(pkt, host, seqs, pkt.rnd, total)
        pk = [p for p in pk if p.key not in self.seen]
        for p in pk:
            p.ttl = 1
            self.remember(p.key)
            self.emit(p, p.total)
        self.sim.stats["repairs"] += len(pk)

    def carousel(self, page, period, until):
        """Push: send a popular page to everyone every `period` seconds."""
        if self.sim.now > until:
            return
        req = Pkt(GET, self.sid, self.sid, -page - 1, ttl=self.cfg.ttl, page=page)
        if self.sending.get(page, -1) < self.sim.now:
            self.start_send(req, self.sid)
        self.sim.after(period, self.carousel, page, period, until)

    # ------------------------------------------------------------ browsing
    def start_fetch(self, page):
        cfg = self.cfg
        host = self.net.pages[page][0]
        msg = self.net.new_msg()
        f = Fetch(page, host, msg, self.sim.now)
        self.net.fetches.append((self.sid, f))
        if page in self.echo_pages or page in self.sites:
            f.done = f.ok = f.echo = True
            f.t_done = self.sim.now
            return f
        self.fetch[msg] = f
        if cfg.page_addr:
            self.fetch[("p", page)] = f
            o = self.over.get(("p", page))
            if o:
                f.chunks |= o
        if cfg.lora_channels > 1 and "sub" in self.ifaces:
            ch = self.net.home.get(host, 0)
            self.ifaces["sub"].chan = ch
            self.sim.tune(self.sid, "sub", ch)
        f.chan = self.ifaces["sub"].chan if "sub" in self.ifaces else 0
        if not (cfg.page_addr and self.sim.now - self.page_heard.get(page, -9) < 1.0):
            self.send_get(f)  # (page-addressed: if the page is on the air already, just listen)
        self.sim.after(self.idle_limit(), self.check, f)
        return f

    def send_get(self, f):
        p = Pkt(GET, self.sid, f.host, f.msg, ttl=self.cfg.ttl, rnd=f.rnd,
                size=self.cfg.header + 12, page=f.page, chan=f.chan)
        self.originate(p)

    def idle_limit(self):
        if getattr(self, "_idle", None) is not None:
            return self._idle
        cfg = self.cfg
        a = self.air(cfg.chunk + cfg.header)
        lim = 2.5 + a * (4 + 2 * cfg.ttl) + 4 * a / cfg.duty
        if cfg.fast_repair:
            lim = 0.6 + a * (3 + cfg.ttl)
        if cfg.relay == "ct":
            lim += a * cfg.ct_spacing * 3
        self._idle = lim
        return lim

    def got_chunk(self, f, pkt):
        f.chunks.add(pkt.seq)
        f.total = pkt.total
        f.last = self.sim.now
        f.hops = max(f.hops, pkt.hops)
        if self.complete(f.chunks, f.total):
            self.finish(f, True)

    def finish(self, f, ok):
        f.done, f.ok, f.t_done = True, ok, self.sim.now
        self.fetch.pop(f.msg, None)
        self.fetch.pop(("p", f.page), None)
        if ok and self.cfg.echo:
            self.echo_pages[f.page] = self.sim.now
        if self.cfg.lora_channels > 1 and "sub" in self.ifaces:
            home = self.net.home.get(self.sid, 0)
            self.ifaces["sub"].chan = home
            self.sim.tune(self.sid, "sub", home)

    def check(self, f):
        if f.done:
            return
        cfg = self.cfg
        now = self.sim.now
        if now - f.t0 > cfg.timeout:
            return self.finish(f, False)
        lim = self.idle_limit()
        if now - f.last < lim:
            self.sim.at(max(f.last + lim, now + 1e-3), self.check, f)
            return
        f.rnd += 1
        if f.rnd > cfg.retries and not cfg.fast_repair:  # fast repair: keep asking until timeout
            return self.finish(f, False)
        f.last = now
        if f.total:
            if cfg.coding:
                src = sum(1 for q in f.chunks if q < f.total)
                need = max(f.total + 1 - len(f.chunks), 0) if src < f.total else 0
                need = max(need, 1)
                p = Pkt(RESEND, self.sid, f.host, f.msg, ttl=cfg.ttl, rnd=f.rnd,
                        size=cfg.header + 4, page=f.page, need=need, chan=f.chan)
            else:
                missing, chars = [], 0
                for s in range(f.total):
                    if s not in f.chunks:
                        c = len(str(s)) + 1
                        if chars + c > cfg.chunk:
                            break
                        missing.append(s)
                        chars += c
                p = Pkt(RESEND, self.sid, f.host, f.msg, ttl=cfg.ttl, rnd=f.rnd,
                        size=cfg.header + chars, page=f.page, need=missing, chan=f.chan)
            f.asked_next = len(missing) if not cfg.coding else need
            p.h = self.d_host.get(f.msg, 99)
            asked = getattr(f, "asked", 0)
            progress = len(f.chunks) - getattr(f, "last_count", 0) >= 0.5 * asked
            f.last_count = len(f.chunks)
            f.asked = getattr(f, "asked_next", 0)
            if not progress and cfg.relay in ("gradient", "route"):
                p.wide, p.h = True, 99.0  # last round got nothing: let every relay help
            if cfg.relay == "route" and f.msg in self.up and progress:
                p.nh = self.up[f.msg]
                self.remember(p.key)
                self.route_send(p, p.nh)
            else:
                self.originate(p)
        else:
            self.send_get(f)
        self.sim.after(lim, self.check, f)

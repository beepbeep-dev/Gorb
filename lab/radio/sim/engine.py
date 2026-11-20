"""Discrete-event radio network simulator.

What it models
  * every frame is a Transmission with a start/end time on a channel
    (band + frequency index); received power is computed for every station
  * reception at the end of a frame succeeds when, at that receiver:
      - it has a radio on that band, tuned to that channel since before the
        frame began (and, for LoRa, to the same SF/bandwidth)
      - it was not itself transmitting on that band during the frame
        (half duplex)
      - it was not already locked onto an earlier frame it could decode,
        unless the new frame arrived during that earlier frame's preamble
        (receivers sync to the first preamble they hear; Bor et al.,
        "Do LoRa Low-Power Wide-Area Networks Scale?", MSWiM 2016)
      - signal / (sum of same-mode interferers) >= capture margin
        (6 dB LoRa, 10 dB Wi-Fi), and for LoRa signals at a different SF on
        the same frequency, signal/interference >= -10 dB (imperfect
        orthogonality; Croce et al., IEEE Comm. Letters 2018)
      - then a random draw against the PER curve at its SNR (noise only)
  * concurrent transmissions of the *same* frame (Glossy style) that start
    within a timing tolerance do not destroy each other; the receiver sees
    the strongest copy, with an extra failure probability for beating
  * carrier sense before sending: LoRa CAD sees same-SF frames above
    sensitivity (90% of the time); Wi-Fi CCA sees frames 4 dB above
    sensitivity or any energy above -62 dBm
  * background traffic from outside Gorb (home Wi-Fi on 2.4 GHz, other LoRa
    users on 915 MHz): busy channel at carrier sense with some probability,
    and frames hit by an unseen interferer with P = 1 - exp(-airtime / T)
  * duty cycle as a token bucket (like the firmware), Wi-Fi unicast with
    MAC ACK and up to 7 retries (ESP-NOW default)
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections import defaultdict

import numpy as np

from .world import rician_db

BG_DEFAULT = {  # band -> (P(channel busy at carrier sense), mean gap between hits in s)
    "sub": (0.01, 60.0),
    "2g4": (0.25, 0.08),
}


class Tx:
    __slots__ = ("node", "iface", "mode", "chan", "t0", "t1", "pkt", "p", "ovl", "ct_id",
                 "dst", "size", "on_done")

    def __init__(self):
        self.ovl = []


class Sim:
    def __init__(self, world, seed=0, bg=None, multi_sf_rx=False, ct_penalty=0.1,
                 ct_tol=None, fading=True):
        self.w = world
        self.n = world.n
        self.rng = np.random.default_rng(seed + 7919)
        self.now = 0.0
        self.heap = []
        self.cnt = itertools.count()
        self.active = defaultdict(list)
        self.nodes = [None] * self.n
        self.bg = dict(BG_DEFAULT if bg is None else bg)
        self.multi_sf_rx = multi_sf_rx
        self.ct_penalty = ct_penalty
        self.ct_tol = ct_tol  # seconds; None -> 1 LoRa symbol
        self.fading = fading
        bands = ("sub", "2g4")
        self.has = {b: world.has(b) for b in bands}
        self.chan = {b: np.where(self.has[b], 0, -1) for b in bands}
        self.chan_since = {b: np.zeros(self.n) for b in bands}
        self.rxid = {b: np.zeros(self.n, int) for b in bands}
        self.last_tx_end = {b: np.full(self.n, -1.0) for b in bands}
        self._modeid = {}
        self.stats = defaultdict(float)
        self.airtime = defaultdict(float)
        self.energy = 0.0

    # ------------------------------------------------------------- events
    def at(self, t, fn, *a):
        heapq.heappush(self.heap, (t, next(self.cnt), fn, a))

    def after(self, dt, fn, *a):
        self.at(self.now + max(dt, 0.0), fn, *a)

    def run(self, until=float("inf"), stop=None):
        h = self.heap
        while h:
            t, _, fn, a = heapq.heappop(h)
            if t > until:
                heapq.heappush(h, (t, 0, fn, a))
                break
            self.now = t
            fn(*a)
            if stop is not None and stop():
                break
        return self.now

    def mode_id(self, mode):
        return self._modeid.setdefault(mode.rxkey, len(self._modeid) + 1)

    def tune(self, sid, band, chan=None, mode=None):
        if chan is not None and self.chan[band][sid] != chan:
            self.chan[band][sid] = chan
            self.chan_since[band][sid] = self.now
        if mode is not None:
            mid = self.mode_id(mode)
            if self.rxid[band][sid] != mid:
                self.rxid[band][sid] = mid
                self.chan_since[band][sid] = self.now

    # --------------------------------------------------------- carrier sense
    def busy(self, sid, band, chan, mode):
        if self.rng.random() < self.bg[band][0]:
            return True
        for o in self.active[(band, chan)]:
            p = o.p[sid]
            if mode.kind == "lora":
                if o.mode.rxkey == mode.rxkey and p >= o.mode.sens_dbm and self.rng.random() < 0.9:
                    return True
            elif p >= o.mode.sens_dbm + 4 or p >= -62:
                return True
        return False

    # ----------------------------------------------------------- transmit
    def transmit(self, node, iface, mode, pkt, size, dst=None, ct_id=None, on_done=None):
        band = mode.band
        t = Tx()
        t.node, t.iface, t.mode, t.chan, t.pkt, t.size = node, iface, mode, iface.chan, pkt, size
        t.dst, t.ct_id, t.on_done = dst, ct_id, on_done
        air = mode.airtime(size)
        t.t0, t.t1 = self.now, self.now + air
        g = self.w.gain[band][node]
        fade = rician_db(self.rng, self.w.rician_k, self.n) if self.fading else 0.0
        t.p = mode.tx_dbm + g + fade
        key = (band, t.chan)
        act = self.active[key]
        for o in act:
            o.ovl.append(t)
        t.ovl = list(act)
        act.append(t)
        self.last_tx_end[band][node] = t.t1
        self.airtime[band] += air
        self.stats["tx_" + band] += 1
        self.energy += air * mode.tx_watts
        self.at(t.t1, self._end, t)
        return t

    def _end(self, t):
        band = t.mode.band
        self.active[(band, t.chan)].remove(t)
        mode = t.mode
        cand = (self.chan[band] == t.chan) & (self.chan_since[band] <= t.t0)
        if mode.kind == "lora" and not self.multi_sf_rx:
            cand &= self.rxid[band] == self.mode_id(mode)
        cand &= self.last_tx_end[band] <= t.t0
        cand[t.node] = False
        if t.dst is not None:  # Wi-Fi unicast: only the addressee keeps it
            m = np.zeros(self.n, bool)
            m[t.dst] = cand[t.dst]
            cand = m
        cand &= t.p >= mode.sens_dbm - 4
        idx = np.nonzero(cand)[0]
        acked = False
        if len(idx):
            S = t.p[idx]
            Slin = 10 ** (S / 10)
            Ico = np.zeros(len(idx))
            Ix = np.zeros(len(idx))
            bad = np.zeros(len(idx), bool)
            ct_seen = np.zeros(len(idx), bool)
            ct_skip = np.zeros(len(idx), bool)
            tol = self.ct_tol if self.ct_tol is not None else (mode.tsym or 1e-6)
            for o in t.ovl:
                po = o.p[idx]
                if (t.ct_id is not None and o.ct_id == t.ct_id and abs(o.t0 - t.t0) <= tol):
                    ct_seen |= True
                    ct_skip |= po > S  # a stronger identical copy: count it there instead
                    continue
                if o.mode.rxkey == mode.rxkey or mode.kind != "lora" or o.mode.kind != "lora":
                    Ico += 10 ** (po / 10)
                    if o.t0 < t.t0 - o.mode.preamble_s:
                        bad |= po >= o.mode.sens_dbm  # receiver was busy with o
                else:
                    Ix += 10 ** (po / 10)
            coll = (Slin < Ico * 10 ** (mode.capture_db / 10)) | (Slin < Ix * 0.1)
            snr = S - mode.noise_dbm
            p = mode.p_ok(snr, t.size)
            if ct_seen.any():
                p = np.where(ct_seen, p * (1 - self.ct_penalty), p)
            p = p * math.exp(-(t.t1 - t.t0) / self.bg[band][1])
            u = self.rng.random(len(idx))
            ok = (~bad) & (~coll) & (~ct_skip) & (u < p)
            self.stats["collisions"] += int(np.sum((coll | bad) & ~ct_skip & (snr >= mode.snr_req)))
            self.stats["rx_ok"] += int(ok.sum())
            nodes = self.nodes
            for k in np.nonzero(ok)[0]:
                r = idx[k]
                if t.dst is not None and r == t.dst:
                    acked = True
                nodes[r].on_rx(t.pkt, t, float(S[k]), float(snr[k]))
            # frames that failed on noise/fading alone (for packet combining)
            soft = np.nonzero((~ok) & (~bad) & (~coll) & (~ct_skip))[0]
            for k in soft:
                r = idx[k]
                if getattr(nodes[r], "combining", False):
                    nodes[r].on_corrupt(t.pkt, t, float(S[k]), float(snr[k]))
        if t.on_done:
            t.on_done(t, acked)


class Iface:
    """One radio of a station with its own transmit queue and MAC."""

    WIFI_SW = 1.0e-3  # per-frame software time of esp_now_send (measured ESP-NOW throughput ~0.5 Mbit/s at 1M)

    def __init__(self, sim, sid, band, mode, duty=1.0, chan=0):
        self.sim, self.sid, self.band, self.mode = sim, sid, band, mode
        self.chan = chan
        self.duty = duty
        self.budget = duty * 360.0
        self.stamp = 0.0
        self.q = []
        self.cnt = itertools.count()
        self.state = "idle"
        self.drops = {}
        sim.tune(sid, band, chan, mode)

    def backlog(self):
        return sum(e[3].airtime(e[4].size) for e in self.q)

    def send(self, pkt, prio=0, mode=None, dst=None, no_cs=False, drop=None):
        """Queue a frame. `drop` (optional) is checked right before sending;
        if it returns True the frame is discarded (firmware re-checks relay
        suppression this way while waiting for the channel)."""
        if drop is not None:
            self.drops[id(pkt)] = drop
        heapq.heappush(self.q, (prio, next(self.cnt), 0.0, mode or self.mode, pkt, dst, 0, no_cs))
        if self.state == "idle":
            self._try()

    def _refill(self):
        now = self.sim.now
        self.budget = min(self.duty * 3600.0, self.budget + (now - self.stamp) * self.duty)
        self.stamp = now

    def _try(self):
        if self.state != "idle" or not self.q:
            return
        sim = self.sim
        while self.q and self.drops:
            d = self.drops.get(id(self.q[0][4]))
            if d is None or not d():
                break
            self.drops.pop(id(self.q[0][4]), None)
            heapq.heappop(self.q)
            sim.stats["relay_cancelled"] += 1
        if not self.q:
            return
        prio, c, waited, mode, pkt, dst, tries, no_cs = self.q[0]
        air = mode.airtime(pkt.size)
        if self.duty < 1.0:
            self._refill()
            if self.budget < air:
                self.state = "wait"
                sim.after((air - self.budget) / self.duty + 1e-3, self._wake)
                return
        if not no_cs and waited < 10.0 and sim.busy(self.sid, self.band, self.chan, mode):
            if mode.kind == "lora":
                d = sim.rng.uniform(0.1, 0.6)  # firmware: random(100, 600) ms after CAD hit
            else:
                d = 50e-6 + sim.rng.integers(0, 32) * 20e-6 + 1e-3
            heapq.heapreplace(self.q, (prio, c, waited + d, mode, pkt, dst, tries, no_cs))
            self.state = "wait"
            sim.after(d, self._wake)
            return
        heapq.heappop(self.q)
        self.drops.pop(id(pkt), None)
        self.transmit_now(pkt, mode, dst, tries)

    def transmit_now(self, pkt, mode=None, dst=None, tries=0, ct_id=None):
        """Send immediately (used by concurrent-transmission relays). Returns
        False if the radio is already sending."""
        if self.state == "tx":
            return False
        mode = mode or self.mode
        self.state = "tx"
        if self.duty < 1.0:
            self._refill()
            self.budget -= mode.airtime(pkt.size)
        self.sim.tune(self.sid, self.band, None, mode) if mode.kind == "lora" else None
        self.sim.transmit(self.sid, self, mode, pkt, pkt.size, dst=dst, ct_id=ct_id,
                          on_done=lambda t, ack: self._done(t, ack, tries))
        return True

    def _done(self, t, acked, tries):
        sim = self.sim
        if t.mode.kind == "lora" and t.mode is not self.mode:
            sim.tune(self.sid, self.band, None, self.mode)  # back to the listening SF
        if t.dst is not None and not acked and tries < 7:
            heapq.heappush(self.q, (-1, next(self.cnt), 0.0, t.mode, t.pkt, t.dst, tries + 1, False))
        if t.dst is not None:
            sim.stats["acked" if acked else "unacked"] += 1
        self.state = "wait"
        if t.mode.kind == "wifi":
            gap = self.WIFI_SW + 50e-6 + sim.rng.integers(0, 32) * 20e-6
            if t.dst is not None:
                gap += 314e-6  # SIFS + ACK at 1 Mbit/s
        else:
            gap = 2e-3  # radio turnaround + SPI
        gap += getattr(t.pkt, "gap", 0.0) if t.node == self.sid and t.pkt.hops == 0 else 0.0
        sim.after(gap, self._wake)

    def _wake(self):
        if self.state == "wait":
            self.state = "idle"
        self._try()

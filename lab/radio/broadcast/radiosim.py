"""Discrete-event simulation of Gorb page delivery over a shared LoRa channel.

Modelled after gorb/node.py (fetch / _serve / _resend / Echo / relay) and the
G.O.R.B Small firmware (listen-before-talk with CAD and 100-600 ms back-off,
duty-cycle token bucket, 12-slot transmit queue on relays).

Schemes
  arq        current Gorb: one ARQ session per requester (GET, DATA 0..K-1,
             RESEND lists, up to 6 rounds, 120 s timeout, user reloads twice)
  arq_echo   the same plus Gorb Echo (keep complete overheard replies; answer
             other people's GETs from the copy)
  arq_fixed  arq_echo with small fixes that need no new packet types: the
             host never queues a chunk that is already queued; a requester
             that hears the host busy sending keeps waiting (no 120 s timeout)
             and may use up to 30 rounds; Echo keeps copies but does not
             answer other people's GETs (that can make several stations send
             the same page at once). Shows the true cost of one unicast ARQ
             session per listener.
  mc_list    hypothetical improved ARQ without coding: one shared stream per
             page; everyone accepts it; RESEND lists, host sends the union
  rlnc       one shared stream per page: K systematic DATA, then coded repair
             packets; listeners send NEED(n) ("send n more"), with suppression
             (stay quiet if someone already asked for at least as many)
  rlnc_echo  rlnc where stations keep partial coded state from what they
             overheard, so a later fetch only asks for the remainder
"""

from __future__ import annotations

import heapq
import math
import random
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from gorb.small import REGIONS  # noqa: E402  (read-only use of the airtime formula)

CHUNK = 180
HOST_NAME = 9  # "news.gorb"
CALL_NAME = 6  # "KD2A01"
PATH_LEN = 11  # "/index.html"
HDR = 17  # fixed Gorb header + 2 name length bytes
CODE_HDR = 4  # gorbcode repair header
RESEND_LIMIT = 180
MAX_ROUNDS = 6
FETCH_TIMEOUT = 120.0
ATTEMPTS = 3  # the user reloads after a failure, twice
RELOAD_DELAY = 10.0
ECHO_TTL = 600.0
CAD_BLIND = 0.005  # a transmission younger than this is not detected by CAD
NEED_WINDOW = 1.5  # NEED senders pick a random moment in this window (suppression)

CTRL = ("GET", "RESEND", "NEED", "OFFER")


@lru_cache(maxsize=None)
def airtime(region: str, nbytes: int) -> float:
    return REGIONS[region].airtime(nbytes)


class Pkt:
    __slots__ = ("typ", "src", "dst", "msg", "seq", "total", "rnd", "size", "ttl", "hops",
                 "info", "relayed")

    def __init__(self, typ, src, dst, msg, seq=0, total=0, rnd=0, size=40, ttl=3, hops=0,
                 info=None):
        self.typ, self.src, self.dst, self.msg = typ, src, dst, msg
        self.seq, self.total, self.rnd, self.size = seq, total, rnd, size
        self.ttl, self.hops, self.info, self.relayed = ttl, hops, info, False

    @property
    def key(self):
        return (self.typ, self.src, self.dst, self.msg, self.seq, self.rnd)

    def relay_copy(self):
        p = Pkt(self.typ, self.src, self.dst, self.msg, self.seq, self.total, self.rnd,
                self.size, self.ttl - 1, self.hops + 1, self.info)
        p.relayed = True
        return p


# ---------------------------------------------------------------- loss models
class Bernoulli:
    def __init__(self, p: np.ndarray, rng: np.random.Generator):
        self.p, self.rng = p, rng

    def draw(self, s: int) -> np.ndarray:
        return self.rng.random(self.p.shape[1]) < self.p[s]


class GilbertElliott:
    """Two-state burst loss per link, stepped once per packet the sender sends."""

    def __init__(self, n, rng, pg=0.02, pb=0.70, p_gb=0.059, p_bg=0.25, mask=None):
        self.rng = rng
        self.pg, self.pb, self.p_gb, self.p_bg = pg, pb, p_gb, p_bg
        pi_b = p_gb / (p_gb + p_bg)
        self.bad = rng.random((n, n)) < pi_b
        self.mean = pg * (1 - pi_b) + pb * pi_b

    def draw(self, s):
        u = self.rng.random(self.bad.shape[1])
        lost = np.where(self.bad[s], u < self.pb, u < self.pg)
        v = self.rng.random(self.bad.shape[1])
        self.bad[s] = np.where(self.bad[s], v >= self.p_bg, v < self.p_gb)
        return lost


# ---------------------------------------------------------------- simulator core
@dataclass
class Tx:
    start: float
    end: float
    sender: int
    pkt: Pkt


class Station:
    def __init__(self, sim, idx, name_len, relay=False, qcap=None, gap=0.005, fifo=False):
        self.sim, self.idx, self.name_len = sim, idx, name_len
        self.relay, self.qcap, self.gap, self.fifo = relay, qcap, gap, fifo
        self.q: list = []
        self.busy = False
        self.try_pending = False
        d = REGIONS[sim.region].duty
        self.duty, self.bucket, self.last_refill = d, 360 * d, 0.0
        self.seen: dict = {}
        self.proto = None

    # transmit side
    def enqueue(self, pkt: Pkt, relay=False) -> bool:
        if self.qcap is not None and len(self.q) >= self.qcap:
            self.sim.stats["queue_drops"] += 1
            return False
        if not relay:
            self.seen[pkt.key] = self.seen.get(pkt.key, 0) + 1
        prio = 0 if self.fifo else (pkt.total if pkt.typ in ("DATA", "CODED") else 0)
        heapq.heappush(self.q, (prio, next(self.sim.counter), pkt))
        self.kick(0.0)
        return True

    def cancel(self, pred):
        n = len(self.q)
        self.q = [e for e in self.q if not pred(e[2])]
        if len(self.q) != n:
            heapq.heapify(self.q)

    def idle(self):
        return not self.q and not self.busy

    def kick(self, delay):
        if not self.try_pending and not self.busy:
            self.try_pending = True
            self.sim.at(delay, self.try_tx)

    def try_tx(self):
        self.try_pending = False
        sim = self.sim
        if self.busy:
            return
        while self.q and self.q[0][2].relayed and self.seen.get(self.q[0][2].key, 0) > 2:
            heapq.heappop(self.q)  # two others already repeated it
        if not self.q:
            return
        pkt = self.q[0][2]
        a = airtime(sim.region, pkt.size)
        now = sim.t
        self.bucket = min(3600 * self.duty, self.bucket + (now - self.last_refill) * self.duty)
        self.last_refill = now
        if self.bucket < a:
            self.try_pending = True
            sim.at(max(0.25, (a - self.bucket) / self.duty), self.try_tx)
            return
        if sim.channel_busy_for(self.idx):
            self.try_pending = True
            sim.at(sim.rng.uniform(0.1, 0.6), self.try_tx)
            return
        heapq.heappop(self.q)
        self.bucket -= a
        self.busy = True
        if self.proto is not None:
            self.proto.on_tx(pkt)
        sim.transmit(self, pkt, a)

    def tx_done(self):
        self.busy = False
        if self.q:
            self.kick(self.gap)

    # receive side
    def receive(self, pkt: Pkt):
        k = pkt.key
        n = self.seen.get(k, 0) + 1
        self.seen[k] = n
        if n > 1:
            return
        for_me = self.proto.on_rx(pkt) if self.proto else False
        if self.relay and not for_me and pkt.ttl > 0:
            d = self.sim.rng.uniform(0.05, 0.4) + airtime(self.sim.region, pkt.size) / 2
            self.sim.at(d, self._relay_go, pkt)

    def _relay_go(self, pkt):
        if self.seen.get(pkt.key, 0) > 2:
            return
        self.sim.stats["relayed"] += 1
        self.enqueue(pkt.relay_copy(), relay=True)


class Sim:
    def __init__(self, n, hear, loss, region="US915", seed=0):
        self.t = 0.0
        self.ev: list = []
        self.counter = iter(range(10**12))
        self.rng = random.Random(seed)
        self.region = region
        self.hear = hear
        self.loss = loss
        self.active: list[Tx] = []
        self.stations: list[Station] = []
        self.stats = {"air": 0.0, "air_by": {}, "n_by": {}, "relayed": 0, "queue_drops": 0,
                      "collisions": 0, "air_relay": 0.0}
        self.stop = False

    def at(self, delay, fn, *args):
        heapq.heappush(self.ev, (self.t + delay, next(self.counter), fn, args))

    def run(self, until):
        while self.ev and not self.stop:
            t, _, fn, args = heapq.heappop(self.ev)
            if t > until:
                self.t = until
                break
            self.t = t
            fn(*args)

    def channel_busy_for(self, idx):
        now = self.t
        for tx in self.active:
            if tx.end > now and tx.start <= now - CAD_BLIND and self.hear[tx.sender, idx]:
                return True
        return False

    def transmit(self, st, pkt, a):
        tx = Tx(self.t, self.t + a, st.idx, pkt)
        self.active.append(tx)
        s = self.stats
        s["air"] += a
        s["air_by"][pkt.typ] = s["air_by"].get(pkt.typ, 0.0) + a
        s["n_by"][pkt.typ] = s["n_by"].get(pkt.typ, 0) + 1
        if pkt.relayed:
            s["air_relay"] += a
        self.at(a, self._end, tx)

    def _end(self, tx):
        st = self.stations[tx.sender]
        now = self.t
        self.active = [o for o in self.active if o.end > now - 1.0]
        mask = self.hear[tx.sender].copy()
        collided = False
        for o in self.active:
            if o is tx or o.start >= tx.end or o.end <= tx.start:
                continue
            mask &= ~self.hear[o.sender]
            mask[o.sender] = False
            collided = True
        if collided:
            self.stats["collisions"] += 1
        lost = self.loss.draw(tx.sender)
        mask &= ~lost
        mask[tx.sender] = False
        st.tx_done()
        for r in np.nonzero(mask)[0]:
            self.stations[r].receive(tx.pkt)


# ---------------------------------------------------------------- page / helpers
@dataclass
class Page:
    nbytes: int

    @property
    def K(self):
        return math.ceil(self.nbytes / CHUNK)

    def chunk_len(self, seq):
        return CHUNK if seq < self.K - 1 else self.nbytes - (self.K - 1) * CHUNK


def idle_limit(region):
    ca = airtime(region, CHUNK + 40)
    return 2.5 + ca * (4 + 2 * 3) + 4 * ca / REGIONS[region].duty


def resend_payload(missing):
    out, n = [], 0
    for s in missing:
        w = len(str(s)) + 1
        if n + w > RESEND_LIMIT:
            break
        out.append(s)
        n += w
    return out, n


@dataclass
class Result:
    done_t: list = field(default_factory=list)  # per listener: seconds from first request, inf = failed
    from_echo: int = 0


# ---------------------------------------------------------------- current Gorb (ARQ)
class ArqHost:
    def __init__(self, sim, st, page, fixed=False):
        self.sim, self.st, self.page = sim, st, page
        self.responses: set = set()
        self.building: set = set()
        self.fixed = fixed  # fix 1: never queue a chunk that is already waiting in the queue
        self.queued: set = set()

    def on_tx(self, pkt):
        if self.fixed and pkt.typ == "DATA":
            self.queued.discard((pkt.dst, pkt.msg, pkt.seq))

    def on_rx(self, pkt):
        if pkt.typ == "GET" and pkt.dst == 0:
            key = (pkt.src, pkt.msg)
            if key in self.responses:
                self.send(pkt, range(self.page.K))
            elif key not in self.building:
                self.building.add(key)
                self.sim.at(0.05, self._built, pkt)
            return True
        if pkt.typ == "RESEND" and pkt.dst == 0:
            if (pkt.src, pkt.msg) in self.responses:
                self.send(pkt, [s for s in pkt.info if 0 <= s < self.page.K])
            return True
        return False

    def _built(self, pkt):
        key = (pkt.src, pkt.msg)
        self.building.discard(key)
        self.responses.add(key)
        self.send(pkt, range(self.page.K))

    def send(self, req, seqs):
        ttl = max(3, req.hops + 1)
        for s in seqs:
            if self.fixed:
                k = (req.src, req.msg, s)
                if k in self.queued:
                    continue
                self.queued.add(k)
            self.st.enqueue(Pkt("DATA", 0, req.src, req.msg, s, self.page.K, req.rnd,
                                HDR + HOST_NAME + CALL_NAME + self.page.chunk_len(s), ttl))


class ArqNode:
    """A listener (or relay) running node.py's fetch, with optional Gorb Echo."""

    def __init__(self, sim, st, page, echo, res, want=True, fixed=False):
        self.sim, self.st, self.page, self.echo, self.res = sim, st, page, echo, res
        self.fixed = fixed  # fix 2: hearing the host busy with others' replies counts as activity
        self.echo_time = None  # when we stored a complete copy
        self.heard_gets: dict = {}
        self.overheard: dict = {}
        self.answered: set = set()
        self.responses: set = set()
        self.pend = None
        self.attempts = 0
        self.first_req = None
        self.final = not want
        self.IDLE = idle_limit(sim.region)

    def on_tx(self, pkt):
        pass

    def has_echo(self):
        return self.echo and self.echo_time is not None and self.sim.t - self.echo_time < ECHO_TTL

    # ---- Echo
    def echo_listen(self, pkt):
        if pkt.typ == "GET":
            self.heard_gets[(pkt.src, pkt.msg)] = pkt.dst
            return
        if pkt.typ != "DATA" or not pkt.total:
            return
        key = (pkt.dst, pkt.msg)
        self.answered.add(key)
        if self.heard_gets.get(key) != pkt.src:
            return
        o = self.overheard.setdefault(key, set())
        o.add(pkt.seq)
        if len(o) >= pkt.total:
            self.overheard.pop(key, None)
            self.echo_time = self.sim.t

    def echo_answer(self, pkt):
        req = (pkt.src, pkt.msg)
        if req in self.answered or (req in self.responses and pkt.rnd == 0) or not self.has_echo():
            return
        self.responses.add(req)
        self.send(pkt, range(self.page.K))

    def send(self, req, seqs):
        ttl = max(3, req.hops + 1)
        for s in seqs:
            self.st.enqueue(Pkt("DATA", 0, req.src, req.msg, s, self.page.K, req.rnd,
                                HDR + HOST_NAME + CALL_NAME + self.page.chunk_len(s), ttl))

    def on_rx(self, pkt):
        if self.echo:
            self.echo_listen(pkt)
        if pkt.typ == "RESEND" and (pkt.src, pkt.msg) in self.responses:
            self.send(pkt, [s for s in pkt.info if 0 <= s < self.page.K])
            return True
        if pkt.typ == "GET" and self.has_echo() and not self.fixed:
            d = self.sim.rng.uniform(0.2, 0.5) + 3 * airtime(self.sim.region, CHUNK + 40)
            self.sim.at(d, self.echo_answer, pkt)
            return True
        if pkt.typ == "DATA" and pkt.dst == self.st.idx:
            self.got(pkt)
            return True
        if self.fixed and pkt.typ == "DATA" and self.pend is not None:
            self.pend["last"] = self.sim.t
        return False

    # ---- fetch
    def start(self):
        if self.first_req is None:
            self.first_req = self.sim.t
        if self.has_echo():
            self.res.from_echo += 1
            self.finish(True)
            return
        self.attempts += 1
        msg = self.sim.rng.getrandbits(32)
        self.pend = {"msg": msg, "chunks": set(), "total": 0, "rnd": 0, "last": self.sim.t,
                     "start": self.sim.t}
        self.get(0)
        self.sim.at(self.IDLE + self.sim.rng.uniform(0, 0.1), self.idle, msg)
        if not self.fixed:  # fixed: only silence ends a fetch, not a busy channel
            self.sim.at(FETCH_TIMEOUT, self.timeout, msg)

    def get(self, rnd):
        self.st.enqueue(Pkt("GET", self.st.idx, 0, self.pend["msg"], 0, 0, rnd,
                            HDR + CALL_NAME + HOST_NAME + PATH_LEN, 3))

    def got(self, pkt):
        p = self.pend
        if p is None or pkt.msg != p["msg"]:
            return
        p["last"] = self.sim.t
        p["total"] = pkt.total
        p["chunks"].add(pkt.seq)
        if len(p["chunks"]) >= p["total"]:
            if self.echo:
                self.echo_time = self.sim.t
            self.finish(True)

    def idle(self, msg):
        p = self.pend
        if p is None or p["msg"] != msg:
            return
        if self.sim.t - p["last"] < self.IDLE - 1e-9:
            self.sim.at(p["last"] + self.IDLE - self.sim.t + self.sim.rng.uniform(0, 0.1),
                        self.idle, msg)
            return
        p["rnd"] += 1
        if p["rnd"] > (30 if self.fixed else MAX_ROUNDS):
            self.finish(False)
            return
        p["last"] = self.sim.t
        if p["total"]:
            missing = [s for s in range(p["total"]) if s not in p["chunks"]]
            seqs, nbytes = resend_payload(missing)
            self.st.enqueue(Pkt("RESEND", self.st.idx, 0, msg, 0, 0, p["rnd"],
                                HDR + CALL_NAME + HOST_NAME + nbytes, 3, info=seqs))
        else:
            self.get(p["rnd"])
        self.sim.at(self.IDLE + self.sim.rng.uniform(0, 0.1), self.idle, msg)

    def timeout(self, msg):
        if self.pend is not None and self.pend["msg"] == msg:
            self.finish(False)

    def finish(self, ok):
        self.pend = None
        if ok:
            self.res.done_t.append(self.sim.t - self.first_req)
            self.final = True
        elif self.attempts < ATTEMPTS:
            self.sim.at(RELOAD_DELAY, self.start)
        else:
            self.res.done_t.append(math.inf)
            self.final = True


# ---------------------------------------------------------------- shared stream (mc_list / rlnc)
class McHost:
    """One shared stream per page. mode 'list' (RESEND lists) or 'need' (RLNC)."""

    def __init__(self, sim, st, page, mode):
        self.sim, self.st, self.page, self.mode = sim, st, page, mode
        self.sess = None
        self.first_dst = None
        self.q_sys: set = set()
        self.q_rep = 0
        self.q_offer = False
        self.next_rep = page.K
        self.round = 0  # bumped per batch so repeated DATA isn't dropped as a duplicate

    def on_tx(self, pkt):
        if pkt.typ == "DATA":
            self.q_sys.discard(pkt.seq)
        elif pkt.typ == "CODED":
            self.q_rep -= 1
        elif pkt.typ == "OFFER":
            self.q_offer = False

    def offer(self):
        if not self.q_offer:
            self.q_offer = True
            self.round += 1
            self.st.enqueue(Pkt("OFFER", 0, -1, self.sess, 0, self.page.K, self.round & 0xFF,
                                HDR + HOST_NAME + 1 + PATH_LEN + 4, 3))

    def send_sys(self, seqs):
        self.round += 1
        for s in seqs:
            if s in self.q_sys:
                continue
            self.q_sys.add(s)
            self.st.enqueue(Pkt("DATA", 0, self.first_dst, self.sess, s, self.page.K,
                                self.round & 0xFF,
                                HDR + HOST_NAME + CALL_NAME + self.page.chunk_len(s), 3))

    def send_rep(self, n):
        for _ in range(n - self.q_rep):
            self.q_rep += 1
            self.st.enqueue(Pkt("CODED", 0, self.first_dst, self.sess, self.next_rep, self.page.K,
                                0, HDR + HOST_NAME + CALL_NAME + CODE_HDR + CHUNK, 3))
            self.next_rep += 1

    def idle_now(self):
        return not self.q_sys and self.q_rep <= 0

    def on_rx(self, pkt):
        if pkt.typ == "GET" and pkt.dst == 0:
            if self.sess is None:
                self.sess, self.first_dst = pkt.msg, pkt.src
                self.offer()
                self.send_sys(range(self.page.K))
            else:
                self.offer()
                if self.idle_now():  # a GET for a finished stream = "send me K"
                    if self.mode == "need":
                        self.send_rep(self.page.K)
                    else:
                        self.send_sys(range(self.page.K))
            return True
        if pkt.typ == "NEED" and pkt.dst == 0 and pkt.msg == self.sess:
            self.send_rep(pkt.info)
            return True
        if pkt.typ == "RESEND" and pkt.dst == 0 and pkt.msg == self.sess:
            self.send_sys([s for s in pkt.info if 0 <= s < self.page.K])
            return True
        return False


class McNode:
    def __init__(self, sim, st, page, mode, res, keep_partial, extra, want=True):
        self.sim, self.st, self.page, self.mode, self.res = sim, st, page, mode, res
        self.keep_partial = keep_partial
        self.extra = extra  # extra symbols beyond K this listener's decoder needs
        self.known: set = set()
        self.sess = None
        self.sys: set = set()
        self.nrep = 0
        self.max_sys = -1
        self.max_rep = -1
        self.done_time = None
        self.last_heard = -1e9
        self.wanting = False
        self.final = not want
        self.first_req = None
        self.attempts = 0
        self.rnd = 0
        self.last = 0.0
        self.gen = 0
        self.need_heard = 0
        self.IDLE = idle_limit(sim.region)

    def on_tx(self, pkt):
        pass

    def count(self):
        return len(self.sys) + self.nrep

    def complete(self):
        if self.mode == "need":
            return self.count() >= self.page.K + self.extra
        return len(self.sys) >= self.page.K

    def listening(self):
        return self.wanting or self.keep_partial

    def on_rx(self, pkt):
        t = pkt.typ
        if t == "GET" and pkt.dst == 0:
            self.known.add(pkt.msg)
            return False
        if t == "OFFER":
            self.known.add(pkt.msg)
            self.sess = pkt.msg
            if self.wanting:
                self.last = self.sim.t
                self.st.cancel(lambda q: q.typ == "GET")  # the page is already on the air
            return False
        if t in ("DATA", "CODED") and pkt.msg in self.known and self.listening():
            self.sess = pkt.msg
            if self.done_time is None:
                if t == "DATA":
                    self.sys.add(pkt.seq)
                    self.max_sys = max(self.max_sys, pkt.seq)
                else:
                    self.nrep += 1
                    self.max_rep = max(self.max_rep, pkt.seq)
                self.last_heard = self.sim.t
                if self.complete():
                    self.done_time = self.sim.t
            if self.wanting:
                self.last = self.sim.t
                self.st.cancel(lambda q: q.typ == "GET")
                if self.done_time is not None:
                    self.st.cancel(lambda q: q.typ in ("NEED", "RESEND"))
                    self.finish(True)
            return pkt.dst == self.st.idx
        if t == "NEED" and pkt.msg in self.known:
            self.need_heard = max(self.need_heard, pkt.info)
        return False

    # ---- fetch
    def start(self):
        if self.first_req is None:
            self.first_req = self.sim.t
        fresh = self.sim.t - self.last_heard < ECHO_TTL
        if self.done_time is not None and fresh:
            self.res.from_echo += 1
            self.finish(True)
            return
        if not fresh:  # forget stale partial state
            self.sys, self.nrep, self.max_sys, self.max_rep = set(), 0, -1, -1
            self.done_time = None
        self.attempts += 1
        self.wanting = True
        self.rnd = 0
        self.gen += 1
        self.last = self.sim.t
        self.msg = self.sim.rng.getrandbits(32)
        if self.sess is not None and self.count() > 0 and fresh:
            self.ask()  # we already hold part of it: just ask for the rest
        else:
            self.known.add(self.msg)
            self.get()
        self.sim.at(self.IDLE, self.idle, self.gen)
        self.sim.at(FETCH_TIMEOUT, self.timeout, self.gen)

    def get(self):
        self.st.enqueue(Pkt("GET", self.st.idx, 0, self.msg, 0, 0, self.rnd,
                            HDR + CALL_NAME + HOST_NAME + PATH_LEN, 3))

    def loss_est(self):
        sent = (self.max_sys + 1) + (self.max_rep - self.page.K + 1 if self.max_rep >= 0 else 0)
        if sent <= 0:
            return 0.3
        return min(0.75, max(0.0, 1 - self.count() / sent))

    def ask(self):
        if self.mode == "need":
            d = max(1, self.page.K - self.count())
            n = math.ceil(d / (1 - self.loss_est()))
            self.need_heard = 0
            self.sim.at(self.sim.rng.uniform(0, NEED_WINDOW), self._send_need, n, self.gen)
        else:
            self.sim.at(self.sim.rng.uniform(0, NEED_WINDOW), self._send_resend, self.gen)

    def _send_resend(self, gen):
        if gen != self.gen or not self.wanting:
            return
        missing = [s for s in range(self.page.K) if s not in self.sys]
        seqs, nbytes = resend_payload(missing)
        self.st.enqueue(Pkt("RESEND", self.st.idx, 0, self.sess, 0, 0, self.rnd,
                            HDR + CALL_NAME + HOST_NAME + nbytes, 3, info=seqs))

    def _send_need(self, n, gen):
        if gen != self.gen or not self.wanting or self.need_heard >= n:
            return  # someone already asked for at least as many: stay quiet
        self.st.enqueue(Pkt("NEED", self.st.idx, 0, self.sess, 0, 0, self.rnd,
                            HDR + CALL_NAME + HOST_NAME + 4, 3, info=n))

    def idle(self, gen):
        if gen != self.gen or not self.wanting:
            return
        if self.sim.t - self.last < self.IDLE - 1e-9:
            self.sim.at(self.last + self.IDLE - self.sim.t + self.sim.rng.uniform(0, 0.1),
                        self.idle, gen)
            return
        self.rnd += 1
        if self.rnd > MAX_ROUNDS:
            self.finish(False)
            return
        self.last = self.sim.t
        if self.sess is None or self.count() == 0:
            self.get()
        else:
            self.ask()
        self.sim.at(self.IDLE + self.sim.rng.uniform(0, 0.1), self.idle, gen)

    def timeout(self, gen):
        if gen == self.gen and self.wanting:
            self.finish(False)

    def finish(self, ok):
        self.wanting = False
        self.gen += 1
        if ok:
            self.res.done_t.append(self.sim.t - self.first_req)
            self.final = True
        elif self.attempts < ATTEMPTS:
            if not self.keep_partial:
                self.sys, self.nrep, self.max_sys, self.max_rep = set(), 0, -1, -1
            self.sim.at(RELOAD_DELAY, self.start)
        else:
            self.res.done_t.append(math.inf)
            self.final = True


# ---------------------------------------------------------------- scenario runner
def run(scheme, page_bytes, losses, *, region="US915", relay=False, relay_loss=0.05,
        burst=False, arrivals="together", window=1800.0, seed=0, extra_dist=None,
        t_max=4 * 3600.0, ll_loss=0.2, pace=False, timeout=FETCH_TIMEOUT):
    """One run. losses: per-listener loss rate (host<->listener, or relay<->listener).
    Returns dict of metrics."""
    rng = np.random.default_rng(seed)
    nl = len(losses)
    off = 2 if relay else 1
    n = off + nl
    hear = np.ones((n, n), dtype=bool)
    np.fill_diagonal(hear, False)
    p = np.full((n, n), ll_loss)
    for i, l in enumerate(losses):
        a = 1 if relay else 0
        p[a, off + i] = p[off + i, a] = l
    if relay:
        hear[0, off:] = hear[off:, 0] = False
        p[0, 1] = p[1, 0] = relay_loss
    if burst:
        loss = GilbertElliott(n, rng)
    else:
        loss = Bernoulli(p, rng)
    sim = Sim(n, hear, loss, region, seed)
    page = Page(page_bytes)
    res = Result()
    mode = {"arq": None, "arq_echo": None, "arq_fixed": None, "mc_list": "list", "rlnc": "need",
            "rlnc_echo": "need"}[scheme]
    global FETCH_TIMEOUT
    FETCH_TIMEOUT = timeout
    # PC host: next frame right after TXDONE over serial (20 ms). pace=True: leave
    # 0.5 s after each packet so a relay can repeat it before the next one comes.
    host = Station(sim, 0, HOST_NAME, gap=0.5 if pace else 0.02)
    sim.stations.append(host)
    fixed = scheme == "arq_fixed"
    echo = scheme in ("arq_echo", "arq_fixed")
    host.proto = ArqHost(sim, host, page, fixed) if mode is None else McHost(sim, host, page, mode)
    nodes = []
    if relay:
        rs = Station(sim, 1, CALL_NAME, relay=True, qcap=12, fifo=True)
        sim.stations.append(rs)
        if mode is None:
            rs.proto = ArqNode(sim, rs, page, echo, Result(), want=False, fixed=fixed)
        else:
            rs.proto = None
    pyrng = random.Random(seed + 7)
    for i in range(nl):
        st = Station(sim, off + i, CALL_NAME, fifo=True)
        sim.stations.append(st)
        if mode is None:
            nd = ArqNode(sim, st, page, echo, res, fixed=fixed)
        else:
            ex = pyrng.choice(extra_dist) if (extra_dist and mode == "need") else 0
            nd = McNode(sim, st, page, mode, res, keep_partial=(scheme == "rlnc_echo"), extra=ex)
        st.proto = nd
        nodes.append(nd)
        t0 = pyrng.uniform(0, 2.0) if arrivals == "together" else pyrng.uniform(0, window)
        sim.at(t0, nd.start)

    snap = {}

    def watchdog():
        if all(nd.final for nd in nodes):
            # Everyone has the page or gave up. Stop here; whatever is still
            # queued would go out anyway, so add its airtime as backlog.
            snap["air"] = sim.stats["air"]
            snap["t"] = sim.t
            snap["backlog"] = sum(airtime(region, e[2].size) for st in sim.stations for e in st.q)
            sim.stop = True
            return
        sim.at(5.0, watchdog)

    sim.at(5.0, watchdog)
    sim.run(t_max)
    FETCH_TIMEOUT = 120.0
    done = list(res.done_t) + [math.inf] * (nl - len(res.done_t))
    s = sim.stats
    ctrl_n = sum(s["n_by"].get(k, 0) for k in CTRL)
    ctrl_air = sum(s["air_by"].get(k, 0.0) for k in CTRL)
    fin = [d for d in done if math.isfinite(d)]
    return {
        "air": s["air"] + snap.get("backlog", 0.0), "air_final": snap.get("air", s["air"]), "t_final": snap.get("t", sim.t),
        "air_ctrl": ctrl_air, "air_relay": s["air_relay"], "ctrl_n": ctrl_n,
        "n_by": dict(s["n_by"]), "delivered": len(fin) / nl,
        "median": float(np.median(done)) if fin else math.inf,
        "p90": float(np.percentile(done, 90)) if len(fin) >= 0.9 * nl else math.inf,
        "done": done, "from_echo": res.from_echo, "collisions": s["collisions"],
        "queue_drops": s["queue_drops"], "sim_end": sim.t,
    }

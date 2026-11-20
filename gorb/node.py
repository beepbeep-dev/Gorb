"""A G.O.R.B station: hosts sites, fetches pages, and relays for others."""

from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import random
import re
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from . import media, rules
from .packet import Packet, PacketType
from .transport import Transport

log = logging.getLogger("gorb")

CHUNK = 180  # payload bytes per DATA packet; small chunks survive bad signal better
CANCEL_ROUND = 250  # RESEND round number used for "stop, I have it"
NARROW_FRAME = 250  # biggest packet every radio can carry (ESP-NOW: 250, LoRa: 255)
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?\.gorb$")
CALLSIGN_RE = re.compile(r"^[A-Za-z0-9/-]{3,20}$")


class GorbError(Exception):
    pass


def parse_url(url: str) -> tuple[str, str]:
    """gorb://cats.gorb/about.html -> ("cats.gorb", "/about.html")"""
    url = url.strip()
    if url.startswith("gorb://"):
        url = url[len("gorb://") :]
    domain, _, path = url.partition("/")
    domain = domain.lower()
    if not domain.endswith(".gorb"):
        domain += ".gorb"
    if not DOMAIN_RE.match(domain):
        raise GorbError(f"bad gorb domain: {domain!r}")
    return domain, "/" + path


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    body: bytes
    hops: int = 0
    seconds: float = 0.0
    from_echo: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Station:
    callsign: str
    domains: list[str]
    hops: int
    last_heard: float


@dataclass
class _Pending:
    domain: str
    path: str
    chunks: dict[int, bytes] = field(default_factory=dict)
    total: int = 0
    error: str | None = None
    hops: int = 0
    blob: bytes | None = None  # filled from an overheard copy (Gorb Echo)
    event: threading.Event = field(default_factory=threading.Event)
    last_activity: float = field(default_factory=time.monotonic)


class Node:
    def __init__(
        self,
        transport: Transport,
        callsign: str,
        site_dirs: dict[str, str | os.PathLike] | None = None,
        relay: bool = True,
        beacon_interval: float = 600.0,
        ttl: int = 3,
        image_budget: int = media.DEFAULT_IMAGE_BUDGET,
        motion_budget: int = media.DEFAULT_MOTION_BUDGET,
        hd_budget: int = media.DEFAULT_HD_BUDGET,
        echo: bool = True,
    ):
        if not CALLSIGN_RE.match(callsign):
            raise GorbError(f"bad callsign {callsign!r}")
        self.transport = transport
        self.media = media.MediaCache(image_budget, motion_budget, hd_budget)
        # Gorb Echo: keep what we overhear so neighbours (and we) get it free.
        self.echo = echo
        self._echo: OrderedDict[tuple[str, str], tuple[bytes, float]] = OrderedDict()
        self._echo_bytes = 0
        self._heard_gets: OrderedDict[tuple[str, int], tuple[str, str]] = OrderedDict()
        self._overheard: dict[tuple[str, int], dict] = {}
        self._answered: OrderedDict[tuple[str, int], bool] = OrderedDict()  # requests someone replied to
        self.callsign = callsign.upper()
        self.sites: dict[str, Path] = {}
        for domain, d in (site_dirs or {}).items():
            domain = domain.lower()
            if not DOMAIN_RE.match(domain):
                raise GorbError(f"bad gorb domain {domain!r} (use something like cats.gorb)")
            self.sites[domain] = Path(d).resolve()
        # On G.O.R.B Small the ESP32 relays by itself, even with no computer.
        self.relay = (relay and not getattr(transport, "relays_in_hardware", False)
                      and getattr(transport, "relay_useful", True))
        self.beacon_interval = beacon_interval
        self.ttl = ttl

        self.directory: dict[str, Station] = {}
        self.stats = {"rx": 0, "tx": 0, "relayed": 0, "served": 0, "blocked": 0,
                      "echo_hits": 0, "echo_served": 0}

        self._seen: OrderedDict[tuple, int] = OrderedDict()  # key -> times heard
        self._pending: dict[int, _Pending] = {}
        self._responses: OrderedDict[tuple, tuple[list[bytes], float]] = OrderedDict()
        self._building: set[tuple[str, int]] = set()
        # Chunks queued but not yet on air, per request: a repeated GET/RESEND
        # must not queue the same chunks again (lab finding: several people
        # asking at once used to snowball into hours of duplicate copies).
        self._unsent: dict[tuple[str, int], set[int]] = {}
        self._paced: set[tuple[str, int]] = set()  # requests that came through a relay
        self._cancelled: OrderedDict[tuple[str, int], bool] = OrderedDict()  # requester has it
        self._lock = threading.RLock()
        # Small things go first: requests, relays and short replies (pages,
        # picture previews) jump ahead of long replies (full pictures, video).
        self._txq: queue.PriorityQueue[tuple[int, int, Packet]] = queue.PriorityQueue()
        self._txcount = itertools.count()
        self._tx_time = 0.0
        self._started = time.monotonic()
        self._stop = threading.Event()

    # ------------------------------------------------------------------ life
    def start(self) -> "Node":
        if hasattr(self.transport, "set_local_names"):
            # Tell the radio which packets are ours so it doesn't relay them.
            self.transport._names = [self.callsign, *self.sites]
        self.transport.start(self._on_frame)
        if self.sites:
            threading.Thread(target=self._prepare_media, daemon=True, name="gorb-media").start()
        threading.Thread(target=self._tx_loop, daemon=True, name="gorb-tx").start()
        if self.beacon_interval > 0:
            threading.Thread(target=self._beacon_loop, daemon=True, name="gorb-beacon").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.transport.close()

    # --------------------------------------------------------------- sending
    def _send(self, pkt: Packet) -> None:
        self._remember(pkt.key)
        self._queue(pkt)

    def _queue(self, pkt: Packet) -> None:
        prio = pkt.total if pkt.type == PacketType.DATA else 0
        self._txq.put((prio, next(self._txcount), pkt))

    def _tx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, pkt = self._txq.get(timeout=0.5)
            except queue.Empty:
                continue
            if pkt.type == PacketType.DATA and (pkt.dst, pkt.msg_id) in self._cancelled:
                with self._lock:
                    left = self._unsent.get((pkt.dst, pkt.msg_id))
                    if left is not None:
                        left.discard(pkt.seq)
                continue  # they already have the page
            frame = pkt.encode()
            # Rule 3 / rule 5: listen before talking and never hog the channel.
            waited = 0.0
            while self.transport.busy() and waited < 10:
                delay = random.uniform(0.05, 0.25)
                time.sleep(delay)
                waited += delay
            elapsed = time.monotonic() - self._started
            air = self.transport.airtime(len(frame))
            duty = min(rules.MAX_TX_DUTY, getattr(self.transport, "max_duty", 1.0))
            if elapsed > 5 and (self._tx_time + air) / elapsed > duty:
                time.sleep(air)
            try:
                self.transport.send(frame)
            except Exception as e:  # keep the station alive on audio hiccups
                log.warning("transmit failed: %s", e)
                continue
            self._tx_time += air
            self.stats["tx"] += 1
            if pkt.type == PacketType.DATA:
                req = (pkt.dst, pkt.msg_id)
                with self._lock:
                    left = self._unsent.get(req)
                    if left is not None:
                        left.discard(pkt.seq)
                        if not left:
                            self._unsent.pop(req, None)
                    paced = req in self._paced
                if paced:
                    # The reply goes through a relay, which can only repeat it
                    # in the gaps between our packets: leave it some room.
                    time.sleep(max(0.3, 2 * air))
            log.debug("TX %s %s->%s #%d seq=%d", pkt.type.name, pkt.src, pkt.dst, pkt.msg_id, pkt.seq)

    def _beacon_loop(self) -> None:
        # First beacon soon after start, then on the interval. Beacons also
        # serve as station identification (radio rules usually need one).
        self._stop.wait(random.uniform(0.2, 1.0))
        while not self._stop.is_set():
            self.beacon()
            self._stop.wait(self.beacon_interval)

    def beacon(self) -> None:
        payload = json.dumps({"call": self.callsign, "domains": sorted(self.sites)}).encode()
        self._send(Packet(PacketType.BEACON, self.callsign, "*", payload,
                          msg_id=random.getrandbits(32), ttl=self.ttl))

    # -------------------------------------------------------------- receiving
    def _remember(self, key: tuple) -> int:
        with self._lock:
            n = self._seen.get(key, 0) + 1
            self._seen[key] = n
            self._seen.move_to_end(key)
            while len(self._seen) > 5000:
                self._seen.popitem(last=False)
            return n

    def _on_frame(self, frame: bytes) -> None:
        try:
            pkt = Packet.decode(frame)
        except ValueError:
            return
        self.stats["rx"] += 1
        if self._remember(pkt.key) > 1:
            return  # duplicate (another relay already repeated it)
        log.debug("RX %s %s->%s #%d seq=%d hops=%d", pkt.type.name, pkt.src, pkt.dst,
                  pkt.msg_id, pkt.seq, pkt.hops)
        if self.echo:
            self._echo_listen(pkt)
        if pkt.type in (PacketType.DATA, PacketType.WAIT) and pkt.dst != self.callsign:
            # The host we're waiting for is busy sending to someone else: it's
            # alive, so keep waiting instead of asking again.
            now = time.monotonic()
            with self._lock:
                for p in self._pending.values():
                    if p.domain == pkt.src:
                        p.last_activity = now
        for_me = self._handle(pkt)
        if self.relay and not for_me and pkt.ttl > 0:
            self._schedule_relay(pkt)

    def _handle(self, pkt: Packet) -> bool:
        """Process a packet. Returns True if it was addressed only to us."""
        t = pkt.type
        if t == PacketType.BEACON:
            try:
                info = json.loads(pkt.payload)
                domains = [d for d in info.get("domains", []) if DOMAIN_RE.match(d)]
            except (ValueError, AttributeError):
                return False
            self.directory[pkt.src] = Station(pkt.src, domains, pkt.hops, time.time())
            return False
        if t == PacketType.GET and pkt.dst in self.sites:
            self._serve(pkt)
            return True
        if t == PacketType.RESEND and (pkt.dst in self.sites or (pkt.src, pkt.msg_id) in self._responses):
            self._resend(pkt)
            return True
        if t == PacketType.GET and self.echo and self._echo_get(pkt.dst, self._payload_path(pkt)):
            self._echo_answer(pkt)
            return True  # answered from our echo: no need to pass the request on
        if t in (PacketType.DATA, PacketType.ERROR, PacketType.WAIT) and pkt.dst == self.callsign:
            self._receive_response(pkt)
            return True
        return False

    # ----------------------------------------------------------- Gorb Echo
    # Radio is a broadcast: every station in range hears every reply, even
    # ones meant for someone else. Echo keeps those overheard replies, so a
    # page or picture somebody nearby already loaded costs no airtime again.
    ECHO_TTL = 600.0  # seconds an overheard reply stays fresh
    ECHO_MAX_BYTES = 8 * 1024 * 1024

    @staticmethod
    def _payload_path(pkt: Packet) -> str:
        return pkt.payload.decode(errors="replace") or "/"

    def _echo_get(self, domain: str, path: str) -> bytes | None:
        with self._lock:
            hit = self._echo.get((domain, path))
            if hit and time.time() - hit[1] < self.ECHO_TTL:
                self._echo.move_to_end((domain, path))
                return hit[0]
        return None

    def _echo_store(self, domain: str, path: str, blob: bytes) -> None:
        with self._lock:
            old = self._echo.pop((domain, path), None)
            if old:
                self._echo_bytes -= len(old[0])
            self._echo[(domain, path)] = (blob, time.time())
            self._echo_bytes += len(blob)
            while self._echo_bytes > self.ECHO_MAX_BYTES and self._echo:
                _, (b, _) = self._echo.popitem(last=False)
                self._echo_bytes -= len(b)
            # Anyone here still waiting for this page can stop now: we just
            # overheard the whole thing sent to someone else.
            for p in self._pending.values():
                if p.domain == domain and p.path == path and p.blob is None:
                    p.blob = blob
                    p.event.set()

    def _echo_listen(self, pkt: Packet) -> None:
        """Collect replies we overhear (including ones addressed to others)."""
        if pkt.type == PacketType.GET:
            with self._lock:
                self._heard_gets[(pkt.src, pkt.msg_id)] = (pkt.dst, self._payload_path(pkt))
                while len(self._heard_gets) > 500:
                    self._heard_gets.popitem(last=False)
            return
        if pkt.type != PacketType.DATA or not pkt.total:
            return
        key = (pkt.dst, pkt.msg_id)
        with self._lock:
            self._answered[key] = True
            while len(self._answered) > 500:
                self._answered.popitem(last=False)
            req = self._heard_gets.get(key)
            if req is None or req[0] != pkt.src:
                return
            o = self._overheard.setdefault(key, {"total": pkt.total, "chunks": {}, "t": time.time()})
            o["chunks"][pkt.seq] = pkt.payload
            if len(o["chunks"]) < o["total"]:
                if len(self._overheard) > 50:  # forget the stalest half-heard replies
                    for k in sorted(self._overheard, key=lambda k: self._overheard[k]["t"])[:10]:
                        self._overheard.pop(k, None)
                return
            self._overheard.pop(key, None)
        blob = b"".join(o["chunks"][i] for i in range(o["total"]))
        try:
            head = zlib.decompress(blob).partition(b"\n")[0]
            if json.loads(head).get("status") != 200:
                return
        except (zlib.error, ValueError):
            return
        self._echo_store(req[0], req[1], blob)
        log.debug("echo: kept %s%s (%d bytes)", req[0], req[1], len(blob))

    def _echo_answer(self, pkt: Packet) -> None:
        """Answer someone's request from what we overheard, unless the real
        host turns out to be close enough to answer first."""
        def go():
            req = (pkt.src, pkt.msg_id)
            with self._lock:
                if req in self._answered or (req in self._responses and pkt.round == 0):
                    return  # the host (or another echo) is already answering
            blob = self._echo_get(pkt.dst, self._payload_path(pkt))
            if blob is None:
                return
            size = self._chunk_size(pkt.dst, pkt.src, pkt.narrow)
            chunks = [blob[i : i + size] for i in range(0, len(blob), size)] or [b""]
            with self._lock:
                self._responses[(pkt.src, pkt.msg_id)] = (chunks, time.time())
            self.stats["echo_served"] += 1
            log.info("echo: answering %s for %s%s from overheard copy", pkt.src, pkt.dst,
                     self._payload_path(pkt))
            self._send_chunks(pkt, chunks, range(len(chunks)), pkt.round)

        delay = random.uniform(0.2, 0.5) + 3 * self.transport.airtime(CHUNK + 40)
        threading.Timer(delay, go).start()

    def _prepare_media(self) -> None:
        """Squeeze every picture and video ahead of time so nobody waits on it."""
        for domain, root in self.sites.items():
            for f in sorted(root.rglob("*")):
                if self._stop.is_set():
                    return
                if not f.is_file() or not media.is_media(f):
                    continue
                variants = media.VIDEO_VARIANTS if media.is_video(f) else media.IMAGE_VARIANTS
                for v in variants:
                    try:
                        self.media.get(f, v)
                    except media.MediaError as e:
                        log.warning("can't prepare %s (%s): %s", f.name, v, e)
                        break
        log.info("pictures and videos ready")

    def _schedule_relay(self, pkt: Packet) -> None:
        # Rule 2: repeat packets for people who can't hear each other. Wait a
        # random moment first; if other relays already repeated it, stay quiet.
        def go():
            if self._seen.get(pkt.key, 0) > 2:
                return
            self.stats["relayed"] += 1
            self._queue(pkt.relayed())

        delay = random.uniform(0.05, 0.4) + self.transport.airtime(len(pkt.payload) + 40) * 0.5
        threading.Timer(delay, go).start()

    # ---------------------------------------------------------------- hosting
    def _resolve_path(self, domain: str, path: str) -> Path | None:
        root = self.sites[domain]
        rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return None  # tried to escape the site folder
        if target.is_dir():
            target = target / "index.html"
        return target if target.is_file() else None

    def _build_response(self, domain: str, path: str) -> bytes:
        target = self._resolve_path(domain, path)
        if target is None:
            status, ctype, body = 404, "text/html", (
                f"<h1>404 - not on this frequency</h1><p>{domain}{path} was not found.</p>"
            ).encode()
        elif media.is_media(target):
            # Gorb Pixels / Gorb Motion: shrink to fit the radio first.
            query = dict(p.partition("=")[::2] for p in path.partition("?")[2].split("&") if p)
            variant = query.get("gorb", "standard")
            if variant not in media.IMAGE_VARIANTS + media.VIDEO_VARIANTS:
                variant = "standard"
            try:
                ctype, body = self.media.get(target, variant)
                status = 200
            except media.MediaError as e:
                log.warning("can't send %s%s: %s", domain, path, e)
                status, ctype, body = 415, "text/html", f"<p>{e}</p>".encode()
        else:
            body = target.read_bytes()
            ctype = {
                ".html": "text/html", ".htm": "text/html", ".txt": "text/plain",
                ".css": "text/css", ".svg": "image/svg+xml",
            }.get(target.suffix.lower(), "application/octet-stream")
            status = 200
            if ctype.startswith("text") or ctype.endswith("xml"):
                reason = rules.check_content(body.decode("utf-8", errors="replace"))
                if reason:
                    self.stats["blocked"] += 1
                    log.warning("refusing to serve %s%s: %s", domain, path, reason)
                    status, ctype = 451, "text/html"
                    body = f"<h1>Blocked</h1><p>This page breaks the G.O.R.B rules ({reason}).</p>".encode()
        head = json.dumps({"status": status, "type": ctype}).encode()
        blob = zlib.compress(head + b"\n" + body, 9)
        if len(blob) > rules.MAX_RESPONSE_BYTES:
            msg = b'{"status":413,"type":"text/html"}\n<h1>413 - page too big for radio</h1>'
            blob = zlib.compress(msg)
        return blob

    def _serve(self, pkt: Packet) -> None:
        key = (pkt.src, pkt.msg_id)
        with self._lock:
            cached = self._responses.get(key)
            if cached is None:
                if key in self._building:
                    return  # already working on it (the requester asked again)
                self._building.add(key)
        if cached is None:
            # Build the reply off the radio thread: squeezing a video can take
            # a while, and the station must keep listening meanwhile.
            threading.Thread(target=self._serve_build, args=(pkt,), daemon=True).start()
        else:
            # The requester asked again: they heard nothing, so send it all again.
            chunks, _ = cached
            self._send_chunks(pkt, chunks, range(len(chunks)), pkt.round)

    def _serve_build(self, pkt: Packet) -> None:
        key = (pkt.src, pkt.msg_id)
        done = threading.Event()

        def keep_them_waiting():
            n = 0
            while not done.wait(2.0):
                n += 1
                self._send(Packet(PacketType.WAIT, pkt.dst, pkt.src, b"", pkt.msg_id, seq=n,
                                  ttl=max(self.ttl, pkt.hops + 1)))

        threading.Thread(target=keep_them_waiting, daemon=True).start()
        try:
            blob = self._build_response(pkt.dst, pkt.payload.decode(errors="replace") or "/")
            size = self._chunk_size(pkt.dst, pkt.src, pkt.narrow)
            chunks = [blob[i : i + size] for i in range(0, len(blob), size)] or [b""]
            with self._lock:
                self._responses[key] = (chunks, time.time())
                while len(self._responses) > 50:
                    self._responses.popitem(last=False)
            self.stats["served"] += 1
            log.info("serving %s%s to %s (%d chunks)", pkt.dst,
                     pkt.payload.decode(errors="replace"), pkt.src, len(chunks))
            self._send_chunks(pkt, chunks, range(len(chunks)), pkt.round)
        finally:
            done.set()
            with self._lock:
                self._building.discard(key)

    def _resend(self, pkt: Packet) -> None:
        if not pkt.payload:  # "I have it now", possibly before we even built it
            with self._lock:
                self._cancelled[(pkt.src, pkt.msg_id)] = True
                while len(self._cancelled) > 500:
                    self._cancelled.popitem(last=False)
            return
        with self._lock:
            cached = self._responses.get((pkt.src, pkt.msg_id))
        if cached is None:
            return
        chunks, _ = cached
        try:
            wanted = [int(s) for s in pkt.payload.decode().split(",") if s]
        except ValueError:
            return
        if not wanted:
            return
        self._send_chunks(pkt, chunks, [s for s in wanted if 0 <= s < len(chunks)], pkt.round)

    @property
    def narrow(self) -> bool:
        """Are we on a small-packet radio (so replies to us must be small)?"""
        mf = getattr(self.transport, "max_frame", None)
        return mf is None or mf < 512

    def _chunk_size(self, src: str, dst: str, narrow: bool = False) -> int:
        """Biggest chunk that still fits in one radio frame on the way."""
        max_frame = getattr(self.transport, "max_frame", None)
        chunk = getattr(self.transport, "chunk_size", CHUNK)  # Wi-Fi takes far bigger pieces
        if narrow:  # the requester is behind LoRa/ESP-NOW somewhere: stay small
            max_frame = min(max_frame or NARROW_FRAME, NARROW_FRAME)
            chunk = min(chunk, CHUNK)
        if not max_frame:
            return chunk
        overhead = Packet(PacketType.DATA, src, dst).encode()
        return max(32, min(chunk, max_frame - len(overhead)))

    def _send_chunks(self, req: Packet, chunks: list[bytes], seqs, rnd: int) -> None:
        # Reply as far as the request travelled: a request we heard directly
        # gets a reply nobody needs to relay (lab finding: pointless relays
        # tripled channel use). Retries get one extra hop in case the link
        # only works one way.
        ttl = req.hops + (1 if rnd > 0 else 0)
        key = (req.src, req.msg_id)
        with self._lock:
            unsent = self._unsent.setdefault(key, set())
            todo = [s for s in seqs if s not in unsent]  # don't queue what's still waiting
            unsent.update(todo)
            if req.hops > 0 and getattr(self.transport, "relay_useful", True):
                self._paced.add(key)
                if len(self._paced) > 200:
                    self._paced.pop()
        for seq in todo:
            self._send(Packet(PacketType.DATA, req.dst, req.src, chunks[seq], req.msg_id,
                              seq, len(chunks), ttl=ttl, round=rnd))

    # ---------------------------------------------------------------- browsing
    def _receive_response(self, pkt: Packet) -> None:
        with self._lock:
            p = self._pending.get(pkt.msg_id)
            if p is None or pkt.src != p.domain:
                return
            p.last_activity = time.monotonic()
            if pkt.type == PacketType.WAIT:
                return  # host is still preparing it: keep waiting
            p.hops = max(p.hops, pkt.hops)
            if pkt.type == PacketType.ERROR:
                p.error = pkt.payload.decode(errors="replace")
                p.event.set()
                return
            p.total = pkt.total
            p.chunks[pkt.seq] = pkt.payload
            if p.total and len(p.chunks) >= p.total:
                p.event.set()

    def fetch(self, url: str, timeout: float = 900.0, retries: int = 6) -> Page:
        """Request a page over the air and wait for it to arrive."""
        domain, path = parse_url(url)
        started = time.monotonic()
        if domain in self.sites:  # our own site: no need to transmit
            blob = self._build_response(domain, path)
            return self._decode_page(f"gorb://{domain}{path}", blob, 0, 0.0)

        if len(path.encode()) > self._chunk_size(self.callsign, domain):
            raise GorbError("address too long to send over the radio")
        if self.echo:
            blob = self._echo_get(domain, path)
            if blob is not None:  # someone nearby loaded it recently and we overheard it
                self.stats["echo_hits"] += 1
                page = self._decode_page(f"gorb://{domain}{path}", blob, 0, 0.0)
                page.from_echo = True
                return page
        msg_id = random.getrandbits(32)
        p = _Pending(domain, path)
        with self._lock:
            self._pending[msg_id] = p
        chunk_air = self.transport.airtime(CHUNK + 40)
        try:
            rnd = 0
            self._send(Packet(PacketType.GET, self.callsign, domain, path.encode(), msg_id,
                              ttl=self.ttl, narrow=self.narrow))
            while True:
                # Wait while chunks keep trickling in.
                # Hosts on duty-cycle limited bands may pause between chunks.
                duty = getattr(self.transport, "max_duty", 1.0)
                idle_limit = 2.5 + chunk_air * (4 + 2 * self.ttl) + 4 * chunk_air / duty
                while not p.event.is_set():
                    p.event.wait(0.1)
                    if time.monotonic() - p.last_activity > idle_limit:
                        break
                    if time.monotonic() - started > timeout:
                        raise GorbError(f"timed out waiting for {domain}")
                if p.event.is_set():
                    break
                rnd += 1
                if rnd > retries:
                    if p.total:
                        raise GorbError(
                            f"lost signal: got {len(p.chunks)}/{p.total} chunks from {domain}")
                    raise GorbError(f"no answer from {domain} (nobody in range hosts it?)")
                p.last_activity = time.monotonic()
                if p.total:
                    missing = [str(s) for s in range(p.total) if s not in p.chunks]
                    log.info("asking %s to resend %d chunk(s)", domain, len(missing))
                    # Keep each RESEND packet small; ask for the first batch.
                    payload = ""
                    limit = self._chunk_size(self.callsign, domain)
                    for s in missing:
                        if len(payload) + len(s) + 1 > limit:
                            break
                        payload += s + ","
                    self._send(Packet(PacketType.RESEND, self.callsign, domain, payload.encode(),
                                      msg_id, ttl=self.ttl, round=rnd))
                else:
                    log.info("no reply from %s yet, asking again", domain)
                    self._send(Packet(PacketType.GET, self.callsign, domain, path.encode(),
                                      msg_id, ttl=self.ttl, round=rnd, narrow=self.narrow))
            if p.error:
                raise GorbError(p.error)
            if p.blob is not None:
                self.stats["echo_hits"] += 1
                # Tell the host to stop sending our copy: an empty RESEND means
                # "I need nothing more" (older hosts simply ignore it).
                self._send(Packet(PacketType.RESEND, self.callsign, domain, b"", msg_id,
                                  ttl=self.ttl, round=CANCEL_ROUND))
                page = self._decode_page(f"gorb://{domain}{path}", p.blob, p.hops,
                                         time.monotonic() - started)
                page.from_echo = True
                return page
            blob = b"".join(p.chunks[i] for i in range(p.total))
            page = self._decode_page(f"gorb://{domain}{path}", blob, p.hops,
                                     time.monotonic() - started)
            if self.echo and page.status == 200:
                self._echo_store(domain, path, blob)
            return page
        finally:
            with self._lock:
                self._pending.pop(msg_id, None)

    def _decode_page(self, url: str, blob: bytes, hops: int, seconds: float) -> Page:
        try:
            raw = zlib.decompress(blob)
            head, _, body = raw.partition(b"\n")
            meta = json.loads(head)
        except (zlib.error, ValueError) as e:
            raise GorbError(f"garbled response: {e}") from e
        page = Page(url, int(meta.get("status", 200)), str(meta.get("type", "text/html")),
                    body, hops, seconds)
        if page.content_type.startswith("text") or page.content_type.endswith("xml"):
            reason = rules.check_content(page.text)
            if reason:  # rule 1 is enforced on the receiving side too
                self.stats["blocked"] += 1
                page = Page(url, 451, "text/html",
                            f"<h1>Blocked</h1><p>G.O.R.B hid this page ({reason}).</p>".encode(),
                            hops, seconds)
        return page

    def known_domains(self) -> dict[str, Station]:
        out: dict[str, Station] = {}
        for st in self.directory.values():
            for d in st.domains:
                out[d] = st
        for d in self.sites:
            out[d] = Station(self.callsign, list(self.sites), 0, time.time())
        return out

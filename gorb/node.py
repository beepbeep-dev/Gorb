"""A G.O.R.B station: hosts sites, fetches pages, and relays for others."""

from __future__ import annotations

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

from . import rules
from .packet import Packet, PacketType
from .transport import Transport

log = logging.getLogger("gorb")

CHUNK = 180  # payload bytes per DATA packet; small chunks survive bad signal better
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
    ):
        if not CALLSIGN_RE.match(callsign):
            raise GorbError(f"bad callsign {callsign!r}")
        self.transport = transport
        self.callsign = callsign.upper()
        self.sites: dict[str, Path] = {}
        for domain, d in (site_dirs or {}).items():
            domain = domain.lower()
            if not DOMAIN_RE.match(domain):
                raise GorbError(f"bad gorb domain {domain!r} (use something like cats.gorb)")
            self.sites[domain] = Path(d).resolve()
        self.relay = relay
        self.beacon_interval = beacon_interval
        self.ttl = ttl

        self.directory: dict[str, Station] = {}
        self.stats = {"rx": 0, "tx": 0, "relayed": 0, "served": 0, "blocked": 0}

        self._seen: OrderedDict[tuple, int] = OrderedDict()  # key -> times heard
        self._pending: dict[int, _Pending] = {}
        self._responses: OrderedDict[tuple, tuple[list[bytes], float]] = OrderedDict()
        self._lock = threading.RLock()
        self._txq: queue.Queue[Packet] = queue.Queue()
        self._tx_time = 0.0
        self._started = time.monotonic()
        self._stop = threading.Event()

    # ------------------------------------------------------------------ life
    def start(self) -> "Node":
        self.transport.start(self._on_frame)
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
        self._txq.put(pkt)

    def _tx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pkt = self._txq.get(timeout=0.5)
            except queue.Empty:
                continue
            frame = pkt.encode()
            # Rule 3 / rule 5: listen before talking and never hog the channel.
            waited = 0.0
            while self.transport.busy() and waited < 10:
                delay = random.uniform(0.05, 0.25)
                time.sleep(delay)
                waited += delay
            elapsed = time.monotonic() - self._started
            air = self.transport.airtime(len(frame))
            if elapsed > 5 and (self._tx_time + air) / elapsed > rules.MAX_TX_DUTY:
                time.sleep(air)
            try:
                self.transport.send(frame)
            except Exception as e:  # keep the station alive on audio hiccups
                log.warning("transmit failed: %s", e)
                continue
            self._tx_time += air
            self.stats["tx"] += 1
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
        if t == PacketType.RESEND and pkt.dst in self.sites:
            self._resend(pkt)
            return True
        if t in (PacketType.DATA, PacketType.ERROR) and pkt.dst == self.callsign:
            self._receive_response(pkt)
            return True
        return False

    def _schedule_relay(self, pkt: Packet) -> None:
        # Rule 2: repeat packets for people who can't hear each other. Wait a
        # random moment first; if other relays already repeated it, stay quiet.
        def go():
            if self._seen.get(pkt.key, 0) > 2:
                return
            self.stats["relayed"] += 1
            self._txq.put(pkt.relayed())

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
            blob = self._build_response(pkt.dst, pkt.payload.decode(errors="replace") or "/")
            chunks = [blob[i : i + CHUNK] for i in range(0, len(blob), CHUNK)] or [b""]
            with self._lock:
                self._responses[key] = (chunks, time.time())
                while len(self._responses) > 50:
                    self._responses.popitem(last=False)
            self.stats["served"] += 1
            log.info("serving %s%s to %s (%d chunks)", pkt.dst,
                     pkt.payload.decode(errors="replace"), pkt.src, len(chunks))
            self._send_chunks(pkt, chunks, range(len(chunks)), 0)
        else:
            # The requester asked again: they heard nothing, so send it all again.
            chunks, _ = cached
            self._send_chunks(pkt, chunks, range(len(chunks)), pkt.round)

    def _resend(self, pkt: Packet) -> None:
        with self._lock:
            cached = self._responses.get((pkt.src, pkt.msg_id))
        if cached is None:
            return
        chunks, _ = cached
        try:
            wanted = [int(s) for s in pkt.payload.decode().split(",") if s]
        except ValueError:
            return
        self._send_chunks(pkt, chunks, [s for s in wanted if 0 <= s < len(chunks)], pkt.round)

    def _send_chunks(self, req: Packet, chunks: list[bytes], seqs, rnd: int) -> None:
        ttl = max(self.ttl, req.hops + 1)
        for seq in seqs:
            self._send(Packet(PacketType.DATA, req.dst, req.src, chunks[seq], req.msg_id,
                              seq, len(chunks), ttl=ttl, round=rnd))

    # ---------------------------------------------------------------- browsing
    def _receive_response(self, pkt: Packet) -> None:
        with self._lock:
            p = self._pending.get(pkt.msg_id)
            if p is None or pkt.src != p.domain:
                return
            p.last_activity = time.monotonic()
            p.hops = max(p.hops, pkt.hops)
            if pkt.type == PacketType.ERROR:
                p.error = pkt.payload.decode(errors="replace")
                p.event.set()
                return
            p.total = pkt.total
            p.chunks[pkt.seq] = pkt.payload
            if p.total and len(p.chunks) >= p.total:
                p.event.set()

    def fetch(self, url: str, timeout: float = 120.0, retries: int = 6) -> Page:
        """Request a page over the air and wait for it to arrive."""
        domain, path = parse_url(url)
        started = time.monotonic()
        if domain in self.sites:  # our own site: no need to transmit
            blob = self._build_response(domain, path)
            return self._decode_page(f"gorb://{domain}{path}", blob, 0, 0.0)

        msg_id = random.getrandbits(32)
        p = _Pending(domain, path)
        with self._lock:
            self._pending[msg_id] = p
        chunk_air = self.transport.airtime(CHUNK + 40)
        try:
            rnd = 0
            self._send(Packet(PacketType.GET, self.callsign, domain, path.encode(), msg_id,
                              ttl=self.ttl))
            while True:
                # Wait while chunks keep trickling in.
                idle_limit = 2.5 + chunk_air * (4 + 2 * self.ttl)
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
                    for s in missing:
                        if len(payload) + len(s) + 1 > CHUNK:
                            break
                        payload += s + ","
                    self._send(Packet(PacketType.RESEND, self.callsign, domain, payload.encode(),
                                      msg_id, ttl=self.ttl, round=rnd))
                else:
                    log.info("no reply from %s yet, asking again", domain)
                    self._send(Packet(PacketType.GET, self.callsign, domain, path.encode(),
                                      msg_id, ttl=self.ttl, round=rnd))
            if p.error:
                raise GorbError(p.error)
            blob = b"".join(p.chunks[i] for i in range(p.total))
            return self._decode_page(f"gorb://{domain}{path}", blob, p.hops,
                                     time.monotonic() - started)
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

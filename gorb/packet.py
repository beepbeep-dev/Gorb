"""G.O.R.B packet format (what goes inside one modem frame).

    magic    2  b"GB"
    version  1
    type     1  see PacketType
    ttl      1  how many more relays may repeat this packet
    hops     1  how many relays already repeated it
    round    1  retry counter, so a retransmission isn't dropped as a duplicate
    msg_id   4  ties a request to its response
    seq      2  chunk number
    total    2  number of chunks in the response
    src      1 + n  sender callsign (or domain)
    dst      1 + n  destination callsign / domain ("*" = everyone)
    payload  rest
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from enum import IntEnum

MAGIC = b"GB"
VERSION = 1
_HEAD = struct.Struct(">2sBBBBBIHH")


class PacketType(IntEnum):
    BEACON = 1  # "I'm here, I host these domains" (also our station ID)
    GET = 2  # request a page: dst = domain, payload = path
    DATA = 3  # one chunk of a response
    RESEND = 4  # "I'm missing these chunks": payload = comma separated seqs
    ERROR = 5  # request failed: payload = reason


@dataclass(frozen=True)
class Packet:
    type: PacketType
    src: str
    dst: str
    payload: bytes = b""
    msg_id: int = 0
    seq: int = 0
    total: int = 0
    ttl: int = 3
    hops: int = 0
    round: int = 0

    def encode(self) -> bytes:
        src = self.src.encode()[:255]
        dst = self.dst.encode()[:255]
        return (
            _HEAD.pack(
                MAGIC, VERSION, int(self.type), self.ttl, self.hops, self.round,
                self.msg_id, self.seq, self.total,
            )
            + bytes([len(src)]) + src
            + bytes([len(dst)]) + dst
            + self.payload
        )

    @classmethod
    def decode(cls, data: bytes) -> "Packet":
        if len(data) < _HEAD.size + 2:
            raise ValueError("packet too short")
        magic, ver, ptype, ttl, hops, rnd, msg_id, seq, total = _HEAD.unpack_from(data)
        if magic != MAGIC or ver != VERSION:
            raise ValueError("not a gorb packet")
        i = _HEAD.size
        n = data[i]
        src = data[i + 1 : i + 1 + n].decode(errors="replace")
        i += 1 + n
        n = data[i]
        dst = data[i + 1 : i + 1 + n].decode(errors="replace")
        i += 1 + n
        return cls(PacketType(ptype), src, dst, data[i:], msg_id, seq, total, ttl, hops, rnd)

    @property
    def key(self) -> tuple:
        """Identity used for duplicate detection (ignores ttl/hops)."""
        return (self.type, self.src, self.dst, self.msg_id, self.seq, self.round)

    def relayed(self) -> "Packet":
        return replace(self, ttl=self.ttl - 1, hops=self.hops + 1)

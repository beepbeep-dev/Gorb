"""Command line for G.O.R.B.

    gorb browse --callsign N0CALL                     open the browser (http://127.0.0.1:8088)
    gorb host   --callsign N0CALL --site cats.gorb=./site
    gorb relay  --callsign N0CALL                     just help carry signals (rule 2)
    gorb get    --callsign N0CALL gorb://cats.gorb    print a page in the terminal
    gorb demo                                         host + relay + browser, all simulated
    gorb encode page.html out.wav / gorb decode in.wav   offline / SDR use

Add --radio to use a real radio through your sound card instead of the
simulated band (needs: pip install sounddevice).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

from . import __version__, rules
from .modem import Demodulator, ModemConfig, Modulator
from .node import GorbError, Node
from .transport import AudioTransport, SimTransport, Transport


def _add_radio_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--callsign", required=True,
                   help="your station name; on ham bands this must be your licensed callsign")
    p.add_argument("--radio", action="store_true", help="use a real radio via the sound card")
    p.add_argument("--input-device", help="sound card input (radio speaker), see --list-devices")
    p.add_argument("--output-device", help="sound card output (radio mic)")
    p.add_argument("--ptt", metavar="PORT", help="serial port that keys the radio (e.g. /dev/ttyUSB0)")
    p.add_argument("--ptt-line", choices=["rts", "dtr"], default="rts")
    p.add_argument("--busy-level", type=float, default=0.02,
                   help="input level that counts as 'someone is talking' (radio mode)")
    p.add_argument("--channel", type=int, default=0, help="simulated band channel number")
    p.add_argument("--pos", default="0,0", help="simulated position x,y")
    p.add_argument("--range", type=float, default=10.0, help="simulated radio range")
    p.add_argument("--noise", type=float, default=0.05, help="simulated static level")
    p.add_argument("--loss", type=float, default=0.0, help="simulated chance a packet is lost")
    p.add_argument("--realtime", action="store_true",
                   help="simulate real airtime (1200 baud) instead of instant delivery")
    p.add_argument("--no-relay", action="store_true",
                   help="don't relay for others (please don't: rule 2)")
    p.add_argument("--beacon", type=float, default=600.0, help="seconds between station IDs")
    p.add_argument("--ttl", type=int, default=3, help="max relay hops for your packets")
    p.add_argument("-v", "--verbose", action="store_true")


def _transport(a: argparse.Namespace) -> Transport:
    if a.radio:
        def dev(v):
            return int(v) if v is not None and v.isdigit() else v
        return AudioTransport(dev(a.input_device), dev(a.output_device), ptt_port=a.ptt,
                              ptt_line=a.ptt_line, busy_threshold=a.busy_level)
    x, y = (float(v) for v in a.pos.split(","))
    return SimTransport(a.channel, x, y, a.range, a.noise, a.loss, a.realtime)


def _sites(specs: list[str]) -> dict[str, str]:
    sites = {}
    for spec in specs or []:
        if "=" not in spec:
            raise SystemExit(f"--site wants DOMAIN=FOLDER, got {spec!r}")
        domain, folder = spec.split("=", 1)
        if not Path(folder).is_dir():
            raise SystemExit(f"site folder not found: {folder}")
        sites[domain if domain.endswith(".gorb") else domain + ".gorb"] = folder
    return sites


def _node(a, sites=None, relay=None) -> Node:
    return Node(_transport(a), a.callsign, sites,
                relay=(not a.no_relay) if relay is None else relay,
                beacon_interval=a.beacon, ttl=a.ttl).start()


def _wait_forever(node: Node) -> None:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n73! signing off.")
        node.stop()


def cmd_host(a):
    sites = _sites(a.site)
    if not sites:
        raise SystemExit("give at least one --site DOMAIN=FOLDER")
    for domain, folder in sites.items():
        for f in Path(folder).rglob("*"):
            if f.suffix.lower() in (".html", ".htm", ".txt"):
                reason = rules.check_content(f.read_text(errors="replace"))
                if reason:
                    print(f"warning: {f} will not be served ({reason})")
    node = _node(a, sites)
    print(f"{node.callsign} on the air hosting: " + ", ".join(f"gorb://{d}" for d in sites))
    _wait_forever(node)


def cmd_relay(a):
    node = _node(a, relay=True)
    print(f"{node.callsign} relaying signals for everyone (rule 2). Ctrl+C to stop.")
    _wait_forever(node)


def cmd_browse(a):
    from .browser import serve_browser
    node = _node(a, _sites(a.site))
    serve_browser(node, a.host, a.port)
    print(f"G.O.R.B browser open at http://{a.host}:{a.port}  (station {node.callsign})")
    _wait_forever(node)


def _html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|h\d|li|div|tr)>", "\n", text)
    text = re.sub(r"(?i)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 [\1]", text)
    text = re.sub(r"<[^>]+>", "", text)
    import html as _h
    return re.sub(r"\n{3,}", "\n\n", _h.unescape(text)).strip()


def cmd_get(a):
    node = _node(a)
    try:
        page = node.fetch(a.url, timeout=a.timeout)
    except GorbError as e:
        raise SystemExit(f"error: {e}")
    finally:
        node.stop()
    print(page.text if a.raw else _html_to_text(page.text))
    print(f"\n-- {page.url} status {page.status}, {len(page.body)} bytes, "
          f"{page.hops} hop(s), {page.seconds:.1f}s", file=sys.stderr)


def cmd_demo(a):
    """Three simulated stations in a row; the browser can only reach the
    host through the relay in the middle."""
    from .browser import serve_browser
    site = Path(__file__).resolve().parent.parent / "examples" / "site"
    if not site.is_dir():
        raise SystemExit(f"example site missing: {site}")
    ch = 7
    host = Node(SimTransport(ch, 0, 0, 10, realtime=a.realtime), "HOST-1",
                {"welcome.gorb": site}, beacon_interval=30).start()
    relay = Node(SimTransport(ch, 8, 0, 10, realtime=a.realtime), "RELAY-1",
                 beacon_interval=30).start()
    me = Node(SimTransport(ch, 16, 0, 10, realtime=a.realtime), "YOU-1",
              beacon_interval=30).start()
    serve_browser(me, "127.0.0.1", a.port)
    print("Simulated band (channel 7):")
    print("  HOST-1  at x=0   hosts gorb://welcome.gorb")
    print("  RELAY-1 at x=8   relays (rule 2)")
    print("  YOU-1   at x=16  too far to hear HOST-1 directly")
    print(f"Open http://127.0.0.1:{a.port}/go?url=gorb%3A%2F%2Fwelcome.gorb   Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for n in (host, relay, me):
            n.stop()


def cmd_encode(a):
    cfg = ModemConfig(sample_rate=a.rate)
    data = Path(a.input).read_bytes()
    from .packet import Packet, PacketType
    import zlib
    blob = zlib.compress(b'{"status":200,"type":"text/html"}\n' + data, 9)
    mod = Modulator(cfg)
    frames = []
    for i in range(0, len(blob), 180):
        pkt = Packet(PacketType.DATA, a.domain, "*", blob[i : i + 180], 0, i // 180,
                     (len(blob) + 179) // 180)
        frames += [mod.modulate(pkt.encode()), np.zeros(cfg.sample_rate // 10, np.float32)]
    audio = np.concatenate(frames)
    with wave.open(a.output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(cfg.sample_rate)
        w.writeframes((audio * 32767).astype("<i2").tobytes())
    print(f"wrote {a.output}: {len(audio) / cfg.sample_rate:.1f}s of audio, {len(frames) // 2} packets")


def cmd_decode(a):
    from .packet import Packet
    import zlib
    with wave.open(a.input, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit("only 16-bit WAV files are supported")
    audio = np.frombuffer(raw, "<i2").astype(np.float32) / 32767
    if ch > 1:
        audio = audio.reshape(-1, ch)[:, 0]
    chunks, total = {}, 0
    for payload, _ in Demodulator(ModemConfig(sample_rate=rate)).decode(audio):
        try:
            pkt = Packet.decode(payload)
        except ValueError:
            continue
        print(f"packet {pkt.type.name} from {pkt.src} seq {pkt.seq}/{pkt.total}", file=sys.stderr)
        chunks[pkt.seq], total = pkt.payload, pkt.total
    if not total or len(chunks) < total:
        raise SystemExit(f"incomplete: got {len(chunks)}/{total} packets")
    body = zlib.decompress(b"".join(chunks[i] for i in range(total))).partition(b"\n")[2]
    if a.output:
        Path(a.output).write_bytes(body)
        print(f"wrote {a.output}", file=sys.stderr)
    else:
        sys.stdout.write(body.decode(errors="replace"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="gorb", description="G.O.R.B - Global Open Radio Browser")
    p.add_argument("--version", action="version", version=f"gorb {__version__}")
    p.add_argument("--list-devices", action="store_true", help="list sound cards and exit")
    p.add_argument("--rules", action="store_true", help="print the G.O.R.B rules and exit")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("browse", help="open the browser")
    _add_radio_args(s)
    s.add_argument("--site", action="append", help="also host DOMAIN=FOLDER")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8088)
    s.set_defaults(fn=cmd_browse)

    s = sub.add_parser("host", help="host a site on the air")
    _add_radio_args(s)
    s.add_argument("--site", action="append", help="DOMAIN=FOLDER, e.g. cats.gorb=./mysite")
    s.set_defaults(fn=cmd_host)

    s = sub.add_parser("relay", help="relay signals for others")
    _add_radio_args(s)
    s.set_defaults(fn=cmd_relay)

    s = sub.add_parser("get", help="fetch one page and print it")
    _add_radio_args(s)
    s.add_argument("url")
    s.add_argument("--raw", action="store_true", help="print HTML instead of text")
    s.add_argument("--timeout", type=float, default=120)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("demo", help="simulated host + relay + browser")
    s.add_argument("--port", type=int, default=8088)
    s.add_argument("--realtime", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("encode", help="turn an HTML file into a WAV you can transmit")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--domain", default="file.gorb")
    s.add_argument("--rate", type=int, default=48000)
    s.set_defaults(fn=cmd_encode)

    s = sub.add_parser("decode", help="turn a recorded WAV back into HTML")
    s.add_argument("input")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_decode)

    a = p.parse_args(argv)
    if a.rules:
        print(rules.RULES_TEXT)
        return
    if a.list_devices:
        try:
            import sounddevice as sd  # type: ignore
        except ImportError:
            raise SystemExit("needs: pip install sounddevice")
        print(sd.query_devices())
        return
    if not getattr(a, "fn", None):
        p.print_help()
        return
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    a.fn(a)


if __name__ == "__main__":
    main()

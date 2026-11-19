"""The G.O.R.B browser window.

Runs a tiny web server on your own computer (http://127.0.0.1:8088) that
shows an address bar. Typing gorb://cats.gorb sends the request over the
radio; the page that comes back is shown in a locked-down frame: no
scripts, no internet requests, links rewritten to stay on G.O.R.B.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlparse

from . import branding, rules
from .node import GorbError, Node, Page, parse_url

def page_csp(server_origin: str) -> str:
    """Pages may only load pictures from this browser (which fetches them by radio)."""
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; img-src data: {server_origin}; "
        f"media-src {server_origin}; "
        "font-src data:; form-action 'none'; base-uri 'none'"
    )


PAGE_CSP = page_csp("'self'")

_TAG_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", re.DOTALL)
_ATTR_RE = re.compile(r"""(\s(?:href|src|action|poster|srcset)\s*=\s*)(["'])(.*?)\2""",
                      re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"""\sstyle\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_VIDEO_RE = re.compile(r"<video\b([^>]*)>(.*?)</video\s*>", re.IGNORECASE | re.DOTALL)
_SRC_RE = re.compile(r"""\ssrc\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_KEEP_RE = re.compile(r"""\s(?:width|height|class|style|title|alt)\s*=\s*(["']).*?\1""",
                      re.IGNORECASE | re.DOTALL)


def rewrite_links(page_html: str, base_url: str) -> str:
    """Make a received page work inside the browser.

    * gorb links go back through the browser (/go), web links stay as they are
    * pictures (Gorb Pixels 2) are stacked in three layers: the HD picture on
      top, the standard one behind it and the tiny blurred preview at the
      back, so whatever has arrived so far is what you see
    * videos (Gorb Motion 2) become real players with sound: AV1 for browsers
      that have it, H.264 for the rest, with the first frame as a poster
    """
    domain, path = parse_url(base_url)
    base = f"http://{domain}{path}"

    def resolve(target: str) -> str | None:
        """Absolute gorb:// URL for a link, or None if it isn't a gorb link."""
        low = target.lower()
        if low.startswith(("data:", "#", "mailto:", "http://", "https://", "javascript:")):
            return None
        if low.startswith("gorb://"):
            return target
        u = urlparse(urljoin(base, target))
        return f"gorb://{u.netloc}{u.path or '/'}" + (f"?{u.query}" if u.query else "")

    def media_url(absolute: str, variant: str) -> str:
        return "/media?url=" + quote(absolute, safe="") + "&v=" + variant

    def fix_video(m: re.Match) -> str:
        attrs, inner = m.group(1), m.group(2)
        src = _SRC_RE.search(attrs) or _SRC_RE.search(inner)
        if not src:
            return ""
        absolute = resolve(html.unescape(src.group(2)).strip())
        if absolute is None:
            return ""
        keep = "".join(k.group(0) for k in _KEEP_RE.finditer(attrs))
        e = lambda u: html.escape(u, quote=True)  # noqa: E731
        return (f'<video controls preload="auto" playsinline{keep} '
                f'poster="{e(media_url(absolute, "preview"))}">'
                f'<source src="{e(media_url(absolute, "av1"))}" '
                f'type=\'video/webm; codecs="av01.0.04M.08, opus"\'>'
                f'<source src="{e(media_url(absolute, "h264"))}" type="video/mp4"></video>')

    def fix_tag(m: re.Match) -> str:
        tag, attrs = m.group(1).lower(), m.group(2)
        preview = None

        def fix_attr(a: re.Match) -> str:
            nonlocal preview
            prefix, q, raw = a.group(1), a.group(2), html.unescape(a.group(3)).strip()
            name = prefix.strip().split("=")[0].strip().lower()
            if name == "srcset":
                return ""  # one picture is plenty over radio
            if raw.startswith("/media?url="):
                return a.group(0)  # already pointed at the radio (a rewritten video)
            if raw.lower().startswith("javascript:"):
                return f"{prefix}{q}#{q}"
            absolute = resolve(raw)
            if absolute is None:
                return a.group(0)
            if name in ("src", "poster") and tag in ("img", "image", "input"):
                preview = (media_url(absolute, "standard"), media_url(absolute, "preview"))
                new = media_url(absolute, "hd")
            elif name == "href":
                new = "/go?url=" + quote(absolute, safe="")
            else:
                new = "#"  # iframes, audio, ... aren't supported over radio
            return f"{prefix}{q}{html.escape(new, quote=True)}{q}"

        attrs = _ATTR_RE.sub(fix_attr, attrs)
        if preview:
            bg = ("background:" + ", ".join(f"url({u}) center/cover no-repeat" for u in preview) + ";")
            st = _STYLE_RE.search(attrs)
            if st:
                attrs = attrs[:st.start()] + f" style={st.group(1)}{html.escape(bg)}" \
                    f"{st.group(2)}{st.group(1)}" + attrs[st.end():]
            else:
                attrs += f' style="{html.escape(bg)}"'
        return f"<{m.group(1)}{attrs}>"

    out = _SCRIPT_RE.sub("", page_html)  # scripts can't run anyway; save bytes
    out = _VIDEO_RE.sub(fix_video, out)
    out = _TAG_RE.sub(fix_tag, out)
    return '<base target="_top">' + out


CHROME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>G.O.R.B{title}</title>
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<style>
:root {{ --bg:#0f1714; --panel:#16221d; --ink:#d8f5e6; --dim:#7fa596; --accent:#4ff0a0; --warn:#ffcc66; }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; height:100%; background:var(--bg); color:var(--ink);
  font:15px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
header {{ display:flex; gap:10px; align-items:center; padding:10px 14px; background:var(--panel);
  border-bottom:1px solid #24362e; flex-wrap:wrap; }}
.logo {{ color:var(--accent); font-weight:bold; letter-spacing:2px; text-decoration:none;
  display:flex; align-items:center; gap:8px; }}
.biglogo {{ display:block; width:100%; max-width:440px; height:auto; margin:8px 0 18px; }}
form {{ flex:1; display:flex; gap:8px; min-width:240px; }}
input {{ flex:1; padding:8px 10px; border-radius:6px; border:1px solid #2f4a3e; background:#0b110f;
  color:var(--ink); font:inherit; }}
button {{ padding:8px 14px; border-radius:6px; border:0; background:var(--accent); color:#04130b;
  font:inherit; font-weight:bold; cursor:pointer; }}
.meta {{ color:var(--dim); font-size:13px; }}
main {{ height:calc(100% - 58px); position:relative; }}
iframe {{ width:100%; height:100%; border:0; background:#fff; position:relative; z-index:1; }}
.tuning {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
  justify-content:center; gap:14px; color:var(--dim); }}
.wave {{ display:flex; gap:4px; height:40px; align-items:center; }}
.wave i {{ width:5px; background:var(--accent); border-radius:3px; animation:w 1s ease-in-out infinite; }}
.wave i:nth-child(2){{animation-delay:.1s}} .wave i:nth-child(3){{animation-delay:.2s}}
.wave i:nth-child(4){{animation-delay:.3s}} .wave i:nth-child(5){{animation-delay:.4s}}
@keyframes w {{ 0%,100%{{height:8px}} 50%{{height:38px}} }}
.home {{ max-width:760px; margin:0 auto; padding:28px 16px; }}
.home a {{ color:var(--accent); }}
.home li {{ margin:6px 0; }}
pre {{ white-space:pre-wrap; color:var(--dim); }}
</style></head><body>
<header>
  <a class="logo" href="/"><img src="/icon.svg" alt="" width="26" height="26">G.O.R.B</a>
  <form action="/go"><input name="url" value="{url}" placeholder="gorb://example.gorb" autofocus>
  <button>Tune in</button></form>
  <span class="meta">{callsign} &middot; {status}</span>
</header>
<main>{body}</main>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    node: Node
    cache: dict[str, Page]

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body: str | bytes, ctype="text/html; charset=utf-8", csp=None,
              cache: str = "no-store"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(data)

    def _chrome(self, url: str, body: str, title: str = "") -> str:
        s = self.node.stats
        status = f"rx {s['rx']} / tx {s['tx']} / relayed {s['relayed']}"
        return CHROME.format(url=html.escape(url), body=body, callsign=html.escape(self.node.callsign),
                             status=status, title=html.escape(title))

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            return self._send(200, self._chrome("", self._home()))
        if u.path == "/go":
            url = q.get("url", "").strip()
            if not url:
                return self._redirect("/")
            if not url.startswith("gorb://"):
                url = "gorb://" + url
            try:
                parse_url(url)
            except GorbError as e:
                return self._send(400, self._chrome(url, f'<div class="home"><h2>{html.escape(str(e))}</h2></div>'))
            frame = (
                '<div class="tuning"><div class="wave"><i></i><i></i><i></i><i></i><i></i></div>'
                f"<div>tuning in to {html.escape(url)} &hellip; radio is slow, hang tight</div></div>"
                f'<iframe sandbox="allow-top-navigation-by-user-activation allow-popups" '
                f'src="/frame?url={quote(url, safe="")}"></iframe>'
            )
            return self._send(200, self._chrome(url, frame, " - " + url))
        if u.path == "/frame":
            return self._frame(q.get("url", ""), refresh="refresh" in q)
        if u.path == "/media":
            return self._media(q.get("url", ""), q.get("v", "standard"))
        if u.path in ("/icon.svg", "/logo.svg"):
            art = branding.ICON_SVG if u.path == "/icon.svg" else branding.LOGO_SVG
            return self._send(200, art, "image/svg+xml", cache="max-age=86400")
        if u.path == "/api/status":
            info = {
                "callsign": self.node.callsign,
                "stats": self.node.stats,
                "domains": {d: {"station": st.callsign, "hops": st.hops}
                            for d, st in self.node.known_domains().items()},
            }
            return self._send(200, json.dumps(info), "application/json")
        self._send(404, "not found", "text/plain")

    def _redirect(self, where: str):
        self.send_response(302)
        self.send_header("Location", where)
        self.end_headers()

    def _csp(self) -> str:
        host = self.headers.get("Host", "")
        origin = f"http://{host}" if re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?", host) else "'self'"
        return page_csp(origin)

    def _get(self, url: str, refresh: bool = False) -> Page:
        """Fetch over the radio, sharing one fetch between everyone asking for
        the same URL at the same time (a page with the same picture twice)."""
        with self.lock:
            url_lock = self.url_locks.setdefault(url, threading.Lock())
        with url_lock:
            page = None if refresh else self.cache.get(url)
            if page is None or time.time() - page_time(page) > 300:
                page = self.node.fetch(url)
                page.fetched_at = time.time()  # type: ignore[attr-defined]
                self.cache[url] = page
            return page

    def _media(self, url: str, variant: str):
        if not url.startswith("gorb://"):
            return self._send(400, "bad url", "text/plain")
        if variant and variant != "standard":
            url += ("&" if "?" in url else "?") + "gorb=" + quote(variant)
        try:
            page = self._get(url)
        except GorbError as e:
            return self._send(504, str(e), "text/plain")
        if page.status >= 400 or not page.content_type.startswith(("image/", "video/")):
            return self._send(404, "no picture", "text/plain")
        body = page.body
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if rng and body:  # video players ask for byte ranges (Safari insists)
            start = int(rng.group(1)) if rng.group(1) else max(0, len(body) - int(rng.group(2) or 0))
            end = int(rng.group(2)) if rng.group(1) and rng.group(2) else len(body) - 1
            end = min(end, len(body) - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.end_headers()
                return
            part = body[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", page.content_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(len(part)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "max-age=600")
            self.end_headers()
            self.wfile.write(part)
            return
        self.send_response(200)
        self.send_header("Content-Type", page.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "max-age=600")
        self.end_headers()
        self.wfile.write(body)

    def _frame(self, url: str, refresh: bool):
        csp = self._csp()
        try:
            page = self._get(url, refresh)
        except GorbError as e:
            body = (f'<body style="font-family:monospace;padding:24px"><h2>No signal</h2>'
                    f"<p>{html.escape(str(e))}</p><p>Check that a station hosting this "
                    "domain is on the air and in range (or that a relay can reach it).</p></body>")
            return self._send(504, body, csp=csp)
        foot = (f'<div style="font:12px monospace;color:#777;border-top:1px solid #ddd;margin-top:24px;'
                f'padding:6px">received over radio &middot; {len(page.body)} bytes &middot; '
                f"{page.hops} relay hop(s) &middot; {page.seconds:.1f}s</div>")
        if page.content_type == "text/html":
            body = rewrite_links(page.text, url) + foot
        elif page.content_type.startswith("image/"):
            src = html.escape("/media?url=" + quote(url, safe=""))
            body = (f'<div style="text-align:center;padding:16px"><img src="{src}" '
                    f'style="max-width:100%;image-rendering:auto"></div>{foot}')
        else:
            body = f"<pre>{html.escape(page.text)}</pre>{foot}"
        self._send(200 if page.status < 400 else page.status, body, csp=csp)

    def _home(self) -> str:
        domains = self.node.known_domains()
        if domains:
            items = "".join(
                f'<li><a href="/go?url={quote("gorb://" + d, safe="")}">gorb://{html.escape(d)}</a>'
                f' <span class="meta">via {html.escape(st.callsign)}, {st.hops} hop(s)</span></li>'
                for d, st in sorted(domains.items())
            )
        else:
            items = "<li class='meta'>Nothing heard yet. Stations announce themselves every few minutes.</li>"
        return (
            '<div class="home"><img class="biglogo" src="/logo.svg" '
            f'alt="G.O.R.B - {html.escape(branding.MOTTO)}">'
            "<p>Web pages carried by radio waves instead of Wi-Fi. Type a gorb:// address above.</p>"
            f"<h3>Stations heard on the air</h3><ul>{items}</ul>"
            f"<h3>Rules</h3><pre>{html.escape(rules.RULES_TEXT)}</pre></div>"
        )


def page_time(page: Page) -> float:
    return getattr(page, "fetched_at", 0.0)


def serve_browser(node: Node, host: str = "127.0.0.1", port: int = 8088) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"node": node, "cache": {}, "url_locks": {},
                                            "lock": threading.Lock()})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="gorb-browser").start()
    return server

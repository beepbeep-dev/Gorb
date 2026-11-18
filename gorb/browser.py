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

from . import rules
from .node import GorbError, Node, Page, parse_url

PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src data:; form-action 'none'; base-uri 'none'"
)

_ATTR_RE = re.compile(r"""(\s(?:href|src|action)\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)


def rewrite_links(page_html: str, base_url: str) -> str:
    """Point gorb links back at the browser, open web links in a new tab."""
    domain, path = parse_url(base_url)
    base = f"http://{domain}{path}"

    def fix(m: re.Match) -> str:
        prefix, quote_char, target = m.group(1), m.group(2), html.unescape(m.group(3)).strip()
        low = target.lower()
        if low.startswith(("data:", "#", "mailto:")):
            new = target
        elif low.startswith(("http://", "https://")):
            new = target  # the regular web; opens outside G.O.R.B
        elif low.startswith("javascript:"):
            new = "#"
        else:
            if low.startswith("gorb://"):
                absolute = target
            else:
                u = urlparse(urljoin(base, target))
                absolute = f"gorb://{u.netloc}{u.path or '/'}"
            new = "/go?url=" + quote(absolute, safe="")
        return f"{prefix}{quote_char}{html.escape(new, quote=True)}{quote_char}"

    out = _SCRIPT_RE.sub("", page_html)  # scripts can't run anyway; save bytes
    out = _ATTR_RE.sub(fix, out)
    return '<base target="_top">' + out


CHROME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>G.O.R.B{title}</title>
<style>
:root {{ --bg:#0f1714; --panel:#16221d; --ink:#d8f5e6; --dim:#7fa596; --accent:#4ff0a0; --warn:#ffcc66; }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; height:100%; background:var(--bg); color:var(--ink);
  font:15px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
header {{ display:flex; gap:10px; align-items:center; padding:10px 14px; background:var(--panel);
  border-bottom:1px solid #24362e; flex-wrap:wrap; }}
.logo {{ color:var(--accent); font-weight:bold; letter-spacing:2px; text-decoration:none; }}
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
  <a class="logo" href="/">G.O.R.B</a>
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

    def _send(self, code: int, body: str | bytes, ctype="text/html; charset=utf-8", csp=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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

    def _frame(self, url: str, refresh: bool):
        page = None if refresh else self.cache.get(url)
        if page is None or time.time() - page_time(page) > 300:
            try:
                page = self.node.fetch(url)
                page.fetched_at = time.time()  # type: ignore[attr-defined]
                self.cache[url] = page
            except GorbError as e:
                body = (f'<body style="font-family:monospace;padding:24px"><h2>No signal</h2>'
                        f"<p>{html.escape(str(e))}</p><p>Check that a station hosting this "
                        "domain is on the air and in range (or that a relay can reach it).</p></body>")
                return self._send(504, body, csp=PAGE_CSP)
        foot = (f'<div style="font:12px monospace;color:#777;border-top:1px solid #ddd;margin-top:24px;'
                f'padding:6px">received over radio &middot; {len(page.body)} bytes &middot; '
                f"{page.hops} relay hop(s) &middot; {page.seconds:.1f}s</div>")
        if page.content_type == "text/html":
            body = rewrite_links(page.text, url) + foot
        else:
            body = f"<pre>{html.escape(page.text)}</pre>{foot}"
        self._send(200 if page.status < 400 else page.status, body, csp=PAGE_CSP)

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
            '<div class="home"><h1>Global Open Radio Browser</h1>'
            "<p>Web pages carried by radio waves instead of Wi-Fi. Type a gorb:// address above.</p>"
            f"<h3>Stations heard on the air</h3><ul>{items}</ul>"
            f"<h3>Rules</h3><pre>{html.escape(rules.RULES_TEXT)}</pre></div>"
        )


def page_time(page: Page) -> float:
    return getattr(page, "fetched_at", 0.0)


def serve_browser(node: Node, host: str = "127.0.0.1", port: int = 8088) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"node": node, "cache": {}})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="gorb-browser").start()
    return server

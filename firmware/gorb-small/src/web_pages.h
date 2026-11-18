// HTML for the board's built-in browser (served over its own Wi-Fi hotspot).
#pragma once

static const char PAGE_HEAD[] = R"HTML(<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>G.O.R.B</title><style>
:root{--bg:#0f1714;--panel:#16221d;--ink:#d8f5e6;--dim:#7fa596;--accent:#4ff0a0;--bad:#ff8a7a}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
font:15px/1.45 ui-monospace,Menlo,Consolas,monospace}
header{display:flex;gap:8px;align-items:center;padding:10px 12px;background:var(--panel);
border-bottom:1px solid #24362e;flex-wrap:wrap}
.logo{color:var(--accent);font-weight:bold;letter-spacing:2px;text-decoration:none}
form.bar{flex:1;display:flex;gap:6px;min-width:220px}
input,select,textarea{padding:8px 10px;border-radius:6px;border:1px solid #2f4a3e;background:#0b110f;
color:var(--ink);font:inherit;max-width:100%}
form.bar input{flex:1;min-width:0}
button{padding:8px 14px;border-radius:6px;border:0;background:var(--accent);color:#04130b;font:inherit;
font-weight:bold;cursor:pointer}
button.ghost{background:transparent;color:var(--bad);border:1px solid var(--bad)}
.meta{color:var(--dim);font-size:13px}
.wrap{max-width:760px;margin:0 auto;padding:20px 16px}
a{color:var(--accent)}li{margin:6px 0}
label{display:block;margin:12px 0 4px;color:var(--dim)}
textarea{width:100%;min-height:280px}
.note{background:#24301f;border-left:3px solid #ffcc66;padding:8px 12px;margin:12px 0}
.err{color:var(--bad)}
main.frame{height:calc(100% - 58px)}iframe{width:100%;height:100%;border:0;background:#fff}
</style></head><body>)HTML";

static const char PAGE_TAIL[] = "</body></html>";

// Shown inside the page frame while a page is still arriving over the air.
// (main.cpp puts the doctype and the auto-refresh tag in front of this)
static const char TUNING_PAGE[] = R"HTML(<style>
body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
gap:14px;background:#0f1714;color:#7fa596;font:15px ui-monospace,Menlo,monospace;text-align:center;padding:16px}
.w{display:flex;gap:4px;height:40px;align-items:center}
.w i{width:5px;background:#4ff0a0;border-radius:3px;animation:a 1s ease-in-out infinite}
.w i:nth-child(2){animation-delay:.1s}.w i:nth-child(3){animation-delay:.2s}
.w i:nth-child(4){animation-delay:.3s}.w i:nth-child(5){animation-delay:.4s}
@keyframes a{0%,100%{height:8px}50%{height:38px}}</style></head><body>
<div class="w"><i></i><i></i><i></i><i></i><i></i></div>)HTML";

static const char PAGE_CSP[] =
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "form-action 'none'; base-uri 'none'";

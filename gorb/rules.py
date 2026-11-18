"""The G.O.R.B rules, as code where code can help.

Rule 1: no NSFW, violent or adults-only content. Hosts refuse to serve
        pages that trip the filter and browsers refuse to show them.
Rule 2: everyone helps carry signals -> see Node(relay=True).
Rule 3: treat others the way you want to be treated -> listen before
        talking, back off when the channel is busy, small chunks.
Rule 4: vibecoded sites are welcome -> no checks on how a site was made.
Rule 5: no signal jammers -> G.O.R.B never transmits without data, never
        transmits over someone else, and caps how long one node may talk.

The keyword filter is deliberately simple. It is a first line of defence,
not a moderation system: it catches pages that label themselves.
"""

from __future__ import annotations

import re

BLOCKED_TERMS = [
    "nsfw",
    "porn",
    "pornography",
    "xxx",
    "adults only",
    "adult only",
    "18+",
    "explicit content",
    "nude",
    "nudes",
    "gore",
    "gory",
    "snuff",
    "beheading",
    "torture video",
]

_PATTERN = re.compile(
    r"(?<![\w+])(" + "|".join(re.escape(t) for t in BLOCKED_TERMS) + r")(?![\w+])",
    re.IGNORECASE,
)

RULES_TEXT = """\
1. No not-safe-for-work, violent or adults-only content.
2. If you use G.O.R.B you help: your radio relays signals for others.
3. The golden rule: treat others the way you want to be treated.
4. Vibecoded sites are accepted.
5. No signal jammers.
Breaking the rules gets you kicked off G.O.R.B."""


def check_content(text: str) -> str | None:
    """Return the reason a page breaks rule 1, or None if it looks fine."""
    m = _PATTERN.search(text)
    if m:
        return f"rule 1: page contains blocked term {m.group(1)!r}"
    return None


# Rule 5 / rule 3: limits that keep one station from hogging the channel.
MAX_RESPONSE_BYTES = 64 * 1024  # largest page (after compression) a host will send
MAX_TX_DUTY = 0.5  # a node may transmit at most this fraction of the time

"""Diagnostic signals: reading what a response is actually telling you.

The escalation ladder is only useful if you can tell which rung you are on. These
are the cheap, mechanical tests that turn a response into a *move*.

The most expensive failure in acquisition is silent: saving a challenge page as
if it were data. Detecting that and stopping is worth more than any bypass.

>>> classify(403, {"set-cookie": "datadome=abc"}, "")
'challenge'
>>> classify(200, {"content-type": "text/html"}, "<html><body>hi</body></html>")
'ok'
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

# Statuses at which a vendor marker means "you were stopped" rather than
# "this origin is guarded".
#
# 503 and Cloudflare's 52x range belong here even though they read as server
# errors: interstitial challenge pages are historically served at 503, and
# several vendors use it for load-shedding. Leaving it out meant a challenge
# was classified as a transient error, retried, and then returned as data.
BLOCKED_STATUSES = frozenset(
    {401, 403, 429, 503, 520, 521, 522, 523, 524, 525, 526, 527}
)

__all__ = [
    "ChallengeEncountered",
    "detect_challenge",
    "classify",
    "looks_truncated",
    "CHALLENGE_MARKERS",
    "INTERACTIVE_MARKERS",
]

# (vendor, where-to-look, pattern). Header/cookie markers are more reliable than
# body markers, which is why they are checked first.
CHALLENGE_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("datadome", "header", r"x-datadome"),
    ("datadome", "cookie", r"\bdatadome="),
    ("datadome", "body", r"captcha-delivery\.com"),
    ("cloudflare", "header", r"cf-mitigated"),
    ("cloudflare", "body", r"challenge-platform|cf_chl_opt|__cf_chl"),
    ("akamai", "cookie", r"\b_abck="),
    ("imperva", "cookie", r"\bincap_ses|visid_incap"),
    ("perimeterx", "cookie", r"\b_px(?:\d+)?="),
    ("kasada", "header", r"x-kpsdk"),
)

# Markers meaning a *human* is required. These are terminal: do not escalate,
# do not retry, and never attempt to solve them.
INTERACTIVE_MARKERS: tuple[tuple[str, str], ...] = (
    ("turnstile", r"challenges\.cloudflare\.com|cf-turnstile"),
    ("recaptcha", r"google\.com/recaptcha|g-recaptcha"),
    ("hcaptcha", r"hcaptcha\.com|h-captcha"),
)


class ChallengeEncountered(RuntimeError):
    """A bot-protection challenge was served instead of content.

    Raised rather than returned so that a challenge can never be mistaken for
    data by a caller that forgot to check. Carries what is needed to decide what
    to do next — and "solve it" is not among the options.
    """

    def __init__(
        self,
        url: str,
        *,
        vendor: str | None = None,
        interactive: bool = False,
        status: int | None = None,
    ):
        self.url = url
        self.vendor = vendor
        self.interactive = interactive
        self.status = status
        kind = "interactive challenge" if interactive else "challenge"
        super().__init__(
            f"{vendor or 'unknown'} {kind} at {url}"
            + (f" (HTTP {status})" if status else "")
            + (
                ". A human must complete this; do not retry."
                if interactive
                else ". Escalate transport or stop; do not retry this request."
            )
        )


@dataclass(frozen=True)
class ChallengeVerdict:
    """What, if anything, is standing between us and the content.

    Crucially separates two facts that are easy to conflate:

    - `vendor` — *who* guards this origin. Protected sites emit vendor headers and
      cookies on perfectly successful responses too, so this is metadata.
    - `serving` — whether a challenge is being served **right now, instead of the
      content**. This is the one that should stop a job.

    Treating vendor-presence as blocking makes a client useless on exactly the
    sites it exists for, which is a mistake worth encoding against.
    """

    vendor: str | None = None
    interactive: bool = False
    serving: bool = False

    def __bool__(self) -> bool:
        return self.serving


def _joined_cookies(headers: Mapping[str, str]) -> str:
    return " ".join(v for k, v in headers.items() if k.lower() == "set-cookie")


def detect_challenge(
    status: int, headers: Mapping[str, str], body: str = ""
) -> ChallengeVerdict:
    """Identify a bot-protection challenge from cheap signals.

    Checks headers and cookies before the body, since those are harder to fake
    and cheaper to inspect. The body is scanned in full: an earlier version
    truncated it, which hid widgets and challenge markers on large pages — and a
    missed challenge is the expensive direction of this error.

    >>> bool(detect_challenge(200, {}, "<html>fine</html>"))
    False
    >>> detect_challenge(403, {"cf-mitigated": "challenge"}, "").serving
    True

    A protected site emits vendor headers on successful responses too. That
    identifies the guard; it does not mean you were stopped:

    >>> verdict = detect_challenge(200, {"x-datadome": "protected"}, "<html>data</html>")
    >>> verdict.vendor, verdict.serving
    ('datadome', False)

    An interactive widget is terminal, not an escalation cue:

    >>> detect_challenge(200, {}, '<div class="cf-turnstile" data-sitekey="x">').interactive
    True
    """
    header_blob = " ".join(f"{k}: {v}" for k, v in headers.items()).lower()
    cookie_blob = _joined_cookies(headers).lower()
    lowered_body = body.lower()

    for name, pattern in INTERACTIVE_MARKERS:
        if re.search(pattern, lowered_body):
            return ChallengeVerdict(vendor=name, interactive=True, serving=True)

    haystacks = {"header": header_blob, "cookie": cookie_blob, "body": lowered_body}
    vendor_seen: str | None = None
    for vendor, where, pattern in CHALLENGE_MARKERS:
        if not re.search(pattern, haystacks[where]):
            continue
        vendor_seen = vendor_seen or vendor
        # A challenge *page* in the body is proof on its own; header and cookie
        # markers only mean the vendor is in front of this origin.
        if where == "body":
            return ChallengeVerdict(vendor=vendor, serving=True)

    return ChallengeVerdict(
        vendor=vendor_seen,
        serving=vendor_seen is not None and status in BLOCKED_STATUSES,
    )


def looks_truncated(body: bytes, headers: Mapping[str, str]) -> bool:
    """Whether the payload is shorter than the response claimed.

    A `200` carrying a truncated body is a silent corruption that only shows up
    downstream, so it is worth one cheap check here.

    >>> looks_truncated(b"abc", {"Content-Length": "3"})
    False
    >>> looks_truncated(b"abc", {"Content-Length": "99"})
    True
    """
    for key, value in headers.items():
        if key.lower() == "content-length":
            try:
                return len(body) < int(value)
            except ValueError:
                return False
    return False


def classify(status: int, headers: Mapping[str, str], body: str = "") -> str:
    """One-word verdict driving the escalation decision.

    Returns one of: `ok`, `challenge`, `rate_limited`, `forbidden`, `not_found`,
    `client_error`, `server_error`, `auth_required`, `empty`.

    >>> classify(429, {}, "")
    'rate_limited'
    >>> classify(200, {}, "")
    'empty'

    A bare 403 is the most common block there is, and must never read as success:

    >>> classify(403, {}, "nope")
    'forbidden'
    >>> classify(451, {}, "nope")
    'client_error'
    """
    if detect_challenge(status, headers, body):
        return "challenge"
    if status == 429:
        return "rate_limited"
    if status in (401, 407):
        return "auth_required"
    if status == 404:
        return "not_found"
    if status == 403:
        return "forbidden"
    if status >= 500:
        return "server_error"
    if 400 <= status < 500:
        return "client_error"
    if 200 <= status < 300 and not body.strip():
        return "empty"
    return "ok"

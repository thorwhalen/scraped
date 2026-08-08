"""Rung 0: reconnaissance. The highest-return two minutes of any acquisition job.

Before writing a single selector, ask the site what it will simply hand over.
Most of the time something on this list makes the rest of the job trivial, and
finding that out costs four requests.

`probe(url)` answers, in one call:

- what `robots.txt` permits, and how fast it asks you to go
- which sitemaps exist (a complete URL universe, far better than link-following)
- whether the same URL serves JSON to an `Accept: application/json` request —
  a free test for a public API hiding behind an HTML route
- which embedded state blobs the page carries, and how big they are
- whether a bot-protection challenge is standing in the way

The result is a `SiteProfile`: a small, printable summary that tells you which
rung of the ladder you are on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence
from urllib.parse import urljoin, urlsplit

from .blobs import StateBlob, scan_state_blobs
from .capture import Capture, Request
from .signals import detect_challenge

__all__ = ["SiteProfile", "probe", "robots_sitemaps"]

DFLT_JSON_ACCEPT = "application/json, text/json;q=0.9, */*;q=0.1"

# Generator fingerprints worth naming, since each implies a known payload rung.
_GENERATOR_HINTS = (
    ("__NEXT_DATA__", "next.js (pages router)"),
    ("__next_f", "next.js (app router / rsc)"),
    ("__NUXT__", "nuxt 2"),
    ("__NUXT_DATA__", "nuxt 3"),
    ("__APOLLO_STATE__", "apollo client"),
    ("__remixContext", "remix"),
    ("ng-state", "angular universal"),
)


@dataclass(frozen=True)
class SiteProfile:
    """What one site will hand over, and how hard it intends to make it."""

    url: str
    host: str
    status: int | None = None
    content_type: str = ""
    robots_allowed: bool | None = None
    crawl_delay: float | None = None
    sitemaps: Sequence[str] = ()
    serves_json: bool = False
    state_blobs: Sequence[StateBlob] = field(default_factory=tuple)
    challenge_vendor: str | None = None
    challenge_interactive: bool = False
    challenge_serving: bool = False
    generator: str | None = None
    notes: Sequence[str] = ()

    @property
    def best_payload_rung(self) -> str:
        """The most structured payload available, named.

        This is the recommendation: `api` beats `state_blob` beats `dom`.
        """
        if self.serves_json:
            return "api"
        if any(blob.kind == "ld_json" for blob in self.state_blobs):
            return "ld_json"
        if self.state_blobs:
            return "state_blob"
        return "dom"

    def summary(self) -> str:
        """A few lines a human or an agent can act on directly."""
        lines = [
            f"{self.url}",
            f"  status          {self.status}",
            f"  robots          allowed={self.robots_allowed} delay={self.crawl_delay}",
            f"  sitemaps        {len(self.sitemaps)}",
            f"  serves JSON     {self.serves_json}",
            f"  generator       {self.generator or 'unknown'}",
            f"  best payload    {self.best_payload_rung}",
        ]
        if self.state_blobs:
            blobs = ", ".join(
                f"{blob.kind}({blob.size}B)" for blob in self.state_blobs[:5]
            )
            lines.append(f"  state blobs     {blobs}")
        if self.challenge_serving:
            kind = (
                "interactive challenge" if self.challenge_interactive else "challenge"
            )
            lines.append(f"  BLOCKED         {self.challenge_vendor} {kind}")
        elif self.challenge_vendor:
            lines.append(
                f"  guarded by      {self.challenge_vendor} (not blocking; "
                f"use an impersonating transport)"
            )
        lines.extend(f"  note            {note}" for note in self.notes)
        return "\n".join(lines)


def robots_sitemaps(robots_txt: str) -> list[str]:
    """Sitemap URLs declared in a robots.txt.

    The `Sitemap:` directive is independent of any `User-agent` block, which is
    why it is parsed separately from the access rules.

    >>> robots_sitemaps("User-agent: *\\nSitemap: https://x.com/sitemap.xml")
    ['https://x.com/sitemap.xml']
    """
    found = []
    for line in robots_txt.splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "sitemap" and value.strip():
            found.append(value.strip())
    return found


def _detect_generator(text: str) -> str | None:
    for marker, name in _GENERATOR_HINTS:
        if marker in text:
            return name
    return None


def probe(
    url: str,
    *,
    fetcher: Callable[[Request], Capture] | None = None,
    check_json: bool = True,
) -> SiteProfile:
    """Reconnoitre a URL: what is available, and what is in the way.

    Performs at most three requests (robots.txt, a JSON-negotiation probe, and the
    page itself) and never raises on a challenge — the whole point is to *report*
    the obstacle rather than trip over it.
    """
    from .fetchers.http import default_fetcher

    fetcher = fetcher or default_fetcher(raise_on_challenge=False)
    split = urlsplit(url)
    host = f"{split.scheme}://{split.netloc}"
    notes: list[str] = []

    robots_txt = _safe_text(fetcher, urljoin(host, "/robots.txt"))
    sitemaps = robots_sitemaps(robots_txt) if robots_txt else []
    robots_allowed, crawl_delay = _robots_verdict(robots_txt, url)

    serves_json = False
    if check_json:
        json_capture = _safe_capture(
            fetcher, Request(url, headers={"Accept": DFLT_JSON_ACCEPT})
        )
        if json_capture is not None and json_capture.ok:
            serves_json = "json" in json_capture.response.content_type
            if serves_json:
                notes.append(
                    "same URL serves JSON under content negotiation - "
                    "no HTML parsing needed"
                )

    capture = _safe_capture(fetcher, Request(url))
    if capture is None:
        return SiteProfile(
            url=url,
            host=host,
            robots_allowed=robots_allowed,
            crawl_delay=crawl_delay,
            sitemaps=tuple(sitemaps),
            serves_json=serves_json,
            notes=tuple(notes + ["page fetch failed"]),
        )

    text = capture.text() if capture.response.is_textual else ""
    verdict = detect_challenge(capture.response.status, capture.response.headers, text)
    blobs = tuple(scan_state_blobs(text)) if text else ()

    if verdict.interactive:
        notes.append("interactive challenge - a human must complete this; do not retry")
    elif verdict.serving:
        notes.append("challenge served - escalate transport, do not retry as-is")
    if not blobs and capture.ok and text and "<div" in text:
        notes.append(
            "no state blob found - may be client-rendered; look for an XHR API"
        )

    return SiteProfile(
        url=url,
        host=host,
        status=capture.response.status,
        content_type=capture.response.content_type,
        robots_allowed=robots_allowed,
        crawl_delay=crawl_delay,
        sitemaps=tuple(sitemaps),
        serves_json=serves_json,
        state_blobs=blobs,
        challenge_vendor=verdict.vendor,
        challenge_interactive=verdict.interactive,
        challenge_serving=verdict.serving,
        generator=_detect_generator(text),
        notes=tuple(notes),
    )


def _robots_verdict(robots_txt: str, url: str) -> tuple[bool | None, float | None]:
    if not robots_txt:
        return None, None
    try:
        from protego import Protego
    except ImportError:  # pragma: no cover
        return None, None
    from .policy import DFLT_USER_AGENT

    parser = Protego.parse(robots_txt)
    delay = parser.crawl_delay(DFLT_USER_AGENT)
    return parser.can_fetch(url, DFLT_USER_AGENT), (
        float(delay) if delay is not None else None
    )


def _safe_capture(fetcher, request: Request) -> Capture | None:
    try:
        return fetcher(request)
    except Exception:
        return None


def _safe_text(fetcher, url: str) -> str:
    capture = _safe_capture(fetcher, Request(url))
    return capture.text() if capture is not None and capture.ok else ""

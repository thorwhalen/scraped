"""Policy: robots compliance, politeness, and retries.

Policy is an *injected object*, not a global setting, and its outcome is recorded
in every `Capture`. That is the whole point: "did we respect robots on this
capture" becomes a queryable fact rather than an archaeology exercise, and an
override is a deliberate, attributable act rather than a forgotten flag.

>>> policy = RobotsPolicy(get_robots_txt=lambda host: "User-agent: *\\nDisallow: /x")
>>> policy.check("https://example.com/ok").robots_allowed
True
>>> policy.check("https://example.com/x/1").robots_allowed
False
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping
from urllib.parse import urlsplit

from .capture import PolicyOutcome

__all__ = [
    "RobotsPolicy",
    "Politeness",
    "RobotsDisallowed",
    "retry_delays",
    "should_retry",
    "DFLT_USER_AGENT",
]

# An honest, contactable identity is the cheapest insurance available and costs
# nothing when things go well.
DFLT_USER_AGENT = "scraped/acquire (+https://github.com/thorwhalen/scraped)"

DFLT_MIN_INTERVAL = 1.0
DFLT_JITTER = 0.5
DFLT_MAX_ATTEMPTS = 4
DFLT_BASE_DELAY = 1.0
DFLT_MAX_DELAY = 30.0

# Statuses worth another attempt. 403 is deliberately absent: retrying a refusal
# burns the address's reputation and confirms automation.
RETRIABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class RobotsDisallowed(PermissionError):
    """The robots policy forbids this URL and no override was given."""


def _host_of(url: str) -> str:
    split = urlsplit(url)
    return f"{split.scheme}://{split.netloc}"


@dataclass
class RobotsPolicy:
    """Robots compliance, defaulting to on.

    `get_robots_txt` is injected so this module never performs I/O itself — which
    keeps it testable and avoids a circular dependency on the fetchers.

    Set `override_reason` to proceed despite a disallow. The reason is recorded in
    the capture; there is deliberately no way to override *silently*.
    """

    get_robots_txt: Callable[[str], str | None]
    user_agent: str = DFLT_USER_AGENT
    enabled: bool = True
    override_reason: str | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    def _parser(self, host: str):
        if host not in self._cache:
            try:
                from protego import Protego
            except ImportError:  # pragma: no cover - exercised only without protego
                self._cache[host] = None
                return None
            text = self.get_robots_txt(host)
            self._cache[host] = Protego.parse(text) if text else None
        return self._cache[host]

    def allowed(self, url: str) -> bool | None:
        """Whether the policy permits this URL. `None` when unknown."""
        parser = self._parser(_host_of(url))
        if parser is None:
            return None
        return parser.can_fetch(url, self.user_agent)

    def crawl_delay(self, url: str) -> float | None:
        """Any `Crawl-delay` the site asks for.

        RFC 9309 deliberately excluded `Crawl-delay` from the standard, but it is
        a widely honored convention and honoring it is free.
        """
        parser = self._parser(_host_of(url))
        if parser is None:
            return None
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None

    def check(self, url: str) -> PolicyOutcome:
        """Evaluate the policy, without raising."""
        if not self.enabled:
            return PolicyOutcome(
                robots_checked=False,
                robots_allowed=None,
                robots_override_reason=self.override_reason or "robots policy disabled",
            )
        allowed = self.allowed(url)
        return PolicyOutcome(
            robots_checked=True,
            robots_allowed=allowed,
            robots_override_reason=self.override_reason,
        )

    def enforce(self, url: str) -> PolicyOutcome:
        """Evaluate the policy and raise if it forbids the URL.

        >>> policy = RobotsPolicy(get_robots_txt=lambda h: "User-agent: *\\nDisallow: /")
        >>> policy.enforce("https://example.com/a")
        Traceback (most recent call last):
            ...
        scraped.acquire.policy.RobotsDisallowed: robots.txt disallows https://example.com/a
        """
        outcome = self.check(url)
        if outcome.robots_allowed is False and not self.override_reason:
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        return outcome


@dataclass
class Politeness:
    """Per-host pacing. Per-host, never global — one slow site must not throttle
    every other one.

    >>> pace = Politeness(min_interval=0.0, jitter=0.0)
    >>> pace.wait("https://example.com/a")  # returns immediately
    0.0
    """

    min_interval: float = DFLT_MIN_INTERVAL
    jitter: float = DFLT_JITTER
    _last: dict = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def wait(self, url: str, *, crawl_delay: float | None = None) -> float:
        """Sleep as long as politeness requires; return the seconds slept."""
        interval = max(self.min_interval, crawl_delay or 0.0)
        host = urlsplit(url).netloc
        with self._lock:
            elapsed = time.monotonic() - self._last.get(host, float("-inf"))
            delay = max(0.0, interval - elapsed) + random.uniform(0, self.jitter)
            self._last[host] = time.monotonic() + delay
        if delay > 0:
            time.sleep(delay)
        return delay


def should_retry(status: int | None, *, challenge: bool = False) -> bool:
    """Whether another attempt is warranted.

    A challenge is never retried — the answer will not change, and repeating it
    confirms automation.

    >>> should_retry(503)
    True
    >>> should_retry(403)
    False
    >>> should_retry(429, challenge=True)
    False
    """
    if challenge:
        return False
    if status is None:  # connection error / timeout
        return True
    return status in RETRIABLE_STATUSES


def retry_delays(
    attempts: int = DFLT_MAX_ATTEMPTS,
    *,
    base: float = DFLT_BASE_DELAY,
    cap: float = DFLT_MAX_DELAY,
    rng: random.Random | None = None,
) -> list[float]:
    """Full-jitter backoff delays.

    Full jitter does the least total work of the common strategies, at the cost of
    slightly longer wall time — the right trade when being polite is the point.

    >>> delays = retry_delays(4, rng=random.Random(0))
    >>> len(delays), all(0 <= d <= 8 for d in delays)
    (3, True)
    """
    rng = rng or random.Random()
    return [rng.uniform(0, min(cap, base * 2**i)) for i in range(attempts - 1)]


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """Honor an explicit `Retry-After`, which is a site telling you the answer.

    >>> retry_after_seconds({"Retry-After": "12"})
    12.0
    >>> retry_after_seconds({}) is None
    True
    """
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                return None
    return None

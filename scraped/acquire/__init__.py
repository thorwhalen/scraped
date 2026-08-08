"""Raw data acquisition: get the bytes down faithfully, with provenance.

An information-extraction job splits into three concerns. This package owns only
the first, and provides seams for the other two rather than implementing them:

1. **acquisition** — get the raw bytes, with enough provenance to know where they
   came from and whether you were allowed to take them. *This package.*
2. **extraction** — pull the target fields out. Hand `Capture` and the located
   state blobs to whatever does this (an LLM, a schema, hand-written code).
3. **formatting** — shape the result. Downstream entirely.

## Simple path

    >>> from scraped.acquire import probe, fetch, scan_state_blobs
    >>> profile = probe("https://example.com")           # doctest: +SKIP
    >>> print(profile.summary())                         # doctest: +SKIP
    >>> capture = fetch("https://example.com")           # doctest: +SKIP
    >>> blobs = scan_state_blobs(capture.text())         # doctest: +SKIP

## The one idea worth internalizing

Payload shape and transport are **independent axes**. Optimize payload first:

    payload:    api > internal api > state blob > sitemap > dom > text > vision
    transport:  plain http > impersonating http > +session > browser > headful

Any payload rung is reachable from any transport rung, and the highest-value cell
on the grid — *drive a real browser, then read the embedded state blob* — is the
one the usual single-ladder framing hides. Reading a page's own `__NEXT_DATA__`
beats parsing its DOM even when you had to pay for a browser to see it: the blob
is the data rather than a rendering of it, and it is complete even when the DOM
renders lazily.

See `misc/docs/acquisition-architecture.md` for the full design.
"""

from .blobs import StateBlob, scan_state_blobs, state_blob_markers
from .cache import CaptureStore, cached, capture_store
from .capture import (
    Capture,
    Fetcher,
    FetcherInfo,
    PolicyOutcome,
    ProbeMarks,
    Request,
    Response,
    body_digest,
    request_digest,
)
from .fetchers import (
    BrowserArtifacts,
    BrowserFetcher,
    BrowserSession,
    CurlCffiTransport,
    HttpFetcher,
    UrllibTransport,
    browser_check_requirements,
    default_fetcher,
    storage_state_headers,
)
from .policy import Politeness, RobotsDisallowed, RobotsPolicy
from .probe import SiteProfile, probe, robots_sitemaps
from .signals import ChallengeEncountered, classify, detect_challenge
from .url import canonicalize_url

__all__ = [
    # simple path
    "probe",
    "fetch",
    "scan_state_blobs",
    # records
    "Capture",
    "Request",
    "Response",
    "SiteProfile",
    "StateBlob",
    "FetcherInfo",
    "PolicyOutcome",
    "ProbeMarks",
    # seams
    "Fetcher",
    "HttpFetcher",
    "CurlCffiTransport",
    "UrllibTransport",
    "default_fetcher",
    "BrowserFetcher",
    "BrowserSession",
    "BrowserArtifacts",
    "browser_check_requirements",
    "storage_state_headers",
    # policy
    "RobotsPolicy",
    "RobotsDisallowed",
    "Politeness",
    # caching
    "CaptureStore",
    "capture_store",
    "cached",
    # diagnostics
    "ChallengeEncountered",
    "detect_challenge",
    "classify",
    "state_blob_markers",
    # utilities
    "canonicalize_url",
    "request_digest",
    "body_digest",
    "robots_sitemaps",
]


def fetch(url: str, *, fetcher=None, **request_kwargs) -> Capture:
    """Fetch one URL into a `Capture`, using sensible defaults.

    The simple thing made simple: an impersonating transport, per-host politeness,
    jittered retries, challenge detection, and an automatic state-blob scan — none
    of which you have to name to get.

    For anything beyond one URL, build a fetcher once and reuse it, so politeness
    and caching are shared across the job.
    """
    fetcher = fetcher or default_fetcher()
    return fetcher(Request(url, **request_kwargs))

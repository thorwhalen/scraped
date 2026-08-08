"""Transport rungs: the interchangeable ways of actually getting bytes.

Every fetcher is a `Callable[[Request], Capture]`, so rungs are swappable without
any caller knowing which one is in play. Rung numbers follow the transport ladder
in `misc/docs/acquisition-architecture.md`.

The browser rung imports cleanly without a browser installed — the driver is
imported only when a session is actually opened — so `check_requirements()` can
tell you what is missing instead of an `ImportError` doing it badly.
"""

from .browser import (
    BrowserArtifacts,
    BrowserFetcher,
    BrowserSession,
    storage_state_headers,
)
from .browser import check_requirements as browser_check_requirements
from .http import (
    CurlCffiTransport,
    HttpFetcher,
    UrllibTransport,
    default_fetcher,
    robots_reader,
)

__all__ = [
    "HttpFetcher",
    "UrllibTransport",
    "CurlCffiTransport",
    "default_fetcher",
    "robots_reader",
    "BrowserFetcher",
    "BrowserSession",
    "BrowserArtifacts",
    "browser_check_requirements",
    "storage_state_headers",
]

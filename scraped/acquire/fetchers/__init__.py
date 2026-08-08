"""Transport rungs: the interchangeable ways of actually getting bytes.

Every fetcher is a `Callable[[Request], Capture]`, so rungs are swappable without
any caller knowing which one is in play. Rung numbers follow the transport ladder
in `misc/docs/acquisition-architecture.md`.
"""

from .http import HttpFetcher, UrllibTransport, CurlCffiTransport, default_fetcher

__all__ = ["HttpFetcher", "UrllibTransport", "CurlCffiTransport", "default_fetcher"]

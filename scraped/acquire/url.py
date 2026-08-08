"""URL canonicalization — the dedup key for the frontier and the cache.

The same content is routinely reachable at many URLs (tracking parameters,
session ids, `www` vs bare, trailing slashes, http vs https). Canonicalizing
before hashing is what stops a crawl from fetching the same page five times and
what makes a cache actually hit.

>>> canonicalize_url("HTTPS://Example.COM:443/a/../b/?utm_source=x&z=1&a=2#frag")
'https://example.com/b?a=2&z=1'
"""

from __future__ import annotations

import posixpath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["canonicalize_url", "strip_tracking_params", "DFLT_TRACKING_PREFIXES"]

# Parameters that identify a *referral*, not a resource. Dropping them is what
# collapses the same page arriving from five campaigns into one cache entry.
DFLT_TRACKING_PREFIXES = ("utm_", "gclid", "fbclid", "mc_", "_hs", "msclkid", "igshid")

_DFLT_PORTS = {"http": "80", "https": "443"}


def strip_tracking_params(
    query: str, *, prefixes: tuple[str, ...] = DFLT_TRACKING_PREFIXES
) -> str:
    """Drop referral parameters and sort the rest.

    Sorting matters: query order is not semantically meaningful for the vast
    majority of endpoints, and leaving it unsorted fragments the cache.

    >>> strip_tracking_params("b=2&utm_source=nl&a=1")
    'a=1&b=2'
    """
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.lower().startswith(prefixes)
    ]
    return urlencode(sorted(kept))


def canonicalize_url(url: str, *, keep_fragment: bool = False) -> str:
    """Reduce a URL to a stable identity for deduplication.

    Lowercases scheme and host, drops the default port, normalizes dot segments,
    strips a trailing slash on non-root paths, removes tracking parameters, sorts
    the query, and drops the fragment (which is client-side by definition).

    >>> canonicalize_url("https://example.com")
    'https://example.com/'
    >>> canonicalize_url("https://example.com/a/")
    'https://example.com/a'
    >>> canonicalize_url("https://example.com/a/./b/../c")
    'https://example.com/a/c'
    """
    split = urlsplit(url.strip())
    scheme = split.scheme.lower()
    host = split.hostname or ""
    port = split.port

    netloc = host
    if port is not None and str(port) != _DFLT_PORTS.get(scheme, ""):
        netloc = f"{host}:{port}"
    if split.username:
        credentials = split.username + (f":{split.password}" if split.password else "")
        netloc = f"{credentials}@{netloc}"

    path = posixpath.normpath(split.path or "/")
    if path == ".":
        path = "/"
    # posixpath.normpath eats a meaningful trailing slash; we drop it anyway,
    # except at the root where it is the whole path.
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            strip_tracking_params(split.query),
            split.fragment if keep_fragment else "",
        )
    )

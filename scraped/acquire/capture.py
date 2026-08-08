"""The `Capture` record: an immutable, provenance-bearing result of one fetch.

A `Capture` is deliberately isomorphic to a WARC (ISO 28500) request/response
pair, so that emitting real WARC later is a serialization change rather than a
redesign. It carries everything needed to answer "where did this byte string come
from, when, how, and were we allowed to take it" without re-reading the network.

Two invariants matter:

- **Raw bytes, never decoded text.** Decoding is a lossy interpretation and
  belongs to the extraction layer. `Capture.text()` exists for convenience but is
  explicit about the encoding it chose.
- **Body is content-addressed.** `body_sha256` is the identity of the payload,
  which buys deduplication, change detection, and cheap re-runs together.

>>> capture = Capture.from_body(
...     Request("https://example.com"), b"<html></html>", status=200
... )
>>> capture.response.status
200
>>> capture.response.body_len
13
>>> capture.ok
True
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .url import canonicalize_url

__all__ = [
    "Request",
    "Response",
    "FetcherInfo",
    "PolicyOutcome",
    "ProbeMarks",
    "Capture",
    "Fetcher",
    "body_digest",
    "request_digest",
    "utcnow",
]

# Headers that legitimately vary a response and therefore belong in a cache key.
# Anything else is noise that would fragment the cache for no benefit.
DFLT_VARY_HEADERS = ("accept", "accept-language", "authorization", "cookie")

_TEXTUAL_HINTS = ("text/", "json", "xml", "javascript", "html")


def utcnow() -> datetime:
    """Timezone-aware current time. Provenance timestamps are always UTC.

    >>> utcnow().tzinfo is timezone.utc
    True
    """
    return datetime.now(timezone.utc)


def body_digest(body: bytes) -> str:
    """Content address of a payload.

    >>> body_digest(b"")[:8]
    'e3b0c442'
    """
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class Request:
    """What we asked for."""

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class Response:
    """What came back, minus the payload itself."""

    status: int
    final_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    redirect_chain: Sequence[str] = ()
    body_sha256: str = ""
    body_len: int = 0

    @property
    def content_type(self) -> str:
        """Lowercased `Content-Type`, or empty string when absent."""
        for key, value in self.headers.items():
            if key.lower() == "content-type":
                return value.lower()
        return ""

    @property
    def is_textual(self) -> bool:
        """Whether the declared content type suggests text rather than binary.

        >>> Response(200, "u", {"Content-Type": "text/html"}).is_textual
        True
        >>> Response(200, "u", {"Content-Type": "image/png"}).is_textual
        False
        """
        return any(hint in self.content_type for hint in _TEXTUAL_HINTS)


@dataclass(frozen=True)
class FetcherInfo:
    """Which machinery produced this capture, and at what transport rung."""

    client_name: str = "unknown"
    client_version: str = ""
    impersonate_profile: str | None = None
    transport_rung: int = 0


@dataclass(frozen=True)
class PolicyOutcome:
    """Whether policy was consulted, and what it said.

    Recording the override *reason* is the point: "did we respect robots on this
    capture" becomes a queryable fact rather than an archaeology exercise.
    """

    robots_checked: bool = False
    robots_allowed: bool | None = None
    robots_override_reason: str | None = None


@dataclass(frozen=True)
class ProbeMarks:
    """Cheap structural facts noticed at capture time.

    Filled in automatically for HTML captures. This is the seam to the extraction
    layer: it says *where the structured data is* without extracting anything.
    """

    state_blob_markers: Sequence[str] = ()
    ld_json_count: int = 0
    #: Body shorter than the declared Content-Length: a silent corruption that
    #: otherwise only shows up downstream.
    truncated: bool = False
    challenge_vendor: str | None = None
    #: Whether a challenge was served *instead of* content. A `challenge_vendor`
    #: without this only records who guards the origin.
    challenge_serving: bool = False


@dataclass(frozen=True)
class Capture:
    """One fetch, with provenance.

    The body is carried in memory here; stores persist it separately, keyed by
    `response.body_sha256`.
    """

    request: Request
    response: Response
    fetched_at: datetime = field(default_factory=utcnow)
    elapsed_ms: float = 0.0
    fetcher: FetcherInfo = field(default_factory=FetcherInfo)
    policy: PolicyOutcome = field(default_factory=PolicyOutcome)
    probe: ProbeMarks = field(default_factory=ProbeMarks)
    body: bytes = b""

    @classmethod
    def from_body(
        cls, request: Request, body: bytes, *, status: int = 200, **kwargs: Any
    ) -> "Capture":
        """Build a capture from a payload, deriving the content address.

        Convenience for tests, replays, and fetchers that already hold the bytes.
        """
        response = kwargs.pop("response", None) or Response(
            status=status,
            final_url=kwargs.pop("final_url", request.url),
            headers=kwargs.pop("headers", {}),
            redirect_chain=kwargs.pop("redirect_chain", ()),
        )
        response = replace(response, body_sha256=body_digest(body), body_len=len(body))
        return cls(request=request, response=response, body=body, **kwargs)

    @property
    def ok(self) -> bool:
        """Whether the status is a success code."""
        return 200 <= self.response.status < 300

    @property
    def blocked(self) -> bool:
        """Whether this looks like an access-control rejection rather than an error."""
        return self.response.status in (401, 403, 429) or self.probe.challenge_serving

    def text(self, *, encoding: str | None = None, errors: str = "replace") -> str:
        """Decode the body, explicitly.

        Decoding is the extraction layer's concern; this is a convenience, and it
        is deliberate about the fact that a choice is being made.

        >>> Capture.from_body(Request("u"), b"hi").text()
        'hi'
        """
        return self.body.decode(encoding or self._declared_encoding(), errors=errors)

    def _declared_encoding(self) -> str:
        """The charset the server claimed, if Python actually has that codec.

        `errors="replace"` protects against bad *bytes*, not a bad codec *name*:
        `charset=utf8mb4` (MySQL-backed CMSes) raises `LookupError`. Servers
        declare nonexistent charsets often enough that this must never propagate.
        """
        content_type = self.response.content_type
        if "charset=" in content_type:
            declared = content_type.split("charset=", 1)[1].split(";")[0].strip()
            declared = declared.strip("\"'")
            try:
                codecs.lookup(declared)
                return declared
            except (LookupError, ValueError):
                pass
        return "utf-8"

    def metadata(self) -> dict:
        """Everything except the payload, JSON-ready.

        This is what a store persists alongside the content-addressed body.

        A `bytes` request body is base64-encoded rather than stringified. JSON's
        `default=str` turns bytes into their *repr*, which round-trips to a
        corrupted `str` with no error at write time — and rung (b), calling a
        site's internal JSON/GraphQL API, is POST-shaped by definition, so this
        is the common case rather than an exotic one.
        """
        as_dict = asdict(self)
        as_dict.pop("body")
        as_dict["fetched_at"] = self.fetched_at.isoformat()
        request_body = as_dict["request"].get("body")
        if isinstance(request_body, (bytes, bytearray)):
            as_dict["request"]["body"] = base64.b64encode(request_body).decode("ascii")
            as_dict["request"]["body_is_base64"] = True
        return as_dict

    def to_json(self, **kwargs: Any) -> str:
        """Serialize the metadata.

        Deliberately no `default=` fallback: an unserializable field should fail
        loudly here rather than be silently stringified into the store.
        """
        return json.dumps(self.metadata(), **kwargs)


@runtime_checkable
class Fetcher(Protocol):
    """The dependency-injection seam of the whole package.

    Every transport rung — plain HTTP, impersonating HTTP, browser — is just a
    callable of this shape. This is the generalization of the older
    `acquire_content(uri_to_content, ...)` seam.
    """

    def __call__(self, request: Request) -> Capture:
        """Fetch one request into one capture."""
        ...


def request_digest(
    request: Request, *, vary_headers: Sequence[str] = DFLT_VARY_HEADERS
) -> str:
    """Cache key for a request: what actually distinguishes one fetch from another.

    Canonicalized URL + method + the subset of headers that plausibly vary the
    response + a hash of any body. Deliberately ignores headers that only add
    noise (User-Agent, tracing ids), because fragmenting the cache on those makes
    re-runs expensive for no correctness gain.

    >>> a = Request("https://x.com/p?b=2&a=1")
    >>> b = Request("https://x.com/p?a=1&b=2")
    >>> request_digest(a) == request_digest(b)  # query order is not meaningful
    True
    >>> c = Request("https://x.com/p?a=1&b=2", headers={"User-Agent": "whatever"})
    >>> request_digest(b) == request_digest(c)  # noise headers ignored
    True
    """
    # Fold case deterministically: with both "Accept" and "accept" present, a
    # plain dict comprehension would let insertion order decide the digest.
    lowered: dict[str, list[str]] = {}
    for key, value in request.headers.items():
        lowered.setdefault(key.lower(), []).append(value)
    significant = {
        key: ", ".join(sorted(lowered[key]))
        for key in sorted(vary_headers)
        if key in lowered
    }
    parts = [
        request.method.upper(),
        canonicalize_url(request.url),
        json.dumps(significant, sort_keys=True),
        body_digest(request.body) if request.body else "",
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()

"""HTTP transport rungs 1–2: plain and browser-impersonating clients.

Start at the impersonating client, not a plain one. The marginal cost is a single
keyword argument and it removes an entire failure class — TLS (JA3/JA4), HTTP/2
and header-order fingerprinting — which otherwise presents as an unexplained
`403` on a URL that works fine in a browser.

Transports sit behind a facade because this corner of the ecosystem churns hard:
clients get abandoned, relicensed, and renamed on a yearly cadence. Swapping the
transport must never touch calling code.

>>> fetcher = HttpFetcher(transport=_EchoTransport(b"<html>ok</html>"))
>>> capture = fetcher(Request("https://example.com"))
>>> capture.ok, capture.response.body_len
(True, 15)
>>> capture.fetcher.transport_rung
1
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

from ..capture import (
    Capture,
    FetcherInfo,
    PolicyOutcome,
    ProbeMarks,
    Request,
    Response,
    body_digest,
    utcnow,
)
from ..blobs import state_blob_markers
from ..policy import (
    DFLT_MAX_ATTEMPTS,
    DFLT_USER_AGENT,
    Politeness,
    RobotsPolicy,
    retry_after_seconds,
    retry_delays,
    should_retry,
)
from ..signals import ChallengeEncountered, detect_challenge, looks_truncated

__all__ = [
    "TransportResult",
    "robots_reader",
    "Transport",
    "UrllibTransport",
    "CurlCffiTransport",
    "HttpFetcher",
    "default_fetcher",
]

DFLT_TIMEOUT = 30.0
DFLT_IMPERSONATE = "chrome"
# Honor Retry-After, but not blindly: 'Retry-After: 86400' is a polite way of
# saying go away, and sleeping on it hangs the job for a day.
DFLT_MAX_RETRY_AFTER = 120.0

# Chrome's own order matters as much as the values: header *ordering* is part of
# what is fingerprinted. Impersonating transports set their own; this is the
# fallback for the plain one.
DFLT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": DFLT_USER_AGENT,
}


@dataclass(frozen=True)
class TransportResult:
    """The raw outcome of one network call, before any policy or provenance."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str
    redirect_chain: tuple[str, ...] = ()


class Transport(Protocol):
    """A client that can perform one HTTP request.

    The narrow waist of the package: everything above it is policy, provenance,
    and diagnosis; everything below it is somebody else's library.
    """

    name: str
    rung: int

    def send(self, request: Request, *, timeout: float) -> TransportResult:
        """Perform the request."""
        ...


class _EchoTransport:
    """A transport that returns a fixed body. For doctests and offline tests."""

    name = "echo"
    rung = 1

    def __init__(self, body: bytes = b"", *, status: int = 200, headers=None):
        self._body, self._status = body, status
        self._headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def send(self, request: Request, *, timeout: float) -> TransportResult:
        return TransportResult(
            status=self._status,
            headers=self._headers,
            body=self._body,
            final_url=request.url,
        )


class UrllibTransport:
    """Stdlib transport. Zero dependencies, maximally detectable.

    Useful for robots.txt, sitemaps, and any origin that does not care — but it
    advertises an OpenSSL TLS fingerprint, so treat a `403` here as uninformative
    until retried on the impersonating rung.
    """

    name = "urllib"
    rung = 1

    def send(
        self, request: Request, *, timeout: float = DFLT_TIMEOUT
    ) -> TransportResult:
        import urllib.error
        import urllib.request

        headers = {**DFLT_HEADERS, **dict(request.headers)}
        req = urllib.request.Request(
            request.url, data=request.body, headers=headers, method=request.method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                return TransportResult(
                    status=response.status,
                    headers=_fold_headers(response.headers),
                    body=body,
                    final_url=response.url,
                    redirect_chain=(
                        (request.url,) if response.url != request.url else ()
                    ),
                )
        except urllib.error.HTTPError as error:  # an HTTP error is still a result
            return TransportResult(
                status=error.code,
                headers=_fold_headers(error.headers) if error.headers else {},
                body=error.read(),
                final_url=error.url or request.url,
            )


class CurlCffiTransport:
    """Browser-impersonating transport — the default, and where you should start.

    Reproduces a real browser's TLS ClientHello and HTTP/2 settings, which is what
    makes the difference on origins that fingerprint the client rather than
    reading the User-Agent.
    """

    name = "curl_cffi"
    rung = 2

    def __init__(self, impersonate: str = DFLT_IMPERSONATE):
        self.impersonate = impersonate

    def send(
        self, request: Request, *, timeout: float = DFLT_TIMEOUT
    ) -> TransportResult:
        session = self._session()
        response = session.request(
            request.method,
            request.url,
            headers=dict(request.headers) or None,
            data=request.body,
            timeout=timeout,
            impersonate=self.impersonate,
            allow_redirects=True,
        )
        history = tuple(getattr(h, "url", "") for h in getattr(response, "history", ()))
        return TransportResult(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            final_url=str(response.url),
            redirect_chain=history,
        )

    @staticmethod
    def _session():
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as error:  # pragma: no cover
            raise ImportError(
                "The impersonating transport needs curl_cffi (a core dependency, so "
                "this means a broken install). Reinstall with:\n"
                "    pip install curl_cffi\n"
                "or fall back to UrllibTransport(), accepting that origins which "
                "fingerprint TLS will refuse it."
            ) from error
        return curl_requests.Session()


@dataclass
class HttpFetcher:
    """A `Fetcher` that turns requests into provenance-bearing captures.

    Owns the concerns that must not be reimplemented per job: robots policy,
    per-host politeness, jittered retries, challenge detection, and the automatic
    state-blob scan that makes the result useful to the extraction layer.
    """

    transport: Transport = field(default_factory=CurlCffiTransport)
    timeout: float = DFLT_TIMEOUT
    headers: Mapping[str, str] = field(default_factory=dict)
    robots: RobotsPolicy | None = None
    politeness: Politeness | None = None
    max_attempts: int = DFLT_MAX_ATTEMPTS
    max_retry_after: float = DFLT_MAX_RETRY_AFTER
    raise_on_challenge: bool = True

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")

    def __call__(self, request: Request) -> Capture:
        """Fetch one request, applying policy and recording provenance."""
        policy_outcome = (
            self.robots.enforce(request.url) if self.robots else PolicyOutcome()
        )
        if self.politeness:
            crawl_delay = self.robots.crawl_delay(request.url) if self.robots else None
            self.politeness.wait(request.url, crawl_delay=crawl_delay)

        merged = Request(
            url=request.url,
            method=request.method,
            headers={**self.headers, **dict(request.headers)},
            body=request.body,
        )
        result, elapsed_ms = self._send_with_retries(merged)
        return self._to_capture(merged, result, elapsed_ms, policy_outcome)

    def _send_with_retries(self, request: Request) -> tuple[TransportResult, float]:
        delays = retry_delays(self.max_attempts)
        started = time.perf_counter()
        last: TransportResult | None = None

        for attempt in range(self.max_attempts):
            try:
                last = self.transport.send(request, timeout=self.timeout)
            except Exception:
                if attempt == self.max_attempts - 1:
                    raise
                time.sleep(delays[attempt])
                continue

            verdict = detect_challenge(last.status, last.headers, _decode(last.body))
            if not should_retry(
                last.status,
                challenge=bool(verdict),
                vendor_guarded=verdict.vendor is not None,
            ):
                break
            if attempt == self.max_attempts - 1:
                break
            explicit = retry_after_seconds(last.headers)
            if explicit is not None and explicit > self.max_retry_after:
                break  # the site is asking for longer than this job should wait
            time.sleep(explicit if explicit is not None else delays[attempt])

        if last is None:  # unreachable given __post_init__, but never assert:
            raise RuntimeError(  # `python -O` strips asserts and we'd pass None on
                f"no attempt was made for {request.url}"
            )
        return last, (time.perf_counter() - started) * 1000

    def _to_capture(
        self,
        request: Request,
        result: TransportResult,
        elapsed_ms: float,
        policy_outcome: PolicyOutcome,
    ) -> Capture:
        text = _decode(result.body)
        verdict = detect_challenge(result.status, result.headers, text)

        if verdict and self.raise_on_challenge:
            raise ChallengeEncountered(
                result.final_url,
                vendor=verdict.vendor,
                interactive=verdict.interactive,
                status=result.status,
            )

        marks = ProbeMarks(
            state_blob_markers=tuple(state_blob_markers(text)) if text else (),
            ld_json_count=text.count("application/ld+json") if text else 0,
            truncated=looks_truncated(result.body, result.headers),
            challenge_vendor=verdict.vendor,
            challenge_serving=verdict.serving,
        )
        response = Response(
            status=result.status,
            final_url=result.final_url,
            headers=dict(result.headers),
            redirect_chain=tuple(result.redirect_chain),
            body_sha256=body_digest(result.body),
            body_len=len(result.body),
        )
        return Capture(
            request=request,
            response=response,
            fetched_at=utcnow(),
            elapsed_ms=round(elapsed_ms, 2),
            fetcher=FetcherInfo(
                client_name=self.transport.name,
                impersonate_profile=getattr(self.transport, "impersonate", None),
                transport_rung=self.transport.rung,
            ),
            policy=policy_outcome,
            probe=marks,
            body=result.body,
        )


def _fold_headers(message) -> dict[str, str]:
    """Flatten an email.Message-style header set without losing duplicates.

    `dict(message.items())` keeps only the last value for a repeated name, which
    for `Set-Cookie` means dropping every cookie but one — and four of the nine
    bot-vendor markers are cookie-based, so a real block can go undetected.
    """
    folded: dict[str, list[str]] = {}
    for key, value in message.items():
        folded.setdefault(key, []).append(value)
    return {key: ", ".join(values) for key, values in folded.items()}


def _decode(body: bytes) -> str:
    """Decode a body for text signals.

    Deliberately not truncated. `__NEXT_DATA__` is emitted near the *end* of a
    document, and the script regex needs the closing tag, so a head-limited scan
    silently reports no blobs on exactly the large pages where blobs matter most.
    Measured cost of a full scan is well under a second on a 20 MB document.
    """
    return body.decode("utf-8", errors="replace")


def robots_reader(transport: Transport) -> "Callable[[str], str | None]":
    """A `robots.txt` reader that bypasses policy, so checking cannot recurse."""

    def read(host: str) -> str | None:
        try:
            result = transport.send(
                Request(f"{host.rstrip('/')}/robots.txt"), timeout=DFLT_TIMEOUT
            )
        except Exception:
            return None
        if not 200 <= result.status < 300:
            return None
        return result.body.decode("utf-8", errors="replace")

    return read


def default_fetcher(*, robots: "bool | RobotsPolicy" = True, **kwargs) -> HttpFetcher:
    """The recommended starting fetcher: impersonating, polite, robots-respecting.

    Robots compliance defaults to **on**, per the design: an override must be a
    deliberate, attributable act. Pass a `RobotsPolicy(override_reason=...)` as
    `robots=` to supply your own — the reason is recorded in every capture — or
    `robots=False` to disable the check entirely.

    Falls back to the stdlib transport when `curl_cffi` is unavailable, so the
    package stays usable and is merely *less capable* without it.
    """
    try:
        from curl_cffi import requests as _  # noqa: F401

        transport: Transport = CurlCffiTransport()
    except ImportError:  # pragma: no cover
        transport = UrllibTransport()
    kwargs.setdefault("transport", transport)
    kwargs.setdefault("politeness", Politeness())
    if isinstance(robots, RobotsPolicy):
        # A caller-supplied policy carries the attributable override reason; an
        # earlier version treated `robots` as a bool and silently discarded it.
        kwargs["robots"] = robots
    elif robots:
        kwargs.setdefault(
            "robots", RobotsPolicy(get_robots_txt=robots_reader(kwargs["transport"]))
        )
    return HttpFetcher(**kwargs)

"""Caching: make a re-run free, and make change detection fall out for nothing.

This is what makes iterating on the *extraction* half tolerable — you should be
able to re-run an extraction a hundred times without touching the network once.

The cache is owned here rather than imported because the popular HTTP caches bind
to a specific client's transport stack (`requests.Session`, httpx transports) and
none of them work with an impersonating libcurl-based client. Since cache entries
must also carry provenance and survive a client swap, the right shape is a plain
`Mapping` keyed by request digest.

Bodies are stored separately, keyed by their own SHA-256. That single choice buys
deduplication (the same payload at five URLs is stored once) and change detection
(a digest that did not change means the page did not change).

>>> store = CaptureStore.in_memory()
>>> fetcher = cached(lambda request: Capture.from_body(request, b"hi"), store)
>>> first = fetcher(Request("https://example.com"))
>>> second = fetcher(Request("https://example.com"))   # served from the store
>>> first.response.body_sha256 == second.response.body_sha256
True
>>> len(store)
1
"""

from __future__ import annotations

import base64
import json
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator

from .capture import (
    Capture,
    FetcherInfo,
    PolicyOutcome,
    ProbeMarks,
    Request,
    Response,
    request_digest,
)

__all__ = ["CaptureStore", "cached", "capture_store"]


def _as_text(value) -> str:
    """Stores may hand back bytes or str; both are fine."""
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value


def _capture_from(metadata: dict, body: bytes) -> Capture:
    """Rebuild a `Capture` from its persisted metadata and body.

    Restores the two things JSON cannot represent on its own: a `bytes` request
    body (base64 on the way out) and tuple-typed fields (JSON only has arrays).
    Both matter for round-trip equality — a restored capture that merely *looks*
    like the original will pass a shallow test and fail a re-digest.
    """
    request_fields = dict(metadata["request"])
    if request_fields.pop("body_is_base64", False) and request_fields.get("body"):
        request_fields["body"] = base64.b64decode(request_fields["body"])

    response_fields = dict(metadata["response"])
    response_fields["redirect_chain"] = tuple(
        response_fields.get("redirect_chain") or ()
    )

    probe_fields = dict(metadata.get("probe", {}))
    probe_fields["state_blob_markers"] = tuple(
        probe_fields.get("state_blob_markers") or ()
    )

    return Capture(
        request=Request(**request_fields),
        response=Response(**response_fields),
        fetched_at=datetime.fromisoformat(metadata["fetched_at"]),
        elapsed_ms=metadata.get("elapsed_ms", 0.0),
        fetcher=FetcherInfo(**metadata.get("fetcher", {})),
        policy=PolicyOutcome(**metadata.get("policy", {})),
        probe=ProbeMarks(**probe_fields),
        body=body,
    )


@dataclass
class CaptureStore(MutableMapping):
    """A `Mapping[request_digest, Capture]` over two content stores.

    `metadata` and `bodies` are any `MutableMapping` — a dict, a `dol` directory
    store, an S3 store. That is the whole migration story: local-dir to cloud is a
    constructor change and touches no acquisition logic.
    """

    metadata: MutableMapping
    bodies: MutableMapping

    @classmethod
    def in_memory(cls) -> "CaptureStore":
        """A store backed by dicts. For tests and one-shot jobs."""
        return cls(metadata={}, bodies={})

    def __getitem__(self, digest: str) -> Capture:
        meta = json.loads(_as_text(self.metadata[digest]))
        body = self.bodies.get(meta["response"]["body_sha256"], b"")
        return _capture_from(meta, body)

    def __setitem__(self, digest: str, capture: Capture) -> None:
        if capture.body:
            self.bodies[capture.response.body_sha256] = capture.body
        self.metadata[digest] = capture.to_json().encode("utf-8")

    def __delitem__(self, digest: str) -> None:
        del self.metadata[digest]

    def __iter__(self) -> Iterator[str]:
        return iter(self.metadata)

    def __len__(self) -> int:
        return len(self.metadata)

    def add(self, capture: Capture) -> str:
        """Store a capture under its own request digest; return the digest."""
        digest = request_digest(capture.request)
        self[digest] = capture
        return digest


def capture_store(rootdir: str) -> CaptureStore:
    """A directory-backed store, creating the directories it needs.

    Uses `dol` so the same object works over a local directory, S3, or anything
    else with a `Mapping` face.

    `~` is expanded and both subdirectories are created up front: `dol.Files`
    does neither, so without this the first write fails with a bare `KeyError`
    from the underlying `open()`.

    >>> import tempfile, os
    >>> store = capture_store(os.path.join(tempfile.mkdtemp(), "job"))
    >>> from .capture import Capture, Request
    >>> digest = store.add(Capture.from_body(Request("https://x.com/a"), b"hi"))
    >>> store[digest].body
    b'hi'
    """
    from pathlib import Path

    from dol import Files

    root = Path(rootdir).expanduser()
    metadata_dir = root / "metadata"
    bodies_dir = root / "bodies"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    bodies_dir.mkdir(parents=True, exist_ok=True)

    return CaptureStore(
        metadata=Files(f"{metadata_dir}/"), bodies=Files(f"{bodies_dir}/")
    )


def cached(
    fetcher: Callable[[Request], Capture],
    store: CaptureStore,
    *,
    revalidate: bool = False,
    should_cache: Callable[[Capture], bool] = lambda capture: capture.ok,
) -> Callable[[Request], Capture]:
    """Wrap a fetcher so repeated requests are served from `store`.

    Two modes:

    - **fetch-once** (default) — a hit is returned as-is; a re-run costs nothing.
    - **revalidate** — a hit's validators are sent as `If-None-Match` /
      `If-Modified-Since`, and a `304` reuses the stored body. This is what makes
      incremental crawls cheap without going stale.

    Only successful captures are cached by default: caching a `403` would make an
    unblocked retry impossible without clearing the store.
    """

    def fetch(request: Request) -> Capture:
        digest = request_digest(request)
        hit = store[digest] if digest in store else None

        if hit is not None and not revalidate:
            return hit

        if hit is not None:
            request = Request(
                url=request.url,
                method=request.method,
                headers={**dict(request.headers), **_validators(hit)},
                body=request.body,
            )

        capture = fetcher(request)

        if hit is not None and capture.response.status == 304:
            refreshed = Capture(
                request=hit.request,
                response=hit.response,
                fetched_at=capture.fetched_at,
                elapsed_ms=capture.elapsed_ms,
                fetcher=capture.fetcher,
                policy=capture.policy,
                probe=hit.probe,
                body=hit.body,
            )
            store[digest] = refreshed
            return refreshed

        if should_cache(capture):
            store[digest] = capture
        return capture

    return fetch


def _validators(capture: Capture) -> dict[str, str]:
    """Conditional-request headers derived from a stored capture.

    Prefer `ETag` over `Last-Modified` when both are present: `Last-Modified` has
    one-second granularity and clock-skew problems across load-balanced origins.
    """
    headers: dict[str, str] = {}
    stored = {k.lower(): v for k, v in capture.response.headers.items()}
    if "etag" in stored:
        headers["If-None-Match"] = stored["etag"]
    elif "last-modified" in stored:
        headers["If-Modified-Since"] = stored["last-modified"]
    return headers

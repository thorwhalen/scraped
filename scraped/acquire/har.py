"""Internal-API discovery from a HAR: rung 3 of the payload ladder (scraped#5).

A browser session records a HAR (Phase 2 does this by default). Somewhere in it
is the request the page itself made for the data you want, usually a JSON or
GraphQL call. Finding it buys a stable, paginated, machine-readable endpoint
that outlives several DOM redesigns.

- :func:`har_captures` reads a HAR (a ``.har`` file, Playwright's ``.har.zip``
  with bodies stored beside it, or an already-parsed mapping) into
  :class:`~scraped.acquire.capture.Capture` records: the same record every
  fetcher produces, so the rest of the package applies unchanged.
- :func:`discover_api` ranks those captures by how many of your target strings
  their response bodies contain, a structured payload before an HTML one, and
  script or style assets last (a bundle that merely mentions a label is not the
  data).
- :func:`replay_request` and :func:`client_code` turn the winner into a request
  you can send again at rung 1 (plain HTTP), as a value or as a runnable snippet.

Everything here reads files; nothing touches the network.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .capture import Capture, Request, Response

__all__ = [
    "har_captures",
    "ApiCandidate",
    "discover_api",
    "replay_request",
    "client_code",
    "DFLT_DROP_HEADERS",
    "CREDENTIAL_HEADER_RE",
]

#: Request headers a replay should not send because the transport sets them.
#: HTTP/2 pseudo-headers (``:authority``) are always dropped too.
DFLT_DROP_HEADERS = frozenset(
    {"host", "content-length", "connection", "accept-encoding"}
)

#: A header whose name matches this carries the recorded session's credentials,
#: and a replay drops it unless ``keep_credentials``.
CREDENTIAL_HEADER_RE = re.compile(
    r"cookie|auth|token|api[-_]?key|secret|session|csrf|xsrf|signature", re.IGNORECASE
)

#: Payload kinds, best first.
PAYLOAD_KINDS = ("structured", "text", "document", "asset")

#: Content-type hints per kind, in the order they are tried: an SVG is an image
#: before it is XML, and XHTML a document before it is XML.
_CONTENT_TYPE_HINTS = (
    (
        "asset",
        ("javascript", "ecmascript", "text/css", "image/", "font/", "audio/", "video/"),
    ),
    ("document", ("text/html", "xhtml")),
    ("structured", ("json", "graphql", "+xml", "/xml", "x-ndjson")),
    ("text", ("text/plain", "text/csv")),
)

_log = logging.getLogger(__name__)


def _headers(pairs: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """HAR ``[{name, value}]`` as a dict; a repeated name keeps its values comma-joined."""
    out: dict[str, str] = {}
    for pair in pairs or ():
        name, value = str(pair.get("name", "")), str(pair.get("value", ""))
        if name:
            out[name] = f"{out[name]}, {value}" if name in out else value
    return out


def _body(
    content: Mapping[str, Any], attachments: Mapping[str, bytes], url: str
) -> bytes:
    """A response body from a HAR ``content``: inline text (maybe base64) or a zip attachment."""
    if "_file" in content:
        if content["_file"] in attachments:
            return attachments[content["_file"]]
        _log.warning(
            "HAR entry %s: attached body %r is not in the archive",
            url,
            content["_file"],
        )
    text = content.get("text")
    if text is None:
        return b""
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text)
        except (binascii.Error, ValueError) as error:
            _log.warning("HAR entry %s: undecodable base64 body (%s)", url, error)
            return b""
    return str(text).encode("utf-8")


def _post_body(post: Mapping[str, Any]) -> bytes | None:
    """A request body from HAR ``postData``: its text, else its form ``params`` url-encoded."""
    if post.get("text") is not None:
        return str(post["text"]).encode("utf-8")
    params = post.get("params")
    if not params:
        return None
    if "multipart" in str(post.get("mimeType", "")).lower():
        _log.warning(
            "multipart postData recorded as params only: its body is not rebuilt"
        )
        return None
    return urlencode([(p.get("name", ""), p.get("value", "")) for p in params]).encode(
        "utf-8"
    )


def _load(
    source: str | Path | Mapping[str, Any],
) -> tuple[Mapping[str, Any], dict[str, bytes]]:
    if isinstance(source, Mapping):
        return source, {}
    path = Path(source).expanduser()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            har_name = next((n for n in names if n.endswith(".har")), None)
            if har_name is None:
                raise ValueError(f"{path} is a zip without a .har file in it")
            har = json.loads(archive.read(har_name))
            attachments = {n: archive.read(n) for n in names if n != har_name}
        return har, attachments
    return json.loads(path.read_text(encoding="utf-8-sig")), {}


def har_captures(source: str | Path | Mapping[str, Any]) -> Iterator[Capture]:
    """The entries of a HAR as :class:`Capture` records, in recorded order.

    >>> har = {"log": {"entries": [{
    ...     "request": {"method": "GET", "url": "https://example.com/api/items?page=1",
    ...                 "headers": [{"name": "Accept", "value": "application/json"}]},
    ...     "response": {"status": 200, "headers": [],
    ...                  "content": {"mimeType": "application/json", "text": '{"items": []}'}}}]}}
    >>> [c.request.url for c in har_captures(har)]
    ['https://example.com/api/items?page=1']
    """
    har, attachments = _load(source)
    for entry in (har.get("log") or {}).get("entries") or ():
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        content = resp.get("content") or {}
        headers = _headers(resp.get("headers"))
        if content.get("mimeType") and not any(
            k.lower() == "content-type" for k in headers
        ):
            headers["Content-Type"] = content["mimeType"]
        request = Request(
            url=str(req.get("url", "")),
            method=str(req.get("method", "GET")).upper(),
            headers=_headers(req.get("headers")),
            body=_post_body(req.get("postData") or {}),
        )
        yield Capture.from_body(
            request,
            _body(content, attachments, request.url),
            response=Response(
                status=int(resp.get("status") or 0),
                final_url=request.url,
                headers=headers,
            ),
        )


def _payload_kind(capture: Capture) -> str:
    """What a response carries, by its content type; sniffed when that says nothing useful."""
    content_type = capture.response.content_type
    for kind, hints in _CONTENT_TYPE_HINTS:
        if any(hint in content_type for hint in hints):
            return kind
    if capture.response.is_textual:
        return "text"
    # No content type, or a generic one such as application/octet-stream.
    try:
        json.loads(capture.body)
        return "structured"
    except (ValueError, UnicodeDecodeError):
        pass
    try:
        capture.body.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "asset"


def _searchable(capture: Capture) -> str:
    """The body as text to search: a JSON body re-serialised so ``\\u00e9`` reads as ``é``."""
    text = capture.text()
    try:
        return json.dumps(json.loads(text), ensure_ascii=False)
    except ValueError:
        return text


@dataclass(frozen=True)
class ApiCandidate:
    """A recorded request whose response contains some of the target strings."""

    capture: Capture
    found: tuple[str, ...]
    occurrences: int
    payload: str  # "structured", "text", "document" or "asset"

    @property
    def request(self) -> Request:
        return self.capture.request


def discover_api(
    source: str | Path | Mapping[str, Any] | Iterable[Capture],
    targets: Iterable[str],
    *,
    min_found: int = 1,
    include_assets: bool = False,
    case_sensitive: bool = False,
) -> list[ApiCandidate]:
    """Recorded requests ranked by how well their responses carry ``targets``.

    ``targets`` are strings you can see on the page (a title, a price, a name).
    Ranked by the number of distinct targets found, then payload kind
    (structured before text before HTML), then total occurrences, then the
    smallest body. Failed responses (status 400 and up, or no status) are left
    out, and so are redirects (status 3xx: their bodies are not the data) and
    script, style, image and font assets unless ``include_assets``. A response
    without a useful content type is sniffed (JSON, else text). Targets equal
    after case folding count once.

    >>> har = {"log": {"entries": [
    ...   {"request": {"method": "GET", "url": "https://example.com/item/7"},
    ...    "response": {"status": 200, "content": {"mimeType": "text/html",
    ...                 "text": "<h1>Blue Kettle</h1><p>12.50</p>"}}},
    ...   {"request": {"method": "GET", "url": "https://example.com/api/item/7"},
    ...    "response": {"status": 200, "content": {"mimeType": "application/json",
    ...                 "text": '{"name": "Blue Kettle", "price": "12.50"}'}}}]}}
    >>> [(c.request.url, c.payload) for c in discover_api(har, ["blue kettle", "12.50"])]
    [('https://example.com/api/item/7', 'structured'), ('https://example.com/item/7', 'document')]
    """
    fold = (lambda s: s) if case_sensitive else str.casefold
    needles = list({fold(t): t for t in reversed([t for t in targets if t])}.items())[
        ::-1
    ]
    if not needles:
        raise ValueError("discover_api needs at least one non-empty target string")
    captures = (
        source if not isinstance(source, (str, Path, Mapping)) else har_captures(source)
    )
    found: list[ApiCandidate] = []
    for capture in captures:
        if not 200 <= capture.response.status < 300:
            continue
        payload = _payload_kind(capture)
        if payload == "asset" and not include_assets:
            continue
        haystack = fold(_searchable(capture))
        hits = {t: haystack.count(n) for n, t in needles if n in haystack}
        if len(hits) >= min_found:
            found.append(
                ApiCandidate(capture, tuple(hits), sum(hits.values()), payload)
            )
    order = {kind: i for i, kind in enumerate(PAYLOAD_KINDS)}
    found.sort(
        key=lambda c: (
            -len(c.found),
            order[c.payload],
            -c.occurrences,
            c.capture.response.body_len,
        )
    )
    return found


def replay_request(
    candidate: ApiCandidate | Capture | Request,
    *,
    drop_headers: Iterable[str] = DFLT_DROP_HEADERS,
    keep_credentials: bool = False,
) -> Request:
    """The recorded request, ready to send again at rung 1, minus ``drop_headers``.

    Headers that look like credentials (any name matching
    :data:`CREDENTIAL_HEADER_RE`: cookies, authorization, tokens, API keys,
    CSRF) are dropped unless ``keep_credentials``: the recording's session is
    not yours to reuse silently. Credentials inside the URL are not touched.
    """
    if isinstance(candidate, ApiCandidate):
        candidate = candidate.request
    if isinstance(candidate, Capture):
        candidate = candidate.request
    drop = {h.lower() for h in drop_headers}
    headers = {
        k: v
        for k, v in candidate.headers.items()
        if k.lower() not in drop
        and not k.startswith(":")
        and (keep_credentials or not CREDENTIAL_HEADER_RE.search(k))
    }
    return Request(
        candidate.url, method=candidate.method, headers=headers, body=candidate.body
    )


def client_code(
    candidate: ApiCandidate | Capture | Request,
    *,
    drop_headers: Iterable[str] = DFLT_DROP_HEADERS,
    keep_credentials: bool = False,
) -> str:
    """A runnable Python snippet that sends the recorded request again through :func:`scraped.acquire.fetch`.

    >>> code = client_code(Request("https://example.com/api/items", headers={"Cookie": "s=1"}))
    >>> print(code)
    from scraped.acquire import fetch
    <BLANKLINE>
    capture = fetch(
        'https://example.com/api/items',
        method='GET',
        headers={},
        body=None,
    )
    print(capture.response.status, capture.text()[:500])
    """
    request = replay_request(
        candidate, drop_headers=drop_headers, keep_credentials=keep_credentials
    )
    return "\n".join(
        [
            "from scraped.acquire import fetch",
            "",
            "capture = fetch(",
            f"    {request.url!r},",
            f"    method={request.method!r},",
            f"    headers={dict(request.headers)!r},",
            f"    body={request.body!r},",
            ")",
            "print(capture.response.status, capture.text()[:500])",
        ]
    )

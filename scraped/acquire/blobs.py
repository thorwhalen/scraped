"""Find the structured data a page already contains, without parsing its DOM.

Server-rendered sites routinely ship their own object graph inside the HTML —
`__NEXT_DATA__`, `__APOLLO_STATE__`, JSON-LD, and friends. Reading that blob is
strictly better than scraping the DOM: it is the data rather than a *rendering*
of the data, it is complete even when the DOM lazily renders only what is
visible, and it survives CSS and markup redesigns.

This module is the seam to the extraction layer. It does not extract anything; it
reports *where the structured data is* and how big it is, and leaves the choosing
to the caller.

>>> html = '<html><script id="__NEXT_DATA__" type="application/json">{"a":1}</script></html>'
>>> blobs = scan_state_blobs(html)
>>> [(b.kind, b.data) for b in blobs]
[('next_data', {'a': 1})]

Lazily-rendered listings are the motivating case: a DOM scrape of a paginated
site returned 35/32/29 items on pages that each held 35, while the state blob
held all of them on every page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

__all__ = [
    "StateBlob",
    "scan_state_blobs",
    "state_blob_markers",
    "iter_script_texts",
    "GLOBAL_ASSIGNMENT_KINDS",
    "SCRIPT_ID_KINDS",
]

# `window.X = ...` / `self.X = ...` globals, by the framework that writes them.
GLOBAL_ASSIGNMENT_KINDS = {
    "__NUXT__": "nuxt",
    "__APOLLO_STATE__": "apollo",
    "__PRELOADED_STATE__": "redux",
    "__INITIAL_STATE__": "redux",
    "__remixContext": "remix",
    "__STATIC_ROUTER_HYDRATION_DATA__": "react_router",
}

# `<script id="...">` blobs, by the framework that writes them.
SCRIPT_ID_KINDS = {
    "__NEXT_DATA__": "next_data",
    "__NUXT_DATA__": "nuxt_data",
    "ng-state": "angular",
    "__remixContext": "remix",
}

# Below this, a bare JSON-looking script is more likely config than payload.
DFLT_MIN_GENERIC_SIZE = 2048

# A commented-out script is stale markup, not page state. Stripping comments
# first stops a leftover blob from being reported as the real one.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Values may legally be unquoted (`<script type=application/json id=x>`),
# and a real page will do it.
_ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>=`]+))""")
_FLIGHT_RE = re.compile(r"(?:self|window)\.__next_f\s*\.\s*push\s*\(")
_JSON_TYPES = ("application/json", "application/ld+json")

_decoder = json.JSONDecoder()


@dataclass(frozen=True)
class StateBlob:
    """One embedded structured-data payload found in an HTML document."""

    kind: str
    marker: str
    raw: str
    size: int
    element_id: str | None = None
    data: Any | None = None

    @property
    def parsed(self) -> bool:
        """Whether the payload was decodable as JSON.

        `False` with a non-empty `raw` means the payload needs a format-specific
        parser — React Flight chunks and Nuxt 2 function expressions are the
        common cases.
        """
        return self.data is not None


def iter_script_texts(html: str) -> Iterator[tuple[dict[str, str], str]]:
    """Yield `(attributes, body)` for every `<script>` element.

    A regex rather than a DOM parse, deliberately: this must work on truncated,
    malformed, and challenge-page HTML where a parser may bail.

    >>> list(iter_script_texts('<script type="x" id="y">B</script>'))
    [({'type': 'x', 'id': 'y'}, 'B')]
    """
    for match in _SCRIPT_RE.finditer(_COMMENT_RE.sub("", html)):
        attrs = {
            key.lower(): (double or single or bare)
            for key, double, single, bare in _ATTR_RE.findall(match.group("attrs"))
        }
        yield attrs, match.group("body")


def _json_value_at(text: str, start: int) -> tuple[Any, int] | None:
    """Decode the JSON value that begins **immediately** at `start`, if any.

    Uses the stdlib decoder's `raw_decode` so that braces inside strings and
    escapes are handled correctly — a hand-rolled brace counter gets this wrong.

    The "immediately" is load-bearing. An earlier version searched *forward* for
    the next `{` or `[`, which meant an assignment whose right-hand side was not
    JSON would happily decode some unrelated object later in the script and
    report it as the framework's state — with `parsed=True` and a `raw` that
    disagreed with `data`. Silent wrong data is the worst outcome this package
    can produce, so: skip whitespace, and if the next character is not a JSON
    container, report nothing.

    >>> _json_value_at('window.X = [1,2]; window.Y = {"z":9}', len("window.X = "))
    ([1, 2], 16)
    >>> _json_value_at('window.X = "no"; window.Y = {"z":9}', len("window.X = ")) is None
    True
    """
    index = start
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    if index >= len(text) or text[index] not in "{[":
        return None
    try:
        return _decoder.raw_decode(text, index)
    except ValueError:
        return None


def _blob_from_script_id(attrs: dict[str, str], body: str) -> StateBlob | None:
    element_id = attrs.get("id", "")
    kind = SCRIPT_ID_KINDS.get(element_id)
    if kind is None:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return StateBlob(
        kind=kind,
        marker=element_id,
        raw=body,
        size=len(body),
        element_id=element_id,
        data=data,
    )


def _blob_from_json_type(attrs: dict[str, str], body: str) -> StateBlob | None:
    script_type = attrs.get("type", "").lower()
    if script_type not in _JSON_TYPES:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    kind = "ld_json" if script_type == "application/ld+json" else "json_script"
    return StateBlob(
        kind=kind,
        marker=script_type,
        raw=body,
        size=len(body),
        element_id=attrs.get("id"),
        data=data,
    )


def _blobs_from_globals(body: str) -> Iterator[StateBlob]:
    for name, kind in GLOBAL_ASSIGNMENT_KINDS.items():
        marker = re.search(r"(?:window|self)\s*\.\s*" + re.escape(name) + r"\s*=", body)
        if marker is None:
            continue
        decoded = _json_value_at(body, marker.end())
        if decoded is None:
            # A function expression or other non-JSON RHS (Nuxt 2 does this).
            # Report it anyway — knowing it is there is the useful part.
            yield StateBlob(
                kind=kind,
                marker=name,
                raw=body[marker.end() :],
                size=len(body),
            )
            continue
        data, end = decoded
        raw = body[marker.end() : end]
        yield StateBlob(
            kind=kind, marker=name, raw=raw.strip(), size=len(raw), data=data
        )


def _flight_blob(html: str) -> StateBlob | None:
    """Collect React Server Component streaming chunks, if present.

    Next.js App Router streams data as `self.__next_f.push([...])` calls carrying
    React Flight wire format — which is *not* JSON. We concatenate the chunks and
    report them unparsed; decoding Flight needs a dedicated parser.
    """
    html = _COMMENT_RE.sub("", html)
    chunks: list[str] = []
    for match in _FLIGHT_RE.finditer(html):
        decoded = _json_value_at(html, match.end())
        if decoded is None:
            continue
        payload = decoded[0]
        if (
            isinstance(payload, list)
            and len(payload) > 1
            and isinstance(payload[1], str)
        ):
            chunks.append(payload[1])
    if not chunks:
        return None
    raw = "".join(chunks)
    return StateBlob(
        kind="rsc_flight", marker="__next_f", raw=raw, size=len(raw), data=None
    )


def scan_state_blobs(
    html: str,
    *,
    min_generic_size: int = DFLT_MIN_GENERIC_SIZE,
    include_generic: bool = True,
) -> list[StateBlob]:
    """Find every embedded structured-data payload, largest first.

    Recognizes framework-specific markers (Next.js, Nuxt, Apollo, Redux, Remix,
    Angular TransferState), standardized JSON-LD, and — when `include_generic` —
    any sufficiently large bare JSON script.

    >>> html = '''
    ... <script type="application/ld+json">{"@type":"Product"}</script>
    ... <script>window.__APOLLO_STATE__ = {"Product:1":{"name":"x"}};</script>
    ... '''
    >>> sorted(b.kind for b in scan_state_blobs(html))
    ['apollo', 'ld_json']

    JSON-LD is worth trying first when present: it is standardized, so it is the
    most stable payload on the web.

    >>> blob = next(b for b in scan_state_blobs(html) if b.kind == 'ld_json')
    >>> blob.data['@type']
    'Product'
    """
    found: list[StateBlob] = []

    for attrs, body in iter_script_texts(html):
        blob = _blob_from_script_id(attrs, body) or _blob_from_json_type(attrs, body)
        if blob is not None:
            found.append(blob)
            continue
        found.extend(_blobs_from_globals(body))
        if include_generic and _looks_like_bare_json(body, min_generic_size):
            try:
                data = json.loads(body)
            except ValueError:
                continue
            found.append(
                StateBlob(
                    kind="json_script",
                    marker="bare-json",
                    raw=body,
                    size=len(body),
                    element_id=attrs.get("id"),
                    data=data,
                )
            )

    flight = _flight_blob(html)
    if flight is not None:
        found.append(flight)

    return sorted(found, key=lambda blob: blob.size, reverse=True)


def _looks_like_bare_json(body: str, min_size: int) -> bool:
    stripped = body.strip()
    return len(stripped) >= min_size and stripped[:1] in ("{", "[")


def state_blob_markers(html: str) -> Sequence[str]:
    """The marker names present, cheapest possible summary for capture metadata.

    >>> state_blob_markers('<script id="__NEXT_DATA__" type="application/json">{}</script>')
    ['__NEXT_DATA__']
    """
    return [blob.marker for blob in scan_state_blobs(html, include_generic=False)]

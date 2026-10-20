"""What a discovered API call is: its GraphQL operation and its pagination (scraped#5, part 2).

Once :func:`~scraped.acquire.har.discover_api` has found the request, two
questions decide whether it can be replayed for the whole dataset:

- **GraphQL persisted queries.** Clients using Apollo *Automatic Persisted
  Queries* send only a hash (``extensions.persistedQuery.sha256Hash``). A server
  that has not seen the hash answers ``PersistedQueryNotFound``, and the client
  retries with the full query text, so a HAR that caught the first, uncached
  request, or that miss-and-retry, holds the text. :func:`persisted_queries`
  recovers ``{hash: query}`` from a HAR's captures, keeping only text whose
  sha256 matches the hash.
- **Pagination.** :func:`pagination_shape` reads one request and its response for
  the paging style (offset/limit, page number, or cursor), the page size and any
  total. Deep paging is often capped (typically around page 100 to 200 at 50 per
  page); when the total implies more pages than ``deep_page_cap``, the shape says
  so, because then the only complete strategy is partitioning the query space
  (by date, category, price range) into slices that each stay under the cap.

Everything here reads recorded requests; nothing touches the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .capture import Capture, Request

__all__ = [
    "GraphQLOperation",
    "graphql_operations",
    "persisted_queries",
    "persisted_query_misses",
    "Pagination",
    "pagination_shape",
    "DFLT_DEEP_PAGE_CAP",
]

#: Pages beyond which a deep-paging cap is likely; see :func:`pagination_shape`.
DFLT_DEEP_PAGE_CAP = 100

#: Request parameter names, by paging style, lowercased.
OFFSET_PARAMS = ("offset", "skip", "start", "from")
LIMIT_PARAMS = (
    "limit",
    "take",
    "rows",
    "size",
    "count",
    "per_page",
    "perpage",
    "pagesize",
    "page_size",
    "first",
)
PAGE_PARAMS = ("page", "pagenumber", "page_number", "pageindex")
CURSOR_PARAMS = (
    "after",
    "cursor",
    "pagetoken",
    "page_token",
    "next_token",
    "nexttoken",
    "continuation",
    "starting_after",
)

#: Response field names that carry the next cursor or the total, lowercased.
NEXT_CURSOR_FIELDS = (
    "endcursor",
    "nextcursor",
    "next_cursor",
    "nextpagetoken",
    "next_page_token",
    "next_token",
    "continuation",
)
#: In priority order: ``count`` is left out, as it is as often the size of the page returned.
TOTAL_FIELDS = (
    "totalcount",
    "total_count",
    "totalresults",
    "total_results",
    "totalhits",
    "nbhits",
    "total",
)
HAS_NEXT_FIELDS = ("hasnextpage", "has_next_page", "has_more", "hasmore")

#: The text of a GraphQL document starts with an operation keyword or a selection set.
_GRAPHQL_TEXT = re.compile(
    r"^\s*(query|mutation|subscription|fragment)\b|^\s*\{", re.IGNORECASE
)
#: A date or time: a value like this in ``after``/``from`` is a filter, not a position.
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


# --------------------------------------------------------------------------- #
# GraphQL operations and persisted queries
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GraphQLOperation:
    """One GraphQL operation as recorded: its name, text (if sent), variables, persisted hash."""

    operation_name: str | None
    query: str | None
    variables: Mapping[str, Any] = field(default_factory=dict)
    persisted_hash: str | None = None


def _json(text: str | bytes | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


def _operation(payload: Mapping[str, Any]) -> GraphQLOperation | None:
    if not isinstance(payload, Mapping) or not ({"query", "extensions"} & set(payload)):
        return None
    extensions = payload.get("extensions") or {}
    if isinstance(extensions, str):
        extensions = _json(extensions) or {}
    variables = payload.get("variables") or {}
    if isinstance(variables, str):
        variables = _json(variables) or {}
    persisted = (
        (extensions.get("persistedQuery") or {})
        if isinstance(extensions, Mapping)
        else {}
    )
    persisted_hash = (
        persisted.get("sha256Hash") if isinstance(persisted, Mapping) else None
    )
    query = payload.get("query")
    if not (isinstance(query, str) and _GRAPHQL_TEXT.match(query)):
        query = None  # a search API's ``query=shoes`` is not a GraphQL document
    if query is None and not isinstance(persisted_hash, str):
        return None
    return GraphQLOperation(
        operation_name=payload.get("operationName"),
        query=query if isinstance(query, str) and query.strip() else None,
        variables=variables if isinstance(variables, Mapping) else {},
        persisted_hash=persisted_hash.lower()
        if isinstance(persisted_hash, str)
        else None,
    )


def graphql_operations(request: Request) -> list[GraphQLOperation]:
    """The GraphQL operations a request carries: a POST JSON body (one, or a batch), or GET parameters.

    >>> body = b'{"operationName": "Items", "query": "query Items { items { id } }", "variables": {"first": 50}}'
    >>> [op.operation_name for op in graphql_operations(Request("https://example.com/graphql", "POST", body=body))]
    ['Items']
    """
    payload = _json(request.body)
    payloads = payload if isinstance(payload, list) else [payload]
    ops = [op for p in payloads if isinstance(p, Mapping) and (op := _operation(p))]
    if not ops:  # a GET, or a POST with the hash in the URL
        op = _operation(dict(parse_qsl(urlsplit(request.url).query)))
        ops = [op] if op else []
    return ops


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def persisted_queries(captures: Iterable[Capture]) -> dict[str, str]:
    """``{sha256Hash: query text}`` recovered from recorded requests that sent both.

    Apollo's hash is the sha256 of the exact query text, so a pair is kept only
    when the text hashes to it (the text as sent; a client that reformats the
    query would not match, and is left out rather than guessed).

    >>> q = "query Items { items { id } }"
    >>> h = hashlib.sha256(q.encode()).hexdigest()
    >>> body = json.dumps({"query": q, "extensions": {"persistedQuery": {"version": 1, "sha256Hash": h}}})
    >>> c = Capture.from_body(Request("https://example.com/graphql", "POST", body=body.encode()), b"{}")
    >>> persisted_queries([c]) == {h: q}
    True
    """
    found: dict[str, str] = {}
    for capture in captures:
        for op in graphql_operations(capture.request):
            if (
                op.query
                and op.persisted_hash
                and _sha256(op.query) == op.persisted_hash
            ):
                found[op.persisted_hash] = op.query
    return found


def persisted_query_misses(captures: Iterable[Capture]) -> list[Capture]:
    """Recorded requests the server answered with ``PersistedQueryNotFound``: the client's next request for that hash carries the text."""
    misses = []
    for capture in captures:
        payload = _json(capture.body)
        errors = []
        for part in payload if isinstance(payload, list) else [payload]:
            if isinstance(part, Mapping) and isinstance(part.get("errors"), list):
                errors += part["errors"]

        def is_miss(error: Any) -> bool:
            if not isinstance(error, Mapping):
                return False
            extensions = error.get("extensions")
            code = extensions.get("code") if isinstance(extensions, Mapping) else None
            return (
                error.get("message") == "PersistedQueryNotFound"
                or code == "PERSISTED_QUERY_NOT_FOUND"
            )

        if any(is_miss(e) for e in errors):
            misses.append(capture)
    return misses


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pagination:
    """How a request pages through its results, as far as one request and response show.

    ``kind`` is ``"offset"``, ``"page"``, ``"cursor"`` or ``None`` (no paging
    seen). ``params`` names the request parameters that page (a ``variables.``
    prefix for GraphQL variables). ``deep_paging_capped`` is true when ``total``
    needs more than the cap's pages at ``page_size``: then partition the query
    space instead of paging deeper.
    """

    kind: str | None
    params: Mapping[str, str] = field(default_factory=dict)
    page_size: int | None = None
    total: int | None = None
    next_cursor: str | None = None
    pages_needed: int | None = None
    deep_paging_capped: bool = False


def _request_params(request: Request) -> dict[str, Any]:
    """Query-string parameters, JSON body fields and GraphQL variables (prefixed), flat."""
    params: dict[str, Any] = dict(parse_qsl(urlsplit(request.url).query))
    body = _json(request.body)
    if isinstance(body, Mapping):
        params.update(
            {k: v for k, v in body.items() if not isinstance(v, (Mapping, list))}
        )

    def flatten(prefix: str, node: Mapping[str, Any], depth: int = 4) -> None:
        for k, v in node.items():
            if isinstance(v, Mapping) and depth > 0:
                flatten(f"{prefix}.{k}", v, depth - 1)
            elif not isinstance(v, list):
                params[f"{prefix}.{k}"] = v

    for op in graphql_operations(request):
        flatten("variables", op.variables)
    return params


def _find(
    params: Mapping[str, Any], names: tuple[str, ...], *, numeric: bool = False
) -> str | None:
    """The first parameter named one of ``names``, skipping date-like values (and non-numbers if ``numeric``)."""
    for key, value in params.items():
        if key.rsplit(".", 1)[-1].lower() not in names:
            continue
        if value is None and not numeric:
            return key  # a first page sends a null cursor: the parameter is still the cursor
        if isinstance(value, str) and _DATE_LIKE.match(value):
            continue  # ``after=2020-01-01`` filters by date
        if numeric and _int(value) is None:
            continue  # ``size=M`` is a clothing size
        return key
    return None


def _walk(node: Any, names: tuple[str, ...], *, depth: int = 6) -> Any:
    """The value under the highest-priority key in ``names`` at the shallowest level that has one.

    Breadth first through mappings only, ``depth`` levels down: a total or a cursor
    sits in the response's envelope, never inside a list of items.
    """
    level = [node]
    for _ in range(depth):
        nested, here = [], {}
        for item in level:
            if isinstance(item, Mapping):
                for key, value in item.items():
                    name = str(key).lower()
                    if name in names and not isinstance(value, (Mapping, list)):
                        here.setdefault(name, value)
                    elif isinstance(value, Mapping):
                        nested.append(value)
        for name in names:
            if name in here:
                return here[name]
        level = nested
    return None


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 and not isinstance(value, bool) else None


def pagination_shape(
    capture: Capture, *, deep_page_cap: int = DFLT_DEEP_PAGE_CAP
) -> Pagination:
    """The paging style of a recorded request, its page size, and whether deep paging would hit a cap.

    >>> req = Request("https://example.com/api/items?offset=0&limit=50")
    >>> shape = pagination_shape(Capture.from_body(req, b'{"total": 12000, "items": []}'))
    >>> shape.kind, shape.page_size, shape.pages_needed, shape.deep_paging_capped
    ('offset', 50, 240, True)
    """
    params = _request_params(capture.request)
    body = _json(capture.body)
    total = _int(_walk(body, TOTAL_FIELDS)) if body is not None else None
    next_cursor = _walk(body, NEXT_CURSOR_FIELDS) if body is not None else None
    if body is not None and _walk(body, HAS_NEXT_FIELDS) is False:
        next_cursor = None  # the last page still names its end cursor
    size_key = _find(params, LIMIT_PARAMS, numeric=True)
    page_size = _int(params[size_key]) if size_key else None
    named = {"size": size_key} if size_key else {}
    if cursor_key := _find(params, CURSOR_PARAMS):
        kind, named = "cursor", {**named, "cursor": cursor_key}
    elif offset_key := _find(params, OFFSET_PARAMS, numeric=True):
        kind, named = "offset", {**named, "offset": offset_key}
    elif page_key := _find(params, PAGE_PARAMS, numeric=True):
        kind, named = "page", {**named, "page": page_key}
    elif next_cursor is not None:
        kind = "cursor"  # the first page sends no cursor; the response hands one back
    elif size_key:
        kind = (
            "offset"  # a size with no position yet: the first page of an offset scheme
        )
    else:
        kind = None
    pages = -(-total // page_size) if total is not None and page_size else None
    return Pagination(
        kind=kind,
        params=named,
        page_size=page_size,
        total=total,
        next_cursor=None if next_cursor is None else str(next_cursor),
        pages_needed=pages,
        deep_paging_capped=pages is not None and pages > deep_page_cap,
    )

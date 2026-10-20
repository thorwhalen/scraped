"""GraphQL persisted queries and pagination shape of recorded API calls (scraped#5, part 2). Offline."""

import hashlib
import json

from scraped.acquire import Capture, Request
from scraped.acquire.api_shape import (
    graphql_operations,
    pagination_shape,
    persisted_queries,
    persisted_query_misses,
)

QUERY = "query Listings($first: Int, $after: String) { listings(first: $first, after: $after) { pageInfo { endCursor hasNextPage } totalCount } }"
HASH = hashlib.sha256(QUERY.encode()).hexdigest()
GQL_URL = "https://example.com/graphql"


def _post(payload, response=b"{}"):
    return Capture.from_body(Request(GQL_URL, "POST", body=json.dumps(payload).encode()), response)


def _apq(query=None, *, first=50, after=None):
    payload = {
        "operationName": "Listings",
        "variables": {"first": first, "after": after},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH}},
    }
    if query is not None:
        payload["query"] = query
    return payload


def test_the_miss_and_retry_yields_the_query_text():
    miss = _post(_apq(), json.dumps({"errors": [{"message": "PersistedQueryNotFound"}]}).encode())
    retry = _post(_apq(QUERY))
    assert persisted_query_misses([miss, retry]) == [miss]
    assert persisted_queries([miss, retry]) == {HASH: QUERY}


def test_text_that_does_not_hash_to_the_claimed_hash_is_not_kept():
    assert persisted_queries([_post(_apq(QUERY + " "))]) == {}


def test_get_requests_and_batches_are_read():
    from urllib.parse import urlencode

    params = {"operationName": "Listings", "variables": json.dumps({"first": 10}),
              "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": HASH}})}
    get = Request(f"{GQL_URL}?{urlencode(params)}")
    [op] = graphql_operations(get)
    assert op.persisted_hash == HASH and op.query is None and op.variables == {"first": 10}
    batch = Request(GQL_URL, "POST", body=json.dumps([_apq(QUERY), _apq(QUERY)]).encode())
    assert len(graphql_operations(batch)) == 2
    assert graphql_operations(Request("https://example.com/api", "POST", body=b'{"a": 1}')) == []


def test_a_relay_cursor_on_graphql_variables():
    body = {"data": {"listings": {"pageInfo": {"endCursor": "Y3Vyc29yOjUw", "hasNextPage": True}, "totalCount": 9000}}}
    shape = pagination_shape(_post(_apq(QUERY), json.dumps(body).encode()))
    assert shape.kind == "cursor"
    assert shape.params == {"size": "variables.first", "cursor": "variables.after"}
    assert (shape.page_size, shape.total, shape.next_cursor) == (50, 9000, "Y3Vyc29yOjUw")
    assert shape.pages_needed == 180 and shape.deep_paging_capped


def test_page_numbers_under_the_cap():
    capture = Capture.from_body(
        Request("https://example.com/api/search?q=kettle&page=2&per_page=50"),
        b'{"results": [{"id": 1}], "total_results": 420}',
    )
    shape = pagination_shape(capture)
    assert (shape.kind, shape.page_size, shape.pages_needed, shape.deep_paging_capped) == ("page", 50, 9, False)


def test_no_paging_seen():
    shape = pagination_shape(Capture.from_body(Request("https://example.com/api/item/7"), b'{"name": "x"}'))
    assert shape.kind is None and shape.pages_needed is None


def test_a_custom_cap():
    capture = Capture.from_body(Request("https://example.com/api?offset=0&limit=50"), b'{"total": 600}')
    assert not pagination_shape(capture).deep_paging_capped
    assert pagination_shape(capture, deep_page_cap=10).deep_paging_capped

---
name: scraped-api-discovery
description: >-
  Find the internal JSON API behind a web page from a recorded HAR with
  scraped.acquire: discover_api ranks recorded requests by how many on-page
  strings their responses carry, replay_request strips credentials for an HTTP
  replay, client_code emits a runnable snippet, and graphql_operations /
  persisted_queries / pagination_shape read GraphQL persisted queries and the
  pagination of a call. Use for "find the API this page calls", "which request
  returns the listings", "replay this XHR", "how does this endpoint paginate".
  Loaded by the scraped-acquire router.
license: MIT
metadata:
  audience: consumers
---

# API discovery (payload rung: internal api)

Start from a HAR (the browser rung records one) and some strings you can see on
the page — a title, a price, a name.

```python
from scraped.acquire import client_code, discover_api, pagination_shape, replay_request

candidates = discover_api(har_path, ["Blue Bicycle", "349"])
best = candidates[0]                     # most targets found, structured first
request = replay_request(best)           # credential-looking headers dropped
print(client_code(best))                 # a snippet that re-sends it via fetch
print(pagination_shape(best.capture))     # cursor / page / offset, if detectable
```

`har_captures(har_path)` yields every recorded exchange as a `Capture` if you need
to look yourself.

## GraphQL

`graphql_operations(request)` names the operations in a request.
`persisted_queries(captures)` maps persisted-query hashes to query text seen in
the HAR; `persisted_query_misses(captures)` lists calls whose hash has no known
query (you cannot replay those with a new query).

## Rules

- Credentials are dropped by default. `keep_credentials=True` is the user's call.
- Validate a replay with one request at the HTTP rung before claiming the API
  works; mark the claim `PARTIAL` until you have.

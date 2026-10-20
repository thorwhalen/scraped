---
name: scraped-http-rung
description: >-
  Fetch pages and JSON over HTTP with scraped.acquire: fetch, default_fetcher
  (impersonating TLS, per-host politeness, jittered retries, robots-aware,
  challenge detection), cached capture stores for free re-runs, Capture
  provenance, and reading embedded state blobs with scan_state_blobs. Use for
  "fetch this page", "the plain request gets 403", "cache the captures", "reuse a
  browser session's cookies over HTTP". Loaded by the scraped-acquire router.
license: MIT
metadata:
  audience: consumers
---

# HTTP rung

```python
from scraped.acquire import (
    Request,
    cached,
    capture_store,
    default_fetcher,
    scan_state_blobs,
)

fetcher = cached(default_fetcher(), capture_store("~/.cache/my-job"))
capture = fetcher(Request(url))  # a second run costs no network at all
blobs = scan_state_blobs(capture.text())
```

Build one fetcher per job and reuse it, so politeness and the cache are shared.
`fetch(url)` is the one-URL shortcut.

## What the default fetcher already does

- Impersonating transport (`CurlCffiTransport`) — a plain client gets 403 from
  origins that fingerprint TLS. `UrllibTransport` is the dependency-free fallback.
- Robots compliance via `default_fetcher()`. `HttpFetcher()` built by hand opts
  out: pass `robots=RobotsPolicy(...)` yourself.
- Per-host politeness and full-jitter retries on transient errors.
- `ChallengeEncountered` is raised, never retried, never saved as data.

## Reading a `Capture`

`capture.text()` (parse JSON from it) and its request/response/provenance records.
Bodies are content-addressed (`body_digest`), so deduplication and change
detection are free.

## Reusing a browser session

`storage_state_headers(path, url_host=host)` turns a saved browser storage state
into cookie headers, so after one browser login you work at HTTP speed.

When HTTP is refused or the data appears only after JavaScript runs, load
`scraped-browser-rung`.

---
name: scraped-browser-rung
description: >-
  Drive a real browser with scraped.acquire's BrowserSession and BrowserFetcher
  (patchright) when plain HTTP is refused or the data only appears after
  JavaScript runs: HAR recording, JSON response capture, saved storage state to
  drop back to HTTP, artifacts written to disk. Use for "the page needs a
  browser", "record a HAR", "log in once and reuse the session", "headful
  browser". Loaded by the scraped-acquire router.
license: MIT
metadata:
  audience: consumers
---

# Browser rung

Requires `pip install 'scraped[browser]' && patchright install chromium`.
`browser_check_requirements()` says what is missing and how to install it.

```python
from scraped.acquire import BrowserFetcher, BrowserSession, Request, scan_state_blobs

with BrowserSession(artifact_dir="~/runs/job") as session:
    capture = BrowserFetcher(session=session)(Request(url))
    session.save_storage_state()          # then work at HTTP speed
    artifacts = session.artifacts()       # har_path and friends, on disk
blobs = scan_state_blobs(capture.text())  # the blob beats the DOM
```

## The rule this rung enforces

**The browser writes to disk; you read the file path.** HAR and download paths are
set at context creation and are not optional. Getting a payload out of an
automated browser through memory is its own failure class
(`misc/docs/browser-result-exfiltration.md`).

## After the browser

1. Read the state blob from the capture rather than the DOM.
2. Hand the HAR to `scraped-api-discovery`: the JSON call behind the page is
   usually replayable over HTTP.
3. Reuse the saved storage state via `storage_state_headers`.

Never solve or bypass an interactive challenge. If a human is required, stop.

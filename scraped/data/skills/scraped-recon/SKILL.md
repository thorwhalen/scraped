---
name: scraped-recon
description: >-
  Reconnoitre a URL before scraping it, with scraped.acquire.probe: robots verdict
  and crawl delay, sitemaps, whether the origin serves JSON, the site generator
  (next.js, nuxt, ...), embedded state blobs, the best payload rung, and which
  bot-protection vendor guards it. Use as the first step of any scraping job, for
  "what will this site give me", "is this site scrapable", "what is it built
  with", "is there a __NEXT_DATA__ blob". Loaded by the scraped-acquire router.
license: MIT
metadata:
  audience: consumers
---

# Recon: `probe` first

```python
from scraped.acquire import probe

profile = probe("https://example.com/listing")
print(profile.summary())
```

`probe` performs at most three requests (robots.txt, a JSON-negotiation probe, the
page) and never raises on a challenge: it reports the obstacle.

## Reading the summary → next move

| Summary says | Move |
|---|---|
| `robots allowed=False` | Stop. Overriding robots is the user's decision, not yours. |
| `serves JSON True` | Payload rung 1: request JSON directly (`scraped-http-rung`). |
| `state blobs next_data(...)` | Read the blob: `scan_state_blobs(capture.text())`. |
| `best payload dom` | Check sitemaps first; parse the DOM only if nothing better exists. |
| `guarded by X (not blocking)` | Keep the default impersonating transport. |
| `guarded by X (blocking)` | Escalate transport: `scraped-browser-rung`. |
| interactive challenge | Terminal. A human is required; stop and report it. |

`robots_sitemaps(robots_txt)` lists sitemap URLs; a sitemap often beats crawling.

Record what recon found as claims in your report (see `scraped-acquirer`).

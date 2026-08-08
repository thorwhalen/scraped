# Acquisition tooling survey

Library landscape for the acquisition layer, with health verdicts. **Verified
2026-08-08** against live PyPI/GitHub APIs. Constraint: free and self-hostable only —
no commercial scraping APIs, proxy networks, or CAPTCHA solvers.

The churn rate here is extreme, which is *why* `scraped.acquire` puts a facade in
front of every one of these. Re-verify before trusting any verdict below.

---

## 1. Chosen stack

Core (light, always installed):

| Library | Role |
|---|---|
| `curl_cffi` | impersonating HTTP client — the default fetcher |
| `protego` | robots.txt policy |
| `dol` | `Mapping`-shaped stores, so local-dir → S3 is a constructor change |

Optional extras: `[browser]` → `patchright` (rungs 4–5); `[dom]` → `selectolax`,
needed only when you genuinely must parse the DOM (rung e).

Optional, per-job: `parsel` (XPath + CSS + JMESPath in one object), `warcio` (real
WARC output), `pywb` (recording proxy, separate venv).

Two libraries an earlier draft of this survey listed as core were dropped once the
code was written, and the reasons generalize:

- **`tenacity`** — the retry policy this package needs is *not* generic. It must
  never retry a challenge, must honor an explicit `Retry-After`, and must apply
  full jitter. That is about twenty lines, and expressing it directly keeps the
  policy readable at the point where correctness matters.
- **`selectolax`** — the script-tag scan that finds state blobs uses a regex
  rather than a DOM parse, deliberately: it must keep working on truncated,
  malformed, and challenge-page HTML, exactly the inputs where a parser bails.
  DOM parsing is rung (e), which the design treats as the fallback, so its
  dependency is an extra rather than core.

Unchanged and untouched: `scrapy`, which continues to back `download_site` /
`markdown_of_site`.

---

## 2. HTTP clients

| Library | Latest | Released | Verdict |
|---|---|---|---|
| **curl_cffi** | 0.16.0 | 2026-08-01 | **Healthy — chosen default** |
| niquests | 3.21.0 | 2026-07-29 | Healthy; credible httpx successor; bus factor 1 |
| primp | 1.3.1 | 2026-05-23 | Healthy; bus factor 1 |
| wreq (ex-`rnet`) | 0.12.1 | 2026-07-11 | Healthy, **renamed and relicensed** |
| requests | 2.34.2 | 2026-05-14 | maintenance mode |
| **httpx** | 0.28.1 | 2024-12-06 | **Frozen — avoid** |
| **tls-client** | 1.0.1 | 2024-02-02 | **Abandoned — do not adopt** |

**`curl_cffi`** is the linchpin: MIT, requests-shaped API, sync + async + WebSocket,
impersonates JA3/TLS, HTTP/2 (Akamai), and since 0.15.0 **HTTP/3** fingerprints.
Version gaps are deliberate — profiles are added only when a browser's fingerprint
actually changes. Two honest caveats for the risk register: it **vendors libcurl**,
so libcurl CVEs become ours; and there is a commercial gate, with weekly-updated
browser profiles behind a hosted product while OSS gets them on release cadence.
There is no `ja4=` parameter by design — faithful ClientHello reproduction yields a
matching JA4 hash anyway; what's missing is only a knob to dial an arbitrary one.

**`httpx` deserves a blunt note.** Not dead, but **no release in 20 months, 14
commits in the last 12, issue tracker disabled, 78 untriaged PRs**. Still the right
*API* to program against, but expect no fixes — and it gives no impersonation, which
is the whole point at rung 1. `niquests` is the credible successor if an evolving
client is wanted: Apache-2.0, HTTP/1.1+2+3, SLSA-signed, zero open issues and PRs,
and browser TLS impersonation via a `utls` extra — i.e. an impersonation path with
no vendored libcurl. Its risks are bus factor 1 and a hard urllib3 fork in the tree.

**`tls-client`**: last release 2024-02-02, 66 open issues, 1 opened / 0 closed in 90
days. Ships **7 prebuilt binary blobs (10–19 MB each) committed to git with no build
script, no checksums, no provenance** — an unverifiable, unsigned, 2.5-year-stale
build of an *actively maintained* Go upstream. Also BSD-4-Clause (GPL-incompatible).

**`rnet` → `wreq` is a live migration trap**: repo renamed, PyPI project moved, and
**GPL-3.0 → Apache-2.0 relicense**. Installing `rnet` today lands on an abandoned
GPL-3.0 line.

---

## 3. Frameworks

| Library | Latest | Released | Verdict |
|---|---|---|---|
| scrapy | 2.17.0 | 2026-07-07 | Healthy; the reference architecture |
| crawlee-python | 1.9.1 | 2026-08-06 | Healthy; **genuinely self-hostable** |
| **crawl4ai** | 0.9.2 | 2026-07-15 | **Risky — rejected** |
| katana | v1.7.0 | 2026-08-05 | Healthy Go CLI, **anti-robots by design** |
| browsertrix-crawler | v1.14.1 | 2026-07-30 | Healthy; AGPL; Docker-only |

**`scrapy`** remains the architecture to copy even where it isn't used — its
middleware / frontier / dupefilter / `JOBDIR` decomposition is the model for §6 of
the architecture doc. Too much machinery (Twisted, project layout, settings system)
for occasional use as a *fetcher*, which is why the new core doesn't build on it.

**`crawlee-python`**: the OSS half is genuinely self-hostable — verified
structurally, not from marketing. No vendor package in any of its 15 extras; the
dependency runs the other way. Default storage is filesystem JSON. **Gotcha:
`respect_robots_txt_file` defaults to `False`.**

**`crawl4ai` rejected** despite being the most-starred project surveyed, on two
grounds. The **license is not plain Apache-2.0** despite the SPDX tag: the LICENSE
appends an attribution requirement reaching *public uses* (credit in an About/Credits
section or CLI help), broader than Apache's NOTICE clause — and the README
contradicts the LICENSE by calling it "recommended". And it pins a **single-release
fork of `litellm` created the day of a PyPI supply-chain compromise**, now four-plus
months with zero security patches under sole-owner namespace while upstream ships
daily. Forking was a defensible emergency; the unmaintained pin is the problem.

**`katana`**: MIT and no cloud coupling, but a default-on update check posts a
persistent machine ID to a vendor endpoint, and it is **architecturally anti-robots**
— it parses `Disallow` directives and converts them into *crawl targets*, with no
robots-obeying mode. Also `pip install katana` installs an unrelated bioinformatics
package.

---

## 4. Browser automation / anti-detect

| Library | Latest | Released | License | Verdict |
|---|---|---|---|---|
| playwright | 1.62.0 | 2026-07-31 | Apache-2.0 | Healthy; **3 open issues**; best-maintained here |
| **patchright** | 1.61.2 | 2026-07-05 | Apache-2.0 | **Chosen** — minimal, drop-in |
| seleniumbase | 4.51.11 | 2026-08-07 | MIT | **Healthiest anti-detect option** |
| camoufox | 0.5.4 | 2026-07-16 | MPL-2.0 | Healthy but **pre-1.0**; governance shifting |
| zendriver | 0.15.5 | 2026-07-15 | **AGPL-3.0** | better-maintained nodriver fork |
| nodriver | 0.50.3 | 2026-05-13 | **AGPL-3.0** | **Slow** — 17 commits/yr |
| scrapling | 0.4.12 | 2026-07-26 | BSD-3 | healthy wrapper, fat pinned deps |
| botasaurus | 4.0.97 | 2026-01-06 | MIT | **Risky / splintered** |
| **undetected-chromedriver** | 3.5.5 | 2024-02-17 | GPL-3.0 | **Abandoned** |
| rebrowser-patches | 1.0.19 | 2025-05-09 | **none** | **Stale**; cite the analysis, don't ship it |

**The detection landscape shifted, and old advice is now actively wrong.** The
decisive technique of the last two years is **CDP attachment detection**:
Puppeteer/Playwright/Selenium-4 issue `Runtime.enable` per frame to get an execution
context ID; with the Runtime domain on, Chrome must serialize console/exception
payloads for the attached client, **and that serialization runs inside the page's own
JS realm**. So a page defines an object with a getter, calls `console.debug(obj)`,
and checks whether the getter fired. A few lines of JS, boolean result, near-zero
false positives.

**Consequently, stealth *plugins* as a category are finished.** `puppeteer-extra-
plugin-stealth` last published 2023-03-01; `playwright-extra` likewise. The Python
`playwright-stealth` fork *is* maintained, but its own README says: *"Don't expect
this to bypass anything but the simplest of bot detection methods."* They patch the
2020 surface (`navigator.webdriver`, plugins, WebGL strings) and cannot reach
protocol-level leaks, TLS fingerprints, or behavioral scoring. **The 2026 pattern is
to patch the runtime, not the page.**

**`patchright` chosen** as the minimal drop-in: patched Playwright that kills
`Runtime.enable`, kills the `Console.enable` leak, and strips automation flags. Two
costs that are easy to miss: it **disables the Console API entirely** (no in-page
`console`, no console capture for debugging), and it is **Chromium-only**. Lags
upstream Playwright by about one minor. Its README is heavily affiliate-monetized
across proxy vendors — treat its bypass claims as vendor-adjacent.

> **Critical trap.** `patchright`'s default `alwaysIsolated` Runtime-fix mode
> executes in an **isolated world**, which cannot see main-world globals. So
> `page.evaluate(() => window.__NEXT_DATA__)` returns `undefined`. State blobs must
> be parsed from `page.content()` **text**. This inverts the intuitive design:
> "extract from HTML source" is primary, "evaluate in page" is the fallback.

**`seleniumbase`** is the surprise winner on health — 1,194 releases, 15 open issues
/ **0 open PRs**, the most responsive tracker surveyed — carrying three current
stealth layers (UC Mode, CDP Mode, Stealthy Playwright Mode). Its cost is surface
area: a pytest plugin, GUI commander, dashboards, recorder. Good escalation target
at rung 6, wrong default.

**`undetected-chromedriver` is abandoned**: last release 2024-02-17, **1,142 open
issues frozen behind a disabled tracker** — and still ~1.75M downloads/month of dead
code. The author's own README names `nodriver` the successor; `nodriver` itself is
17 commits in 12 months with a dormant tracker, a poor bet for a library whose entire
value is tracking an adversary's updates, and it is **AGPL-3.0** — a real disclosure
question for any hosted use, not a shrug.

**`camoufox`** takes the structurally strongest approach — a custom-compiled Firefox
with fingerprint spoofing injected at the **C++ level** rather than via JS shims.
But: self-declared beta, a 313–663 MB browser download per platform, and engine
development migrated into a commercial venture-studio's repos. **It pins
`playwright<1.61` and therefore cannot coexist with `scrapling` (which pins
`playwright==1.61.0`) in one environment** — use a separate venv.

**Star counts are a bad health proxy in this space.** `scrapling`: 73k★ / 944k
downloads. `botasaurus`: 5.6k★ / **21.8k** downloads. Meanwhile `primp` has 569★ and
10.8M downloads. Use downloads + commit velocity + issue close-rate. Fake-star
inflation is pervasive as of 2026 — unknown repos on the `claude-code-skills` topic
show 44k–98k stars.

---

## 5. Parsing, selection, extraction of structure

`parsel` (1.11.0), `selectolax` (0.4.11), `lxml` (6.1.1 — a 7.0.0a3 alpha is in
flight, don't pin to it), `beautifulsoup4` (4.15.0; source on Launchpad, not GitHub)
are all fine. Prefer `selectolax` for high-volume CSS selection; `parsel` when XPath
+ CSS + JMESPath in one object is wanted.

`jmespath` (1.1.0) is near-dormant but low-risk. **`jsonpath-ng` predates and does
not implement RFC 9535** (JSONPath, standardized Feb 2024); use `python-jsonpath`
(`strict=True`) or `jsonpath-rfc9535` where conformance matters.

**`extruct` is not meaningfully maintained** — last release 2024-11-08, **0 commits
in 12 months**. It remains the only Python library covering JSON-LD + microdata +
RDFa + OpenGraph together, so it is a "use it and own the risk" call. For JSON-LD
alone — the case that matters ~90% of the time — a five-line
`<script type="application/ld+json">` scan has no dependency risk at all, which is
what `scan_state_blobs` does.

For Next.js specifically, `njsparser` handles both `__NEXT_DATA__` and the RSC Flight
format.

---

## 6. Caching, replay, archiving, provenance

| Library | Latest | Released | Verdict |
|---|---|---|---|
| requests-cache | 1.3.3 | 2026-07-03 | Healthy — but binds to `requests` |
| hishel | 1.3.0 | 2026-06-11 | Healthy; **v1.x removed FileStorage** |
| vcrpy | 8.3.0 | 2026-07-04 | Healthy — useful for offline extraction tests |
| warcio | 1.8.1 | 2026-03-31 | Healthy; canonical WARC lib |
| mitmproxy | 12.2.3 | 2026-05-12 | Healthy; requires Python ≥3.12 |
| protego | 0.6.2 | 2026-06-25 | Healthy; zero deps; **chosen** |
| wayback (EDGI) | 0.5.1 | 2026-06-19 | Healthy — **use this** |
| **waybackpy** | 3.0.6 | 2022-03-15 | **Abandoned**, no deprecation notice — a trap |
| reppy | 0.4.14 | 2019-09-16 | **Abandoned**, build fails on 3.12 |

Two corrections to widely-repeated beliefs, both verified by direct testing:

- **`requests-cache` revalidation is always on.** Conditional headers are added
  whenever an ETag/Last-Modified is stored. The much-cited `cache_control=False`
  default governs only whether *response* cache headers drive **expiration** — not
  revalidation.
- **`hishel` v1.x has only SQLite and Redis storages.** `FileStorage`, `S3Storage`,
  and `InMemoryStorage` existed in 0.x and were **removed**. It is also no longer
  httpx-only, and clients moved into submodules.

**Neither works with `curl_cffi`** — which is the operative fact, and why the cache
is owned locally.

**Stdlib `urllib.robotparser` is better than its reputation and still wrong for us.**
Direct testing on 3.12: it *does* expose `crawl_delay()`, `request_rate()`, and
`site_maps()` correctly. The real gap is **wildcard matching** — given
`Disallow: /*.pdf$` it allows `/a/file.pdf` (wrong) where `protego` blocks it — plus
missing RFC 9309 longest-match `Allow` precedence. Hence `protego`: BSD-3, zero
dependencies, implements Google's reference spec.

**Writing WARC from a browser session: there is no maintained in-process Python
option in 2026.** Worth knowing before designing around it.
`warcio.capture_http` **does not work with Playwright** — it monkey-patches
`http.client.HTTPConnection`, which browser-binary traffic never touches (it also
doesn't work with `httpx` or `curl_cffi`, for the same reason). The maintained route
is a **recording proxy**: run `pywb` in record mode and point the browser at it with
TLS errors ignored. Isolate it — `pywb` pins `requires_python <3.13`. Dead ends:
`har2warc` (2018), `warcat` (2017), `internetarchive/warc` (Python 2).

**Free archived-content sources** worth checking before fetching anything: the
Wayback CDX Server API is free and unauthenticated, returning timestamps, status
codes, content digests, and sizes without fetching full pages. Common Crawl's index
is still free and keyless — but its FAQ states the CDX endpoint is heavily rate
limited and that a blocked IP should **wait 24 hours**, so treat `503` there as a
hard stop, not a retry. Note `cdx-toolkit` moved orgs.

---

## 7. Retries, rate limiting

`backoff` is unmaintained and points users to **`tenacity`**, which has full, equal,
and decorrelated jitter built in. Full jitter is the right default — it does the
least total work at the cost of slightly longer wall time. `stamina` is a thin
opinionated layer over tenacity.

Rate limiting: `pyrate-limiter` 4.x (leaky bucket, sync + async, multiprocess-safe)
or `aiolimiter` for pure asyncio. `aiometer` is concurrency scheduling, not rate
limiting.

---

## 8. Deliberately excluded

All commercial scraping APIs, proxy networks, and every CAPTCHA-solving service.
Mentioned once so the boundary is explicit; not recommended anywhere.

Note that several OSS projects above are the open half of an open-core funnel. Using
the OSS half is fine — just know which way the incentives point when reading their
benchmarks.

---

## REFERENCES

[1] [curl_cffi](https://github.com/lexiforest/curl_cffi) · [impersonation targets](https://github.com/lexiforest/curl_cffi/blob/main/docs/impersonate/targets.rst) · [on JA4](https://github.com/lexiforest/curl_cffi/blob/main/docs/impersonate/ja4.rst)
[2] [niquests](https://github.com/jawah/niquests)
[3] [Python-Tls-Client](https://github.com/FlorianREGAZ/Python-Tls-Client) · [Go upstream](https://github.com/bogdanfinn/tls-client)
[4] [wreq-python (ex-`rnet`)](https://github.com/0x676e67/wreq-python)
[5] [Scrapy — Downloader Middleware](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html) · [Jobs: pausing and resuming](https://docs.scrapy.org/en/latest/topics/jobs.html)
[6] [crawlee-python](https://github.com/apify/crawlee-python)
[7] [crawl4ai](https://github.com/unclecode/crawl4ai) · [litellm PyPI compromise — Datadog Security Labs](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/)
[8] [katana](https://github.com/projectdiscovery/katana)
[9] [browsertrix-crawler](https://github.com/webrecorder/browsertrix-crawler)
[10] [patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)
[11] [How New Headless Chrome & the CDP Signal Are Impacting Bot Detection — DataDome](https://datadome.co/threat-research/how-new-headless-chrome-the-cdp-signal-are-impacting-bot-detection/)
[12] [How to fix Runtime.enable CDP detection — Rebrowser](https://rebrowser.net/blog/how-to-fix-runtime-enable-cdp-detection-of-puppeteer-playwright-and-other-automation-libraries) · [rebrowser-bot-detector](https://github.com/rebrowser/rebrowser-bot-detector)
[13] [SeleniumBase](https://github.com/seleniumbase/SeleniumBase) · [UC Mode](https://github.com/seleniumbase/SeleniumBase/blob/master/help_docs/uc_mode.md) · [CDP Mode](https://seleniumbase.io/examples/cdp_mode/ReadMe/)
[14] [camoufox](https://github.com/daijro/camoufox)
[15] [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) · [nodriver](https://github.com/ultrafunkamsterdam/nodriver) · [zendriver](https://github.com/cdpdriver/zendriver)
[16] [playwright_stealth](https://github.com/Mattwmaster58/playwright_stealth) · [puppeteer-extra](https://github.com/berstend/puppeteer-extra)
[17] [Scrapling](https://github.com/D4Vinci/Scrapling) · [botasaurus](https://github.com/omkarcloud/botasaurus)
[18] [parsel](https://github.com/scrapy/parsel) · [selectolax](https://github.com/rushter/selectolax)
[19] [extruct](https://github.com/scrapinghub/extruct)
[20] [njsparser](https://github.com/novitae/njsparser) · [Scraping Next.js sites — Trickster Dev](https://www.trickster.dev/post/scraping-nextjs-web-sites-in-2025/)
[21] [requests-cache — headers and conditional requests](https://requests-cache.readthedocs.io/en/stable/user_guide/headers.html)
[22] [hishel](https://github.com/karpetrosyan/hishel)
[23] [warcio](https://github.com/webrecorder/warcio) · [pywb](https://github.com/webrecorder/pywb) · [Creating a web archive — Crawlee](https://crawlee.dev/python/docs/guides/creating-web-archive)
[24] [protego](https://github.com/scrapy/protego) · [RFC 9309 — Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309.html)
[25] [Wayback Machine APIs](https://archive.org/help/wayback_api.php) · [edgi wayback](https://github.com/edgi-govdata-archiving/wayback) · [Common Crawl Index](https://index.commoncrawl.org/) · [CC FAQ](https://commoncrawl.org/faq)
[26] [tenacity](https://github.com/jd/tenacity) · [Exponential Backoff and Jitter — AWS](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
[27] [PyrateLimiter](https://github.com/vutran1710/PyrateLimiter)
[28] [JA3/JA4 TLS Fingerprinting — Scrapfly](https://scrapfly.io/blog/posts/ja3-ja4-tls-fingerprinting-guide-to-detection-and-evasion)
[29] [WARC 1.0 Format Specification — IIPC](https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.0/)
[30] [Playwright — storageState](https://playwright.dev/python/docs/auth) · [ARIA snapshots](https://playwright.dev/python/docs/aria-snapshots)

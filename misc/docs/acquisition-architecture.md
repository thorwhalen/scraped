# Acquisition architecture

Design record for `scraped.acquire` — the **raw data acquisition** half of web
information-extraction jobs. Extraction of target fields and output formatting are
deliberately *out of scope*; this document defines the seams they hook into.

---

## 1. Terminology: why "acquisition"

The field's vocabulary is muddled. The terms sort as follows:

| Term | What it actually denotes | Fit for "get the bytes down faithfully, with provenance" |
|---|---|---|
| **scraping** | the whole job: fetch **and** parse **and** structure | poor — it is the word for the thing we are splitting |
| **crawling** | *frontier expansion*: discovering new URLs from fetched ones | poor — implies link-following we often don't want |
| **harvesting** | bulk capture into an archive (IIPC, national libraries) | good, but domain-marked (legal deposit) |
| **ingestion** | moving acquired data *into* your system | wrong half — downstream of us |
| **acquisition** | "collection and capture of data at the source" | **best fit** — medium-neutral, no parsing implication |
| **extract** (ETL) | pull records out of a source system; in practice includes shaping | ambiguous — "extract" is what the *other* half wants |
| **fetching** | single-resource retrieval | best fit for the *component*, too small for the subsystem |
| **slurping** | Jargon File: "to read a large data file entirely into core before working on it" | **avoid** — wrong denotation; "pod slurping" also carries a theft connotation |

**Decision:** the subsystem is **acquisition**. Its internals use the established
crawler-architecture vocabulary, which is precise and thirty years old:

- **frontier** — the queue of URLs eligible for fetch, with policy attached
  (priority, revisit interval, per-host politeness).
- **fetcher** — one URL → one `Capture`. Heritrix names its pipeline the *Fetch
  Chain*; Nutch has a *fetcher* consuming a *fetchlist*.
- **fetch chain** — the ordered, injectable transforms around a fetch. Scrapy's
  `process_request` / `process_response` / `process_exception` triple is the
  canonical Python shape and is what our DI seam imitates.
- **capture** — the immutable, provenance-bearing result.

Two adjacent formal concepts we deliberately borrow from:

- **WARC (ISO 28500)** is the only standardized container that stores *request and
  response together with headers and timestamps*. It is the reference model for
  "faithful, with provenance", and `Capture` is **modelled on** a WARC
  request/response pair.

  Be precise about how far that goes: it is a deliberate design target, **not a
  present capability**. A valid WARC `request` record cannot be emitted from a
  `Capture` today, because `Request.headers` holds what the caller asked for, not
  what went on the wire — an impersonating client sets its own headers (and their
  order, which is itself fingerprint-relevant) inside the native library, where we
  never see them. There is also no HTTP version or raw status line, and
  `Response.headers` is a flattened dict, so repeated headers do not survive as
  separate values. Closing that gap means capturing wire-level data, which is what
  a recording proxy is for.
- **Content negotiation** (RFC 9110 proactive negotiation) is an acquisition
  *strategy*, not protocol trivia: the same URL frequently serves JSON to an
  `Accept: application/json` request and HTML to a browser. It costs one header
  to test and is the cheapest possible escalation.

---

## 2. The two-axis model

The conventional presentation is a single ladder from cheap to expensive. **That
framing is wrong and actively harmful.** There are two independent axes:

- **Axis A — payload shape.** How structured and how stable the thing you receive
  is. Determines extraction cost and long-term maintenance burden.
- **Axis B — transport.** How much machinery it took to be *allowed* to receive it.
  Determines runtime cost and fragility.

**Optimize A first, B second — and note that any payload rung is reachable from any
transport rung.** "Drive a full browser, then read the embedded state blob" is the
highest-value cell on the grid, and the single-ladder framing hides it, because it
makes browser automation feel like the bottom of the ladder rather than an
orthogonal cost.

This is not theoretical. The motivating incident (a DataDome-protected Next.js
classifieds site) resolved exactly there: transport escalated all the way to a real
browser session, while the payload stayed near the *top* of the structure ladder —
the server's own `__NEXT_DATA__` JSON, not the rendered DOM. The DOM-parsing
attempt that preceded it silently under-collected, returning 35/32/29 items on
pages that each held 35, because cards were rendered lazily.

### Axis A — payload ladder, with detection tests

**(a) Official / public API.** Stable, versioned, documented.
*Detect:* `robots.txt` often references it; check `developer.<domain>`,
`api.<domain>`, `/.well-known/`, `openapi.json`/`swagger.json`; try
`Accept: application/json` against the HTML URL — one request, sometimes just works.

**(b) Undocumented internal JSON/GraphQL API.** The same data the site's own
frontend consumes. Usually the best cost/robustness point.
*Detect:* drive the page under a recording proxy or with HAR recording enabled, then
grep the HAR for `application/json` responses whose bodies contain target strings.
*GraphQL caveat:* many endpoints use Apollo **Automatic Persisted Queries** — the
client sends only a `sha256Hash`. Recover the query text via the
`PersistedQueryNotFound` error path or by capturing the first uncached request.

**(c) Embedded server-state blobs in the HTML.** Zero extra requests, and the data
is the server's own object graph rather than a rendering of it.

| Framework | Marker | Notes |
|---|---|---|
| Next.js Pages Router | `<script id="__NEXT_DATA__" type="application/json">` | plain JSON — cleanest case on the web |
| Next.js App Router / RSC | `self.__next_f.push([...])` across many tags | React Flight wire format, **not JSON** |
| Nuxt 2 | `window.__NUXT__ = ...` | sometimes a function expression, needs evaluation |
| Nuxt 3 | `<script id="__NUXT_DATA__">` | JSON in a devalue-style flattened reference format |
| Apollo | `window.__APOLLO_STATE__` | normalized cache, entities keyed `Type:id` |
| Redux | `window.__PRELOADED_STATE__` / `__INITIAL_STATE__` | plain object |
| Remix | `window.__remixContext` | |
| SvelteKit | `__sveltekit_*` bootstrap, `data-sveltekit-fetched` tags | |
| Angular Universal | `<script id="ng-state" type="application/json">` | TransferState |
| Gatsby | per-route `page-data.json` | fetch it directly — it *is* rung (b) |
| any | `<script type="application/ld+json">` | Schema.org — highest stability when present |
| any | `<script type="application/json" id="...">` | generic; scan unconditionally |

*Generic detection heuristic* (implemented as `scan_state_blobs`): scan all
`<script>` tags; for each, try (1) `type` is a JSON media type → parse directly;
(2) body matches `^\s*(self|window)\.__[A-Za-z_]+` → capture the assignment RHS;
(3) body > 2 KB and starts with `{` or `[` → attempt parse. Report every hit with
its size and let the caller choose.

**This scan runs automatically on every HTML capture and its results are recorded
in the capture metadata.** It is the single highest-leverage thing the acquisition
layer can do for the extraction layer.

**(d) Sitemaps and feeds.** The correct source for *URL discovery* — cheaper and
more complete than link-following. `Sitemap:` in `robots.txt` (independent of any
`User-agent` block), `/sitemap.xml`, `/sitemap_index.xml`, `<link rel="alternate">`
for RSS/Atom. `<lastmod>` drives incremental crawls.

**(e) DOM parsing.** The default that shouldn't be. Brittle against class-name churn
and CSS-in-JS hashing, and it forces reverse-engineering a *presentation* of the
data instead of reading the data. Anchor on stable attributes (`itemprop`,
`data-testid`, `aria-*`, semantic elements), never generated class names.

**(f) Rendered text / article extraction.** Discards structure, keeps prose. Correct
for editorial content, wrong for tabular or entity data.

**(g) Vision / screenshot.** Last resort. For model consumption an **ARIA snapshot**
is a better intermediate than a screenshot: a YAML accessibility tree compresses a
50 KB page to 2–5 KB of structured text.

### Axis B — transport ladder

1. Plain HTTP client — free, fastest, most detectable
2. Impersonating HTTP client — fixes TLS/HTTP2/HTTP3 fingerprints; still no JS
3. Impersonating client + captured session state — replay cookies obtained once
4. Patched browser automation — pays for JS execution, passes runtime fingerprinting
5. Real headful browser with persistent profile — highest fidelity, lowest throughput
6. Human-assisted — a person completes the initial challenge; automation continues

---

## 3. `Capture` is the design

Everything else is an accessory. A frozen, serializable dataclass isomorphic to a
WARC request/response pair:

```
Capture:
  request:   method, url, headers_sent, body_sent
  response:  final_url, redirect_chain, status, headers, body_sha256, body_len
  timing:    fetched_at_utc, elapsed_ms
  fetcher:   client_name, client_version, impersonate_profile, transport_rung
  policy:    robots_checked, robots_allowed, robots_override_reason
  probe:     state_blob_markers_found, ld_json_count, challenge_vendor | None
  body:      bytes  — stored separately, content-addressed by sha256
```

Body separate from metadata, keyed by hash, buys deduplication, change detection,
and cheap re-runs in one move. Metadata is small enough for SQLite or JSONL; bodies
go to a content-addressed store. Both sides are `Mapping`s, so local-dir → S3 is a
constructor change and touches no acquisition logic.

**Store raw bytes, never decoded text.** Decoding is a lossy interpretation and
belongs to the extraction half. Charset resolution order when the extraction layer
does decode: HTTP `Content-Type` charset → `<meta charset>` → BOM → detection.

---

## 4. Seams

| Concern | Seam | Owner |
|---|---|---|
| acquisition | `Fetcher: Callable[[Request], Capture]` | this package |
| → extraction | `Capture` + `SiteProfile.state_blobs` (pre-located, typed, sized) | caller / LLM / any extractor |
| → formatting | *(nothing — hand off)* | downstream packages |
| storage | `Mapping[RequestDigest, Capture]` | `dol` |
| caching | owned here — see below | this package |
| robots policy | injected policy object, default on, override recorded in `Capture` | `protego` behind a facade |

The `Fetcher` protocol is the generalization of the pre-existing
`acquire_content(uri_to_content, ...)` seam, which already injected both the
content-getter and the store. The plugin architecture that the long-standing TODO
in `tools.py` called for *is* the transport ladder.

**Why we own the cache rather than importing one.** `requests-cache` binds to
`requests.Session`; `hishel` binds to httpx/requests transports. Neither works with
an impersonating libcurl-based client, which is deliberately coupled to
libcurl-impersonate and offers no adapter mounting. Bridge shims exist but are thin
and lossy. Since cache entries must also carry provenance and survive a client swap,
the cache is a `Mapping[RequestDigest, Capture]` over a content-addressed store.

The cache key is a **request digest**: canonicalized URL + method + the subset of
headers that actually vary the response (per `Vary`) + body hash. Two modes:
*fetch-once* (a re-run costs nothing — this is what makes iterating on the
extraction half tolerable) and *revalidate* (send `If-None-Match` /
`If-Modified-Since`, treat `304` as "reuse body, update `fetched_at`").

**Facades over every third-party library.** This is not ceremony. The churn rate in
this space is extreme: the most-downloaded stealth driver is abandoned with its
issue tracker disabled, a popular TLS client ships unsigned multi-year-stale binary
blobs, and stealth *plugins* as a category are obsolete. See
[`acquisition-tooling-survey.md`](acquisition-tooling-survey.md).

---

## 5. Escalation ladder — symptom to move

**Rung 0 — reconnaissance (once per site, automatable, always).** This is the
highest-return step in the entire workflow and is implemented as `probe(url)`:

```
GET /robots.txt        → Sitemap: directives, Disallow policy, AI-crawler rules
GET /sitemap.xml       → URL universe + <lastmod> for incrementality
GET <target> with Accept: application/json     → free rung-(a) test
HEAD <target>          → server, CDN, Content-Type, cache headers
scan HTML for:         __NEXT_DATA__ | __next_f | __NUXT | __APOLLO_STATE__
                       | __PRELOADED_STATE__ | __remixContext | ng-state
                       | application/ld+json | type="application/json"
```

**Rung 1 — impersonating HTTP client.** Start here, not at a plain client. The
marginal cost is zero and it removes the entire TLS/HTTP2 fingerprint failure class
in one argument.
→ *Promote when:* `200` but target content absent from the HTML.

**Rung 2 — read the embedded state blob.** Parse it out of the HTML *text*. Most
jobs should end here.
→ *Promote when:* no blob, or the blob is a stub carrying only route metadata
(common on App Router pages where real data streams in separately).

**Rung 3 — find and call the internal API.** Drive once with HAR recording, grep for
JSON responses containing target strings, convert the winning request to code, and
replay it at rung 1. Highest long-term value: one discovery session buys a stable,
paginated, machine-readable endpoint that outlives several DOM redesigns.
→ *Promote when:* the endpoint needs a token minted by in-page JS, or is signed.

**Rung 4 — capture the session, then drop back down.** Launch headful with a
persistent profile, complete the handshake, serialize `storage_state`, then return
to rung 1 or 3 with cookies injected. You pay for the browser once, not per request.
Treat that state file as a **credential**: never commit it, scope its lifetime.
→ *Promote when:* the token is short-lived, per-request, or bound to a live context.

**Rung 5 — stay in the browser, still read the blob.** Take `page.content()` and
extract the state blob from the HTML source, **not** the DOM. Better still, register
a response handler and capture the site's own JSON as it arrives — rung-(b) payloads
at rung-5 transport cost, working even when the app consumes and discards the
response.
→ *Promote when:* detected on a page that loads fine manually.

**Rung 6 — change the browser.** A different patched engine or fingerprint surface.
Prefer a real browser channel, persistent context, headful, no custom UA.

**Rung 7 — stop.** Emit `ChallengeEncountered`, save the challenge page as evidence,
surface it. **Do not solve CAPTCHAs.** The remaining legitimate options are: ask the
site for API access, check whether a public web archive already has the content, or
accept a human-in-the-loop step.

### Diagnostic signal table

| Signal | Meaning | Move |
|---|---|---|
| `200`, content in HTML | server-rendered | rung 2 or 5 |
| `200`, empty container divs | CSR / hydration | rung 2 → 3 |
| `__NEXT_DATA__` present | Pages Router | parse as JSON, done |
| `self.__next_f`, no `__NEXT_DATA__` | App Router / RSC | Flight parse, not `json.loads` |
| `application/ld+json` present | Schema.org | try first — most stable payload on the web |
| `403` + `cf-mitigated: challenge` | managed challenge | rung 4–6 |
| `403` + interactive-widget sitekey on a loaded page | interactive challenge | **rung 7** |
| `403` + vendor cookie / `x-*` vendor header | commercial bot manager | rung 4–6 |
| `403`, plain body, no vendor markers | often UA/TLS only | rung 1 usually fixes it |
| `429` + `Retry-After` | honest rate limit | honor exactly; lower concurrency |
| `200` but short body, no `<title>` | truncation or CDN error page | retry, compare hashes |
| results stop at page ~N (N≈100–200 at 50/page) | deep-paging cap | partition the query space |
| `Set-Cookie` every request, rotating token | session/CSRF binding | rung 4 |
| row count fixed regardless of scroll depth | virtualized list | rung 3 — the DOM will never hold it |

---

## 6. Cross-cutting concerns this layer owns

> **Status:** politeness, retries, duplicate detection, and robots policy are
> implemented. Checkpointing, canary URLs, and byte-level drift diffing are
> **designed but not built** — they arrive with the multi-page frontier. They are
> stated here because they belong to this layer, not because they exist yet.

- **Politeness** — per-host token bucket, not global. 1–3 concurrent per host,
  0.5–3 s spacing with jitter, honor `Retry-After` exactly, honor `Crawl-delay`
  when present even though RFC 9309 deliberately excluded it from the standard.
  Identify honestly in the User-Agent with a contact URL.
- **Retries** — full jitter by default. Retry connection errors, timeouts, `429`,
  `5xx`. **Never retry a `403` carrying challenge markers** — it burns the IP's
  reputation and confirms automation. Cap total elapsed, not just attempts.
- **Checkpointing** — a disk-persisted frontier, a disk-persisted dupefilter, and a
  persisted job-state map. Every state transition durable *before* the network call,
  so a crash re-fetches at most one URL. Resuming is the default; `--fresh` is the
  explicit override.
- **Duplicates** — URL canonicalization into the frontier's dedup key, plus
  content-hash dedup at the store (which also gives free change detection).
  Canonicalization drops the fragment, which is correct at the HTTP rungs (the
  server never sees it) but **collapses distinct pages at the browser rung**,
  where `#!/page/1` and `#!/page/2` are different content. Pass
  `keep_fragment=True` when caching browser fetches of a fragment-routed app.
- **Data drift** — the worst failure class, because everything reports success.
  Three controls belong *here* even though they look like extraction concerns:
  canary URLs with known values asserted every run; shape assertions on the raw
  payload (key presence, array non-emptiness, count vs declared total) before
  handing off; and byte-level diffing against the previous capture — a hash change
  on a page expected to be stable, or *no* change on a page expected to move, are
  both alarms.
- **Robots and legal posture** — robots compliance is a *policy object injected into
  the fetcher*, defaulting to on, with an explicit named override recorded in the
  provenance record. "Did we respect robots on this capture" is then a queryable
  fact rather than an archaeology exercise. RFC 9309 standardized the protocol in
  2022 but deliberately excluded `Crawl-delay`. Machine-readable reservation signals
  (`robots.txt`, `llms.txt`, `ai.txt`) increasingly carry legal weight.

---

## 7. Explicitly out of scope

- **Extraction** of target fields from a capture, and **formatting** of the result.
  Seams are provided; implementations are not.
- **CAPTCHA solving.** Detect, emit a typed error, stop.
- **Paid services** of any kind — no commercial scraping APIs, proxy networks, or
  solver services. Everything here is free and self-hostable.
- **Replacing the existing Scrapy-based site crawler.** `download_site` and
  `markdown_of_site` are unaffected.

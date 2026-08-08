# Getting results out of an automated browser

A first-class problem class, distinct from acquiring the data in the first place —
and empirically the more expensive of the two. In the incident that motivated this
package, locating the data took minutes; moving 42 KB of it from the browser to disk
took the rest of the session and six failed approaches.

The failures were not bad luck. There are **three independent gates**, and confusing
them is what wastes the time.

---

## The three gates

### Gate 1 — the agent-harness tool-output cap

Independent of the browser entirely. Agent harnesses truncate tool results before
the model ever sees them — Claude Code caps around 25K tokens
(`MAX_MCP_OUTPUT_TOKENS`), other MCP clients set their own limits, and in practice a
JS-evaluation tool returned ~1000 characters before truncating.

**Nothing done inside the browser defeats this.** The only fix is to never route the
payload through the tool-result channel.

### Gate 2 — the CDP WebSocket frame limit

Chrome's DevTools HTTP server has `kDefaultMaxBufferSize` = **1 MB** and handles
neither fragmentation nor continuation frames; oversized messages cause a **silent
disconnect**.

This bites raw-CDP tooling. It does *not* bite Playwright, which launches Chromium
with `--remote-debugging-pipe` (inherited file descriptors, not a TCP socket) — one
of several reasons to prefer a real driver over hand-rolled CDP.

### Gate 3 — the browser's own network and permission policy

This is the one that produces the maddening symptom: a request that **stalls
silently, with no console error and nothing arriving at the other end**.

**Chrome 142 (October 2025) shipped Local Network Access** (formerly Private Network
Access) with full enforcement and no fallback. A public HTTPS page requesting
`127.0.0.1`, `localhost`, `*.local`, or any RFC1918 address now triggers a permission
prompt; denied or unanswered, the request fails silently. Layered on top, the site's
own CSP `connect-src` will usually not list a local receiver either.

Related gates in the same family: `navigator.clipboard.writeText` requires a secure
context **and** document focus **and** clipboard-write permission — an unfocused
automated tab fails with `NotAllowedError`.

---

## The rule

> **The browser writes to disk; the agent reads the file path.**
> Never move a payload through the model's context.

Concretely, in this package: every browser-rung fetcher sets `record_har_path` and
`downloads_path` at context creation as non-negotiable defaults, and returns a
filesystem path plus a `Capture` record — never a payload.

The deeper reason the motivating incident was hard: it was driven through a
**browser-extension automation channel, which has no filesystem of its own**, so
every exit route necessarily ran through the model's context. A locally driven
Playwright browser runs *in our process* and can write straight to disk. For any job
whose output is a payload rather than an observation, drive the browser locally.

---

## Known-good patterns, in the order to try them

1. **Fetch it server-side instead.** If you have the URL and the session cookies,
   exfiltrate the *cookies* (small) and re-fetch the payload outside the browser with
   an impersonating HTTP client. Best answer whenever it applies — it converts an
   exfiltration problem into a fetch problem.

2. **Intercept the response rather than the page.** `page.on("response")` +
   `response.json()`, or a CDP session with `Network.getResponseBody`. You get the
   payload *before* the app touches it, and there is no in-page exfiltration step at
   all, because the write happens in the driving process. Works even when the app
   consumes and discards the response.

3. **`record_har_path` on the context.** Set
   `record_har_path=...` (optionally with `record_har_url_filter="**/api/**"`) at
   context creation; every request and response body lands in a file on the host
   filesystem, flushed when the context closes. Zero in-page code, survives every
   gate above, and doubles as the discovery artifact for the internal-API rung.
   **For an agent-driven session this is the single most reliable pattern** — set it
   before navigating and the data cannot be lost.

4. **Blob download + download event.** In-page:
   `URL.createObjectURL(new Blob([json], {type:'application/json'}))` → `<a download>`
   → `click()`. Playwright fires the download event for blob URLs identically to
   server downloads, and `download.save_as(path)` writes to the real filesystem.
   *Caveats:* a strict CSP may block the blob navigation (some sites need `blob:` in
   `frame-src`), and a CSP `sandbox` directive requires `allow-downloads`.

5. **CDP `Browser.setDownloadBehavior`** with
   `{behavior: "allowAndName", downloadPath: <abs>, eventsEnabled: true}`, plus
   `Browser.downloadWillBegin` / `downloadProgress` events reporting state and final
   path. The correct low-level primitive when driving raw CDP — headless Chrome
   denies downloads by default without it.

6. **`Page.captureSnapshot {format: "mhtml"}`** for the whole rendered page as one
   self-contained file, written from the driving process. A good archival fallback
   when you don't yet know what you need.

7. **Recording proxy** (`pywb` or `warcprox`). Point the browser at it; every byte
   lands in WARC on disk regardless of what the page does. Highest fidelity, highest
   setup cost.

8. **Chunked evaluation returning slices.** Only if stuck on raw CDP with Gate 2 and
   nothing above is available. Note that evaluation returns must be JSON-serializable
   — functions, DOM nodes, `Map`/`Set`, `RegExp`, `Error`, class instances, and
   cycles do not survive, and deeply nested structures hit a depth cap.

9. **Browser extension + downloads API / native messaging.** Real but heavy.
   Justified only for a persistent tool, never for a one-off.

---

## Anti-patterns

| Anti-pattern | Gate | Symptom |
|---|---|---|
| `fetch`/`XHR` from the page to a localhost receiver | 3 | **silent stall**, no console error, nothing received |
| `navigator.clipboard` in an unfocused tab | 3 | `NotAllowedError` |
| rendering JSON into the DOM and reading it back via a page-text tool | 1 | works, but truncates at the harness cap and corrupts whitespace-significant content |
| returning the payload as a tool result | 1 | truncated before the model sees it |
| raw CDP for payloads > 1 MB | 2 | silent disconnect |

---

## If you are stuck on an extension channel anyway

The DOM-render + full-page-text-extraction route (anti-pattern 3 above) *does* work
for payloads under the harness cap, and was how the motivating incident finally
resolved. If forced into it, **verify integrity**: compute a SHA-256 of the canonical
payload in-page, reconstruct the file, and re-hash it locally before trusting the
result. Comparing a re-serialized canonical form (compact separators, non-ASCII
preserved) makes the comparison robust to formatting differences between the
in-page and local serializers.

This is a fallback to be verified, not a design.

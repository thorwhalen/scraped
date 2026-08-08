"""Transport rungs 4–5: a real browser, with disk-first defaults.

The design constraint here is **not** stealth. It is that a payload must never
travel through the caller's context. Getting results *out* of an automated
browser is a distinct and expensive failure class — three independent gates (the
agent harness's tool-output cap, the 1 MB CDP frame limit, and Chrome's Local
Network Access enforcement, which makes a page's `fetch` to `127.0.0.1` stall
*silently*) block the obvious routes. See
`misc/docs/browser-result-exfiltration.md`.

So the rule this module enforces:

    The browser writes to disk; the caller reads the file path.

`record_har_path` and `downloads_path` are set at context creation and are not
optional. Every fetch returns a `Capture` plus paths to artifacts on the real
filesystem, and the HAR doubles as the discovery input for the internal-API rung.

**Caveat on provenance at this rung:** a `Capture.body` here is
`page.content()` — a re-serialized DOM, not the bytes the server sent — so
`body_sha256` hashes a *rendering*. The package-wide 'raw bytes, never decoded
text' invariant holds at the HTTP rungs; here the HAR is the byte-exact record.

Two traps worth knowing before reading the code:

- **Patched drivers run `page.evaluate` in an isolated world**, which cannot see
  main-world globals. `page.evaluate(() => window.__NEXT_DATA__)` returns
  `undefined` there. So state blobs are parsed from `page.content()` *text*, and
  evaluation is the fallback rather than the primary path.
- **Paying for a browser once is the point.** Capture `storage_state` and hand it
  back to a cheap HTTP fetcher (rung 4) rather than driving the browser for every
  page.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..blobs import state_blob_markers
from ..capture import (
    Capture,
    FetcherInfo,
    PolicyOutcome,
    ProbeMarks,
    Request,
    Response,
    body_digest,
    utcnow,
)
from ..policy import RobotsPolicy
from ..signals import ChallengeEncountered, detect_challenge

__all__ = [
    "BrowserArtifacts",
    "BrowserFetcher",
    "BrowserSession",
    "check_requirements",
    "storage_state_headers",
]

DFLT_WAIT_UNTIL = "domcontentloaded"
DFLT_NAV_TIMEOUT_MS = 45_000
DFLT_ARTIFACT_DIR = "~/.config/scraped/browser-runs"

# Captured API responses are the prize; filter to keep the HAR usable.
DFLT_JSON_CONTENT_HINTS = ("application/json", "text/json", "+json")


def check_requirements(*, verbose: bool = True) -> dict:
    """Report what the browser rung needs and what is actually present.

    Guides rather than fails: this rung is an optional extra, so the useful
    behaviour is to say precisely what to install.

    >>> report = check_requirements(verbose=False)
    >>> set(report) == {"driver", "browser_binary", "ready", "instructions"}
    True
    """
    driver = None
    for candidate in ("patchright", "playwright"):
        try:
            __import__(f"{candidate}.sync_api")
            driver = candidate
            break
        except ImportError:
            continue

    instructions = []
    if driver is None:
        instructions.append(
            "pip install 'scraped[browser]'   # patchright: patched Playwright"
        )
    if driver == "playwright":
        instructions.append(
            "pip install patchright   # preferred: plain Playwright is detectable "
            "via the Runtime.enable CDP signal"
        )
    binary_present = driver is not None and _browser_binary_present(driver)
    if driver is not None and not binary_present:
        instructions.append(f"{driver} install chromium   # the browser itself")

    report = {
        "driver": driver,
        "browser_binary": binary_present,
        "ready": bool(driver and binary_present),
        "instructions": instructions,
    }
    if verbose:  # pragma: no cover - human-facing
        print(f"driver:         {driver or 'MISSING'}")
        print(f"browser binary: {'present' if binary_present else 'MISSING'}")
        for line in instructions:
            print(f"  -> {line}")
    return report


def _browser_binary_present(driver: str) -> bool:
    try:
        module = __import__(f"{driver}.sync_api", fromlist=["sync_playwright"])
        with module.sync_playwright() as p:
            return bool(p.chromium.executable_path)
    except Exception:
        return False


def _driver():
    """Import the best available driver, preferring the patched one."""
    for candidate in ("patchright", "playwright"):
        try:
            return (
                __import__(f"{candidate}.sync_api", fromlist=["sync_playwright"]),
                candidate,
            )
        except ImportError:
            continue
    raise ImportError(
        "The browser rung needs a Playwright-compatible driver.\n"
        "    pip install 'scraped[browser]'\n"
        "    patchright install chromium\n"
        "Run scraped.acquire.fetchers.browser.check_requirements() for details."
    )


@dataclass(frozen=True)
class BrowserArtifacts:
    """Where this fetch's evidence landed on disk.

    Paths, never payloads. The HAR in particular is both the safety net (every
    byte the page received is on disk regardless of what the page did with it)
    and the input to internal-API discovery.
    """

    har_path: Path | None = None
    downloads_dir: Path | None = None
    storage_state_path: Path | None = None
    captured_json: Sequence[Path] = ()

    def __bool__(self) -> bool:
        return bool(self.har_path or self.captured_json)


@dataclass
class BrowserSession:
    """A browser context configured so results cannot be lost.

    Use as a context manager. Artifacts go to `artifact_dir`; nothing of
    consequence is returned through memory alone.
    """

    artifact_dir: Path = field(
        default_factory=lambda: Path(DFLT_ARTIFACT_DIR).expanduser()
    )
    headless: bool = False
    storage_state: str | Path | None = None
    record_har: bool = True
    har_url_filter: str | None = None
    capture_json_responses: bool = True
    channel: str | None = None
    viewport: Mapping[str, int] | None = None

    def __post_init__(self):
        self.artifact_dir = Path(self.artifact_dir).expanduser()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._downloads = self.artifact_dir / "downloads"
        self._downloads.mkdir(exist_ok=True)
        self._json_dir = self.artifact_dir / "json"
        self._json_dir.mkdir(exist_ok=True)
        self._har_path = (
            self.artifact_dir / "session.har.zip" if self.record_har else None
        )
        self._captured: list[Path] = []
        self._stack = None

    def __enter__(self) -> "BrowserSession":
        api, self.driver_name = _driver()
        self._pw = api.sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "downloads_path": str(self._downloads),
        }
        if self.channel:
            launch_kwargs["channel"] = self.channel
        self._browser = self._pw.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {"accept_downloads": True}
        if self._har_path is not None:
            context_kwargs["record_har_path"] = str(self._har_path)
            if self.har_url_filter:
                context_kwargs["record_har_url_filter"] = self.har_url_filter
        if self.storage_state:
            context_kwargs["storage_state"] = str(self.storage_state)
        if self.viewport is None:
            context_kwargs["no_viewport"] = True
        else:
            context_kwargs["viewport"] = dict(self.viewport)

        self._context = self._browser.new_context(**context_kwargs)
        self._context.set_default_navigation_timeout(DFLT_NAV_TIMEOUT_MS)
        return self

    def __exit__(self, *exc_info):
        # Closing the context is what flushes the HAR. Do it even on failure —
        # a failed run's evidence is the most valuable kind.
        for closer in (
            getattr(self, "_context", None),
            getattr(self, "_browser", None),
        ):
            try:
                closer and closer.close()
            except Exception:  # pragma: no cover
                pass
        try:
            self._pw.stop()
        except Exception:  # pragma: no cover
            pass
        return False

    def save_storage_state(self, path: str | Path | None = None) -> Path:
        """Persist cookies and origin storage so cheap rungs can reuse this session.

        **Treat the result as a credential.** It carries live session cookies:
        never commit it, and scope its lifetime deliberately.
        """
        target = Path(path or self.artifact_dir / "storage_state.json").expanduser()
        self._context.storage_state(path=str(target))
        return target

    def _watch_json(self, page) -> None:
        """Write every JSON response to disk as it arrives.

        This is the highest-value pattern in the module: it yields the site's own
        API payloads — a top-rung *payload* at browser *transport* cost — and it
        works even when the app consumes and discards the response.
        """

        def on_response(response):
            try:
                content_type = (response.headers or {}).get("content-type", "").lower()
                if not any(hint in content_type for hint in DFLT_JSON_CONTENT_HINTS):
                    return
                body = response.body()
                if not body:
                    return
                target = self._json_dir / f"{body_digest(body)[:16]}.json"
                if not target.exists():
                    target.write_bytes(body)
                    self._captured.append(target)
            except Exception:  # a discarded response is normal; never fail the run
                pass

        page.on("response", on_response)

    def artifacts(self) -> BrowserArtifacts:
        """Where everything landed."""
        return BrowserArtifacts(
            har_path=self._har_path,
            downloads_dir=self._downloads,
            captured_json=tuple(self._captured),
        )


@dataclass
class BrowserFetcher:
    """A `Fetcher` that drives a real browser and writes its evidence to disk.

    Same callable shape as the HTTP rungs, so callers do not know or care which
    transport is underneath.
    """

    session: BrowserSession
    wait_until: str = DFLT_WAIT_UNTIL
    settle_ms: int = 0
    robots: RobotsPolicy | None = None
    raise_on_challenge: bool = True
    rung: int = 5

    def __call__(self, request: Request) -> Capture:
        """Navigate to the request's URL and capture the rendered document."""
        policy_outcome = (
            self.robots.enforce(request.url) if self.robots else PolicyOutcome()
        )
        page = self.session._context.new_page()
        if self.session.capture_json_responses:
            self.session._watch_json(page)

        started = time.perf_counter()
        try:
            response = page.goto(request.url, wait_until=self.wait_until)
            if self.settle_ms:
                page.wait_for_timeout(self.settle_ms)
            # page.content() is documentElement.outerHTML — a re-serialized
            # post-script DOM, NOT the wire bytes. It is still the right source
            # for state blobs (a patched driver's page.evaluate runs in an
            # isolated world and cannot see window.__NEXT_DATA__ at all), but it
            # means body_sha256 at this rung hashes a rendering: use the HAR for
            # byte-exact provenance and cross-run change detection.
            html = page.content()
            status = response.status if response else 0
            headers = dict(response.headers) if response else {}
            final_url = page.url
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            page.close()

        body = html.encode("utf-8")
        verdict = detect_challenge(status, headers, html)
        if verdict and self.raise_on_challenge:
            raise ChallengeEncountered(
                final_url,
                vendor=verdict.vendor,
                interactive=verdict.interactive,
                status=status,
            )

        return Capture(
            request=request,
            response=Response(
                status=status,
                final_url=final_url,
                headers=headers,
                body_sha256=body_digest(body),
                body_len=len(body),
            ),
            fetched_at=utcnow(),
            elapsed_ms=round(elapsed_ms, 2),
            fetcher=FetcherInfo(
                client_name=getattr(self.session, "driver_name", "browser"),
                transport_rung=self.rung,
            ),
            policy=policy_outcome,
            probe=ProbeMarks(
                state_blob_markers=tuple(state_blob_markers(html)),
                ld_json_count=html.count("application/ld+json"),
                challenge_vendor=verdict.vendor,
                challenge_serving=verdict.serving,
            ),
            body=body,
        )


def storage_state_headers(path: str | Path, *, url_host: str | None = None) -> dict:
    """Turn a saved `storage_state` into a `Cookie` header for cheap fetchers.

    This is rung 4, and the reason the browser is worth paying for once: capture
    the session in a browser, then do the actual work with an impersonating HTTP
    client at a fraction of the cost.

    >>> import json, tempfile, pathlib
    >>> state = {"cookies": [{"name": "a", "value": "1", "domain": "x.com"}]}
    >>> path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    >>> _ = path.write_text(json.dumps(state))
    >>> storage_state_headers(path)
    {'Cookie': 'a=1'}
    """
    state = json.loads(Path(path).expanduser().read_text())
    cookies = state.get("cookies", [])
    if url_host:
        cookies = [
            c for c in cookies if url_host.endswith(c.get("domain", "").lstrip("."))
        ]
    if not cookies:
        return {}
    return {"Cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies)}

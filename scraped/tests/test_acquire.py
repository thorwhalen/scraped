"""Tests for the acquisition layer.

Offline by default: the transport is injected, so nothing here touches the
network. That is the point of the `Fetcher` seam — if these needed a live site,
the seam would be in the wrong place.
"""

import json

import pytest

from scraped.acquire import (
    Capture,
    CaptureStore,
    ChallengeEncountered,
    HttpFetcher,
    Politeness,
    Request,
    RobotsDisallowed,
    RobotsPolicy,
    cached,
    canonicalize_url,
    classify,
    detect_challenge,
    request_digest,
    scan_state_blobs,
)
from scraped.acquire.fetchers.http import TransportResult


class FakeTransport:
    """A scriptable transport. Records what it was asked for."""

    name = "fake"
    rung = 1

    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def send(self, request, *, timeout=None):
        self.requests.append(request)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return result


def html_result(body: str, *, status=200, headers=None):
    return TransportResult(
        status=status,
        headers=headers or {"Content-Type": "text/html; charset=utf-8"},
        body=body.encode(),
        final_url="https://example.com/page",
    )


# --------------------------------------------------------------------------
# state blobs — the core insight


NEXT_DATA_PAGE = """
<html><body>
  <div id="__next"><!-- only 3 of 35 cards rendered, the rest lazy --></div>
  <script id="__NEXT_DATA__" type="application/json">
  {"props":{"pageProps":{"searchData":{"total":193,"ads":[
     {"list_id":1,"subject":"first"},{"list_id":2,"subject":"second"},
     {"list_id":3,"subject":"third"}]}}}}
  </script>
</body></html>
"""


def test_state_blob_beats_the_dom():
    """The regression that motivated this package.

    A DOM scrape of a lazily-rendered listing under-collects silently. The state
    blob is complete by construction. This asserts we find the blob and that it
    carries more rows than the DOM shows.
    """
    blobs = scan_state_blobs(NEXT_DATA_PAGE)
    assert [b.kind for b in blobs] == ["next_data"]
    ads = blobs[0].data["props"]["pageProps"]["searchData"]["ads"]
    assert len(ads) == 3
    assert NEXT_DATA_PAGE.count("<article") == 0  # nothing to scrape in the DOM


def test_scan_finds_each_framework():
    cases = {
        '<script type="application/ld+json">{"@type":"Product"}</script>': "ld_json",
        '<script>window.__APOLLO_STATE__ = {"a":1};</script>': "apollo",
        '<script>window.__PRELOADED_STATE__ = {"a":1};</script>': "redux",
        '<script id="ng-state" type="application/json">{"a":1}</script>': "angular",
        '<script id="__NUXT_DATA__" type="application/json">[1]</script>': "nuxt_data",
    }
    for html, expected_kind in cases.items():
        kinds = [b.kind for b in scan_state_blobs(html)]
        assert expected_kind in kinds, f"{expected_kind} not found in {kinds}"


def test_scan_survives_braces_inside_strings():
    """A hand-rolled brace counter gets this wrong; the JSON decoder does not."""
    html = '<script>window.__APOLLO_STATE__ = {"t":"a } b { c","n":1};</script>'
    (blob,) = [b for b in scan_state_blobs(html) if b.kind == "apollo"]
    assert blob.data == {"t": "a } b { c", "n": 1}


def test_rsc_flight_is_reported_but_not_parsed():
    """App Router streams React Flight, which is not JSON. Say so honestly."""
    html = '<script>self.__next_f.push([1,"chunk-a"])</script>'
    (blob,) = [b for b in scan_state_blobs(html) if b.kind == "rsc_flight"]
    assert blob.raw == "chunk-a"
    assert blob.parsed is False


def test_unparseable_global_is_still_reported():
    """Nuxt 2 sometimes assigns a function expression. Knowing it's there matters."""
    html = "<script>window.__NUXT__ = (function(a){return {a:a}})(1);</script>"
    kinds = [b.kind for b in scan_state_blobs(html)]
    assert "nuxt" in kinds


# --------------------------------------------------------------------------
# challenge detection — the most expensive failure is the silent one


def test_challenge_is_raised_not_returned():
    transport = FakeTransport(
        html_result("blocked", status=403, headers={"cf-mitigated": "challenge"})
    )
    fetcher = HttpFetcher(transport=transport, max_attempts=1)
    with pytest.raises(ChallengeEncountered) as caught:
        fetcher(Request("https://example.com/page"))
    assert caught.value.vendor == "cloudflare"
    assert "do not retry" in str(caught.value)


def test_interactive_challenge_is_flagged_terminal():
    verdict = detect_challenge(200, {}, '<div class="cf-turnstile" data-sitekey="k">')
    assert verdict.interactive is True


@pytest.mark.parametrize(
    "status,headers",
    [
        (200, {"set-cookie": "datadome=abc"}),
        (200, {"x-datadome": "protected"}),
        (200, {"x-datadome-cid": "abc"}),
    ],
)
def test_vendor_presence_on_a_good_response_is_not_a_block(status, headers):
    """The vendor guarding a site is not the vendor stopping you.

    Protected origins emit their headers and cookies on perfectly successful
    responses. Treating that as a challenge makes the client useless on exactly
    the sites it exists for — found by running against a live protected site,
    which returned 200 with a full payload and was wrongly reported as blocked.
    """
    verdict = detect_challenge(status, headers, "<html>" + "x" * 6000 + "</html>")
    assert verdict.vendor == "datadome"  # still recorded, as metadata
    assert verdict.serving is False
    assert not verdict


def test_vendor_presence_plus_refusal_status_is_a_block():
    verdict = detect_challenge(403, {"x-datadome": "protected"}, "")
    assert verdict.serving is True


def test_challenge_page_in_body_is_a_block_at_any_status():
    verdict = detect_challenge(200, {}, "<html>captcha-delivery.com</html>")
    assert verdict.serving is True


def test_challenge_is_never_retried():
    transport = FakeTransport(
        html_result("no", status=429, headers={"x-datadome": "protected"})
    )
    fetcher = HttpFetcher(transport=transport, max_attempts=5, raise_on_challenge=False)
    fetcher(Request("https://example.com/page"))
    assert len(transport.requests) == 1, "a challenge must not be retried"


def test_transient_error_is_retried():
    transport = FakeTransport(html_result("busy", status=503), html_result("ok"))
    fetcher = HttpFetcher(transport=transport, max_attempts=3)
    capture = fetcher(Request("https://example.com/page"))
    assert capture.ok
    assert len(transport.requests) == 2


def test_classify_names_the_move():
    assert classify(429, {}, "") == "rate_limited"
    assert classify(200, {}, "") == "empty"
    assert classify(403, {"cf-mitigated": "challenge"}, "") == "challenge"


# --------------------------------------------------------------------------
# provenance


def test_capture_records_provenance_and_probe_marks():
    transport = FakeTransport(html_result(NEXT_DATA_PAGE))
    capture = HttpFetcher(transport=transport)(Request("https://example.com/page"))

    assert capture.response.body_sha256
    assert capture.response.body_len == len(NEXT_DATA_PAGE.encode())
    assert capture.fetcher.client_name == "fake"
    assert "__NEXT_DATA__" in capture.probe.state_blob_markers
    assert capture.fetched_at.tzinfo is not None

    round_tripped = json.loads(capture.to_json())
    assert "body" not in round_tripped, "the payload must not ride in the metadata"


def test_body_is_raw_bytes_not_decoded_text():
    transport = FakeTransport(
        TransportResult(200, {"Content-Type": "text/html"}, b"\xff\xfe raw", "u")
    )
    capture = HttpFetcher(transport=transport)(Request("https://example.com"))
    assert isinstance(capture.body, bytes)
    assert capture.body == b"\xff\xfe raw"


# --------------------------------------------------------------------------
# policy


def test_robots_disallow_is_enforced_by_default():
    policy = RobotsPolicy(get_robots_txt=lambda host: "User-agent: *\nDisallow: /no")
    with pytest.raises(RobotsDisallowed):
        policy.enforce("https://example.com/no/thing")


def test_robots_override_requires_a_recorded_reason():
    policy = RobotsPolicy(
        get_robots_txt=lambda host: "User-agent: *\nDisallow: /",
        override_reason="site owner granted access by email",
    )
    outcome = policy.enforce("https://example.com/x")
    assert outcome.robots_allowed is False
    assert outcome.robots_override_reason  # the override is attributable


def test_politeness_is_per_host_not_global():
    pace = Politeness(min_interval=0.05, jitter=0.0)
    pace.wait("https://a.com/1")
    slept = pace.wait("https://b.com/1")  # different host must not be throttled
    assert slept == 0.0


# --------------------------------------------------------------------------
# caching


def test_cache_makes_reruns_free():
    calls = []

    def fetcher(request):
        calls.append(request.url)
        return Capture.from_body(request, b"payload")

    store = CaptureStore.in_memory()
    wrapped = cached(fetcher, store)
    wrapped(Request("https://example.com/a"))
    wrapped(Request("https://example.com/a"))
    assert len(calls) == 1


def test_cache_key_ignores_noise_but_not_meaning():
    same = request_digest(Request("https://x.com/p?b=2&a=1")) == request_digest(
        Request("https://x.com/p?a=1&b=2&utm_source=nl")
    )
    assert same
    assert request_digest(Request("https://x.com/p?a=1")) != request_digest(
        Request("https://x.com/p?a=2")
    )


def test_failures_are_not_cached():
    store = CaptureStore.in_memory()
    wrapped = cached(
        lambda request: Capture.from_body(request, b"nope", status=403), store
    )
    wrapped(Request("https://example.com/a"))
    assert len(store) == 0, "caching a block makes an unblocked retry impossible"


def test_bodies_are_deduplicated_across_urls():
    store = CaptureStore.in_memory()
    wrapped = cached(lambda request: Capture.from_body(request, b"same"), store)
    wrapped(Request("https://example.com/a"))
    wrapped(Request("https://example.com/b"))
    assert len(store) == 2
    assert len(store.bodies) == 1


def test_capture_survives_a_store_round_trip():
    store = CaptureStore.in_memory()
    original = Capture.from_body(Request("https://example.com/a"), b"hello")
    digest = store.add(original)
    restored = store[digest]
    assert restored.body == b"hello"
    assert restored.response.body_sha256 == original.response.body_sha256
    assert restored.request.url == original.request.url


# --------------------------------------------------------------------------
# url canonicalization


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://Example.com:443/a", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/a?utm_source=x", "https://example.com/a"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://example.com/a/../b", "https://example.com/b"),
    ],
)
def test_canonicalize_url(raw, expected):
    assert canonicalize_url(raw) == expected

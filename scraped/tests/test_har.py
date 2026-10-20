"""Internal-API discovery from HAR files (scraped#5). Offline: synthetic HARs only."""

import base64
import json
import zipfile

import pytest

from scraped.acquire import client_code, discover_api, har_captures, replay_request


def _entry(url, mime, text, *, method="GET", status=200, headers=(), post=None, b64=False):
    content = {"mimeType": mime}
    if b64:
        content.update(text=base64.b64encode(text.encode()).decode(), encoding="base64")
    else:
        content["text"] = text
    request = {"method": method, "url": url, "headers": [{"name": k, "value": v} for k, v in headers]}
    if post is not None:
        request["postData"] = {"mimeType": "application/json", "text": post}
    return {"request": request, "response": {"status": status, "headers": [], "content": content}}


GQL = json.dumps({"operationName": "Listing", "variables": {"id": 7}, "query": "query Listing { item { name } }"})
HAR = {
    "log": {
        "entries": [
            _entry("https://example.com/listing/7", "text/html", "<h1>Café Lumière</h1><b>12.50</b>"),
            _entry("https://example.com/static/app.js", "application/javascript", "'Café Lumière' 12.50"),
            _entry(
                "https://example.com/graphql",
                "application/json",
                json.dumps({"data": {"item": {"name": "Café Lumière", "price": "12.50"}}}),
                method="POST",
                post=GQL,
                headers=[("Cookie", "session=fictional"), ("Content-Type", "application/json"), (":authority", "example.com")],
            ),
            _entry("https://example.com/api/other", "application/json", '{"x": "12.50"}', b64=True),
            _entry("https://example.com/api/gone", "application/json", '{"name": "Café Lumière"}', status=404),
        ]
    }
}
TARGETS = ["café lumière", "12.50"]


def test_the_json_call_carrying_every_target_ranks_first():
    ranked = discover_api(HAR, TARGETS)
    assert [c.request.url for c in ranked] == [
        "https://example.com/graphql",
        "https://example.com/listing/7",
        "https://example.com/api/other",
    ]
    best = ranked[0]
    assert best.payload == "structured" and set(best.found) == set(TARGETS)
    assert best.request.method == "POST" and json.loads(best.request.body)["operationName"] == "Listing"


def test_escaped_json_text_still_matches():
    escaped = {"log": {"entries": [_entry("https://example.com/api", "application/json", '{"n": "Caf\\u00e9 Lumi\\u00e8re"}')]}}
    assert [c.found for c in discover_api(escaped, TARGETS[:1])] == [(TARGETS[0],)]


def test_assets_only_on_request():
    urls = [c.request.url for c in discover_api(HAR, TARGETS, include_assets=True)]
    assert "https://example.com/static/app.js" in urls


def test_base64_bodies_are_decoded():
    other = [c for c in har_captures(HAR) if c.request.url.endswith("/api/other")][0]
    assert other.body == b'{"x": "12.50"}'


def test_a_playwright_zip_with_attached_bodies(tmp_path):
    har = {"log": {"entries": [_entry("https://example.com/api/items", "application/json", "")]}}
    del har["log"]["entries"][0]["response"]["content"]["text"]
    har["log"]["entries"][0]["response"]["content"]["_file"] = "abc123.json"
    path = tmp_path / "session.har.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("har.har", json.dumps(har))
        archive.writestr("abc123.json", '{"items": [{"name": "Blue Kettle"}]}')
    assert [c.request.url for c in discover_api(path, ["blue kettle"])] == ["https://example.com/api/items"]


def test_a_plain_har_file(tmp_path):
    path = tmp_path / "s.har"
    path.write_text(json.dumps(HAR), encoding="utf-8")
    assert len(list(har_captures(path))) == 5


def test_replays_drop_credentials_and_pseudo_headers():
    best = discover_api(HAR, TARGETS)[0]
    request = replay_request(best)
    assert set(request.headers) == {"Content-Type"}
    assert "Cookie" in replay_request(best, keep_credentials=True).headers
    code = client_code(best)
    assert "fictional" not in code
    compile(code, "<client_code>", "exec")  # runnable as written
    assert "method='POST'" in code and "'https://example.com/graphql'" in code


def test_targets_are_required():
    with pytest.raises(ValueError, match="target"):
        discover_api(HAR, [""])


def test_more_credential_headers_are_dropped():
    from scraped.acquire import Request

    headers = {"X-API-Key": "k", "Proxy-Authorization": "p", "X-Auth-Token": "t", "Accept": "*/*"}
    assert set(replay_request(Request("https://example.com/api", headers=headers)).headers) == {"Accept"}


def test_form_posts_recorded_as_params_keep_their_body():
    entry = _entry("https://example.com/api/search", "application/json", '{"name": "Blue Kettle"}', method="POST")
    entry["request"]["postData"] = {
        "mimeType": "application/x-www-form-urlencoded",
        "params": [{"name": "q", "value": "blue kettle"}, {"name": "page", "value": "1"}],
    }
    [capture] = har_captures({"log": {"entries": [entry]}})
    assert capture.request.body == b"q=blue+kettle&page=1"


def test_content_types_and_sniffing():
    entries = [
        _entry("https://example.com/icon.svg", "image/svg+xml", "<svg>Blue Kettle</svg>"),
        _entry("https://example.com/api/raw", "", '{"name": "Blue Kettle"}'),
        _entry("https://example.com/moved", "text/html", "Blue Kettle", status=302),
    ]
    ranked = discover_api({"log": {"entries": entries}}, ["blue kettle"])
    assert [(c.request.url, c.payload) for c in ranked] == [("https://example.com/api/raw", "structured")]


def test_targets_equal_after_folding_count_once():
    [c] = discover_api(HAR, ["Caf\u00e9 Lumi\u00e8re", "CAF\u00c9 LUMI\u00c8RE", "caf\u00e9 lumi\u00e8re"])[:1]
    assert len(c.found) == 1


def test_bad_bodies_and_boms_do_not_stop_the_reader(tmp_path, caplog):
    bad = _entry("https://example.com/api/a", "application/json", "")
    bad["response"]["content"].update(text="not=base64==!", encoding="base64")
    good = _entry("https://example.com/api/b", "application/json", '{"name": "Blue Kettle"}')
    path = tmp_path / "bom.har"
    path.write_text(json.dumps({"log": {"entries": [bad, good]}}), encoding="utf-8-sig")
    assert [c.request.url for c in discover_api(path, ["blue kettle"])] == ["https://example.com/api/b"]

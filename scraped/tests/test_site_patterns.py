"""Site-pattern memory (scraped#7): public/private split, merge, round trip. Offline."""

from datetime import date
from pathlib import Path
import time

import pytest

from scraped.acquire import SiteProfile, SitePattern, pattern_from_run, recall, remember
from scraped.acquire.site_patterns import default_paths, has_secret, read_patterns, seed_path


@pytest.fixture
def files(tmp_path):
    return {"public": tmp_path / "site-patterns.md", "local": tmp_path / "site-patterns.local.md"}


def test_a_public_pattern_round_trips_and_merges(files):
    remember(SitePattern("shop.example.com", generator="Next.js", notes=("a",)), today=date(2026, 1, 2), **files)
    remember(SitePattern("shop.example.com", payload="state blob", notes=("b",)), today=date(2026, 3, 4), **files)
    got = read_patterns(files["public"])["shop.example.com"]
    assert (got.generator, got.payload, got.notes, got.seen) == ("Next.js", "state blob", ("a", "b"), "2026-03-04")
    assert not files["local"].exists()


@pytest.mark.parametrize("pattern", [
    SitePattern("app.example.com", authenticated=True),
    SitePattern("10.1.2.3"),
    SitePattern("intranet"),
    SitePattern("wiki.corp"),
])
def test_private_patterns_never_reach_the_public_file(files, pattern):
    assert remember(pattern, **files) == files["local"]
    assert not files["public"].exists()
    with pytest.raises(ValueError):
        remember(pattern, private=False, **files)


def test_recall_lets_the_local_entry_override_the_public_one(files):
    remember(SitePattern("example.com", transport="http", cap="10000 results"), **files)
    remember(SitePattern("example.com", transport="browser"), private=True, **files)
    got = recall("example.com", **files)
    assert (got.transport, got.cap) == ("browser", "10000 results")
    assert recall("unknown.example", **files) is None


def test_pattern_from_run_reads_the_profile_and_pagination():
    class Shape:
        kind, params, deep_paging_capped = "cursor", {"size": "first", "cursor": "after"}, True

    got = pattern_from_run(SiteProfile(url="https://x.example/", host="x.example", generator="Gatsby"),
                           transport="http", payload="api", pagination=Shape())
    assert got.pagination == "cursor (size=first, cursor=after)" and got.cap == "deep paging capped"
    assert got.generator == "Gatsby"


def test_the_seed_file_ships_and_holds_no_private_host():
    seed = seed_path()
    assert seed.is_file()
    assert all(not SitePattern(h).host.endswith(".corp") for h in read_patterns(seed))


def test_the_default_files_live_outside_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPED_SITE_PATTERNS_DIR", str(tmp_path / "patterns"))
    public, local = default_paths()
    assert (public.parent, local.name) == (tmp_path / "patterns", "site-patterns.local.md")
    assert remember(SitePattern("shop.example.com", transport="http")) == public
    assert not seed_path().read_text().count("shop.example.com")
    monkeypatch.delenv("SCRAPED_SITE_PATTERNS_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))  # Windows
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import scraped

    public, _ = default_paths()
    assert Path(scraped.__file__).parent not in public.parents
    assert tmp_path in public.parents and not public.parent.exists()


def test_recall_layers_seed_then_public_then_local(tmp_path, files):
    seed = tmp_path / "seed.md"
    remember(SitePattern("x.com", generator="Gatsby", cap="500"), public=seed, local=files["local"])
    remember(SitePattern("x.com", cap="1000"), **files)
    remember(SitePattern("x.com", transport="browser"), private=True, **files)
    got = recall("x.com", seed=seed, **files)
    assert (got.generator, got.cap, got.transport, got.private) == ("Gatsby", "1000", "browser", True)
    assert recall("x.com", seed="", **files).generator == ""


@pytest.mark.parametrize("field, text", [
    ("notes", "send Cookie: sid=9f8e7d6c5b4a"),
    ("notes", "worked with Authorization: Bearer abc123def456"),
    ("notes", "api_key=a1b2c3d4e5f6 in query"),
    ("notes", "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0"),
    ("payload", "api (session_id=7f3a9c2e11)"),
    ("notes", "ghp_abcdefghijklmnopqrstuvwxyz0123"),
    ("notes", "id 3f9a2b7c8d1e4f5a6b7c8d9e0f1a2b3c"),
    ("notes", "login via https://user:pw1234@host/x"),
    ("notes", '{"api_key": "a1b2c3d4e5f6g7"}'),
    ("notes", "password: hunter2"),
    ("notes", "x-api-key: abcdefghijklmnop"),
    ("notes", "accessToken=ab12cd34ef56"),
    ("notes", "Basic dXNlcjpwYXNz"),
])
def test_secret_looking_text_goes_to_the_local_file(files, field, text):
    pattern = SitePattern("shop.example.com", **{field: (text,) if field == "notes" else text})
    assert remember(pattern, **files) == files["local"]
    assert not files["public"].exists()
    with pytest.raises(ValueError, match="credential"):
        remember(pattern, private=False, **files)


@pytest.mark.parametrize("text", [
    "cursor (size=variables.first, cursor=variables.after)", "deep paging capped",
    "page via ?pageToken=", "basic pagination", "token pagination", "cookie banner blocks the DOM",
    "10000 results", "state blob in __NEXT_DATA__", "outside: 2024-10-01 snapshot", "author: 12345678",
    "persisted-query-hash-sha256-in-extensions-field", "session: 30 minutes", "sid=123",
])
def test_ordinary_notes_are_not_mistaken_for_secrets(text):
    assert not has_secret(text)


def test_a_probe_shaped_host_is_normalised_and_stays_public(files):
    profile = SiteProfile(url="https://Shop.Example.com/", host="https://Shop.Example.com")
    pattern = pattern_from_run(profile, transport="http", payload="api")
    assert pattern.host == "shop.example.com"
    assert remember(pattern, **files) == files["public"]
    assert recall("https://shop.example.com:443", **files).payload == "api"


@pytest.mark.parametrize("bad", ["l1\n## secret.corp\n- authenticated: yes", "a\r## x.corp", "a b"])
def test_a_line_break_cannot_forge_a_section(bad):
    with pytest.raises(ValueError):
        SitePattern("y.com", notes=(bad,))
    with pytest.raises(ValueError):
        SitePattern("y.com", cap=bad)


def test_recalled_local_facts_are_never_promoted_to_the_public_file(files):
    from dataclasses import replace

    remember(SitePattern("x.com", transport="http"), **files)
    remember(SitePattern("x.com", notes=("internal admin path",)), private=True, **files)
    assert remember(replace(recall("x.com", **files), cap="1000"), **files) == files["local"]
    assert "internal admin" not in files["public"].read_text()


@pytest.mark.parametrize("host", ["wiki.corp.", "foo.intranet", "127.1", "0x7f.1", "a.test", "[::1]:8080"])
def test_internal_host_spellings_are_private(host):
    from scraped.acquire import is_private_host

    assert is_private_host(host)


@pytest.mark.parametrize("unit", ["a", "a-", "a_", "aB", "x:", "a."])
def test_the_detector_stays_fast_on_long_runs(unit):
    text = unit * (50_000 // len(unit))
    start = time.perf_counter()
    has_secret(text)
    assert time.perf_counter() - start < 1


def test_a_local_file_left_beside_the_skill_is_flagged(tmp_path, monkeypatch):
    from scraped.acquire import site_patterns

    legacy = tmp_path / "references" / "site-patterns.local.md"
    legacy.parent.mkdir()
    legacy.write_text("")
    monkeypatch.setattr(site_patterns, "seed_path", lambda: legacy.with_name("site-patterns.md"))
    monkeypatch.setenv("SCRAPED_SITE_PATTERNS_DIR", str(tmp_path / "user"))
    site_patterns._warn_if_legacy_local.cache_clear()
    with pytest.warns(UserWarning, match="no longer read"):
        recall("x.com", seed="")

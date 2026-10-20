"""Site-pattern memory (scraped#7): public/private split, merge, round trip. Offline."""

from datetime import date

import pytest

from scraped.acquire import SiteProfile, SitePattern, pattern_from_run, recall, remember
from scraped.acquire.site_patterns import default_paths, read_patterns


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


def test_the_public_file_ships_and_holds_no_private_host():
    public, _ = default_paths()
    assert public.is_file()
    assert all(not SitePattern(h).host.endswith(".corp") for h in read_patterns(public))

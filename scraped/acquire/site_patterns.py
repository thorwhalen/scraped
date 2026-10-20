"""Site-pattern memory: what one job learned about a site, kept for the next one.

Each acquisition job rediscovers the same facts about a site: which generator
built it, which transport and payload rung worked, how it paginates and where
paging is capped. This module keeps them in two Markdown files, one section per
host, which a person and an agent can both read:

- ``site-patterns.md`` -- committed and public: fingerprints, rungs, pagination
  shapes, known caps of public sites;
- ``site-patterns.local.md`` -- gitignored, beside it: anything private
  (authenticated flows, internal hosts). The split is the point of the design.

:func:`remember` is the post-run step. It routes a pattern to the local file
whenever it is private (:attr:`SitePattern.authenticated`, or an internal host,
see :func:`is_private_host`), so a private fact cannot land in the public file by
default. :func:`recall` reads both, the local entry overriding the public one.

>>> import tempfile, os
>>> folder = tempfile.mkdtemp()
>>> public, local = (os.path.join(folder, n) for n in ("p.md", "p.local.md"))
>>> str(remember(SitePattern("shop.example.com", generator="Next.js", transport="http",
...                      payload="state blob"), public=public, local=local)) == public
True
>>> str(remember(SitePattern("wiki.corp", transport="browser", authenticated=True),
...          public=public, local=local)) == local
True
>>> recall("shop.example.com", public=public, local=local).payload
'state blob'
>>> "wiki.corp" in open(public).read()
False
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, fields, replace
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Sequence

#: The public file's name; the private overlay is the same name with ``.local``.
SITE_PATTERNS_NAME = "site-patterns.md"
LOCAL_SITE_PATTERNS_NAME = "site-patterns.local.md"

#: Host suffixes that name a private network, never a public site.
PRIVATE_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".intranet",
    ".private",
    ".lan",
    ".corp",
    ".home.arpa",
    ".localhost",
    ".test",
    ".invalid",
)
#: A public top-level label: letters, or an IDNA ``xn--`` label. Anything else is not a public site.
_PUBLIC_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]+)$")
#: Characters that would break a one-line Markdown field: line breaks and other controls.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")

_HEADER = """# Site patterns

What earlier jobs learned about each site, one section per host, written by
`scraped.acquire.remember` after a run. Public sites only: authenticated flows and
internal hosts go to the gitignored `site-patterns.local.md` beside this file.
"""

_SECTION_RE = re.compile(r"^## (?P<host>\S+)\s*$")
_FIELD_RE = re.compile(r"^- (?P<key>[a-z_]+): (?P<value>.*)$")


@dataclass(frozen=True)
class SitePattern:
    """What is known about one host. Every field but ``host`` is optional."""

    host: str
    generator: str = ""
    transport: str = (
        ""  # the transport rung that worked: http, impersonating http, browser...
    )
    payload: str = ""  # the payload rung that carried the data: api, state blob, dom...
    pagination: str = ""  # e.g. "cursor (size=variables.first, cursor=variables.after)"
    cap: str = ""  # a known paging or result cap, e.g. "10000 results"
    seen: str = ""  # ISO date of the run that last confirmed it
    notes: tuple[str, ...] = ()
    authenticated: bool = False  # needed a login: always private
    private: bool = (
        False  # came from (or belongs in) the local file: never written public
    )

    def __post_init__(self):
        object.__setattr__(self, "host", normalize_host(self.host))
        for name in (*_TEXT_FIELDS, "notes"):
            values = getattr(self, name)
            for value in values if name == "notes" else (values,):
                if _CONTROL_RE.search(value):
                    raise ValueError(
                        f"{name} of {self.host} holds a line break or control character"
                    )

    def merged(self, newer: "SitePattern") -> "SitePattern":
        """This pattern updated by ``newer``: its set fields win, notes accumulate."""
        updates = {
            f.name: getattr(newer, f.name)
            for f in fields(self)
            if f.name not in ("host", "notes") and getattr(newer, f.name)
        }
        notes = tuple(dict.fromkeys((*self.notes, *newer.notes)))
        return replace(self, **updates, notes=notes)


def normalize_host(host: str) -> str:
    """The bare, lower-case host name of ``host``, which may be a URL or ``host:port``.

    >>> normalize_host("https://user:pw@Shop.Example.com.:8443/path")
    'shop.example.com'
    """
    text = host.strip()
    parts = urlsplit(text if "://" in text else f"//{text}")
    try:
        name = parts.hostname or ""
    except ValueError:
        name = ""
    name = name.rstrip(".")
    if not name or _CONTROL_RE.search(name) or " " in name:
        raise ValueError(f"not a host name: {host!r}")
    return name


def is_private_host(host: str) -> bool:
    """Whether ``host`` names a private network rather than a public site.

    Conservative: a name whose last label is not a public top-level label (an IP
    shorthand like ``127.1``, a dotless name) counts as private.

    >>> [is_private_host(h) for h in ("example.com", "https://10.0.0.7:80", "intranet",
    ...                                "wiki.corp.", "127.1", "[::1]:8080")]
    [False, True, True, True, True, True]
    """
    name = normalize_host(host)
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        tld = name.rsplit(".", 1)[-1]
        return (
            "." not in name
            or name.endswith(PRIVATE_HOST_SUFFIXES)
            or not _PUBLIC_TLD_RE.match(tld)
        )
    return not address.is_global


def is_private(pattern: SitePattern) -> bool:
    """Whether ``pattern`` belongs in the local file: marked private, authenticated, or internal."""
    return pattern.private or pattern.authenticated or is_private_host(pattern.host)


def default_paths() -> tuple[Path, Path]:
    """``(public, local)``: the files that ship beside the ``scraped-acquire`` skill."""
    folder = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "skills"
        / "scraped-acquire"
        / "references"
    )
    return folder / SITE_PATTERNS_NAME, folder / LOCAL_SITE_PATTERNS_NAME


def read_patterns(path: str | Path) -> dict[str, SitePattern]:
    """The patterns in one file, by host; empty when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return {}
    found: dict[str, dict] = {}
    current: Optional[dict] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if section := _SECTION_RE.match(line):
            current = found.setdefault(
                section["host"], {"host": section["host"], "notes": []}
            )
        elif current is not None and (item := _FIELD_RE.match(line)):
            key, value = item["key"], item["value"].strip()
            if key == "note":
                current["notes"].append(value)
            elif key == "authenticated":
                current["authenticated"] = value.lower() == "yes"
            elif key in _TEXT_FIELDS:
                current[key] = value
    return {
        host: SitePattern(**{**kw, "notes": tuple(kw["notes"])})
        for host, kw in found.items()
    }


_TEXT_FIELDS = ("generator", "transport", "payload", "pagination", "cap", "seen")


def _render(pattern: SitePattern) -> str:
    lines = [f"## {pattern.host}", ""]
    lines += [
        f"- {k}: {getattr(pattern, k)}" for k in _TEXT_FIELDS if getattr(pattern, k)
    ]
    if pattern.authenticated:
        lines.append("- authenticated: yes")
    lines += [f"- note: {note}" for note in pattern.notes]
    return "\n".join(lines) + "\n"


def write_patterns(path: str | Path, patterns: Mapping[str, SitePattern]) -> None:
    """Write ``patterns`` to ``path``, sorted by host, under the standard header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(_render(patterns[host]) for host in sorted(patterns))
    path.write_text(_HEADER + ("\n" + body if body else ""), encoding="utf-8")


def recall(
    host: str, *, public: str | Path | None = None, local: str | Path | None = None
) -> Optional[SitePattern]:
    """What is known about ``host``: the public entry updated by the local one, or None.

    A result that drew on the local file is marked ``private``, so passing it back to
    :func:`remember` can never copy the local facts into the public file.
    """
    dflt_public, dflt_local = default_paths()
    host = normalize_host(host)
    public_entry = read_patterns(public or dflt_public).get(host)
    local_entry = read_patterns(local or dflt_local).get(host)
    if local_entry is not None:
        local_entry = replace(local_entry, private=True)
    known = [entry for entry in (public_entry, local_entry) if entry is not None]
    if not known:
        return None
    pattern = known[0]
    for newer in known[1:]:
        pattern = pattern.merged(newer)
    return pattern


def remember(
    pattern: SitePattern,
    *,
    public: str | Path | None = None,
    local: str | Path | None = None,
    private: Optional[bool] = None,
    today: Optional[date] = None,
) -> Path:
    """The post-run step: merge ``pattern`` into its file and return that file's path.

    It goes to the local file when ``private`` is true, or when it is left as None and
    :func:`is_private` says so. Passing ``private=False`` for an authenticated or
    internal pattern raises ``ValueError``: that is the leak this split exists to stop.
    """
    dflt_public, dflt_local = default_paths()
    if private is False and is_private(pattern):
        raise ValueError(
            f"{pattern.host} is private (authenticated or an internal host); "
            "it can only be remembered in the local file"
        )
    target = Path(
        (local or dflt_local)
        if (private or is_private(pattern))
        else (public or dflt_public)
    )
    if not pattern.seen:
        pattern = replace(pattern, seen=(today or date.today()).isoformat())
    patterns = read_patterns(target)
    known = patterns.get(pattern.host)
    patterns[pattern.host] = known.merged(pattern) if known else pattern
    write_patterns(target, patterns)
    return target


def pattern_from_run(
    profile,
    *,
    transport: str,
    payload: str,
    pagination=None,
    cap: str = "",
    notes: Sequence[str] = (),
    authenticated: bool = False,
) -> SitePattern:
    """A pattern from a run's :class:`~scraped.acquire.SiteProfile` and what worked.

    ``pagination`` is a :func:`~scraped.acquire.pagination_shape` result, or None.
    """
    paging = ""
    if pagination is not None and getattr(pagination, "kind", None):
        params = ", ".join(f"{k}={v}" for k, v in (pagination.params or {}).items())
        paging = f"{pagination.kind} ({params})" if params else pagination.kind
        if not cap and getattr(pagination, "deep_paging_capped", False):
            cap = "deep paging capped"
    return SitePattern(
        host=profile.host,
        generator=profile.generator or "",
        transport=transport,
        payload=payload,
        pagination=paging,
        cap=cap,
        notes=tuple(notes),
        authenticated=authenticated,
    )

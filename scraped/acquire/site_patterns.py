"""Site-pattern memory: what one job learned about a site, kept for the next one.

Each acquisition job rediscovers the same facts about a site: which generator
built it, which transport and payload rung worked, how it paginates and where
paging is capped. This module keeps them in Markdown files, one section per
host, which a person and an agent can both read:

- ``site-patterns.md`` -- public: fingerprints, rungs, pagination shapes, known
  caps of public sites;
- ``site-patterns.local.md`` -- beside it, never shared: anything private
  (authenticated flows, internal hosts, text that looks like a credential). The
  split is the point of the design.

Both live in a user data folder (see :func:`default_paths`), since an installed
package's own folder is read-only and replaced on upgrade. The ``site-patterns.md``
shipped with the ``scraped-acquire`` skill is seed data (:func:`seed_path`): read
first, never written unless passed explicitly as ``public``, which is what a
contributor does to commit a pattern back to the package.

:func:`remember` is the post-run step. It routes a pattern to the local file
whenever it is private (:attr:`SitePattern.authenticated`, an internal host, see
:func:`is_private_host`, or a field that looks like a secret, see
:func:`secret_fields`), so a private fact cannot land in the public file by
default. :func:`recall` reads the seed, then the public file, then the local one,
each later entry overriding the earlier.

>>> import tempfile, os
>>> folder = tempfile.mkdtemp()
>>> public, local = (os.path.join(folder, n) for n in ("p.md", "p.local.md"))
>>> str(remember(SitePattern("shop.example.com", generator="Next.js", transport="http",
...                      payload="state blob"), public=public, local=local)) == public
True
>>> str(remember(SitePattern("shop.example.com", notes=("cookie: sid=9f8e7d6c5b4a",)),
...          public=public, local=local)) == local
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
import os
import re
import warnings
from dataclasses import dataclass, fields, replace
from functools import lru_cache
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Sequence

#: The public file's name; the private overlay is the same name with ``.local``.
SITE_PATTERNS_NAME = "site-patterns.md"
LOCAL_SITE_PATTERNS_NAME = "site-patterns.local.md"
#: Environment variable naming the folder of the user's two files (see :func:`default_paths`).
SITE_PATTERNS_DIR_ENVVAR = "SCRAPED_SITE_PATTERNS_DIR"
#: The app data subfolder holding them when the environment variable is unset.
SITE_PATTERNS_SUBDIR = "site-patterns"

#: Before a credential-like key: up to four ``word_``/``word-``/``word.`` or camelCase
#: prefixes (``x-api-key``, ``accessToken``), starting at a word start (so ``outside``
#: is not ``sid``). Bounded, so matching stays linear on long runs.
_KEY_PREFIX = r"(?<![a-z0-9])(?:[a-z0-9]{1,32}[_.-]|[a-z][a-z0-9]{0,31}(?=(?-i:[A-Z]))){0,4}"
#: After it: an optional ``id``/``key``/``token`` suffix, a closing quote (JSON), ``=`` or ``:``.
_KEY_TAIL = r"(?:[_.-]?(?:id|key|token))?[\"']?\s*[=:]\s*[\"']?"

#: Shapes of a credential in free text: a header or cookie line, a secret-named key
#: with a value (any value for a password, secret or key; an opaque one holding a
#: digit for a token, session or auth key), credentials in a URL, a JWT, a known key
#: prefix, or a long opaque string with at least four digits. Errs on the side of
#: routing to the local file: a false positive keeps a harmless fact private, a false
#: negative publishes a secret.
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(?<![a-z0-9-])(?:proxy-)?(?:authorization|cookie|set-cookie)\s*:"),
    re.compile(
        r"(?i)\b(?:bearer|basic)\s+(?=[a-z0-9._~+/=-]{0,512}(?-i:[A-Z0-9]))"
        r"[a-z0-9._~+/=-]{8,}"
    ),
    re.compile(
        r"(?i)" + _KEY_PREFIX + r"(?:pass(?:wor)?d|pass|pwd|secret|api[_-]?key"
        r"|access[_-]?key|private[_-]?key|credentials?)" + _KEY_TAIL + r"[^\s\"',;]{4,}"
    ),
    re.compile(
        r"(?i)" + _KEY_PREFIX + r"(?:token|session|sid|auth)" + _KEY_TAIL
        + r"(?=[^\s\"',;]{0,512}\d)[^\s\"',;]{8,}"
    ),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]{0,15}://[^/\s:@]{1,64}:[^/\s@]{1,128}@"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}"),
    re.compile(
        r"\b(?:(?:sk|pk|rk)[-_](?:live[-_]|test[-_])?[a-zA-Z0-9]{8,}|gh[pousr]_[a-zA-Z0-9]{20,}"
        r"|glpat-[a-zA-Z0-9_-]{16,}|AIza[a-zA-Z0-9_-]{30,}|AKIA[0-9A-Z]{16}"
        r"|xox[abposr]-[a-zA-Z0-9-]{10,})"
    ),
    re.compile(
        r"(?<![a-zA-Z0-9_-])(?=(?:[a-zA-Z_-]*\d){4})"
        r"(?![a-zA-Z0-9_]*-[a-zA-Z0-9_]*-[a-zA-Z0-9_]*-)[a-zA-Z0-9_-]{32,}"
    ),
)

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
`scraped.acquire.remember` after a run. Public sites only: authenticated flows,
internal hosts and anything that looks like a credential go to
`site-patterns.local.md` beside this file, which is never shared.
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


def has_secret(text: str) -> bool:
    """Whether ``text`` looks like it holds a credential (see :data:`SECRET_TEXT_PATTERNS`).

    >>> [has_secret(t) for t in ("cursor (size=first, cursor=after)", "10000 results",
    ...                          "needs Cookie: sid=abc", "api_key=a1b2c3d4e5",
    ...                          "page via ?pageToken=", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0")]
    [False, False, True, True, False, True]
    """
    return any(p.search(text) for p in SECRET_TEXT_PATTERNS)


def secret_fields(pattern: SitePattern) -> tuple[str, ...]:
    """The names of ``pattern``'s text fields (notes included) that look like secrets."""
    found = [name for name in _TEXT_FIELDS if has_secret(getattr(pattern, name))]
    if any(has_secret(note) for note in pattern.notes):
        found.append("notes")
    return tuple(found)


def is_private(pattern: SitePattern) -> bool:
    """Whether ``pattern`` belongs in the local file.

    True when it is marked private, authenticated, names an internal host, or holds
    text that looks like a credential.
    """
    return (
        pattern.private
        or pattern.authenticated
        or is_private_host(pattern.host)
        or bool(secret_fields(pattern))
    )


def seed_path() -> Path:
    """The ``site-patterns.md`` shipped beside the ``scraped-acquire`` skill: seed data.

    :func:`recall` reads it first; :func:`remember` writes to it only when it is passed
    explicitly as ``public`` (a contributor committing a pattern back to the package).
    """
    return (
        Path(__file__).resolve().parent.parent
        / "data"
        / "skills"
        / "scraped-acquire"
        / "references"
        / SITE_PATTERNS_NAME
    )


def default_paths() -> tuple[Path, Path]:
    """``(public, local)``: the user's own files, in a writable data folder.

    The folder is ``$SCRAPED_SITE_PATTERNS_DIR`` when set, else the ``site-patterns``
    subfolder of scraped's app data folder (``~/.local/share/scraped`` on Linux).
    Nothing is created until :func:`remember` writes.
    """
    folder = os.environ.get(SITE_PATTERNS_DIR_ENVVAR)
    if not folder:
        from config2py import get_app_data_folder

        folder = Path(get_app_data_folder("scraped")) / SITE_PATTERNS_SUBDIR
    folder = Path(folder).expanduser()
    return folder / SITE_PATTERNS_NAME, folder / LOCAL_SITE_PATTERNS_NAME


@lru_cache(maxsize=1)
def _warn_if_legacy_local(local: Path) -> None:
    """Warn once when a local file still sits where scraped < 0.0.27 kept it."""
    legacy = seed_path().with_name(LOCAL_SITE_PATTERNS_NAME)
    if legacy.is_file() and not local.is_file():
        warnings.warn(
            f"{legacy} is no longer read; move it to {local} to keep its private "
            "patterns (site-pattern files now default to a user data folder)",
            stacklevel=3,
        )


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
    host: str,
    *,
    public: str | Path | None = None,
    local: str | Path | None = None,
    seed: str | Path | None = None,
) -> Optional[SitePattern]:
    """What is known about ``host``, or None.

    The seed entry (``seed``, default :func:`seed_path`), updated by the public entry,
    updated by the local one. Pass ``seed=""`` to skip the seed. A result that drew on
    the local file is marked ``private``, so passing it back to :func:`remember` can
    never copy the local facts into the public file.
    """
    dflt_public, dflt_local = default_paths()
    host = normalize_host(host)
    public = Path(public or dflt_public)
    seed = seed_path() if seed is None else seed
    seed_entry = (
        read_patterns(seed).get(host)
        if seed and Path(seed).resolve() != public.resolve()
        else None
    )
    public_entry = read_patterns(public).get(host)
    if local is None:
        _warn_if_legacy_local(dflt_local)
    local_entry = read_patterns(local or dflt_local).get(host)
    if local_entry is not None:
        local_entry = replace(local_entry, private=True)
    known = [
        entry
        for entry in (seed_entry, public_entry, local_entry)
        if entry is not None
    ]
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
    internal pattern, or one holding secret-looking text, raises ``ValueError``: that
    is the leak this split exists to stop. The files default to :func:`default_paths`;
    pass ``public=seed_path()`` to commit a public pattern back to the package.
    """
    dflt_public, dflt_local = default_paths()
    if private is False and is_private(pattern):
        secrets = secret_fields(pattern)
        why = (
            f"its {', '.join(secrets)} look like a credential"
            if secrets
            else "authenticated or an internal host"
        )
        raise ValueError(
            f"{pattern.host} is private ({why}); "
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

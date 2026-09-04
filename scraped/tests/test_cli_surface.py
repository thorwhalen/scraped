"""Characterization tests pinning the ``scraped`` command-line surface.

``cli_goldens/scraped.json`` was recorded from the *argh* implementation of
:func:`scraped.tools.main` before the migration to :mod:`cw`, with
``cw.testing.characterize``. Every assertion below compares today's CLI against
the grammar argh produced, not against something written by hand afterwards.

Two things the golden alone would not catch, pinned here explicitly:

* **Exit codes.** ``cw.dispatch`` *returns* the exit code where argh exited by
  itself, so ``main()`` must hand it back to the console-script shim and the
  ``__main__`` guard must ``raise SystemExit(main())``. Drop either and every
  argument error starts exiting 0 -- invisible to every other test here.
* **``download-site``'s grammar.** That subcommand's ``--help`` body renders
  ``DFLT_ROOTDIR``, which resolves under the running user's home directory, so
  the recorded body is neither portable nor safe to commit. The case is left out
  of the golden and its *usage line* -- which carries every flag, short flag and
  ``nargs`` and no path at all -- is asserted directly instead.

The golden replays non-strictly: ``--help`` bodies are compared but a pure
formatting difference is reported rather than failed, because CPython rewrites
argparse's own option column between versions. At migration time the *strict*
comparison was empty on CPython 3.10 and 3.12 alike.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cw.testing import assert_replay, normalise_usage

GOLDEN_PATH = Path(__file__).parent / "cli_goldens" / "scraped.json"

# Recorded from argh, before the migration. `scraped` has no __main__.py, so the
# console script is the only entry point there is.
DOWNLOAD_SITE_USAGE = (
    "usage: scraped download-site [-h] [-u [URL_TO_FILEPATH]] [-d DEPTH] "
    "[-f FILTER_URLS] [-m] [-v VERBOSITY] [-r ROOTDIR] url"
)


def test_cli_surface_matches_the_argh_recorded_golden():
    """The whole grammar, replayed against what argh produced."""
    assert_replay(json.loads(GOLDEN_PATH.read_text(encoding="utf-8")))


def test_golden_carries_no_machine_specific_paths():
    """A golden that names an absolute path can only replay on one computer."""
    raw = GOLDEN_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in raw and "\\\\Users\\\\" not in raw
    assert json.loads(raw)["prog"] == ["scraped"]


def _run(*argv):
    return subprocess.run(
        ["scraped", *argv], capture_output=True, text=True, timeout=120
    )


def test_download_site_grammar_is_unchanged():
    """The one subcommand whose help body cannot be committed verbatim."""
    r = _run("download-site", "--help")
    assert r.returncode == 0
    assert normalise_usage(r.stdout) == normalise_usage(DOWNLOAD_SITE_USAGE)


def test_no_arguments_prints_usage_to_stdout_and_exits_zero():
    """argh's behaviour, preserved. Plain argparse would exit 2 to stderr."""
    r = _run()
    assert r.returncode == 0
    assert r.stdout.startswith("usage: scraped")
    assert r.stderr == ""


@pytest.mark.parametrize(
    "argv",
    [
        ("no-such-command",),
        ("markdown-of-site", "--no-such-flag"),
        ("markdown-of-site",),  # missing the required positional
    ],
)
def test_argument_errors_exit_two(argv):
    """Guards `raise SystemExit(main())` / `return cw.dispatch(...)`.

    Without them the exit code is swallowed and every one of these exits 0.
    """
    assert _run(*argv).returncode == 2


def test_commands_list_is_what_the_parser_dispatches():
    """`COMMANDS` is the single source of truth the help text is built from."""
    from scraped.tools import COMMANDS

    names = {f.__name__ for f in COMMANDS}
    assert names == {"markdown_of_site", "download_site", "scrape_multiple_sites"}

    help_text = _run("--help").stdout
    for cli_name in ("markdown-of-site", "download-site", "scrape-multiple-sites"):
        assert cli_name in help_text

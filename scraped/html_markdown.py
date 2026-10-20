"""Light HTML -> Markdown conversion: one document in, one Markdown string out.

This module imports only ``bs4`` and ``markdownify``, never ``scrapy`` or
``hubcap``, so a caller that only needs the converter (e.g. ``contaix``) can use
it without paying for the crawler stack::

    >>> from scraped.html_markdown import html_to_markdown_str
    >>> html_to_markdown_str("<h1>Title</h1><p>some_identifier</p>")
    '# Title\\n\\nsome_identifier\\n'

:func:`scraped.html_to_markdown` (several documents, files, directories, saving)
is built on the same helpers, so the two always agree on the defaults.
"""

import re
from collections.abc import Iterator, Mapping
from typing import Tuple

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

#: Tags whose *content* is never part of the document text. ``markdownify``
#: already drops ``script``/``style``, but not ``<title>``, so the whole head is
#: removed before conversion (``html2text``, which this replaced, ignored it too).
NON_CONTENT_TAGS = ("head", "script", "style", "noscript")

#: Defaults for the HTML->Markdown conversion. Override any of them by passing
#: the same keyword to :func:`html_to_markdown_str` or :func:`scraped.html_to_markdown`.
HTML_TO_MARKDOWN_DEFAULTS = {
    "heading_style": "ATX",  # "# Title", not the underlined setext form
    "bullets": "*",
    "escape_underscores": False,  # keeps identifiers like ``foo_bar`` readable
    "escape_asterisks": False,
}

#: An opening (or closing) fence of a fenced code block, allowing markdown's
#: three spaces of leading indentation.
_CODE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _outside_code_fences(markdown: str) -> Iterator[Tuple[bool, str]]:
    """Split *markdown* into ``(is_fenced_code, text)`` chunks.

    An unterminated fence keeps everything after it marked as code, which is the
    conservative choice: whitespace inside code is content.

    >>> list(_outside_code_fences("a\\n```\\nb\\n```\\nc\\n"))
    [(False, 'a\\n'), (True, '```\\nb\\n```\\n'), (False, 'c\\n')]
    """
    chunk: list = []
    fence = ""
    for line in markdown.splitlines(keepends=True):
        match = _CODE_FENCE_RE.match(line)
        if not fence:
            if match:
                yield False, "".join(chunk)
                chunk, fence = [line], match.group(1)[0] * 3
            else:
                chunk.append(line)
        else:
            chunk.append(line)
            if match and match.group(1)[0] * 3 == fence:
                yield True, "".join(chunk)
                chunk, fence = [], ""
    yield bool(fence), "".join(chunk)


def _collapse_blank_runs(markdown: str) -> str:
    """Collapse runs of blank lines to a single one, *outside code blocks*.

    Two blank lines between top-level ``def``s is PEP 8, so a global
    ``re.sub(r"\\n{3,}", "\\n\\n", ...)`` would silently rewrite the source code
    on every technical page scraped. Fenced regions pass through untouched.

    >>> _collapse_blank_runs("a\\n\\n\\n\\nb\\n")
    'a\\n\\nb\\n'
    >>> _collapse_blank_runs("```\\na\\n\\n\\n\\nb\\n```\\n")
    '```\\na\\n\\n\\n\\nb\\n```\\n'
    """
    return "".join(
        text if is_code else re.sub(r"\n{3,}", "\n\n", text)
        for is_code, text in _outside_code_fences(markdown)
    )


def _resolve_markdownify_options(overrides: Mapping) -> dict:
    """Merge *overrides* over :data:`HTML_TO_MARKDOWN_DEFAULTS`, validating names.

    ``MarkdownConverter`` silently ignores options it does not know, so a typo --
    or an option name carried over from ``html2text``, which this module used
    before -- would be a quiet no-op rather than an error. Raise instead.
    """
    known = {k for k in vars(MarkdownConverter.DefaultOptions) if not k.startswith("_")}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise TypeError(
            f"Unknown markdownify option(s): {', '.join(unknown)}. "
            "These are `markdownify.MarkdownConverter` options; the `html2text` "
            "options this function used to accept (body_width, ignore_links, "
            "unicode_snob, ...) no longer apply. "
            f"Valid options are: {', '.join(sorted(known))}."
        )
    return {**HTML_TO_MARKDOWN_DEFAULTS, **overrides}


def _convert_html(converter: MarkdownConverter, html: str) -> str:
    """Convert one HTML string to Markdown, dropping non-content tags first."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()
    return _collapse_blank_runs(converter.convert_soup(soup)).strip() + "\n"


def html_to_markdown_str(html: str | bytes, **markdownify_options) -> str:
    """Convert one HTML document to a clean Markdown string.

    The defaults are :data:`HTML_TO_MARKDOWN_DEFAULTS` (ATX headings, ``*``
    bullets, no underscore/asterisk escaping); the ``<head>``, scripts, styles
    and ``noscript`` blocks are dropped, and runs of blank lines are collapsed
    outside fenced code.

    Unlike :func:`scraped.html_to_markdown`, the input is always taken as HTML
    text: a file path or directory is not read, and bytes that are not UTF-8
    raise ``UnicodeDecodeError`` rather than being skipped.

    :param html: The HTML, as ``str`` or UTF-8 ``bytes``.
    :param markdownify_options: ``markdownify.MarkdownConverter`` options
        overriding the defaults. Unknown names raise ``TypeError``.

    >>> html_to_markdown_str("<ul><li>a</li><li>b</li></ul>")
    '* a\\n* b\\n'
    >>> html_to_markdown_str(b"<h2>T</h2>", heading_style="UNDERLINED")
    'T\\n-\\n'
    """
    if isinstance(html, bytes):
        html = html.decode()
    if not isinstance(html, str):
        raise TypeError(f"html must be str or bytes, not {type(html).__name__}")
    converter = MarkdownConverter(**_resolve_markdownify_options(markdownify_options))
    return _convert_html(converter, html)

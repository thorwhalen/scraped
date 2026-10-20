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
import sys
import threading
from collections.abc import Iterator, Mapping
from typing import Tuple

from bs4 import BeautifulSoup, NavigableString
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

#: BeautifulSoup parser used by default: the stdlib one, no extra dependency.
#: ``parser="lxml"`` (when installed) parses leniently like a browser: omitted
#: ``</li>`` / ``</td>`` ... work.
DFLT_HTML_PARSER = "html.parser"

#: An opening/closing code fence (any indentation: fences nest in list items).
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

#: Recursion limit used (temporarily) to retry a page nested too deeply for the
#: default one: ``markdownify`` recurses once per nesting level. Python < 3.11
#: spends C stack on every Python call, so the retry runs in a thread with
#: :data:`DEEP_HTML_THREAD_STACK_BYTES` of stack, which makes the high limit safe there too.
DEEP_HTML_RECURSION_LIMIT = 100_000
#: Windows refuses a thread stack of 256 MiB or more (CPython's ``THREAD_MAX_STACKSIZE``).
DEEP_HTML_THREAD_STACK_BYTES = (
    (255 if sys.platform == "win32" else 512) * 1024 * 1024
)  # virtual: only touched pages cost memory

#: Serialises the temporary raise of the (process-wide) recursion limit, so two
#: threads retrying deep pages cannot leave the raised value behind as "previous".
_recursion_limit_lock = threading.Lock()

#: Permalink-anchor texts (Sphinx ``headerlink``, MkDocs ``toclink``, ...) that
#: carry no content and would otherwise become ``[¶](#section)`` on every heading.
_PERMALINK_TEXTS = frozenset({"¶", "#", "§", "🔗"})
_PERMALINK_CLASSES = frozenset(
    {"headerlink", "toclink", "anchor", "permalink", "header-anchor"}
)

#: Containers whose only job is nesting: one with a single element child and no text of
#: its own renders exactly as that child, so :func:`_prepare_soup` unwraps it (this is what
#: keeps ``markdownify``'s per-level cost from adding up on deeply nested pages).
_WRAPPER_TAGS = ("div", "span", "section", "article", "main", "font", "center")

#: Where a line of text may start in the rendered markdown: a marker there (``- ``,
#: ``1. ``, ``# ``, ``> ``) would turn plain text into a list, heading or quote.
_LINE_START_CONTAINERS = frozenset(
    "p div li dd dt blockquote section article main center body".split()
)
_LINE_START_MARKER_RE = re.compile(r"^(?:[-+*>]|#{1,6}|\d{1,9}[.)])(?=\s|$)")
#: A whole line that markdown reads as a rule (``---``, ``* * *``, ``___``) or as a setext
#: underline (``===``, ``---`` under a ``<br>``-separated title).
_RULE_LINE_RE = re.compile(r"^([-*_=])(?:\s*\1){2,}$")

#: Tags that end a line (and so start a new one): blocks, their parts, ``<br>`` and ``<hr>``.
_LINE_BREAK_TAGS = frozenset(
    "div p ul ol dl table pre blockquote section article main header footer nav aside "
    "form fieldset figure address details center li dd dt tr tbody thead tfoot hr br "
    "h1 h2 h3 h4 h5 h6".split()
)
#: Tags that end a paragraph of inline content (``<br>`` does not: it is inside one).
_BLOCK_END_TAGS = _LINE_BREAK_TAGS - {"br"}

#: Inline tags that render as their content alone (no markers around it).
_TRANSPARENT_INLINE_TAGS = frozenset(
    {"span", "font", "label", "small", "abbr", "sub", "sup"}
)

_BLOCK_TAGS = ("div", "p", "ul", "ol", "dl", "table", "pre", "blockquote", "section")
_INLINE_TAGS = frozenset(
    "a b i u em strong span code small font label abbr cite mark sub sup".split()
)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

#: Whitespace that is not significant outside ``<pre>``.
_WHITESPACE_RUN_RE = re.compile(r"[ \t\n\r\f]+")


def _outside_code_fences(markdown: str) -> Iterator[Tuple[bool, str]]:
    """Split *markdown* into ``(is_fenced_code, text)`` chunks.

    A fence may be indented (fences nest in list items). A closing fence must use
    the opening fence's character, be at least as long, and carry nothing else
    (CommonMark). An unterminated fence keeps everything after it marked as code,
    which is the conservative choice: whitespace inside code is content.

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
                chunk, fence = [line], match.group(1)
            else:
                chunk.append(line)
        else:
            chunk.append(line)
            closes = (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= len(fence)
                and not line.strip().strip(fence[0])
            )
            if closes:
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
    >>> _collapse_blank_runs("````\\n```\\n\\n\\n\\n```\\n````\\n")
    '````\\n```\\n\\n\\n\\n```\\n````\\n'
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


def _make_converter(**options):
    """A ``MarkdownConverter`` whose ``<pre>`` fences outgrow any backticks in the code."""

    class _Converter(MarkdownConverter):
        def convert_pre(self, el, text, *args, **kwargs):
            out = super().convert_pre(el, text, *args, **kwargs)
            longest = max(
                (len(run) for run in re.findall(r"`+", text or "")), default=0
            )
            if out and longest >= 3:
                fence = "`" * (longest + 1)
                head, _, tail = out.partition("```")
                body, _, rest = tail.rpartition("\n```")
                out = f"{head}{fence}{body}\n{fence}{rest}"
            return out

    return _Converter(**options)


def _parse_html(html: str, parser: str = DFLT_HTML_PARSER):
    """Parse *html* with the BeautifulSoup *parser* (``"lxml"`` is lenient like a browser)."""
    return BeautifulSoup(html, parser)


def _escape_line_start(text: str) -> str:
    r"""Backslash-escape a list/heading/quote marker at the start of ``text``.

    >>> for t in ("- a", "1. a", "# a", "> a", "a - b", "-1", "---", "* * *", "==="):
    ...     print(_escape_line_start(t))
    \- a
    1\. a
    \# a
    \> a
    a - b
    -1
    \---
    \* * *
    \===
    """
    if _RULE_LINE_RE.match(text.strip()):
        return f"\\{text}"
    m = _LINE_START_MARKER_RE.match(text)
    if m is None:
        return text
    marker = m.group()
    if marker[0].isdigit():
        return f"{marker[:-1]}\\{marker[-1]}{text[m.end() :]}"
    return f"\\{marker}{text[m.end() :]}"


def _only_child(tag):
    """The one element ``tag`` merely wraps (see :func:`_unwrap_wrapper_chains`), else ``None``."""
    if tag.name not in _WRAPPER_TAGS:
        return None
    inline = tag.name in ("span", "font")
    kids = [
        c
        for c in tag.contents
        if inline or not (type(c) is NavigableString and not c.strip())
    ]
    if len(kids) != 1 or getattr(kids[0], "name", None) is None:
        return None
    if kids[0].name == "pre":
        return None  # a ``code_language_callback`` reads the wrapper's attributes
    if inline or kids[0].name in _BLOCK_TAGS + _HEADING_TAGS:
        return kids[0]
    return None


def _unwrap_wrapper_chains(soup) -> None:
    """Replace each chain of containers that only nest (:data:`_WRAPPER_TAGS`) by its innermost element, in place.

    A block wrapper holding just one block element (and whitespace), or an inline wrapper
    holding just one element, renders as that element alone. Each chain is replaced once,
    so a page nested thousands of levels deep of such wrappers costs one step, not one per level.
    """
    for head in _tags_under(soup, _WRAPPER_TAGS):
        if head.parent is None or _only_child(head.parent) is head:
            continue  # gone, or inside a chain whose head replaces it
        inner = _only_child(head)
        if inner is None:
            continue
        while (child := _only_child(inner)) is not None:
            inner = child
        head.replace_with(inner.extract())


def _tags_under(el, names, *, with_href: bool = False) -> list:
    """The tags named in ``names`` below ``el``, in document order.

    A plain walk of ``el.descendants``: much cheaper on large pages than
    ``find_all``, whose per-node filter machinery dominated the pre-pass.
    """
    return [
        d
        for d in el.descendants
        if getattr(d, "name", None) in names and (not with_href or d.has_attr("href"))
    ]


def _strings_under(el) -> list:
    """The plain text nodes below ``el`` (a cheap stand-in for ``el.find_all(string=True)``)."""
    return [d for d in el.descendants if type(d) is NavigableString]


def _has_descendant(el, names) -> bool:
    """Whether a tag named in ``names`` is below ``el`` (a cheap ``el.find(names) is not None``)."""
    return any(getattr(d, "name", None) in names for d in el.descendants)


def _prepare_soup(
    soup, *, escape_line_start: bool = True, include_links: bool = True
) -> None:
    """Fix the shapes ``markdownify`` renders badly (in place).

    ``escape_line_start`` backslash-escapes text that would otherwise open a list, heading
    or quote (the converter's own ``escape_misc`` is off, being too noisy). With
    ``include_links=False`` an ``<a>`` renders as its text alone, so it is transparent.
    """
    for tag in _tags_under(soup, NON_CONTENT_TAGS):
        tag.decompose()
    _unwrap_wrapper_chains(soup)
    # Permalink anchors on headings are noise, not content ("see § 3" is content).
    for a in _tags_under(soup, ("a",), with_href=True):
        if (
            a.get_text(strip=True) in _PERMALINK_TEXTS
            and a["href"].startswith("#")
            and (
                _PERMALINK_CLASSES.intersection(a.get("class") or ())
                or a.find_parent(_HEADING_TAGS + ("dt",)) is not None
            )
        ):
            a.decompose()
    # Bare markdown link destinations break on spaces and parentheses.
    for a in _tags_under(soup, ("a",), with_href=True):
        a["href"] = re.sub(
            r"[ ()]", lambda m: f"%{ord(m.group()):02X}", a["href"].strip()
        )
    # A non-breaking space is not a character of code (it makes pasted code invalid) and
    # would keep a heading from matching the page title: it is a plain space there.
    for el in _tags_under(soup, ("pre", "textarea") + _HEADING_TAGS):
        for text in _strings_under(el):
            if "\xa0" in text:
                text.replace_with(text.replace("\xa0", " "))
    # Source newlines/indentation are not significant outside <pre>; left in, a
    # line starting with "# " or "- " would turn a paragraph into a heading/list.
    verbatim = {
        id(string)
        for el in _tags_under(soup, ("pre", "textarea"))
        for string in _strings_under(el)
    }
    for text in _strings_under(soup):
        if type(text) is NavigableString and id(text) not in verbatim:
            collapsed = _WHITESPACE_RUN_RE.sub(" ", text)
            if collapsed != text:  # most nodes are already clean: skip the tree surgery
                text.replace_with(collapsed)
    # Inline content right after a list would be glued onto its last item: it becomes a
    # paragraph of its own.
    for sub in _tags_under(soup, ("ul", "ol", "dl")):
        sibling = sub.next_sibling
        if sibling is None or not sibling.get_text(strip=True):
            continue
        if getattr(sibling, "name", None) in _BLOCK_END_TAGS:
            continue
        run, node = [], sibling
        while node is not None and getattr(node, "name", None) not in _BLOCK_END_TAGS:
            run.append(node)
            node = node.next_sibling
        paragraph = soup.new_tag("p")
        sub.insert_after(paragraph)
        for node in run:
            paragraph.append(node.extract())
    if escape_line_start:
        _escape_line_starts(soup, verbatim, include_links=include_links)
    # `<a><h2>T</h2></a>` would render as `[## T](url)`: put the link inside.
    for a in _tags_under(soup, ("a",), with_href=True):
        if not _has_descendant(a, _BLOCK_TAGS + _HEADING_TAGS):
            continue
        for heading in a.find_all(_HEADING_TAGS):
            if heading.find("a") is None:
                link = soup.new_tag("a", href=a["href"])
                link.extend(list(heading.contents))
                heading.append(link)
        if a.find(_HEADING_TAGS) is not None:
            a.unwrap()


def _opens_a_line(text, *, transparent=_TRANSPARENT_INLINE_TAGS) -> bool:
    """Whether ``text`` starts a line of the rendered markdown: nothing but whitespace
    precedes it in its block container (looking through inline wrappers like ``<b>``), or
    only a ``<br>`` / block element does."""
    node = text
    while True:
        for sibling in node.previous_siblings:
            if type(sibling) is NavigableString:
                if sibling.strip():
                    return False
            elif getattr(sibling, "name", None) is not None:
                if sibling.name in _LINE_BREAK_TAGS:
                    return True
                if (
                    sibling.get_text(strip=True)
                    or sibling.name == "img"
                    or sibling.find("img") is not None
                ):
                    return False  # inline content before it on the line
                # an empty inline tag (``<a id=x></a>``) renders nothing: look further back
        parent = node.parent
        if parent is None:
            return False
        if parent.name in _LINE_START_CONTAINERS:
            return True
        if parent.name not in transparent:
            return False  # e.g. <b>: the line starts with its ``**``, not with the text
        node = parent


def _escape_line_starts(soup, verbatim: set, *, include_links: bool = True) -> None:
    """Escape marker-looking text at the start of a block (see :func:`_escape_line_start`)."""
    transparent = _TRANSPARENT_INLINE_TAGS | (
        frozenset() if include_links else frozenset({"a"})
    )
    for text in _strings_under(soup):
        if type(text) is not NavigableString or id(text) in verbatim:
            continue
        stripped = text.lstrip()
        looks_like_markup = _LINE_START_MARKER_RE.match(
            stripped
        ) or _RULE_LINE_RE.match(stripped.rstrip())
        if looks_like_markup and _opens_a_line(text, transparent=transparent):
            lead = text[: len(text) - len(stripped)]
            text.replace_with(lead + _escape_line_start(stripped))


def _convert_deep(converter, soup) -> str:
    """Convert ``soup`` in a big-stack thread under a raised recursion limit.

    Both the recursion limit and ``threading.stack_size`` are process-wide, so one
    raiser at a time (``_recursion_limit_lock``); both are restored afterwards.
    """
    result: list = []

    def work():
        try:
            result.append((converter.convert_soup(soup), None))
        except BaseException as e:  # re-raised in the caller's thread
            result.append((None, e))

    with _recursion_limit_lock:
        previous_limit = sys.getrecursionlimit()
        previous_stack = threading.stack_size()
        sys.setrecursionlimit(max(previous_limit, DEEP_HTML_RECURSION_LIMIT))
        try:
            threading.stack_size(DEEP_HTML_THREAD_STACK_BYTES)
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            thread.join()
        finally:
            threading.stack_size(previous_stack)
            sys.setrecursionlimit(previous_limit)
    markdown, error = result[0]
    if error is not None:
        raise error
    return markdown


def _strip_excluded(options: dict, *, ignore_images: bool, include_links: bool) -> dict:
    """*options* with ``<img>`` (``ignore_images``) / ``<a>`` (not ``include_links``)
    rendered as their text alone."""
    excluded = [
        t for t, skip in (("img", ignore_images), ("a", not include_links)) if skip
    ]
    if not excluded:
        return options
    if options.get("convert"):  # markdownify forbids `strip` together with `convert`
        return {
            **options,
            "convert": [t for t in options["convert"] if t not in excluded],
        }
    strip = options.get("strip") or []
    return {
        **options,
        "strip": [*([strip] if isinstance(strip, str) else strip), *excluded],
    }


def _convert_html(
    converter: MarkdownConverter,
    html: str,
    *,
    clean: bool = True,
    include_links: bool = True,
    parser: str = DFLT_HTML_PARSER,
) -> str:
    """Convert one HTML string to Markdown, dropping non-content tags first.

    With ``clean`` (the default) the soup goes through :func:`_prepare_soup` first,
    and a page nested too deeply for the recursion limit is retried
    (:func:`_convert_deep`).
    """
    soup = _parse_html(html, parser)
    if clean:
        _prepare_soup(
            soup,
            escape_line_start=not converter.options.get("escape_misc"),
            include_links=include_links,
        )
        try:
            markdown = converter.convert_soup(soup)
        except RecursionError:  # very deeply nested page: retry with a higher limit
            markdown = _convert_deep(converter, soup)
    else:
        for tag in soup.find_all(NON_CONTENT_TAGS):
            tag.decompose()
        markdown = converter.convert_soup(soup)
    return _collapse_blank_runs(markdown).strip() + "\n"


def html_to_markdown_str(
    html: str | bytes,
    *,
    clean: bool = True,
    ignore_images: bool = False,
    include_links: bool = True,
    parser: str = DFLT_HTML_PARSER,
    **markdownify_options,
) -> str:
    """Convert one HTML document to a clean Markdown string.

    The defaults are :data:`HTML_TO_MARKDOWN_DEFAULTS` (ATX headings, ``*``
    bullets, no underscore/asterisk escaping); the ``<head>``, scripts, styles
    and ``noscript`` blocks are dropped, and runs of blank lines are collapsed
    outside fenced code.

    With ``clean`` (the default) the shapes ``markdownify`` renders badly are
    fixed first: permalink anchors (Sphinx ``headerlink``, MkDocs ``toclink``,
    ...) are dropped, single-child wrapper chains unwrapped, source whitespace
    collapsed outside ``<pre>``, text that would open a list, heading, quote or
    rule at a line start escaped, and a page nested too deeply for the
    recursion limit retried in a big-stack thread. ``clean=False`` converts the
    soup as parsed.

    Unlike :func:`scraped.html_to_markdown`, the input is always taken as HTML
    text: a file path or directory is not read, and bytes that are not UTF-8
    raise ``UnicodeDecodeError`` rather than being skipped.

    :param html: The HTML, as ``str`` or UTF-8 ``bytes``.
    :param clean: Run the cleaning passes above (default ``True``).
    :param ignore_images: Drop images (default ``False``: kept).
    :param include_links: Keep hyperlinks (``False`` keeps the link text only).
    :param parser: The BeautifulSoup parser (:data:`DFLT_HTML_PARSER`, or ``"lxml"``).
    :param markdownify_options: ``markdownify.MarkdownConverter`` options
        overriding the defaults. Unknown names raise ``TypeError``.

    >>> html_to_markdown_str("<ul><li>a</li><li>b</li></ul>")
    '* a\\n* b\\n'
    >>> html_to_markdown_str(b"<h2>T</h2>", heading_style="UNDERLINED")
    'T\\n-\\n'
    >>> html_to_markdown_str('<h2>Usage<a class="headerlink" href="#usage">¶</a></h2>')
    '## Usage\\n'
    >>> html_to_markdown_str("<p>a <a href='u'>c</a></p>", include_links=False)
    'a c\\n'
    """
    if isinstance(html, bytes):
        html = html.decode()
    if not isinstance(html, str):
        raise TypeError(f"html must be str or bytes, not {type(html).__name__}")
    options = _strip_excluded(
        _resolve_markdownify_options(markdownify_options),
        ignore_images=ignore_images,
        include_links=include_links,
    )
    return _convert_html(
        _make_converter(**options),
        html,
        clean=clean,
        include_links=include_links,
        parser=parser,
    )

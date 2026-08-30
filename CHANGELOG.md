# Changelog

## Unreleased

### Changed

- **HTML→Markdown conversion now uses `markdownify` (MIT) instead of `html2text`
  (GPL-3.0-or-later).** `html2text` was imported at module scope in
  `scraped/util.py`, so `import scraped` required a copyleft distribution to be
  present — in an MIT-licensed package. `markdownify>=1.0` replaces it as a core
  dependency, and `beautifulsoup4` (which `markdownify` requires anyway) is now
  declared explicitly, since `util.py` imports it directly to strip
  `head`/`script`/`style`/`noscript` before converting.

- **Output is no longer hard-wrapped.** `html2text` was constructed with no
  options, so it ran at its default `body_width = 78` and reflowed every
  paragraph onto 78-column lines. `markdownify` does not wrap. This is the
  single largest textual difference in this release: regenerating any bundle
  produced by `markdown_of_site` will show every paragraph reflow onto one line.

- Other output differences: fenced code blocks instead of 4-space-indented ones,
  GitHub-flavoured tables instead of `html2text`'s non-standard table syntax,
  ATX headings, and `<title>` no longer leaking into the output. HTML entities
  and non-breaking spaces are now preserved rather than folded to ASCII —
  `&copy;` stays `©` instead of becoming `(C)`, `&mdash;` stays `—` instead of
  `--`, `caf&eacute;` stays `café`, and `&nbsp;` stays U+00A0 instead of
  becoming a plain space. Anything that diffs, greps or pattern-matches
  generated Markdown may need to re-baseline.

- Blank-line runs are normalised **outside fenced code blocks only**, so the two
  blank lines PEP 8 puts between top-level `def`s survive conversion of a
  technical page.

- **Breaking (keyword surface):** `html_to_markdown(..., **html2text_options)` is
  now `html_to_markdown(..., **markdownify_options)`. The options are
  `markdownify` `MarkdownConverter` options, not `html2text.HTML2Text`
  attributes — a different vocabulary, so the parameter was *renamed* rather
  than silently reinterpreted. Unknown option names now raise `TypeError` naming
  the valid ones, so an `html2text` option carried over from before (e.g.
  `body_width=0`) fails loudly instead of being silently ignored, which is what
  `MarkdownConverter` does on its own. Defaults live in the new
  `scraped.util.HTML_TO_MARKDOWN_DEFAULTS` and are overridable per call. No
  in-repo caller passed these options, and none was found anywhere in the
  surrounding package fleet.

- Conversion is roughly 2× slower per document, since every page now goes
  through a full BeautifulSoup parse. Negligible next to network latency for
  `markdown_of_site`; worth knowing for a deep crawl over thousands of pages.

### Fixed

- **`html_to_markdown` no longer raises `UnboundLocalError` for a list, tuple or
  generator of HTML strings** (#11). Only the `str` and `Mapping` branches ever
  bound `html_contents`; the iterable branch's assignment was commented out.
  Each item of an iterable now gets the same path / directory / raw-HTML
  dispatch a lone string gets, via the new `_html_contents_of` helper — so a
  list of N items behaves like N single-item calls. Anything that is neither a
  string, an iterable, nor a mapping raises `TypeError` naming the type it got.

- A raw HTML string longer than ~250 characters no longer raises
  `OSError: File name too long` from the path-vs-HTML sniff.

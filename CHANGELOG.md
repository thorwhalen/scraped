# Changelog

## Unreleased

### Changed

- **HTML→Markdown conversion now uses `markdownify` (MIT) instead of `html2text`
  (GPL-3.0-or-later).** `html2text` was imported at module scope in
  `scraped/util.py`, so `import scraped` required a copyleft distribution to be
  present — in an MIT-licensed package. `markdownify` replaces it as a core
  dependency, and `beautifulsoup4` (which `markdownify` requires anyway) is now
  declared explicitly, since `util.py` imports it directly to strip
  `head`/`script`/`style`/`noscript` before converting.

- **Breaking (keyword surface):** `html_to_markdown(..., **html2text_options)` is
  now `html_to_markdown(..., **markdownify_options)`. The options are
  `markdownify` `MarkdownConverter` options, not `html2text.HTML2Text`
  attributes — a different vocabulary, so the parameter was *renamed* rather
  than silently reinterpreted. Defaults live in the new
  `scraped.util.HTML_TO_MARKDOWN_DEFAULTS` and are overridable per call. No
  in-repo caller passed these options.

- The generated Markdown differs in detail: fenced code blocks instead of
  4-space-indented ones, GitHub-flavoured tables instead of `html2text`'s
  non-standard table syntax, and `<title>` no longer leaks into the output.
  Long paragraphs are still left unwrapped. Anything that diffs or
  pattern-matches generated Markdown may need to re-baseline.

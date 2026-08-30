"""Tests for `scraped.util.html_to_markdown`, the HTML->Markdown converter."""

import tempfile
from pathlib import Path

from scraped import html_to_markdown

SAMPLE_HTML = """
<html><head><title>Page title</title><style>body{color:red}</style>
<script>var x = 1;</script></head>
<body>
<h1>Heading One</h1>
<p>Some <b>bold</b> and <em>italic</em> text with a
<a href="https://example.com/a">link</a>.</p>
<ul><li>alpha</li><li>beta with <code>inline_code()</code></li></ul>
<pre><code>def f():
    return 1
</code></pre>
<table><thead><tr><th>A</th><th>B</th></tr></thead>
<tbody><tr><td>1</td><td>2</td></tr></tbody></table>
<blockquote>Quoted - with a caf&eacute;.</blockquote>
</body></html>
"""


def test_structure_survives_conversion():
    md = html_to_markdown(SAMPLE_HTML)
    assert "# Heading One" in md  # ATX headings, not setext
    assert "**bold**" in md and "*italic*" in md
    assert "[link](https://example.com/a)" in md
    assert "* alpha" in md
    assert "`inline_code()`" in md
    assert "| A | B |" in md  # GitHub-flavoured table
    assert "> Quoted" in md
    assert "café" in md  # unicode passes through unescaped


def test_non_content_tags_are_dropped():
    md = html_to_markdown(SAMPLE_HTML)
    assert "Page title" not in md
    assert "color:red" not in md
    assert "var x" not in md


def test_markdownify_options_are_forwarded():
    md = html_to_markdown("<ul><li>a</li></ul>", bullets="-")
    assert "- a" in md


def test_mapping_input_is_aggregated_in_order():
    htmls = {"one": "<h1>One</h1>", "two": "<h1>Two</h1>"}
    md = html_to_markdown(htmls)
    assert md.index("# One") < md.index("# Two")


def test_list_aggregator_returns_one_string_per_document():
    htmls = {"one": "<h1>One</h1>", "two": "<h1>Two</h1>"}
    mds = html_to_markdown(htmls, markdown_contents_aggregator=list)
    assert len(mds) == 2
    assert mds[0].strip() == "# One"


def test_prefixes_are_woven_in():
    htmls = {"one": "<h1>One</h1>"}
    md = html_to_markdown(htmls, prefixes=["<!-- src: one -->"])
    assert md.startswith("<!-- src: one -->\n# One")


def test_directory_of_html_files_and_saving():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "a.html").write_text("<html><body><h1>A</h1></body></html>")
        out = folder / "out.md"
        returned = html_to_markdown(str(folder), save_filepath=str(out))
        assert returned == str(out)
        assert "# A" in out.read_text()

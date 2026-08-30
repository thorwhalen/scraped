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


def test_blank_lines_inside_code_blocks_are_preserved():
    """PEP 8 puts two blank lines between top-level defs -- keep them.

    A blanket ``re.sub(r"\n{3,}", "\n\n", md)`` over the whole document would
    silently rewrite the source code on every technical page scraped.
    """
    html = (
        "<pre><code>def func(x):\n    return x + 1\n\n\n"
        "def test_answer():\n    assert func(3) == 5\n</code></pre>"
    )
    md = html_to_markdown(html)
    assert "return x + 1\n\n\ndef test_answer():" in md


def test_blank_line_runs_outside_code_blocks_are_collapsed():
    md = html_to_markdown("<p>a</p><div><br><br><br><br></div><p>b</p>")
    assert "\n\n\n" not in md


def test_paragraphs_are_no_longer_hard_wrapped():
    """html2text ran with its default ``body_width=78``; markdownify does not."""
    long_sentence = " ".join(["word"] * 60)
    md = html_to_markdown(f"<p>{long_sentence}</p>")
    assert long_sentence in md


def test_unknown_options_raise_rather_than_being_ignored():
    """``MarkdownConverter`` swallows unknown options, so a typo would no-op.

    In particular an ``html2text`` option carried over from before the
    ``markdownify`` migration must not silently do nothing.
    """
    import pytest

    with pytest.raises(TypeError, match="body_width"):
        html_to_markdown("<p>a</p>", body_width=0)


# --- issue #11: any non-str, non-Mapping iterable used to raise -------------


def test_iterable_of_raw_html_strings():
    mds = html_to_markdown(
        ["<h1>One</h1>", "<h1>Two</h1>"], markdown_contents_aggregator=list
    )
    assert [md.strip() for md in mds] == ["# One", "# Two"]


def test_iterable_of_html_file_paths():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "a.html").write_text("<h1>A</h1>")
        (folder / "b.html").write_text("<h1>B</h1>")
        md = html_to_markdown([str(folder / "a.html"), str(folder / "b.html")])
    assert md.index("# A") < md.index("# B")


def test_iterable_mixing_paths_directories_and_raw_html():
    """Each item gets the same dispatch a lone string gets."""
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "sub").mkdir()
        (folder / "sub" / "a.html").write_text("<h1>A</h1>")
        (folder / "b.html").write_text("<h1>B</h1>")
        md = html_to_markdown(
            [str(folder / "sub"), str(folder / "b.html"), "<h1>C</h1>"]
        )
    assert md.index("# A") < md.index("# B") < md.index("# C")


def test_generator_input_is_accepted():
    md = html_to_markdown(f"<h1>{i}</h1>" for i in range(3))
    assert md.index("# 0") < md.index("# 1") < md.index("# 2")


def test_unsupported_type_raises_type_error():
    import pytest

    with pytest.raises(TypeError, match="not int"):
        html_to_markdown(123)

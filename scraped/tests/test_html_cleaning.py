"""The cleaning passes of :func:`scraped.html_markdown.html_to_markdown_str`.

Ported with the passes from ``contaix.web.html_to_clean_markdown`` (thorwhalen/scraped#25),
which is now a thin wrapper: images dropped, output stripped, ``lxml`` parsing.
"""

import pytest

from scraped.html_markdown import html_to_markdown_str

lxml = pytest.importorskip("lxml")


def html_to_clean_markdown(html, **kwargs):
    """contaix's defaults over scraped's converter."""
    kwargs.setdefault("ignore_images", True)
    kwargs.setdefault("parser", "lxml")
    return html_to_markdown_str(html, **kwargs).strip()


def test_basic_structure():
    html = (
        "<h1>Title</h1><p>Some <b>bold</b>, <i>it</i> and <code>code</code>.</p>"
        "<ul><li>one</li><li>two</li></ul><p>Snake_case_name and a*b*c</p>"
    )
    md = html_to_clean_markdown(html)
    assert md.startswith("# Title\n\n")
    assert "**bold**" in md and "`code`" in md
    assert "* one" in md and "* two" in md
    assert "Snake_case_name" in md and "a*b*c" in md  # no escaping noise


def test_links_and_images_options():
    html = '<p><a href="http://x.org/a">link</a> <img src="i.png" alt="pic"></p>'
    assert html_to_clean_markdown(html) == "[link](http://x.org/a)"
    assert html_to_clean_markdown(html, include_links=False) == "link"
    assert "![pic](i.png)" in html_to_clean_markdown(html, ignore_images=False)


def test_non_content_tags_are_dropped():
    html = (
        "<html><head><title>T</title><style>p{}</style></head>"
        "<body><script>alert(1)</script><noscript>x</noscript><p>body</p></body></html>"
    )
    assert html_to_clean_markdown(html) == "body"


def test_blank_lines_inside_code_blocks_are_preserved():
    html = "<p>a</p><br><br><br><p>b</p><pre><code>def f():\n    pass\n\n\ndef g():\n    pass\n</code></pre>"
    md = html_to_clean_markdown(html)
    assert "\n\n\n" not in md.split("```")[0]  # collapsed outside code
    assert "pass\n\n\ndef g" in md  # PEP 8 spacing kept inside code


def test_tables_survive():
    md = html_to_clean_markdown(
        "<table><tr><th>k</th><th>v</th></tr><tr><td>a</td><td>1</td></tr></table>"
    )
    assert "| k | v |" in md and "| a | 1 |" in md


def test_entities_are_kept_as_characters():
    assert html_to_clean_markdown("<p>&copy; a&nbsp;b</p>") == "© a b"


def test_empty_input():
    assert html_to_clean_markdown("") == ""
    assert html_to_clean_markdown("   ") == ""


def test_unknown_options_raise_instead_of_being_ignored():
    with pytest.raises(TypeError, match="ignore_links"):
        html_to_clean_markdown("<p>x</p>", ignore_links=True)
    with pytest.raises(TypeError, match="unicode_snob"):
        html_to_clean_markdown("<p>x</p>", unicode_snob=True)


def test_markdownify_options_pass_through():
    assert html_to_clean_markdown("<h1>T</h1>", heading_style="UNDERLINED").startswith(
        "T\n="
    )
    assert html_to_clean_markdown("<ul><li>a</li></ul>", bullets="-") == "- a"


def test_strip_option_composes_with_flags():
    html = '<p><b>b</b> <a href="u">l</a> <img src="i.png"></p>'
    assert html_to_clean_markdown(html, strip=["b"]) == "b [l](u)"


# --- regressions found by the adversarial review (omitted end tags, glued words, ...) ---


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<ul><li>a<li>b<li>c</ul>", "* a\n* b\n* c"),
        ("<ol><li>one<li>two</ol>", "1. one\n2. two"),
        (
            "<table><tr><th>h1<th>h2<tr><td>a<td>b</table>",
            "| h1 | h2 |\n| --- | --- |\n| a | b |",
        ),
    ],
)
def test_omitted_end_tags_parse_like_a_browser(html, expected):
    assert html_to_clean_markdown(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        "<ul>" + "<li>x" * 2000 + "</ul>",
        "<div>" * 600 + "x" + "</div>" * 600,
        "<b>x " * 700,
    ],
)
def test_deeply_nested_html_does_not_raise_recursion_error(html):
    assert "x" in html_to_clean_markdown(html)


def test_text_after_nested_list_is_not_glued_to_its_last_item():
    md = html_to_clean_markdown(
        "<ul><li>x<ul><li>next batch</li></ul>Ten sheets</li></ul>"
    )
    assert "next batchTen" not in md and "next batch" in md and "Ten sheets" in md


def test_heading_inside_link_stays_a_heading():
    assert html_to_clean_markdown('<a href="/x"><h2>Head</h2></a>') == "## [Head](/x)"
    card = html_to_clean_markdown('<a href="/c"><div><h3>T</h3><p>B</p></div></a>')
    assert card.startswith("### [T](/c)") and "B" in card


def test_code_containing_a_fence_gets_a_longer_fence():
    md = html_to_clean_markdown("<pre>```\ninner\n```</pre>")
    assert md == "````\n```\ninner\n```\n````"


def test_blank_lines_kept_in_code_nested_in_lists():
    md = html_to_clean_markdown(
        "<ul><li>x<ul><li>i<pre>a\n\n\n\nb</pre></li></ul></li></ul>"
    )
    assert "a\n\n\n\n    b" in md


def test_convert_and_string_strip_options():
    html = "<p><b>b</b><i>i</i><img src=x></p>"
    assert html_to_clean_markdown(html, convert=["b", "i", "img"]) == "**b***i*"
    assert html_to_clean_markdown("<p>a <code>c</code></p>", strip="code") == "a c"


def test_source_newlines_do_not_create_block_syntax():
    assert (
        html_to_clean_markdown("<p>Total\n# of items\n- b</p>")
        == "Total # of items - b"
    )


def test_permalink_anchors_are_dropped():
    html = '<h2>Install<a class="headerlink" href="#install">¶</a></h2>'
    assert html_to_clean_markdown(html) == "## Install"


def test_inline_content_after_a_non_nested_list_is_not_glued():
    md = html_to_clean_markdown(
        "<div><ul><li>next batch</li></ul><span>Ten sheets</span></div>"
    )
    assert "batchTen" not in md and "Ten sheets" in md


def test_only_heading_permalinks_are_dropped_not_real_section_signs():
    assert html_to_clean_markdown("<p>See <a href='#fn1'>§</a> 3 of the act</p>") == (
        "See [§](#fn1) 3 of the act"
    )
    assert html_to_clean_markdown("<p>Issue <a href='#'>#</a></p>") == "Issue <#>"


def test_link_destinations_with_spaces_and_parens_stay_valid():
    assert (
        html_to_clean_markdown('<a href="my file.html">x</a>') == "[x](my%20file.html)"
    )
    assert html_to_clean_markdown('<a href="/a)b">y</a>') == "[y](/a%29b)"


def test_deep_nesting_is_not_quadratic_in_prepare():
    import time

    start = time.perf_counter()
    html_to_clean_markdown("<p><font>x " * 600 + "end")
    assert time.perf_counter() - start < 10


def test_concurrent_deep_pages_leave_the_recursion_limit_untouched():
    import sys
    import threading

    html = "<div>" * 1500 + "x" + "</div>" * 1500
    before = sys.getrecursionlimit()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(html_to_clean_markdown(html)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4 and all("x" in r for r in results)
    assert sys.getrecursionlimit() == before


# ---------------------------------------------------------------------------
# Residual differences from html2text (thorwhalen/contaix#7)
# ---------------------------------------------------------------------------


def test_nbsp_in_code_and_headings_is_a_plain_space():
    assert html_to_clean_markdown("<pre>a&nbsp;=&nbsp;1</pre>") == "```\na = 1\n```"
    assert html_to_clean_markdown("<h2>A&nbsp;B</h2>") == "## A B"
    assert "\xa0" in html_to_clean_markdown("<p>a&nbsp;b</p>")  # prose keeps it


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<p>- not a list</p>", "\\- not a list"),
        ("<p>1. step</p>", "1\\. step"),
        ("<p># not a heading</p>", "\\# not a heading"),
        ("<div>&gt; not a quote</div>", "\\> not a quote"),
        ("<p>a<br>- b</p>", "a  \n\\- b"),
        ("<ul><li>- x</li></ul>", "* \\- x"),
        ("<p>a - b and 1. c</p>", "a - b and 1. c"),  # not at the start of a line
        ("<p><b>- x</b></p>", "**- x**"),  # starts with the markers, not the text
        ("<p>-1 and #hash</p>", "-1 and #hash"),
        ("<pre>- code\n1. code</pre>", "```\n- code\n1. code\n```"),
    ],
)
def test_text_that_would_open_a_list_heading_or_quote_is_escaped(html, expected):
    assert html_to_clean_markdown(html) == expected


def test_line_start_escaping_can_be_left_to_markdownify():
    assert html_to_clean_markdown("<p>- x</p>", escape_misc=True) == "\\- x"


def test_inline_text_after_a_list_is_a_paragraph_without_a_stray_line():
    md = html_to_clean_markdown("<ul><li>a</li></ul>tail <b>bold</b> end<p>next</p>")
    assert md == "* a\n\ntail **bold** end\n\nnext"
    assert all(line.strip() or not line for line in md.split("\n"))


def test_deeply_nested_wrappers_are_flattened_cheaply():
    import time

    n = 5000
    html = "<div>" * n + "<p>deep</p>" + "</div>" * n
    start = time.perf_counter()
    assert html_to_clean_markdown(html) == "deep"
    assert time.perf_counter() - start < 5  # was ~8 s


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<div><div><p>a</p></div></div>", "a"),
        ("<div> <section>\n<p>a</p></section> </div><p>b</p>", "a\n\nb"),
        ("<p>x <span><span><b>y</b></span></span> z</p>", "x **y** z"),
        # a wrapper with text of its own, or an inline child of a block, is kept
        ("<div>t<div><p>a</p></div></div>", "t\n\na"),
        ("<p>a</p><div><b>b</b></div><p>c</p>", "a\n\n**b**\n\nc"),
    ],
)
def test_wrapper_flattening_does_not_change_the_rendering(html, expected):
    assert html_to_clean_markdown(html) == expected


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<p>Press <kbd>Ctrl</kbd> + <kbd>C</kbd></p>", "Press `Ctrl` + `C`"),
        ("<p>see <q>quote</q> - he said</p>", 'see "quote" - he said'),
        ("<p><a id='x'></a>- item</p>", "\\- item"),
        ("<p><span class='icon'></span>1. step</p>", "1\\. step"),
    ],
)
def test_escaping_only_at_the_real_start_of_a_line(html, expected):
    assert html_to_clean_markdown(html) == expected


def test_text_after_an_image_is_mid_line():
    md = html_to_clean_markdown("<p>Value<img src=x alt=y> - z</p>", ignore_images=False)
    assert md == "Value![y](x) - z"


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<ul><li>a</li></ul>tail<br>after br", "* a\n\ntail  \nafter br"),
        ("<ul><li>a</li></ul>tail<kbd>K</kbd> after", "* a\n\ntail`K` after"),
        ("<ul><li>a</li></ul>tail<h2>H</h2>", "* a\n\ntail\n\n## H"),
    ],
)
def test_the_whole_inline_run_after_a_list_is_one_paragraph(html, expected):
    assert html_to_clean_markdown(html) == expected


def test_a_nbsp_inside_code_does_not_lose_its_protection():
    md = html_to_clean_markdown("<pre>a&nbsp;b\n<div>- x&nbsp;y</div></pre>")
    assert "\\" not in md and "- x y" in md


def test_an_image_before_text_makes_it_mid_line():
    md = html_to_clean_markdown("<p><img src=a alt=b> - x</p>", ignore_images=False)
    assert md == "![b](a) - x"


def test_a_wrapper_of_a_code_block_keeps_its_attributes_for_the_language_callback():
    html = '<div class="highlight-python"><div class="highlight"><pre>x = 1</pre></div></div>'
    md = html_to_clean_markdown(
        html, code_language_callback=lambda el: " ".join(el.parent["class"])
    )
    assert md.startswith("```highlight\n")


def test_text_that_looks_like_a_rule_stays_text():
    out = html_to_clean_markdown("<p>one</p><p>---</p><p>***</p><p>___</p><p>two</p>")
    assert out.splitlines().count("---") == 0 and "\\---" in out and "\\***" in out and "\\___" in out


def test_setext_look_alike_after_a_br_stays_text():
    out = html_to_clean_markdown("<p>Title<br>===</p><p>Other<br>---</p>")
    assert "\\===" in out and "\\---" in out


def test_real_rules_are_still_rules():
    assert "---" in html_to_clean_markdown("<p>a</p><hr><p>b</p>").replace("\\---", "")


@pytest.mark.parametrize("tag", ["small", "abbr", "sub", "sup"])
def test_plain_rendering_inline_tags_do_not_hide_a_line_start(tag):
    out = html_to_clean_markdown(f"<ul><li><{tag}>1. Intro</{tag}></li></ul>")
    assert out == "* 1\\. Intro" or out == "- 1\\. Intro"


def test_unlinked_anchor_does_not_hide_a_line_start():
    out = html_to_clean_markdown("<ul><li><a href='#i'>1. Intro</a></li></ul>", include_links=False)
    assert "1\\. Intro" in out


def test_deep_retry_runs_on_a_big_stack_and_restores_process_state():
    import sys
    import threading

    limit, stack = sys.getrecursionlimit(), threading.stack_size()
    n = 6000
    html = "".join(f"<div>t{i}<span>" for i in range(n)) + "x" + "</span></div>" * n
    assert html_to_clean_markdown(html).startswith("t0")
    assert (sys.getrecursionlimit(), threading.stack_size()) == (limit, stack)

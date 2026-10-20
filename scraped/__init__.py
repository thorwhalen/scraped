"""Tools for scraping.

The top-level names are imported lazily: ``import scraped`` stays cheap, and the
crawler stack (``scrapy``, ``hubcap``) only loads when a name that needs it is
first used. :func:`html_to_markdown_str` needs only ``bs4`` and ``markdownify``.
"""

from importlib import import_module as _import_module

from scraped.html_markdown import html_to_markdown_str

#: Public name -> module it lives in, loaded on first attribute access.
_LAZY_NAMES = {
    "download_site": "scraped.util",
    "markdown_of_site": "scraped.util",
    "html_to_markdown": "scraped.util",
    "scrape_multiple_sites": "scraped.tools",
    "acquire_content": "scraped.tools",
}

#: Optional extras from other packages: ``name -> (module, attribute)``.
_OPTIONAL_NAMES = {
    "github_repo_text_aggregate": ("hubcap", "repo_text_aggregate"),
}

__all__ = ["html_to_markdown_str", *_LAZY_NAMES]


def __getattr__(name):
    if name in _LAZY_NAMES:
        value = getattr(_import_module(_LAZY_NAMES[name]), name)
    elif name in _OPTIONAL_NAMES:
        module, attr = _OPTIONAL_NAMES[name]
        try:
            value = getattr(_import_module(module), attr)
        except ImportError as error:
            raise AttributeError(
                f"scraped.{name} needs the optional package {module!r}"
            ) from error
    else:
        raise AttributeError(f"module 'scraped' has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals(), *_LAZY_NAMES, *_OPTIONAL_NAMES})

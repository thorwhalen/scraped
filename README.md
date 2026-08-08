# scraped

Tools for scraping.

To install:	```pip install scraped```

---

## `scraped.acquire` — get the bytes down, faithfully

Acquisition is one of three separable concerns in any information-extraction job:
**acquire** the raw bytes, **extract** the target fields, **format** the result.
This package owns the first and gives the other two a seam to hook into.

```python
from scraped.acquire import probe, fetch, scan_state_blobs

print(probe("https://example.com").summary())  # what will this site hand over?
capture = fetch("https://example.com")  # impersonating, polite, robots-aware
blobs = scan_state_blobs(capture.text())  # where is the structured data?
```

`probe` costs about three requests and usually decides the whole job:

```
  status          200
  robots          allowed=True delay=None
  sitemaps        1
  serves JSON     False
  generator       next.js (pages router)
  best payload    state_blob
  state blobs     next_data(445784B)
  guarded by      datadome (not blocking; use an impersonating transport)
```

### The one idea worth internalizing

Payload shape and transport are **independent axes**, and you optimize payload first:

```
payload:    api > internal api > state blob > sitemap > dom > text > vision
transport:  plain http > impersonating http > +session > browser > headful
```

Any payload rung is reachable from any transport rung. The highest-value cell on
the grid — *drive a real browser, then read the page's embedded state blob* — is
the one a single cheap-to-expensive ladder hides, because it makes browser
automation look like the bottom rather than an orthogonal cost.

This matters concretely. Scraping the DOM of a lazily-rendered listing returned
35/32/29 rows from pages that each held 35. Reading the same pages'
`__NEXT_DATA__` returned all of them, on every page, and kept working when the
markup changed — because the blob *is* the data rather than a rendering of it.

### What you get without asking for it

- **Impersonating transport by default.** A plain client gets `403` from origins
  that fingerprint TLS; the marginal cost of not being one is a single argument.
- **Robots compliance on by default** via `default_fetcher()` (and therefore
  `fetch`/`probe`), with overrides recorded in the capture, so "were we allowed to
  take this" is a queryable fact. Constructing `HttpFetcher()` directly opts out —
  pass `robots=RobotsPolicy(...)` if you build your own.
- **Per-host politeness and full-jitter retries** — and a challenge is *never*
  retried, because the answer will not change and repeating it confirms automation.
- **Challenges detected and raised**, never silently saved as if they were data.
- **Provenance on every capture** — WARC-isomorphic, with the body content-addressed
  so deduplication, change detection, and free re-runs all fall out of one choice.

```python
from scraped.acquire import default_fetcher, cached, capture_store, Request

fetcher = cached(default_fetcher(), capture_store("~/.cache/my-job"))
capture = fetcher(Request(url))  # second run costs no network at all
```

### Browser rung (optional)

```bash
pip install 'scraped[browser]' && patchright install chromium
```

```python
from scraped.acquire import BrowserSession, BrowserFetcher, Request

with BrowserSession(artifact_dir="~/runs/job") as session:
    capture = BrowserFetcher(session=session)(Request(url))
    session.save_storage_state()  # then work at HTTP speed from here
    print(session.artifacts().har_path)  # every byte, on disk
```

The rule this rung enforces: **the browser writes to disk; you read the file
path.** HAR and download paths are set at context creation and are not optional.
Getting a payload *out* of an automated browser is its own expensive failure
class — see [`misc/docs/browser-result-exfiltration.md`](misc/docs/browser-result-exfiltration.md).

### Design docs

[`misc/docs/`](misc/docs/) — [architecture](misc/docs/acquisition-architecture.md)
(terminology, the two-axis model, the escalation ladder and its diagnostic signal
table), [tooling survey](misc/docs/acquisition-tooling-survey.md) (what to use,
what is abandoned, and why), and
[browser exfiltration](misc/docs/browser-result-exfiltration.md).

---


# Showcase of main functionalities

Note that when pip installed, `scraped` comes with a command line tool of that name. 
Run this in your terminal:

```bash
scraped -h
```

Output:

```
usage: tools.py [-h] {markdown-of-site,download-site,scrape-multiple-sites} ...

...
```

These tools are written in python, so you can use them by importing

```python
from scraped import markdown_of_site, download_site, scrape_multiple_sites
```

`download_site` downloads one (by default, `depth=1`) or several (if you specify
a larger `depth`) pages of a target url, saving them in files of a folder of 
your (optional) choice. 

`scrape_multiple_sites` can be used to download several sites.

`markdown_of_site` uses `download_site` (by default, saving to a temporary folder), 
then aggregates all the pages into a single markdown string, which it can save for 
you if you ask for it (by specifying a `save_filepath`)

Below you'll find more details on these functionalities. 

You'll find more useful functions in the code, but the three I mention here are 
the "top" ones I use most often.

## markdown_of_site

Download a site and convert it to markdown.

This can be quite useful when you want to perform some NLP analysis on a site, 
feed some information to an AI model, or simply want to read the site offline.
Markdown offers a happy medium between readability and simplicity, and is
supported by many tools and platforms.

Args:
- url: The URL of the site to download.
- depth: The maximum depth to follow links.
- filter_urls: A function to filter URLs to download.
- save_filepath: The file path where the combined Markdown will be saved.
- verbosity: The verbosity level.
- dir_to_save_page_slurps: The directory to save the downloaded pages.
- extra_kwargs: Extra keyword arguments to pass to the Scrapy spider.

Returns:
- The Markdown string of the site (if save_filepath is None), otherwise the save_filepath.

```python
>>> markdown_of_site(
...     "https://i2mint.github.io/dol/",
...     depth=2,
...     save_filepath='~/dol_documentation.md'
... )  # doctest: +SKIP
'~/dol_documentation.md'
```

If you don't specify a `save_filepath`, the function will return the Markdown 
string, which you can then analyze directly, and/or store as you wish.

```python
>>> markdown_string = markdown_of_site("https://i2mint.github.io/dol/")  # doctest: +SKIP
>>> print(f"{type(markdown_string).__name__} of length {len(markdown_string)}")  # doctest: +SKIP
str of length 626439
```

## download_site

```python
download_site("http://www.example.com")
```

will just download the page the url points to, storing it in the default rootdir, 
which, for example, on unix/mac, is `~/.config/scraped/data`, but can be configured 
through a `SCRAPED_DFLT_ROOTDIR` environment variable.

The `depth` argument will enable you to download more content starting from the url:


```python
download_site("http://www.example.com", depth=3)
```

And there's more arguments:
* `start_url`: The URL to start downloading from.
* `url_to_filepath`: The function to convert URLs to local filepaths.
* `depth`: The maximum depth to follow links.
* `filter_urls`: A function to filter URLs to download.
* `mk_missing_dirs`: Whether to create missing directories.
* `verbosity`: The verbosity level.
* `rootdir`: The root directory to save the downloaded files.
* `extra_kwargs`: Extra keyword arguments to pass to the Scrapy spider.


# scraped

Tools for scraping.

To install:	```pip install scraped```


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
download_site('http://www.example.com')
```

will just download the page the url points to, storing it in the default rootdir, 
which, for example, on unix/mac, is `~/.config/scraped/data`, but can be configured 
through a `SCRAPED_DFLT_ROOTDIR` environment variable.

The `depth` argument will enable you to download more content starting from the url:


```python
download_site('http://www.example.com', depth=3)
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



# scraped
Tools for scraping


To install:	```pip install scraped```


# Examples

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


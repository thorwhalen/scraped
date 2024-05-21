"""Utils for scraped."""

import os
from functools import partial
from typing import Optional, Callable, Union
import multiprocessing

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from urllib.parse import urlparse, urljoin

from config2py import get_app_data_folder
from graze.base import _url_to_localpath

DFLT_ROOTDIR = get_app_data_folder('scraped', ensure_exists=True)

import requests
from urllib.parse import urljoin


def url_to_localpath(url: str, rootdir: str = DFLT_ROOTDIR) -> str:
    path = _url_to_localpath(url)
    if rootdir:
        return os.path.join(rootdir, path)
    return path


def explicit_url(response) -> str:
    """
    Takes a URL and returns a more explicit URL indicating the actual filepath.
    (specifically, if it's an HTML file, a folder, or some other kind of file)
    on the server side.

    Parameters:
    url (str): The input URL to be analyzed.

    Returns:
    str: A more explicit URL indicating the actual file path.

    Examples:
    >>> get_explicit_url('http://www.example.com/more')  # doctest: +SKIP
    'http://www.example.com/more/index.html'

    >>> get_explicit_url('http://www.example.com/more/page.html')  # doctest: +SKIP
    'http://www.example.com/more/page.html'

    >>> get_explicit_url('http://www.example.com/file')  # doctest: +SKIP
    'http://www.example.com/file'
    """
    content_type = response.headers.get('Content-Type', '').lower()
    if isinstance(content_type, bytes):
        content_type = content_type.decode()
    url = response.url

    # Check if the URL is pointing to an HTML file or another type of file
    if 'text/html' in content_type:
        # print(
        #     f"-------------------------\n\n"
        #     f"{url=}\n"
        #     f"\n\n-------------------------"
        #     )
        if not url.endswith('.html'):
            # URL points to a directory
            url = urljoin(url, 'index.html')

    return url


class RecursiveDownloader(scrapy.Spider):
    """
    A Scrapy spider that recursively downloads contents from a given URL to a local dir.

    :param start_url: The URL to start downloading from.
    :param url_to_filepath: The function to convert URLs to local filepaths.
    :param depth: The maximum depth to follow links.
    :param filter_urls: A function to filter URLs to download.
    :param mk_missing_dirs: Whether to create missing directories.
    :param verbosity: The verbosity level.
    :param rootdir: The root directory to save the downloaded files.
    :param extra_kwargs: Extra keyword arguments to pass to the Scrapy spider.
    """

    name = "recursive_downloader"

    def __init__(
        self,
        start_url: str,
        rootdir: str = DFLT_ROOTDIR,
        *,
        depth: int = 1,
        filter_urls: Optional[Callable[[str], bool]] = None,
        mk_missing_dirs: bool = True,
        verbosity: int = 0,
        url_to_filepath: Optional[Union[str, Callable[[str], str]]] = url_to_localpath,
        **extra_kwargs,
    ):
        self.start_urls = [start_url]
        self.allowed_domains = [urlparse(start_url).netloc]
        self.url_to_filepath = partial(url_to_filepath, rootdir=rootdir)
        self.depth = depth
        self.filter_urls = filter_urls
        self.mk_missing_dirs = mk_missing_dirs
        self.custom_settings = {
            'LOG_LEVEL': ['ERROR', 'INFO', 'DEBUG'][verbosity],
            'DEPTH_STATS': True,
            'DEPTH_PRIORITY': 1,
            **extra_kwargs,
        }
        super().__init__()

    def parse(self, response: scrapy.http.Response):
        url = response.url
        filepath = self.url_to_filepath(url)
        dirpath = os.path.dirname(filepath)

        if not os.path.exists(dirpath):
            if self.mk_missing_dirs:
                os.makedirs(dirpath)
            else:
                raise FileNotFoundError(
                    f"Directory (needed to save scrapes) not found: {dirpath}"
                )

        with open(filepath, 'wb') as f:
            f.write(response.body)

        if self.custom_settings['LOG_LEVEL'] != 'ERROR':
            self.log(f"Downloaded {response.url} to {filepath}")

        depth = response.meta.get('depth', 0)

        if depth < self.depth:
            link_extractor = LinkExtractor()
            for link in link_extractor.extract_links(response):
                if not self.filter_urls or self.filter_urls(link.url):
                    yield response.follow(
                        link.url, self.parse, meta={'depth': depth + 1}
                    )


def _download_site(
    url: str,
    url_to_filepath: Optional[Union[str, Callable[[str], str]]] = url_to_localpath,
    *,
    depth: int = 1,
    filter_urls: Optional[Callable[[str], bool]] = None,
    mk_missing_dirs: bool = True,
    verbosity: int = 0,
    rootdir: str = DFLT_ROOTDIR,
    **extra_kwargs,
):
    """
    Recursively downloads contents from the given URL to a local folder.

    :param start_url: The URL to start downloading from.
    :param url_to_filepath: The function to convert URLs to local filepaths.
    :param depth: The maximum depth to follow links.
    :param filter_urls: A function to filter URLs to download.
    :param mk_missing_dirs: Whether to create missing directories.
    :param verbosity: The verbosity level.
    :param rootdir: The root directory to save the downloaded files.
    :param extra_kwargs: Extra keyword arguments to pass to the Scrapy spider.

    """

    process = CrawlerProcess()
    process.crawl(
        RecursiveDownloader,
        start_url=url,
        url_to_filepath=url_to_filepath,
        depth=depth,
        filter_urls=filter_urls,
        mk_missing_dirs=mk_missing_dirs,
        verbosity=verbosity,
        rootdir=rootdir,
        **extra_kwargs,
    )
    process.start()


def download_site(
    url: str,
    url_to_filepath: Optional[Union[str, Callable[[str], str]]] = url_to_localpath,
    *,
    depth: int = 1,
    filter_urls: Optional[Callable[[str], bool]] = None,
    mk_missing_dirs: bool = True,
    verbosity: int = 0,
    rootdir: str = DFLT_ROOTDIR,
    **extra_kwargs,
):
    """
    Recursively downloads contents from the given URL to a local folder.

    :param start_url: The URL to start downloading from.
    :param url_to_filepath: The function to convert URLs to local filepaths.
    :param depth: The maximum depth to follow links.
    :param filter_urls: A function to filter URLs to download.
    :param mk_missing_dirs: Whether to create missing directories.
    :param verbosity: The verbosity level.
    :param rootdir: The root directory to save the downloaded files.
    :param extra_kwargs: Extra keyword arguments to pass to the Scrapy spider.

    """

    _crawl = partial(
        _download_site,
        url,
        url_to_filepath,
        depth=depth,
        filter_urls=filter_urls,
        mk_missing_dirs=mk_missing_dirs,
        verbosity=verbosity,
        rootdir=rootdir,
        **extra_kwargs,
    )

    p = multiprocessing.Process(target=_crawl)
    p.start()
    p.join()

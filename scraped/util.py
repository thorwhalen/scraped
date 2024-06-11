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

_DFLT_DATA_ROOTDIR = get_app_data_folder('scraped/data', ensure_exists=True)
DFLT_ROOTDIR = os.environ.get('SCRAPED_DFLT_ROOTDIR', _DFLT_DATA_ROOTDIR)


def url_to_localpath(url: str, rootdir: str = DFLT_ROOTDIR) -> str:
    path = _url_to_localpath(url)
    if rootdir:
        return os.path.join(rootdir, path)
    return path


# def explicit_url(response) -> str:
#     """
#     Takes a URL and returns a more explicit URL indicating the actual filepath.
#     (specifically, if it's an HTML file, a folder, or some other kind of file)
#     on the server side.

#     Parameters:
#     url (str): The input URL to be analyzed.

#     Returns:
#     str: A more explicit URL indicating the actual file path.

#     Examples:
#     >>> get_explicit_url('http://www.example.com/more')  # doctest: +SKIP
#     'http://www.example.com/more/index.html'

#     >>> get_explicit_url('http://www.example.com/more/page.html')  # doctest: +SKIP
#     'http://www.example.com/more/page.html'

#     >>> get_explicit_url('http://www.example.com/file')  # doctest: +SKIP
#     'http://www.example.com/file'
#     """
#     content_type = response.headers.get('Content-Type', '').lower()
#     if isinstance(content_type, bytes):
#         content_type = content_type.decode()
#     url = response.url

#     # Check if the URL is pointing to an HTML file or another type of file
#     if 'text/html' in content_type:
#         # print(
#         #     f"-------------------------\n\n"
#         #     f"{url=}\n"
#         #     f"\n\n-------------------------"
#         #     )
#         if not url.endswith('.html'):
#             # URL points to a directory
#             url = urljoin(url, 'index.html')

#     return url


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
        url_to_filepath: Optional[Union[str, Callable[[str], str]]] = None,
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
            'LOG_FORMAT': '%(levelname)s: %(message)s',
            'LOG_FILE': None,  # Disable logging to file
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
    url_to_filepath: Optional[Union[str, Callable[[str], str]]] = None,
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

    process = CrawlerProcess(
        {
            'LOG_LEVEL': ['ERROR', 'INFO', 'DEBUG'][verbosity],
            'LOG_FORMAT': '%(levelname)s: %(message)s',
            'LOG_FILE': None,  # Disable logging to file
        }
    )
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


# TODO: Return object that can be used to (a) know where the data is being saved,
#  and (b) status on progress
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


def markdown_of_site(
    url: str,
    *,
    depth: int = 1,
    filter_urls: Optional[Callable[[str], bool]] = None,
    verbosity: int = 0,
    rootdir: str = DFLT_ROOTDIR,
    **extra_kwargs,
):
    from pdfdol import html_to_markdown

    # make a temporary directory, ensuring it is empty
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmpdir:
        # download the site to the temporary directory

        _url_to_localpath = partial(url_to_localpath, rootdir=tmpdir)
        download_site(
            url,
            url_to_filepath=_url_to_localpath,
            depth=depth,
            filter_urls=filter_urls,
            verbosity=verbosity,
            rootdir=tmpdir,
            **extra_kwargs,
        )
        # convert the site to markdown

        markdown = html_to_markdown(str(tmpdir))

    return markdown


import requests
import os
import mimetypes
from urllib.parse import unquote
from graze.base import _url_to_localpath


# This function is not used in the current implementation of the package,
# but is provided here in case it is needed in the future.
# TODO: Itegrate the option to use content disposistion in the download_file function.
#   Note that this will sill require to use the filepath given by the url, to keep
#   since content-disposition gives a filename, but not a path.
def _filename_from_content_disposition(content_disposition):
    """
    Extract filename from the Content-Disposition header if available.

    :param content_disposition: The Content-Disposition header value.
    :type content_disposition: str
    :return: The extracted filename or None if not found.
    :rtype: str or None
    """
    if not content_disposition:
        return None
    parts = content_disposition.split(';')
    for part in parts:
        if part.strip().startswith('filename='):
            filename = part.split('=')[1].strip('"')
            return unquote(filename)  # Decode percent-encoded filename
    return None


def _extension_from_mime(mime_type, custom_mime_map=None):
    """
    Get the file extension for a given MIME type using the mimetypes module and custom map.

    :param mime_type: The MIME type of the file.
    :type mime_type: str
    :param custom_mime_map: A dictionary mapping MIME types to file extensions.
    :type custom_mime_map: dict
    :return: The file extension for the given MIME type, or an empty string if not found.
    :rtype: str
    """
    custom_mime_map = custom_mime_map or {}
    if mime_type in custom_mime_map:
        return custom_mime_map[mime_type]
    extension = mimetypes.guess_extension(mime_type)
    return extension if extension else ""


def _extension_from_response(response, *, custom_mime_map=None):
    """
    Determine the file extension from the response headers.

    :param response: The HTTP response object.
    :type response: requests.Response
    :param custom_mime_map: A dictionary mapping MIME types to file extensions.
    :type custom_mime_map: dict
    :return: The file extension for the content of the response,
        or an empty string if not found.
    :rtype: str
    """
    content_type = response.headers.get('Content-Type', '')
    return _extension_from_mime(content_type, custom_mime_map)


def _dflt_extension_cast(extension, *, prefix='.content_type'):
    if extension:
        return prefix + extension
    return extension


# Note: Uses requests.get, where as we use scrapy's get method in scrapy, which
#    return a scrapy.http.Response object, which does not have a content attribute
#    (the equivalent is the body attribute, which is a bytes object)
# TODO: Make the download_file function compatible with scrapy's Response object.
def download_file(
    url,
    save_directory,
    *,
    url_to_filename: Callable[[str], str] = _url_to_localpath,
    extension_cast: Optional[Callable] = _dflt_extension_cast,
    custom_mime_map=None,
    content_attribute='content',  # change to 'body' for scrapy
):
    """
    Download a file from the given URL and save it to the specified directory with the correct extension.

    :param url: The URL of the file to download.
    :type url: str
    :param extension_cast: A function to cast the extension to a different format.
    :param save_directory: The directory where the file will be saved.
    :type save_directory: str
    :param custom_mime_map: A dictionary mapping MIME types to file extensions. Defaults to None.
    :type custom_mime_map: dict, optional
    :raises Exception: If the file could not be downloaded successfully.

    The reason to cast the extension is to allow for the possibility of making
    the extension so that we recognize if it comes from the url or from the
    content-type.
    """
    response = requests.get(url)
    __extension_from_response = partial(
        _extension_from_response, custom_mime_map=custom_mime_map
    )
    if response.status_code == 200:
        filename = url_to_filename(url)
        extension = __extension_from_response(response)
        if extension_cast:
            extension = extension_cast(extension)

        # Ensure the save directory exists
        os.makedirs(save_directory, exist_ok=True)

        # Full path to save the file
        save_path = os.path.join(save_directory, filename)

        # Save the content to file
        content_bytes = getattr(response, content_attribute)
        with open(save_path, 'wb') as file:
            file.write(content_bytes)
    else:
        raise Exception(
            f"Failed to download the file. Status code: {response.status_code}"
        )

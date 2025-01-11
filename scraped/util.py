"""Utils for scraped."""

import os
from functools import partial
from typing import Iterable, Mapping, Optional, Callable, Union, Tuple, List, Dict
import multiprocessing
import re
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse, urljoin

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor

import html2text
from config2py import get_app_data_folder
from graze.base import url_to_localpath as graze_url_to_localpath

_DFLT_DATA_ROOTDIR = get_app_data_folder('scraped/data', ensure_exists=True)
DFLT_ROOTDIR = os.environ.get('SCRAPED_DFLT_ROOTDIR', _DFLT_DATA_ROOTDIR)


def url_to_localpath(url: str, rootdir: str = DFLT_ROOTDIR) -> str:
    """Convert a URL to a local file path, considering slashes as marking directories"""
    path = graze_url_to_localpath(url)
    if rootdir:
        return os.path.join(rootdir, path)
    return path


def url_to_filename(url: str) -> str:
    """
    Convert a URL to a filename (getting rid of http header and slashes)
    """
    # remove slash suffix if there
    if url[-1] == '/':
        url = url[:-1]
    return url.replace('https://', '').replace('http://', '').replace('/', '__')


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


def is_html_content(content: Union[str, bytes]) -> bool:
    """
    Check if the given content is HTML.

    :param content: The content to check, either a string or bytes.
    :return: True if the content is HTML, otherwise False.

    >>> html_string = "<html><head><title>Test</title></head><body><p>Hello, World!</p></body></html>"
    >>> non_html_string = "This is just a plain text."
    >>> is_html_content(html_string)
    True
    >>> is_html_content(non_html_string)
    False
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='ignore')

    # A simple regex to check for common HTML tags
    html_tags = re.compile(
        (
            r'<(html|head|body|title|meta|link|script|style|div|span|p|a|img|table|tr'
            r'|td|ul|ol|li|h1|h2|h3|h4|h5|h6|br|hr|!--)'  # Opening tags
        ),
        re.IGNORECASE,
    )

    if html_tags.search(content):
        return True
    return False


# TODO: Replace this by a version that uses dol.store_aggregate
def html_to_markdown(
    htmls: Union[str, Iterable[str], Mapping[str, str]],
    save_filepath: Optional[str] = None,
    *,
    content_filt=is_html_content,
    markdown_contents_aggregator: Callable = "\n\n".join,
    prefixes=None,
    **html2text_options,
):
    """
    Convert one or several HTML files into a single Markdown file or return the
    Markdown string(s).

    :param htmls: A single file path, an iterable of file paths, or a mapping of
        names to file paths.
    :param save_filepath: The file path where the combined Markdown will be saved,
        or the folder path where it should be saved (a name will be generated based
        on the url).
        If None, returns the Markdown string.
    :param content_filt: A function to filter the content to be converted to Markdown.
    :param markdown_contents_aggregator: A function to aggregate the Markdown strings.
    :param prefixes: A list of prefixes to be woven with to each Markdown string
        (there must be the same number of prefixes as HTML files).
    :param html2text_options: Options to pass to the html2text.HTML2Text()
        converter.
    :return: Combined Markdown string if save_filepath is None, otherwise returns the
        path where the Markdown file was saved.

    Tips:

    - If you want to just get a list of Markdown strings, set `save_filepath=None`
        and `markdown_contents_aggregator=list`.
    """

    def read_html_file(filepath):
        return Path(filepath).read_bytes()

    if isinstance(htmls, Mapping):
        html_contents = filter(content_filt, htmls.values())
    else:
        if isinstance(htmls, str):
            if htmls.endswith(".html"):
                html_contents = [read_html_file(htmls)]
            elif len(htmls) < 1000 and Path(htmls).is_dir():
                # TODO: Handle this better, and in such a way that directories can be
                #   captured and produce their own markdown content, which will then
                #   be combined with the rest of the markdown content.

                # For now though:
                # Recursively find all HTML files in the directory
                html_contents = map(
                    read_html_file, filter(Path.is_file, Path(htmls).rglob("*"))
                )
            else:
                html_contents = [htmls]
        if not isinstance(htmls, Iterable):
            raise TypeError(
                f"htmls must be an iterable of file paths or a mapping, not {htmls}"
            )
        # html_contents = map(read_html_file, htmls)

    # Initialize the html2text converter with options
    converter = html2text.HTML2Text()
    for key, value in html2text_options.items():
        setattr(converter, key, value)

    # Convert HTML contents to Markdown
    def _markdown_contents(html_contents):
        for html_content in html_contents:
            try:
                if isinstance(html_content, bytes):
                    html_content = html_content.decode()
                yield converter.handle(html_content)
            except UnicodeDecodeError:
                print(f"Failed to decode HTML content: {html_content[:30]=}")
                # TODO: Give more control to the user to decide what to do in this case
                # skip it
                pass

    markdown_contents = list(_markdown_contents(html_contents))

    if prefixes:
        markdown_contents = (
            f"{prefix}\n{markdown}"
            for prefix, markdown in zip(prefixes, markdown_contents)
        )
    combined_markdown = markdown_contents_aggregator(markdown_contents)

    if save_filepath:
        Path(save_filepath).expanduser().absolute().write_text(combined_markdown)
        return save_filepath
    else:
        return combined_markdown


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
    save_filepath: Optional[str] = None,
    deduplicate_lines_min_block_size: Optional[int] = None,
    verbosity: int = 0,
    dir_to_save_page_slurps: str = None,
    **extra_kwargs,
):
    """
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
    - deduplicate_lines_min_block_size: The minimum block size to deduplicate lines.
    - verbosity: The verbosity level.
    - dir_to_save_page_slurps: The directory to save the downloaded pages.
    - extra_kwargs: Extra keyword arguments to pass to the Scrapy spider.

    Returns:
    - The Markdown string of the site (if save_filepath is None), otherwise the save_filepath.

    Note: deduplicate_lines_min_block_size requires the hg package to be installed.
        We advise you to install it using `pip install hg`, and use 
        `hg.deduplicate_string_lines` directly on the output markdown string if you 
        don't know what the minimum block size should be. This will avoid having to 
        redowload the site if you want to change the minimum block size.
        
    >>> markdown_of_site(
    ...     "https://i2mint.github.io/dol/",
    ...     depth=2,
    ...     save_filepath='~/dol_documentation.md'
    ... )  # doctest: +SKIP
    '~/dol_documentation.md'

    If you don't specify a `save_filepath`, the function will return the Markdown
    string, which you can then analyze directly, and/or store as you wish.

    >>> markdown_string = markdown_of_site("https://i2mint.github.io/dol/")  # doctest: +SKIP
    >>> print(f"{type(markdown_string).__name__} of length {len(markdown_string)}")  # doctest: +SKIP
    str of length 626439

    """

    # process the save_filepath
    if save_filepath:
        # if it's a directory, extend it to contain the filename
        if os.path.isdir(save_filepath):
            save_filepath = os.path.join(save_filepath, url_to_filename(url) + '.md')
        else:  # check that the directory containing the filepath exists
            containing_dir = os.path.dirname(save_filepath)
            if containing_dir == '':
                containing_dir = os.getcwd()
            if not os.path.exists(containing_dir):
                raise FileNotFoundError(
                    f"Directory (needed to save Markdown) not found: {containing_dir}"
                )

    # make a temporary directory, ensuring it is empty
    # TODO: Wasteful to make a tmpdir if dir_to_save_page_slurps is given. Instead,
    #   would be nice to use a placeholder context manager that does nothing if the
    #   directory is given.
    if not dir_to_save_page_slurps:
        dir_to_save_page_slurps = TemporaryDirectory(prefix='scraped_').name
    else:
        assert os.path.isdir(
            dir_to_save_page_slurps
        ), f"dir_to_save_page_slurps must be a directory: {dir_to_save_page_slurps}"

    # download the site to the temporary directory
    _url_to_localpath = partial(url_to_localpath, rootdir=dir_to_save_page_slurps)

    download_site(
        url,
        url_to_filepath=_url_to_localpath,
        depth=depth,
        filter_urls=filter_urls,
        verbosity=verbosity,
        rootdir=dir_to_save_page_slurps,
        **extra_kwargs,
    )

    # convert the site to markdown
    markdown = html_to_markdown(dir_to_save_page_slurps, save_filepath=save_filepath)

    if deduplicate_lines_min_block_size:
        markdown, _ = deduplicate_lines(
            markdown, min_block_size=deduplicate_lines_min_block_size
        )

    return markdown


def deduplicate_lines(
    text: str, min_block_size: int = 5, key: Optional[Callable] = hash
) -> Tuple[str, List[Dict]]:
    """
    Deduplicate text Deduplicate text lines.

    Returns:
       - final_text: deduplicated text (lines joined by newline)
       - removed_blocks: metadata about removed blocks 

    :param text:             The input string.
    :param min_block_size:   The size for initial block match.
    :param key:              Optional key function mapping each line to a comparable/hashable value.
                             If None, lines are hashed as-is.
    """
    from hg import deduplicate_string_lines  # pip install hg
    return deduplicate_string_lines(text, min_block_size=min_block_size, key=key)


import requests
import os
import mimetypes
from urllib.parse import unquote

# from graze.base import url_to_localpath


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
    url_to_filename: Callable[[str], str] = url_to_localpath,
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

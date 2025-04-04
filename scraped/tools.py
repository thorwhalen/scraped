"""Scraping tools"""

from functools import partial
from scraped.util import markdown_of_site, download_site, DFLT_STORE_DIR

markdown_of_site_depth_3 = partial(markdown_of_site, depth=3)


import os
import requests
from typing import Dict, Union, MutableMapping, KT, VT, TypeVar, Callable, Any
from functools import partial


URI = TypeVar('URI')
Dirpath = str
ContentType = TypeVar('ContentType')
StoreFunc = Callable[[KT, ContentType], None]


def is_not_none(x):
    return x is not None


# TODO: Make a general uri_to_content function parametrized by a plugin architecture for the
#   uri->uri_to_content function (see graze, pyckyp, dol, dacc for ideas and code)
def acquire_content(
    uri_to_content: Callable[[URI], ContentType],
    uris: Dict[KT, URI] = None,
    store: Union[Dirpath, MutableMapping, StoreFunc] = DFLT_STORE_DIR,
    *,
    save_condition: Callable[[Any], bool] = is_not_none,
):
    """
    Downloads and stores content from a given set of URIs.

    uri_to_content is a callable function that takes a URI and returns content. This is usually set to:
    - a function that reads file content, like `open(filepath).read()`
    - a function that fetches URL content, like `requests.get(url).content`

    However, here, we demonstrate with a simple string operation (e.g., uppercasing strings) as a substitute
    to show functionality.

    Note that the uri_to_content function will usually be something giving you the
    contents of a file or URL.

    >>> from pathlib import Path
    >>> files_uri_to_content = lambda filepath: Path(filepath).read_text()
    >>> urls_uri_to_content = lambda url: requests.get(url).content  # doctest: +SKIP

    Here, we use a simple `str.upper` to not have to deal with actual IO during tests:
    Also, we'll use a dict as a store, for test simplicity purposes.
    Usually, though, you'll want to use a directory or a MutableMapping as store,
    or a function that stores content in a specific way.

    >>> store = {}
    >>> uris = {'example1': 'hello', 'example2': 'world'}
    >>> acquire_content(str.upper, uris, store)  # uri_to_content here is str.upper, to simulate content acquisition.
    >>> store
    {'example1': 'HELLO', 'example2': 'WORLD'}

    Note that often you want to just fix the uri_to_content function and sometimes store.
    The acquire_content acts as a function factory for your convenience. If you don't
    specify uris (but at least specify `uri_to_content`), you get a function that takes
    uris as the first argument, and stores the content therefrom.

    >>> content_acquirer = acquire_content(str.upper, store=store)  # doctest: +ELLIPSIS
    >>> content_acquirer({'example3': 'foo', 'example4': 'bar'})
    >>> store
    {'example1': 'HELLO', 'example2': 'WORLD', 'example3': 'FOO', 'example4': 'BAR'}


    # Examples that would be typical for uri_to_content:
    # acquire_content(lambda filepath: open(filepath, 'rb').read(), uris, store)  # Reads file content +SKIP
    # acquire_content(lambda url: requests.get(url).content, uris, store)  # Fetches URL content +SKIP

    See:
    * [A tiny flexible data acquisition python function](https://medium.com/@thorwhalen1/a-tiny-flexible-data-acquisition-python-function-518289dcd1e6) and 
    * [gist](https://gist.github.com/thorwhalen/e8fe6c0454ab2109d4713f886b38bbda)

    """
    # if uris is None, we're parametrizing the download_content function
    store = _ensure_store_func(store)

    if uris is None:
        assert callable(
            uri_to_content
        ), "uri_to_content must be a callable if uris is None"
        return partial(acquire_content, uri_to_content, store=store)

    # Loop through uris and store the processed content
    for key, uri in uris.items():
        content = uri_to_content(uri)
        if save_condition(content):
            store(key, content)


def _ensure_store_func(store: Union[Dirpath, MutableMapping, Callable]) -> StoreFunc:
    """
    Ensures a store function is returned based on the type of 'store' argument provided.

    - If store is a callable, it returns store directly.
    - If store is a directory path, it creates a Files object (using dol) to manage file storage in that directory.
    - If store is a MutableMapping, it returns the __setitem__ method of the store.
    - If none of these types match, a ValueError is raised.

    Examples:

    >>> store = {}
    >>> func = _ensure_store_func(store)
    >>> func('key', 'value')  # should store the value in the dictionary
    >>> assert store == {'key': 'value'}

    Let's specify a (temporary) directory path as the store:
    >>> import tempfile
    >>> store = tempfile.gettempdir()
    >>> try:
    ...     func = _ensure_store_func(store)
    ... except ValueError:
    ...     print("Directory does not exist, as expected.")  # Simulates an invalid directory check

    >>> _ensure_store_func(lambda k, v: print(f"Storing {k}: {v}"))  # doctest: +ELLIPSIS
    <function <lambda> at ...>

    """
    if callable(store):
        return store
    elif isinstance(store, str):
        dirpath = os.path.expanduser(store)
        if os.path.isdir(dirpath):
            from dol import Files

            return Files(dirpath).__setitem__
        else:
            raise ValueError(f"The directory path {dirpath} does not exist.")
    elif isinstance(store, MutableMapping):
        # If store is a MutableMapping, we'll use its __setitem__ method
        store_obj = store
        return store_obj.__setitem__
    else:
        raise ValueError(
            "uri_to_content must be a callable, or MutableMapping, or a dir path"
        )


# A few useful uri_to_content functions, elegantly defined as (picklable) function compositions

from dol import Pipe
from pathlib import Path
import requests
from operator import methodcaller, attrgetter

acquire_content.path_to_bytes = Pipe(Path, methodcaller('read_bytes'))
acquire_content.path_to_string = Pipe(Path, methodcaller('read_text'))


@acquire_content
def url_to_bytes(url: URI, verbose: int = 2) -> bytes:
    verbose = int(verbose)
    try:
        response = requests.get(url)
        response.raise_for_status()  # Check for HTTP errors
        if verbose >= 2:
            print(f"Successfully downloaded and stored contents from {url}")
        return response.content
    except requests.exceptions.RequestException as e:
        if verbose >= 1:
            print(f"Failed to download from {url}: {e}")

acquire_content.url_to_bytes = url_to_bytes


def scrape_multiple_sites(
    name_and_url: dict,
    save_dir: str = ".",
    *,
    url_scrape_function: callable = markdown_of_site_depth_3,
):
    """
    Scrape multiple URLs and save the results to a directory.

    Args:
    - name_and_url: a dictionary mapping names to URLs
    - save_dir: the directory to save the results to
    - url_scrape_function: the function to use to scrape the URLs

    """
    from lkj import print_progress
    import scraped
    import os

    save_dir = os.path.abspath(os.path.expanduser(save_dir))
    # raise if save_dir is not a directory
    if not os.path.isdir(save_dir):
        raise NotADirectoryError(f"{save_dir} is not a directory")

    def gen():
        for i, (name, url) in enumerate(name_and_url.items(), 1):
            try:
                print_progress(f"Scraping {name} ({url})...")
                md_content = url_scrape_function(url, depth=3)
                with open(f"{save_dir}/{name}.md", "w") as f:
                    f.write(md_content)
            except Exception as e:
                print(f"Error scraping {name} ({url}): {e}")
                print("Continuing (but returning (name, url) pair in output dict)...")
                yield name, url

    return dict(gen())


def main():
    """
    Run the command-line interface of scraped.
    """
    import argh

    argh.dispatch_commands(
        [
            markdown_of_site,
            download_site,
            scrape_multiple_sites,
        ]
    )


if __name__ == "__main__":
    main()

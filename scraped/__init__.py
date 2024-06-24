"""Tools for scraping"""

from scraped.util import download_site, markdown_of_site
from scraped.tools import scrape_multiple_sites

# ------------------------------------------------------------------------------
# other useful tools from other packages

from contextlib import suppress as _suppress

with _suppress(ImportError, ModuleNotFoundError):
    from hubcap import repo_text_aggregate as github_repo_text_aggregate

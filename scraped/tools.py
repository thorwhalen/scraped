"""Scraping tools"""

from functools import partial
from scraped.util import markdown_of_site, download_site

markdown_of_site_depth_3 = partial(markdown_of_site, depth=3)


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
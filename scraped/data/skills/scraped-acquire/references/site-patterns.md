# Site patterns

What earlier jobs learned about each site, one section per host, written by
`scraped.acquire.remember` after a run. Public sites only: authenticated flows,
internal hosts and anything that looks like a credential go to
`site-patterns.local.md` beside this file, which is never shared.

## books.toscrape.com

- transport: http
- payload: dom
- pagination: next link (catalogue/page-N.html)
- seen: 2026-10-26
- note: static HTML, no state blob, no JSON

## en.wikipedia.org

- generator: MediaWiki
- transport: http
- payload: api
- pagination: cursor (continue=continue, apcontinue=apcontinue echoed back from the previous response)
- seen: 2026-10-26
- note: w/api.php?action=query&format=json returns JSON; page HTML carries a JSON-LD Article blob; sitemap listed in robots.txt

## news.ycombinator.com

- transport: http
- payload: api
- seen: 2026-10-26
- note: robots.txt asks Crawl-delay 30 seconds, so prefer the Firebase API on hacker-news.firebaseio.com (v0/topstories.json returns up to 500 ids)

## quotes.toscrape.com

- transport: http
- payload: api
- pagination: page number (api/quotes?page=N, stop when has_next is false)
- seen: 2026-10-26
- note: HTML pages carry no state blob; /api/quotes returns JSON with has_next, page, quotes, tag, top_ten_tags (10 quotes per page)

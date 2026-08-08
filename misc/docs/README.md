# `scraped` reference docs

Index of durable reference material. Read a document on demand; don't load the
folder wholesale.

| Document | Synopsis |
|---|---|
| [`acquisition-architecture.md`](acquisition-architecture.md) | Design record for `scraped.acquire`: why "acquisition" rather than scraping/slurping, the two-axis (payload × transport) model, the `Capture` record, the seams to extraction/formatting, the escalation ladder and its diagnostic signal table. **Start here.** |
| [`acquisition-tooling-survey.md`](acquisition-tooling-survey.md) | Library landscape with health verdicts, verified 2026-08-08: which HTTP clients, browser drivers, parsers, and caches to use, which are abandoned or carry licensing/supply-chain risk, and why. Re-verify before trusting. |
| [`browser-result-exfiltration.md`](browser-result-exfiltration.md) | How to get a payload out of an automated browser: the three independent gates (harness output cap, CDP frame limit, Chrome Local Network Access), the "browser writes to disk, agent reads the path" rule, known-good patterns in order, and anti-patterns with their symptoms. |

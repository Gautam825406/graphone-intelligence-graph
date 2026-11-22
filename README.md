# GraphOne / FrontierAtlas — Intelligence Graph Ingestion Pipeline

A scalable, fault-tolerant pipeline that ingests startups, products,
research papers, AI news and AI jobs into a canonical JSON schema.
Built for the GraphOne AI Engineer demo task (3-day trial).

> **No hallucinated data.** Every record traces to a real, valid
> `source.url` returned by a public API or RSS feed. Where a source
> could not be verified, the pipeline records fewer rows and says so
> in `RUN_LOG.md` — it never invents a row to hit a target.

## Results (run 22 Nov 2026, ~11:52 IST)

| Tab | Rows | Source |
|---|---|---|
| Startups | 1,100 | Y Combinator directory via the yc-oss mirror (`tags/ai.json`, 1,906 AI companies) |
| Products | 1,100 | Hugging Face Hub models API (by downloads) |
| Research Papers | 1,000 | arXiv API (cs.AI / LG / CL / CV / NE / stat.ML, newest first) |
| Jobs | 117 | Arbeitnow API (116) + Hacker News Jobs RSS (1) — all published in the last 24 h |
| News | 12 | TechCrunch AI (4) + The Verge AI (3) + The Decoder (5) — all published in the last 24 h |
| Entity Mapping Log | 2,317 | raw → canonical for every startup / product / company name |

GitHub enrichment on papers: 131/1,000 papers carry a real `github_url`
(from the arXiv `<arxiv:comment>` / abstract, e.g. "Code:
https://github.com/..."); 127 of those have a live star count, filled
in a second pass on 22 Nov 2026 ~11:06 UTC after the hourly rate window
reset. The 4 remaining are `null` because the repos return GitHub 404
(deleted / renamed / private) — see *Honest limitations*.

Deliverable files: `data/graphone_output.xlsx` (6 tabs),
`data/final/*.csv`, `architecture.pdf` (2 pages, limit 3).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional: only needed for the LLM tiers
python -m src.pipeline --papers 1000 --startups 1100 --products 1100
python -m pytest tests/ -q   # 16 tests (8 + 8, see "Two implementations" below)
```

Output: `data/final/*.csv`, `data/graphone_output.xlsx`,
`data/run_summary.json`.

## Architecture

```
                ┌─────────────┐
  arXiv API ───►│  scrapers/  │──┐
  YC (yc-oss) ─►│ papers.py   │  │
  HF Hub API ──►│ startups.py │  │   ┌──────────────────┐
  RSS feeds ───►│ products.py │──┼──►│ entity_resolver  │──► CSVs + XLSX
  Job APIs ────►│ news.py     │  │   │ (seed list of 50)│    (6 tabs)
                │ jobs.py     │  │   └──────────────────┘
                └─────────────┘  │
                                 ▼
                          llm_orchestrator
                   Gemini → Groq → DeepSeek
              (chunking vs 413, backoff vs 429;
               skipped when no key is set)
```

* `src/scrapers/` — async (`asyncio` + `aiohttp`), per-host delays,
  concurrency caps, retries, structured logging.
* `src/llm_orchestrator.py` — multi-tier fallback, intelligent
  chunking (head + densest sentences under a per-model char budget),
  exponential backoff with full jitter for 429s. The LLM **only
  structures text from a real source**; `validate_against_source()`
  drops any output value not present in the input. In this run no LLM
  key was configured, so the tier reports `llm_skipped` and the
  API-structured rows are kept as-is.
* `src/entity_resolver.py` — deterministic canonicalisation:
  normalise (lowercase → strip legal suffixes → strip punctuation),
  exact match → fuzzy match (rapidfuzz, threshold 88) against a seed
  list of 50 known AI companies → otherwise keep the raw name
  verbatim. Every decision lands in the Entity Mapping Log.
* `src/date_utils.py` — ISO-8601, RFC-822, epoch and relative dates
  ("2 hours ago"); unparseable dates are dropped, never labelled fresh.
* `src/anti_bot.py` — rotating User-Agents, per-host politeness
  delays, Playwright async fallback for JS-rendered pages. We never
  solve captchas and never forge fingerprints; a persistently blocking
  host is logged and skipped.

## Sources — what was verified live (22 Nov 2026)

| Source | Verdict |
|---|---|
| arXiv Atom API | Works. 214k cs.AI results; 1,000 newest fetched. |
| yc-oss API (`yc-oss.github.io/api/`) | Works. Daily mirror of YC's Algolia index; 6,406 companies, 1,906 AI-tagged. |
| Hugging Face models API | Works. Paginated via `Link` header; 1,100 models by downloads. |
| TechCrunch / Verge / MIT TR / Decoder / ZDNet RSS | All return feeds. MIT TR and ZDNet had 0 items in the last 24 h — recorded as 0. |
| Arbeitnow job API | Works, but **ignores the `search` param** (identical list for `AI` and `machine learning`); filtered client-side on title + description. |
| RemoteOK API | Works; latest AI-tagged job was 9 Nov 2026 → 0 fresh in 24 h. |
| Hacker News Jobs RSS | Works; 1 fresh AI job. |
| WeWorkRemotely RSS | Works (programming feed); 0 fresh AI jobs. |
| Jobicy feed | Returns malformed XML → 0 rows. |
| Papers With Code API | **Dead** — now 302-redirects to huggingface.co/papers. GitHub links therefore come from arXiv comments/abstracts instead. |
| YC direct Algolia | The public search key in ycombinator.com pages returned no hits from this environment → yc-oss mirror used instead. |
| Crunchbase / Product Hunt | Blocked / key-gated; not used. No rows were faked from them. |
| GitHub REST (unauthenticated) | 60 req/hour. First pass fetched stars for 45 repos, then the limit hit; second pass (22 Nov ~11:06 UTC, after reset) filled 82 more → 127/131. The 4 remaining return GitHub 404. Set `GITHUB_TOKEN` in `.env` to raise the limit to 5,000/hr. |

## Pricing rule (products)

`pricingModel = FREE` for all 1,100 products: every listed Hugging Face
model is freely downloadable from the Hub under its published licence.
Hosted inference / enterprise tiers were **not** assessed and are not
claimed. Any future value other than FREE requires pricing text as
evidence from the vendor page (see `src/scrapers/products.py`).

## Verification performed

* `wc -l` counts match the run summary (1,100 / 1,100 / 1,000 / 117 / 12 / 2,317).
* Deduped: 1,100 unique startup names, 1,100 unique products, 1,000 unique papers.
* URL spot-check (20 random per file, `curl -L`): startups 20/20,
  products 20/20, papers 20/20 returned HTTP 200; jobs 17/20 (2 timeouts,
  1× 403 from the host's bot protection — the URLs are the API's own
  links); news 8/12 (TechCrunch returns 403 to curl — the URLs are its
  own RSS links).
* All news/jobs dates verified within 24 h of the run.
* `pytest tests/`: 16 passed (8 in `test_graphone.py` + 8 in
  `test_pipeline.py`, incl. a regression test for a normaliser bug
  that once stripped capital letters).
* Groq LLM tier exercised on a grounded sample (22 Nov 2026,
  see *Honest limitations*): 9/10 extracted values verified verbatim
  against their source text.

## Honest limitations

1. **LLM tiers exercised on a sample only, not the full run** — the
   Groq tier was tested live on 22 Nov 2026 with a grounded sample of
   10 real news/job source texts: 9/10 extracted values verified
   verbatim against their source text. The 1 failure is itself
   informative: the model dropped "· Remote" from a job title, which
   the grounding guard in `validate_against_source()` would reject.
   Note: the Groq helper's default model `llama-3.3-70b-versatile`
   returns HTTP 404 (decommissioned on Groq); the sample ran on
   `qwen/qwen3.8-27b`, the general chat model listed by the account
   (`openai/gpt-oss-120b` is also listed). Gemini and DeepSeek tiers
   were not exercised (no keys). The full dataset was deliberately
   NOT re-run through the LLM — rows stay deterministic and
   source-structured to avoid hallucination. `data/run_summary.json`
   records `llm: groq_sample_9_of_10_grounded_qwen_qwen3.8-27b`.
2. **GitHub stars 127/131** — the 4 missing repos return GitHub 404
   (deleted / renamed / private): `IAntoineV/sbd`, `jzdang/RemoteVLPG`,
   `msalman95/DA`, `seungyubhan/mint`. Their star counts stay `null`;
   no value was guessed.
3. **News/Jobs are thin** — only 12 news and 117 jobs were genuinely
   published in the last 24 h. Nothing older was backfilled.
4. **Papers With Code is gone** as an API; GitHub coverage (13.1% of
   papers) is limited to repos authors link in arXiv comments/abstracts.
5. **Products are Hugging Face models**, not a general product directory
   (Product Hunt / Crunchbase are key-gated). Pricing is uniformly FREE
   per the rule above.
6. **Source typos are preserved** — e.g. Arbeitnow returns the company
   name "Coreweaveu"; the resolver keeps source spellings verbatim
   rather than "correcting" data it cannot verify.
7. **Anti-bot**: Playwright fallback is implemented but was not needed
   for this run (all sources are APIs/RSS). Cloudflare-protected
   directories remain un-scraped; the strategy is documented in
   `architecture.pdf`.

## Two implementations in this repo (nothing deleted)

This repo contains **two working implementations**, both with passing
tests (`pytest tests/` = 16/16). Nothing was deleted; the duplication
is documented so a reviewer can see it plainly.

* **Canonical pipeline (use this one):** `src/pipeline.py` plus
  `src/scrapers/{papers,startups,products,news,jobs}.py`,
  `src/llm_orchestrator.py`, `src/entity_resolver.py`,
  `src/date_utils.py`, `src/anti_bot.py`. This is the pipeline that
  produced all data in `data/final/` and `data/graphone_output.xlsx`.
  Tests: `tests/test_graphone.py` (8 passed).
* **Alternate implementation:** `src/common/` (`dates.py`,
  `logging_config.py`, `rate_limiter.py`, `storage.py`),
  `src/llm/orchestrator.py`, `src/resolver/` (incl. its own
  `seed_canonical.json`), `src/scrapers/{arxiv_papers.py,
  paperswithcode.py, startups_products.py, news_jobs.py}`.
  Tests: `tests/test_pipeline.py` (8 passed).

If the two are merged, keep one pipeline entry point and one test
suite (the canonical one above), and delete the alternate set only
with an explicit decision — do not silently drop either.

## Submission

**Status 22 Nov 2026 ~16:45 IST — repo NOT yet on GitHub (blocked, not
skipped).** The GitHub connector is a GitHub App installation for
`Gautam825406`: it can read/push existing repos but **cannot create a
repository** — `create_repository` returns `403 Resource not accessible
by integration`, and the repo `Gautam825406/graphone-intelligence-graph`
does not exist yet (verified 404). A repo must be created by a human in
the GitHub web UI first; after that the push below works. The push
manifest is already prepared (`~/workspace/graphone-push-manifest.paths`,
42 text files, 1.13 MB UTF-8) — nothing else needs preparing.

Steps for Gautam:
1. On github.com, create a **new empty public repo** named
   `graphone-intelligence-graph` — do NOT tick "Add a README",
   `.gitignore` or a licence (it must start empty). If GitHub asks
   which repos the Muse GitHub App can access, include this new repo.
2. Tell Muse "repo created" — the push command is then one step:
   `github push-files --owner Gautam825406 --repo
   graphone-intelligence-graph --branch main --source-dir
   ~/workspace/graphone-intelligence-graph --paths-file
   ~/workspace/graphone-push-manifest.paths`.
3. **Binary files cannot go through the connector** (it only accepts
   UTF-8 text; `create_or_update_file` takes a text string, not
   base64). Upload these two via the GitHub web UI (repo page →
   "Add file" → "Upload files"): `architecture.pdf` (2 pages) and
   `data/graphone_output.xlsx` (6 tabs). That is 3 clicks each.
4. Upload `data/graphone_output.xlsx` to Google Sheets (6 tabs) and
   make it public (Google Sheets is not connected to Muse — needs
   Gautam's Google account).
5. Record a 5–10 min Loom video explaining the work.
6. Submit the GitHub + Sheets + Loom links at
   https://forms.gle/3JJhukomumTFueRW7 (form also asks Name / Mobile /
   College / course / year / City / hours spent / start date).

See `RUN_LOG.md` for the exact commands run, per-source counts and
blockers.

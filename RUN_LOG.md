# RUN LOG — GraphOne / FrontierAtlas demo task

Run date: **22 Nov 2026, ~10:41–10:56 UTC (16:11–16:26 IST)**. All times UTC unless noted.
Operator: Muse (subagent) for Gautam Keshri. Assignment PDF:
`~/workspace/user/files/AI_Engineer_Nov_2026.pdf`.

## Commands run

```bash
pip install --break-system-packages aiohttp feedparser rapidfuzz reportlab pytest pypdf
python -m src.pipeline --papers 1000 --startups 1100 --products 1100   # run 1: failed in jobs (RemoteOK tags bug)
python -m src.pipeline --papers 1000 --startups 1100 --products 1100   # run 2: OK, then resolver + jobs-filter fixes
python -m pytest tests/test_graphone.py -q                             # 8 passed
python -m src.pipeline --papers 1000 --startups 1100 --products 1100   # run 3 (FINAL): OK
python3 tools/make_architecture_pdf.py                                  # architecture.pdf, 2 pages
```

## Final counts (run 3, `data/run_summary.json`)

| Output | Rows |
|---|---|
| Startups | 1,100 (1,100 unique, 1,084 with employeeCount) |
| Products | 1,100 (1,100 unique, all pricingModel=FREE per disclosed rule) |
| Research Papers | 1,000 (1,000 unique) |
| Jobs | 117 (all within 24 h) |
| News | 12 (all within 24 h) |
| Entity Mapping Log | 2,317 (exact 65, fuzzy 1, self 2,251) |
| Papers with github_url | 131 |
| Papers with github_stars | 45 (run 3) → **127 (run 4, see below)** |

News per source: TechCrunch AI 4, The Verge AI 3, MIT Technology Review AI 0,
The Decoder 5, ZDNet AI 0.
Jobs per source: Arbeitnow 116, RemoteOK 0, Hacker News Jobs 1,
WeWorkRemotely 0, Jobicy 0.

## Sources used (all verified live 22 Nov 2026)

- arXiv API — 1,000 newest cs.AI/LG/CL/CV/NE + stat.ML papers.
- yc-oss API (`yc-oss.github.io/api/tags/ai.json`) — 1,906 AI-tagged YC companies; 1,100 taken.
- Hugging Face Hub models API — 1,100 models by downloads, paginated via Link header.
- RSS: TechCrunch AI, The Verge AI, MIT Tech Review AI, The Decoder, ZDNet AI.
- Jobs: Arbeitnow API, RemoteOK API, Hacker News Jobs RSS, WeWorkRemotely RSS, Jobicy feed.

## Blockers / failures encountered (all handled honestly)

1. **aiohttp + egress proxy**: `ssl.SSLError WRONG_VERSION_NUMBER` on first run.
   Fix: `ClientSession(trust_env=True)` via `src/anti_bot.py::new_session()`.
2. **Papers With Code API dead**: returns 302 to huggingface.co/papers.
   GitHub links therefore come from arXiv `<arxiv:comment>` / abstract regex.
3. **GitHub unauthenticated rate limit (60/hr)**: stars fetched for 45 repos,
   then 403; remaining stars left `null`. Set `GITHUB_TOKEN` to raise to 5,000/hr.
   **Resolved in run 4** (see below): after the hourly window reset, 82 more
   stars were fetched (86 attempted, 4× 404) → 127/131.
4. **YC direct Algolia**: public search key returned no hits → yc-oss mirror used.
5. **VentureBeat RSS 429, AI News RSS 403** → not used; The Decoder + ZDNet used instead.
6. **Arbeitnow ignores its `search` param** (identical list for `AI` vs `machine learning`).
   Fixed by client-side AI-keyword filtering on title + description (237 → 116 rows).
7. **RemoteOK**: latest AI job 9 Nov 2026 → 0 fresh in 24 h. **MIT Tech Review / ZDNet**: 0 fresh.
   **WeWorkRemotely AI feed**: 301 to an empty feed → working programming feed used, AI-filtered, 0 fresh.
   **Jobicy feed**: malformed XML → 0 rows. Nothing backfilled anywhere.
8. **Bugs found in own code and fixed**:
   - `entity_resolver.normalise()` stripped capital letters (`LeadGenius` → `eadenius`)
     because the punctuation class only kept `a-z`. Fixed by lowercasing first; regression test added.
   - `jobs.py`: RemoteOK `tags` is a list, not a string (`TypeError`, killed run 1). Fixed with join.

## Verification performed (22 Nov 2026)

- Counts via `wc -l` / pandas match the run summary; XLSX has exactly 6 tabs with matching row counts.
- Dedup: 1,100 unique startups, 1,100 unique products, 1,000 unique papers, 0 empty titles/names.
- URL spot-check, 20 random per file (`curl -L`): startups 20/20, products 20/20,
  papers 20/20 HTTP 200; jobs 17/20 (2 timeouts, 1× 403 — host bot protection on the API's own links);
  news 8/12 (TechCrunch returns 403 to curl — its own RSS links).
- All news/jobs dates checked within 24 h of the run.
- `pytest tests/test_graphone.py`: 8 passed.
- `architecture.pdf`: 2 pages (limit 3), all 4 required sections present (text-extract verified).

## Run 4 — stars refill + Groq sample (22 Nov 2026, ~11:00–11:10 UTC / 16:30–16:40 IST)

```bash
python3 - <<'PY'   # GitHub stars retry: api.github.com/repos/{owner}/{repo}, 1.5 s delay, backoff
                   # 86 missing repos attempted → 82 filled, 4× 404
python3 ~/workspace/custom-connectors/custom.groq/bin/groq.py chat --model qwen/qwen3.8-27b --prompt "..."
                   # grounded sample, 10 items (5 news full-text + 5 job titles)
python3 - <<'PY'   # rebuilt data/graphone_output.xlsx from the 6 final CSVs (same method as src/pipeline.py)
python -m pytest tests/ -q   # 16 passed
```

**GitHub stars:** 45 → **127/131**. The 4 still missing return GitHub 404
(deleted / renamed / private) and stay `null`, never guessed:
`IAntoineV/sbd`, `jzdang/RemoteVLPG`, `msalman95/DA`, `seungyubhan/mint`.
`data/final/research_papers.csv`, `data/graphone_output.xlsx` (Research
Papers tab) and `data/run_summary.json` (`github_stars_present: 127`) all
updated and re-verified (XLSX re-read: 127 stars, 131 URLs).

**Groq LLM tier — grounded sample, 10 items:** model
`qwen/qwen3.8-27b` via the stored `custom.groq` credential. **9/10 passed**
the verbatim grounding check (every extracted value appears in the source
text). The 1 failure: job "Werkstudent Marketing (m/w/d) · Remote" —
the model returned the title without "· Remote", so the grounding guard
would reject it. Two findings recorded:
- The helper's default model `llama-3.3-70b-versatile` returns HTTP 404
  from Groq (decommissioned); `list-models` shows only 11 models for this
  account, general chat models being `qwen/qwen3.8-27b` and
  `openai/gpt-oss-120b`. The sample ran on Qwen; the orchestrator's Groq
  model is configurable per `.env.example`.
- Gemini and DeepSeek tiers were NOT exercised (no keys). The full dataset
  was deliberately NOT re-run through the LLM; rows stay deterministic and
  source-structured. `data/run_summary.json` records
  `llm: groq_sample_9_of_10_grounded_qwen_qwen3.8-27b`.

## Known gaps (also in README.md)

- LLM tiers: Groq exercised on a 10-item grounded sample only (9/10 pass);
  Gemini/DeepSeek not exercised (no keys); full-run LLM extraction deliberately
  not done — deterministic source-structured rows kept to avoid hallucination.
- GitHub stars 127/131; the 4 missing are GitHub 404s. Products are HF models only;
  pricing uniformly FREE per rule.
- News (12) and jobs (117) are thin because only genuinely 24 h-fresh items were kept.
- Source typos preserved verbatim (e.g. Arbeitnow company "Coreweaveu").

## Note on parallel work in this repo

Another agent session built a parallel structure in the same repo on 22 Nov 2026
(`src/common/`, `src/llm/`, `src/resolver/`, `src/scrapers/arxiv_papers.py`,
`paperswithcode.py`, `startups_products.py`, `news_jobs.py`, `tests/test_pipeline.py`,
`.venv/`, `logs/`). Those files were **not modified or deleted** by this build;
this pipeline lives in `src/pipeline.py`, `src/scrapers/{papers,startups,products,news,jobs}.py`,
`src/{date_utils,entity_resolver,llm_orchestrator,anti_bot}.py`. If the two
implementations are merged, keep one pipeline entry point and one test suite.

## Run 5 — GitHub push attempt (22 Nov 2026, ~16:40–16:50 IST)

- Identity verified via GitHub connector: `Gautam825406` / Gautam Keshri.
- `create_repository` (public `graphone-intelligence-graph`) → **403
  `Resource not accessible by integration`**. The connector is a GitHub
  App installation: it can read/push repos it was granted, but cannot
  create repositories (GitHub App user tokens have no scopes to upgrade;
  repo creation is not in the App's permissions).
- Verified the repo does not exist: `get_file_contents` and
  `list_commits` both return 404 for
  `Gautam825406/graphone-intelligence-graph`.
- Push manifest prepared and validated per
  `/opt/hatch/connectors/github/references/snapshot-push.md`:
  `~/workspace/graphone-push-manifest.paths` — 42 text files, 1,129,785
  bytes UTF-8 (limit 16 MiB), all valid UTF-8, NUL-terminated; excludes
  the 2 binaries (`architecture.pdf`, `data/graphone_output.xlsx`) and
  all sync records. The push itself was NOT run — there is no repo to
  push to. Local commit `ce38326` is ready; the moment the repo exists,
  one `push-files` command uploads all 42 files.
- Binary-file check: `create_or_update_file` takes a text `content`
  string which the server base64-encodes — it cannot carry binary
  files. So `architecture.pdf` and `data/graphone_output.xlsx` must be
  uploaded via the GitHub web UI (see README Submission steps).
- No sync record was written (no confirmed push); nothing was advanced.

## Next steps for Gautam (NOT done by the agent)

1. **Create the empty public repo** `graphone-intelligence-graph` on
   github.com (no README / .gitignore / licence — must start empty),
   include it in the Muse GitHub App's repository access if asked,
   then tell Muse "repo created" so the prepared push can run.
2. Upload `architecture.pdf` + `data/graphone_output.xlsx` via GitHub
   web UI (Add file → Upload files).
3. Upload `data/graphone_output.xlsx` to Google Sheets (6 tabs) and make it public.
   Google Sheets is not connected to Muse — this needs Gautam's Google account.
4. Record a 5–10 min Loom video explaining the work (the submission form asks for it).
5. Submit the GitHub + Sheets + Loom links at https://forms.gle/3JJhukomumTFueRW7
   (form also asks Name / Mobile / College / course / year / City / hours spent / start date).
6. DONE in run 4 — no longer open: GitHub stars refill (127/131) and Groq sample (9/10 grounded).
   Decide on the duplicate implementations (canonical vs alternate, see README) before merging.

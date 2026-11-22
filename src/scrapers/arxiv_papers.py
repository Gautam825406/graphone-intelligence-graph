"""Phase I — Research Papers vertical: arXiv API (no key needed).

Endpoint: http://export.arxiv.org/api/query
- Paginates in batches of 100 (arXiv max_results per call).
- Respects arXiv's 3-second-between-calls guidance via RateLimiter.
- Parses Atom: title, authors, published, id (arxiv_url), pdf link.
- Never invents a paper, date or URL: every row comes from the Atom feed.
- GitHub enrichment is a separate step (paperswithcode.py); stars are
  null until a real GitHub repo + real star count are fetched.

Usage:
    python -m src.scrapers.arxiv_papers --categories cs.AI cs.LG cs.CL \
        --max-results 1000 --out data/research_papers.csv
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.dates import to_iso  # noqa: E402
from src.common.logging_config import get_logger  # noqa: E402
from src.common.rate_limiter import RateLimiter  # noqa: E402
from src.common.storage import NOW_ISO, write_csv  # noqa: E402

logger = get_logger("arxiv")
NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_API = "https://export.arxiv.org/api/query"
# arXiv asks for ≤1 request / 3s. 0.33 req/s with burst 1 enforces that.
LIMITER = RateLimiter(rate_per_second=1 / 3, burst=1)

PAPER_FIELDS = [
    "schemaVersion", "recordType", "source.name", "source.url",
    "content.title", "content.authors", "content.paper_url",
    "content.github_url", "content.github_stars", "content.published_date",
    "collectedAt",
]


async def fetch_batch(session: aiohttp.ClientSession, query: str,
                      start: int, max_results: int) -> list[dict]:
    await LIMITER.acquire()
    params = {"search_query": query, "start": start,
              "max_results": max_results, "sortBy": "submittedDate",
              "sortOrder": "descending"}
    async with session.get(ARXIV_API, params=params, timeout=60) as resp:
        if resp.status == 429:
            raise RuntimeError("arXiv 429 — back off")
        resp.raise_for_status()
        text = await resp.text()
    root = ET.fromstring(text)
    rows: list[dict] = []
    for entry in root.findall("atom:entry", NS):
        arxiv_id = (entry.findtext("atom:id", namespaces=NS) or "").strip()
        title = " ".join((entry.findtext("atom:title", namespaces=NS) or "").split())
        published_raw = (entry.findtext("atom:published", namespaces=NS) or "").strip()
        authors = [a.findtext("atom:name", namespaces=NS) or ""
                   for a in entry.findall("atom:author", NS)]
        authors = [a.strip() for a in authors if a.strip()]
        pdf_url = ""
        for link in entry.findall("atom:link", NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
        if not arxiv_id or not title or not published_raw:
            logger.warning("Skipping incomplete arXiv entry: %s", arxiv_id or "?")
            continue
        try:
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Bad published date %r for %s", published_raw, arxiv_id)
            continue
        rows.append({
            "schemaVersion": "1.0", "recordType": "RESEARCH_PAPER",
            "source.name": "arXiv", "source.url": arxiv_id,
            "content.title": title, "content.authors": "; ".join(authors),
            "content.paper_url": arxiv_id, "content.github_url": "",
            "content.github_stars": "", "content.published_date": to_iso(published),
            "collectedAt": NOW_ISO,
            "_pdf_url": pdf_url,
        })
    return rows


async def fetch_papers(categories: list[str], max_results: int) -> list[dict]:
    query = " OR ".join(f"cat:{c}" for c in categories)
    seen: set[str] = set()
    rows: list[dict] = []
    async with aiohttp.ClientSession(trust_env=True, 
            headers={"User-Agent": "GraphOne-Demo/0.1 (research; contact: demo)"}) as session:
        start = 0
        while len(rows) < max_results:
            batch_n = min(100, max_results - len(rows))
            batch = await fetch_batch(session, query, start, batch_n)
            if not batch:
                logger.warning("arXiv returned empty batch at start=%d — stopping", start)
                break
            new = [r for r in batch if r["content.paper_url"] not in seen]
            for r in new:
                seen.add(r["content.paper_url"])
            rows.extend(new)
            logger.info("arXiv: %d / %d (start=%d, batch=%d)", len(rows), max_results, start, len(batch))
            start += len(batch)
            if len(batch) < batch_n:
                break
    return rows[:max_results]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", nargs="+", default=["cs.AI", "cs.LG", "cs.CL"])
    ap.add_argument("--max-results", type=int, default=1000)
    ap.add_argument("--out", default="data/research_papers.csv")
    args = ap.parse_args()
    rows = asyncio.run(fetch_papers(args.categories, args.max_results))
    for r in rows:
        r.pop("_pdf_url", None)
    n = write_csv(args.out, rows, PAPER_FIELDS)
    logger.info("Wrote %d papers to %s", n, args.out)


if __name__ == "__main__":
    main()

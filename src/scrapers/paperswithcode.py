"""Phase I — Papers With Code correlation + GitHub star enrichment.

- Papers With Code public API: https://paperswithcode.com/api/v1/papers/
  (no key). Each paper lists `repository` links; we take the first
  github.com link as content.github_url. No repo -> github_url stays "".
- GitHub REST API: GET /repos/{owner}/{repo} -> stargazers_count.
  Unauthenticated: 60 req/hr (we stay well under it, 1 req/1.2s, and stop
  on 403 rate-limit). With GITHUB_TOKEN: 5,000 req/hr.
- Stars are recorded ONLY from a real GitHub response. No repo or a
  failed lookup -> github_stars stays null ("") — never 0, never guessed.

Usage:
    python -m src.scrapers.paperswithcode --in data/research_papers.csv \
        --limit 50 --out data/research_papers.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.logging_config import get_logger  # noqa: E402
from src.common.rate_limiter import RateLimiter  # noqa: E402

logger = get_logger("pwc")
PWC_API = "https://paperswithcode.com/api/v1/papers/"
GITHUB_API = "https://api.github.com/repos"
GH_RE = re.compile(r"github\.com/([^/]+)/([^/#?]+)")
LIMITER = RateLimiter(rate_per_second=0.8, burst=1)  # ~1 req / 1.2s


async def github_stars(session, owner: str, repo: str) -> int | None:
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "GraphOne-Demo/0.1"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    await LIMITER.acquire()
    url = f"{GITHUB_API}/{owner}/{repo}"
    async with session.get(url, headers=headers, timeout=30) as resp:
        if resp.status == 404:
            logger.warning("GitHub repo not found: %s/%s", owner, repo)
            return None
        if resp.status in (403, 429):
            logger.warning("GitHub rate-limited (%s) on %s/%s — stopping enrichment",
                           resp.status, owner, repo)
            raise RuntimeError("github-rate-limited")
        if resp.status != 200:
            logger.warning("GitHub %s on %s/%s", resp.status, owner, repo)
            return None
        data = await resp.json()
        return int(data.get("stargazers_count", 0))


async def pwc_github_for_arxiv(session, arxiv_id: str) -> str | None:
    """Best-effort: Papers With Code lookup by arXiv id. Returns github URL or None.

    Note: PWC's search by arXiv id is not a stable public endpoint, so this
    tries the papers list filtered by arxiv id and tolerates failure —
    a miss means 'no repo found', never an invented URL.
    """
    await LIMITER.acquire()
    try:
        async with session.get(PWC_API, params={"arxiv_id": arxiv_id}, timeout=30) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("PWC lookup failed for %s: %s", arxiv_id, exc)
        return None
    for paper in data.get("results", []):
        for repo in paper.get("repositories", []) or []:
            url = repo.get("url", "")
            if "github.com" in url:
                return url
        # also check top-level repository field
        url = paper.get("repository", "") or ""
        if "github.com" in url:
            return url
    return None


async def enrich(rows: list[dict], limit: int) -> tuple[int, int]:
    """Enrich the first `limit` rows that lack a github_url. Returns (with_repo, starred)."""
    enriched_with_repo = starred = 0
    async with aiohttp.ClientSession(trust_env=True, ) as session:
        for row in rows[:limit]:
            if row.get("content.github_url"):
                continue
            arxiv_id = row.get("content.paper_url", "").rstrip("/").split("/")[-1]
            try:
                gh = await pwc_github_for_arxiv(session, arxiv_id)
            except Exception:
                gh = None
            if not gh:
                continue
            row["content.github_url"] = gh
            enriched_with_repo += 1
            m = GH_RE.search(gh)
            if m:
                try:
                    stars = await github_stars(session, m.group(1), m.group(2).rstrip(".git"))
                except RuntimeError:
                    break
                if stars is not None:
                    row["content.github_stars"] = str(stars)
                    starred += 1
            logger.info("Enriched %s -> %s stars=%s", arxiv_id, gh, row.get("content.github_stars"))
    return enriched_with_repo, starred


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/research_papers.csv")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", default="data/research_papers.csv")
    args = ap.parse_args()
    rows = list(csv.DictReader(Path(args.in).open(encoding="utf-8")))
    with_repo, starred = asyncio.run(enrich(rows, args.limit))
    fields = list(rows[0].keys()) if rows else []
    with Path(args.out).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    logger.info("Enrichment: %d repos found, %d star counts fetched (limit %d)",
                with_repo, starred, args.limit)


if __name__ == "__main__":
    main()

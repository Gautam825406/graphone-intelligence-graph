"""arXiv research-paper scraper (Phase I).

Source: arXiv Atom API (export.arxiv.org/api/query) - official, no key,
no scraping of HTML. Categories cs.AI / cs.LG / cs.CL / cs.CV / cs.NE /
stat.ML, newest first. Papers With Code's public API now 302-redirects
to Hugging Face (verified 22 Nov 2026), so it is attempted and its
failure is logged, NOT silently replaced: github_url comes from the
arXiv <arxiv:comment> / abstract, which frequently carries the official
code link (e.g. "Code: https://github.com/..."). Stars are fetched from
the public GitHub API (unauthenticated, 60 req/hour) with a hard cap;
when the cap is hit the remaining stars stay null and are reported.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET

import aiohttp

from anti_bot import PoliteLimiter, new_session, random_headers
from date_utils import now_utc

log = logging.getLogger("graphone.papers")

ARXIV = "https://export.arxiv.org/api/query"
CATS = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:cs.NE OR cat:stat.ML"
GITHUB_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _clean_repo(path: str) -> str:
    return path.rstrip("/").rstrip(".").rstrip(")")


async def fetch_github_stars(session: aiohttp.ClientSession, repo: str,
                             budget: list) -> int | None:
    """budget[0] = remaining unauthenticated GitHub calls (60/hour)."""
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    url = f"https://api.github.com/repos/{repo}"
    try:
        async with session.get(url, headers={"Accept": "application/vnd.github+json",
                                             "User-Agent": "GraphOne-IntelligenceGraph/1.0"},
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 403:
                budget[0] = 0
                log.warning("GitHub API rate limit hit; remaining stars left null.")
                return None
            if r.status != 200:
                return None
            data = await r.json()
            return int(data.get("stargazers_count") or 0)
    except Exception as exc:
        log.debug("GitHub stars lookup failed for %s: %s", repo, exc)
        return None


async def fetch_papers(target: int = 1000, *, per_page: int = 200) -> list[dict]:
    limiter = PoliteLimiter(min_delay=3.0, max_concurrency=2)  # arXiv asks for 3s gaps
    papers: list[dict] = []
    seen: set[str] = set()
    collected = now_utc().isoformat().replace("+00:00", "Z")
    gh_budget = [55]  # stay under the unauthenticated 60/hr GitHub limit
    async with new_session() as session:
        start = 0
        while len(papers) < target:
            params = {
                "search_query": CATS,
                "start": start,
                "max_results": per_page,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            url = ARXIV + "?" + urllib.parse.urlencode(params)
            await limiter.wait("export.arxiv.org")
            try:
                async with session.get(url, headers=random_headers(),
                                       timeout=aiohttp.ClientTimeout(total=60)) as r:
                    if r.status != 200:
                        log.error("arXiv returned %s at start=%s", r.status, start)
                        break
                    xml = await r.text()
            except Exception as exc:
                log.error("arXiv fetch failed at start=%s: %s", start, exc)
                break
            root = ET.fromstring(xml)
            entries = root.findall("a:entry", NS)
            if not entries:
                break
            batch: list[dict] = []
            for e in entries:
                pid = (e.find("a:id", NS).text or "").strip()
                arxiv_id = pid.rsplit("/", 1)[-1].split("v")[0]
                if arxiv_id in seen:
                    continue
                seen.add(arxiv_id)
                title = " ".join((e.find("a:title", NS).text or "").split())
                authors = [a.find("a:name", NS).text for a in e.findall("a:author", NS)]
                summary = e.find("a:summary", NS).text or ""
                comment_el = e.find("arxiv:comment", NS)
                comment = comment_el.text if comment_el is not None else ""
                m = GITHUB_RE.search(comment + "\n" + summary)
                gh_url = None
                if m:
                    gh_url = "https://github.com/" + _clean_repo(m.group(1))
                published = (e.find("a:published", NS).text or "").replace("+00:00", "Z")
                batch.append({
                    "schemaVersion": "1.0",
                    "recordType": "RESEARCH_PAPER",
                    "source.name": "arXiv",
                    "source.url": f"https://arxiv.org/abs/{arxiv_id}",
                    "content.title": title,
                    "content.authors": json.dumps(authors, ensure_ascii=False),
                    "content.paper_url": f"https://arxiv.org/abs/{arxiv_id}",
                    "content.github_url": gh_url,
                    "content.github_stars": None,
                    "content.published_date": published,
                    "collectedAt": collected,
                    "_repo": gh_url.rsplit("github.com/", 1)[-1] if gh_url else None,
                })
            # Stars: only for repos actually found, within budget.
            for p in batch:
                if p["_repo"]:
                    p["content.github_stars"] = await fetch_github_stars(
                        session, p["_repo"], gh_budget)
                    await asyncio.sleep(0.2)
            papers.extend(batch)
            log.info("papers: %d collected (start=%d)", len(papers), start)
            start += per_page
            if len(entries) < per_page:
                break
    for p in papers:
        p.pop("_repo", None)
    return papers[:target]

"""AI jobs ingestion (Phase II) - 5 distinct job boards, 24h freshness.

Boards (verified live 22 Nov 2026):
  1. Arbeitnow API  https://www.arbeitnow.com/api/job-board-api (search=AI / machine learning)
  2. RemoteOK API   https://remoteok.com/api (all listings, AI/ML keyword filter)
  3. Hacker News jobs RSS  https://hnrss.org/jobs
  4. WeWorkRemotely RSS https://weworkremotely.com/categories/remote-programming-jobs.rss
     (the /remote-ai-jobs.rss URL 301-redirects to an empty feed -
     verified 22 Nov 2026 - so the working programming feed is used
     with the same AI keyword filter; 0 fresh rows is reported honestly)
  5. Jobicy feed  https://jobicy.com/feed/  (returns malformed XML -
     verified 22 Nov 2026 - so it contributes 0 rows; kept in the list
     so the failure is visible, not silently swapped)

Freshness rule is identical to news: keep ONLY items whose parsed
date is within the last 24h. A board with zero fresh items contributes
zero rows, honestly - nothing is backfilled. RemoteOK's `date` field
and Arbeitnow's epoch `created_at` are machine dates (confidence 1.0);
RSS pubDates are parsed the same way as news.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import aiohttp
import feedparser

from anti_bot import new_session, random_headers
from date_utils import is_fresh, now_utc, parse_date

log = logging.getLogger("graphone.jobs")

AI_KEYWORDS = re.compile(
    r"\b(ai|artificial intelligence|machine learning|ml engineer|deep learning|"
    r"llm|genai|generative ai|nlp|computer vision|data scientist|"
    r"prompt|rag|transformer)\b",
    re.IGNORECASE,
)

ROLE_MAP = [
    (re.compile(r"data scientist|data analyst|analytics", re.I), "Data"),
    (re.compile(r"sales|business dev|account exec", re.I), "Sales"),
    (re.compile(r"product|design|ux|ui", re.I), "Product/Design"),
    (re.compile(r"research", re.I), "Research"),
    (re.compile(r"devops|infra|platform|sre", re.I), "Infrastructure"),
]


def role_family(title: str) -> str:
    for rx, fam in ROLE_MAP:
        if rx.search(title or ""):
            return fam
    return "Engineering"


async def _arbeitnow(session, search: str, now, collected) -> list[dict]:
    out = []
    url = f"https://www.arbeitnow.com/api/job-board-api?search={search}"
    try:
        async with session.get(url, headers=random_headers(),
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                log.warning("Arbeitnow %s -> HTTP %s", search, r.status)
                return out
            data = await r.json()
    except Exception as exc:
        log.warning("Arbeitnow %s failed: %s", search, exc)
        return out
    for j in data.get("data", []):
        iso, conf, method = parse_date(j.get("created_at"), now=now)
        if not is_fresh(iso, now=now, hours=24):
            continue
        title = j.get("title", "")
        # The Arbeitnow API ignores the `search` query param (verified
        # 22 Nov 2026: search=AI and search="machine learning" returned
        # the identical latest-jobs list), so filter client-side on
        # title + description text. Unmatched rows are dropped, not kept.
        desc = re.sub(r"<[^>]+>", " ", j.get("description") or "")
        if not AI_KEYWORDS.search(title + " " + desc):
            continue
        out.append({
            "schemaVersion": "1.0", "recordType": "JOB",
            "source.name": "Arbeitnow", "source.url": j.get("url", ""),
            "content.company": (j.get("company_name") or "").strip(),
            "content.title": title,
            "content.date": iso,
            "content.is_remote": bool(j.get("remote")),
            "content.role_family": role_family(title),
            "content.location": (j.get("location") or ""),
            "content.date_confidence": conf, "content.date_method": method,
            "collectedAt": collected,
        })
    return out


async def _remoteok(session, now, collected) -> list[dict]:
    out = []
    try:
        async with session.get("https://remoteok.com/api",
                               headers=random_headers(),
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                log.warning("RemoteOK -> HTTP %s", r.status)
                return out
            data = await r.json()
    except Exception as exc:
        log.warning("RemoteOK failed: %s", exc)
        return out
    for j in data[1:]:  # [0] is the legal notice
        title = j.get("position", "")
        tags = j.get("tags") or []
        tag_text = " ".join(tags) if isinstance(tags, list) else str(tags)
        if not AI_KEYWORDS.search(title + " " + tag_text):
            continue
        iso, conf, method = parse_date(j.get("date"), now=now)
        if not is_fresh(iso, now=now, hours=24):
            continue
        out.append({
            "schemaVersion": "1.0", "recordType": "JOB",
            "source.name": "RemoteOK", "source.url": j.get("url", ""),
            "content.company": (j.get("company") or "").strip(),
            "content.title": title,
            "content.date": iso,
            "content.is_remote": True,
            "content.role_family": role_family(title),
            "content.location": (j.get("location") or ""),
            "content.date_confidence": conf, "content.date_method": method,
            "collectedAt": collected,
        })
    return out


def _rss_jobs(source: str, url: str, now, collected, *, ai_only: bool) -> list[dict]:
    out = []
    feed = feedparser.parse(url, agent="GraphOne-IntelligenceGraph/1.0")
    for e in feed.entries:
        title = (e.get("title") or "").strip()
        if ai_only and not AI_KEYWORDS.search(title + " " + (e.get("summary") or "")):
            continue
        iso, conf, method = parse_date(e.get("published") or e.get("updated") or "", now=now)
        if not is_fresh(iso, now=now, hours=24):
            continue
        company = ""
        m = re.search(r"^(.*?)\s+is hiring\b", title, re.IGNORECASE)
        if m:
            company = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
        elif ":" in title:
            company = title.split(":", 1)[0].strip()
        out.append({
            "schemaVersion": "1.0", "recordType": "JOB",
            "source.name": source, "source.url": e.get("link", ""),
            "content.company": company,
            "content.title": title,
            "content.date": iso,
            "content.is_remote": ("remote" in title.lower() or "remote" in source.lower()),
            "content.role_family": role_family(title),
            "content.location": "",
            "content.date_confidence": conf, "content.date_method": method,
            "collectedAt": collected,
        })
    log.info("jobs %s: %d fresh (of %d entries)", source, len(out), len(feed.entries))
    return out


async def fetch_jobs() -> tuple[list[dict], dict]:
    now = now_utc()
    collected = now.isoformat().replace("+00:00", "Z")
    per_source: dict[str, int] = {}
    all_jobs: list[dict] = []
    async with new_session() as session:
        for search in ("AI",):  # API ignores the query; one call is enough
            rows = await _arbeitnow(session, search, now, collected)
            per_source[f"Arbeitnow ({search})"] = len(rows)
            all_jobs.extend(rows)
        rows = await _remoteok(session, now, collected)
        per_source["RemoteOK"] = len(rows)
        all_jobs.extend(rows)
    for source, url, ai_only in [
        ("Hacker News Jobs", "https://hnrss.org/jobs", True),
        ("WeWorkRemotely", "https://weworkremotely.com/categories/remote-programming-jobs.rss", True),
        ("Jobicy", "https://jobicy.com/feed/", True),
    ]:
        rows = _rss_jobs(source, url, now, collected, ai_only=ai_only)
        per_source[source] = len(rows)
        all_jobs.extend(rows)
    # Dedupe by (company,title,url) - HN titles embed the company.
    seen: set[str] = set()
    deduped: list[dict] = []
    for j in all_jobs:
        key = (j["content.company"].lower(), j["content.title"].lower(), j["source.url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(j)
    return deduped, per_source

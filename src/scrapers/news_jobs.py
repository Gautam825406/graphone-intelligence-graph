"""Phase II — Freshness: 5 AI news sources + 5 AI job boards via RSS/Atom.

Freshness rule (hard): an item is included ONLY if its real published
date parses and is within the last 24h. Missing/undated items are
excluded, never backfilled with old items. Seen-URL dedup in SQLite
(data/seen.db) so a rerun never emits the same article/job twice.

Sources (real RSS feeds, verified 22 Nov 2026 — if a feed moves, the
fetcher logs the failure and continues with the rest):
  NEWS: TechCrunch AI, VentureBeat AI, MIT Technology Review (AI),
        The Verge AI, Wired AI
  JOBS: YC Work at a Startup (AI), Wellfound, LinkedIn is NOT RSS-able
        (documented limitation) — so we use: Hacker News 'Who is hiring'
        is not dated per-item either, so the job list uses RSS-capable
        boards: RemoteOK AI, We Work Remotely (AI/ML), Dice AI,
        Indeed is blocked (documented), AngelList/Wellfound RSS,
        YC jobs RSS. Each feed is attempted; failures are logged.

Run:
    python -m src.scrapers.news_jobs --out-dir data
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.dates import is_fresh, parse_date, to_iso  # noqa: E402
from src.common.logging_config import get_logger  # noqa: E402
from src.common.storage import NOW_ISO, init_seen_db, is_seen, mark_seen, write_csv  # noqa: E402

logger = get_logger("news_jobs")

NEWS_SOURCES = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"),
    ("Wired AI", "https://www.wired.com/feed/category/artificial-intelligence/latest/rss"),
]

JOB_SOURCES = [
    ("RemoteOK AI", "https://remoteok.com/rss?search=ai"),
    ("We Work Remotely AI", "https://weworkremotely.com/categories/remote-ai-jobs.rss"),
    ("YC Jobs AI", "https://www.ycombinator.com/jobs.rss"),
    ("Wellfound AI", "https://wellfound.com/jobs.rss"),
    ("Dice AI", "https://www.dice.com/jobs/rss/q-AI"),
]

NEWS_FIELDS = ["schemaVersion", "recordType", "source.name", "source.url",
               "content.title", "content.published_date", "collectedAt"]
JOB_FIELDS = ["schemaVersion", "recordType", "source.name", "source.url",
              "content.title", "content.company", "content.date",
              "content.is_remote", "content.role_family", "content.location", "collectedAt"]


def _entry_date(entry) -> datetime | None:
    # feedparser gives parsed structs; prefer them, fall back to raw strings.
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    for key in ("published", "updated"):
        dt = parse_date(entry.get(key))
        if dt:
            return dt
    return None


async def fetch_feed(session: aiohttp.ClientSession, name: str, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=30,
                               headers={"User-Agent": "GraphOne-Demo/0.1"}) as resp:
            if resp.status != 200:
                logger.warning("%s feed HTTP %s (%s)", name, resp.status, url)
                return []
            text = await resp.text()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s feed failed (%s): %s", name, url, exc)
        return []
    feed = feedparser.parse(text)
    if feed.bozo and not feed.entries:
        logger.warning("%s feed unparseable (%s): %s", name, url, feed.bozo_exception)
    return [{"source_name": name, "source_url": url, "entry": e} for e in feed.entries]


async def run(out_dir: str, hours: int = 24) -> tuple[list[dict], list[dict]]:
    conn = init_seen_db(Path(out_dir) / "seen.db")
    now = datetime.now(timezone.utc)
    news_rows: list[dict] = []
    job_rows: list[dict] = []
    async with aiohttp.ClientSession(trust_env=True, ) as session:
        for label, sources, is_job in (("news", NEWS_SOURCES, False), ("jobs", JOB_SOURCES, True)):
            tasks = [fetch_feed(session, n, u) for n, u in sources]
            for entries in await asyncio.gather(*tasks):
                for item in entries:
                    e, src = item["entry"], item["source_name"]
                    link = (e.get("link") or "").strip()
                    title = (e.get("title") or "").strip()
                    if not link or not title:
                        continue
                    if is_seen(conn, link):
                        continue
                    dt = _entry_date(e)
                    if not is_fresh(dt, hours=hours, now=now):
                        continue  # not provably fresh — excluded, never backfilled
                    mark_seen(conn, link, src, to_iso(dt))
                    base = {"schemaVersion": "1.0", "source.name": src,
                            "source.url": link, "collectedAt": NOW_ISO}
                    if is_job:
                        job_rows.append({**base, "recordType": "JOB",
                            "content.title": title,
                            "content.company": (e.get("author") or "").strip(),
                            "content.date": to_iso(dt),
                            "content.is_remote": "remote" in title.lower() or "remote" in (e.get("summary") or "").lower(),
                            "content.role_family": "AI/ML",
                            "content.location": ""})
                    else:
                        news_rows.append({**base, "recordType": "NEWS",
                            "content.title": title,
                            "content.published_date": to_iso(dt)})
    conn.close()
    return news_rows, job_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()
    news, jobs = asyncio.run(run(args.out_dir, args.hours))
    n = write_csv(Path(args.out_dir) / "news_24h.csv", news, NEWS_FIELDS)
    j = write_csv(Path(args.out_dir) / "jobs_24h.csv", jobs, JOB_FIELDS)
    logger.info("Fresh news: %d | Fresh jobs: %d (last %dh)", n, j, args.hours)
    if not news:
        logger.info("news_24h.csv is header-only: no feed yielded a dated item in the window")
    if not jobs:
        logger.info("jobs_24h.csv is header-only: no feed yielded a dated item in the window")


if __name__ == "__main__":
    main()

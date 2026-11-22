"""AI news ingestion (Phase II) - 5 distinct RSS sources, 24h freshness.

Sources (all verified live 22 Nov 2026; VentureBeat returned 429 and
AI News returned 403 from this environment, so neither is used):
  1. TechCrunch AI      https://techcrunch.com/category/artificial-intelligence/feed/
  2. The Verge AI       https://www.theverge.com/rss/ai-artificial-intelligence/index.xml
  3. MIT Tech Review AI https://www.technologyreview.com/topic/artificial-intelligence/feed/
  4. The Decoder        https://the-decoder.com/feed/
  5. ZDNet AI           https://www.zdnet.com/topic/artificial-intelligence/rss.xml

Freshness rule: an item is kept ONLY if its parsed publication date is
within the last 24h of the run. Items with no parseable date are
dropped, never labelled fresh. Full text = feed content:encoded /
summary, plus a best-effort fetch of the article page (HTML stripped
with the stdlib parser, 8s timeout, 5 concurrent) - if the fetch
fails the feed text is kept and the shortfall is logged.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from html.parser import HTMLParser

import aiohttp
import feedparser

from anti_bot import PoliteLimiter, new_session, random_headers
from date_utils import is_fresh, now_utc, parse_date

log = logging.getLogger("graphone.news")

SOURCES = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "MIT Technology Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "The Decoder": "https://the-decoder.com/feed/",
    "ZDNet AI": "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
}
TAG_RE = re.compile(r"<[^>]+>")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer", "aside"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer", "aside") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def strip_html(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html or "")
    except Exception:
        return TAG_RE.sub(" ", html or "")
    return " ".join(p.parts)


async def _fetch_fulltext(session, url: str, limiter: PoliteLimiter) -> str | None:
    try:
        host = url.split("/")[2]
        await limiter.wait(host)
        async with session.get(url, headers=random_headers(),
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            html = await r.text()
        text = strip_html(html)
        return text if len(text) > 500 else None
    except Exception:
        return None


def fetch_news() -> tuple[list[dict], dict]:
    """Synchronous RSS parse (feedparser) + async full-text enrichment."""
    now = now_utc()
    collected = now.isoformat().replace("+00:00", "Z")
    items: list[dict] = []
    per_source: dict[str, int] = {}
    for source, url in SOURCES.items():
        feed = feedparser.parse(url, agent="GraphOne-IntelligenceGraph/1.0")
        kept = 0
        for e in feed.entries:
            raw_date = e.get("published") or e.get("updated") or ""
            iso, conf, method = parse_date(raw_date, now=now)
            if not is_fresh(iso, now=now, hours=24):
                continue
            content = ""
            if e.get("content"):
                content = e["content"][0].get("value", "")
            content = strip_html(content) or strip_html(e.get("summary", ""))
            items.append({
                "schemaVersion": "1.0",
                "recordType": "NEWS",
                "source.name": source,
                "source.url": e.get("link", ""),
                "content.title": (e.get("title") or "").strip(),
                "content.text": content,
                "content.published_date": iso,
                "content.date_confidence": conf,
                "content.date_method": method,
                "collectedAt": collected,
            })
            kept += 1
        per_source[source] = kept
        log.info("news %s: %d fresh in 24h (of %d entries)", source, kept, len(feed.entries))
    return items, per_source


async def enrich_fulltext(items: list[dict], limit: int = 5) -> None:
    """Best-effort article fetch for a small sample per source."""
    limiter = PoliteLimiter(min_delay=0.5, max_concurrency=5)
    async with new_session() as session:
        by_source: dict[str, int] = {}
        tasks = []
        for it in items:
            n = by_source.get(it["source.name"], 0)
            if n >= limit:
                continue
            by_source[it["source.name"]] = n + 1
            tasks.append((it, _fetch_fulltext(session, it["source.url"], limiter)))
        for it, coro in tasks:
            text = await coro
            if text:
                it["content.text"] = text[:8000]
                it["content.fulltext_fetched"] = True
            else:
                it["content.fulltext_fetched"] = False
    for it in items:
        it.setdefault("content.fulltext_fetched", False)

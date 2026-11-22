"""Startup scraper (Phase I).

Primary source: yc-oss API (https://yc-oss.github.io/api/), a public
static mirror of the Y Combinator directory, refreshed daily from YC's
own Algolia index. `tags/ai.json` = 1,906 AI-tagged YC companies
(verified 22 Nov 2026). Each record carries a real YC profile URL
(https://www.ycombinator.com/companies/<slug>) as source.url and the
company website as a bonus field. team_size -> employeeCount (null
when YC does not publish it). Direct Algolia access to YC was tested
and rejected: the public search key embedded in ycombinator.com pages
returned no hits from this environment, so the documented mirror is
used instead of faking a direct scrape. Crunchbase was not used: it
blocks unauthenticated access and has no free API.
"""
from __future__ import annotations

import json
import logging

import aiohttp

from anti_bot import new_session, random_headers
from date_utils import now_utc

log = logging.getLogger("graphone.startups")

YC_AI = "https://yc-oss.github.io/api/tags/ai.json"
YC_ALL = "https://yc-oss.github.io/api/companies/all.json"


async def fetch_startups(target: int = 1100) -> list[dict]:
    collected = now_utc().isoformat().replace("+00:00", "Z")
    async with new_session() as session:
        async with session.get(YC_AI, headers=random_headers(),
                               timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                raise RuntimeError(f"yc-oss ai.json returned HTTP {r.status}")
            data = await r.json()
    log.info("yc-oss ai.json: %d companies", len(data))
    out: list[dict] = []
    seen: set[str] = set()
    for c in data:
        name = (c.get("name") or "").strip()
        slug = (c.get("slug") or "").strip()
        if not name or not slug:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        team = c.get("team_size")
        out.append({
            "schemaVersion": "1.0",
            "recordType": "STARTUP",
            "source.name": "Y Combinator (via yc-oss mirror)",
            "source.url": f"https://www.ycombinator.com/companies/{slug}",
            "content.entityName": name,
            "content.data.employeeCount": int(team) if isinstance(team, int) else None,
            "content.website": c.get("website") or "",
            "content.batch": c.get("batch") or "",
            "content.industry": c.get("industry") or "",
            "content.one_liner": (c.get("one_liner") or "").strip(),
            "collectedAt": collected,
        })
        if len(out) >= target:
            break
    return out

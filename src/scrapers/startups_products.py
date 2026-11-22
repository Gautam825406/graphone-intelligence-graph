"""Phase I — Startups & Products: real-source skeleton + verified pull.

HONEST STATUS (22 Nov 2026): the full 1,000 + 1,000 is NOT yet produced.
Crunchbase / Product Hunt / YC directory are Cloudflare-protected or
require an API key, so bulk scraping them needs the Phase V Playwright
path + keys (see README, 'What is NOT done'). This module implements
two keyless, verifiable sources and reports exactly what worked:

1. Y Combinator public Algolia index
   (https://45bwlj1sgx-dsn.algolia.net/1/indexes/*/queries — the same
   endpoint the public ycombinator.com/companies directory calls).
   If it returns 200, rows are real YC companies with a real YC URL.
   If it is blocked, the run logs the failure and writes 0 rows.

2. GitHub topic search (unauthenticated REST, 60 req/hr):
   GET https://api.github.com/search/repositories?q=topic:artificial-intelligence
   Each repo is a real product (repo = product) with a real github.com URL
   and star count. This is the fallback that demonstrably works keyless.

Never invent a startup/product. A failed source -> fewer rows, not fake rows.

Run:
    python -m src.scrapers.startups_products --out-dir data --github-limit 100
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.logging_config import get_logger  # noqa: E402
from src.common.storage import NOW_ISO, write_csv  # noqa: E402

logger = get_logger("startups_products")

STARTUP_FIELDS = ["schemaVersion", "recordType", "source.name", "source.url",
                  "content.entityName", "content.data.employeeCount", "collectedAt"]
PRODUCT_FIELDS = ["schemaVersion", "recordType", "source.name", "source.url",
                  "content.startupName", "content.entityName", "content.productUrl",
                  "content.pricingModel", "collectedAt"]

# YC Algolia — the public key is fetched from the YC site itself; see fetch_yc().
# We do NOT hard-code any credential: fetch_yc() first GETs
# https://www.ycombinator.com/companies and extracts the Algolia key from
# the page, exactly as a browser would. If that fails, YC is skipped.
YC_ALGOLIA_URL = "https://45bwlj1sgx-dsn.algolia.net/1/indexes/*/queries"


async def fetch_yc(session: aiohttp.ClientSession, limit: int = 200) -> list[dict]:
    """Attempt YC directory via its public Algolia endpoint. Returns [] on any failure."""
    import re
    try:
        async with session.get("https://www.ycombinator.com/companies",
                               timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status != 200:
                logger.warning("YC companies page HTTP %s — YC source skipped", resp.status)
                return []
            html = await resp.text()
    except Exception as exc:  # noqa: BLE001
        logger.warning("YC companies page failed: %s — YC source skipped", exc)
        return []
    m = re.search(r'"algoliaApiKey"\s*:\s*"([^"]+)"', html) or \
        re.search(r"algolia[^\"']*key[^\"']*\"([A-Za-z0-9]{32})\"", html, re.I)
    if not m:
        logger.warning("YC Algolia key not found in page — YC source skipped (Cloudflare/JS-rendered)")
        return []
    key = m.group(1)
    payload = {"requests": [{"indexName": "YCCompany_production",
                             "params": f"query=&hitsPerPage={limit}&page=0"}]}
    headers = {"X-Algolia-API-Key": key,
               "X-Algolia-Application-Id": "45BWLJ1SGX",
               "Content-Type": "application/json"}
    try:
        async with session.post(YC_ALGOLIA_URL, json=payload, headers=headers, timeout=30) as resp:
            if resp.status != 200:
                logger.warning("YC Algolia HTTP %s — YC source skipped", resp.status)
                return []
            data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("YC Algolia query failed: %s", exc)
        return []
    rows = []
    for hit in data.get("results", [{}])[0].get("hits", []):
        name = hit.get("name", "").strip()
        slug = hit.get("slug", "")
        if not name or not slug:
            continue
        rows.append({"schemaVersion": "1.0", "recordType": "STARTUP",
                     "source.name": "Y Combinator", "source.url": f"https://www.ycombinator.com/companies/{slug}",
                     "content.entityName": name,
                     "content.data.employeeCount": hit.get("team_size") or "",
                     "collectedAt": NOW_ISO})
    logger.info("YC: %d real startups", len(rows))
    return rows


async def fetch_github_ai_products(session: aiohttp.ClientSession, limit: int = 100) -> list[dict]:
    """GitHub topic:artificial-intelligence repos as products (keyless, rate-limit aware)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "GraphOne-Demo/0.1"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    rows: list[dict] = []
    per_page = 30
    page = 1
    while len(rows) < limit:
        url = ("https://api.github.com/search/repositories"
               f"?q=topic:artificial-intelligence&sort=stars&order=desc"
               f"&per_page={per_page}&page={page}")
        try:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status in (403, 429):
                    logger.warning("GitHub search rate-limited (%s) — stopping at %d products", resp.status, len(rows))
                    break
                if resp.status != 200:
                    logger.warning("GitHub search HTTP %s — stopping", resp.status)
                    break
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("GitHub search failed: %s", exc)
            break
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            rows.append({"schemaVersion": "1.0", "recordType": "PRODUCT",
                         "source.name": "GitHub", "source.url": it["html_url"],
                         "content.startupName": (it.get("owner", {}) or {}).get("login", ""),
                         "content.entityName": it.get("name", ""),
                         "content.productUrl": it["html_url"],
                         "content.pricingModel": "FREE",
                         "collectedAt": NOW_ISO})
            if len(rows) >= limit:
                break
        page += 1
        await asyncio.sleep(1)
    logger.info("GitHub topics: %d real products", len(rows))
    return rows


async def run(out_dir: str, github_limit: int = 100, yc_limit: int = 200):
    async with aiohttp.ClientSession(trust_env=True, ) as session:
        startups = await fetch_yc(session, yc_limit)
        products = await fetch_github_ai_products(session, github_limit)
    return startups, products


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--github-limit", type=int, default=100)
    ap.add_argument("--yc-limit", type=int, default=200)
    args = ap.parse_args()
    startups, products = asyncio.run(run(args.out_dir, args.github_limit, args.yc_limit))
    write_csv(Path(args.out_dir) / "startups_sample.csv", startups, STARTUP_FIELDS)
    write_csv(Path(args.out_dir) / "products_sample.csv", products, PRODUCT_FIELDS)
    logger.info("Startups sample: %d | Products sample: %d", len(startups), len(products))


if __name__ == "__main__":
    main()

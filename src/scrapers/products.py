"""Product scraper (Phase I).

Source: Hugging Face Hub API (https://huggingface.co/api/models) -
public, no key, paginated via the RFC 5988 Link header. Every model
is a real AI product with a resolvable URL
https://huggingface.co/<model-id>; the author (org/user) is the
producing startup/org, canonicalised downstream by the resolver.

Pricing rule (disclosed, uniform, no per-product invention):
  pricingModel = FREE for every row, because every listed model is
  freely downloadable from the Hub under its published licence.
  Hosted inference / enterprise tiers are NOT assessed and are not
  claimed. If a future run adds vendor pricing pages, the rule in
  README.md requires pricing text as evidence before any other value
  is used.
"""
from __future__ import annotations

import json
import logging
import re

import aiohttp

from anti_bot import new_session, random_headers
from date_utils import now_utc

log = logging.getLogger("graphone.products")

HF_MODELS = "https://huggingface.co/api/models"
NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


async def fetch_products(target: int = 1100) -> list[dict]:
    collected = now_utc().isoformat().replace("+00:00", "Z")
    out: list[dict] = []
    seen: set[str] = set()
    url = HF_MODELS + "?limit=100&full=true&sort=downloads&direction=-1"
    async with new_session() as session:
        pages = 0
        while url and len(out) < target and pages < 20:
            pages += 1
            async with session.get(url, headers=random_headers(),
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    log.error("HF models API returned %s", r.status)
                    break
                data = await r.json()
                link = r.headers.get("Link", "")
            m = NEXT_RE.search(link)
            url = m.group(1) if m else None
            for mod in data:
                mid = mod.get("id", "")
                if not mid or mid.lower() in seen:
                    continue
                seen.add(mid.lower())
                out.append({
                    "schemaVersion": "1.0",
                    "recordType": "PRODUCT",
                    "source.name": "Hugging Face Hub",
                    "source.url": f"https://huggingface.co/{mid}",
                    "content.entityName": mid,
                    "content.startupName": (mod.get("author") or "").strip(),
                    "content.pricingModel": "FREE",
                    "content.pipeline_tag": mod.get("pipeline_tag") or "",
                    "content.downloads": int(mod.get("downloads") or 0),
                    "content.likes": int(mod.get("likes") or 0),
                    "collectedAt": collected,
                })
            log.info("products: %d collected (page %d)", len(out), pages)
    return out[:target]

"""Pipeline orchestrator (Phases I-V).

Runs, in order: papers -> startups -> products -> news -> jobs,
canonicalises every entity name with EntityResolver, writes
data/final/*.csv + data/graphone_output.xlsx (6 tabs), and prints a
run summary. LLM extraction is attempted per record only when a tier
is configured; in this trial run no LLM key is configured, so the
orchestrator logs `llm_skipped` and keeps the source-structured rows
- it never asks an LLM to invent a record.

Usage:
    python -m src.pipeline            # from the repo root
    python -m src.pipeline --papers 1200 --startups 1100 --products 1100
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("graphone.pipeline")

import asyncio  # noqa: E402

from entity_resolver import EntityResolver  # noqa: E402
from llm_orchestrator import LLMOrchestrator  # noqa: E402
from scrapers.jobs import fetch_jobs  # noqa: E402
from scrapers.news import enrich_fulltext, fetch_news  # noqa: E402
from scrapers.papers import fetch_papers  # noqa: E402
from scrapers.products import fetch_products  # noqa: E402
from scrapers.startups import fetch_startups  # noqa: E402


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


async def main() -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", type=int, default=1000)
    ap.add_argument("--startups", type=int, default=1100)
    ap.add_argument("--products", type=int, default=1100)
    args = ap.parse_args()

    resolver = EntityResolver(ROOT / "data" / "seed_canonical.json")
    llm = LLMOrchestrator()
    final = ROOT / "data" / "final"
    final.mkdir(parents=True, exist_ok=True)

    papers = await fetch_papers(target=args.papers)
    log.info("PAPERS done: %d", len(papers))
    startups = await fetch_startups(target=args.startups)
    log.info("STARTUPS done: %d", len(startups))
    products = await fetch_products(target=args.products)
    log.info("PRODUCTS done: %d", len(products))
    news, news_per_source = fetch_news()
    log.info("NEWS done: %d", len(news))
    await enrich_fulltext(news, limit=5)
    jobs, jobs_per_source = await fetch_jobs()
    log.info("JOBS done: %d", len(jobs))

    # Canonicalise names; resolver appends to its mapping log.
    for s in startups:
        s["content.entityName"], _, _ = resolver.resolve(
            s["content.entityName"], source="Y Combinator")
    for p in products:
        p["content.startupName"], _, _ = resolver.resolve(
            p["content.startupName"], source="Hugging Face Hub")
        # Product display name stays the model id; canonical startup is resolved.
    for j in jobs:
        if j["content.company"]:
            j["content.company"], _, _ = resolver.resolve(
                j["content.company"], source=j["source.name"])

    # LLM tier status (no keys in this run -> skipped, honestly logged).
    llm_status = llm.extract(
        "Sample check only - no record text is sent to an LLM in this run.",
        '{"entityName": "string|null"}',
    ) if llm.configured else None
    log.info("LLM status: %s",
             llm_status.status if llm_status else
             "llm_skipped (no GEMINI/GROQ/DEEPSEEK_API_KEY configured)")

    write_csv(final / "research_papers.csv", papers, [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.title", "content.authors", "content.paper_url",
        "content.github_url", "content.github_stars",
        "content.published_date", "collectedAt"])
    write_csv(final / "startups.csv", startups, [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.entityName", "content.data.employeeCount",
        "content.website", "content.batch", "content.industry",
        "content.one_liner", "collectedAt"])
    write_csv(final / "products.csv", products, [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.entityName", "content.startupName",
        "content.pricingModel", "content.pipeline_tag",
        "content.downloads", "content.likes", "collectedAt"])
    write_csv(final / "news.csv", news, [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.title", "content.text", "content.published_date",
        "content.date_confidence", "content.date_method",
        "content.fulltext_fetched", "collectedAt"])
    write_csv(final / "jobs.csv", jobs, [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.company", "content.title", "content.date",
        "content.is_remote", "content.role_family", "content.location",
        "content.date_confidence", "content.date_method", "collectedAt"])
    write_csv(final / "entity_mapping_log.csv", resolver.log, [
        "raw_name", "canonical_name", "method", "confidence", "source"])

    # XLSX with exactly the 6 required tabs.
    import pandas as pd

    tabs = {
        "Startups": final / "startups.csv",
        "Products": final / "products.csv",
        "Research Papers": final / "research_papers.csv",
        "Jobs": final / "jobs.csv",
        "News": final / "news.csv",
        "Entity Mapping Log": final / "entity_mapping_log.csv",
    }
    xlsx = ROOT / "data" / "graphone_output.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for tab, csv_path in tabs.items():
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
            df.to_excel(writer, sheet_name=tab, index=False)
    counts = {
        "startups": len(startups),
        "products": len(products),
        "research_papers": len(papers),
        "jobs": len(jobs),
        "news": len(news),
        "entity_mapping_log": len(resolver.log),
        "github_stars_present": sum(1 for p in papers if p["content.github_stars"] is not None),
        "github_urls_present": sum(1 for p in papers if p["content.github_url"]),
        "news_per_source": news_per_source,
        "jobs_per_source": jobs_per_source,
        "llm": llm_status.status if llm_status else "llm_skipped",
    }
    (ROOT / "data" / "run_summary.json").write_text(
        json.dumps(counts, indent=2, ensure_ascii=False))
    log.info("COUNTS: %s", json.dumps(counts, indent=2))
    log.info("Wrote %s and %s", xlsx, final)
    return counts


if __name__ == "__main__":
    asyncio.run(main())

"""Generate architecture.pdf (max 3 pages) for the GraphOne demo task.

Run:  python3 tools/make_architecture_pdf.py   (from repo root)
Uses reportlab Platypus; the script asserts the output is <= 3 pages.
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer)

OUT = Path(__file__).resolve().parent.parent / "architecture.pdf"

H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12, spaceBefore=8, spaceAfter=3)
B = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=4)
SMALL = ParagraphStyle("s", fontName="Helvetica", fontSize=8.5, leading=11, spaceAfter=3)
TITLE = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=17, leading=20)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10, leading=13, textColor="#444444")

SECTIONS = [
    ("GraphOne / FrontierAtlas — Intelligence Graph: Technical Architecture",
     TITLE, None),
    ("AI Engineer demo task  •  Gautam Keshri  •  22 Nov 2026  •  Pipeline run verified live: "
     "1,100 startups, 1,100 products, 1,000 papers, 117 jobs, 12 news (24 h fresh)",
     SUB, None),
    ("1. Scale strategy — 500,000 records without manual intervention", H,
     "Ingestion is a set of small, stateless, idempotent workers, not one big scraper. "
     "A seed queue (arXiv categories, YC directory pages, HF Hub cursors, RSS feeds) is "
     "partitioned by source and by key range (e.g. arXiv start offsets, HF Link-header "
     "cursors) into a durable queue (SQS / Kafka). N workers pull one partition each, "
     "fetch with asyncio + aiohttp (concurrency capped per host), and write raw payloads "
     "to object storage (S3) keyed by content hash before any parsing — so a parse bug "
     "never requires a re-crawl. A second stage parses + canonicalises from S3 in "
     "parallel and upserts into the store. Scaling 1k → 500k records changes only three "
     "numbers: worker count, per-host concurrency, and queue partitions. No code change. "
     "Backpressure: if a host returns 429s the partition is parked with a delay and "
     "retried; a dead-letter queue holds records that fail 5 times, with the raw payload "
     "preserved for inspection. Cost control: APIs/RSS first (free, structured), HTML "
     "scraping only for sources with no feed, Playwright only for JS-rendered pages."),
    ("2. Handling 413s and 429s — context windows and rate limits", H,
     "413 (payload too large). Every LLM call goes through a char budget per tier "
     "(Gemini Flash 120k, DeepSeek 60k, Groq Llama-3 24k chars — conservative vs the "
     "model context, because the budget is for the extraction payload). chunk_input() "
     "keeps the head (title/lede, 40% of budget) verbatim and fills the rest with the "
     "highest keyword-density sentences in original order; oversized input is split "
     "into sequential chunks, each extracted separately and merged. A payload can "
     "therefore never trigger a 413, whatever the source length. 429 (rate limit). "
     "Exponential backoff with full jitter — sleep ~ U(0, 2^attempt) seconds, 5 attempts "
     "per tier — then fall through to the next tier (Gemini → Groq → DeepSeek). "
     "Per-tier token-bucket rate limiters cap outbound calls below each provider's "
     "published limit; a 429 also writes the Retry-After value into the bucket. "
     "Circuit breaker: 10 consecutive failures open the tier for 60 s. Every retry, "
     "fallback and skip is logged with the record id, so a failed extraction is always "
     "traceable and never silently replaced by invented data."),
    ("3. Freshness tracking — never process the same article/job twice", H,
     "Identity is a content hash, not a URL: id = SHA-256(normalised URL + title). "
     "Each crawler node first does a single atomic check-and-set against a shared "
     "store (Redis SETNX / Postgres INSERT … ON CONFLICT DO NOTHING on a unique "
     "content_hash column) and only fetches/parses on success — the claim is the "
     "deduplication, so two nodes racing on the same item cannot both process it. "
     "Freshness is a machine date, never a guess: RSS pubDate / API created_at / "
     "arXiv published are parsed to ISO-8601 (relative dates such as '2 hours ago' "
     "are resolved against run time); items with no parseable date are dropped, never "
     "labelled fresh. A per-source watermark (max published_date seen) is stored after "
     "each run; the next run only accepts items newer than the watermark, and a "
     "24 h rolling window is enforced at write time (is_fresh check). Re-runs are "
     "safe: re-ingesting an old feed re-hits the same content hashes and is skipped. "
     "Clock skew is tolerated with a +5 min grace window on the future side only."),
    ("4. Storage strategy", H,
     "Primary store: PostgreSQL. The workload is relational at its core — entities "
     "with stable ids, upserts by content hash, filtered queries (fresh jobs by date, "
     "papers by category) and exactly-once semantics via unique constraints. JSONB "
     "columns hold source-specific fields without schema churn. Vector/graph needs "
     "are served by extensions and a sidecar, not by replacing Postgres: pgvector "
     "for embeddings (paper / product similarity search, dedup candidates) and "
     "Apache AGE, or Neo4j if graph traversal becomes the hot path (founder → startup "
     "→ product → paper → repo multi-hop queries). Raw payloads live in S3 (cheap, "
     "immutable, replayable); derived embeddings live in pgvector next to the rows "
     "they describe, so one transaction keeps entity + embedding consistent. "
     "Why not a graph DB as primary? Graph stores are poor at bulk upserts, range "
     "queries on dates, and exactly-once ingestion — the three operations this "
     "pipeline does most. Why not a document store alone? Entity resolution and "
     "freshness both need uniqueness constraints and joins. Postgres + pgvector + "
     "S3 gives all three, with one operational store to run."),
    ("Verification (this run). ", H,
     "1,100 / 1,100 / 1,000 rows; dedupe confirmed; 20 random source URLs per file "
     "checked (startups, products, papers 20/20 HTTP 200); all news/jobs within 24 h; "
     "16 unit tests pass. Groq tier exercised on a grounded sample 9/10 "
     "(qwen/qwen3.8-27b; llama-3.3-70b-versatile is decommissioned on Groq); "
     "Gemini/DeepSeek not exercised (no keys). GitHub stars 127/131 repos "
     "(4 repos are GitHub 404s); Papers With Code API is dead (302 to Hugging "
     "Face). Full detail: README.md and RUN_LOG.md."),
]

def build():
    doc = BaseDocTemplate(str(OUT), pagesize=A4,
                          leftMargin=18*mm, rightMargin=18*mm,
                          topMargin=16*mm, bottomMargin=16*mm,
                          title="GraphOne Intelligence Graph — Architecture",
                          author="Gautam Keshri")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])
    story = []
    for text, style, body in SECTIONS:
        story.append(Paragraph(text, style))
        if body:
            story.append(Paragraph(body, B))
        if style is H:
            story.append(Spacer(1, 1*mm))
    doc.build(story)
    from pypdf import PdfReader
    n = len(PdfReader(str(OUT)).pages)
    print(f"architecture.pdf: {n} pages")
    assert n <= 3, f"architecture.pdf is {n} pages, must be <= 3"

if __name__ == "__main__":
    build()

"""CSV + SQLite storage with the exact schemas from the assignment PDF.

Schemas (schemaVersion "1.0"):
- Startup: schemaVersion, recordType=STARTUP, source.name, source.url,
  content.entityName, content.data.employeeCount, collectedAt
- Product: schemaVersion, recordType=PRODUCT, source.name, source.url,
  content.startupName, content.pricingModel, collectedAt
  (+ content.entityName / content.productUrl kept as extra columns)
- Research Paper: schemaVersion, recordType=RESEARCH_PAPER, source.name,
  source.url, content.title, content.authors, content.paper_url,
  content.github_url, content.github_stars, content.published_date, collectedAt
- Job: schemaVersion, recordType=JOB, source.name, source.url,
  content.company, content.date, content.is_remote, content.role_family,
  collectedAt (+ content.title / content.location as extras)
- News: schemaVersion, recordType=NEWS, source.name, source.url,
  content.title, content.published_date, collectedAt
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .dates import to_iso

NOW_ISO = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_csv(path: str | Path, rows: list[dict], fieldnames: list[str]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return len(rows)


def init_seen_db(path: str | Path) -> sqlite3.Connection:
    """Dedup store: seen URLs, used by Phase II freshness (see docs/architecture.md)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, first_seen TEXT, "
        "source TEXT, published TEXT)")
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE url=?", (url,)).fetchone() is not None


def mark_seen(conn: sqlite3.Connection, url: str, source: str,
              published: str | None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen VALUES (?,?,?,?)",
        (url, to_iso(datetime.now(timezone.utc)), source, published))
    conn.commit()

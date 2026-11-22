"""Tests: date normalisation, entity resolution, chunking, 429 backoff."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.dates import parse_date, is_fresh
from src.common.rate_limiter import backoff_delay
from src.llm.orchestrator import chunk_text, DEFAULT_CHAR_BUDGET
from src.resolver.entity_resolver import EntityResolver


def test_parse_relative_hours_ago():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    dt = parse_date("2 hours ago", now=now)
    assert dt == now - timedelta(hours=2)


def test_parse_relative_minutes_and_days():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    assert parse_date("45 minutes ago", now=now) == now - timedelta(minutes=45)
    assert parse_date("3 days ago", now=now) == now - timedelta(days=3)
    assert parse_date("just now", now=now) == now


def test_parse_iso_and_rfc822():
    dt = parse_date("2026-11-22T08:30:00Z")
    assert dt is not None and dt.year == 2026 and dt.month == 11 and dt.day == 22
    dt2 = parse_date("Sat, 22 Nov 2026 08:30:00 GMT")
    assert dt2 is not None and dt2.day == 22


def test_parse_missing_date_returns_none():
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not a date at all") is None


def test_freshness_24h_boundary():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    assert is_fresh(now - timedelta(hours=23), now=now) is True
    assert is_fresh(now - timedelta(hours=25), now=now) is False
    assert is_fresh(None, now=now) is False  # undated is never fresh


def test_entity_resolution_openai_variants():
    r = EntityResolver()
    for raw in ("OpenAI", "OpenAI, Inc.", "Open AI", "openai inc"):
        canon, method = r.resolve(raw)
        assert canon == "OpenAI", (raw, canon, method)
    assert r.resolve("Anthropic")[0] == "Anthropic"
    assert r.resolve("Mistral")[0] == "Mistral AI"  # fuzzy
    canon, method = r.resolve("Some Random Nonexistent Startup XYZ")
    assert method == "unresolved" and canon == "Some Random Nonexistent Startup XYZ"


def test_chunking_never_exceeds_budget():
    long_text = "x" * 100_000
    assert len(chunk_text(long_text)) <= DEFAULT_CHAR_BUDGET
    assert len(chunk_text(long_text, budget=1000)) <= 1000
    assert chunk_text("short") == "short"
    assert chunk_text("") == ""


def test_backoff_grows_and_is_bounded():
    d0, d3 = backoff_delay(0, base=1.0), backoff_delay(3, base=1.0)
    assert 0 <= d0 <= 1.0
    assert 0 <= d3 <= 8.0  # 2**3 = 8 cap for attempt 3
    assert 0 <= backoff_delay(10, base=1.0, cap=60.0) <= 60.0

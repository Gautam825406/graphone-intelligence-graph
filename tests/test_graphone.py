"""Regression tests for the GraphOne pipeline modules in src/.

Run:  python3 -m pytest tests/test_graphone.py -q
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from date_utils import is_fresh, parse_date  # noqa: E402
from entity_resolver import EntityResolver, normalise  # noqa: E402
from llm_orchestrator import backoff_delay, chunk_input  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "data" / "seed_canonical.json"


def test_normalise_keeps_capitals():
    # Regression: PUNCT used to delete capital letters ("LeadGenius" ->
    # "eadenius"). Normalise must lowercase first.
    assert normalise("LeadGenius") == "leadgenius"
    assert normalise("OpenAI, Inc.") == "openai"


def test_resolver_exact_alias():
    r = EntityResolver(SEED)
    assert r.resolve("OpenAI, Inc.")[0] == "OpenAI"
    assert r.resolve("Open AI")[0] == "OpenAI"


def test_resolver_keeps_unknown_verbatim():
    r = EntityResolver(SEED)
    canon, method, _ = r.resolve("LeadGenius")
    assert canon == "LeadGenius" and method == "self"


def test_resolver_fuzzy():
    r = EntityResolver(SEED)
    canon, method, conf = r.resolve("Anthropicc")
    assert canon == "Anthropic" and method == "fuzzy" and conf >= 0.88


def test_parse_relative():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    iso, conf, _ = parse_date("2 hours ago", now=now)
    assert iso.startswith("2026-11-22T10:00") and conf == 0.7
    assert is_fresh(iso, now=now, hours=24)
    old, _, _ = parse_date("3 days ago", now=now)
    assert not is_fresh(old, now=now, hours=24)


def test_parse_rfc822_and_missing():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    iso, conf, _ = parse_date("Sat, 21 Nov 2026 21:37:10 +0000", now=now)
    assert iso == "2026-11-21T21:37:10Z" and conf == 1.0
    iso2, conf2, _ = parse_date(None, now=now)
    assert iso2 is None and not is_fresh(iso2, now=now)


def test_chunking_never_exceeds_budget():
    text = "Funding round. " * 5000
    chunks = chunk_input(text, 1000)
    assert chunks and all(len(c) <= 1000 for c in chunks)
    assert len(chunk_input("short", 1000)) == 1


def test_backoff_grows_with_jitter():
    ds = [backoff_delay(a) for a in range(5)]
    assert all(0 <= d <= 2 ** a for a, d in enumerate(ds))

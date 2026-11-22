"""Date parsing / normalisation for Phase II freshness.

Handles ISO-8601, RFC-822 (RSS), epoch seconds, and relative dates
("2 hours ago", "yesterday", "just now"). When a source carries no strict
date, the heuristic is explicit and the confidence is downgraded - the
record is NEVER silently labelled fresh.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from email.utils import parsedate_to_datetime

RELATIVE_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<u>second|minute|hour|day|week|month)s?\s*ago",
    re.IGNORECASE,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(raw, *, now: datetime | None = None, first_seen: datetime | None = None):
    """Return (iso_string_or_None, confidence_float, method_str).

    confidence: 1.0 strict machine date, 0.7 relative date, 0.5 feed-level
    fallback, 0.3 first-seen heuristic (only used to *exclude*, never to
    claim freshness).
    """
    now = now or now_utc()
    if raw is None:
        return None, 0.0, "missing"
    if isinstance(raw, (int, float)):
        try:
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z"), 1.0, "epoch"
        except (ValueError, OSError, OverflowError):
            return None, 0.0, "epoch_invalid"
    s = str(raw).strip()
    if not s:
        return None, 0.0, "missing"
    low = s.lower()
    if low in ("just now", "now", "today"):
        return now.isoformat().replace("+00:00", "Z"), 0.7, "relative:just-now"
    if low == "yesterday":
        dt = now - timedelta(days=1)
        return dt.isoformat().replace("+00:00", "Z"), 0.7, "relative:yesterday"
    m = RELATIVE_RE.search(s)
    if m:
        n = int(m.group("n"))
        unit = m.group("u").lower()
        delta = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
            "month": timedelta(days=30 * n),
        }[unit]
        dt = now - delta
        return dt.isoformat().replace("+00:00", "Z"), 0.7, f"relative:{n}-{unit}"
    # ISO-8601 (incl. trailing Z)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), 1.0, "iso8601"
    except ValueError:
        pass
    # RFC-822 / RSS pubDate
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            raise ValueError("unparseable")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), 1.0, "rfc822"
    except (ValueError, TypeError):
        pass
    # Fallback: first-seen heuristic - mark low confidence, caller decides.
    if first_seen is not None:
        return (
            first_seen.isoformat().replace("+00:00", "Z"),
            0.3,
            "heuristic:first-seen",
        )
    return None, 0.0, "unparseable"


def is_fresh(iso: str | None, *, now: datetime | None = None, hours: int = 24) -> bool:
    """True only if a parsed date exists and is within the last `hours`."""
    if not iso:
        return False
    now = now or now_utc()
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - dt) <= timedelta(hours=hours) and dt <= now + timedelta(minutes=5)

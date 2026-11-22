"""Date normalisation — the freshness-critical module.

Rules (honest, no invention):
- Parse ISO-8601, RFC-822 (RSS), and common human formats.
- Parse relative dates: "2 hours ago", "45 minutes ago", "3 days ago".
- If no date can be found, return None — the caller must NOT invent one.
  Callers treat None as "not provably fresh" and exclude it from 24h output.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil import parser as dateutil_parser

_RELATIVE_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>second|seconds|minute|minutes|hour|hours|day|days|week|weeks)\s*ago",
    re.IGNORECASE,
)
_JUST_NOW_RE = re.compile(r"just\s+now", re.IGNORECASE)


def parse_date(raw: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Normalise any date string to an aware UTC datetime, or None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    if _JUST_NOW_RE.search(text):
        return now
    m = _RELATIVE_RE.search(text)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit").lower()
        delta = {
            "second": timedelta(seconds=n), "seconds": timedelta(seconds=n),
            "minute": timedelta(minutes=n), "minutes": timedelta(minutes=n),
            "hour": timedelta(hours=n), "hours": timedelta(hours=n),
            "day": timedelta(days=n), "days": timedelta(days=n),
            "week": timedelta(weeks=n), "weeks": timedelta(weeks=n),
        }[unit]
        return now - delta
    try:
        dt = dateutil_parser.parse(text)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_fresh(dt: Optional[datetime], hours: int = 24,
             now: Optional[datetime] = None) -> bool:
    """True only if dt is provably within the last `hours` hours."""
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - dt) <= timedelta(hours=hours) and dt <= now + timedelta(minutes=5)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None

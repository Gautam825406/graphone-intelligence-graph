"""Deterministic entity resolution (Phase IV).

Pipeline per raw name:
  1. Normalise: strip, collapse whitespace, drop legal suffixes
     (Inc, LLC, Ltd, GmbH, Pvt Ltd, ...), drop punctuation, case-fold.
  2. Exact match against the seed canonical list / alias table.
  3. Fuzzy match (rapidfuzz token_sort_ratio, threshold 88) against seed.
  4. Otherwise keep the cleaned name as its own canonical form and
     record method="self" - we never invent a canonical name.

Every decision is appended to the mapping log: raw -> canonical,
method, confidence. No LLM is involved here: resolution must be
reproducible, so it is pure string logic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|company|gmbh|pty|pvt|private|"
    r"s\.a\.|s\.p\.a\.|b\.v\.|n\.v\.|kk|plc)\b\.?",
    re.IGNORECASE,
)
PUNCT = re.compile(r"[^a-z0-9&\s\.]")
WS = re.compile(r"\s+")


def normalise(name: str) -> str:
    # Lowercase FIRST: the punctuation class only keeps a-z, so applying
    # it to mixed-case input would silently delete capital letters
    # (e.g. "LeadGenius" -> "eadenius"). Fixed 22 Nov 2026.
    s = (name or "").strip().lower()
    s = LEGAL_SUFFIXES.sub("", s)
    s = s.replace("&", " and ")
    s = PUNCT.sub("", s)
    s = WS.sub(" ", s).strip()
    return s


def _key(name: str) -> str:
    return normalise(name).lower()


class EntityResolver:
    def __init__(self, seed_path: str | Path):
        data = json.loads(Path(seed_path).read_text())
        self.canonical: list[str] = data["canonical"]
        self.aliases: dict[str, str] = data.get("aliases", {})
        self._by_key = {_key(c): c for c in self.canonical}
        for alias, canon in self.aliases.items():
            self._by_key[_key(alias)] = canon
        self.log: list[dict] = []

    def resolve(self, raw: str, *, source: str = "") -> tuple[str, str, float]:
        """Return (canonical, method, confidence) and append to the log."""
        raw = (raw or "").strip()
        if not raw:
            return "", "empty", 0.0
        key = _key(raw)
        if key in self._by_key:
            canon, method, conf = self._by_key[key], "exact", 1.0
        else:
            canon, method, conf = self._fuzzy(raw)
        self.log.append(
            {
                "raw_name": raw,
                "canonical_name": canon,
                "method": method,
                "confidence": round(conf, 3),
                "source": source,
            }
        )
        return canon, method, conf

    def _fuzzy(self, raw: str) -> tuple[str, str, float]:
        try:
            from rapidfuzz import fuzz
        except ImportError:  # pragma: no cover - difflib fallback
            import difflib

            best, score = None, 0.0
            for cand in self.canonical:
                s = difflib.SequenceMatcher(None, _key(raw), _key(cand)).ratio() * 100
                if s > score:
                    best, score = cand, s
            if best and score >= 88:
                return best, "fuzzy", score / 100
            return raw.strip(), "self", 0.5
        from rapidfuzz import fuzz

        best, score = None, 0.0
        for cand in self.canonical:
            s = fuzz.token_sort_ratio(_key(raw), _key(cand))
            if s > score:
                best, score = cand, s
        if best and score >= 88:
            return best, "fuzzy", score / 100
        # No seed match: keep the raw name verbatim as its own canonical
        # form. Never rewrite casing/spelling we did not verify.
        return raw.strip(), "self", 0.5

    def dedupe_key(self, name: str) -> str:
        return _key(name)

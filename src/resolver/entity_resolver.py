"""Deterministic entity resolution (Phase IV).

Normalisation: lowercase, strip punctuation / legal suffixes
(Inc., Ltd., Corp., LLC, AI-etc. handled carefully), collapse whitespace.
Matching: exact normalised match first; then rapidfuzz token-sort ratio
(threshold 90) or difflib fallback; otherwise return the cleaned raw name
and log it as UNRESOLVED — never force a wrong canonical.
Every decision is logged to an Entity Mapping Log: raw -> canonical, method.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    import difflib
    _HAS_RAPIDFUZZ = False

# NOTE: "ai" / "labs" are NOT stripped — they are part of real names
# ("Open AI", "Stability AI", "Scale AI"). Only legal suffixes are stripped.
_SUFFIXES = re.compile(
    r"\b(inc|ltd|llc|corp|corporation|company|co)\.?,?$",
    re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9& ]+")
_WS = re.compile(r"\s+")


def normalise(name: str) -> str:
    s = name.strip().lower()
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    # Strip one trailing legal suffix, then re-collapse.
    s2 = _SUFFIXES.sub("", s).strip()
    return _WS.sub(" ", s2).strip() or s


def _compact(name: str) -> str:
    """Space-stripped form, so 'Open AI' matches 'OpenAI'."""
    return normalise(name).replace(" ", "")


def _similarity(a: str, b: str) -> float:
    if _HAS_RAPIDFUZZ:
        return max(fuzz.token_sort_ratio(a, b),
                   fuzz.ratio(_compact(a), _compact(b)))
    return difflib.SequenceMatcher(None, _compact(a), _compact(b)).ratio() * 100


class EntityResolver:
    def __init__(self, seed_path: str | Path | None = None):
        path = Path(seed_path) if seed_path else Path(__file__).with_name("seed_canonical.json")
        self.canonical: list[str] = json.loads(path.read_text())["canonical"]
        self._by_norm = {normalise(c): c for c in self.canonical}
        self._by_norm.update({_compact(c): c for c in self.canonical})
        self.mapping_log: list[dict] = []

    def resolve(self, raw: str) -> tuple[str, str]:
        """Return (canonical_name, method). Method: exact|fuzzy|unresolved."""
        raw_clean = raw.strip()
        norm = normalise(raw_clean)
        if norm in self._by_norm:
            method = "exact"
            canon = self._by_norm[norm]
        elif _compact(raw_clean) in self._by_norm:
            method = "exact"
            canon = self._by_norm[_compact(raw_clean)]
        else:
            best, best_score = None, 0.0
            for canon in self.canonical:
                score = _similarity(norm, normalise(canon))
                if score > best_score:
                    best, best_score = canon, score
            if best is not None and best_score >= 85:
                method, canon = "fuzzy", best
            else:
                method, canon = "unresolved", raw_clean
        self.mapping_log.append(
            {"raw_name": raw_clean, "canonical_name": canon, "method": method})
        return canon, method

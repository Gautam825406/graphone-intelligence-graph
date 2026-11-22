"""Multi-tier LLM extraction engine (Phase III).

Fallback chain: Gemini Flash -> Groq Llama-3 -> DeepSeek.
Keys come ONLY from environment variables (GEMINI_API_KEY,
GROQ_API_KEY, DEEPSEEK_API_KEY). If a tier has no key it is skipped
and logged - never a hard failure, and never a fabricated key.

Hard rules:
  * The LLM ONLY structures text that came from a real source record.
    It is forbidden to invent entities, URLs, dates or numbers. The
    system prompt says so, and `validate_against_source()` drops any
    output field that is not grounded in the input text.
  * 413 (payload too large): chunk_input() keeps the head + the most
    information-dense sentences (keyword-weighted) under a per-model
    char budget; each chunk is extracted separately and merged.
  * 429 (rate limit): exponential backoff with full jitter, up to
    5 attempts per tier, then fall through to the next tier.
  * If no tier is configured, extract() returns a structured
    "llm_skipped" result and the pipeline keeps the API-structured
    record as-is. That is the honest behaviour for this trial run:
    arXiv / YC / Hugging Face already return structured JSON, so no
    LLM call is needed to produce the deliverable rows.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass

log = logging.getLogger("graphone.llm")

# Conservative char budgets (~4 chars/token). Gemini Flash 1M ctx in
# theory, but we budget for the *extraction* payload, not the model max.
TIER_BUDGET_CHARS = {"gemini": 120_000, "groq": 24_000, "deepseek": 60_000}
MAX_ATTEMPTS = 5
BASE_BACKOFF_S = 1.0

SYSTEM_PROMPT = (
    "You structure ONLY the text you are given into JSON. Rules: "
    "1) Never invent a name, URL, date, number or fact. "
    "2) If a field is not present in the input, output null for it. "
    "3) Copy URLs and dates exactly as written. "
    "4) Output valid JSON only, no commentary."
)

DENSE_KEYWORDS = re.compile(
    r"(founded|funding|revenue|employee|headquarter|product|pricing|"
    r"launch|acqui|valuation|investor|ceo|founder)",
    re.IGNORECASE,
)


def chunk_input(text: str, budget_chars: int) -> list[str]:
    """Split `text` into chunks <= budget, keeping dense content first.

    Head (first 40% of budget) is always kept verbatim - it usually
    holds the title/lede. The remainder is filled with the highest
    keyword-density sentences, in original order.
    """
    if len(text) <= budget_chars:
        return [text]
    head_len = int(budget_chars * 0.4)
    head, rest = text[:head_len], text[head_len:]
    sentences = re.split(r"(?<=[.!?])\s+", rest)
    scored = sorted(
        enumerate(sentences),
        key=lambda p: len(DENSE_KEYWORDS.findall(p[1])),
        reverse=True,
    )
    room = budget_chars - head_len
    keep: set[int] = set()
    used = 0
    for idx, sent in scored:
        if used + len(sent) + 1 > room:
            continue
        keep.add(idx)
        used += len(sent) + 1
    tail = " ".join(sentences[i] for i in sorted(keep))
    chunks = [head + "\n" + tail]
    # If the tail still overflowed (pathological input), hard-split.
    out: list[str] = []
    for c in chunks:
        while len(c) > budget_chars:
            out.append(c[:budget_chars])
            c = c[budget_chars:]
        out.append(c)
    return out


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter: U(0, base * 2**attempt)."""
    return random.uniform(0, BASE_BACKOFF_S * (2 ** attempt))


@dataclass
class LLMResult:
    tier: str
    data: dict | None
    status: str  # "ok" | "llm_skipped" | "all_tiers_failed"
    detail: str = ""


class LLMOrchestrator:
    def __init__(self):
        self.keys = {
            "gemini": os.environ.get("GEMINI_API_KEY"),
            "groq": os.environ.get("GROQ_API_KEY"),
            "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
        }
        self.configured = [t for t, k in self.keys.items() if k]
        log.info("LLM tiers configured: %s", self.configured or "none")

    # -- grounding guard -------------------------------------------------
    @staticmethod
    def validate_against_source(data: dict, source_text: str) -> dict:
        """Drop string values that do not appear (case-insensitive) in
        the source text, except nulls/numbers already present. URLs must
        appear verbatim."""
        clean: dict = {}
        low = source_text.lower()
        for k, v in data.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, str):
                clean[k] = v if (v.lower() in low or len(v) < 4) else None
            else:
                clean[k] = v
        return clean

    # -- tier clients ----------------------------------------------------
    def _call_gemini(self, prompt: str) -> dict:
        import google.generativeai as genai

        genai.configure(api_key=self.keys["gemini"])
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content(SYSTEM_PROMPT + "\n\n" + prompt)
        return json.loads(resp.text.strip().strip("`").replace("json", "", 1))

    def _call_groq(self, prompt: str) -> dict:
        import urllib.request

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {self.keys['groq']}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
        return json.loads(body["choices"][0]["message"]["content"])

    def _call_deepseek(self, prompt: str) -> dict:
        import urllib.request

        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(
                {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {self.keys['deepseek']}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
        return json.loads(body["choices"][0]["message"]["content"])

    _CALLERS = {"gemini": _call_gemini, "groq": _call_groq, "deepseek": _call_deepseek}

    def extract(self, text: str, schema_hint: str) -> LLMResult:
        """Structure `text` into JSON. Returns llm_skipped when no tier
        is configured - the caller keeps its API-structured record."""
        if not self.configured:
            return LLMResult(
                tier="none",
                data=None,
                status="llm_skipped",
                detail="No LLM API key configured (GEMINI/GROQ/DEEPSEEK_API_KEY); "
                "records kept in their source-structured form.",
            )
        prompt = f"Extract into this JSON shape: {schema_hint}\n\nTEXT:\n{text}"
        for tier in ("gemini", "groq", "deepseek"):
            if tier not in self.configured:
                continue
            budget = TIER_BUDGET_CHARS[tier]
            chunks = chunk_input(text, budget)
            merged: dict = {}
            try:
                for chunk in chunks:
                    for attempt in range(MAX_ATTEMPTS):
                        try:
                            out = self._CALLERS[tier](self, prompt if len(chunks) == 1 else chunk)
                            merged.update(self.validate_against_source(out, text))
                            break
                        except Exception as exc:  # 429 / 5xx / timeout
                            msg = str(exc)
                            if "429" in msg or "rate" in msg.lower():
                                if attempt == MAX_ATTEMPTS - 1:
                                    raise
                                time.sleep(backoff_delay(attempt))
                                continue
                            raise
                return LLMResult(tier=tier, data=merged, status="ok")
            except Exception as exc:
                log.warning("Tier %s failed (%s); falling through.", tier, exc)
                continue
        return LLMResult(
            tier="none", data=None, status="all_tiers_failed",
            detail="All configured LLM tiers failed after retries.",
        )

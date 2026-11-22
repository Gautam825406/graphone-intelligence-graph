"""Phase III — Multi-tier LLM extraction engine.

Fallback chain: Gemini Flash (Tier 1) -> Groq Llama 3 (Tier 2) -> DeepSeek (Tier 3).
- Env-key gating: a tier with no key is SKIPPED honestly, never faked.
  If no key is set at all, extract() raises LLMUnavailable — the pipeline
  must then fall back to deterministic (non-LLM) parsing, not hallucinate.
- Chunking: payloads are truncated to fit a per-tier char budget
  (default 24,000 chars ≈ 6k tokens, well under every tier's context
  window) by keeping the semantically dense head + tail; chunk_text()
  is unit-tested to never exceed the limit.
- 429 handling: exponential backoff + full jitter (src/common/rate_limiter),
  up to 5 attempts per tier, then fall through to the next tier.
- Anti-hallucination prompt: "Extract ONLY facts present in the text.
  If a field is not present, return null. Never invent a name, date,
  number or URL."

LLM calls are lazy-imported (google-generativeai / groq / openai) so the
package imports and tests pass with zero keys installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass

from src.common.logging_config import get_logger
from src.common.rate_limiter import backoff_delay

logger = get_logger("llm")

DEFAULT_CHAR_BUDGET = 24_000
SYSTEM_PROMPT = (
    "You extract structured JSON from the supplied text. "
    "Extract ONLY facts present in the text. If a field is not present, "
    "return null for it. Never invent a name, date, number or URL. "
    "Return valid JSON only, no commentary."
)


class LLMUnavailable(RuntimeError):
    """No LLM tier is configured (no API key) — caller must use deterministic parsing."""


class RateLimited(RuntimeError):
    pass


def chunk_text(text: str, budget: int = DEFAULT_CHAR_BUDGET) -> str:
    """Truncate to <= budget chars, keeping head + tail (densest parts).

    Never exceeds budget; returns "" for empty input.
    """
    if len(text) <= budget:
        return text
    head = int(budget * 0.7)
    tail = budget - head - 20  # 20 chars for the ellipsis marker
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


@dataclass
class Tier:
    name: str
    env_key: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.env_key))


TIERS = [
    Tier("gemini", "GEMINI_API_KEY", "gemini-1.5-flash"),
    Tier("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    Tier("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
]

# --- per-tier callers (lazy imports, 429 detection) -----------------------

def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


async def _call_gemini(prompt: str, model: str) -> str:
    import google.generativeai as genai  # type: ignore
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    m = genai.GenerativeModel(model)
    resp = await asyncio.to_thread(m.generate_content, prompt)
    return resp.text or ""


async def _call_groq(prompt: str, model: str) -> str:
    from groq import Groq  # type: ignore
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    resp = await asyncio.to_thread(
        lambda: client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": prompt}],
            temperature=0))
    return resp.choices[0].message.content or ""


async def _call_deepseek(prompt: str, model: str) -> str:
    from openai import OpenAI  # type: ignore
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                    base_url="https://api.deepseek.com")
    resp = await asyncio.to_thread(
        lambda: client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": prompt}],
            temperature=0))
    return resp.choices[0].message.content or ""


_CALLERS = {"gemini": _call_gemini, "groq": _call_groq, "deepseek": _call_deepseek}


async def extract(text: str, schema_hint: str = "",
                  budget: int = DEFAULT_CHAR_BUDGET) -> dict:
    """Extract structured JSON from `text` via the fallback chain.

    Raises LLMUnavailable if no tier is configured; raises the last
    error if every configured tier fails after retries.
    """
    payload = chunk_text(text, budget)
    prompt = f"{SYSTEM_PROMPT}\nSchema hint: {schema_hint}\n\nText:\n{payload}"
    configured = [t for t in TIERS if t.configured]
    if not configured:
        raise LLMUnavailable(
            "No LLM tier configured — set GEMINI_API_KEY, GROQ_API_KEY or "
            "DEEPSEEK_API_KEY. Refusing to fabricate extraction output.")
    last_exc: Exception | None = None
    for tier in configured:
        for attempt in range(5):
            try:
                raw = await _CALLERS[tier.name](prompt, tier.model)
                return json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_rate_limit(exc):
                    delay = backoff_delay(attempt)
                    logger.warning("%s 429 (attempt %d) — backing off %.1fs",
                                   tier.name, attempt, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("%s failed: %s", tier.name, exc)
                break  # non-429: fall through to next tier
        logger.warning("Tier %s exhausted — trying next tier", tier.name)
    raise RuntimeError(f"All LLM tiers failed. Last error: {last_exc}")

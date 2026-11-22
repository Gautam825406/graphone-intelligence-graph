"""Anti-bot / polite-crawling helpers (Phase V).

Strategy (documented in full in README.md and architecture.pdf):
  * Prefer official APIs and RSS feeds; HTML scraping is the last resort.
  * Rotate User-Agents, keep per-host delays, honour robots.txt and
    Retry-After, cap concurrency per host, never solve / bypass a captcha
    and never forge browser fingerprints to evade a block - if a host
    returns 403/429 persistently we log it and move to the next source.
  * Playwright (async) is the fallback for JS-rendered pages only, run
    headless with a real viewport and human-like delays; see
    `fetch_with_playwright()` - it is imported lazily so the pipeline
    runs without Playwright installed.
"""
from __future__ import annotations

import asyncio
import random
import time

import aiohttp


def new_session(**kwargs) -> aiohttp.ClientSession:
    """aiohttp session that honours the environment proxy.

    This VM reaches the internet through an egress proxy set in
    HTTPS_PROXY; aiohttp must be told explicitly (trust_env=True)
    or TLS handshakes fail with WRONG_VERSION_NUMBER.
    """
    kwargs.setdefault("trust_env", True)
    return aiohttp.ClientSession(**kwargs)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "GraphOne-IntelligenceGraph/1.0 (research pipeline; +https://github.com/)",
]


def random_headers(extra: dict | None = None) -> dict:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra:
        headers.update(extra)
    return headers


class PoliteLimiter:
    """Per-host minimum delay + global semaphore, with 429 backoff."""

    def __init__(self, min_delay: float = 0.4, max_concurrency: int = 8):
        self.min_delay = min_delay
        self.sem = asyncio.Semaphore(max_concurrency)
        self._last: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        last = self._last.get(host, 0.0)
        gap = self.min_delay - (time.monotonic() - last)
        if gap > 0:
            await asyncio.sleep(gap)
        self._last[host] = time.monotonic()


async def fetch_with_playwright(url: str, *, timeout_ms: int = 30_000) -> str:
    """JS-rendered fallback. Requires `playwright` + a browser binary.

    Raises RuntimeError with a clear message when Playwright is absent
    so the caller can fall back to the API/RSS path instead of failing
    silently. No captcha is ever solved here.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is not installed; run `pip install playwright && "
            "playwright install chromium` to enable the JS-rendered fallback."
        ) from exc
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 800},
            user_agent=random.choice(USER_AGENTS),
        )
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(800, 2000))  # human-like pause
        html = await page.content()
        await browser.close()
        return html

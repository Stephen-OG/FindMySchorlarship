"""
Async HTTP fetching, queue management, link extraction, and search fallback.

The fetch() function adds page-level HTML caching (24h TTL) so re-crawling
the same URL within or across sessions doesn't re-hit the network.
"""

import asyncio
import os
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin

import aiohttp
import requests
from bs4 import BeautifulSoup

try:
    from serpapi import GoogleSearch  # type: ignore
except Exception:
    try:
        from serpapi.google_search import GoogleSearch  # type: ignore
    except Exception:
        GoogleSearch = None

from utils.cache import get_cache
from utils.crawl._constants import (
    HEADERS,
    SEARCH_FALLBACK_URL_LIMIT,
)
from utils.crawl._utils import (
    get_domain_scope,
    normalize_url,
    sanitize_text_for_llm,
)
from utils.crawl.models import CrawlQueueItem, QueryConstraints
from utils.logger import logger

SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")
HTML_CACHE_TTL = int(os.getenv("HTML_CACHE_TTL_SECONDS", str(24 * 3600)))  # 24 hours


def _looks_like_html(body: str) -> bool:
    sample = (body or "").lstrip().lower()
    return (
        sample.startswith("<!doctype html") or sample.startswith("<html") or "<html" in sample[:500]
    )


def _is_bot_challenge(status: int, body: str) -> bool:
    """Return True when the response is a bot-protection challenge (e.g. Cloudflare)."""
    if status not in (403, 429, 503):
        return False
    sample = (body or "").lower()
    return any(
        marker in sample
        for marker in (
            "just a moment",
            "cf-browser-verification",
            "enable javascript",
            "checking your browser",
        )
    )


def _is_html_response(content_type: str, body: str) -> bool:
    content_type = (content_type or "").lower()
    return (
        "text/html" in content_type
        or "application/xhtml+xml" in content_type
        or _looks_like_html(body)
    )


def _requests_fetch(url: str, timeout: int) -> Optional[str]:
    """Best-effort fallback for sites that behave poorly with aiohttp."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        body = response.text
        if response.ok and _is_html_response(response.headers.get("content-type", ""), body):
            return body
        logger.debug(
            "Requests fallback skipped %s: status=%s content_type=%s",
            url,
            response.status_code,
            response.headers.get("content-type", ""),
        )
    except Exception as exc:
        logger.debug("Requests fallback failed for %s: %s", url, exc)
    return None


_PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_PLAYWRIGHT_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]
# Max concurrent Playwright pages per browser to cap memory usage.
_PLAYWRIGHT_CONCURRENCY = 4


async def create_playwright_browser():
    """
    Launch a shared headless Chromium browser for the duration of a crawl.

    Usage in engine.py:
        browser, pw = await create_playwright_browser()
        try:
            ...pass browser to fetch()...
        finally:
            await browser.close()
            await pw.stop()
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — bot-protected sites will not be fetched")
        return None, None

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=_PLAYWRIGHT_LAUNCH_ARGS)
    return browser, pw


async def _playwright_fetch(
    url: str,
    timeout: int,
    browser=None,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Optional[str]:
    """
    Fetch a URL using a headless Chromium page, bypassing Cloudflare and other
    bot-protection that blocks datacenter IPs.

    Accepts a shared `browser` (created once per crawl session via
    create_playwright_browser) so Chrome only launches once per crawl.
    Falls back to creating a standalone browser when none is supplied.
    """
    _own_browser = False
    pw_handle = None

    try:
        if browser is None or not browser.is_connected():
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                logger.warning("Playwright not installed — cannot bypass bot protection")
                return None
            pw_handle = await async_playwright().start()
            browser = await pw_handle.chromium.launch(
                headless=True, args=_PLAYWRIGHT_LAUNCH_ARGS
            )
            _own_browser = True

        async def _fetch_page() -> Optional[str]:
            context = await browser.new_context(
                user_agent=_PLAYWRIGHT_USER_AGENT,
                java_script_enabled=True,
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            try:
                await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                html = await page.content()
                return html if html and _looks_like_html(html) else None
            finally:
                await context.close()

        if semaphore is not None:
            async with semaphore:
                html = await _fetch_page()
        else:
            html = await _fetch_page()

        if html:
            logger.info("Playwright hit: %s", url)
        return html

    except Exception as exc:
        logger.debug("Playwright failed for %s: %s", url, exc)
        return None
    finally:
        if _own_browser and browser is not None:
            await browser.close()
        if pw_handle is not None:
            await pw_handle.stop()


# ── HTTP Fetch (with URL-level HTML cache) ─────────────────────────────────────


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = 15,
    browser=None,
    pw_semaphore: Optional[asyncio.Semaphore] = None,
) -> Optional[str]:
    """
    Fetch a single URL and return its HTML, or None on failure.

    Checks the persistent cache first (key: ``html:<url>``).  On a live fetch,
    successful HTML responses are stored for HTML_CACHE_TTL seconds so the same
    page is never downloaded twice — even across server restarts.

    Pass `browser` (from create_playwright_browser) and `pw_semaphore` to reuse
    a single Chromium instance across the crawl instead of launching a new one
    per bot-protected URL.
    """
    cache_key = f"html:{url}"
    cache = get_cache()

    cached_html = await cache.get(cache_key)
    if cached_html is not None:
        logger.debug("HTML cache hit: %s", url)
        return cached_html

    bot_protected = False
    try:
        async with session.get(
            url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            html = await r.text()
            if r.status == 200 and _is_html_response(r.headers.get("content-type", ""), html):
                await cache.set(cache_key, html, HTML_CACHE_TTL)
                return html
            if _is_bot_challenge(r.status, html) or r.status == 403:
                bot_protected = True
                logger.warning(
                    "⚠️  Bot protection detected for %s (status=%s) — site is blocking automated access",
                    url,
                    r.status,
                )
            else:
                logger.debug(
                    "Skipping %s: status=%s content_type=%s",
                    url,
                    r.status,
                    r.headers.get("content-type", "").lower(),
                )
    except Exception as exc:
        logger.debug("Fetch failed for %s: %s", url, exc)

    # Plain requests won't bypass Cloudflare — skip it for bot-protected sites.
    if not bot_protected:
        html = await asyncio.to_thread(_requests_fetch, url, timeout)
        if html is not None:
            await cache.set(cache_key, html, HTML_CACHE_TTL)
            return html

    # For bot-protected sites use the shared Playwright browser (headless Chromium)
    # to solve Cloudflare JS challenges. Allow extra time for JS rendering.
    playwright_timeout = max(timeout, 60) if bot_protected else timeout
    html = await _playwright_fetch(url, playwright_timeout, browser=browser, semaphore=pw_semaphore)
    if html is not None:
        await cache.set(cache_key, html, HTML_CACHE_TTL)
        return html

    return None


# ── Queue management ───────────────────────────────────────────────────────────


def pop_next_batch(
    queue: List[CrawlQueueItem],
    visited: Set[str],
    queued_urls: Set[str],
    batch_size: int = 12,
    required_seed_visits_remaining: int = 0,
) -> List[CrawlQueueItem]:
    """
    Pop the next batch of URLs to fetch, prioritising seed URLs first
    until the required seed-visit quota is met.
    """
    queue.sort(key=lambda item: item.priority, reverse=True)
    batch: List[CrawlQueueItem] = []

    # Fill with seeds first (ensures high-priority paths are visited)
    while queue and len(batch) < batch_size and required_seed_visits_remaining > 0:
        idx = next((i for i, item in enumerate(queue) if item.source == "seed"), None)
        if idx is None:
            break
        item = queue.pop(idx)
        queued_urls.discard(item.url)
        if item.url in visited:
            continue
        visited.add(item.url)
        batch.append(item)
        required_seed_visits_remaining -= 1

    # Fill remaining slots from top of queue
    while queue and len(batch) < batch_size:
        item = queue.pop(0)
        queued_urls.discard(item.url)
        if item.url in visited:
            continue
        visited.add(item.url)
        batch.append(item)

    return batch


# ── Link extraction ────────────────────────────────────────────────────────────


def extract_links(html: str, base_url: str) -> List[str]:
    """Extract all internal links from HTML, restricted to the same domain scope."""
    from urllib.parse import urlparse

    soup = BeautifulSoup(html, "lxml")
    base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(base_url))
    base_scope = get_domain_scope(base_url)
    links: List[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:")):
            continue
        absolute = urljoin(base, href)
        if get_domain_scope(absolute) == base_scope:
            links.append(normalize_url(absolute))

    return list(set(links))


# ── SerpAPI fallback ───────────────────────────────────────────────────────────


def search_fallback_urls(
    domain_url: str,
    user_query: Optional[str],
    constraints: QueryConstraints,
) -> List[Dict[str, str]]:
    """
    Use site-restricted SerpAPI search to find likely funding pages when
    the structural BFS crawl returns weak results.

    Only called when fetched_page_results is empty or avg_score < 20.
    """
    if GoogleSearch is None or not SERPAPI_KEY:
        return []

    scope = get_domain_scope(domain_url)
    if not scope:
        return []

    subject_terms = constraints.subject_terms[:2]
    degree_terms = constraints.degree_terms[:2] or ["phd", "doctoral"]
    query_variants = [
        f"site:{scope} {' '.join(degree_terms)} funding",
        f"site:{scope} {' '.join(degree_terms)} scholarship",
    ]
    if subject_terms:
        query_variants.append(f"site:{scope} {' '.join(subject_terms)} {' '.join(degree_terms)}")
        query_variants.append(
            f"site:{scope} {' '.join(subject_terms)} funding scholarship research"
        )
    if user_query:
        query_variants.append(f"site:{scope} {user_query}")

    url_entries: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for query in query_variants[:4]:
        try:
            search = GoogleSearch({"q": query, "api_key": SERPAPI_KEY, "num": 5})
            results = search.get_dict()
        except Exception as exc:
            logger.debug("Fallback search failed for %s: %s", query, exc)
            continue

        for result in results.get("organic_results", []) or []:
            link = normalize_url(str(result.get("link", "") or ""))
            if not link or get_domain_scope(link) != scope or link in seen:
                continue
            seen.add(link)
            url_entries.append(
                {
                    "url": link,
                    "title": sanitize_text_for_llm(str(result.get("title", "") or "")),
                    "snippet": sanitize_text_for_llm(str(result.get("snippet", "") or "")),
                }
            )
            if len(url_entries) >= SEARCH_FALLBACK_URL_LIMIT:
                return url_entries

    return url_entries

"""
University domain discovery — tiered lookup strategy.

Tier 1: Curated database  (utils/university_db.py) — instant, zero cost
Tier 2: DuckDuckGo        (free, no API key required)
Tier 3: SerpAPI           (paid, last resort only)

Results are persisted in the shared cache (SQLite/Redis) so every successful
lookup is remembered across server restarts.
"""

import os
import re
from typing import List, Optional
from urllib.parse import urlparse

import aiohttp
from agents import function_tool
from dotenv import load_dotenv

try:
    from serpapi import GoogleSearch  # type: ignore
except Exception:
    try:
        from serpapi.google_search import GoogleSearch  # type: ignore
    except Exception:
        GoogleSearch = None

from utils.cache import DOMAIN_TTL_SECONDS, get_cache
from utils.logger import logger
from utils.university_db import lookup_university

load_dotenv(override=True)

SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")

_SCHOOL_STOPWORDS = {
    "of",
    "the",
    "and",
    "for",
    "at",
    "in",
    "on",
    "university",
    "college",
    "institute",
    "school",
}
_NON_UNIVERSITY_HINTS = {"research", "institute", "company", "corp", "inc", "foundation", "ngo"}
_UNIVERSITY_HINTS = {"university", "univ", "college", "faculty", "campus", "edu", ".ac."}


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _normalize_netloc(url: str) -> str:
    netloc = urlparse(url).netloc.lower().strip()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _domain_scope(netloc: str) -> str:
    parts = netloc.split(".")
    if len(parts) <= 2:
        return netloc
    if parts[-2:] == ["ac", "uk"] and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _school_tokens(school: str) -> List[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", school.lower())
    return [t for t in raw_tokens if len(t) > 2 and t not in _SCHOOL_STOPWORDS]


def _is_probable_university_domain(netloc: str, school_tokens: List[str]) -> bool:
    domain_compact = netloc.replace("-", "").replace(".", "")
    token_match = any(token in domain_compact for token in school_tokens)
    has_uni_hint = any(hint in netloc for hint in _UNIVERSITY_HINTS)
    has_academic_tld = ".edu" in netloc or ".ac." in netloc or netloc.endswith(".ac.uk")
    has_non_uni_hint = any(hint in netloc for hint in _NON_UNIVERSITY_HINTS)

    if not (token_match or has_uni_hint or has_academic_tld):
        return False
    if has_non_uni_hint and not (has_uni_hint or has_academic_tld):
        return False
    return True


def _dedupe_domains(raw_urls: List[str], school_tokens: List[str], num: int) -> List[str]:
    """Filter, normalize and deduplicate a list of candidate URLs."""
    cleaned: List[str] = []
    seen: set = set()
    for u in raw_urls:
        netloc = _normalize_netloc(u)
        if not netloc:
            continue
        if _is_probable_university_domain(netloc, school_tokens):
            scope = _domain_scope(netloc)
            for candidate in (scope, netloc):
                base = f"https://{candidate}"
                if base not in seen:
                    cleaned.append(base)
                    seen.add(base)

    # Permissive fallback
    if not cleaned:
        for u in raw_urls:
            netloc = _normalize_netloc(u)
            if not netloc:
                continue
            if (
                "univ" in netloc
                or ".edu" in netloc
                or ".ac." in netloc
                or any(token in netloc for token in school_tokens)
            ):
                scope = _domain_scope(netloc)
                for candidate in (scope, netloc):
                    base = f"https://{candidate}"
                    if base not in seen:
                        cleaned.append(base)
                        seen.add(base)

    return cleaned[:num]


# ── Tier 2: DuckDuckGo Instant Answer ─────────────────────────────────────────


async def _duckduckgo_search(school: str, country: Optional[str], num: int) -> List[str]:
    """
    Search DuckDuckGo's free JSON endpoint for university domains.
    No API key required. Called only after a DB miss.
    """
    query_parts = [school, "official site", "scholarship", "funding"]
    if country:
        query_parts.append(country)
    query = " ".join(query_parts)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
    }
    params = {"q": query, "format": "json", "no_html": "1", "no_redirect": "1"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.duckduckgo.com/",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"DuckDuckGo returned status {resp.status} for '{school}'")
                    return []
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed for '{school}': {e}")
        return []

    urls: List[str] = []

    if data.get("AbstractURL"):
        urls.append(data["AbstractURL"])

    for topic in data.get("RelatedTopics", []):
        if isinstance(topic, dict) and topic.get("FirstURL"):
            urls.append(topic["FirstURL"])

    for item in data.get("Infobox", {}).get("content", []):
        if isinstance(item, dict) and item.get("data_type") == "string":
            val = item.get("value", "")
            if val.startswith("http"):
                urls.append(val)

    school_tokens = _school_tokens(school)
    return _dedupe_domains(urls, school_tokens, num)


# ── Tier 3: SerpAPI (existing logic, unchanged) ────────────────────────────────


def _serpapi_search(school: str, country: Optional[str], num: int) -> List[str]:
    """Call SerpAPI as the final fallback. Requires SERPAPI_API_KEY."""
    if GoogleSearch is None:
        logger.error("SerpAPI GoogleSearch import unavailable.")
        return []
    if not SERPAPI_KEY:
        logger.warning("SERPAPI_API_KEY not set — Tier 3 skipped.")
        return []

    query_parts = [school, "official site", "scholarship", "funding"]
    if country:
        query_parts.append(country)
    query = " ".join(query_parts)

    try:
        search = GoogleSearch({"q": query, "api_key": SERPAPI_KEY, "num": num})
        results = search.get_dict()
    except Exception as e:
        logger.error(f"SerpAPI search failed for '{school}': {e}")
        return []

    raw_urls = [r["link"] for r in results.get("organic_results", []) if "link" in r]
    school_tokens = _school_tokens(school)
    return _dedupe_domains(raw_urls, school_tokens, num)


# ── Main tool ─────────────────────────────────────────────────────────────────


@function_tool
async def find_university_domain(school: str, country: Optional[str] = None) -> List[str]:
    """
    Find official university domains using a tiered discovery strategy:

    1. Curated DB lookup (instant, free)
    2. DuckDuckGo search (free, no API key)
    3. SerpAPI (paid, only when both above fail)

    Results are persisted in the shared cache (SQLite/Redis).
    """
    num = 5
    school_clean = (school or "").strip()
    if len(school_clean) < 2:
        logger.info("Skipping domain lookup for empty/invalid school name")
        return []

    cache_key = f"domain:{school_clean.lower()}|{(country or '').strip().lower()}"
    cache = get_cache()

    # ── Check persistent cache ────────────────────────────────────────────────
    cached = await cache.get(cache_key)
    if cached is not None:
        logger.info(f"[Cache] Domain hit for '{school_clean}': {cached}")
        return cached[:num]

    logger.info(f"[Domain] Looking up '{school_clean}' (country={country})")

    # ── Tier 1: Curated DB ────────────────────────────────────────────────────
    db_results = lookup_university(school_clean, country)
    if db_results:
        logger.info(f"[Tier 1 DB] Found for '{school_clean}': {db_results}")
        await cache.set(cache_key, db_results[:num], DOMAIN_TTL_SECONDS)
        return db_results[:num]

    # ── Tier 2: DuckDuckGo ────────────────────────────────────────────────────
    ddg_results = await _duckduckgo_search(school_clean, country, num)
    if ddg_results:
        logger.info(f"[Tier 2 DDG] Found for '{school_clean}': {ddg_results}")
        await cache.set(cache_key, ddg_results, DOMAIN_TTL_SECONDS)
        return ddg_results

    logger.info(f"[Tier 2 DDG] No results for '{school_clean}', falling back to SerpAPI")

    # ── Tier 3: SerpAPI ───────────────────────────────────────────────────────
    serp_results = _serpapi_search(school_clean, country, num)
    if serp_results:
        logger.info(f"[Tier 3 SerpAPI] Found for '{school_clean}': {serp_results}")
    else:
        logger.warning(f"[Domain] All tiers exhausted for '{school_clean}', returning empty")

    await cache.set(cache_key, serp_results, DOMAIN_TTL_SECONDS)
    return serp_results

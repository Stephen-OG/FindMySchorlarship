"""
Core BFS crawl engine for a single university domain.

_crawl_university_funding_impl() is the only public function here.
It is called by the tool layer (tools.py) and should not be imported elsewhere.
"""

import asyncio
from typing import Any, Dict, List, Optional, Set

import aiohttp
from bs4 import BeautifulSoup

from utils.cache import CRAWL_TTL_SECONDS, get_cache
from utils.crawl._utils import (
    get_domain_scope,
    normalize_query_cache_key,
    normalize_url,
    sanitize_page_payload,
    sanitize_text_for_llm,
)
from utils.crawl.fetcher import (
    _PLAYWRIGHT_CONCURRENCY,
    create_playwright_browser,
    extract_links,
    fetch,
    pop_next_batch,
    search_fallback_urls,
)
from utils.crawl.models import CrawlQueueItem, QueryConstraints
from utils.crawl.scorer import (
    build_query_constraints,
    create_dynamic_keyword_pattern,
    explain_page_filter_decision,
    is_funding_relevant,
    score_link_priority,
)
from utils.crawl.seeds import build_multi_domain_queue
from utils.keyword_extractor import extract_query_keywords
from utils.logger import logger


def _build_page_payload(
    url: str,
    title: str,
    text: str,
    relevance_score: int,
    score_breakdown,
    crawl_source: str,
    page_type: str = "funding_page",
) -> Dict[str, Any]:
    return sanitize_page_payload(
        {
            "url": url,
            "title": title or "No title",
            "preview": text[:700],
            "text": text[:700],
            "full_text": text[:2000],
            "page_type": page_type,
            "relevance_score": relevance_score,
            "score_breakdown": score_breakdown.model_dump(),
            "crawl_source": crawl_source,
        }
    )


def _select_final_candidates(
    results: List[Dict[str, Any]],
    query_constraints: QueryConstraints,
    min_relevance_score: int,
    max_results: int,
) -> tuple[List[Dict[str, Any]], List[tuple[str, str]]]:
    prefiltered = [r for r in results if r.get("relevance_score", 0) >= min_relevance_score]

    filtered: List[Dict[str, Any]] = []
    dropped: List[tuple[str, str]] = []
    for result in prefiltered:
        keep, reason = explain_page_filter_decision(
            str(result.get("url", "")),
            str(result.get("full_text", "") or result.get("text", "")),
            query_constraints,
        )
        if keep:
            filtered.append(result)
        else:
            dropped.append((str(result.get("url", "")), reason))

    deduped: Dict[str, Dict[str, Any]] = {}
    for result in filtered:
        norm_url = normalize_url(str(result.get("url", "")))
        existing = deduped.get(norm_url)
        if existing is None or result.get("relevance_score", 0) > existing.get(
            "relevance_score", 0
        ):
            deduped[norm_url] = result

    final = sorted(deduped.values(), key=lambda x: x.get("relevance_score", 0), reverse=True)
    return final[:max_results], dropped


async def crawl_university(
    domain_url: str,
    user_query: Optional[str] = None,
    precomputed_keywords: Optional[List[str]] = None,
    extra_seed_domains: Optional[List[str]] = None,
    max_pages: int = 100,
    max_results: int = 40,
    min_relevance_score: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    BFS-crawl a single university domain for funding pages.

    Returns:
        {"funding_pages": [...], "candidate_pages": [...]}

    Results are persisted to the shared cache keyed by (domain_scope, query).
    Subsequent calls for the same domain+query return instantly from cache.
    """
    domain_scope = get_domain_scope(domain_url)
    _cache = get_cache()

    # ── Keyword setup (must run before cache key, for a stable key) ────────────
    custom_keywords = list(precomputed_keywords or [])
    if not custom_keywords and user_query:
        kw = await extract_query_keywords(user_query)
        custom_keywords = kw.all_keywords
    if custom_keywords:
        logger.info("Keywords for %s: %s", domain_url, ", ".join(custom_keywords))

    # Key on (domain, sorted_keywords) so semantically equivalent queries share
    # the same cache entry regardless of how the user phrased the original query.
    cache_key = f"crawl:{domain_scope}:{normalize_query_cache_key(user_query, custom_keywords)}"

    # ── Cache hit ──────────────────────────────────────────────────────────────
    cached = await _cache.get(cache_key)
    if cached is not None:
        logger.info("♻️  Cache hit for %s (scope %s)", domain_url, domain_scope)
        return {
            "funding_pages": [sanitize_page_payload(p) for p in cached.get("funding_pages", [])],
            "candidate_pages": [
                sanitize_page_payload(p) for p in cached.get("candidate_pages", [])
            ],
        }

    keyword_pattern = create_dynamic_keyword_pattern(custom_keywords)
    query_constraints = build_query_constraints(user_query, custom_keywords)
    domain_url = normalize_url(domain_url)

    # ── Build initial queue ────────────────────────────────────────────────────
    to_visit, to_visit_set, required_seed_visits = build_multi_domain_queue(
        domain_url, query_constraints, extra_seed_domains or [], max_pages
    )
    logger.info(
        "Seeded %d target(s) for %s (seed quota: %d)",
        len(to_visit),
        domain_url,
        required_seed_visits,
    )

    visited: Set[str] = set()
    results: List[Dict[str, Any]] = []
    fetched_page_results: List[Dict[str, Any]] = []
    fetched_seed_pages = fetched_discovered_pages = fetched_root_pages = 0

    # ── BFS loop ───────────────────────────────────────────────────────────────
    browser, pw_handle = await create_playwright_browser()
    pw_semaphore = asyncio.Semaphore(_PLAYWRIGHT_CONCURRENCY)
    try:
        async with aiohttp.ClientSession() as session:
            while to_visit and len(visited) < max_pages:
                batch = pop_next_batch(
                    to_visit,
                    visited,
                    to_visit_set,
                    batch_size=12,
                    required_seed_visits_remaining=max(0, required_seed_visits),
                )
                if not batch:
                    break
                required_seed_visits = max(
                    0, required_seed_visits - sum(1 for item in batch if item.source == "seed")
                )

                pages = await asyncio.gather(
                    *[fetch(session, item.url, browser=browser, pw_semaphore=pw_semaphore)
                      for item in batch]
                )

                for queue_item, html in zip(batch, pages):
                    url = queue_item.url
                    if not html:
                        continue

                    if queue_item.source == "seed":
                        fetched_seed_pages += 1
                    elif queue_item.source == "root":
                        fetched_root_pages += 1
                    else:
                        fetched_discovered_pages += 1

                    soup = BeautifulSoup(html, "lxml")
                    for el in soup(["script", "style", "nav", "footer", "header"]):
                        el.decompose()
                    text = sanitize_text_for_llm(soup.get_text(separator="\n", strip=True))

                    score_breakdown = is_funding_relevant(
                        url, text, keyword_pattern, custom_keywords, query_constraints
                    )
                    page_payload = _build_page_payload(
                        url=url,
                        title=soup.title.string if soup.title else "No title",
                        text=text,
                        relevance_score=score_breakdown.score,
                        score_breakdown=score_breakdown,
                        crawl_source=queue_item.source,
                        page_type="funding_page" if score_breakdown.is_relevant else "crawled_page",
                    )
                    fetched_page_results.append(page_payload)
                    if score_breakdown.is_relevant:
                        results.append(page_payload)

                    # Enqueue new links
                    source_boost = (
                        60
                        if score_breakdown.is_relevant
                        else (
                            25
                            if (
                                score_breakdown.text_matches_funding
                                or score_breakdown.has_doctoral_terms
                            )
                            else 0
                        )
                    )
                    for link in extract_links(html, url):
                        if link not in visited and link not in to_visit_set:
                            priority = (
                                score_link_priority(link, custom_keywords, query_constraints)
                                + source_boost
                            )
                            to_visit.append(CrawlQueueItem(url=link, priority=priority, source=url))
                            to_visit_set.add(link)

            # ── Fallback search ────────────────────────────────────────────────────
            avg_score = (
                sum(p.get("relevance_score", 0) for p in fetched_page_results)
                / len(fetched_page_results)
                if fetched_page_results
                else 0
            )
            weak_crawl = len(fetched_page_results) < 3 and avg_score < 20
            if not fetched_page_results or weak_crawl:
                logger.info(
                    "Triggering fallback for %s (pages=%d avg_score=%.1f weak=%s)",
                    domain_url,
                    len(fetched_page_results),
                    avg_score,
                    weak_crawl,
                )
                fallback_entries = search_fallback_urls(domain_url, user_query, query_constraints)
                if fallback_entries:
                    fallback_pages = await asyncio.gather(
                        *[fetch(session, e.get("url", ""), browser=browser, pw_semaphore=pw_semaphore)
                          for e in fallback_entries]
                    )
                    for entry, html in zip(fallback_entries, fallback_pages):
                        url = entry.get("url", "")
                        title = entry.get("title", "") or "No title"
                        if html:
                            soup = BeautifulSoup(html, "lxml")
                            for el in soup(["script", "style", "nav", "footer", "header"]):
                                el.decompose()
                            text = sanitize_text_for_llm(soup.get_text(separator="\n", strip=True))
                        else:
                            text = sanitize_text_for_llm(f"{title}\n{entry.get('snippet', '')}")
                            if not text:
                                continue

                        score_breakdown = is_funding_relevant(
                            url, text, keyword_pattern, custom_keywords, query_constraints
                        )
                        fb_payload = _build_page_payload(
                            url=url,
                            title=title,
                            text=text,
                            relevance_score=score_breakdown.score,
                            score_breakdown=score_breakdown,
                            crawl_source="search_fallback",
                            page_type="funding_page"
                            if score_breakdown.is_relevant
                            else "search_result",
                        )
                        fetched_page_results.append(fb_payload)
                        if score_breakdown.is_relevant:
                            results.append(fb_payload)

    finally:
        if browser is not None:
            await browser.close()
        if pw_handle is not None:
            await pw_handle.stop()

    # ── Final selection ────────────────────────────────────────────────────────
    final_results, dropped = _select_final_candidates(
        results, query_constraints, min_relevance_score, max_results
    )
    if dropped:
        logger.info(
            "%s dropped %d page(s): %s",
            domain_url,
            len(dropped),
            [f"{u} ({r})" for u, r in dropped[:10]],
        )

    t100 = sum(1 for r in final_results if r.get("relevance_score", 0) >= 100)
    t50 = sum(1 for r in final_results if 50 <= r.get("relevance_score", 0) < 100)
    t5 = sum(1 for r in final_results if 5 <= r.get("relevance_score", 0) < 50)
    logger.info(
        "%s: %d pages | Exceptional(100+):%d High(50-99):%d Moderate(5-49):%d | "
        "visited=%d fetched=%d seed=%d root=%d disc=%d queue=%d",
        domain_url,
        len(final_results),
        t100,
        t50,
        t5,
        len(visited),
        len(fetched_page_results),
        fetched_seed_pages,
        fetched_root_pages,
        fetched_discovered_pages,
        len(to_visit),
    )

    # Dedupe candidates
    cand_map: Dict[str, Dict[str, Any]] = {}
    for page in fetched_page_results:
        norm = normalize_url(str(page.get("url", "")))
        existing = cand_map.get(norm)
        if existing is None or int(page.get("relevance_score", 0) or 0) >= int(
            existing.get("relevance_score", 0) or 0
        ):
            cand_map[norm] = sanitize_page_payload(page)

    candidate_pages = sorted(
        cand_map.values(),
        key=lambda p: (
            1 if str(p.get("crawl_source", "")) == "seed" else 0,
            int(p.get("relevance_score", 0) or 0),
        ),
        reverse=True,
    )[:max_results]

    access_blocked = len(fetched_page_results) == 0
    if access_blocked:
        logger.warning(
            "⚠️  %s: no pages fetched after crawl + fallback — "
            "site likely blocks automated access (Cloudflare or similar)",
            domain_url,
        )

    payload = {
        "funding_pages": [sanitize_page_payload(p) for p in final_results],
        "candidate_pages": list(candidate_pages),
        "access_blocked": access_blocked,
    }
    # Don't cache blocked results for the full TTL — the ScraperAPI key may not
    # be set yet, or the site may become accessible after a short period.
    # Retry after 10 minutes instead of serving stale empty results for 7 days.
    ttl = 600 if access_blocked else CRAWL_TTL_SECONDS
    await _cache.set(cache_key, payload, ttl)
    return payload


async def get_cached_crawl_payload(
    domain_url: str,
    user_query: Optional[str] = None,
    keywords: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Return sanitized cached crawl results, or empty dicts if no cache entry.

    Pass `keywords` (from extract_query_keywords) to use the same stable key
    that crawl_university() wrote. Falling back to raw query is kept for
    callers that don't have keywords available.
    """
    domain_scope = get_domain_scope(domain_url)
    # Resolve keywords if not provided, so the key matches what crawl_university stored
    resolved_keywords = list(keywords or [])
    if not resolved_keywords and user_query:
        kw = await extract_query_keywords(user_query)
        resolved_keywords = kw.all_keywords
    cache_key = f"crawl:{domain_scope}:{normalize_query_cache_key(user_query, resolved_keywords)}"
    cached = await get_cache().get(cache_key) or {}
    return {
        "funding_pages": [sanitize_page_payload(p) for p in cached.get("funding_pages", [])],
        "candidate_pages": [sanitize_page_payload(p) for p in cached.get("candidate_pages", [])],
    }

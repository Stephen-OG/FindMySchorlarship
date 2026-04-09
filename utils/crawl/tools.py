"""
@function_tool entry points for the crawl package.

These are the only symbols the agent orchestrator imports.
Everything else in utils/crawl/ is internal.
"""

import asyncio
from typing import Any, Dict, List, Optional

from agents import function_tool
from pydantic import BaseModel

from utils.crawl._utils import get_domain_scope
from utils.crawl.engine import crawl_university
from utils.crawl.formatter import (
    _coerce_university_input,
    _format_university_result,
    build_crawler_result,
)
from utils.keyword_extractor import extract_query_keywords
from utils.logger import logger


class UniversityInput(BaseModel):
    """Flexible input schema — handles multiple agent output shapes."""

    school: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    domain_url: Optional[str] = None
    url: Optional[str] = None
    domains: Optional[List[str]] = None


@function_tool
async def crawl_universities_formatted(
    universities: Optional[List[UniversityInput]] = None,
    schools: Optional[List[UniversityInput]] = None,
    domains: Optional[List[str]] = None,
    user_query: Optional[str] = None,
    extracted_keywords: Optional[List[str]] = None,
    max_pages: int = 40,
    max_results_per_university: int = 40,
    min_relevance_score: int = 5,
) -> Dict[str, Any]:
    """
    Crawl multiple universities and return a fully formatted CrawlerResult object.

    Expected university item shape:
    - {"school": "...", "domain": "https://example.edu"}
    OR search-agent style:
    - {"school": "...", "domains": ["https://example.edu", ...]}

    Args:
        extracted_keywords: Pre-extracted keywords from the orchestrator.
                            If provided, skips redundant keyword extraction.
                            If None, extracts from user_query (backward compatible).
    """
    try:
        seen_domains: set = set()
        normalized_universities: List[Dict[str, Any]] = []
        effective_max_pages = max(10, min(max_pages, 40))
        effective_max_results = max(1, min(max_results_per_university, effective_max_pages))

        for raw in universities or schools or []:
            raw_dict = raw.model_dump() if isinstance(raw, UniversityInput) else raw
            if not isinstance(raw_dict, dict):
                continue
            normalized = _coerce_university_input(raw_dict)
            if not normalized:
                continue
            dedupe_key = get_domain_scope(normalized["domain"])
            if dedupe_key in seen_domains:
                continue
            seen_domains.add(dedupe_key)
            normalized_universities.append(normalized)

        if extracted_keywords is not None:
            logger.info("Keywords from orchestrator: %s", extracted_keywords)
        else:
            kw = await extract_query_keywords(user_query) if user_query else None
            extracted_keywords = kw.all_keywords if kw else []
            logger.info("Keywords extracted locally: %s", extracted_keywords)

        all_scores: List[int] = []

        async def crawl_one(uni: Dict[str, Any]) -> Dict[str, Any]:
            try:
                result = await asyncio.wait_for(
                    crawl_university(
                        domain_url=uni["domain"],
                        user_query=user_query,
                        precomputed_keywords=extracted_keywords,
                        extra_seed_domains=uni.get("auxiliary_domains", []),
                        max_pages=effective_max_pages,
                        max_results=effective_max_results,
                        min_relevance_score=min_relevance_score,
                    ),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                logger.warning("Crawl timed out for %s", uni["domain"])
                result = {"funding_pages": [], "candidate_pages": [], "crawl_timed_out": True}
            except Exception as exc:
                logger.exception("Crawl failed for %s (%s): %s", uni["school"], uni["domain"], exc)
                result = {"funding_pages": [], "candidate_pages": []}

            formatted = _format_university_result(uni, result)
            all_scores.extend(
                p.get("relevance_score", 0) for p in formatted.get("funding_pages", [])
            )
            return formatted

        crawler_universities = list(
            await asyncio.gather(*[crawl_one(uni) for uni in normalized_universities])
        )

        return build_crawler_result(
            crawler_universities, extracted_keywords, user_query, all_scores
        )

    except Exception as exc:
        logger.exception("crawl_universities_formatted failed: %s", exc)
        return {
            "universities": [],
            "search_strategy": "query-guided crawl with keyword extraction"
            if user_query
            else "domain crawl",
            "total_funding_pages": 0,
            "keyword_analysis": {
                "user_query": user_query or "",
                "keywords": [],
                "keyword_count": 0,
            },
            "relevance_tiers": {"exceptional": 0, "high": 0, "moderate": 0},
        }


@function_tool
async def crawl_university_funding(
    domain_url: str,
    user_query: Optional[str] = None,
    max_pages: int = 100,
    max_results: int = 40,
    min_relevance_score: int = 5,
) -> List[Dict[str, Any]]:
    """Single-university crawl tool (legacy wrapper)."""
    result = await crawl_university(
        domain_url=domain_url,
        user_query=user_query,
        max_pages=max_pages,
        max_results=max_results,
        min_relevance_score=min_relevance_score,
    )
    return result.get("funding_pages", [])

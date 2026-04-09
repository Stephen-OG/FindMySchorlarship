"""
Unified keyword extraction module for FindMyScholarship AI

Extracts query keywords ONCE and caches them for reuse by crawler and analyzer,
reducing redundant LLM calls from 2-3 to 1 (25-33% cost savings).
"""

import json
from typing import List

from openai import AsyncOpenAI
from pydantic import BaseModel

from utils.cache import KEYWORD_TTL_SECONDS, get_cache
from utils.logger import logger

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


class QueryKeywords(BaseModel):
    """Structured keywords extracted from user query"""

    degree_terms: List[str]  # ["phd", "doctoral", "masters", "undergraduate"]
    subject_terms: List[str]  # ["machine-learning", "computer-science", "biology"]
    generic_terms: List[str]  # ["international", "uk", "europe"]
    funding_preference_terms: List[str]  # ["full-funding", "stipend", "tuition-waiver"]
    all_keywords: List[str]  # Flattened list of all keywords


async def extract_query_keywords(query: str) -> QueryKeywords:
    """
    Extract academic keywords from user query using a single LLM call.
    Results are persisted in the shared cache (SQLite/Redis) keyed by query.

    This replaces separate keyword extractions in crawler and analyzer with
    ONE centralized extraction, reducing API calls and enabling keyword reuse.

    Args:
        query: User's scholarship search query

    Returns:
        QueryKeywords object with categorized and flattened keywords

    Example:
        keywords = await extract_query_keywords("PhD funding in machine learning at MIT")
        # Returns:
        # degree_terms: ["phd", "doctoral"]
        # subject_terms: ["machine-learning", "machine learning", "cs"]
        # generic_terms: []
        # funding_preference_terms: ["full-funding"]
        # all_keywords: ["phd", "doctoral", "machine-learning", "machine learning", ...]
    """
    cache_key = f"keywords:{query.lower().strip()}"
    cache = get_cache()
    cached = await cache.get(cache_key)
    if cached is not None:
        logger.debug(f"[Cache] Keyword hit for query: {query[:60]}")
        return QueryKeywords(**cached)

    try:
        response = await _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract academic keywords from queries for scholarship searching. "
                        "Return JSON with degree_terms, subject_terms, generic_terms, and funding_preference_terms."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""Query: {query}

Extract keywords and categorize them:

1. DEGREE TERMS (phd, doctoral, masters, undergraduate, bachelors, etc.)
2. SUBJECT TERMS (fields of study: machine-learning, computer-science, social-policy, etc.)
3. GENERIC TERMS (international, uk, european, domestic, etc.)
4. FUNDING PREFERENCE TERMS (full-funding, tuition-waiver, stipend, full-scholarship, etc.)

Be comprehensive - include ALL relevant academic terms and variations.

Return format:
{{
  "degree_terms": ["term1", "term2", ...],
  "subject_terms": ["term1", "term2", ...],
  "generic_terms": ["term1", "term2", ...],
  "funding_preference_terms": ["term1", "term2", ...]
}}""",
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        parsed = json.loads(response.choices[0].message.content)

        degree_terms = parsed.get("degree_terms", [])
        subject_terms = parsed.get("subject_terms", [])
        generic_terms = parsed.get("generic_terms", [])
        funding_preference_terms = parsed.get("funding_preference_terms", [])

    except Exception as e:
        logger.warning(f"⚠️ Keyword extraction failed: {e}, using fallback")
        degree_terms = []
        subject_terms = []
        generic_terms = []
        funding_preference_terms = []

    # Fallback: regex-based extraction if LLM fails
    query_lower = query.lower()

    # Degree fallback
    if not degree_terms:
        if any(word in query_lower for word in ["phd", "doctoral", "doctorate"]):
            degree_terms.extend(["phd", "doctoral", "doctorate"])
        if any(word in query_lower for word in ["master", "master's", "msc", "m.sc"]):
            degree_terms.extend(["masters", "master", "msc"])
        if any(word in query_lower for word in ["undergraduate", "bachelor", "bachelors"]):
            degree_terms.extend(["undergraduate", "bachelor"])

    # Generic fallback
    if not generic_terms:
        if "international" in query_lower:
            generic_terms.append("international")
        if any(word in query_lower for word in ["europe", "european", "eu"]):
            generic_terms.extend(["europe", "european"])

    # Deduplicate and normalize
    all_keywords = list(
        set(
            k.lower().strip()
            for k in degree_terms + subject_terms + generic_terms + funding_preference_terms
            if k
        )
    )

    logger.info(
        f"🔍 Extracted keywords | degree: {degree_terms} | subject: {subject_terms} | generic: {generic_terms} | funding_pref: {funding_preference_terms}"
    )

    result = QueryKeywords(
        degree_terms=degree_terms,
        subject_terms=subject_terms,
        generic_terms=generic_terms,
        funding_preference_terms=funding_preference_terms,
        all_keywords=all_keywords,
    )
    await cache.set(cache_key, result.model_dump(), KEYWORD_TTL_SECONDS)
    return result

"""
Relevance scoring for crawled pages.

Determines whether a page is funding-relevant and assigns a numeric score
used for ranking and filtering. All functions are pure (no I/O).
"""

import re
from typing import List, Optional

from utils.crawl._constants import (
    ACADEMIC_HUB_PATH_HINTS,
    BASE_FUNDING_KEYWORDS,
    DOCTORAL_PATH_HINTS,
    FUNDING_PATH_HINTS,
    FUNDING_PREFERENCE_TERMS,
    FUNDING_URL_PATTERNS,
    GENERIC_QUERY_TERMS,
    INSTITUTION_HINT_TERMS,
)
from utils.crawl._utils import (
    keyword_variants_for_matching,
    normalized_match_text,
)
from utils.crawl.models import PageScoreBreakdown, QueryConstraints

# ── Pattern factory ────────────────────────────────────────────────────────────


def create_dynamic_keyword_pattern(custom_keywords: List[str]) -> re.Pattern:
    """
    Build a single compiled regex from base funding keywords + caller-supplied keywords.
    Compile ONCE per crawl and reuse — never call this per-page.
    """
    keyword_variants: List[str] = []
    for kw in custom_keywords:
        keyword_variants.extend(re.escape(v) for v in keyword_variants_for_matching(kw))
    all_keywords = BASE_FUNDING_KEYWORDS + keyword_variants
    return re.compile(r"(" + r"|".join(all_keywords) + r")", re.I)


# ── Query constraints ──────────────────────────────────────────────────────────


def is_institution_keyword(keyword: str) -> bool:
    normalized = normalized_match_text(keyword)
    if not normalized:
        return True
    return any(term in normalized.split() for term in INSTITUTION_HINT_TERMS)


def build_query_constraints(query: Optional[str], custom_keywords: List[str]) -> QueryConstraints:
    query_text = normalized_match_text(query or "")

    generic_terms = sorted(
        {term for term in custom_keywords if normalized_match_text(term) in GENERIC_QUERY_TERMS}
    )
    funding_preference_terms = sorted(
        {
            normalized_match_text(term)
            for term in custom_keywords
            if normalized_match_text(term) in FUNDING_PREFERENCE_TERMS
        }
    )

    degree_terms: List[str] = []
    if any(t in query_text for t in ["phd", "doctoral", "doctorate", "doctor of philosophy"]):
        degree_terms.extend(["phd", "doctoral", "doctorate"])
    if any(t in query_text for t in ["master", "masters", "m.sc", "msc"]):
        degree_terms.extend(["masters", "master"])
    if any(t in query_text for t in ["undergraduate", "bachelor"]):
        degree_terms.extend(["undergraduate", "bachelor"])

    subject_terms: List[str] = []
    for keyword in custom_keywords:
        normalized = normalized_match_text(keyword)
        if (
            not normalized
            or normalized in GENERIC_QUERY_TERMS
            or normalized in FUNDING_PREFERENCE_TERMS
            or is_institution_keyword(normalized)
        ):
            continue
        subject_terms.append(normalized)

    if not subject_terms and query_text:
        for chunk in re.split(r"\bor\b|\band\b|,", query_text):
            chunk = chunk.strip()
            if not chunk or is_institution_keyword(chunk):
                continue
            if any(d in chunk for d in ["phd", "doctoral", "doctorate", "master", "undergraduate"]):
                continue
            if any(
                t in chunk
                for t in ["artificial intelligence", "machine learning", "computer science"]
            ):
                if chunk not in {"uk", "usa", "us"}:
                    subject_terms.append(chunk)

    return QueryConstraints(
        degree_terms=sorted(set(degree_terms)),
        subject_terms=sorted(set(subject_terms), key=len, reverse=True),
        generic_terms=generic_terms,
        funding_preference_terms=funding_preference_terms,
    )


# ── Page-level filtering ───────────────────────────────────────────────────────


def should_keep_page_for_query(url: str, text: str, constraints: QueryConstraints) -> bool:
    if not constraints.degree_terms and not constraints.subject_terms:
        return True
    match_text = normalized_match_text(f"{url} {text}")
    has_subject = any(t in match_text for t in constraints.subject_terms)
    has_doctoral = any(
        t in match_text
        for t in ["phd", "doctoral", "doctorate", "doctoral college", "research degree"]
    )
    has_research_hub = any(
        t in match_text
        for t in [
            "research funding",
            "postgraduate research",
            "doctoral funding",
            "studentship",
            "studentships",
            "doctoral college",
        ]
    )
    has_taught_only = any(
        t in match_text for t in ["postgraduate taught", "masters", "master's", "undergraduate"]
    )

    if any(t in constraints.degree_terms for t in ["phd", "doctoral", "doctorate"]):
        if has_subject and has_doctoral:
            return True
        if has_subject and not has_taught_only:
            return True
        if has_doctoral and has_research_hub:
            return True
        if has_research_hub and not has_taught_only:
            return True
        if has_doctoral and not constraints.subject_terms:
            return True
        return False

    if constraints.subject_terms and not has_subject:
        return False
    return True


def explain_page_filter_decision(
    url: str, text: str, constraints: QueryConstraints
) -> tuple[bool, str]:
    if not constraints.degree_terms and not constraints.subject_terms:
        return True, "no specific degree/subject constraints"

    match_text = normalized_match_text(f"{url} {text}")
    has_subject = any(t in match_text for t in constraints.subject_terms)
    has_doctoral = any(
        t in match_text
        for t in ["phd", "doctoral", "doctorate", "doctoral college", "research degree"]
    )
    has_research_hub = any(
        t in match_text
        for t in [
            "research funding",
            "postgraduate research",
            "doctoral funding",
            "studentship",
            "studentships",
            "doctoral college",
        ]
    )
    has_taught_only = any(
        t in match_text for t in ["postgraduate taught", "masters", "master's", "undergraduate"]
    )

    if any(t in constraints.degree_terms for t in ["phd", "doctoral", "doctorate"]):
        if has_subject and has_doctoral:
            return True, "subject + doctoral match"
        if has_subject and not has_taught_only:
            return True, "subject match on a non-taught page"
        if has_doctoral and has_research_hub:
            return True, "doctoral research funding hub"
        if has_research_hub and not has_taught_only:
            return True, "research funding hub"
        if has_taught_only:
            return False, "taught-only or undergraduate page"
        if constraints.subject_terms and not has_subject:
            return False, "missing requested subject terms"
        if not has_doctoral and not has_research_hub:
            return False, "missing doctoral or research funding signals"
        return False, "did not satisfy PhD query filter"

    if constraints.subject_terms and not has_subject:
        return False, "missing requested subject terms"
    return True, "passed general query filter"


# ── Page relevance score ───────────────────────────────────────────────────────


def score_link_priority(
    link: str,
    custom_keywords: List[str],
    constraints: Optional[QueryConstraints] = None,
) -> int:
    priority = 0
    if any(p in link.lower() for p in FUNDING_URL_PATTERNS):
        priority += 100
    priority += calculate_funding_depth(link) * 20

    normalized_link = normalized_match_text(link)
    for kw in custom_keywords:
        if any(v in normalized_link for v in keyword_variants_for_matching(kw)):
            priority += 10

    if constraints:
        if any(t in constraints.degree_terms for t in ["phd", "doctoral", "doctorate"]):
            if any(t in normalized_link for t in DOCTORAL_PATH_HINTS):
                priority += 80
            if any(t in normalized_link for t in FUNDING_PATH_HINTS):
                priority += 60
            if any(t in normalized_link for t in ACADEMIC_HUB_PATH_HINTS):
                priority += 30
        if any(t in normalized_link for t in constraints.subject_terms):
            priority += 40
    return priority


def calculate_funding_depth(url: str) -> int:
    url_lower = url.lower()
    return sum(
        url_lower.count(t)
        for t in [
            "funding",
            "scholarship",
            "phd",
            "doctoral",
            "studentship",
            "financial",
            "bursary",
        ]
    )


def is_funding_relevant(
    url: str,
    text: str,
    keyword_pattern: re.Pattern,
    custom_keywords: List[str],
    constraints: QueryConstraints,
) -> PageScoreBreakdown:
    """Score a page for funding relevance. Returns structured breakdown."""
    url_suggests_funding = any(p in url.lower() for p in FUNDING_URL_PATTERNS)
    normalized_url = normalized_match_text(url)
    normalized_text = normalized_match_text(text)

    url_has_custom = any(
        v in normalized_url for kw in custom_keywords for v in keyword_variants_for_matching(kw)
    )
    text_matches_funding = bool(keyword_pattern.search(text))

    score = 0
    if url_suggests_funding:
        score += 10
    if url_has_custom:
        score += 5
    if text_matches_funding:
        score += 3

    subject_match_count = 0
    for kw in custom_keywords:
        variants = keyword_variants_for_matching(kw)
        hits = max(normalized_text.count(v) for v in variants) if variants else 0
        if normalized_match_text(kw) in constraints.subject_terms:
            if hits:
                subject_match_count += 1
            score += hits * 12
        else:
            score += hits * 2

    if subject_match_count:
        score += subject_match_count * 25

    if any(t in constraints.degree_terms for t in ["phd", "doctoral", "doctorate"]):
        if any(
            t in normalized_text
            for t in ["phd", "doctoral", "doctorate", "doctoral college", "research degree"]
        ):
            score += 30
        if any(
            t in normalized_text
            for t in ["postgraduate taught", "undergraduate", "master's", "masters"]
        ):
            score -= 35

    if constraints.subject_terms and not any(
        t in normalized_text for t in constraints.subject_terms
    ):
        score -= 20
    if any(t in normalized_url for t in ["undergraduate", "postgraduatetaught"]):
        score -= 20

    has_doctoral_terms = any(
        t in normalized_text
        for t in ["phd", "doctoral", "doctorate", "doctoral college", "research degree"]
    )
    has_taught_only_terms = any(
        t in normalized_text
        for t in ["postgraduate taught", "undergraduate", "master's", "masters"]
    )

    return PageScoreBreakdown(
        score=score,
        is_relevant=score >= 3,
        url_suggests_funding=url_suggests_funding,
        url_has_custom=url_has_custom,
        text_matches_funding=text_matches_funding,
        subject_match_count=subject_match_count,
        has_doctoral_terms=has_doctoral_terms,
        has_taught_only_terms=has_taught_only_terms,
    )

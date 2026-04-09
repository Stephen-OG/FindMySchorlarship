"""Pydantic data models shared across the crawl package."""

from typing import List, Optional

from pydantic import BaseModel


class UniversityInput(BaseModel):
    """Flexible input schema for crawler batch calls (handles multiple agent output shapes)."""

    school: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    domain_url: Optional[str] = None
    url: Optional[str] = None
    domains: Optional[List[str]] = None


class QueryConstraints(BaseModel):
    degree_terms: List[str]
    subject_terms: List[str]
    generic_terms: List[str]
    funding_preference_terms: List[str]


class CrawlQueueItem(BaseModel):
    url: str
    priority: int
    source: str


class PageScoreBreakdown(BaseModel):
    score: int
    is_relevant: bool
    url_suggests_funding: bool
    url_has_custom: bool
    text_matches_funding: bool
    subject_match_count: int
    has_doctoral_terms: bool
    has_taught_only_terms: bool

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class FundingPage(BaseModel):
    url: str
    "URL of the funding page"
    title: str
    "Title of the funding page"
    preview: str
    "Summary/preview of the page content"
    relevance_score: Optional[int] = None
    "Relevance score (higher = more relevant to query)"
    text: Optional[str] = None
    "Backward-compatible preview alias used by older analyzer callers"
    full_text: Optional[str] = None
    "Cleaned full page content used for downstream analysis"
    page_type: Optional[str] = None
    "Page classification label (e.g., funding_page)"
    crawl_source: Optional[str] = None
    "How the crawler discovered this page (e.g., seed, root, discovered)"


class UniversityResult(BaseModel):
    school: str
    "The name of the school"
    domain: str
    "The school's official domain"
    funding_pages: List[FundingPage]
    "Primary list of crawled pages carried forward for downstream analysis"
    candidate_pages: Optional[List[FundingPage]] = None
    "Broader set of crawled candidate pages before final query-specific filtering"
    filtered_funding_pages: Optional[List[FundingPage]] = None
    "Final query-filtered funding pages retained for high-confidence presentation"
    summary: Optional[str] = None
    "Brief summary of funding opportunities at this university"


class CrawlerResult(BaseModel):
    universities: List[UniversityResult]
    "Universities crawled with their funding pages"
    search_strategy: Optional[str] = None
    "Description of the search strategy used"
    total_funding_pages: Optional[int] = None
    "Total number of funding pages found across all universities"
    keyword_analysis: Optional[Dict[str, Any]] = None
    "Keywords extracted and used for targeted crawling"
    relevance_tiers: Optional[Dict[str, int]] = None
    "Count of pages in each relevance tier: exceptional (100+), high (50-99), moderate (5-49)"

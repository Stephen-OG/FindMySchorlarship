from typing import List, Optional

from pydantic import BaseModel


# Pydantic models for structured output
class FundingOpportunity(BaseModel):
    name: str
    "Name of the scholarship or funding program"
    degree_level: str
    "Degree level (PhD, Masters, etc.)"
    field: Optional[str] = None
    "Academic field or discipline"
    eligibility: str
    "Eligibility requirements"
    amount: str
    "Funding amount or type"
    deadline: Optional[str] = None
    "Application deadline"
    for_international: Optional[bool] = None
    "Whether available to international students"
    application_process: str
    "How to apply"


class AnalyzedFundingPage(BaseModel):
    url: str
    "Page URL"
    title: str
    "Page title"
    opportunities: List[FundingOpportunity]
    "List of funding opportunities found on this page"
    page_summary: str
    "Brief summary of the page"
    relevance_to_query: str
    "Relevance to user's query (High/Medium/Low)"


class UniversityFundingAnalysis(BaseModel):
    university: str
    "University name"
    domain: str
    "University domain"
    analyzed_pages: List[AnalyzedFundingPage]
    "Detailed analysis of each funding page"
    total_opportunities: int
    "Total number of distinct funding opportunities found"
    summary: str
    "Overall summary of funding available at this university"
    best_matches: List[str]
    "Names of the top 3 most relevant opportunities for the user"


class AnalyzerResult(BaseModel):
    universities: List[UniversityFundingAnalysis]
    "Analyzed funding information for each university"
    overall_summary: str
    "Summary across all universities"
    total_opportunities_found: int
    "Total opportunities across all universities"

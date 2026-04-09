import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import aiohttp
from agents import function_tool
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from models.crawler_model import UniversityResult
from utils.crawl import get_cached_crawl_payload
from utils.logger import logger

load_dotenv(override=True)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# Maximum pages to analyze in a single batch (to stay within token limits)
MAX_PAGES_PER_BATCH = 5
MAX_CONCURRENT_ANALYSIS_BATCHES = 3


def sanitize_text_for_llm(value: str) -> str:
    """Remove control characters that can break prompt construction or API requests."""
    cleaned = value or ""
    cleaned = cleaned.replace("\x00", " ")
    cleaned = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


class AnalyzedFundingPagePayload(BaseModel):
    url: str
    title: str
    opportunities: List[Dict[str, Any]]
    page_summary: str
    relevance_to_query: str


class UniversityFundingAnalysisPayload(BaseModel):
    university: str
    domain: str
    analyzed_pages: List[AnalyzedFundingPagePayload]
    total_opportunities: int
    summary: str
    best_matches: List[str]


class AnalyzerResultPayload(BaseModel):
    universities: List[UniversityFundingAnalysisPayload]
    overall_summary: str
    total_opportunities_found: int


async def _analyze_funding_page_impl(
    url: str, title: str, preview: str, user_query: str, full_text: str = None
) -> dict:
    """
    Analyze a funding page to extract structured information.

    CRITICAL: Always provide full_text parameter from crawler results to avoid refetching the URL.
    The crawler already fetches and cleans the page content, so passing full_text is much faster.

    Args:
        url: Page URL
        title: Page title
        preview: Short preview of page content (for display)
        user_query: User's original query for context
        full_text: REQUIRED - Full page text content from crawler results (avoids refetching URL)

    Returns:
        Structured funding information with opportunities, eligibility, amounts, deadlines, etc.
    """

    logger.info(f"📄 Analyzing: {url}")

    # Use provided full_text if available, otherwise fetch (fallback for backward compatibility)
    if full_text is None or full_text == "":
        logger.warning(f"⚠️  No full_text provided for {url}, refetching (inefficient)")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    url, headers={"User-Agent": "FundingScraper/1.0"}, timeout=15
                ) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, "lxml")

                        # Remove scripts and styles
                        for element in soup(["script", "style", "nav", "footer", "header"]):
                            element.decompose()

                        # Get main content
                        full_text = soup.get_text(separator="\n", strip=True)
                        # Limit to reasonable size (10k chars = ~2500 tokens)
                        full_text = sanitize_text_for_llm(full_text[:10000])
                    else:
                        logger.warning(f"⚠️  Failed to fetch {url}, using preview only")
                        full_text = sanitize_text_for_llm(preview)
            except Exception as e:
                logger.error(f"⚠️  Error fetching {url}: {e}, using preview only")
                full_text = sanitize_text_for_llm(preview)
    else:
        # Crawler already provides cleaned text, so use it directly
        # Just ensure it's within token budget (already limited in crawler, but double-check)
        full_text = sanitize_text_for_llm(
            full_text[:10000] if len(full_text) > 10000 else full_text
        )

    analysis_prompt = f"""Analyze this university funding page and extract key information.

USER'S QUERY: {user_query}

PAGE DETAILS:
URL: {url}
Title: {title}

CONTENT:
{full_text}

CRITICAL RULES (STRICT):
1. ONLY extract funding opportunities that are explicitly present in the provided page content.
2. DO NOT invent, infer, summarize, or generalize funding opportunities.
3. DO NOT create generic or placeholder entries such as:
   - "PhD Machine Learning Funding"
   - "Machine Learning Scholarships"
   unless those exact names appear in the page content.

4. If NO relevant funding opportunities exist that match the user's query:
   - Return an empty opportunities array: []
   - In page_summary, clearly state: "No relevant funding opportunities were found."
   - DO NOT fabricate opportunities.
   - DO NOT create placeholder entries.
   - DO NOT summarize unrelated content as funding.

5. Each funding opportunity you extract MUST:
   - Exist explicitly in the provided page content
   - Be directly relevant to the user's query
   - Include factual information only
   - Never include guessed or assumed information

6. If the page contains no funding information:
   - Return opportunities: []
   - State in page_summary: "No funding information was found on this page."

Extract the following if explicitly stated on the page (omit the field entirely if not found — do NOT write "Not specified"):
1. Funding opportunities mentioned (name each one — use exact names from the page)
2. Degree level (PhD, Masters, Undergraduate, etc.)
3. Field/discipline (if specific)
4. Eligibility requirements (only what is explicitly stated)
5. Funding amount or type (exact figures or descriptions from the page — e.g. "£18,000/year stipend", "full tuition waiver")
6. Application deadline (exact dates only — omit if no date is mentioned)
7. Whether it's for international students, UK/EU students, or both
8. How to apply — use exact steps or URL from the page; if none given, use the page URL as the next step

Be specific and extract actual details from the page. If the page lists multiple opportunities, list them all.

DO NOT hallucinate.
DO NOT fabricate funding.
DO NOT write "Not specified", "N/A", or placeholder values — omit the field instead.
ONLY extract real funding opportunities from the input content.

Return as JSON with this structure:
{{
  "opportunities": [
    {{
      "name": "Name of scholarship/funding (exact name from page)",
      "degree_level": "PhD/Masters/etc",
      "field": "Subject area (omit if not stated)",
      "eligibility": "Who can apply (omit if not stated)",
      "amount": "Funding amount/type (omit if not stated)",
      "deadline": "YYYY-MM-DD or human-readable date (omit if not found on page)",
      "for_international": true/false,
      "application_process": "Exact steps or URL to apply (never omit — always provide the source page URL as fallback)"
    }}
  ],
  "page_summary": "Brief 1-2 sentence summary of what this page offers. If no funding found, state that clearly.",
  "relevance_to_query": "How relevant is this to the user's needs (High/Medium/Low)"
}}"""

    response = await _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a funding information extraction expert. Extract ONLY real, explicitly mentioned funding opportunities from scholarship pages. DO NOT invent, infer, or fabricate funding opportunities. Only extract what is explicitly stated in the page content.",
            },
            {"role": "user", "content": analysis_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    return json.loads(response.choices[0].message.content)


async def _analyze_funding_pages_batch_impl(
    urls: List[str],
    titles: List[str],
    previews: List[str],
    full_texts: List[str],
    user_query: str,
    max_pages_per_batch: int = MAX_PAGES_PER_BATCH,
) -> List[Dict[str, Any]]:
    """
    Analyze multiple funding pages in batches to reduce API calls.

    This function processes multiple pages in a single API call, dramatically reducing
    the number of API calls from N (one per page) to N/max_pages_per_batch.

    CRITICAL: Always provide full_text for each page from crawler results to avoid refetching.

    Args:
        urls: List of page URLs
        titles: List of page titles (same order as urls)
        previews: List of short previews (same order as urls)
        full_texts: List of full page texts from crawler (same order as urls)
        user_query: User's original query for context
        max_pages_per_batch: Maximum pages to analyze per API call (default: 5)

    Returns:
        List of analysis results, one per page, each containing:
        {
            "url": str,
            "opportunities": [...],
            "page_summary": str,
            "relevance_to_query": str
        }
    """

    # Build internal page objects from parallel lists
    if not urls:
        logger.warning("No URLs provided to analyze_funding_pages_batch")
        return []

    n = len(urls)
    pages: List[Dict[str, Any]] = []
    for i in range(n):
        url = urls[i]
        title = titles[i] if i < len(titles) else ""
        preview = sanitize_text_for_llm(previews[i] if i < len(previews) else "")
        full_text = sanitize_text_for_llm(full_texts[i] if i < len(full_texts) else "")
        pages.append(
            {
                "url": url,
                "title": sanitize_text_for_llm(title),
                "preview": preview,
                "full_text": full_text,
            }
        )

    logger.info(f"📦 Batch analyzing {len(pages)} pages in batches of {max_pages_per_batch}")

    async def analyze_batch(
        batch: List[Dict[str, Any]], batch_num: int, total_batches: int
    ) -> List[Dict[str, Any]]:
        logger.info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch)} pages)")

        pages_text = []
        for i, page in enumerate(batch, 1):
            url = page.get("url", "Unknown URL")
            title = sanitize_text_for_llm(page.get("title", "No title"))
            full_text = sanitize_text_for_llm(page.get("full_text", ""))

            if not full_text:
                logger.warning(f"⚠️  Page {i} ({url}) missing full_text, skipping from batch")
                continue

            page_text = full_text[:2000] if len(full_text) > 2000 else full_text

            pages_text.append(f"""
--- PAGE {i} ---
URL: {url}
Title: {title}
Content:
{page_text}
""")

        if not pages_text:
            logger.warning("No valid pages in batch (all missing full_text)")
            return []

        batch_prompt = f"""Analyze these university funding pages and extract key information for EACH page.

USER'S QUERY: {user_query}

PAGES TO ANALYZE:
{"".join(pages_text)}

CRITICAL RULES (STRICT):
1. ONLY extract funding opportunities that are explicitly present in the provided page content.
2. DO NOT invent, infer, summarize, or generalize funding opportunities.
3. DO NOT create generic or placeholder entries such as:
   - "PhD Machine Learning Funding"
   - "Machine Learning Scholarships"
   unless those exact names appear in the page content.

4. If NO relevant funding opportunities exist that match the user's query:
   - Return an empty opportunities array: []
   - In page_summary, clearly state: "No relevant funding opportunities were found."
   - DO NOT fabricate opportunities.
   - DO NOT create placeholder entries.
   - DO NOT summarize unrelated content as funding.

5. Each funding opportunity you extract MUST:
   - Exist explicitly in the provided page content
   - Be directly relevant to the user's query
   - Include factual information only
   - Never include guessed or assumed information

6. If a page contains no funding information:
   - Return opportunities: []
   - State in page_summary: "No funding information was found on this page."

For EACH page, extract the following only when explicitly stated (omit the field entirely if not found — do NOT write "Not specified"):
1. Funding opportunities mentioned (name each one — use exact names from the page, e.g. "Graduate Research Assistantship", "Merit Scholarship")
2. Degree level (PhD, Masters, Undergraduate, etc.)
3. Field/discipline (e.g. "Data Science", "Computer Science" — omit if not stated)
4. Eligibility requirements (GPA, citizenship, enrollment status — omit if not stated)
5. Funding amount or type (exact figures from the page: "$25,000/year", "Full tuition waiver", "£18,000 stipend" — omit if not stated)
6. Application deadline (exact dates only — omit if no date appears on the page)
7. Whether it's for international students, domestic students, or both
8. How to apply — exact steps or direct URL; if no steps are listed, use the page URL so the user knows where to go next

EXTRACTION GUIDELINES:
- If a page mentions "financial aid" or "funding available" but doesn't specify amounts, look for links to detailed pages
- If a page is about a program/certificate, check if it mentions scholarships, assistantships, or funding specifically for that program
- Extract actual numbers, dates, and specific program names - avoid generic phrases
- If the page is not actually about funding but mentions it in passing, note that in the summary
- Be thorough - many pages list multiple funding opportunities

Be specific and extract actual details from each page. If a page lists multiple opportunities, list them all.

DO NOT hallucinate.
DO NOT fabricate funding.
DO NOT create placeholders.
ONLY extract real funding opportunities from the input content.

Return as JSON with this structure (omit any field whose value is unknown — never write "Not specified"):
{{
  "pages": [
    {{
      "url": "Page URL",
      "opportunities": [
        {{
          "name": "Name of scholarship/funding",
          "degree_level": "PhD/Masters/etc",
          "field": "Subject area (omit if not stated)",
          "eligibility": "Who can apply (omit if not stated)",
          "amount": "Exact funding amount/type (omit if not stated)",
          "deadline": "Exact date (omit if not found on page)",
          "for_international": true/false,
          "application_process": "Exact steps or source page URL — never omit"
        }}
      ],
      "page_summary": "Brief 1-2 sentence summary of what this page offers",
      "relevance_to_query": "How relevant is this to the user's needs (High/Medium/Low)"
    }}
  ]
}}

IMPORTANT: Return results for ALL {len(batch)} pages in the order they were provided."""

        try:
            response = await _get_client().chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a funding information extraction expert. Extract ONLY real, explicitly mentioned funding opportunities from scholarship pages. DO NOT invent, infer, or fabricate funding opportunities. Only extract what is explicitly stated in the page content. If no funding is found, return empty opportunities array.",
                    },
                    {"role": "user", "content": batch_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            result = json.loads(response.choices[0].message.content)

            # Extract results for each page
            batch_results = result.get("pages", [])
            normalized_batch_results: List[Dict[str, Any]] = []

            # Ensure we have results for all pages (handle cases where LLM might skip some)
            for i, page in enumerate(batch):
                url = page.get("url", "Unknown")
                # Find matching result by URL
                page_result = next((r for r in batch_results if r.get("url") == url), None)

                if page_result:
                    normalized_batch_results.append(page_result)
                else:
                    # Fallback: create empty result if LLM didn't return it
                    logger.warning(f"⚠️  No result returned for {url}, creating placeholder")
                    normalized_batch_results.append(
                        {
                            "url": url,
                            "opportunities": [],
                            "page_summary": "Analysis incomplete",
                            "relevance_to_query": "Unknown",
                        }
                    )

        except Exception as e:
            logger.error(f"❌ Error analyzing batch {batch_num}: {e}")
            # Create placeholder results for failed batch
            normalized_batch_results = []
            for page in batch:
                normalized_batch_results.append(
                    {
                        "url": page.get("url", "Unknown"),
                        "opportunities": [],
                        "page_summary": f"Analysis failed: {str(e)}",
                        "relevance_to_query": "Unknown",
                    }
                )

        return normalized_batch_results

    total_batches = (len(pages) + max_pages_per_batch - 1) // max_pages_per_batch
    batches = [
        pages[batch_start : batch_start + max_pages_per_batch]
        for batch_start in range(0, len(pages), max_pages_per_batch)
    ]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS_BATCHES)

    async def analyze_batch_with_limit(
        batch: List[Dict[str, Any]], batch_num: int
    ) -> List[Dict[str, Any]]:
        async with semaphore:
            return await analyze_batch(batch, batch_num, total_batches)

    batch_results = await asyncio.gather(
        *[
            analyze_batch_with_limit(batch, batch_num)
            for batch_num, batch in enumerate(batches, start=1)
        ]
    )

    all_results = [result for batch in batch_results for result in batch]

    logger.info(f"✅ Batch analysis complete: {len(all_results)} pages analyzed")
    return all_results


def _build_university_summary(
    university: str, analyzed_pages: List[Dict[str, Any]], total_opportunities: int
) -> str:
    if total_opportunities == 0:
        return (
            f"No relevant funding opportunities were found for {university} in the analyzed pages."
        )
    if not analyzed_pages:
        return f"No funding pages were analyzed for {university}."
    relevant_pages = sum(1 for page in analyzed_pages if page.get("opportunities"))
    return (
        f"Found {total_opportunities} relevant funding opportunit"
        f"{'y' if total_opportunities == 1 else 'ies'} across {relevant_pages} page(s) for {university}."
    )


def _collect_best_matches(analyzed_pages: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    best_matches: List[str] = []
    seen: set[str] = set()
    for page in analyzed_pages:
        for opportunity in page.get("opportunities", []):
            name = sanitize_text_for_llm(str(opportunity.get("name", "")))
            if not name or name in seen:
                continue
            seen.add(name)
            best_matches.append(name)
            if len(best_matches) >= limit:
                return best_matches
    return best_matches


@function_tool
async def analyze_crawler_results(
    universities: List[UniversityResult],
    user_query: str,
    extracted_keywords: Optional[List[str]] = None,
    min_relevance_score: int = 5,
) -> Dict[str, Any]:
    """
    Bridge crawler output into analyzer output.

    This tool accepts the crawler's university/page payload and returns a structured
    AnalyzerResult object, handling the flattening/grouping internally so the
    orchestrator does not need to manually reshape tool outputs.

    Args:
        extracted_keywords: Optional pre-extracted keywords from orchestrator.
                           Can be used to improve relevance scoring and filtering.
                           If None, analysis proceeds without keyword optimization.
    """
    universities_payload = [
        university.model_dump() if isinstance(university, UniversityResult) else university
        for university in universities
    ]

    # Log keyword source for transparency
    if extracted_keywords:
        logger.info(f"🔍 Analyzer using pre-extracted keywords: {extracted_keywords}")
    else:
        logger.info("🔍 Analyzer running without pre-extracted keywords")

    analyzed_universities: List[Dict[str, Any]] = []
    total_opportunities_found = 0

    for university in universities_payload:
        university_name = sanitize_text_for_llm(str(university.get("school", "Unknown University")))
        university_domain = sanitize_text_for_llm(str(university.get("domain", "")))
        funding_pages = university.get("funding_pages", []) or []
        candidate_pages = university.get("candidate_pages", []) or []
        cached_crawl_payload = (
            await get_cached_crawl_payload(university_domain, user_query)
            if university_domain
            else {}
        )
        cached_funding_pages = cached_crawl_payload.get("funding_pages", []) or []
        cached_candidate_pages = cached_crawl_payload.get("candidate_pages", []) or []

        if len(cached_candidate_pages) > len(candidate_pages):
            candidate_pages = cached_candidate_pages
        if len(cached_funding_pages) > len(funding_pages):
            funding_pages = cached_funding_pages
        analysis_source_pages = candidate_pages or funding_pages or []

        logger.info(
            "🧪 Analyzer input for %s | funding_pages=%d | candidate_pages=%d | cached_funding_pages=%d | cached_candidate_pages=%d | using=%d",
            university_name,
            len(funding_pages),
            len(candidate_pages),
            len(cached_funding_pages),
            len(cached_candidate_pages),
            len(analysis_source_pages),
        )

        if candidate_pages:
            eligible_pages = [
                page
                for page in candidate_pages
                if sanitize_text_for_llm(str(page.get("full_text", "") or page.get("text", "")))
            ]
        else:
            eligible_pages = [
                page
                for page in funding_pages
                if int(page.get("relevance_score", 0) or 0) >= min_relevance_score
            ]

        logger.info(
            "🧪 Analyzer eligible pages for %s | min_relevance_score=%d | eligible=%d | source=%s",
            university_name,
            min_relevance_score,
            len(eligible_pages),
            "candidate_pages" if candidate_pages else "funding_pages",
        )

        if eligible_pages:
            analysis_results = await _analyze_funding_pages_batch_impl(
                urls=[str(page.get("url", "")) for page in eligible_pages],
                titles=[
                    sanitize_text_for_llm(str(page.get("title", ""))) for page in eligible_pages
                ],
                previews=[
                    sanitize_text_for_llm(str(page.get("preview", "") or page.get("text", "")))
                    for page in eligible_pages
                ],
                full_texts=[
                    sanitize_text_for_llm(str(page.get("full_text", "") or page.get("text", "")))
                    for page in eligible_pages
                ],
                user_query=user_query,
            )
        else:
            analysis_results = []

        title_by_url = {
            str(page.get("url", "")): sanitize_text_for_llm(str(page.get("title", "No title")))
            for page in eligible_pages
        }

        analyzed_pages: List[Dict[str, Any]] = []
        for result in analysis_results:
            url = sanitize_text_for_llm(str(result.get("url", "")))
            opportunities = result.get("opportunities", []) or []
            if not opportunities:
                continue
            analyzed_pages.append(
                {
                    "url": url,
                    "title": title_by_url.get(url, "No title"),
                    "opportunities": opportunities,
                    "page_summary": sanitize_text_for_llm(
                        str(result.get("page_summary", "No summary available."))
                    ),
                    "relevance_to_query": sanitize_text_for_llm(
                        str(result.get("relevance_to_query", "Unknown"))
                    ),
                }
            )

        total_opportunities = sum(len(page.get("opportunities", [])) for page in analyzed_pages)
        total_opportunities_found += total_opportunities
        best_matches = _collect_best_matches(analyzed_pages)

        analyzed_universities.append(
            {
                "university": university_name,
                "domain": university_domain,
                "analyzed_pages": analyzed_pages,
                "total_opportunities": total_opportunities,
                "summary": _build_university_summary(
                    university_name, analyzed_pages, total_opportunities
                ),
                "best_matches": best_matches,
            }
        )

    if total_opportunities_found == 0:
        overall_summary = (
            "No relevant funding opportunities were found in the analyzed pages. "
            "Try a narrower university page set or a broader funding query."
        )
    else:
        overall_summary = (
            f"Found {total_opportunities_found} relevant funding opportunit"
            f"{'y' if total_opportunities_found == 1 else 'ies'} across "
            f"{len(analyzed_universities)} universit"
            f"{'y' if len(analyzed_universities) == 1 else 'ies'}."
        )

    result_payload = AnalyzerResultPayload(
        universities=[
            UniversityFundingAnalysisPayload.model_validate(university)
            for university in analyzed_universities
        ],
        overall_summary=overall_summary,
        total_opportunities_found=total_opportunities_found,
    )
    return result_payload.model_dump()


@function_tool
async def analyze_funding_page(
    url: str, title: str, preview: str, user_query: str, full_text: str = None
) -> dict:
    return await _analyze_funding_page_impl(
        url=url,
        title=title,
        preview=preview,
        user_query=user_query,
        full_text=full_text,
    )


@function_tool
async def analyze_funding_pages_batch(
    urls: List[str],
    titles: List[str],
    previews: List[str],
    full_texts: List[str],
    user_query: str,
    max_pages_per_batch: int = MAX_PAGES_PER_BATCH,
) -> List[Dict[str, Any]]:
    return await _analyze_funding_pages_batch_impl(
        urls=urls,
        titles=titles,
        previews=previews,
        full_texts=full_texts,
        user_query=user_query,
        max_pages_per_batch=max_pages_per_batch,
    )

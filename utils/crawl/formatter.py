"""
Result formatting and cross-university ranking.

Takes raw per-university crawl payloads and produces the structured
CrawlerResult dict consumed by analyze_crawler_results.

Cross-university ranking:
  After all universities are crawled, pages are globally re-ranked by
  relevance_score so the analyzer sees the highest-signal pages first —
  regardless of which university they came from.
"""

from typing import Any, Dict, List, Optional

from utils.logger import logger


def _coerce_university_input(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise the varied dict shapes the agent may pass in."""
    from utils.crawl._utils import _normalize_domain_url

    school = (entry.get("school") or entry.get("name") or "").strip()

    raw_domains = entry.get("domains")
    auxiliary_domains: List[str] = []
    if isinstance(raw_domains, list):
        auxiliary_domains = [
            n for n in (_normalize_domain_url(str(d or "")) for d in raw_domains) if n
        ]

    domain = entry.get("domain") or entry.get("domain_url") or entry.get("url")
    if not domain and auxiliary_domains:
        domain = auxiliary_domains[0]

    domain = _normalize_domain_url(str(domain or ""))
    if not domain:
        return None

    auxiliary_domains = [d for d in auxiliary_domains if d != domain]
    return {
        "school": school or "Unknown School",
        "domain": domain,
        "auxiliary_domains": auxiliary_domains,
    }


def _format_university_result(
    uni: Dict[str, str],
    crawl_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Convert a raw crawl payload into the per-university dict the analyzer expects."""
    filtered_funding_pages = []
    candidate_pages = []
    all_scores: List[int] = []

    for page in crawl_result.get("funding_pages", []):
        score = int(page.get("relevance_score", 0) or 0)
        all_scores.append(score)
        filtered_funding_pages.append(
            {
                "url": page.get("url", ""),
                "title": page.get("title", "No title"),
                "preview": page.get("preview", page.get("text", "")),
                "relevance_score": score,
                "text": page.get("text", page.get("preview", "")),
                "full_text": page.get("full_text", ""),
                "page_type": page.get("page_type", "funding_page"),
                "crawl_source": page.get("crawl_source", ""),
            }
        )

    for page in crawl_result.get("candidate_pages", []):
        candidate_pages.append(
            {
                "url": page.get("url", ""),
                "title": page.get("title", "No title"),
                "preview": page.get("preview", page.get("text", "")),
                "relevance_score": int(page.get("relevance_score", 0) or 0),
                "text": page.get("text", page.get("preview", "")),
                "full_text": page.get("full_text", ""),
                "page_type": page.get("page_type", "funding_page"),
                "crawl_source": page.get("crawl_source", ""),
            }
        )

    # Prefer candidate_pages (broader) for downstream analysis; fall back to funding_pages
    funding_pages_for_analysis = candidate_pages or filtered_funding_pages

    access_blocked = crawl_result.get("access_blocked", False)
    crawl_timed_out = crawl_result.get("crawl_timed_out", False)
    if access_blocked:
        summary = (
            f"⚠️ Could not retrieve pages from {uni['domain']} — "
            "the site appears to block automated access (e.g. Cloudflare bot protection). "
            "Results for this university may be incomplete or unavailable. "
            "Suggest the user visit the funding page directly."
        )
    elif crawl_timed_out:
        summary = (
            f"⚠️ Crawl timed out for {uni['domain']} — "
            "the site was too slow to respond within the time limit. "
            "Results for this university may be incomplete. "
            "Suggest the user visit the funding page directly."
        )
    else:
        summary = (
            f"Found {len(filtered_funding_pages)} high-confidence funding page(s) "
            f"and {len(candidate_pages)} crawler candidate page(s)."
        )

    return {
        "school": uni["school"],
        "domain": uni["domain"],
        "funding_pages": funding_pages_for_analysis,
        "candidate_pages": candidate_pages,
        "filtered_funding_pages": filtered_funding_pages,
        "access_blocked": access_blocked,
        "crawl_timed_out": crawl_timed_out,
        "summary": summary,
        # Attach best score for cross-university ranking
        "_max_relevance_score": max(all_scores, default=0),
    }


def rank_universities(
    crawler_universities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Re-rank universities by their best page relevance score (descending).

    Universities with higher-scoring funding pages appear first so the
    analyzer — which processes in order — surfaces the strongest results
    without the user having to know which school queried first.
    """
    ranked = sorted(
        crawler_universities,
        key=lambda u: u.get("_max_relevance_score", 0),
        reverse=True,
    )
    # Strip internal ranking key before returning to caller
    for u in ranked:
        u.pop("_max_relevance_score", None)
    return ranked


def build_crawler_result(
    crawler_universities: List[Dict[str, Any]],
    extracted_keywords: List[str],
    user_query: Optional[str],
    all_scores: List[int],
) -> Dict[str, Any]:
    """
    Assemble the final CrawlerResult dict returned by the @function_tool.
    Universities are globally ranked by relevance before inclusion.
    """
    ranked = rank_universities(crawler_universities)

    relevance_tiers = {
        "exceptional": sum(1 for s in all_scores if s >= 100),
        "high": sum(1 for s in all_scores if 50 <= s < 100),
        "moderate": sum(1 for s in all_scores if 5 <= s < 50),
    }

    logger.info(
        "Cross-university ranking complete: %s",
        [
            (
                u["school"],
                max((p.get("relevance_score", 0) for p in u.get("funding_pages", [])), default=0),
            )
            for u in ranked
        ],
    )

    return {
        "universities": ranked,
        "search_strategy": (
            "query-guided crawl with keyword extraction"
            if user_query
            else "domain crawl without query keywords"
        ),
        "total_funding_pages": sum(len(u["funding_pages"]) for u in ranked),
        "keyword_analysis": {
            "user_query": user_query or "",
            "keywords": extracted_keywords,
            "keyword_count": len(extracted_keywords),
        },
        "relevance_tiers": relevance_tiers,
    }

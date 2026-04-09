"""
Evaluation harness for FindMyScholarship AI.

Measures whether the full pipeline correctly extracts FundingOpportunity objects
against a golden dataset of known scholarship cases.

Metrics computed per case and overall:
    precision    — of all opps returned, fraction that match a golden opp
    recall       — of all golden opps, fraction that were found
    f1           — harmonic mean of precision and recall
    field_acc    — fraction of FieldChecks that pass
    crawl_hit    — fraction of expected_pages that appeared in crawl results
    pipeline_ok  — did the pipeline complete without error (always 0/1)

Usage (full pipeline — needs OPENAI_API_KEY + SERPAPI_API_KEY + FLARESOLVERR_URL):
    python -m eval.harness

Usage (cache-only — skips live API calls, uses cached results):
    python -m eval.harness --cached-only

Usage (single case):
    python -m eval.harness --case MIT-phd-cs

Usage (save JSON report):
    python -m eval.harness --output report.json

Usage (list cases):
    python -m eval.harness --list
"""

from __future__ import annotations

import argparse
import asyncio
import json

# Make sure project root is on path when running as a module
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.golden_dataset import GOLDEN_CASES, FieldCheck, GoldenCase, get_case
from utils.logger import logger

# ── Result structures ──────────────────────────────────────────────────────────


@dataclass
class CaseResult:
    case_id: str
    query: str
    pipeline_ok: bool
    error: Optional[str]
    returned_opps: List[str]  # All opportunity names returned
    crawled_pages: List[str]  # All crawled URLs returned
    precision: float
    recall: float
    f1: float
    field_acc: float  # Fraction of FieldChecks that passed
    crawl_hit: float  # Fraction of expected_pages found
    duration_s: float
    field_check_details: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EvalReport:
    run_at: str
    cached_only: bool
    total_cases: int
    passed_cases: int  # Cases where pipeline_ok=True
    avg_precision: float
    avg_recall: float
    avg_f1: float
    avg_field_acc: float
    avg_crawl_hit: float
    total_duration_s: float
    cases: List[CaseResult]


# ── Metric helpers ─────────────────────────────────────────────────────────────


def _partial_match(needle: str, haystack: List[str]) -> bool:
    """Return True if any item in haystack contains needle (case-insensitive)."""
    needle_lower = needle.lower()
    return any(needle_lower in h.lower() for h in haystack)


def _compute_precision_recall(returned: List[str], golden: List[str]) -> tuple[float, float, float]:
    """
    Compute precision, recall, F1 using partial string matching.

    - returned: list of returned opportunity names
    - golden:   list of expected opportunity name fragments
    """
    if not golden:
        # No gold standard — precision is vacuously 1.0, recall is 1.0
        return 1.0, 1.0, 1.0

    if not returned:
        return 0.0, 0.0, 0.0

    # Recall: how many golden opps were found?
    found = sum(1 for g in golden if _partial_match(g, returned))
    recall = found / len(golden)

    # Precision: of returned opps, how many match a golden opp?
    matched = sum(1 for r in returned if any(g.lower() in r.lower() for g in golden))
    precision = matched / len(returned)

    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def _check_fields(
    field_checks: List[FieldCheck],
    opportunities: List[Dict[str, Any]],
) -> tuple[float, List[Dict[str, Any]]]:
    """
    Run FieldChecks against extracted opportunities.
    Returns (accuracy 0-1, detail list).
    """
    if not field_checks:
        return 1.0, []

    details = []
    passed = 0
    for fc in field_checks:
        matched_opp = next(
            (
                o
                for o in opportunities
                if fc.opportunity_name_fragment.lower() in o.get("name", "").lower()
            ),
            None,
        )
        if matched_opp is None:
            details.append(
                {
                    "check": f"{fc.opportunity_name_fragment}.{fc.field}",
                    "passed": False,
                    "reason": "opportunity not found in results",
                    "expected": fc.expected_value,
                    "got": None,
                }
            )
            continue

        actual = str(matched_opp.get(fc.field, "")).lower()
        expected = fc.expected_value.lower()
        ok = expected in actual
        if ok:
            passed += 1
        details.append(
            {
                "check": f"{fc.opportunity_name_fragment}.{fc.field}",
                "passed": ok,
                "expected": fc.expected_value,
                "got": matched_opp.get(fc.field),
            }
        )

    return passed / len(field_checks), details


# ── Pipeline runner ────────────────────────────────────────────────────────────


async def _run_case(case: GoldenCase, cached_only: bool) -> CaseResult:
    """Execute the full pipeline on a single golden case and return metrics."""
    start = time.monotonic()

    try:
        # Import here so the harness can be imported without triggering all agents
        from utils.analyzer import analyze_crawler_results
        from utils.crawler import crawl_universities_formatted, get_cached_crawl_payload
        from utils.keyword_extractor import extract_query_keywords

        # ── Step 1: Keyword extraction ────────────────────────────────────────
        keywords = await extract_query_keywords(case.query)

        # ── Step 2: Domain discovery ──────────────────────────────────────────
        from utils.university_db import lookup_university

        universities_input = []
        for uni_name in case.universities:
            domains = lookup_university(uni_name, case.country)
            if not domains:
                # Fallback: use find_university_domain (may hit DuckDuckGo/SerpAPI)
                if not cached_only:
                    from utils.find_domain import find_university_domain

                    domains = await find_university_domain(uni_name, case.country)
                else:
                    domains = []
            universities_input.append(
                {
                    "school": uni_name,
                    "domain": domains[0] if domains else "",
                    "all_domains": domains,
                }
            )

        # ── Step 3: Crawl ─────────────────────────────────────────────────────
        if cached_only:
            # Only use cached crawl results, don't hit live sites
            crawl_result_raw = None
            for u in universities_input:
                if u["domain"]:
                    cached_payload = await get_cached_crawl_payload(u["domain"], case.query)
                    if cached_payload.get("funding_pages") or cached_payload.get("candidate_pages"):
                        crawl_result_raw = cached_payload
                        break
            if crawl_result_raw is None:
                return CaseResult(
                    case_id=case.id,
                    query=case.query,
                    pipeline_ok=False,
                    error="--cached-only: no cached crawl data found for this case",
                    returned_opps=[],
                    crawled_pages=[],
                    precision=0.0,
                    recall=0.0 if case.expected_opps else 1.0,
                    f1=0.0,
                    field_acc=1.0 if not case.field_checks else 0.0,
                    crawl_hit=0.0,
                    duration_s=time.monotonic() - start,
                )
        else:
            crawl_result = await crawl_universities_formatted(
                universities=universities_input,
                user_query=case.query,
                extracted_keywords=keywords.all_keywords,
            )
            crawl_result_raw = crawl_result

        # ── Step 4: Analyze ───────────────────────────────────────────────────
        if not cached_only:
            analysis = await analyze_crawler_results(
                universities=crawl_result_raw.get("universities", [])
                if isinstance(crawl_result_raw, dict)
                else [],
                user_query=case.query,
                extracted_keywords=keywords,
            )
        else:
            analysis = None

        # ── Gather returned opportunity names ─────────────────────────────────
        all_opps: List[Dict[str, Any]] = []
        all_pages: List[str] = []

        if analysis and hasattr(analysis, "universities"):
            for u in analysis.universities:
                for page in u.analyzed_pages:
                    all_pages.append(page.url)
                    for opp in page.opportunities:
                        all_opps.append(opp.model_dump())
        elif isinstance(crawl_result_raw, dict):
            for p in crawl_result_raw.get("funding_pages", []):
                all_pages.append(p.get("url", ""))
            for p in crawl_result_raw.get("candidate_pages", []):
                all_pages.append(p.get("url", ""))

        returned_names = [o.get("name", "") for o in all_opps]

        # ── Compute metrics ───────────────────────────────────────────────────
        precision, recall, f1 = _compute_precision_recall(returned_names, case.expected_opps)
        field_acc, field_details = _check_fields(case.field_checks, all_opps)

        expected_pages_lower = [p.lower() for p in case.expected_pages]
        pages_lower = [p.lower() for p in all_pages]
        crawl_hit = (
            sum(1 for ep in expected_pages_lower if any(ep in rp for rp in pages_lower))
            / len(expected_pages_lower)
            if expected_pages_lower
            else 1.0
        )

        return CaseResult(
            case_id=case.id,
            query=case.query,
            pipeline_ok=True,
            error=None,
            returned_opps=returned_names,
            crawled_pages=all_pages,
            precision=precision,
            recall=recall,
            f1=f1,
            field_acc=field_acc,
            crawl_hit=crawl_hit,
            duration_s=time.monotonic() - start,
            field_check_details=field_details,
        )

    except Exception as exc:
        logger.exception(f"[Eval] Case {case.id} failed: {exc}")
        return CaseResult(
            case_id=case.id,
            query=case.query,
            pipeline_ok=False,
            error=str(exc),
            returned_opps=[],
            crawled_pages=[],
            precision=0.0,
            recall=0.0 if case.expected_opps else 1.0,
            f1=0.0,
            field_acc=1.0 if not case.field_checks else 0.0,
            crawl_hit=0.0,
            duration_s=time.monotonic() - start,
        )


# ── Report builder ─────────────────────────────────────────────────────────────


def _build_report(results: List[CaseResult], cached_only: bool) -> EvalReport:
    from datetime import datetime, timezone

    n = len(results)
    passed = sum(1 for r in results if r.pipeline_ok)

    def avg(attr: str) -> float:
        vals = [getattr(r, attr) for r in results]
        return sum(vals) / n if n else 0.0

    return EvalReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        cached_only=cached_only,
        total_cases=n,
        passed_cases=passed,
        avg_precision=avg("precision"),
        avg_recall=avg("recall"),
        avg_f1=avg("f1"),
        avg_field_acc=avg("field_acc"),
        avg_crawl_hit=avg("crawl_hit"),
        total_duration_s=sum(r.duration_s for r in results),
        cases=results,
    )


def _print_report(report: EvalReport) -> None:
    w = 70
    print("=" * w)
    print(f"  FindMyScholarship Eval Report — {report.run_at[:19]}Z")
    print(f"  Mode: {'cache-only' if report.cached_only else 'live pipeline'}")
    print("=" * w)
    print(f"  Cases run:     {report.total_cases}")
    print(f"  Pipeline OK:   {report.passed_cases}/{report.total_cases}")
    print(f"  Avg Precision: {report.avg_precision:.2%}")
    print(f"  Avg Recall:    {report.avg_recall:.2%}")
    print(f"  Avg F1:        {report.avg_f1:.2%}")
    print(f"  Avg Field Acc: {report.avg_field_acc:.2%}")
    print(f"  Avg Crawl Hit: {report.avg_crawl_hit:.2%}")
    print(f"  Total time:    {report.total_duration_s:.1f}s")
    print("-" * w)
    for r in report.cases:
        status = "OK " if r.pipeline_ok else "ERR"
        print(
            f"  [{status}] {r.case_id:<30} "
            f"P={r.precision:.0%} R={r.recall:.0%} F1={r.f1:.0%} "
            f"FA={r.field_acc:.0%} CH={r.crawl_hit:.0%} "
            f"({r.duration_s:.1f}s)"
        )
        if r.error:
            print(f"         ERROR: {r.error}")
        for fc in r.field_check_details:
            mark = "✓" if fc["passed"] else "✗"
            print(f"         {mark} {fc['check']}: expected={fc['expected']!r} got={fc['got']!r}")
    print("=" * w)


# ── Entry point ────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="FindMyScholarship evaluation harness")
    parser.add_argument("--cached-only", action="store_true", help="Use cached crawl data only")
    parser.add_argument("--case", metavar="ID", help="Run a single case by ID")
    parser.add_argument("--output", metavar="FILE", help="Save JSON report to file")
    parser.add_argument("--list", action="store_true", help="List all case IDs and exit")
    args = parser.parse_args()

    if args.list:
        from eval.golden_dataset import list_case_ids

        for cid in list_case_ids():
            print(cid)
        return

    if args.case:
        case = get_case(args.case)
        if not case:
            print(f"ERROR: case '{args.case}' not found. Use --list to see valid IDs.")
            sys.exit(1)
        cases_to_run = [case]
    else:
        cases_to_run = GOLDEN_CASES

    print(f"Running {len(cases_to_run)} eval case(s)...")
    results = []
    for case in cases_to_run:
        print(f"  → {case.id} ...", end=" ", flush=True)
        result = await _run_case(case, cached_only=args.cached_only)
        status = "OK" if result.pipeline_ok else "ERR"
        print(f"{status} ({result.duration_s:.1f}s)")
        results.append(result)

    report = _build_report(results, cached_only=args.cached_only)
    _print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            # Convert dataclasses to plain dicts for JSON serialization
            json.dump(asdict(report), f, indent=2, default=str)
        print(f"\nReport saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())

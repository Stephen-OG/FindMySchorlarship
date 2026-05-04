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
    judge_score  — LLM relevance score 0-1 (only with --judge flag)
    pipeline_ok  — did the pipeline complete without error (always 0/1)

Cases with expected_opps=[] are excluded from precision/recall/f1 averages
(they only contribute to pipeline_ok).

MCP-path cases (path="mcp") call search_scholarships / search_research_grants /
search_all_funding directly instead of the crawl+analyze path.

Usage:
    # Full live pipeline
    python -m eval.harness

    # Cache-only (no live crawls)
    python -m eval.harness --cached-only

    # Single case
    python -m eval.harness --case MIT-phd-cs

    # Save JSON report
    python -m eval.harness --output report.json

    # Compare against a baseline report
    python -m eval.harness --output new.json --compare baseline.json

    # Run up to 5 cases concurrently
    python -m eval.harness --concurrency 5

    # Add LLM-as-judge scores (requires OPENAI_API_KEY)
    python -m eval.harness --judge

    # List all case IDs
    python -m eval.harness --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
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
    returned_opps: List[str]  # All opportunity names/titles returned
    crawled_pages: List[str]  # All crawled URLs (empty for MCP cases)
    precision: float
    recall: float
    f1: float
    field_acc: float
    crawl_hit: float
    duration_s: float
    has_golden_opps: bool = True  # False when expected_opps=[] — excluded from P/R/F1 avg
    step_timings: Dict[str, float] = field(default_factory=dict)
    judge_score: Optional[float] = None
    field_check_details: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EvalReport:
    run_at: str
    cached_only: bool
    total_cases: int
    passed_cases: int
    avg_precision: float  # averaged only over cases with has_golden_opps=True
    avg_recall: float
    avg_f1: float
    avg_field_acc: float
    avg_crawl_hit: float
    avg_judge_score: Optional[float]
    total_duration_s: float
    cases: List[CaseResult]


# ── Metric helpers ─────────────────────────────────────────────────────────────


def _partial_match(needle: str, haystack: List[str]) -> bool:
    needle_lower = needle.lower()
    return any(needle_lower in h.lower() for h in haystack)


def _compute_precision_recall(
    returned: List[str], golden: List[str]
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Returns (precision, recall, f1) using partial string matching.
    Returns (None, None, None) when golden is empty — caller should mark
    has_golden_opps=False and exclude from averages.
    """
    if not golden:
        return None, None, None

    if not returned:
        return 0.0, 0.0, 0.0

    found = sum(1 for g in golden if _partial_match(g, returned))
    recall = found / len(golden)

    matched = sum(1 for r in returned if any(g.lower() in r.lower() for g in golden))
    precision = matched / len(returned)

    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def _check_fields(
    field_checks: List[FieldCheck],
    opportunities: List[Dict[str, Any]],
) -> tuple[float, List[Dict[str, Any]]]:
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


def _extract_titles_from_markdown(text: str) -> List[str]:
    """Extract result titles from the MCP tool's markdown output (### N. Title lines)."""
    return re.findall(r"###\s+\d+\.\s+(.+)", text)


# ── LLM-as-judge ──────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are evaluating the quality of scholarship search results.
Given a user query and a list of returned funding opportunity titles, score
the overall result quality on a scale of 0.0 to 1.0.

Scoring guide:
  1.0 — All results are directly relevant and specific to the query (field, level, country)
  0.7 — Majority are relevant; minor mismatches in level or field
  0.4 — Results are in the right domain but largely off-topic (wrong country, wrong level)
  0.1 — Results are returned but completely irrelevant to the query
  0.0 — No results, or all results are gibberish / wrong domain

Return only valid JSON: {"score": <float 0.0-1.0>, "reason": "<one sentence>"}
No other text."""


async def _judge_results(
    query: str,
    returned_opps: List[str],
    country: Optional[str],
) -> float:
    """
    Call gpt-4o-mini to score the relevance of returned opportunities.
    Returns 0.0 on any error so it never breaks the harness run.
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        opps_text = "\n".join(f"- {o}" for o in returned_opps) if returned_opps else "(no results)"
        user_content = (
            f"Query: {query}\nCountry: {country or 'any'}\n\nReturned results:\n{opps_text}"
        )
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=80,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        score = float(parsed["score"])
        logger.debug("[Judge] %s → %.2f (%s)", query[:60], score, parsed.get("reason", ""))
        return max(0.0, min(1.0, score))
    except Exception as exc:
        logger.warning("[Judge] scoring failed for %r: %s", query[:60], exc)
        return 0.0


# ── MCP path runner ────────────────────────────────────────────────────────────


async def _run_mcp_case(
    case: GoldenCase,
    semaphore: asyncio.Semaphore,
    judge: bool,
) -> CaseResult:
    """Run a case that exercises a national-database MCP tool directly."""
    async with semaphore:
        start = time.monotonic()
        step_timings: Dict[str, float] = {}

        try:
            # Import here so the harness can be imported without starting the MCP server
            from mcp_server.server import (
                search_all_funding,
                search_research_grants,
                search_scholarships,
            )

            tool_fn = {
                "search_scholarships": search_scholarships,
                "search_research_grants": search_research_grants,
                "search_all_funding": search_all_funding,
            }.get(case.mcp_tool)

            if tool_fn is None:
                raise ValueError(f"Unknown mcp_tool: {case.mcp_tool!r}")

            # search_research_grants uses 'subject' instead of 'query'
            query_kwarg = "subject" if case.mcp_tool == "search_research_grants" else "query"
            t0 = time.monotonic()
            output_text: str = await tool_fn(
                **{query_kwarg: case.query},
                level=case.mcp_level,
                country=case.mcp_country,
                limit=10,
            )
            step_timings["mcp_call"] = time.monotonic() - t0

            returned_titles = _extract_titles_from_markdown(output_text)

            precision, recall, f1 = _compute_precision_recall(returned_titles, case.expected_opps)
            has_golden = precision is not None
            precision = precision if has_golden else 1.0
            recall = recall if has_golden else 1.0
            f1 = f1 if has_golden else 1.0

            judge_score: Optional[float] = None
            if judge:
                t0 = time.monotonic()
                judge_score = await _judge_results(case.query, returned_titles, case.country)
                step_timings["judge"] = time.monotonic() - t0

            return CaseResult(
                case_id=case.id,
                query=case.query,
                pipeline_ok=True,
                error=None,
                returned_opps=returned_titles,
                crawled_pages=[],
                precision=precision,
                recall=recall,
                f1=f1,
                field_acc=1.0,  # no field checks for MCP cases
                crawl_hit=1.0,  # not applicable
                duration_s=time.monotonic() - start,
                has_golden_opps=has_golden,
                step_timings=step_timings,
                judge_score=judge_score,
            )

        except Exception as exc:
            logger.exception("[Eval] MCP case %s failed: %s", case.id, exc)
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
                field_acc=1.0,
                crawl_hit=1.0,
                duration_s=time.monotonic() - start,
                has_golden_opps=bool(case.expected_opps),
                step_timings=step_timings,
            )


# ── University crawl path runner ───────────────────────────────────────────────


async def _run_university_case(
    case: GoldenCase,
    cached_only: bool,
    semaphore: asyncio.Semaphore,
    judge: bool,
) -> CaseResult:
    """Execute the full crawl+analyze pipeline on a single golden case."""
    async with semaphore:
        start = time.monotonic()
        step_timings: Dict[str, float] = {}

        try:
            from utils.analyzer import _analyze_crawler_results
            from utils.crawl.tools import _crawl_universities_formatted
            from utils.crawler import get_cached_crawl_payload
            from utils.keyword_extractor import extract_query_keywords

            # ── Step 1: Keyword extraction ────────────────────────────────────
            t0 = time.monotonic()
            keywords = await extract_query_keywords(case.query)
            step_timings["keyword_extraction"] = time.monotonic() - t0

            # ── Step 2: Domain discovery ──────────────────────────────────────
            t0 = time.monotonic()
            from utils.university_db import lookup_university

            universities_input = []
            for uni_name in case.universities:
                domains = lookup_university(uni_name, case.country)
                if not domains:
                    if not cached_only:
                        from utils.find_domain import _find_university_domain

                        domains = await _find_university_domain(uni_name, case.country)
                    else:
                        domains = []
                universities_input.append(
                    {
                        "school": uni_name,
                        "domain": domains[0] if domains else "",
                        "all_domains": domains,
                    }
                )
            step_timings["domain_lookup"] = time.monotonic() - t0

            # ── Step 3: Crawl ─────────────────────────────────────────────────
            t0 = time.monotonic()
            if cached_only:
                crawl_result_raw = None
                for u in universities_input:
                    if u["domain"]:
                        cached_payload = await get_cached_crawl_payload(u["domain"], case.query)
                        if cached_payload.get("funding_pages") or cached_payload.get(
                            "candidate_pages"
                        ):
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
                        has_golden_opps=bool(case.expected_opps),
                        step_timings=step_timings,
                    )
            else:
                crawl_result_raw = await _crawl_universities_formatted(
                    universities=universities_input,
                    user_query=case.query,
                    extracted_keywords=keywords.all_keywords,
                )
            step_timings["crawl"] = time.monotonic() - t0

            # ── Step 4: Analyze ───────────────────────────────────────────────
            t0 = time.monotonic()
            if not cached_only:
                analysis = await _analyze_crawler_results(
                    universities=crawl_result_raw.get("universities", [])
                    if isinstance(crawl_result_raw, dict)
                    else [],
                    user_query=case.query,
                    extracted_keywords=keywords.all_keywords,
                )
            else:
                analysis = None
            step_timings["analyze"] = time.monotonic() - t0

            # ── Gather returned opportunity names ─────────────────────────────
            # _analyze_crawler_results returns a plain dict (model_dump output).
            all_opps: List[Dict[str, Any]] = []
            all_pages: List[str] = []

            if isinstance(analysis, dict) and analysis.get("universities"):
                for u in analysis["universities"]:
                    for page in u.get("analyzed_pages", []):
                        all_pages.append(page.get("url", ""))
                        for opp in page.get("opportunities", []):
                            all_opps.append(opp if isinstance(opp, dict) else opp.model_dump())
            elif analysis and hasattr(analysis, "universities"):
                # Pydantic object path (future-proofing)
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

            # ── Compute metrics ───────────────────────────────────────────────
            precision, recall, f1 = _compute_precision_recall(returned_names, case.expected_opps)
            has_golden = precision is not None
            precision = precision if has_golden else 1.0
            recall = recall if has_golden else 1.0
            f1 = f1 if has_golden else 1.0

            field_acc, field_details = _check_fields(case.field_checks, all_opps)

            expected_pages_lower = [p.lower() for p in case.expected_pages]
            pages_lower = [p.lower() for p in all_pages]
            crawl_hit = (
                sum(1 for ep in expected_pages_lower if any(ep in rp for rp in pages_lower))
                / len(expected_pages_lower)
                if expected_pages_lower
                else 1.0
            )

            judge_score: Optional[float] = None
            if judge:
                t0 = time.monotonic()
                judge_score = await _judge_results(case.query, returned_names, case.country)
                step_timings["judge"] = time.monotonic() - t0

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
                has_golden_opps=has_golden,
                step_timings=step_timings,
                judge_score=judge_score,
                field_check_details=field_details,
            )

        except Exception as exc:
            logger.exception("[Eval] Case %s failed: %s", case.id, exc)
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
                has_golden_opps=bool(case.expected_opps),
                step_timings=step_timings,
            )


# ── Case dispatcher ────────────────────────────────────────────────────────────


async def _run_case(
    case: GoldenCase,
    cached_only: bool,
    semaphore: asyncio.Semaphore,
    judge: bool,
) -> CaseResult:
    if case.path == "mcp":
        return await _run_mcp_case(case, semaphore, judge)
    return await _run_university_case(case, cached_only, semaphore, judge)


# ── Report builder ─────────────────────────────────────────────────────────────


def _build_report(results: List[CaseResult], cached_only: bool) -> EvalReport:
    from datetime import datetime, timezone

    n = len(results)
    passed = sum(1 for r in results if r.pipeline_ok)

    # Only average precision/recall/f1 over cases that have golden labels.
    scored = [r for r in results if r.has_golden_opps and r.pipeline_ok]

    def avg_over(results_subset: List[CaseResult], attr: str) -> float:
        vals = [getattr(r, attr) for r in results_subset]
        return sum(vals) / len(vals) if vals else 0.0

    judged = [r for r in results if r.judge_score is not None]
    avg_judge: Optional[float] = (
        sum(r.judge_score for r in judged) / len(judged) if judged else None  # type: ignore[misc]
    )

    return EvalReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        cached_only=cached_only,
        total_cases=n,
        passed_cases=passed,
        avg_precision=avg_over(scored, "precision"),
        avg_recall=avg_over(scored, "recall"),
        avg_f1=avg_over(scored, "f1"),
        avg_field_acc=avg_over([r for r in results if r.pipeline_ok], "field_acc"),
        avg_crawl_hit=avg_over([r for r in results if r.pipeline_ok], "crawl_hit"),
        avg_judge_score=avg_judge,
        total_duration_s=sum(r.duration_s for r in results),
        cases=results,
    )


def _print_report(report: EvalReport) -> None:
    w = 72
    print("=" * w)
    print(f"  FindMyScholarship Eval Report — {report.run_at[:19]}Z")
    print(f"  Mode: {'cache-only' if report.cached_only else 'live pipeline'}")
    print("=" * w)
    print(f"  Cases run:     {report.total_cases}")
    print(f"  Pipeline OK:   {report.passed_cases}/{report.total_cases}")

    scored_count = sum(1 for c in report.cases if c.has_golden_opps and c.pipeline_ok)
    print(f"  Scored cases:  {scored_count}  (cases with golden opps, pipeline OK)")
    print(f"  Avg Precision: {report.avg_precision:.2%}")
    print(f"  Avg Recall:    {report.avg_recall:.2%}")
    print(f"  Avg F1:        {report.avg_f1:.2%}")
    print(f"  Avg Field Acc: {report.avg_field_acc:.2%}")
    print(f"  Avg Crawl Hit: {report.avg_crawl_hit:.2%}")
    if report.avg_judge_score is not None:
        print(f"  Avg LLM Judge: {report.avg_judge_score:.2%}")
    print(f"  Total time:    {report.total_duration_s:.1f}s")
    print("-" * w)

    for r in report.cases:
        status = "OK " if r.pipeline_ok else "ERR"
        no_gold = "" if r.has_golden_opps else " [no-gold]"
        judge_str = f" J={r.judge_score:.0%}" if r.judge_score is not None else ""
        print(
            f"  [{status}] {r.case_id:<32} "
            f"P={r.precision:.0%} R={r.recall:.0%} F1={r.f1:.0%} "
            f"FA={r.field_acc:.0%} CH={r.crawl_hit:.0%}"
            f"{judge_str}{no_gold} ({r.duration_s:.1f}s)"
        )
        if r.error:
            print(f"         ERROR: {r.error}")
        for fc in r.field_check_details:
            mark = "✓" if fc["passed"] else "✗"
            print(f"         {mark} {fc['check']}: expected={fc['expected']!r} got={fc['got']!r}")
        if r.step_timings:
            timing_parts = "  ".join(f"{k}={v:.1f}s" for k, v in sorted(r.step_timings.items()))
            print(f"           timings: {timing_parts}")

    print("=" * w)


# ── Baseline comparison ────────────────────────────────────────────────────────


def _compare_reports(current: EvalReport, baseline_path: str) -> None:
    try:
        with open(baseline_path) as f:
            baseline_data = json.load(f)
    except Exception as exc:
        print(f"\n[compare] Could not load baseline {baseline_path!r}: {exc}")
        return

    w = 72
    print("\n" + "=" * w)
    print(f"  Comparison vs {baseline_path}")
    print("=" * w)

    _AGGREGATE_METRICS = [
        ("avg_precision", "Avg Precision"),
        ("avg_recall", "Avg Recall"),
        ("avg_f1", "Avg F1"),
        ("avg_field_acc", "Avg Field Acc"),
        ("avg_crawl_hit", "Avg Crawl Hit"),
        ("avg_judge_score", "Avg LLM Judge"),
    ]
    print(f"  {'Metric':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
    print("  " + "-" * 54)
    for key, label in _AGGREGATE_METRICS:
        base_val = baseline_data.get(key)
        curr_val = getattr(current, key, None)
        if base_val is None or curr_val is None:
            continue
        delta = curr_val - base_val
        arrow = "▲" if delta > 0.001 else ("▼" if delta < -0.001 else "±")
        print(f"  {label:<20} {base_val:>9.2%} {curr_val:>9.2%}  {delta:+.2%} {arrow}")

    # Per-case F1 deltas
    baseline_f1: Dict[str, float] = {c["case_id"]: c["f1"] for c in baseline_data.get("cases", [])}
    current_f1: Dict[str, float] = {c.case_id: c.f1 for c in current.cases if c.has_golden_opps}

    regressions = []
    improvements = []
    for cid, curr_f1 in current_f1.items():
        base_f1 = baseline_f1.get(cid)
        if base_f1 is None:
            continue
        delta = curr_f1 - base_f1
        if delta > 0.05:
            improvements.append((cid, base_f1, curr_f1, delta))
        elif delta < -0.05:
            regressions.append((cid, base_f1, curr_f1, delta))

    if improvements or regressions:
        print()
        if regressions:
            print("  REGRESSIONS (F1 dropped >5%):")
            for cid, b, c, d in sorted(regressions, key=lambda x: x[3]):
                print(f"    ✗ {cid:<34} {b:.0%} → {c:.0%}  ({d:+.0%})")
        if improvements:
            print("  IMPROVEMENTS (F1 gained >5%):")
            for cid, b, c, d in sorted(improvements, key=lambda x: -x[3]):
                print(f"    ✓ {cid:<34} {b:.0%} → {c:.0%}  ({d:+.0%})")

    print("=" * w)


# ── Entry point ────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="FindMyScholarship evaluation harness")
    parser.add_argument("--cached-only", action="store_true", help="Use cached crawl data only")
    parser.add_argument("--case", metavar="ID", help="Run a single case by ID")
    parser.add_argument("--output", metavar="FILE", help="Save JSON report to file")
    parser.add_argument(
        "--compare", metavar="FILE", help="Compare output against a baseline JSON report"
    )
    parser.add_argument(
        "--concurrency",
        metavar="N",
        type=int,
        default=3,
        help="Max concurrent cases (default: 3)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Score results with LLM-as-judge (requires OPENAI_API_KEY)",
    )
    parser.add_argument("--list", action="store_true", help="List all case IDs and exit")
    args = parser.parse_args()

    if args.list:
        for c in GOLDEN_CASES:
            path_label = f"  [{c.path}]" if c.path != "university" else ""
            print(f"{c.id}{path_label}")
        return

    if args.case:
        case = get_case(args.case)
        if not case:
            print(f"ERROR: case '{args.case}' not found. Use --list to see valid IDs.")
            sys.exit(1)
        cases_to_run = [case]
    else:
        cases_to_run = GOLDEN_CASES

    print(
        f"Running {len(cases_to_run)} eval case(s) "
        f"(concurrency={args.concurrency}, judge={args.judge})..."
    )

    semaphore = asyncio.Semaphore(args.concurrency)

    # Progress printing is best-effort — gather runs everything concurrently
    # so we print a dot per completed task instead of per-start.
    tasks = [
        _run_case(c, cached_only=args.cached_only, semaphore=semaphore, judge=args.judge)
        for c in cases_to_run
    ]

    if args.concurrency == 1:
        # Sequential mode: print progress as each case finishes
        results = []
        for case, task in zip(cases_to_run, tasks):
            print(f"  → {case.id} ...", end=" ", flush=True)
            result = await task
            status = "OK" if result.pipeline_ok else "ERR"
            print(f"{status} ({result.duration_s:.1f}s)")
            results.append(result)
    else:
        results = list(await asyncio.gather(*tasks))

    report = _build_report(results, cached_only=args.cached_only)
    _print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        print(f"\nReport saved to: {args.output}")

    if args.compare:
        _compare_reports(report, args.compare)


if __name__ == "__main__":
    asyncio.run(main())

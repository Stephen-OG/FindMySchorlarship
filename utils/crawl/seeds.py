"""
Seed URL generation for university crawls.

Produces a prioritised list of URLs to visit first, biased towards
paths that commonly host funding/scholarship information.
"""

from typing import List, Set
from urllib.parse import urljoin, urlparse

from utils.crawl._constants import (
    ACADEMIC_HUB_PATH_HINTS,
    AUXILIARY_DOMAIN_SEED_LIMIT,
    DOCTORAL_PATH_HINTS,
    FUNDING_PATH_HINTS,
    MAIN_DOMAIN_SEED_LIMIT,
    MAX_AUXILIARY_SEED_DOMAINS,
    MAX_SEED_URLS,
    MAX_TOTAL_INITIAL_QUEUE,
)
from utils.crawl._utils import (
    get_domain_scope,
    normalize_url,
    path_variants_for_term,
    slugify_path_term,
)
from utils.crawl.models import CrawlQueueItem, QueryConstraints


def generate_seed_urls(domain_url: str, constraints: QueryConstraints) -> List[tuple[str, int]]:
    """
    Generate high-priority URLs that are likely to contain doctoral funding information.
    Returns list of (url, priority) pairs, sorted highest-priority first.
    """
    parsed = urlparse(normalize_url(domain_url))
    base = f"{parsed.scheme}://{parsed.netloc}"

    seed_scores: dict[str, int] = {}

    def add_seed(path: str, priority: int) -> None:
        normalized_path = "/" + path.strip().strip("/")
        if normalized_path == "/":
            return
        existing = seed_scores.get(normalized_path, -(10**9))
        if priority > existing:
            seed_scores[normalized_path] = priority

    # Core paths valid for almost every university
    for path, priority in {
        "/study/funding": 250,
        "/study/funding/postgraduate": 245,
        "/study/funding/postgraduate-research": 245,
        "/study/postgraduate-research": 240,
        "/study/pg-research": 240,
        "/research": 180,
        "/research/degrees": 225,
        "/doctoral-college": 225,
        "/graduateschool": 210,
        "/study": 140,
    }.items():
        add_seed(path, priority)

    has_doctoral = any(t in constraints.degree_terms for t in ["phd", "doctoral", "doctorate"])
    if has_doctoral:
        for path, priority in {
            "/study/funding/postgraduate-research/funding": 260,
            "/study/pg-research/funding": 260,
            "/study/pg-research/funding/phdfunding": 280,
            "/study/pg-research/funding/phd-funding": 275,
            "/study/funding/award": 205,
            "/research/degrees/funding": 260,
            "/research/degrees/doctoral": 255,
            "/doctoral-college/funding": 255,
        }.items():
            add_seed(path, priority)

        doctoral_hubs = sorted(
            {slugify_path_term(t) for t in DOCTORAL_PATH_HINTS if slugify_path_term(t)}
        )
        funding_hubs = sorted(
            {slugify_path_term(t) for t in FUNDING_PATH_HINTS if slugify_path_term(t)}
        )
        academic_hubs = sorted(
            {slugify_path_term(t) for t in ACADEMIC_HUB_PATH_HINTS if slugify_path_term(t)}
        )

        for hub in academic_hubs:
            add_seed(f"/{hub}", 180)
        for doctoral in doctoral_hubs:
            add_seed(f"/{doctoral}", 210)
            add_seed(f"/study/{doctoral}", 225)
            add_seed(f"/research/{doctoral}", 225)
            for funding in funding_hubs:
                add_seed(f"/study/{doctoral}/{funding}", 240)
                add_seed(f"/research/{doctoral}/{funding}", 235)
                add_seed(f"/{doctoral}/{funding}", 220)
        for hub in academic_hubs:
            for funding in funding_hubs:
                add_seed(f"/{hub}/{funding}", 210)

    subject_path_hints = {
        "machine learning": [
            "/computer-science",
            "/data-science",
            "/ai",
            "/artificial-intelligence",
        ],
        "artificial intelligence": [
            "/computer-science",
            "/data-science",
            "/ai",
            "/artificial-intelligence",
        ],
        "computer science": [
            "/computer-science",
            "/computerscience",
            "/engineering/computer-science",
        ],
    }
    for subject in constraints.subject_terms:
        for suffix in subject_path_hints.get(subject, []):
            add_seed(suffix, 235)
            add_seed(f"/study{suffix}", 240)
            add_seed(f"/research{suffix}", 240)
            if has_doctoral:
                add_seed(f"/study/pg-research{suffix}", 250)
                add_seed(f"/research/degrees{suffix}", 250)
        for variant in path_variants_for_term(subject):
            if not variant:
                continue
            add_seed(f"/{variant}", 190)
            add_seed(f"/study/{variant}", 210)
            add_seed(f"/research/{variant}", 210)
            if has_doctoral:
                add_seed(f"/study/pg-research/{variant}", 235)
                add_seed(f"/study/postgraduate-research/{variant}", 235)
                add_seed(f"/research/degrees/{variant}", 230)
                add_seed(f"/doctoral-college/{variant}", 225)

    ranked = sorted(seed_scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        (normalize_url(urljoin(base, path)), priority) for path, priority in ranked[:MAX_SEED_URLS]
    ]


def build_initial_queue(
    domain_url: str,
    constraints: QueryConstraints,
    max_seed_urls: int = MAX_SEED_URLS,
) -> List[CrawlQueueItem]:
    """Build the starting BFS queue: root URL + seeded priority URLs."""
    queue = [CrawlQueueItem(url=normalize_url(domain_url), priority=0, source="root")]
    queue.extend(
        CrawlQueueItem(url=url, priority=priority, source="seed")
        for url, priority in generate_seed_urls(domain_url, constraints)[:max_seed_urls]
    )
    return queue


def generate_auxiliary_seed_domains(domain_url: str) -> List[str]:
    """Probe common university subdomains that may host scholarship info."""
    normalized = normalize_url(domain_url)
    parsed = urlparse(normalized)
    scope = get_domain_scope(normalized)
    if not scope:
        return []

    current_host = parsed.netloc.lower().replace(":80", "").replace(":443", "")
    auxiliary: List[str] = []
    seen: Set[str] = set()
    for prefix in [
        "ask",
        "scholarships",
        "funding",
        "finance",
        "financialaid",
        "research",
        "graduate",
        "graduateschool",
    ]:
        host = f"{prefix}.{scope}"
        if host == current_host:
            continue
        url = f"{parsed.scheme}://{host}"
        if url not in seen:
            auxiliary.append(url)
            seen.add(url)
    return auxiliary


def build_multi_domain_queue(
    domain_url: str,
    constraints: QueryConstraints,
    extra_seed_domains: List[str],
    max_pages: int,
) -> tuple[List[CrawlQueueItem], Set[str], int]:
    """
    Build the combined initial queue for a domain + its auxiliary subdomains.

    Returns:
        (queue, queued_url_set, required_seed_visits)
    """
    to_visit = build_initial_queue(
        domain_url, constraints, max_seed_urls=min(MAIN_DOMAIN_SEED_LIMIT, max_pages)
    )

    # Deduplicate auxiliary domains
    extra = []
    seen_extra: Set[str] = set()
    for ed in (extra_seed_domains or []) + generate_auxiliary_seed_domains(domain_url):
        norm = normalize_url(ed)
        if norm and norm != normalize_url(domain_url) and norm not in seen_extra:
            seen_extra.add(norm)
            extra.append(norm)
            if len(extra) >= MAX_AUXILIARY_SEED_DOMAINS:
                break

    for ed_url in extra:
        extra_queue = build_initial_queue(
            ed_url, constraints, max_seed_urls=min(AUXILIARY_DOMAIN_SEED_LIMIT, max_pages)
        )
        for item in extra_queue:
            to_visit.append(
                CrawlQueueItem(url=item.url, priority=max(item.priority, 220), source="seed")
            )

    to_visit.sort(key=lambda item: item.priority, reverse=True)
    to_visit = to_visit[:MAX_TOTAL_INITIAL_QUEUE]

    queued_url_set: Set[str] = {item.url for item in to_visit}
    total_seeds = sum(1 for item in to_visit if item.source == "seed")
    required_seed_visits = min((total_seeds + 1) // 2, max_pages)

    return to_visit, queued_url_set, required_seed_visits

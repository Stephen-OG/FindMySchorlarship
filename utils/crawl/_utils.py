"""
Low-level utility functions used throughout the crawl package.

All functions here are pure (no I/O, no async) and have no internal dependencies
within the crawl package — safe to import from any submodule.
"""

import re
from typing import Any, Dict, List
from urllib.parse import urlparse, urlunparse

# ── Text normalization ─────────────────────────────────────────────────────────


def normalized_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("-", " ").replace("_", " ")).strip()


def sanitize_text_for_llm(value: str) -> str:
    """Remove control characters that can break downstream model calls."""
    cleaned = value or ""
    cleaned = cleaned.replace("\x00", " ")
    cleaned = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def sanitize_page_payload(page: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize crawler page payloads before caching or returning them."""
    sanitized = dict(page)
    for key in ("title", "preview", "text", "full_text", "page_type", "url"):
        if key in sanitized and sanitized[key] is not None:
            sanitized[key] = sanitize_text_for_llm(str(sanitized[key]))
    return sanitized


# ── URL path helpers ───────────────────────────────────────────────────────────


def slugify_path_term(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", normalized_match_text(value))
    return cleaned.strip("-")


def path_variants_for_term(value: str) -> List[str]:
    normalized = normalized_match_text(value)
    slug = slugify_path_term(value)
    squashed = slug.replace("-", "")
    variants = {normalized, slug, squashed}
    return [v for v in variants if v]


def keyword_variants_for_matching(keyword: str) -> List[str]:
    cleaned = (keyword or "").strip().lower()
    if not cleaned:
        return []
    variants = {
        cleaned,
        cleaned.replace("-", " "),
        cleaned.replace("_", " "),
        cleaned.replace("-", ""),
        cleaned.replace("_", ""),
    }
    return [v for v in variants if v]


# ── URL normalization ──────────────────────────────────────────────────────────


def normalize_url(url: str) -> str:
    """
    Normalize URL to prevent duplicate fetching.
    - Upgrades http → https
    - Removes fragment (#section)
    - Removes trailing slashes (except root /)
    - Lowercases scheme + host
    - Strips default ports
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"
    return urlunparse(
        (
            scheme,
            parsed.netloc.lower().replace(":80", "").replace(":443", ""),
            parsed.path.rstrip("/") or "/",
            parsed.params,
            parsed.query,
            "",  # strip fragment
        )
    )


def get_base_domain(url: str) -> str:
    """Return the netloc (without www. or default ports) of a URL."""
    parsed = urlparse(url)
    base = parsed.netloc.lower().replace(":80", "").replace(":443", "")
    if base.startswith("www."):
        base = base[4:]
    return base


def get_domain_scope(url: str) -> str:
    """
    Return the university-wide crawl/cache scope.

    Collapses subdomains like ``financialaid.oregonstate.edu`` → ``oregonstate.edu``
    so pages from departmental hosts share the same crawl budget.
    Handles British academic TLDs (*.ac.uk) correctly.
    """
    base = get_base_domain(url)
    if not base:
        return ""
    parts = base.split(".")
    if len(parts) <= 2:
        return base
    if parts[-2:] == ["ac", "uk"] and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_query_cache_key(query, keywords: list | None = None) -> str:
    """
    Build a stable cache key component from a query and/or keyword list.

    Strategy:
    - If keywords are provided: sort them, join, hash → deterministic regardless
      of query phrasing.  "PhD ML funding" and "machine learning doctoral funding"
      produce the same keywords and therefore the same cache key.
    - If only a raw query is provided (legacy path): normalise the string directly.
      Less stable but keeps backward compatibility for callers without keywords.
    """
    if keywords:
        stable = "-".join(sorted(k.lower().strip() for k in keywords if k.strip()))
        return stable
    return normalized_match_text(query or "")


# ── URL depth / domain helpers ─────────────────────────────────────────────────


def calculate_funding_depth(url: str) -> int:
    """Count how many funding-related terms appear in a URL path."""
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


def _normalize_domain_url(domain: str) -> str:
    """Ensure a domain string has an https:// scheme."""
    cleaned = (domain or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return f"https://{cleaned}"

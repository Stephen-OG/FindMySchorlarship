"""
Compatibility shim — utils.crawler is now utils.crawl.

All logic has been moved to utils/crawl/ (modular package).
This file re-exports the public API so any code still importing from
utils.crawler continues to work without changes.
"""

from utils.crawl import (  # noqa: F401
    crawl_universities_formatted,
    crawl_university_funding,
    get_cached_crawl_payload,
)

# Also re-export utility functions that other modules may reference directly
from utils.crawl._utils import (  # noqa: F401
    get_base_domain,
    get_domain_scope,
    normalize_query_cache_key,
    normalize_url,
    sanitize_page_payload,
    sanitize_text_for_llm,
)
from utils.crawl.models import (  # noqa: F401
    CrawlQueueItem,
    PageScoreBreakdown,
    QueryConstraints,
    UniversityInput,
)
from utils.crawl.scorer import (  # noqa: F401
    build_query_constraints,
    create_dynamic_keyword_pattern,
    is_funding_relevant,
    score_link_priority,
)

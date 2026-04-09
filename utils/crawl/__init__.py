"""
utils.crawl — modular university web crawler package.

Public API (imported by the agent layer and analyzer):
    crawl_universities_formatted   — batch crawl @function_tool
    crawl_university_funding       — single-university @function_tool (legacy)
    get_cached_crawl_payload       — async helper for analyzer
"""

from utils.crawl.engine import get_cached_crawl_payload
from utils.crawl.tools import crawl_universities_formatted, crawl_university_funding

__all__ = [
    "crawl_universities_formatted",
    "crawl_university_funding",
    "get_cached_crawl_payload",
]

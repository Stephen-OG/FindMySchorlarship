"""Shared constants for the crawl package."""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

GENERIC_FUNDING_TERMS = {
    "funding",
    "scholarship",
    "scholarships",
    "studentship",
    "studentships",
    "stipend",
    "bursary",
    "bursaries",
    "grant",
    "grants",
    "financial aid",
    "tuition",
    "fee",
    "fees",
    "finance",
    "money support",
    "aid",
    "full-funding",
    "tuition-waiver",
}
GENERIC_QUERY_TERMS = GENERIC_FUNDING_TERMS | {
    "phd",
    "doctoral",
    "doctorate",
    "masters",
    "master",
    "msc",
    "undergraduate",
    "bachelor",
    "international",
    "uk",
}
FUNDING_PREFERENCE_TERMS = {
    "full funding",
    "full-funding",
    "tuition waiver",
    "tuition-waiver",
    "stipend",
    "international",
}
INSTITUTION_HINT_TERMS = {
    "university",
    "college",
    "institute",
    "school",
    "faculty",
    "department",
}

BASE_FUNDING_KEYWORDS = [
    r"ph\.?d",
    r"doctoral",
    r"doctorate",
    r"masters?",
    r"m\.sc",
    r"funding",
    r"scholarship",
    r"studentship",
    r"stipend",
    r"bursary",
    r"grant",
    r"financial aid",
    r"tuition",
    r"fee",
    r"finance",
    r"money support",
    r"aid",
]

FUNDING_URL_PATTERNS = [
    "/funding/",
    "/scholarship/",
    "/financial-aid/",
    "/bursary/",
    "/studentship/",
    "/fees-funding/",
    "/finance/",
    "/grants/",
    "/funding-opportunities/",
    "/scholarships/",
    "/financialsupport/",
    "/pg-research/",
    "/phdfunding/",
]

DOCTORAL_PATH_HINTS = {
    "phd",
    "doctoral",
    "doctorate",
    "pgr",
    "pg research",
    "pg-research",
    "postgraduate research",
    "postgraduate-research",
    "research degree",
    "research degrees",
    "research-degrees",
    "researchdegrees",
}
FUNDING_PATH_HINTS = {
    "funding",
    "fees funding",
    "fees-funding",
    "studentship",
    "studentships",
    "scholarship",
    "scholarships",
    "phd funding",
    "phdfunding",
    "doctoral funding",
    "doctoral-funding",
}
ACADEMIC_HUB_PATH_HINTS = {
    "study",
    "research",
    "graduate school",
    "graduate-school",
    "graduateschool",
    "doctoral college",
    "doctoral-college",
    "postgraduate research",
    "postgraduate-research",
    "pg research",
    "pg-research",
    "research degrees",
    "research-degrees",
    "researchdegrees",
}

# Queue / crawl limits
MAX_SEED_URLS = 60
MAX_AUXILIARY_SEED_DOMAINS = 3
MAX_TOTAL_INITIAL_QUEUE = 50
MAIN_DOMAIN_SEED_LIMIT = 24
AUXILIARY_DOMAIN_SEED_LIMIT = 8
SEARCH_FALLBACK_URL_LIMIT = 8

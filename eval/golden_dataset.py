"""
Golden evaluation dataset for FindMyScholarship AI.

Each GoldenCase defines:
  - query:              The natural language user query
  - universities:       Which schools to search (empty for MCP-path cases)
  - expected_pages:     Known funding URLs that MUST appear in crawl results
  - expected_opps:      Funding opportunity name fragments that MUST appear in output
  - field_checks:       Spot-checks on specific fields (name → {field: expected_value})
  - path:               "university" (crawl path) or "mcp" (national-database path)
  - mcp_tool:           For mcp-path cases: "search_scholarships" | "search_research_grants"
                        | "search_all_funding"
  - mcp_level:          Degree level kwarg for the MCP tool ("doctoral", "postgraduate", etc.)
  - mcp_country:        Country filter kwarg ("us", "uk", "any")

Cases are ordered roughly by complexity: simple → multi-university → MCP-path → edge cases.

NOTE: University-path expected_opps and expected_pages are provisional — they were written
from domain knowledge, NOT from verified pipeline output.  Run the harness once, inspect
returned_opps in the JSON report, manually validate which results are correct, then update
these labels.  Until that validation pass is complete, low recall scores are expected and
do not necessarily indicate a broken pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FieldCheck:
    """Assert a specific field value on a named opportunity."""

    opportunity_name_fragment: str  # partial match against FundingOpportunity.name
    field: str  # field to check (amount, deadline, degree_level, etc.)
    expected_value: str  # expected substring within field value


@dataclass
class GoldenCase:
    id: str
    query: str
    universities: List[str]
    country: Optional[str] = None
    expected_pages: List[str] = field(default_factory=list)
    expected_opps: List[str] = field(default_factory=list)
    field_checks: List[FieldCheck] = field(default_factory=list)
    notes: str = ""
    # Path selector — "university" runs crawl+analyze; "mcp" calls an MCP tool directly.
    path: str = "university"
    mcp_tool: str = ""  # "search_scholarships" | "search_research_grants" | "search_all_funding"
    mcp_level: str = "any"
    mcp_country: str = "any"


GOLDEN_CASES: List[GoldenCase] = [
    # ── 1. MIT — PhD CS ───────────────────────────────────────────────────────
    GoldenCase(
        id="MIT-phd-cs",
        query="PhD funding in computer science at MIT",
        universities=["Massachusetts Institute of Technology"],
        country="USA",
        expected_pages=[
            "https://mit.edu/education/funding",
            "https://oge.mit.edu/finances/fellowships",
        ],
        expected_opps=[
            "MIT Presidential Fellowship",
            "NSF Graduate Research Fellowship",
        ],
        field_checks=[
            FieldCheck("Presidential Fellowship", "degree_level", "PhD"),
        ],
        notes="Flagship US school, should always return fellowship results",
    ),
    # ── 2. Oxford — PhD Biochemistry ─────────────────────────────────────────
    GoldenCase(
        id="Oxford-phd-biochem",
        query="PhD scholarship in biochemistry at University of Oxford",
        universities=["University of Oxford"],
        country="UK",
        expected_pages=[
            "https://ox.ac.uk/admissions/graduate/fees-and-funding",
        ],
        expected_opps=[
            "Clarendon Fund",
            "Wellcome Trust",
            "BBSRC studentship",
        ],
        field_checks=[
            FieldCheck("Clarendon", "for_international", "True"),
        ],
        notes="Clarendon Fund is a well-known Oxford scholarship for international students",
    ),
    # ── 3. Cambridge — Masters Engineering ───────────────────────────────────
    GoldenCase(
        id="Cambridge-masters-eng",
        query="Masters funding in engineering at Cambridge",
        universities=["University of Cambridge"],
        country="UK",
        expected_pages=[
            "https://cam.ac.uk/fees-and-funding",
        ],
        expected_opps=[
            "Gates Cambridge Scholarship",
            "Cambridge Trust",
        ],
        field_checks=[
            FieldCheck("Gates Cambridge", "degree_level", "Masters"),
            FieldCheck("Cambridge Trust", "amount", "full"),
        ],
        notes="Gates Cambridge is one of the most prestigious international scholarships",
    ),
    # ── 4. Stanford — PhD Machine Learning ───────────────────────────────────
    GoldenCase(
        id="Stanford-phd-ml",
        query="full funding PhD machine learning Stanford",
        universities=["Stanford University"],
        country="USA",
        expected_pages=[
            "https://stanford.edu/tuition-aid",
        ],
        expected_opps=[
            "Stanford Graduate Fellowship",
            "Knight-Hennessy Scholars",
        ],
        field_checks=[
            FieldCheck("Graduate Fellowship", "degree_level", "PhD"),
        ],
        notes="Stanford typically guarantees funding for PhD admits",
    ),
    # ── 5. ETH Zurich — PhD Data Science ─────────────────────────────────────
    GoldenCase(
        id="ETH-phd-datascience",
        query="PhD data science funding ETH Zurich international students",
        universities=["ETH Zurich"],
        country="Switzerland",
        expected_pages=[
            "https://ethz.ch/en/studies/financial.html",
        ],
        expected_opps=[
            "ETH Fellowship",
            "Excellence Scholarship",
        ],
        field_checks=[
            FieldCheck("ETH Fellowship", "for_international", "True"),
        ],
        notes="ETH has a flagship excellence scholarship program",
    ),
    # ── 6. University of Toronto — Masters AI ────────────────────────────────
    GoldenCase(
        id="Toronto-masters-ai",
        query="Masters in artificial intelligence funding University of Toronto",
        universities=["University of Toronto"],
        country="Canada",
        expected_opps=[
            "Ontario Graduate Scholarship",
            "Vanier Canada Graduate Scholarship",
        ],
        notes="Canadian provincial + federal scholarship programs",
    ),
    # ── 7. NUS — PhD Environmental Science ───────────────────────────────────
    GoldenCase(
        id="NUS-phd-environment",
        query="PhD scholarship environmental science National University of Singapore international",
        universities=["National University of Singapore"],
        country="Singapore",
        expected_opps=[
            "NUS Research Scholarship",
            "Singapore International Graduate Award",
        ],
        field_checks=[
            FieldCheck("Singapore International", "for_international", "True"),
        ],
        notes="SINGA is the flagship international PhD scholarship in Singapore",
    ),
    # ── 8. Edinburgh — PhD Informatics ───────────────────────────────────────
    GoldenCase(
        id="Edinburgh-phd-informatics",
        query="PhD informatics studentship University of Edinburgh",
        universities=["University of Edinburgh"],
        country="UK",
        expected_opps=[
            "EPSRC DTP",
            "School of Informatics Studentship",
        ],
        notes="Edinburgh has strong UKRI-funded doctoral training partnerships",
    ),
    # ── 9. Melbourne — PhD Biomedical ────────────────────────────────────────
    GoldenCase(
        id="Melbourne-phd-biomedical",
        query="PhD biomedical science scholarship University of Melbourne",
        universities=["University of Melbourne"],
        country="Australia",
        expected_opps=[
            "Investigating the epigenetic basis of monocyte exhaustion memory following sepsis",
            "Characterizing alcohol and stress induced changes in dorsomedial frontal cortex function",
            "Discovering lipid transport mechanisms in Mycobacterium tuberculosis",
            "Defining mechanisms for induction of antibacterial lung-resident CD4 T cells",
            "The inhibitory mechanism underlying psychedelic disruption of visual processing",
            "Molecular Investigation of Bacterial Penicillin-Binding Protein Activity and Inhibition",
            "Neural and Cognitive Mechanisms Underlying Perseverance and Their Disruption After Juvenile Social Play Deprivation",
            "Analyzing the Distribution, Effectiveness, and Implementation of Wildfire Smoke Exposure Reduction Strategies on Respiratory Health",
            "Understanding Multidrug-Resistant Pathogen Infections and their Treatment with Antibiotics and Bacteriophages",
            "Mechanisms of axoglial interactions at the paranodal junction",
        ],
        field_checks=[
            FieldCheck("Research Training Program", "amount", "tuition"),
        ],
        notes="RTP scholarships cover tuition + living stipend for domestic and international",
    ),
    # ── 10. UCL — PhD Neuroscience ────────────────────────────────────────────
    GoldenCase(
        id="UCL-phd-neuro",
        query="PhD neuroscience funding UCL international students",
        universities=["University College London"],
        country="UK",
        expected_opps=[
            "UCL Graduate Research Scholarship",
            "Wellcome Trust PhD Programme",
        ],
        field_checks=[
            FieldCheck("Graduate Research Scholarship", "for_international", "True"),
        ],
        notes="UCL offers partial and full funding for international PhD students",
    ),
    # ── 11. Multi-university — Masters Economics ──────────────────────────────
    GoldenCase(
        id="multi-masters-econ",
        query="Masters economics full funding top UK universities",
        universities=[
            "University of Oxford",
            "London School of Economics",
            "University of Cambridge",
        ],
        country="UK",
        expected_opps=[
            "Clarendon Fund",
            "LSE PhD Studentship",
            "Gates Cambridge",
        ],
        notes="Multi-school query — should return results from all three",
    ),
    # ── 12. Multi-university — PhD Physics ───────────────────────────────────
    GoldenCase(
        id="multi-phd-physics",
        query="PhD physics fully funded stipend USA top universities",
        universities=["MIT", "Stanford University", "California Institute of Technology"],
        country="USA",
        expected_opps=[
            "NSF Graduate Research Fellowship",
        ],
        notes="NSF GRF is common across all top US programs",
    ),
    # ── 13. Imperial — PhD Electrical Engineering ─────────────────────────────
    GoldenCase(
        id="Imperial-phd-ee",
        query="fully funded PhD electrical engineering Imperial College London",
        universities=["Imperial College London"],
        country="UK",
        expected_opps=[
            "President's PhD Scholarship",
            "EPSRC studentship",
        ],
        field_checks=[
            FieldCheck("President's PhD", "amount", "full"),
        ],
        notes="Imperial's President's Scholarship is a flagship fully-funded award",
    ),
    # ── 14. Carnegie Mellon — PhD Robotics ────────────────────────────────────
    GoldenCase(
        id="CMU-phd-robotics",
        query="PhD robotics funding Carnegie Mellon University",
        universities=["Carnegie Mellon University"],
        country="USA",
        expected_opps=[
            "RI PhD Fellowship",
        ],
        notes="CMU Robotics Institute has dedicated PhD fellowships",
    ),
    # ── 15. Waterloo — PhD Quantum Computing ─────────────────────────────────
    GoldenCase(
        id="Waterloo-phd-quantum",
        query="PhD quantum computing scholarship University of Waterloo",
        universities=["University of Waterloo"],
        country="Canada",
        expected_opps=[
            "Quantum Information Graduate Scholarship",
            "NSERC Postgraduate Scholarship",
        ],
        notes="Waterloo's IQC is a world-leading quantum institute",
    ),
    # ── 16. TU Munich — PhD Mechanical Engineering ───────────────────────────
    GoldenCase(
        id="TUM-phd-mech",
        query="PhD mechanical engineering scholarship TU Munich international",
        universities=["Technical University of Munich"],
        country="Germany",
        expected_opps=[
            "DAAD Scholarship",
            "TUM Graduate School Fellowship",
        ],
        field_checks=[
            FieldCheck("DAAD", "for_international", "True"),
        ],
        notes="DAAD is the main German international scholarship provider",
    ),
    # ── 17. McGill — Masters Public Health ────────────────────────────────────
    GoldenCase(
        id="McGill-masters-health",
        query="Masters public health funding McGill University Canada",
        universities=["McGill University"],
        country="Canada",
        expected_opps=[
            "McGill Entrance Excellence Award",
            "CIHR Master's Award",
        ],
        notes="Canadian health research council funds graduate studies",
    ),
    # ── 20. Harvard — PhD Law full funding ───────────────────────────────────
    GoldenCase(
        id="Harvard-phd-law",
        query="PhD law full funding Harvard international students",
        universities=["Harvard University"],
        country="USA",
        expected_opps=[
            "Harvard Law School Fellowship",
            "Sheldon Fellowship",
        ],
        field_checks=[
            FieldCheck("Law School Fellowship", "for_international", "True"),
        ],
        notes="Harvard Law has dedicated PhD fellowships with full tuition + stipend",
    ),
    # ── MCP path: national database cases ─────────────────────────────────────
    # These exercise search_scholarships / search_research_grants / search_all_funding
    # directly, bypassing the crawl+analyze path.  expected_opps are fragment terms
    # matched against the text of each returned result title.
    # Labels are provisional — validate after first run.
    GoldenCase(
        id="mcp-uk-phd-ml",
        query="PhD scholarships in machine learning UK",
        universities=[],
        country="UK",
        path="mcp",
        mcp_tool="search_scholarships",
        mcp_level="doctoral",
        mcp_country="uk",
        expected_opps=["EPSRC", "studentship", "PhD"],
        notes="UK EPSRC doctoral training grants should appear in UKRI + jobs.ac.uk results",
    ),
    GoldenCase(
        id="mcp-us-postdoc-biomed",
        query="postdoctoral fellowships in biomedical research USA",
        universities=[],
        country="USA",
        path="mcp",
        mcp_tool="search_research_grants",
        mcp_level="postdoctoral",
        mcp_country="us",
        expected_opps=[
            "Investigating the epigenetic basis of monocyte exhaustion memory following sepsis",
            "Characterizing alcohol and stress induced changes in dorsomedial frontal cortex function",
            "Discovering lipid transport mechanisms in Mycobacterium tuberculosis",
            "Defining mechanisms for induction of antibacterial lung-resident CD4 T cells",
            "The inhibitory mechanism underlying psychedelic disruption of visual processing",
            "Molecular Investigation of Bacterial Penicillin-Binding Protein Activity and Inhibition",
            "Neural and Cognitive Mechanisms Underlying Perseverance and Their Disruption After Juvenile Social Play Deprivation",
            "Analyzing the Distribution, Effectiveness, and Implementation of Wildfire Smoke Exposure Reduction Strategies on Respiratory Health",
            "Understanding Multidrug-Resistant Pathogen Infections and their Treatment with Antibiotics and Bacteriophages",
            "Mechanisms of axoglial interactions at the paranodal junction",
        ],
        notes="NIH Reporter should return biomedical postdoctoral grants",
    ),
    GoldenCase(
        id="mcp-any-phd-cs",
        query="PhD funding computer science international",
        universities=[],
        country=None,
        path="mcp",
        mcp_tool="search_scholarships",
        mcp_level="doctoral",
        mcp_country="any",
        expected_opps=["scholarship", "PhD", "doctoral"],
        notes="Cross-country PhD CS search — both Grants.gov and UKRI should return results",
    ),
    GoldenCase(
        id="mcp-uk-phd-studentships",
        query="funded PhD studentships engineering UK",
        universities=[],
        country="UK",
        path="mcp",
        mcp_tool="search_scholarships",
        mcp_level="doctoral",
        mcp_country="uk",
        expected_opps=["studentship", "engineering", "funded"],
        notes="jobs.ac.uk should return current funded PhD adverts for engineering",
    ),
    GoldenCase(
        id="mcp-us-phd-all-funding",
        query="PhD fellowships data science United States",
        universities=[],
        country="USA",
        path="mcp",
        mcp_tool="search_all_funding",
        mcp_level="doctoral",
        mcp_country="us",
        expected_opps=["fellowship", "data", "science"],
        notes="search_all_funding combines Grants.gov + NIH + UKRI — at least one source should hit",
    ),
    # ── Edge cases ─────────────────────────────────────────────────────────────
    GoldenCase(
        id="edge-unknown-uni",
        query="PhD funding University of Tartu Estonia",
        universities=["University of Tartu"],
        country="Estonia",
        expected_opps=[],  # May return empty — valid result
        notes="Edge case: less prominent university not in curated DB. Should not crash.",
    ),
    GoldenCase(
        id="edge-no-funding",
        query="undergraduate bursary for fine arts at York University",
        universities=["University of York"],
        country="UK",
        expected_opps=[],  # Deliberately obscure — pass if pipeline completes
        notes="Edge case: niche query. Eval passes if pipeline doesn't error.",
    ),
]


def get_case(case_id: str) -> Optional[GoldenCase]:
    """Return a single golden case by ID, or None if not found."""
    for c in GOLDEN_CASES:
        if c.id == case_id:
            return c
    return None


def list_case_ids() -> List[str]:
    return [c.id for c in GOLDEN_CASES]

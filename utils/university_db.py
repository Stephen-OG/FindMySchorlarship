"""
Curated university domain database for FindMyScholarship AI.

Provides instant, zero-cost domain lookups for ~200 top universities worldwide.
Used as Tier 1 in the tiered domain discovery chain:
    Tier 1: university_db  (this file — instant, free)
    Tier 2: DuckDuckGo     (free, no API key)
    Tier 3: SerpAPI        (paid, only as last resort)

Lookup is token-based: query tokens from the school name are matched against
the canonical name and aliases. Partial matches are ranked by token coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class UniversityEntry:
    canonical_name: str
    domains: List[str]  # Primary domains (https://...)
    aliases: List[str] = field(default_factory=list)  # Common abbreviations / alt names
    country: Optional[str] = None


# ── Database ───────────────────────────────────────────────────────────────────
# fmt: off
_DB: List[UniversityEntry] = [
    # ── United States ────────────────────────────────────────────────────────
    UniversityEntry("Massachusetts Institute of Technology", ["https://mit.edu"], ["MIT"], "USA"),
    UniversityEntry("Stanford University", ["https://stanford.edu"], ["Stanford"], "USA"),
    UniversityEntry("Harvard University", ["https://harvard.edu"], ["Harvard"], "USA"),
    UniversityEntry("California Institute of Technology", ["https://caltech.edu"], ["Caltech"], "USA"),
    UniversityEntry("Princeton University", ["https://princeton.edu"], ["Princeton"], "USA"),
    UniversityEntry("Columbia University", ["https://columbia.edu"], ["Columbia"], "USA"),
    UniversityEntry("University of Chicago", ["https://uchicago.edu"], ["UChicago", "UofC"], "USA"),
    UniversityEntry("Yale University", ["https://yale.edu"], ["Yale"], "USA"),
    UniversityEntry("Cornell University", ["https://cornell.edu"], ["Cornell"], "USA"),
    UniversityEntry("University of Pennsylvania", ["https://upenn.edu"], ["UPenn", "Penn"], "USA"),
    UniversityEntry("Duke University", ["https://duke.edu"], ["Duke"], "USA"),
    UniversityEntry("Johns Hopkins University", ["https://jhu.edu", "https://johnshopkins.edu"], ["JHU", "Hopkins"], "USA"),
    UniversityEntry("University of California Berkeley", ["https://berkeley.edu", "https://ucberkeley.edu"], ["UC Berkeley", "UCB", "Cal"], "USA"),
    UniversityEntry("University of California Los Angeles", ["https://ucla.edu"], ["UCLA"], "USA"),
    UniversityEntry("University of California San Diego", ["https://ucsd.edu"], ["UCSD", "UC San Diego"], "USA"),
    UniversityEntry("University of California San Francisco", ["https://ucsf.edu"], ["UCSF"], "USA"),
    UniversityEntry("University of Michigan", ["https://umich.edu"], ["UMich", "Michigan"], "USA"),
    UniversityEntry("Northwestern University", ["https://northwestern.edu"], ["Northwestern"], "USA"),
    UniversityEntry("Carnegie Mellon University", ["https://cmu.edu"], ["CMU"], "USA"),
    UniversityEntry("New York University", ["https://nyu.edu"], ["NYU"], "USA"),
    UniversityEntry("University of Texas Austin", ["https://utexas.edu", "https://ut.edu"], ["UT Austin", "UT"], "USA"),
    UniversityEntry("University of Washington", ["https://uw.edu", "https://washington.edu"], ["UW", "UWash"], "USA"),
    UniversityEntry("Georgia Institute of Technology", ["https://gatech.edu"], ["Georgia Tech", "GT"], "USA"),
    UniversityEntry("University of Illinois Urbana-Champaign", ["https://illinois.edu"], ["UIUC", "Illinois"], "USA"),
    UniversityEntry("University of Wisconsin Madison", ["https://wisc.edu"], ["UW-Madison", "Wisconsin"], "USA"),
    UniversityEntry("Ohio State University", ["https://osu.edu"], ["OSU", "Ohio State"], "USA"),
    UniversityEntry("Pennsylvania State University", ["https://psu.edu"], ["Penn State", "PSU"], "USA"),
    UniversityEntry("University of North Carolina Chapel Hill", ["https://unc.edu"], ["UNC", "Chapel Hill"], "USA"),
    UniversityEntry("Boston University", ["https://bu.edu"], ["BU"], "USA"),
    UniversityEntry("Purdue University", ["https://purdue.edu"], ["Purdue"], "USA"),
    UniversityEntry("University of Minnesota", ["https://umn.edu"], ["UMN", "Minnesota"], "USA"),
    UniversityEntry("University of Colorado Boulder", ["https://colorado.edu"], ["CU Boulder", "UCB"], "USA"),
    UniversityEntry("University of Arizona", ["https://arizona.edu"], ["UA", "UArizona"], "USA"),
    UniversityEntry("Michigan State University", ["https://msu.edu"], ["MSU"], "USA"),
    UniversityEntry("Brown University", ["https://brown.edu"], ["Brown"], "USA"),
    UniversityEntry("Dartmouth College", ["https://dartmouth.edu"], ["Dartmouth"], "USA"),
    UniversityEntry("Vanderbilt University", ["https://vanderbilt.edu"], ["Vanderbilt"], "USA"),
    UniversityEntry("Rice University", ["https://rice.edu"], ["Rice"], "USA"),
    UniversityEntry("Emory University", ["https://emory.edu"], ["Emory"], "USA"),
    UniversityEntry("University of Notre Dame", ["https://nd.edu"], ["Notre Dame", "ND"], "USA"),
    UniversityEntry("Tufts University", ["https://tufts.edu"], ["Tufts"], "USA"),
    UniversityEntry("Georgetown University", ["https://georgetown.edu"], ["Georgetown"], "USA"),
    UniversityEntry("University of Southern California", ["https://usc.edu"], ["USC"], "USA"),
    UniversityEntry("University of Virginia", ["https://virginia.edu"], ["UVA"], "USA"),
    UniversityEntry("Wake Forest University", ["https://wfu.edu"], ["Wake Forest"], "USA"),
    UniversityEntry("University of Florida", ["https://ufl.edu"], ["UF", "UFlorida"], "USA"),
    UniversityEntry("University of California Davis", ["https://ucdavis.edu"], ["UC Davis", "UCD"], "USA"),
    UniversityEntry("University of California Santa Barbara", ["https://ucsb.edu"], ["UCSB", "UC Santa Barbara"], "USA"),
    UniversityEntry("University of California Irvine", ["https://uci.edu"], ["UCI", "UC Irvine"], "USA"),
    UniversityEntry("Arizona State University", ["https://asu.edu"], ["ASU"], "USA"),
    UniversityEntry("Oregon State University", ["https://oregonstate.edu"], ["OSU", "Oregon State"], "USA"),
    UniversityEntry("University of Oregon", ["https://uoregon.edu"], ["UO", "Oregon"], "USA"),
    UniversityEntry("Texas A&M University", ["https://tamu.edu"], ["TAMU", "Texas A&M"], "USA"),
    UniversityEntry("Rutgers University", ["https://rutgers.edu"], ["Rutgers"], "USA"),

    # ── United Kingdom ───────────────────────────────────────────────────────
    UniversityEntry("University of Oxford", ["https://ox.ac.uk"], ["Oxford"], "UK"),
    UniversityEntry("University of Cambridge", ["https://cam.ac.uk"], ["Cambridge"], "UK"),
    UniversityEntry("Imperial College London", ["https://imperial.ac.uk"], ["Imperial", "ICL"], "UK"),
    UniversityEntry("University College London", ["https://ucl.ac.uk"], ["UCL"], "UK"),
    UniversityEntry("London School of Economics", ["https://lse.ac.uk"], ["LSE"], "UK"),
    UniversityEntry("University of Edinburgh", ["https://ed.ac.uk"], ["Edinburgh"], "UK"),
    UniversityEntry("University of Manchester", ["https://manchester.ac.uk"], ["Manchester"], "UK"),
    UniversityEntry("King's College London", ["https://kcl.ac.uk"], ["KCL", "King's"], "UK"),
    UniversityEntry("University of Bristol", ["https://bristol.ac.uk"], ["Bristol"], "UK"),
    UniversityEntry("University of Warwick", ["https://warwick.ac.uk"], ["Warwick"], "UK"),
    UniversityEntry("University of Glasgow", ["https://gla.ac.uk"], ["Glasgow"], "UK"),
    UniversityEntry("University of Birmingham", ["https://birmingham.ac.uk"], ["Birmingham"], "UK"),
    UniversityEntry("University of Sheffield", ["https://sheffield.ac.uk"], ["Sheffield"], "UK"),
    UniversityEntry("University of Nottingham", ["https://nottingham.ac.uk"], ["Nottingham"], "UK"),
    UniversityEntry("University of Southampton", ["https://soton.ac.uk"], ["Southampton"], "UK"),
    UniversityEntry("University of Leeds", ["https://leeds.ac.uk"], ["Leeds"], "UK"),
    UniversityEntry("University of Liverpool", ["https://liverpool.ac.uk"], ["Liverpool"], "UK"),
    UniversityEntry("Durham University", ["https://durham.ac.uk"], ["Durham"], "UK"),
    UniversityEntry("University of St Andrews", ["https://st-andrews.ac.uk"], ["St Andrews"], "UK"),
    UniversityEntry("University of Bath", ["https://bath.ac.uk"], ["Bath"], "UK"),
    UniversityEntry("University of Exeter", ["https://exeter.ac.uk"], ["Exeter"], "UK"),
    UniversityEntry("Queen Mary University of London", ["https://qmul.ac.uk"], ["QMUL", "Queen Mary"], "UK"),
    UniversityEntry("University of York", ["https://york.ac.uk"], ["York"], "UK"),
    UniversityEntry("Lancaster University", ["https://lancaster.ac.uk"], ["Lancaster"], "UK"),
    UniversityEntry("University of Leicester", ["https://leicester.ac.uk"], ["Leicester"], "UK"),
    UniversityEntry("University of Surrey", ["https://surrey.ac.uk"], ["Surrey"], "UK"),
    UniversityEntry("University of Aberdeen", ["https://abdn.ac.uk"], ["Aberdeen"], "UK"),
    UniversityEntry("Cardiff University", ["https://cardiff.ac.uk"], ["Cardiff"], "UK"),
    UniversityEntry("Queen's University Belfast", ["https://qub.ac.uk"], ["QUB", "Queen's Belfast"], "UK"),
    UniversityEntry("Newcastle University", ["https://ncl.ac.uk"], ["Newcastle"], "UK"),
    UniversityEntry("University of Hertfordshire", ["https://www.herts.ac.uk"], ["Hertfordshire", "Herts", "UH"], "UK"),
    UniversityEntry("University of Reading", ["https://reading.ac.uk"], ["Reading"], "UK"),
    UniversityEntry("University of Strathclyde", ["https://strath.ac.uk"], ["Strathclyde"], "UK"),
    UniversityEntry("University of Dundee", ["https://dundee.ac.uk"], ["Dundee"], "UK"),
    UniversityEntry("Loughborough University", ["https://lboro.ac.uk"], ["Loughborough", "Lboro"], "UK"),
    UniversityEntry("Brunel University London", ["https://brunel.ac.uk"], ["Brunel"], "UK"),
    UniversityEntry("University of East Anglia", ["https://uea.ac.uk"], ["UEA", "East Anglia"], "UK"),
    UniversityEntry("Swansea University", ["https://swansea.ac.uk"], ["Swansea"], "UK"),
    UniversityEntry("University of Kent", ["https://kent.ac.uk"], ["Kent"], "UK"),
    UniversityEntry("University of Essex", ["https://essex.ac.uk"], ["Essex"], "UK"),
    UniversityEntry("Coventry University", ["https://coventry.ac.uk"], ["Coventry"], "UK"),
    UniversityEntry("University of Portsmouth", ["https://port.ac.uk"], ["Portsmouth"], "UK"),
    UniversityEntry("University of Plymouth", ["https://plymouth.ac.uk"], ["Plymouth"], "UK"),
    UniversityEntry("Keele University", ["https://keele.ac.uk"], ["Keele"], "UK"),
    UniversityEntry("University of Huddersfield", ["https://hud.ac.uk"], ["Huddersfield"], "UK"),
    UniversityEntry("De Montfort University", ["https://dmu.ac.uk"], ["DMU", "De Montfort"], "UK"),
    UniversityEntry("Northumbria University", ["https://northumbria.ac.uk"], ["Northumbria"], "UK"),
    UniversityEntry("University of Salford", ["https://salford.ac.uk"], ["Salford"], "UK"),
    UniversityEntry("University of Ulster", ["https://ulster.ac.uk"], ["Ulster"], "UK"),
    UniversityEntry("Heriot-Watt University", ["https://hw.ac.uk"], ["Heriot-Watt", "HWU"], "UK"),
    UniversityEntry("University of Lincoln", ["https://lincoln.ac.uk"], ["Lincoln"], "UK"),

    # ── Canada ───────────────────────────────────────────────────────────────
    UniversityEntry("University of Toronto", ["https://utoronto.ca"], ["UofT", "UToronto"], "Canada"),
    UniversityEntry("McGill University", ["https://mcgill.ca"], ["McGill"], "Canada"),
    UniversityEntry("University of British Columbia", ["https://ubc.ca"], ["UBC"], "Canada"),
    UniversityEntry("University of Alberta", ["https://ualberta.ca"], ["UAlberta", "Alberta"], "Canada"),
    UniversityEntry("University of Waterloo", ["https://uwaterloo.ca"], ["Waterloo", "UWaterloo"], "Canada"),
    UniversityEntry("University of Montreal", ["https://umontreal.ca"], ["UdeM", "Montreal"], "Canada"),
    UniversityEntry("McMaster University", ["https://mcmaster.ca"], ["McMaster"], "Canada"),
    UniversityEntry("Queen's University", ["https://queensu.ca"], ["Queen's Canada"], "Canada"),
    UniversityEntry("Western University", ["https://uwo.ca"], ["UWO", "Western Ontario"], "Canada"),
    UniversityEntry("Dalhousie University", ["https://dal.ca"], ["Dal", "Dalhousie"], "Canada"),
    UniversityEntry("Simon Fraser University", ["https://sfu.ca"], ["SFU"], "Canada"),
    UniversityEntry("University of Ottawa", ["https://uottawa.ca"], ["Ottawa", "uOttawa"], "Canada"),
    UniversityEntry("University of Calgary", ["https://ucalgary.ca"], ["UCalgary", "Calgary"], "Canada"),

    # ── Australia ────────────────────────────────────────────────────────────
    UniversityEntry("University of Melbourne", ["https://unimelb.edu.au"], ["UniMelb", "Melbourne"], "Australia"),
    UniversityEntry("Australian National University", ["https://anu.edu.au"], ["ANU"], "Australia"),
    UniversityEntry("University of Sydney", ["https://sydney.edu.au"], ["USyd", "Sydney"], "Australia"),
    UniversityEntry("University of Queensland", ["https://uq.edu.au"], ["UQ", "Queensland"], "Australia"),
    UniversityEntry("University of New South Wales", ["https://unsw.edu.au"], ["UNSW"], "Australia"),
    UniversityEntry("Monash University", ["https://monash.edu"], ["Monash"], "Australia"),
    UniversityEntry("University of Western Australia", ["https://uwa.edu.au"], ["UWA"], "Australia"),
    UniversityEntry("University of Adelaide", ["https://adelaide.edu.au"], ["Adelaide"], "Australia"),

    # ── Europe ───────────────────────────────────────────────────────────────
    UniversityEntry("ETH Zurich", ["https://ethz.ch"], ["ETH", "Swiss Federal Institute of Technology"], "Switzerland"),
    UniversityEntry("EPFL", ["https://epfl.ch"], ["Ecole Polytechnique Federale de Lausanne"], "Switzerland"),
    UniversityEntry("University of Zurich", ["https://uzh.ch"], ["UZH"], "Switzerland"),
    UniversityEntry("Technical University of Munich", ["https://tum.de"], ["TUM", "TU Munich"], "Germany"),
    UniversityEntry("Ludwig Maximilian University Munich", ["https://lmu.de"], ["LMU Munich", "LMU"], "Germany"),
    UniversityEntry("Heidelberg University", ["https://uni-heidelberg.de"], ["Heidelberg"], "Germany"),
    UniversityEntry("Humboldt University Berlin", ["https://hu-berlin.de"], ["HU Berlin", "Humboldt Berlin"], "Germany"),
    UniversityEntry("Free University Berlin", ["https://fu-berlin.de"], ["FU Berlin", "Freie Universitat Berlin"], "Germany"),
    UniversityEntry("University of Amsterdam", ["https://uva.nl"], ["UvA", "Amsterdam"], "Netherlands"),
    UniversityEntry("Delft University of Technology", ["https://tudelft.nl"], ["TU Delft", "Delft"], "Netherlands"),
    UniversityEntry("Leiden University", ["https://leiden.edu", "https://universiteitleiden.nl"], ["Leiden"], "Netherlands"),
    UniversityEntry("Utrecht University", ["https://uu.nl"], ["UU", "Utrecht"], "Netherlands"),
    UniversityEntry("KU Leuven", ["https://kuleuven.be"], ["Leuven", "Catholic University Leuven"], "Belgium"),
    UniversityEntry("Ghent University", ["https://ugent.be"], ["UGent", "Ghent"], "Belgium"),
    UniversityEntry("University of Copenhagen", ["https://ku.dk"], ["KU", "Copenhagen"], "Denmark"),
    UniversityEntry("Technical University of Denmark", ["https://dtu.dk"], ["DTU"], "Denmark"),
    UniversityEntry("University of Oslo", ["https://uio.no"], ["UiO", "Oslo"], "Norway"),
    UniversityEntry("University of Stockholm", ["https://su.se"], ["SU", "Stockholm"], "Sweden"),
    UniversityEntry("KTH Royal Institute of Technology", ["https://kth.se"], ["KTH"], "Sweden"),
    UniversityEntry("Karolinska Institute", ["https://ki.se"], ["KI", "Karolinska"], "Sweden"),
    UniversityEntry("University of Helsinki", ["https://helsinki.fi"], ["UH", "Helsinki"], "Finland"),
    UniversityEntry("Aalto University", ["https://aalto.fi"], ["Aalto"], "Finland"),
    UniversityEntry("Paris Sciences et Lettres", ["https://psl.eu"], ["PSL", "PSL University"], "France"),
    UniversityEntry("Sorbonne University", ["https://sorbonne-universite.fr"], ["Sorbonne", "Paris Sorbonne"], "France"),
    UniversityEntry("University of Paris", ["https://u-paris.fr"], ["Paris Cite", "Paris Descartes"], "France"),
    UniversityEntry("Ecole Polytechnique", ["https://polytechnique.edu"], ["X", "Polytechnique France"], "France"),
    UniversityEntry("Bologna University", ["https://unibo.it"], ["Unibo", "Bologna"], "Italy"),
    UniversityEntry("Sapienza University of Rome", ["https://uniroma1.it"], ["Sapienza", "La Sapienza"], "Italy"),
    UniversityEntry("University of Barcelona", ["https://ub.edu"], ["UB", "Barcelona"], "Spain"),
    UniversityEntry("Complutense University Madrid", ["https://ucm.es"], ["UCM", "Complutense"], "Spain"),
    UniversityEntry("Charles University", ["https://cuni.cz"], ["Charles University Prague"], "Czech Republic"),
    UniversityEntry("University of Warsaw", ["https://uw.edu.pl"], ["UW Poland", "Warsaw"], "Poland"),
    UniversityEntry("Jagiellonian University", ["https://uj.edu.pl"], ["Jagiellonian", "Krakow"], "Poland"),

    # ── Asia ─────────────────────────────────────────────────────────────────
    UniversityEntry("National University of Singapore", ["https://nus.edu.sg"], ["NUS", "Singapore"], "Singapore"),
    UniversityEntry("Nanyang Technological University", ["https://ntu.edu.sg"], ["NTU", "Nanyang"], "Singapore"),
    UniversityEntry("University of Tokyo", ["https://u-tokyo.ac.jp"], ["UTokyo", "Tokyo"], "Japan"),
    UniversityEntry("Kyoto University", ["https://kyoto-u.ac.jp"], ["Kyodai", "Kyoto"], "Japan"),
    UniversityEntry("Osaka University", ["https://osaka-u.ac.jp"], ["Handai", "Osaka"], "Japan"),
    UniversityEntry("Tohoku University", ["https://tohoku.ac.jp"], ["Tohoku"], "Japan"),
    UniversityEntry("Tokyo Institute of Technology", ["https://titech.ac.jp"], ["Tokyo Tech", "TiTech"], "Japan"),
    UniversityEntry("Seoul National University", ["https://snu.ac.kr"], ["SNU", "Seoul National"], "South Korea"),
    UniversityEntry("KAIST", ["https://kaist.ac.kr"], ["Korea Advanced Institute of Science"], "South Korea"),
    UniversityEntry("Peking University", ["https://pku.edu.cn"], ["PKU", "Beida"], "China"),
    UniversityEntry("Tsinghua University", ["https://tsinghua.edu.cn"], ["THU", "Qinghua"], "China"),
    UniversityEntry("Fudan University", ["https://fudan.edu.cn"], ["Fudan"], "China"),
    UniversityEntry("Shanghai Jiao Tong University", ["https://sjtu.edu.cn"], ["SJTU"], "China"),
    UniversityEntry("Zhejiang University", ["https://zju.edu.cn"], ["ZJU", "Zhejiang"], "China"),
    UniversityEntry("University of Science and Technology of China", ["https://ustc.edu.cn"], ["USTC"], "China"),
    UniversityEntry("Hong Kong University of Science and Technology", ["https://ust.hk"], ["HKUST"], "Hong Kong"),
    UniversityEntry("University of Hong Kong", ["https://hku.hk"], ["HKU", "Hong Kong"], "Hong Kong"),
    UniversityEntry("Chinese University of Hong Kong", ["https://cuhk.edu.hk"], ["CUHK"], "Hong Kong"),
    UniversityEntry("Indian Institute of Technology Bombay", ["https://iitb.ac.in"], ["IIT Bombay", "IITB"], "India"),
    UniversityEntry("Indian Institute of Technology Delhi", ["https://iitd.ac.in"], ["IIT Delhi", "IITD"], "India"),
    UniversityEntry("Indian Institute of Science", ["https://iisc.ac.in"], ["IISc", "Bangalore"], "India"),
    UniversityEntry("University of Delhi", ["https://du.ac.in"], ["DU", "Delhi University"], "India"),
    UniversityEntry("Jawaharlal Nehru University", ["https://jnu.ac.in"], ["JNU"], "India"),
]
# fmt: on


# ── Lookup machinery ───────────────────────────────────────────────────────────
_STOPWORDS: Set[str] = {
    "of",
    "the",
    "and",
    "for",
    "at",
    "in",
    "on",
    "de",
    "la",
    "le",
    "university",
    "college",
    "institute",
    "school",
    "faculty",
}


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


def _build_index() -> Dict[str, List[int]]:
    """Build a token → [entry indices] inverted index for fast lookup."""
    index: Dict[str, List[int]] = {}
    for i, entry in enumerate(_DB):
        sources = [entry.canonical_name] + entry.aliases
        for source in sources:
            for token in _tokenize(source):
                index.setdefault(token, []).append(i)
    return index


_INDEX: Dict[str, List[int]] = _build_index()


def lookup_university(school: str, country: Optional[str] = None) -> List[str]:
    """
    Return domain URLs for a university from the curated database.

    Matching strategy:
    1. Tokenize the query school name (remove stopwords).
    2. Score each DB entry by how many query tokens it matches.
    3. Optionally filter by country.
    4. Return domains for entries with the highest token overlap (>= 50%).

    Returns empty list if no confident match found.
    """
    query_tokens = _tokenize(school)
    if not query_tokens:
        return []

    # Count how many query tokens match each entry
    scores: Dict[int, int] = {}
    for token in query_tokens:
        for idx in _INDEX.get(token, []):
            scores[idx] = scores.get(idx, 0) + 1

    if not scores:
        return []

    best_score = max(scores.values())
    # Require matching at least 50% of query tokens, or at least 1 if query is short
    threshold = max(1, len(query_tokens) // 2)
    if best_score < threshold:
        return []

    # Gather all entries at best (or near-best) score
    candidates = [
        idx for idx, score in scores.items() if score >= threshold and score >= best_score - 1
    ]

    # Filter by country if provided
    if country:
        country_lower = country.lower().strip()
        country_filtered = [
            idx
            for idx in candidates
            if _DB[idx].country and _DB[idx].country.lower() == country_lower
        ]
        if country_filtered:
            candidates = country_filtered

    # Precompute each entry's total token count for precision tie-breaking.
    # Precision = matched_tokens / entry_total_tokens.  Higher precision means
    # the entry is a "tighter" match (fewer unrelated tokens), which correctly
    # disambiguates e.g. "University College London" (tokens: ["london"]) from
    # "Imperial College London" (tokens: ["imperial", "london"]).
    def _entry_token_count(idx: int) -> int:
        entry = _DB[idx]
        return len({t for src in [entry.canonical_name] + entry.aliases for t in _tokenize(src)})

    candidates.sort(
        key=lambda idx: (scores[idx], scores[idx] / max(_entry_token_count(idx), 1)),
        reverse=True,
    )
    best_entry = _DB[candidates[0]]
    return list(best_entry.domains)


def get_all_entries() -> List[UniversityEntry]:
    """Return all database entries (for admin/diagnostics)."""
    return list(_DB)

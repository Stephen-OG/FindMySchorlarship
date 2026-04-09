from typing import List

from pydantic import BaseModel


class SchoolAndDomain(BaseModel):
    school: str
    "The name of the school"
    domains: List[str]
    "All relevant domains for this school (may include main site, financial aid subdomains, scholarship portals, etc.)"


class MultipleSchoolsAndDomains(BaseModel):
    schools: List[SchoolAndDomain]
    "List of schools and their domains"
    # search_type: str = "searched"
    search_type: str
    "Either 'explicit' (schools were explicitly mentioned) or 'searched' (schools were found via search)"


class School(BaseModel):
    name: str
    "The name of the school"

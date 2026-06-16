from __future__ import annotations

from typing import Annotated
import operator
from typing_extensions import TypedDict


class TalentState(TypedDict, total=False):
    # Input — company setup (from UI, saved in localStorage)
    company_name: str           # e.g. "Acme Corp"
    company_website: str        # e.g. "acme.com"
    company_linkedin_url: str   # e.g. "linkedin.com/company/acme"
    candidate_icp: str          # free-text ICP from hiring team (treated as authoritative)

    # Input — per-search
    job_title: str
    additional_notes: str       # optional extra context from the user
    job_description: str        # researched + synthesized description (set by research_role node)

    # Phase 1 — ICP
    company_context: str        # fetched from company website
    employee_raw: list          # [{url, name, headline, snippet}]
    icp: dict                   # structured ICP from Claude

    # Phase 2 — Search
    search_queries: list        # queries for current iteration
    all_queries_used: list      # all queries ever used (prevent repeats)
    current_query: str          # per-node field set via Send for fan-out
    raw_candidates: Annotated[list, operator.add]   # accumulates across fan-out + iterations

    # Phase 3 — Scoring
    scored_candidates: list     # [{...candidate fields, role_fit_score, culture_fit_score, ...}]
    usage_stats: dict            # {haiku_calls, sonnet_calls, candidates_scored, filtered_out, est_cost_usd}

    # Control
    iteration: int
    force_refresh: bool     # bypass cache for employees/ICP/role

    # Learning — signals from past high-scoring candidates for this role type
    past_signals: list

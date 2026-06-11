"""
Phase 2 nodes: generate LinkedIn search queries, then fan-out search via Nimble.
"""

from __future__ import annotations

import json
import os
import anthropic
from app.models import TalentState
from app import nimble_client as nimble
from app.nimble_context import build_company_context

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def generate_queries(state: TalentState) -> dict:
    iteration = state.get("iteration", 0)
    print(f"[generate_queries] Iteration {iteration + 1}...")

    job_title       = state.get("job_title", "")
    job_description = state.get("job_description", "")
    icp             = state.get("icp", {})
    used_queries    = state.get("all_queries_used", [])
    scored          = state.get("scored_candidates", [])
    company_name    = (state.get("company_name") or "the company").strip()
    company_ctx     = build_company_context(state)

    refinement_context = ""
    if iteration > 0 and scored:
        avg_role     = sum(c.get("role_fit_score", 0) for c in scored) / len(scored)
        avg_interest = sum(c.get("interest_score", 0) for c in scored) / len(scored)
        top_names    = [c.get("name", "") for c in scored[:5] if c.get("overall_score", 0) >= 7]
        refinement_context = f"""
REFINEMENT PASS (iteration {iteration + 1}):
Previous search found {len(scored)} candidates.
Avg role fit: {avg_role:.1f}/10  |  Avg interest: {avg_interest:.1f}/10
Top candidates so far: {', '.join(top_names) or 'none yet'}
{"→ Improve queries to find candidates with stronger startup/availability signals." if avg_interest < 6 else ""}
{"→ Improve queries to find candidates with stronger technical alignment." if avg_role < 6 else ""}
Find DIFFERENT candidates — avoid overlapping with what was already found.
"""

    num_queries = 8

    prompt = f"""You are generating LinkedIn search queries to find candidates for {company_name}.

{company_ctx}

JOB TO FILL:
Title: {job_title}
Description: {job_description[:1500]}

IDEAL CANDIDATE PROFILE:
{json.dumps(icp, indent=2)[:1200]}
{refinement_context}

Generate exactly {num_queries} Google Search queries — one per strategy below. Each must use a DIFFERENT strategy.
Use Boolean operators (AND, OR, quotes) to maximize precision.

STRATEGY A — Exact role + must-have skills:
  Target the specific title combined with 2-3 critical skills from the JD and ICP key_skills.
  e.g. ("{job_title}" OR "<seniority> <function>") AND ("<key skill 1>" OR "<key skill 2>") AND ("startup" OR "scaleup") site:linkedin.com/in

STRATEGY B — Lookalike companies:
  Find people at companies in the same space — same category, same stage.
  Pull from ICP common_company_types or career_trajectory_patterns. Use specific company names if ICP mentions them.
  e.g. ("<company type from ICP>" OR "<competitor>") "{job_title}" site:linkedin.com/in

STRATEGY C — Career trajectory match:
  Target people whose career path mirrors the ICP career_trajectory_patterns — the "before joining" profile.
  e.g. "{job_title}" ("startup" OR "scaleup") ("previously" OR "ex-") site:linkedin.com/in

STRATEGY D — Availability signals:
  Specifically target candidates showing openness to a move.
  e.g. ("open to work" OR "looking for" OR "seeking new") "{job_title}" site:linkedin.com/in

STRATEGY E — Deep skill search:
  Go narrow on the single most critical skill from ICP key_skills — no title, pure depth.
  e.g. ("<most critical skill from ICP>") ("<supporting skill>") ("startup" OR "B2B" OR "SaaS") site:linkedin.com/in

STRATEGY F — Geography-specific:
  Use ICP location_context to target candidates in the right city/region.
  e.g. ("{job_title}" OR "<synonym>") ("<preferred city from ICP>" OR "<region>") site:linkedin.com/in

STRATEGY G — Company alumni:
  Target people who used to work at relevant companies and have since moved on — use ICP green_flags or typical_backgrounds for company names.
  e.g. ("ex-<company from ICP>" OR "previously <company>" OR "formerly <company>") "{job_title}" site:linkedin.com/in

STRATEGY H — Title variants:
  Use alternative job titles that describe the same function — synonyms the other queries might miss.
  e.g. ("<title variant 1>" OR "<title variant 2>" OR "<title variant 3>") ("startup" OR "scaleup") site:linkedin.com/in

Rules:
- Every query MUST include site:linkedin.com/in
- Do NOT repeat any of these already-used queries: {json.dumps(used_queries)}
- Use location from ICP where relevant
- Aim for precision over breadth — a recruiter reviews every result

Return ONLY valid JSON: {{"queries": ["query1", "query2", "query3", "query4", "query5", "query6", "query7", "query8"]}}"""

    resp = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        queries = json.loads(text).get("queries", [])
    except json.JSONDecodeError:
        queries = []

    if not queries:
        queries = [f'"{job_title}" startup site:linkedin.com/in']
    elif len(queries) < num_queries:
        print(f"[generate_queries] WARNING: got {len(queries)}/{num_queries} queries")

    all_used = list(used_queries) + queries
    print(f"[generate_queries] Generated {len(queries)} queries")

    return {
        "search_queries":   queries,
        "all_queries_used": all_used,
        "iteration":        iteration + 1,
    }


def search_one_query(state: TalentState) -> dict:
    """Fan-out node — one instance per query, invoked in parallel via Send."""
    query = state.get("current_query", "")
    print(f"  [search] {query[:90]}")

    results = nimble.search(query, focus="social", num_results=30)

    candidates = []
    for r in results:
        url = r.get("url", "")
        if "linkedin.com/in/" not in url:
            continue
        title = r.get("title", "")
        name  = title.split(" - ")[0].strip() if " - " in title else title.split("|")[0].strip()
        candidates.append({
            "url":          url,
            "name":         name,
            "headline":     title,
            "snippet":      r.get("description", ""),
            "source_query": query,
        })

    print(f"  [search] → {len(candidates)} candidates")
    return {"raw_candidates": candidates}

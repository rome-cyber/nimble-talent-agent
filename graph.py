from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from app.models import TalentState
from app.agents.icp_builder import fetch_employees, build_icp
from app.agents.role_researcher import research_role
from app.agents.candidate_finder import generate_queries, search_one_query
from app.agents.scorer import score_candidates


def _route_searches(state: TalentState):
    """Fan out — one search_one_query node per query, run in parallel."""
    return [
        Send("search_one_query", {"current_query": q})
        for q in state.get("search_queries", [])
    ]


def _should_refine(state: TalentState) -> str:
    """
    Stop when we have enough strong candidates or have refined too many times.
    Strong = overall_score >= 7.
    """
    scored    = state.get("scored_candidates", [])
    top       = [c for c in scored if c.get("overall_score", 0) >= 7]
    iteration = state.get("iteration", 0)
    target    = state.get("target_candidates", 5)
    # Allow more iterations for larger targets
    max_iter  = 3 if target <= 10 else 4 if target <= 20 else 5

    if len(top) >= target:
        print(f"[graph] Stopping: {len(top)}/{target} strong matches found after iteration {iteration}")
        return "done"

    if len(top) >= max(3, target // 2) and iteration >= 2:
        avg_role = sum(c.get("role_fit_score", 0) for c in scored) / len(scored) if scored else 0
        if avg_role > 6.5:
            print(f"[graph] Stopping: {len(top)} strong matches, avg role fit {avg_role:.1f} after iteration {iteration}")
            return "done"

    if iteration >= max_iter:
        print(f"[graph] Stopping: max iterations ({iteration}) reached with {len(top)}/{target} strong matches")
        return "done"

    print(f"[graph] Refining: {len(top)}/{target} strong matches after iteration {iteration} — generating new queries")
    return "refine"


def build_graph():
    g = StateGraph(TalentState)

    g.add_node("fetch_employees", fetch_employees)
    g.add_node("build_icp", build_icp)
    g.add_node("research_role", research_role)
    g.add_node("generate_queries", generate_queries)
    g.add_node("search_one_query", search_one_query)
    g.add_node("score_candidates", score_candidates)

    g.add_edge(START, "fetch_employees")
    g.add_edge("fetch_employees", "build_icp")
    g.add_edge("build_icp", "research_role")
    g.add_edge("research_role", "generate_queries")
    g.add_conditional_edges("generate_queries", _route_searches, ["search_one_query"])
    g.add_edge("search_one_query", "score_candidates")
    g.add_conditional_edges("score_candidates", _should_refine, {
        "refine": "generate_queries",
        "done": END,
    })

    return g.compile()

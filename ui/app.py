from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import time
import streamlit as st
from graph import build_graph
from app.cache import get_cache_info

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Nimble Talent Agent", page_icon="🎯", layout="wide")

# ── Helpers ────────────────────────────────────────────────────────────────────

_PHASE_LABELS = {
    "fetch_employees": "Fetching current Nimble employee profiles",
    "build_icp": "Building Ideal Candidate Profile",
    "research_role": "Researching role requirements",
    "generate_queries": "Generating LinkedIn search queries",
    "search_one_query": "Searching LinkedIn",
    "score_candidates": "Scoring candidates (bidirectional fit)",
}

# Empirical seconds per phase (used for initial estimate; self-calibrates as run proceeds)
_PHASE_SECS = {
    "fetch_employees": 15,   # 4 Nimble searches + 1 extract
    "build_icp": 8,          # Claude call
    "research_role": 14,     # 4 Nimble searches + Claude call
    "generate_queries": 5,   # Claude call
    "search_one_query": 25,  # all parallel queries combined (first iteration, 8 queries)
    "score_candidates": 12,  # Claude call on ~50 candidates
}
_REFINEMENT_SECS = (
    _PHASE_SECS["generate_queries"] +
    _PHASE_SECS["search_one_query"] * 5 // 8 +   # 5 queries vs 8
    _PHASE_SECS["score_candidates"]
)  # ~32s per refinement pass


class _ProgressTracker:
    """Tracks phase completion and computes a self-calibrating time estimate."""

    def __init__(self):
        self._start = time.monotonic()
        self._total_expected = float(sum(_PHASE_SECS.values()))
        self._completed_secs = 0.0
        self._n_expected_searches = 8   # updated when generate_queries fires
        self._n_searches_done = 0
        self._current_phase = ""

    def phase_done(self, phase: str, n_expected_searches: int = 0):
        self._current_phase = phase
        if phase == "search_one_query":
            per = _PHASE_SECS["search_one_query"] / max(self._n_expected_searches, 1)
            self._completed_secs += per
            self._n_searches_done += 1
        else:
            self._completed_secs += _PHASE_SECS.get(phase, 0)
            if phase == "generate_queries" and n_expected_searches:
                self._n_expected_searches = n_expected_searches

    def add_refinement(self, n_queries: int):
        self._total_expected += _REFINEMENT_SECS
        self._n_expected_searches += n_queries

    @property
    def _fraction(self) -> float:
        return min(self._completed_secs / self._total_expected, 0.98)

    @property
    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def _remaining(self) -> float:
        f = self._fraction
        elapsed = self._elapsed
        if f < 0.04:
            return self._total_expected - elapsed
        return max(0.0, (elapsed / f) - elapsed)

    def render(self, bar, caption_slot):
        elapsed = self._elapsed
        remaining = self._remaining
        bar.progress(self._fraction)
        elapsed_str = f"{int(elapsed)}s elapsed"
        rem_str = f"~{max(1, int(remaining))}s remaining" if remaining > 1 else "finishing…"
        caption_slot.caption(f"⏱ {elapsed_str} · {rem_str}")


def _score_color(score: int) -> str:
    if score >= 7:
        return "🟢"
    if score >= 5:
        return "🟡"
    return "🔴"


def _render_icp(icp: dict):
    with st.expander("📋 Ideal Candidate Profile", expanded=True):
        summary = icp.get("company_summary", "")
        if summary:
            st.markdown(f"*{summary}*")

        col1, col2 = st.columns(2)

        with col1:
            skills = icp.get("key_skills", [])
            if skills:
                st.markdown("**Key skills:** " + ", ".join(skills[:8]))
            roles = icp.get("typical_roles", [])
            if roles:
                st.markdown("**Typical roles:** " + ", ".join(roles[:5]))
            location = icp.get("location_context", "")
            if location:
                st.markdown(f"**Location:** {location}")
            seniority = icp.get("seniority_range", "")
            if seniority:
                st.markdown(f"**Seniority:** {seniority}")

        with col2:
            greens = icp.get("green_flags", [])
            if greens:
                st.markdown("**Green flags:**")
                for g in greens[:5]:
                    st.markdown(f"&nbsp;&nbsp;✅ {g}")
            reds = icp.get("red_flags", [])
            if reds:
                st.markdown("**Red flags:**")
                for r in reds[:4]:
                    st.markdown(f"&nbsp;&nbsp;❌ {r}")


def _render_candidate(c: dict):
    score = c.get("overall_score", 0)
    role = c.get("role_fit_score", 0)
    culture = c.get("culture_fit_score", 0)
    interest = c.get("interest_score", 0)
    name = c.get("name", "Unknown")
    headline = c.get("headline", "")[:80]

    with st.expander(
        f"{_score_color(score)} **{name}** — {headline}  ·  Overall: **{score}/10**",
        expanded=False,
    ):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Overall", f"{score}/10")
        col2.metric("Role Fit", f"{role}/10")
        col3.metric("Culture Fit", f"{culture}/10")
        col4.metric("Interest", f"{interest}/10")

        url = c.get("url", "")
        if url:
            st.markdown(f"**LinkedIn:** [{url}]({url})")

        reasoning = c.get("reasoning", "")
        if reasoning:
            st.markdown(f"**Assessment:** {reasoning}")

        signals = c.get("availability_signals", [])
        if signals:
            st.markdown("**Availability signals:** " + " · ".join(signals))

        snippet = c.get("snippet", "")
        if snippet:
            st.caption(snippet[:300])


# ── Main UI ────────────────────────────────────────────────────────────────────

st.title("🎯 Nimble Talent Agent")
st.caption("Input a role → get a verified shortlist of candidates that fit Nimble and would actually want to join.")

# ── Cache status banner ────────────────────────────────────────────────────────

info = get_cache_info()

if info["has_employees"] and info["has_icp"]:
    days = info["employees_days_ago"]
    fresh = info["employees_fresh"]
    age_str = "today" if days == 0 else f"{days}d ago"
    color = "normal" if fresh else "warning"
    st.info(
        f"👥 **{info['employee_count']} Nimble employees** cached · "
        f"ICP built · last refreshed **{age_str}**"
        + ("" if fresh else " ⚠️ data is over 7 days old"),
        icon=None,
    )
else:
    st.warning("No employee cache yet — first run will fetch live data (~30s extra).", icon="⚡")

with st.form("job_form"):
    job_title = st.text_input("Job Title *", placeholder="e.g. Senior Backend Engineer")
    additional_notes = st.text_area(
        "Additional notes (optional)",
        height=120,
        placeholder="Anything specific Nimble wants in this hire, hard requirements, deal-breakers, or extra context. Leave blank and the agent will research the role automatically.",
    )
    col_btn, col_refresh = st.columns([3, 1])
    with col_btn:
        submitted = st.form_submit_button("Find Candidates", type="primary", use_container_width=True)
    with col_refresh:
        force_refresh = st.form_submit_button("🔄 Refresh cache", use_container_width=True)

if (submitted or force_refresh) and job_title:
    st.divider()

    input_state = {
        "job_title": job_title,
        "additional_notes": additional_notes,
        "force_refresh": bool(force_refresh),
        "raw_candidates": [],
        "all_queries_used": [],
        "iteration": 0,
    }

    graph = build_graph()
    final_state: dict = {}
    prev_raw_count = 0
    tracker = _ProgressTracker()

    progress_bar = st.progress(0.0)
    time_caption = st.empty()

    with st.status("Running Nimble Talent Agent...", expanded=True) as status:
        try:
            for current_state in graph.stream(input_state, stream_mode="values"):

                # ── Detect phase completions ───────────────────────────────────

                if "company_context" in current_state and "company_context" not in final_state:
                    from_cache = current_state.get("_from_cache", False)
                    tracker.phase_done("fetch_employees")
                    suffix = " *(from cache)*" if from_cache else f" ({len(current_state.get('employee_raw', []))} profiles found)"
                    st.write(f"✓ {_PHASE_LABELS['fetch_employees']}{suffix}")

                if "icp" in current_state and "icp" not in final_state:
                    from_cache = current_state.get("_icp_from_cache", False)
                    tracker.phase_done("build_icp")
                    icp = current_state["icp"]
                    suffix = " *(from cache)*" if from_cache else (
                        f" ({len(icp.get('key_skills', []))} skills, "
                        f"{len(icp.get('green_flags', []))} green flags identified)"
                    )
                    st.write(f"✓ {_PHASE_LABELS['build_icp']}{suffix}")

                if "job_description" in current_state and "job_description" not in final_state:
                    from_cache = current_state.get("_role_from_cache", False)
                    tracker.phase_done("research_role")
                    suffix = " *(from cache)*" if from_cache else ""
                    st.write(f"✓ {_PHASE_LABELS['research_role']}{suffix}")
                    with st.expander("📄 Researched job description", expanded=False):
                        st.markdown(current_state["job_description"])

                if "search_queries" in current_state:
                    prev_q = len(final_state.get("search_queries", []))
                    curr_q = len(current_state.get("search_queries", []))
                    if curr_q != prev_q:
                        iteration = current_state.get("iteration", 1)
                        tracker.phase_done("generate_queries", n_expected_searches=curr_q)
                        if iteration > 1:
                            tracker.add_refinement(curr_q)
                        st.write(f"✓ {_PHASE_LABELS['generate_queries']} "
                                 f"— iteration {iteration}, {curr_q} queries")

                curr_raw = len(current_state.get("raw_candidates", []))
                if curr_raw > prev_raw_count:
                    new = curr_raw - prev_raw_count
                    tracker.phase_done("search_one_query")
                    st.write(f"✓ {_PHASE_LABELS['search_one_query']} "
                             f"({new} new · {curr_raw} total)")
                    prev_raw_count = curr_raw

                if "scored_candidates" in current_state:
                    prev_s = len(final_state.get("scored_candidates", []))
                    curr_s = len(current_state.get("scored_candidates", []))
                    if curr_s != prev_s:
                        tracker.phase_done("score_candidates")
                        top = sum(1 for c in current_state["scored_candidates"]
                                  if c.get("overall_score", 0) >= 7)
                        st.write(f"✓ {_PHASE_LABELS['score_candidates']} "
                                 f"— {curr_s} scored, {top} strong (≥ 7/10)")

                final_state = current_state
                tracker.render(progress_bar, time_caption)

            progress_bar.progress(1.0)
            elapsed = int(time.monotonic() - tracker._start)
            time_caption.caption(f"✅ Done in {elapsed}s")
            status.update(label="✅ Complete!", state="complete")

        except Exception as e:
            status.update(label=f"❌ Error: {e}", state="error")
            st.error(str(e))
            st.stop()

    # ── ICP summary ────────────────────────────────────────────────────────────
    icp = final_state.get("icp", {})
    if icp:
        _render_icp(icp)

    # ── Results ────────────────────────────────────────────────────────────────
    scored = final_state.get("scored_candidates", [])

    if not scored:
        st.warning("No candidates found. Try broadening the job description or check your API keys.")
        st.stop()

    all_sorted = sorted(scored, key=lambda x: x.get("overall_score", 0), reverse=True)
    strong = [c for c in all_sorted if c.get("overall_score", 0) >= 7]

    st.subheader(f"Results — {len(strong)} strong candidates out of {len(scored)} evaluated")

    col_filter, _ = st.columns([1, 3])
    with col_filter:
        min_score = st.slider("Minimum overall score", min_value=0, max_value=10, value=6)

    filtered = [c for c in all_sorted if c.get("overall_score", 0) >= min_score]
    st.caption(f"Showing {len(filtered)} candidates with score ≥ {min_score}")

    for candidate in filtered:
        _render_candidate(candidate)

elif submitted:
    st.warning("Please enter a Job Title.")

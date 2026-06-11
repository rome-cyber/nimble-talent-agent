"""
Phase 3 node: bidirectional scoring — does Nimble want them? Would they want Nimble?

Cost optimizations applied:
  A. Batched scoring: 5 candidates per Sonnet call instead of one-at-a-time
  B. Prompt caching: static ICP/rubric/JD block cached across batches (90% cheaper after first)
  C. Haiku pre-filter: cheap yes/no relevance filter before expensive Sonnet scoring
  F. Profile truncation: strip excess tokens before sending to LLM
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from app.models import TalentState
from app.nimble_context import NIMBLE_FOR_PROMPT

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=120.0)

_BATCH_SIZE = 5


# ── F. Profile truncation ─────────────────────────────────────────────────────

def _truncate_profile(c: dict) -> dict:
    """Keep scoring-relevant fields only; truncate long snippets to 500 chars."""
    snippet = c.get("snippet", "")
    return {
        "url":      c.get("url", ""),
        "name":     c.get("name", ""),
        "headline": c.get("headline", ""),
        "snippet":  snippet[:500] if len(snippet) > 500 else snippet,
    }


# ── C. Haiku pre-filter ───────────────────────────────────────────────────────

_HAIKU_BATCH = 40  # max candidates per Haiku call to avoid output truncation


def _prefilter_with_haiku(candidates: list, job_title: str) -> list:
    """
    Use Haiku (~20x cheaper than Sonnet) to drop clearly irrelevant candidates.
    Processes in batches so output never gets truncated.
    Defaults to KEEP on any parse failure or missing verdict.
    """
    if len(candidates) <= 4:
        return candidates

    kept: list = []
    for batch_start in range(0, len(candidates), _HAIKU_BATCH):
        batch = candidates[batch_start: batch_start + _HAIKU_BATCH]
        lines = "\n".join(
            f"[{i + 1}] {c.get('name', '')} | {c.get('headline', '')} | {c.get('snippet', '')[:120]}"
            for i, c in enumerate(batch)
        )
        prompt = f"""Filter candidates for a "{job_title}" role at Nimble — the AI search platform for production agents (nimbleway.com).
Nimble's ideal hire has a background in AI, data, APIs, production software, B2B SaaS, or enterprise tech.

Only mark keep=false for profiles with ZERO connection to technology, software, data, sales, or business:
pure healthcare (nurse, doctor), skilled trades (plumber, electrician), arts/entertainment, agriculture.
When in doubt, keep=true. An AI engineer, data scientist, software engineer, salesperson, or operator always keep=true.

Candidates:
{lines}

Return ONLY valid JSON — one entry per candidate, same order:
[{{"i": 1, "keep": true}}, {{"i": 2, "keep": false}}, ...]"""

        try:
            resp = _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=len(batch) * 16 + 64,  # ~16 tokens per verdict
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            verdicts = json.loads(text)
            # Default KEEP — only drop candidates explicitly marked false
            drop_set = {v["i"] - 1 for v in verdicts if not v.get("keep", True)}
            batch_kept = [c for i, c in enumerate(batch) if i not in drop_set]
            if drop_set:
                print(f"[prefilter] Haiku dropped {len(drop_set)}/{len(batch)} in batch")
            kept.extend(batch_kept)
        except Exception as e:
            print(f"[prefilter] Haiku batch failed ({e}) — keeping all {len(batch)}")
            kept.extend(batch)

    removed = len(candidates) - len(kept)
    if removed:
        print(f"[prefilter] Total removed: {removed}/{len(candidates)}")
    return kept


# ── D. Python pre-ranking (free, no LLM cost) ────────────────────────────────

def _prerank_candidates(candidates: list, icp: dict) -> list:
    """
    Score candidates with pure Python keyword heuristics — zero API cost.
    Used to surface the best candidates before the Sonnet cap so we spend
    our 50-slot budget on the most promising profiles.
    """
    skills = [s.lower() for s in icp.get("key_skills", [])]
    green  = [g.lower() for g in icp.get("green_flags", [])]
    red    = [r.lower() for r in icp.get("red_flags", [])]

    # Generic professional signals — role-agnostic, just startup/B2B fit
    domain_signals     = ["startup", "scaleup", "b2b", "saas", "growth", "series"]
    availability_bonus = ["open to work", "looking for", "seeking", "available", "actively"]

    # Pull preferred locations from ICP; fall back to a minimal default
    raw_locations = icp.get("location_context", "") or ""
    location_bonus = [loc.strip().lower() for loc in raw_locations.replace("/", ",").split(",") if len(loc.strip()) > 2]
    if not location_bonus:
        location_bonus = ["remote"]

    def _score(c: dict) -> float:
        text = (c.get("headline", "") + " " + c.get("snippet", "")).lower()
        pts  = 0.0

        # Key skills hit (+2 each, up to 10)
        pts += min(10, sum(2 for s in skills if s in text))

        # Domain signals (+1 each, up to 5)
        pts += min(5, sum(1 for d in domain_signals if d in text))

        # Green flags (+1.5 each)
        pts += sum(1.5 for g in green if g in text)

        # Red flags (−3 each)
        pts -= sum(3 for r in red if r in text)

        # Availability bonus (+2)
        if any(a in text for a in availability_bonus):
            pts += 2

        # Location bonus (+1)
        if any(loc in text for loc in location_bonus):
            pts += 1

        return pts

    scored = sorted(candidates, key=_score, reverse=True)
    return scored


# ── B. Static context (cached across batches) ─────────────────────────────────

def _build_static_context(job_title: str, job_description: str, icp: dict) -> str:
    """
    Build the reusable scoring context that gets cached by Anthropic after the
    first batch call. All subsequent batches pay ~10% of its token cost.
    Must be >1024 tokens for caching to activate.
    """
    return f"""You are a senior talent sourcing agent for Nimble (nimbleway.com).
Your job is to score candidates for BIDIRECTIONAL fit: would Nimble want to hire them, AND would they be genuinely interested in joining?

{NIMBLE_FOR_PROMPT}


══════════════════════════════════════════════
ROLE TO FILL
══════════════════════════════════════════════
Title: {job_title}

Job Description:
{job_description[:1500]}

══════════════════════════════════════════════
IDEAL CANDIDATE PROFILE
(Built from analysis of current Nimble employees)
══════════════════════════════════════════════
{json.dumps(icp, indent=2)[:1200]}

══════════════════════════════════════════════
SCORING SCALE  (use the FULL 1-10 range)
══════════════════════════════════════════════
10   = Perfect match. Near-perfect technical fit + strong interest signals. Very rare.
8-9  = Strong match. Clear alignment on role AND culture, likely open to a move.
6-7  = Good match. Worth a recruiter outreach. One or two unknowns.
4-5  = Possible. Relevant background but meaningful gaps or weak interest signals.
1-3  = Clear mismatch. Save everyone's time — don't outreach.

CALIBRATION RULES — follow these exactly:
- The realistic center of mass for a good search is 6-8. Expect most scored candidates here.
- Scores 9-10 are rare: reserve for near-perfect fit WITH clear interest signals.
- Scores 1-3 are for clear mismatches only — not "I don't have enough info."
- Missing data is NOT a reason to score below 5. Evidence-based, not absence-of-evidence.
- Do NOT default to 5. If you have any evidence at all, use it to go higher or lower.

══════════════════════════════════════════════
INTEREST SCORE CALCULATION
Start at 5 (neutral) and apply these deltas:
══════════════════════════════════════════════
POSITIVE signals (add to base):
  +2.0  "Open to Work" badge or phrase in profile/snippet — strongest available signal
  +2.0  Two or more startups in career history (self-selects into startup environments)
  +1.5  Changed jobs within the last 6-12 months (demonstrated willingness to move)
  +1.0  Currently at same company 18-36 months (settled but not locked in)
  +1.0  Currently at same company 4+ years with no visible promotion (may be ready)
  +1.0  Active LinkedIn: posts, comments, or profile activity visible in data
  +1.0  Remote-first or location-flexible signals in profile
  +1.0  Located in a preferred location per the ICP above
  +0.5  Career stage naturally suited for a startup move (recent big-co departure, just promoted out of IC, etc.)

NEGATIVE signals (subtract from base):
  -1.5  Currently at large enterprise (FAANG / Fortune 500) for 5+ years with zero startup history
  -1.0  No domain overlap with Nimble's space (data, APIs, web intelligence, SaaS, B2B)

RULES:
- No signals either way → stay at 5. Missing data is NOT negative.
- Cap at 10. Floor at 1. Round to nearest integer.

══════════════════════════════════════════════
SCORE DIMENSIONS
══════════════════════════════════════════════
1. role_fit_score (0-10):
   Match of their specific experience and skills against the job requirements above.

2. culture_fit_score (0-10):
   How closely their background, company types, and career trajectory mirror the ICP.

3. interest_score (0-10):
   Apply the INTEREST SCORE CALCULATION above exactly.

4. overall_score (0-10):
   Formula: (role_fit × 0.40) + (culture_fit × 0.30) + (interest × 0.30), rounded to nearest integer.

══════════════════════════════════════════════
RECRUITER-FACING EXPLANATIONS
══════════════════════════════════════════════
Write plain English explanations aimed at a non-technical recruiter, not a data scientist.
Good example: "Has 4 years of LLM work at production scale — strong technical fit. Missing enterprise sales but that's acceptable."
Bad example: "role_fit_score: 7 because candidate has relevant ML background."

For why_yes and why_no: be honest and specific. The recruiter needs to go into an outreach call with clear eyes.
"""


# ── A. Batched Sonnet scoring ─────────────────────────────────────────────────

def _score_batch(batch: list, static_context: str) -> list:
    """
    Score one batch (≤5 candidates) using Sonnet with prompt caching on the
    static context. First call in a run pays full price; subsequent batches
    get ~90% discount on the static block.
    """
    n = len(batch)
    batch_prompt = f"""Score these {n} candidates. Return a JSON array of EXACTLY {n} objects in the SAME ORDER as the input.

CANDIDATES:
{json.dumps(batch, indent=2)[:3500]}

Return ONLY valid JSON array — no markdown, no explanation, just the array:
[
  {{
    "url": "...",
    "name": "...",
    "headline": "...",
    "snippet": "...",
    "role_fit_score": 7,
    "culture_fit_score": 8,
    "interest_score": 6,
    "overall_score": 7,
    "role_fit_explanation": "1-2 plain English sentences for a non-technical recruiter explaining WHY they got this role fit score. Be specific to their actual experience.",
    "culture_fit_explanation": "1-2 plain English sentences explaining culture/background fit against the Nimble ICP.",
    "interest_explanation": "1-2 plain English sentences explaining why they would or wouldn't be interested in making a move to Nimble.",
    "reasoning": "2-3 sentence overall summary: what makes them worth outreaching (or not) at Nimble specifically.",
    "availability_signals": ["specific observable signal from their profile", "..."],
    "outreach_hook": "One highly specific sentence a recruiter could open with in a cold LinkedIn message. Must reference something concrete from THIS person's background — never generic. Example: 'You spent 3 years building data pipelines at Bright Data before joining a Series A startup — that path is exactly why I'm reaching out.'",
    "why_yes": [
      "Specific reason this person would be interested in Nimble (be concrete, e.g. company stage, domain overlap, current tenure signals)",
      "Second reason if applicable",
      "Third reason max"
    ],
    "why_no": [
      "Honest friction point the recruiter should be aware of (location, seniority mismatch, no startup history, domain gap, etc.)",
      "Second friction point if applicable"
    ],
    "career_snapshot": [
      {{"company": "Company Name", "role": "Job Title", "duration": "~2 years"}},
      "... up to 3 most recent positions extracted from headline/snippet. Return [] if not determinable."
    ]
  }}
]"""

    resp = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6144,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": static_context,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": batch_prompt,
                },
            ],
        }],
    )

    if resp.stop_reason == "max_tokens":
        print(f"[score_batch] WARNING: output truncated for batch of {n}")

    # Log cache usage when available
    if hasattr(resp, 'usage'):
        u = resp.usage
        cache_read = getattr(u, 'cache_read_input_tokens', 0) or 0
        cache_write = getattr(u, 'cache_creation_input_tokens', 0) or 0
        if cache_read or cache_write:
            print(f"  [cache] read={cache_read} write={cache_write}")

    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        print(f"[score_batch] JSON parse error: {e}")
        return []


# ── Main scoring node ─────────────────────────────────────────────────────────

def score_candidates(state: TalentState) -> dict:
    print("[score_candidates] Starting scoring pipeline...")

    raw          = state.get("raw_candidates", [])
    icp          = state.get("icp", {})
    job_title    = state.get("job_title", "")
    job_desc     = state.get("job_description", "")

    # 1. Deduplicate by URL across all fan-out iterations
    seen: set  = set()
    unique: list = []
    for c in raw:
        url = c.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(c)

    if not unique:
        return {"scored_candidates": []}

    # D. Pre-rank with free Python heuristics so the best 20 go to Sonnet
    unique   = _prerank_candidates(unique, icp)

    # Cap at 20 — parallel batches of 5 run in ~15s total (vs 150s sequential for 50)
    to_score = unique[:20]
    print(f"[score_candidates] {len(raw)} raw → {len(unique)} unique → top {len(to_score)} to Sonnet")

    # 2. F — Truncate profiles (strip excess text before sending to LLM)
    to_score = [_truncate_profile(c) for c in to_score]

    # 3. C — Haiku pre-filter (cheap pass to remove clearly irrelevant profiles)
    pre_count   = len(to_score)
    haiku_calls = 0
    if len(to_score) > 4:
        to_score    = _prefilter_with_haiku(to_score, job_title)
        haiku_calls = 1
    print(f"[score_candidates] {len(to_score)} candidates after Haiku pre-filter")

    if not to_score:
        return {"scored_candidates": [], "usage_stats": {"haiku_calls": haiku_calls, "sonnet_calls": 0, "candidates_scored": 0, "filtered_out": pre_count}}

    # 4. B — Build static context once; it gets cached across all batch calls
    static_context = _build_static_context(job_title, job_desc, icp)

    # 5. A — Batch 1 runs first to write the prompt cache; remaining batches run
    #         in parallel and get the ~90% cache-read discount simultaneously.
    #         4 batches: ~15s (write) + ~8s (3 parallel reads) = ~23s vs ~39s sequential.
    all_scored: list = []
    sonnet_calls     = 0
    batches          = [to_score[i : i + _BATCH_SIZE] for i in range(0, len(to_score), _BATCH_SIZE)]
    total_batches    = len(batches)

    print(f"  [score_batch] 1/{total_batches} — scoring {len(batches[0])} candidates (cache warm-up)")
    try:
        all_scored.extend(_score_batch(batches[0], static_context))
        sonnet_calls += 1
    except Exception as e:
        print(f"  [score_batch] ERROR on batch 1: {e} — skipping")

    if len(batches) > 1:
        with ThreadPoolExecutor(max_workers=len(batches) - 1) as ex:
            future_to_num = {
                ex.submit(_score_batch, batch, static_context): idx + 2
                for idx, batch in enumerate(batches[1:])
            }
            for fut in as_completed(future_to_num):
                batch_num = future_to_num[fut]
                try:
                    result = fut.result()
                    all_scored.extend(result)
                    sonnet_calls += 1
                    print(f"  [score_batch] {batch_num}/{total_batches} — done")
                except Exception as e:
                    print(f"  [score_batch] ERROR on batch {batch_num}: {e} — skipping")

    all_scored = sorted(all_scored, key=lambda x: x.get("overall_score", 0), reverse=True)

    # Accumulate usage stats across refinement iterations
    prev = state.get("usage_stats", {})
    usage_stats = {
        "haiku_calls":       prev.get("haiku_calls", 0) + haiku_calls,
        "sonnet_calls":      prev.get("sonnet_calls", 0) + sonnet_calls,
        "candidates_scored": len(all_scored),
        "filtered_out":      prev.get("filtered_out", 0) + max(0, pre_count - len(to_score)),
    }

    top_count = sum(1 for c in all_scored if c.get("overall_score", 0) >= 7)
    print(f"[score_candidates] Done — {len(all_scored)} scored, {top_count} strong (≥7) | haiku={usage_stats['haiku_calls']} sonnet={usage_stats['sonnet_calls']}")
    return {"scored_candidates": all_scored, "usage_stats": usage_stats}

"""
Phase 3 node: bidirectional scoring — does Nimble want them? Would they want Nimble?

Cost optimizations:
  A. Batched scoring: 5 candidates per Sonnet call
  B. Prompt caching: static context cached across batches (~90% cheaper after batch 1)
  C. Haiku pre-filter: cheap relevance pass on the FULL pool before Sonnet
  D. Python pre-ranking + dynamic cap: free heuristics surface best N; cap applied AFTER Haiku
  F. Profile truncation: strip excess tokens before any LLM call

Ordering in score_candidates():
  1. Deduplicate by URL
  2. _prerank_candidates()   — Python heuristics, no cap
  3. _truncate_profile()     — strip each profile before sending to LLMs
  4. _prefilter_with_haiku() — filter irrelevant profiles from the FULL pool
  5. Dynamic cap             — send top N to Sonnet based on Haiku output size
  6. _build_static_context() — build ONCE, same string to every batch (cache key)
  7. Batch score with Sonnet — batch 1 primes cache, rest run in parallel
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from app.models import TalentState
from app.nimble_context import NIMBLE_FOR_PROMPT

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=120.0)

_BATCH_SIZE  = 5
_HAIKU_BATCH = 40


# ── F. Profile truncation ──────────────────────────────────────────────────────

def _truncate_profile(c: dict) -> dict:
    snippet = c.get("snippet", "")
    return {
        "url":      c.get("url", ""),
        "name":     c.get("name", ""),
        "headline": c.get("headline", ""),
        "snippet":  snippet[:500] if len(snippet) > 500 else snippet,
    }


# ── C. Haiku pre-filter ───────────────────────────────────────────────────────

def _prefilter_with_haiku(candidates: list, job_title: str, token_acc: dict | None = None) -> list:
    """
    Haiku relevance filter on the FULL deduplicated pool.
    Called before the dynamic cap so it can actually remove noise.
    Defaults to KEEP on parse failure — never silently drops candidates.
    """
    if len(candidates) <= 4:
        return candidates

    kept: list = []
    for batch_start in range(0, len(candidates), _HAIKU_BATCH):
        batch = candidates[batch_start : batch_start + _HAIKU_BATCH]
        lines = "\n".join(
            f"[{i + 1}] {c.get('name', '')} | {c.get('headline', '')} | {c.get('snippet', '')[:120]}"
            for i, c in enumerate(batch)
        )
        prompt = f"""You are filtering {len(batch)} LinkedIn profiles for a "{job_title}" role at Nimble — the AI search platform for production agents. Nimble hires people with backgrounds in AI, data, software engineering, B2B sales, product, and related technical/business fields.

KEEP (keep=true):
  AI / ML / LLM / data science / NLP
  Software engineering, backend, platform, infrastructure, DevOps
  Data engineering, analytics, data pipelines
  B2B sales, account executive, solutions engineering, business development
  Product management, product marketing, growth
  Startup or scaleup operators, GTM, revenue
  API or SaaS product experience
  Technical recruiting or talent (adjacent)
  Students or recent grads in CS, data science, or engineering

DROP (keep=false) — ONLY if there is CLEARLY ZERO connection to tech, software, or business:
  Pure clinical healthcare (nurse, doctor, dentist, pharmacist) with no tech role
  Skilled trades with no tech crossover (plumber, electrician, carpenter)
  Retail / hospitality / food service with no tech crossover
  Arts, entertainment, or creative fields with no tech or business role
  Pure academic research with zero industry experience and no tech stack
  Government / military with no software or technology role

When in doubt: keep=true.
Only mark keep=false when there is CLEARLY ZERO connection to technology, software, or business.

Profiles:
{lines}

Return ONLY valid JSON array, one entry per candidate, same order:
[{{"i": 1, "keep": true}}, {{"i": 2, "keep": false}}, ...]"""

        try:
            resp = _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=len(batch) * 16 + 64,
                messages=[{"role": "user", "content": prompt}],
            )
            if token_acc is not None:
                u = resp.usage
                token_acc["haiku_input"]  = token_acc.get("haiku_input",  0) + (getattr(u, "input_tokens",  0) or 0)
                token_acc["haiku_output"] = token_acc.get("haiku_output", 0) + (getattr(u, "output_tokens", 0) or 0)

            text = resp.content[0].text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            verdicts = json.loads(text)
            drop_set = {v["i"] - 1 for v in verdicts if not v.get("keep", True)}
            batch_kept = [c for i, c in enumerate(batch) if i not in drop_set]
            if drop_set:
                print(f"  [prefilter] Haiku dropped {len(drop_set)}/{len(batch)} in this batch")
            kept.extend(batch_kept)
        except Exception as e:
            print(f"  [prefilter] Haiku batch failed ({e}) — keeping all {len(batch)}")
            kept.extend(batch)

    return kept


# ── D. Python pre-ranking ─────────────────────────────────────────────────────

def _prerank_candidates(candidates: list, icp: dict) -> list:
    """
    Free Python keyword heuristics — zero API cost.
    Surfaces the best candidates before LLM work. NO cap applied here.
    """
    skills  = [s.lower() for s in icp.get("key_skills", [])]
    green   = [g.lower() for g in icp.get("green_flags", [])]
    red     = [r.lower() for r in icp.get("red_flags", [])]

    domain_signals     = ["startup", "scaleup", "b2b", "saas", "growth", "series"]
    availability_bonus = ["open to work", "looking for", "seeking", "available", "actively"]

    raw_locations = icp.get("location_context", "") or ""
    location_bonus = [loc.strip().lower() for loc in raw_locations.replace("/", ",").split(",") if len(loc.strip()) > 2]
    if not location_bonus:
        location_bonus = ["remote"]

    def _score(c: dict) -> float:
        text = (c.get("headline", "") + " " + c.get("snippet", "")).lower()
        pts  = 0.0
        pts += min(10, sum(2   for s in skills  if s in text))
        pts += min(5,  sum(1   for d in domain_signals if d in text))
        pts += sum(1.5 for g in green if g in text)
        pts -= sum(3   for r in red   if r in text)
        if any(a in text for a in availability_bonus):
            pts += 2
        if any(loc in text for loc in location_bonus):
            pts += 1
        return pts

    return sorted(candidates, key=_score, reverse=True)


# ── B. Static scoring context (cached across batches) ─────────────────────────

def _build_static_context(job_title: str, job_description: str, icp: dict) -> str:
    """
    Build the reusable scoring context once per run.
    The IDENTICAL string is passed to every batch — any variation breaks prompt caching.
    Must be >1024 tokens for Anthropic caching to activate.
    """
    return f"""You are a senior talent sourcing agent for Nimble (nimbleway.com).
Your job: score candidates for BIDIRECTIONAL fit — would Nimble want to hire them, AND would they genuinely want to join?

{NIMBLE_FOR_PROMPT}

══════════════════════════════════════════════
ROLE TO FILL
══════════════════════════════════════════════
Title: {job_title}

Job Description:
{job_description[:1500]}

══════════════════════════════════════════════
IDEAL CANDIDATE PROFILE
(from current Nimble employee analysis)
══════════════════════════════════════════════
{json.dumps(icp, indent=2)[:1200]}

══════════════════════════════════════════════
SCORING SCALE — calibrated to Nimble specifically
══════════════════════════════════════════════

9–10  Ex-Bright Data / Exa / Tavily / Apify / Oxylabs engineer or direct equivalent.
      Startup history (2+ companies under 200 people). Currently at Series A–C or recently left.
      Open to work signal visible, or ≤18 months at current role.
      Domain is core Nimble territory: web data, AI agents, retrieval, scraping infrastructure.

7–8   Production AI engineer or relevant IC at a B2B SaaS startup.
      2–3 year tenure — settled but not locked in.
      No explicit "open" signal but career trajectory shows consistent mobility.
      Domain overlap is strong (AI/ML, data infra, APIs, LLMs) even without exact Nimble match.

5–6   Relevant background (data, APIs, SaaS, cloud) but at a large enterprise.
      Limited startup history, or domain is adjacent rather than direct.
      Could be a fit but requires real effort to find and convert.

3–4   Some technical background but far from Nimble's core space.
      No startup history, no AI/data signals, or tenure signals suggest locked-in.
      A long shot.

1–2   Clear mismatch. Non-technical, wrong domain, no signals of relevance.
      Save everyone's time.

══════════════════════════════════════════════
INTEREST SCORE — start at 5, apply deltas
══════════════════════════════════════════════
POSITIVE signals (add):
  +2.0  "Open to Work" or "seeking" visible in profile — strongest available signal
  +2.0  Two or more startup stints (self-selects startup environments)
  +1.5  Changed jobs within last 6–12 months
  +1.0  At current role 18–36 months (settled but not locked)
  +1.0  At current role 4+ years, no visible promotion (may be ready to move)
  +1.0  Active LinkedIn presence visible in data
  +1.0  Remote-first or location-flexible signals in profile
  +1.0  Located in preferred location per ICP above
  +0.5  Career stage suited for a startup move

NEGATIVE signals (subtract):
  -1.5  FAANG / Fortune 500 for 5+ years, zero startup history
  -1.0  No domain overlap with Nimble's space

RULES: No signals → stay at 5. Missing data is NOT negative. Cap 1–10.

══════════════════════════════════════════════
CALIBRATION RULES
══════════════════════════════════════════════
- Realistic center of mass for a good search: 6–8. Most candidates land here.
- 9–10 is rare: reserve for near-perfect ICP match WITH clear interest signals.
- 1–3 is for clear mismatches only — not "I don't have enough info."
- Missing data is NOT a reason to score below 5. Evidence-based, not absence-of-evidence.
- Do NOT default to 5. Any evidence at all → go higher or lower.
- overall_score = (role_fit × 0.40) + (culture_fit × 0.30) + (interest × 0.30), rounded.

══════════════════════════════════════════════
OUTPUT FIELD INSTRUCTIONS
Write for a recruiter who knows Nimble but is not a technical expert.
══════════════════════════════════════════════

nimble_fit:
  WHY NIMBLE WOULD WANT THEM. 2–3 sentences.
  Be specific to their actual background — name the company, role, or skill.
  Write "Built RAG pipelines at a B2B SaaS startup" not "strong technical background."
  Write "3 years at Bright Data before joining a Series A AI company" not "relevant experience."

candidate_fit:
  WHY THEY MIGHT WANT NIMBLE. 2–3 sentences.
  Use career stage signals, tenure, startup history, and domain overlap.
  If signals are weak: write "Limited signals — interest would need to be verified directly."
  Do not manufacture enthusiasm where there is none.

friction:
  HONEST BLOCKERS. 1–2 sentences.
  Location mismatch, seniority gap, enterprise lock-in, no startup history,
  FAANG retention risk. Do not soften.
  If genuinely clean: write "None identified."

career_narrative:
  THE ARC. 2 sentences.
  What does their career tell you about them as a professional?
  What problems have they been solving? Where are they in their development?
  Example: "Spent 4 years at enterprise data companies building ETL pipelines before moving
  to two successive AI startups — trajectory suggests deliberate move toward the AI-native
  stack. Currently at Series B which suggests comfort with early-stage environments."

icp_match:
  ONE-LINE ICP SIGNAL MAP.
  Example: "Hits: production AI, startup background, B2B SaaS. Missing: web data domain,
  location unclear."

why_yes:
  REASONS TO PRIORITIZE (recruiter perspective — NOT "why the candidate would like Nimble").
  "Strong ICP match on 4/5 signals" not "Nimble's culture would appeal to them."
  2–3 items.

why_no:
  GENUINE DEPRIORITIZERS. Friction, fit gaps, conversion effort required.
  1–2 items. Omit or leave empty if genuinely clean.

role_fit_explanation:   1–2 sentences on role fit score specifically.
culture_fit_explanation: 1–2 sentences on culture/background fit vs Nimble ICP.
interest_explanation:   1–2 sentences explaining the interest score calculation.
reasoning:              2–3 sentence overall summary — worth reaching out or not, and why.
"""


# ── A. Batched Sonnet scoring ─────────────────────────────────────────────────

def _score_batch(batch: list, static_context: str, batch_num: int = 0) -> tuple[list, dict]:
    """
    Score one batch with Sonnet. Returns (scored_candidates, token_usage_dict).
    On max_tokens truncation: splits in half and retries both halves recursively.
    """
    n = len(batch)
    batch_prompt = f"""Score these {n} candidates for the role above. Return a JSON array of EXACTLY {n} objects in the SAME ORDER as the input.

CANDIDATES:
{json.dumps(batch, indent=2)[:3500]}

Return ONLY valid JSON array — no markdown, no explanation:
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
    "nimble_fit": "2-3 sentences on why Nimble would want them — specific to their actual background",
    "candidate_fit": "2-3 sentences on why they might want Nimble, or 'Limited signals — ...' if weak",
    "friction": "1-2 sentences on honest blockers, or 'None identified' if clean",
    "career_narrative": "2 sentences: what their career arc tells you about them as a professional",
    "icp_match": "One sentence: Hits: X, Y. Missing: A, B.",
    "role_fit_explanation": "1-2 sentences on role fit score",
    "culture_fit_explanation": "1-2 sentences on culture/background fit vs Nimble ICP",
    "interest_explanation": "1-2 sentences explaining the interest score",
    "reasoning": "2-3 sentence overall summary for the recruiter",
    "availability_signals": ["specific observable signal from profile"],
    "why_yes": ["reason recruiter should prioritize this person — not candidate motivation"],
    "why_no": ["genuine friction point or fit gap"],
    "career_snapshot": [
      {{"company": "Company Name", "role": "Job Title", "duration": "~2 years"}}
    ]
  }}
]"""

    resp = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": static_context, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": batch_prompt},
            ],
        }],
    )

    # Truncation detected — split and retry before attempting to parse
    if resp.stop_reason == "max_tokens":
        print(f"[score_batch] Truncated — retrying as 2x smaller batches")
        if len(batch) <= 1:
            print(f"[score_batch] Single candidate still truncated — skipping")
            return [], {}
        half = len(batch) // 2
        r1, u1 = _score_batch(batch[:half], static_context, batch_num)
        r2, u2 = _score_batch(batch[half:], static_context, batch_num)
        merged = {k: u1.get(k, 0) + u2.get(k, 0) for k in set(u1) | set(u2)}
        return r1 + r2, merged

    # Log cache hit / miss with percentage
    usage_dict: dict = {}
    if hasattr(resp, "usage"):
        u = resp.usage
        cache_read  = getattr(u, "cache_read_input_tokens",  0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        input_tok   = getattr(u, "input_tokens",  0) or 0
        output_tok  = getattr(u, "output_tokens", 0) or 0
        total_in    = cache_read + cache_write + input_tok
        pct         = int(cache_read / total_in * 100) if total_in else 0
        print(f"  [cache] batch {batch_num}: read={cache_read} write={cache_write} ({pct}% cached)")
        usage_dict = {
            "cache_read":   cache_read,
            "cache_write":  cache_write,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
        }

    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(text)
        return (result if isinstance(result, list) else []), usage_dict
    except json.JSONDecodeError as e:
        print(f"[score_batch] JSON parse error: {e}")
        return [], usage_dict


# ── Main scoring node ─────────────────────────────────────────────────────────

def score_candidates(state: TalentState) -> dict:
    print("[score_candidates] Starting scoring pipeline...")

    raw       = state.get("raw_candidates", [])
    icp       = state.get("icp", {})
    job_title = state.get("job_title", "")
    job_desc  = state.get("job_description", "")

    # 1. Deduplicate by URL across all fan-out iterations
    seen: set   = set()
    unique: list = []
    for c in raw:
        url = c.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(c)

    if not unique:
        return {"scored_candidates": []}

    # 2. Pre-rank with Python heuristics — NO CAP, let Haiku see the full pool
    unique = _prerank_candidates(unique, icp)

    # 3. Truncate profiles before sending to any LLM
    unique = [_truncate_profile(c) for c in unique]

    # 4. Haiku pre-filter on the FULL deduplicated pool
    token_acc: dict = {}
    pre_haiku   = len(unique)
    haiku_calls = 0
    if len(unique) > 4:
        unique      = _prefilter_with_haiku(unique, job_title, token_acc)
        haiku_calls = 1

    kept       = len(unique)
    filtered   = pre_haiku - kept
    filter_pct = int(filtered / pre_haiku * 100) if pre_haiku else 0
    print(f"[score_candidates] Haiku filtered {filtered}/{pre_haiku} ({filter_pct}%)")
    if pre_haiku > 20 and filter_pct < 20:
        print(f"[score_candidates] WARNING: Haiku filtering <20% — search queries may be too narrow or well-targeted")
    if filter_pct > 60:
        print(f"[score_candidates] WARNING: Haiku filtering >60% — search queries may be too broad")

    # 5. Dynamic cap after Haiku
    if kept >= 40:
        to_score = unique[:25]
        print(f"[score_candidates] {len(raw)} raw → {len(seen)} unique → Haiku kept {kept} → capping at top 25 for Sonnet")
    else:
        to_score = unique[:]
        label = "sending all" if kept >= 20 else f"Haiku kept only {kept} (low filter value)"
        print(f"[score_candidates] {len(raw)} raw → {len(seen)} unique → Haiku kept {kept} → {label} to Sonnet")

    if not to_score:
        return {
            "scored_candidates": [],
            "usage_stats": {
                "haiku_calls": haiku_calls, "sonnet_calls": 0,
                "candidates_scored": 0, "filtered_out": filtered, "est_cost_usd": 0.0,
            },
        }

    # 6. Build static context ONCE — identical string to every batch (cache key)
    static_context = _build_static_context(job_title, job_desc, icp)

    # 7. Batch 1 primes prompt cache; remaining batches run in parallel (cache reads)
    all_scored: list  = []
    sonnet_calls      = 0
    batches           = [to_score[i : i + _BATCH_SIZE] for i in range(0, len(to_score), _BATCH_SIZE)]
    total_batches     = len(batches)
    all_usage: dict   = dict(token_acc)  # start with Haiku tokens

    print(f"  [score_batch] 1/{total_batches} — scoring {len(batches[0])} candidates (cache warm-up)")
    try:
        result, batch_usage = _score_batch(batches[0], static_context, batch_num=1)
        all_scored.extend(result)
        sonnet_calls += 1
        for k, v in batch_usage.items():
            all_usage[k] = all_usage.get(k, 0) + v
    except Exception as e:
        print(f"  [score_batch] ERROR on batch 1: {e} — skipping")

    if len(batches) > 1:
        with ThreadPoolExecutor(max_workers=len(batches) - 1) as ex:
            future_to_num = {
                ex.submit(_score_batch, batch, static_context, idx + 2): idx + 2
                for idx, batch in enumerate(batches[1:])
            }
            for fut in as_completed(future_to_num):
                batch_num = future_to_num[fut]
                try:
                    result, batch_usage = fut.result()
                    all_scored.extend(result)
                    sonnet_calls += 1
                    for k, v in batch_usage.items():
                        all_usage[k] = all_usage.get(k, 0) + v
                    print(f"  [score_batch] {batch_num}/{total_batches} — done")
                except Exception as e:
                    print(f"  [score_batch] ERROR on batch {batch_num}: {e} — skipping")

    all_scored = sorted(all_scored, key=lambda x: x.get("overall_score", 0), reverse=True)

    # 8. Credit estimate
    # Sonnet: $3.00/M regular in, $0.30/M cache read, $3.75/M cache write, $15.00/M out
    # Haiku:  $1.00/M in, $5.00/M out
    est_cost = (
        (all_usage.get("haiku_input",  0) / 1_000_000) * 1.00 +
        (all_usage.get("haiku_output", 0) / 1_000_000) * 5.00 +
        (all_usage.get("cache_read",   0) / 1_000_000) * 0.30 +
        (all_usage.get("cache_write",  0) / 1_000_000) * 3.75 +
        (all_usage.get("input_tokens", 0) / 1_000_000) * 3.00 +
        (all_usage.get("output_tokens",0) / 1_000_000) * 15.00
    )

    # Accumulate across refinement iterations
    prev        = state.get("usage_stats", {})
    usage_stats = {
        "haiku_calls":       prev.get("haiku_calls", 0) + haiku_calls,
        "sonnet_calls":      prev.get("sonnet_calls", 0) + sonnet_calls,
        "candidates_scored": len(all_scored),
        "filtered_out":      prev.get("filtered_out", 0) + filtered,
        "est_cost_usd":      round(prev.get("est_cost_usd", 0.0) + est_cost, 3),
    }

    top_count = sum(1 for c in all_scored if c.get("overall_score", 0) >= 7)
    print(
        f"[score_candidates] Done — {len(all_scored)} scored, {top_count} strong (≥7) | "
        f"haiku={usage_stats['haiku_calls']} sonnet={usage_stats['sonnet_calls']} "
        f"est=${usage_stats['est_cost_usd']:.3f}"
    )
    return {"scored_candidates": all_scored, "usage_stats": usage_stats}

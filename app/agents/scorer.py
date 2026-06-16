"""
Phase 3 node: bidirectional scoring — does the company want them? Would they want to join?

Cost optimizations:
  A. Batched scoring: 5 candidates per Sonnet call
  B. Prompt caching: static context cached across batches (~90% cheaper after batch 1)
  C. Haiku pre-filter: cheap relevance pass on the FULL pool before Sonnet
  D. Python pre-ranking + dynamic cap: free heuristics surface best N; cap applied AFTER Haiku
  E. Post signal fetch runs CONCURRENTLY with Haiku pre-filter (no extra wall-clock cost)
  F. Profile truncation: strip excess tokens before any LLM call

Ordering in score_candidates():
  1. Deduplicate by URL
  2. _prerank_candidates()   — Python heuristics, no cap
  3. _truncate_profile()     — strip each profile before sending to LLMs
  4. Haiku pre-filter + post signal fetch run in parallel
  5. Dynamic cap             — send top N to Sonnet based on Haiku output size
  6. Inject post signals     — add post_signals field to capped candidates
  7. _build_static_context() — build ONCE, same string to every batch (cache key)
  8. Batch score with Sonnet — batch 1 primes cache, rest run in parallel
  9. _apply_custom_weights() — recompute overall_score using recruiter weights (Python, not LLM)
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from app.models import TalentState
from app.nimble_context import build_company_context
from app import nimble_client as nimble

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=120.0)

_BATCH_SIZE  = 5
_HAIKU_BATCH = 40
_POST_FETCH_N = 15   # fetch post signals for top N candidates after pre-ranking


# ── F. Profile truncation ──────────────────────────────────────────────────────

def _truncate_profile(c: dict) -> dict:
    snippet = c.get("snippet", "")
    return {
        "url":      c.get("url", ""),
        "name":     c.get("name", ""),
        "headline": c.get("headline", ""),
        "snippet":  snippet[:500] if len(snippet) > 500 else snippet,
    }


# ── E. LinkedIn post signal fetch ─────────────────────────────────────────────

def _fetch_post_signals(candidates: list) -> dict[str, str]:
    """
    Search for recent LinkedIn post/activity signals for the top N pre-ranked candidates.
    Runs concurrently with Haiku pre-filter — no extra wall-clock cost.
    Returns {candidate_url: signal_text}.
    """
    top = [c for c in candidates[:_POST_FETCH_N] if c.get("name")]
    if not top:
        return {}

    results: dict = {}
    with ThreadPoolExecutor(max_workers=min(8, len(top))) as pool:
        futures: dict = {}
        for c in top:
            name = c.get("name", "").strip()
            url  = c.get("url", "")
            # Search for indexed LinkedIn posts and pulse articles
            query = f'"{name}" (site:linkedin.com/posts OR site:linkedin.com/pulse)'
            futures[pool.submit(nimble.search, query, "general", 3)] = url

        for future in as_completed(futures):
            cand_url = futures[future]
            try:
                posts = future.result()
                snippets = []
                for p in posts[:4]:
                    desc = (p.get("description") or "").strip()
                    post_url = p.get("url", "")
                    if desc and "linkedin.com" in post_url:
                        snippets.append(desc[:250])
                if snippets:
                    results[cand_url] = " | ".join(snippets)
            except Exception as e:
                print(f"  [post_signals] {e}")

    found = sum(1 for v in results.values() if v)
    print(f"[post_signals] Found activity for {found}/{len(top)} candidates")
    return results


# ── C. Haiku pre-filter ───────────────────────────────────────────────────────

def _prefilter_with_haiku(
    candidates: list,
    job_title: str,
    company_name: str,
    icp: dict,
    token_acc: dict | None = None,
) -> list:
    """
    Haiku relevance filter on the FULL deduplicated pool.
    Called before the dynamic cap so it can actually remove noise.
    Defaults to KEEP on parse failure — never silently drops candidates.
    """
    if len(candidates) <= 4:
        return candidates

    key_skills    = icp.get("key_skills", [])
    typical_roles = icp.get("typical_roles", [])
    keep_hints    = ""
    if key_skills:
        keep_hints += f"\nKey skills to look for: {', '.join(key_skills[:8])}"
    if typical_roles:
        keep_hints += f"\nTypical roles: {', '.join(typical_roles[:5])}"

    kept: list = []
    for batch_start in range(0, len(candidates), _HAIKU_BATCH):
        batch = candidates[batch_start : batch_start + _HAIKU_BATCH]
        lines = "\n".join(
            f"[{i + 1}] {c.get('name', '')} | {c.get('headline', '')} | {c.get('snippet', '')[:120]}"
            for i, c in enumerate(batch)
        )
        prompt = f"""You are filtering {len(batch)} LinkedIn profiles for a "{job_title}" role at {company_name}.{keep_hints}

KEEP (keep=true) any profile with a plausible connection to the role or company's domain.
When in doubt: keep=true.

DROP (keep=false) ONLY when there is CLEARLY ZERO connection to the role:
  - Wrong field entirely (e.g. plumber applying for a software role)
  - No overlap with required skills, industry, or role type

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
            verdicts  = json.loads(text)
            drop_set  = {v["i"] - 1 for v in verdicts if not v.get("keep", True)}
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
    """Free Python keyword heuristics — zero API cost. NO cap applied here."""
    skills  = [s.lower() for s in icp.get("key_skills", [])]
    green   = [g.lower() for g in icp.get("green_flags", [])]
    red     = [r.lower() for r in icp.get("red_flags", [])]

    domain_signals     = ["startup", "scaleup", "b2b", "saas", "growth", "series"]
    availability_bonus = ["open to work", "looking for", "seeking", "available", "actively"]

    raw_locations  = icp.get("location_context", "") or ""
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


# ── 9. Apply custom weights (Python — never ask the LLM to do this) ───────────

def _apply_custom_weights(scored: list, weights: dict) -> list:
    """Recompute overall_score using recruiter-set weights. Clamps to 1–10."""
    w_role    = weights.get("role_fit",    0.40)
    w_culture = weights.get("culture_fit", 0.30)
    w_interest = weights.get("interest",   0.30)
    total = w_role + w_culture + w_interest
    if total <= 0:
        return scored
    w_role    /= total
    w_culture /= total
    w_interest /= total
    for c in scored:
        raw = (
            c.get("role_fit_score",    5) * w_role +
            c.get("culture_fit_score", 5) * w_culture +
            c.get("interest_score",    5) * w_interest
        )
        c["overall_score"] = max(1, min(10, round(raw)))
    return scored


# ── B. Static scoring context (cached across batches) ─────────────────────────

def _build_static_context(
    job_title: str,
    job_description: str,
    icp: dict,
    company_ctx: str,
    scoring_weights: dict,
    custom_signals: str,
) -> str:
    """
    Build the reusable scoring context once per run.
    The IDENTICAL string is passed to every batch — any variation breaks prompt caching.
    Must be >1024 tokens for Anthropic caching to activate.
    """
    w_role    = scoring_weights.get("role_fit",    0.40)
    w_culture = scoring_weights.get("culture_fit", 0.30)
    w_interest = scoring_weights.get("interest",   0.30)
    total = w_role + w_culture + w_interest or 1.0
    role_pct    = round(w_role    / total * 100)
    culture_pct = round(w_culture / total * 100)
    interest_pct = 100 - role_pct - culture_pct

    custom_block = ""
    if custom_signals and custom_signals.strip():
        custom_block = f"""
══════════════════════════════════════════════
RECRUITER PRIORITY SIGNALS
══════════════════════════════════════════════
The recruiter has flagged these as especially important for this search:

{custom_signals.strip()}

When you see clear evidence of these signals in a candidate's profile or posts, reflect that in
their dimension scores (role fit, culture fit, or interest — whichever is most relevant).
"""

    return f"""You are a senior talent sourcing agent.
Your job: score candidates for BIDIRECTIONAL fit — would the company want to hire them, AND would they genuinely want to join?

{company_ctx}

══════════════════════════════════════════════
ROLE TO FILL
══════════════════════════════════════════════
Title: {job_title}

Job Description:
{job_description[:1500]}

══════════════════════════════════════════════
IDEAL CANDIDATE PROFILE
(from current employee analysis)
══════════════════════════════════════════════
{json.dumps(icp, indent=2)[:1200]}
{custom_block}
══════════════════════════════════════════════
SCORING WEIGHTS (recruiter priorities)
══════════════════════════════════════════════
Role fit:             {role_pct}% — technical skills, domain experience, requirements match
Culture fit:          {culture_pct}% — background, company stage, ICP alignment
Likelihood to join:   {interest_pct}% — availability signals, career stage, tenure, openness

Score each dimension independently and accurately. The weighted overall score will be
calculated externally — your job is to score each dimension on its own merits.

══════════════════════════════════════════════
SCORING SCALE (apply to each dimension)
══════════════════════════════════════════════

9–10  Near-perfect match for this dimension. Strong direct evidence in profile/posts.
7–8   Strong fit. Clear relevant signals with minor gaps.
5–6   Moderate fit. Some relevant background but notable gaps or distance from ideal.
3–4   Weak fit. Few signals, mostly tangential.
1–2   Clear mismatch for this dimension.

══════════════════════════════════════════════
INTEREST SCORE — start at 5, apply deltas
══════════════════════════════════════════════
POSITIVE signals (add):
  +2.0  "Open to Work" or "seeking" visible in profile or posts
  +2.0  Two or more startup stints
  +1.5  Changed jobs within last 6–12 months
  +1.0  At current role 18–36 months (settled but not locked)
  +1.0  At current role 4+ years, no visible promotion
  +1.0  Remote-first or location-flexible signals
  +1.0  Located in preferred location per ICP above
  +0.5  Career stage suited for a startup move
  +1.0  Posts about job search, career transitions, or openness to new roles
  +0.5  Posts about startup life, entrepreneurship, or building something new

NEGATIVE signals (subtract):
  -1.5  FAANG / Fortune 500 for 5+ years, zero startup history
  -1.0  No domain overlap with the company's space

RULES: No signals → stay at 5. Missing data is NOT negative. Cap 1–10.

══════════════════════════════════════════════
POST SIGNALS FIELD
══════════════════════════════════════════════
Some candidates have a `post_signals` field containing snippets from their recent
LinkedIn posts and articles. Use this to:
  - Detect "open to work" or job-seeking language
  - Identify thought leadership and domain depth
  - Find startup affinity, builder mindset, or entrepreneurial signals
  - Spot career transitions or role changes they've announced
Post signals are ADDITIVE — the absence of a post_signals field is not a negative.

══════════════════════════════════════════════
CALIBRATION RULES
══════════════════════════════════════════════
- Realistic center of mass for a good search: 6–8.
- 9–10 is rare: reserve for near-perfect match WITH clear evidence.
- 1–3 is for clear mismatches only — not "I don't have enough info."
- Missing data is NOT a reason to score below 5. Evidence-based, not absence-of-evidence.
- Do NOT default to 5. Any evidence at all → go higher or lower.

══════════════════════════════════════════════
OUTPUT FIELD INSTRUCTIONS
Write for a recruiter who knows the company but is not a technical expert.
══════════════════════════════════════════════

company_fit:
  WHY THE COMPANY WOULD WANT THEM. 2–3 sentences.
  Be specific — name the actual company, role, or skill from their profile.

candidate_fit:
  WHY THEY MIGHT WANT TO JOIN. 2–3 sentences.
  Use career stage signals, tenure, startup history, domain overlap, and post signals if present.
  If signals are weak: write "Limited signals — interest would need to be verified directly."

friction:
  HONEST BLOCKERS. 1–2 sentences.
  Location mismatch, seniority gap, enterprise lock-in. Do not soften.
  If genuinely clean: write "None identified."

career_narrative:
  THE ARC. 2 sentences. What does their career tell you about them as a professional?

icp_match:
  ONE-LINE ICP SIGNAL MAP.
  Example: "Hits: production AI, startup background. Missing: domain overlap, location unclear."

why_yes:
  REASONS TO PRIORITIZE (recruiter perspective). 2–3 items.
  Include post-signal evidence if it's strong.

why_no:
  GENUINE DEPRIORITIZERS. 1–2 items. Omit or leave empty if genuinely clean.

role_fit_explanation:    1–2 sentences on role fit score specifically.
culture_fit_explanation: 1–2 sentences on culture/background fit vs ICP.
interest_explanation:    1–2 sentences explaining the interest score calculation, including any post signals used.
reasoning:               2–3 sentence overall summary — worth reaching out or not, and why.
"""


# ── A. Batched Sonnet scoring ─────────────────────────────────────────────────

def _score_batch(batch: list, static_context: str, batch_num: int = 0) -> tuple[list, dict]:
    """
    Score one batch with Sonnet. Returns (scored_candidates, token_usage_dict).
    On max_tokens truncation: splits in half and retries both halves recursively.
    """
    n = len(batch)
    batch_prompt = f"""Score these {n} candidates for the role above. Return a JSON array of EXACTLY {n} objects in the SAME ORDER as the input.

If a candidate has a `post_signals` field, use it to refine your assessment of their interests,
availability, and domain fit. Post signals can raise or lower any dimension score.

CANDIDATES:
{json.dumps(batch, indent=2)[:4000]}

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
    "company_fit": "2-3 sentences on why the company would want them — specific to their actual background",
    "candidate_fit": "2-3 sentences on why they might want to join, or 'Limited signals — ...' if weak",
    "friction": "1-2 sentences on honest blockers, or 'None identified' if clean",
    "career_narrative": "2 sentences: what their career arc tells you about them as a professional",
    "icp_match": "One sentence: Hits: X, Y. Missing: A, B.",
    "role_fit_explanation": "1-2 sentences on role fit score",
    "culture_fit_explanation": "1-2 sentences on culture/background fit vs ICP",
    "interest_explanation": "1-2 sentences explaining the interest score, including any post signal evidence",
    "reasoning": "2-3 sentence overall summary for the recruiter",
    "availability_signals": ["specific observable signal from profile or posts"],
    "why_yes": ["reason recruiter should prioritize this person"],
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
            "cache_read":    cache_read,
            "cache_write":   cache_write,
            "input_tokens":  input_tok,
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

    raw               = state.get("raw_candidates", [])
    icp               = state.get("icp", {})
    job_title         = state.get("job_title", "")
    job_desc          = state.get("job_description", "")
    company_name      = (state.get("company_name") or "the company").strip()
    company_ctx       = build_company_context(state)
    target_candidates = state.get("target_candidates", 5)
    sonnet_cap        = max(25, target_candidates * 4)
    scoring_weights   = state.get("scoring_weights") or {}
    custom_signals    = state.get("custom_signals") or ""

    # Normalize weights — fall back to defaults if missing/zero
    _w_role    = scoring_weights.get("role_fit",    0.40)
    _w_culture = scoring_weights.get("culture_fit", 0.30)
    _w_interest = scoring_weights.get("interest",   0.30)
    _total = _w_role + _w_culture + _w_interest
    if _total <= 0:
        scoring_weights = {"role_fit": 0.40, "culture_fit": 0.30, "interest": 0.30}
    else:
        scoring_weights = {
            "role_fit":    _w_role    / _total,
            "culture_fit": _w_culture / _total,
            "interest":    _w_interest / _total,
        }

    # 1. Deduplicate by URL — pre-seed with URLs from previous runs
    seen: set    = set(state.get("seen_urls", []))
    unique: list = []
    excluded     = 0
    for c in raw:
        url = c.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(c)
        elif url in set(state.get("seen_urls", [])):
            excluded += 1
    if excluded:
        print(f"[score_candidates] Excluded {excluded} previously seen candidates")

    if not unique:
        return {"scored_candidates": []}

    # 2. Pre-rank with Python heuristics — NO CAP
    unique = _prerank_candidates(unique, icp)

    # 3. Truncate profiles before sending to any LLM
    unique = [_truncate_profile(c) for c in unique]

    # 4. Haiku pre-filter + LinkedIn post signal fetch run IN PARALLEL
    token_acc: dict = {}
    haiku_calls     = 0

    if len(unique) > 4:
        with ThreadPoolExecutor(max_workers=2) as parallel_pool:
            haiku_future = parallel_pool.submit(
                _prefilter_with_haiku, unique, job_title, company_name, icp, token_acc
            )
            posts_future = parallel_pool.submit(_fetch_post_signals, unique)
            unique       = haiku_future.result()
            post_signals = posts_future.result()
        haiku_calls = 1
    else:
        post_signals = _fetch_post_signals(unique)

    pre_haiku   = len(state.get("raw_candidates", []))
    kept        = len(unique)
    filtered    = max(0, pre_haiku - kept - excluded)
    filter_pct  = int(filtered / max(pre_haiku, 1) * 100)
    print(f"[score_candidates] Haiku filtered {filtered}/{pre_haiku} ({filter_pct}%)")

    # 5. Dynamic cap after Haiku — scales with target_candidates
    if kept > sonnet_cap:
        to_score = unique[:sonnet_cap]
        print(f"[score_candidates] capping at top {sonnet_cap} for Sonnet (target={target_candidates})")
    else:
        to_score = unique[:]
        print(f"[score_candidates] sending {kept} to Sonnet (target={target_candidates})")

    if not to_score:
        return {
            "scored_candidates": [],
            "usage_stats": {
                "haiku_calls": haiku_calls, "sonnet_calls": 0,
                "candidates_scored": 0, "filtered_out": filtered, "est_cost_usd": 0.0,
            },
        }

    # 6. Inject post signals into candidates going to Sonnet
    for c in to_score:
        sig = post_signals.get(c.get("url", ""), "")
        if sig:
            c["post_signals"] = sig

    # 7. Build static context ONCE (cache key — must be identical across all batches)
    static_context = _build_static_context(
        job_title, job_desc, icp, company_ctx, scoring_weights, custom_signals
    )

    # 8. Batch 1 primes prompt cache; remaining batches run in parallel
    all_scored: list = []
    sonnet_calls     = 0
    batches          = [to_score[i : i + _BATCH_SIZE] for i in range(0, len(to_score), _BATCH_SIZE)]
    total_batches    = len(batches)
    all_usage: dict  = dict(token_acc)

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

    # 9. Apply custom weights in Python — never trust the LLM to do weighted arithmetic
    all_scored = _apply_custom_weights(all_scored, scoring_weights)
    all_scored = sorted(all_scored, key=lambda x: x.get("overall_score", 0), reverse=True)

    # 10. Cost estimate
    est_cost = (
        (all_usage.get("haiku_input",  0) / 1_000_000) * 1.00 +
        (all_usage.get("haiku_output", 0) / 1_000_000) * 5.00 +
        (all_usage.get("cache_read",   0) / 1_000_000) * 0.30 +
        (all_usage.get("cache_write",  0) / 1_000_000) * 3.75 +
        (all_usage.get("input_tokens", 0) / 1_000_000) * 3.00 +
        (all_usage.get("output_tokens",0) / 1_000_000) * 15.00
    )

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
        f"weights role={scoring_weights['role_fit']:.0%} culture={scoring_weights['culture_fit']:.0%} "
        f"interest={scoring_weights['interest']:.0%} | "
        f"haiku={usage_stats['haiku_calls']} sonnet={usage_stats['sonnet_calls']} "
        f"est=${usage_stats['est_cost_usd']:.3f}"
    )
    return {"scored_candidates": all_scored, "usage_stats": usage_stats}

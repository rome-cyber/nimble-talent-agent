"""
Researches the role before candidate search begins.
Runs all Nimble searches in parallel via ThreadPoolExecutor.
No page extract — search snippets + ICP are sufficient and an extract costs 12s.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from app.models import TalentState
from app import nimble_client as nimble
from app import cache as _cache

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_SEARCHES = [
    ('{title} job requirements startup site:linkedin.com OR site:greenhouse.io OR site:lever.co', "general"),
    ('nimbleway "{title}" OR "nimble" job description responsibilities', "general"),
    ('"{title}" requirements "web data" OR "web scraping" OR "data collection" startup', "general"),
    ('"{title}" job description startup "Series A" OR "Series B" skills must-have', "general"),
]


def research_role(state: TalentState) -> dict:
    job_title = state.get("job_title", "")
    additional_notes = state.get("additional_notes", "").strip()
    force = state.get("force_refresh", False)

    if not additional_notes:
        cached = _cache.get_role(job_title, force_refresh=force)
        if cached:
            print(f"[research_role] Cache hit for '{job_title}'")
            return {"job_description": cached, "_role_from_cache": True}

    print(f"[research_role] Researching '{job_title}' (parallel)...")

    queries = [(q.format(title=job_title), f) for q, f in _SEARCHES]

    # Fetch careers page from cache or schedule a live extract
    careers_cached = _cache.get_careers_page(force_refresh=force)

    # Run all searches + careers extract (if not cached) in parallel
    raw_results = []
    careers_text = careers_cached or ""

    tasks: dict = {}
    for q, focus in queries:
        tasks[("search", q, focus)] = None
    if not careers_cached:
        tasks[("extract", "https://www.nimbleway.com/careers", None)] = None

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {}
        for key in tasks:
            kind, arg, extra = key
            if kind == "search":
                futures[pool.submit(nimble.search, arg, extra, 6)] = ("search",)
            else:
                futures[pool.submit(nimble.extract, arg)] = ("extract",)

        for future in as_completed(futures):
            kind = futures[future][0]
            try:
                result = future.result()
                if kind == "extract":
                    careers_text = result
                    if careers_text:
                        _cache.save_careers_page(careers_text)
                        print("  [research_role] Careers page fetched and cached")
                else:
                    for r in result:
                        raw_results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("description", ""),
                        })
            except Exception as e:
                print(f"  [research_role] Error ({kind}): {e}")

    icp = state.get("icp", {})
    notes_section = f"\nADDITIONAL NOTES FROM HIRING MANAGER:\n{additional_notes}" if additional_notes else ""

    prompt = f"""You are building a job description for Nimble (nimbleway.com), the AI search platform for production agents.

ROLE TO FILL: {job_title}

NIMBLE CAREERS PAGE:
{careers_text[:1500] if careers_text else "(not available)"}

IDEAL CANDIDATE PROFILE (from current Nimble employees):
{json.dumps(icp, indent=2)[:800]}

RESEARCH FINDINGS (job postings and role requirements from similar companies):
{json.dumps(raw_results[:20], indent=2)}
{notes_section}

Write a complete, specific job description for this role AT NIMBLE.
Tailor it to Nimble's context: a growing web data startup where engineers own things end-to-end.
Base it on the research — do not invent requirements that contradict the ICP.

Return ONLY valid JSON:
{{
  "job_description": "Full markdown job description: role overview, key responsibilities (5-7 bullets), must-have requirements, nice-to-haves. Nimble-specific context throughout.",
  "summary": "One sentence: what this role is and why it matters at Nimble"
}}"""

    resp = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(text)
        job_description = result.get("job_description", "")
        summary = result.get("summary", "")
    except json.JSONDecodeError:
        job_description = f"{job_title} at Nimble"
        summary = ""

    if not additional_notes and job_description:
        _cache.save_role(job_title, job_description)

    print(f"[research_role] Done: {len(job_description)} chars — {summary}")
    return {"job_description": job_description, "_role_from_cache": False}

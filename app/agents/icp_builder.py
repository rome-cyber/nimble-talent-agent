"""
Phase 1 nodes: fetch current employee profiles, then build an ICP.
Searches and the website extract run in parallel via ThreadPoolExecutor.
Both nodes check the SQLite cache first — ICP is keyed by employee hash
so it only regenerates when the employee list actually changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from app.models import TalentState
from app import nimble_client as nimble
from app import cache

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _linkedin_company_url(state: TalentState) -> str:
    """Normalise the user-provided LinkedIn company URL to the /people/ page."""
    raw = (state.get("company_linkedin_url") or "").strip().rstrip("/")
    if not raw:
        return ""
    if "/people" not in raw:
        raw = raw + "/people/"
    if not raw.startswith("http"):
        raw = "https://" + raw
    return raw


def _employee_queries(state: TalentState) -> list[str]:
    """
    Build supplemental LinkedIn search queries from company name + website.
    Falls back gracefully when fields are missing.
    """
    name    = (state.get("company_name") or "").strip()
    website = (state.get("company_website") or "").strip().lower()
    # strip protocol so we get the bare domain for search
    domain  = re.sub(r'^https?://', '', website).rstrip("/") if website else ""

    queries: list[str] = []

    if domain:
        queries.append(f'"{domain}" site:linkedin.com/in')
    if name:
        queries.append(f'"{name}" engineer OR developer site:linkedin.com/in')
        queries.append(f'"{name}" sales OR "account executive" OR product site:linkedin.com/in')
    if name and domain:
        queries.append(f'"{name}" "{domain}" site:linkedin.com/in')

    return queries


def _company_signals(state: TalentState) -> list[str]:
    """Signals used to confirm a profile actually belongs to the company."""
    name    = (state.get("company_name") or "").strip().lower()
    website = (state.get("company_website") or "").strip().lower()
    domain  = re.sub(r'^https?://', '', website).rstrip("/") if website else ""

    signals = []
    if domain:
        signals.append(domain)
    if name:
        signals.append(name)
    return signals


def _parse_company_page(content: str) -> list[dict]:
    """Extract employee profiles from a LinkedIn company people page."""
    profiles = []
    seen: set = set()

    urls = re.findall(r'https?://(?:www\.)?linkedin\.com/in/[\w%-]+', content)

    for url in urls:
        url = url.rstrip('/').split('?')[0]
        if url in seen:
            continue
        seen.add(url)

        idx = content.find(url)
        surrounding = content[max(0, idx - 200): idx + 200]
        lines = [l.strip() for l in surrounding.split('\n') if l.strip()]
        name = lines[0] if lines else ''
        headline = lines[1] if len(lines) > 1 else ''

        profiles.append({"url": url, "name": name, "headline": headline, "snippet": ""})

    print(f"  [company_page] Parsed {len(profiles)} profiles from LinkedIn company page")
    return profiles


def _hash_employees(profiles: list) -> str:
    urls = sorted(p.get("url", "") for p in profiles if p.get("url"))
    return hashlib.md5(json.dumps(urls).encode()).hexdigest()[:12]


def _hash_icp_inputs(profiles: list, candidate_icp: str) -> str:
    """Cache key that captures both who's on the team AND what the hiring team wants."""
    urls = sorted(p.get("url", "") for p in profiles if p.get("url"))
    payload = json.dumps({"urls": urls, "icp": candidate_icp.strip()})
    return hashlib.md5(payload.encode()).hexdigest()[:12]


def fetch_employees(state: TalentState) -> dict:
    force = state.get("force_refresh", False)

    cached_profiles, cached_context = cache.get_employees(force_refresh=force)
    if cached_profiles is not None:
        print(f"[fetch_employees] Cache hit — {len(cached_profiles)} employees")
        return {"company_context": cached_context, "employee_raw": cached_profiles, "_from_cache": True}

    print("[fetch_employees] Cache miss — fetching in parallel...")

    linkedin_url  = _linkedin_company_url(state)
    company_website = (state.get("company_website") or "").strip()
    about_url     = ""
    if company_website:
        domain = re.sub(r'^https?://', '', company_website).rstrip("/")
        about_url = f"https://{domain}/about"

    tasks: dict = {}
    if about_url:
        tasks["extract_about"] = ("extract", about_url)
    if linkedin_url:
        tasks["extract_linkedin"] = ("extract", linkedin_url)
    for q in _employee_queries(state):
        tasks[q] = ("search", q)

    if not tasks:
        print("[fetch_employees] No company info provided — skipping employee fetch")
        return {"company_context": "", "employee_raw": [], "_from_cache": False}

    company_context = ""
    profiles: list = []
    seen_urls: set = set()
    signals = _company_signals(state)

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {}
        for key, (kind, arg) in tasks.items():
            if kind == "extract":
                futures[pool.submit(nimble.extract, arg)] = key
            else:
                futures[pool.submit(nimble.search, arg, "social", 10)] = "search"

        for future in as_completed(futures):
            kind = futures[future]
            try:
                result = future.result()
                if kind == "extract_about":
                    company_context = result
                    # fallback: try homepage if /about returned too little
                    if len(company_context) < 200 and company_website:
                        domain = re.sub(r'^https?://', '', company_website).rstrip("/")
                        company_context = nimble.extract(f"https://{domain}")
                elif kind == "extract_linkedin":
                    linkedin_profiles = _parse_company_page(result)
                    for p in linkedin_profiles:
                        url = p.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            profiles.append(p)
                else:
                    for r in result:
                        url = r.get("url", "")
                        if "linkedin.com/in/" in url and url not in seen_urls:
                            seen_urls.add(url)
                            title = r.get("title", "")
                            name  = title.split(" - ")[0].strip() if " - " in title else title.split("|")[0].strip()
                            profiles.append({
                                "url":     url,
                                "name":    name,
                                "headline": title,
                                "snippet": r.get("description", ""),
                            })
            except Exception as e:
                print(f"  [fetch_employees] Error ({kind}): {e}")

    # Validate: only keep profiles that actually reference this company
    if signals:
        before   = len(profiles)
        profiles = [
            p for p in profiles
            if any(sig in (p.get("url","") + " " + p.get("headline","") + " " + p.get("snippet","")).lower()
                   for sig in signals)
        ]
        print(f"[fetch_employees] Validation: {before} → {len(profiles)} confirmed employees")
    else:
        # No signals to validate against — trust the LinkedIn company page results
        print(f"[fetch_employees] {len(profiles)} profiles (no validation signals)")

    cache.save_employees(profiles, company_context)
    print(f"[fetch_employees] Found {len(profiles)} employees — saved to cache")
    return {"company_context": company_context, "employee_raw": profiles, "_from_cache": False}


def build_icp(state: TalentState) -> dict:
    force         = state.get("force_refresh", False)
    profiles      = state.get("employee_raw", [])
    candidate_icp = (state.get("candidate_icp") or "").strip()

    icp_hash   = _hash_icp_inputs(profiles, candidate_icp)
    cached_icp = cache.get_icp(force_refresh=force, employee_hash=icp_hash)
    if cached_icp is not None:
        print(f"[build_icp] Cache hit (hash={icp_hash}) — skipping rebuild")
        return {"icp": cached_icp, "_icp_from_cache": True}

    print(f"[build_icp] Cache miss (hash={icp_hash}) — building ICP...")

    company_name    = (state.get("company_name") or "the company").strip()
    company_context = state.get("company_context", "")
    candidate_icp   = (state.get("candidate_icp") or "").strip()

    hiring_team_section = ""
    if candidate_icp:
        hiring_team_section = f"""
HIRING TEAM'S CANDIDATE ICP (treat as authoritative — incorporate these requirements):
{candidate_icp}
"""

    prompt = f"""You are analyzing current employees at {company_name} to build an Ideal Candidate Profile (ICP) for talent sourcing.

COMPANY CONTEXT:
{company_context[:2000] if company_context else "(not available)"}
{hiring_team_section}
CURRENT EMPLOYEE LINKEDIN PROFILES ({len(profiles)} found):
{json.dumps(profiles[:30], indent=2)}

Analyze the profiles. Extract patterns from what you can actually observe — do not invent.

Focus especially on CAREER TRAJECTORY: What did people do BEFORE joining? What companies, what roles, what career stage?
This is the most predictive signal for finding new candidates who would actually join.

{"If no employee profiles were provided, base the ICP entirely on the hiring team's requirements above and the company context." if not profiles else ""}

Return ONLY valid JSON:
{{
  "company_summary": "2-3 sentence description of {company_name} and its culture based on the data",
  "typical_roles": ["role type observed", "..."],
  "typical_backgrounds": ["background pattern observed before joining", "..."],
  "common_company_types": ["types of companies people came FROM", "..."],
  "career_trajectory_patterns": ["e.g. 'Enterprise SaaS AE → {company_name} AE'"],
  "key_skills": ["skill 1", "skill 2"],
  "green_flags": ["signal that strongly suggests someone would thrive here", "..."],
  "red_flags": ["signal that suggests someone would NOT fit", "..."],
  "location_context": "where employees are based",
  "seniority_range": "typical seniority level observed",
  "culture_notes": "what the data suggests about the working culture and values"
}}"""

    resp = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        icp = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[build_icp] JSON parse error: {e} — fallback")
        icp = {}

    cache.save_icp(icp, employee_hash=icp_hash)
    print(f"[build_icp] Done — {len(icp.get('key_skills', []))} skills, saved to cache (hash={icp_hash})")
    return {"icp": icp, "_icp_from_cache": False}

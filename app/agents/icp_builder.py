"""
Phase 1 nodes: fetch current Nimble employee profiles, then build an ICP.
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
from app.nimble_context import BASE_CANDIDATE_ICP

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# LinkedIn company people page — most reliable source of current employees
_LINKEDIN_COMPANY_URL = "https://www.linkedin.com/company/nimbledata/people/"

# Supplemental Google searches — keyed to the real company identifiers
# Use "nimbleway.com" (domain) or "nimbledata" (LinkedIn slug) to avoid
# false positives from the Swedish "Nimbleway" governance consultancy.
_EMPLOYEE_QUERIES = [
    '"nimbleway.com" site:linkedin.com/in',
    'nimble "web data" OR "data extraction" OR "scraping API" site:linkedin.com/in',
    'nimble "nimbleway.com" engineer OR developer site:linkedin.com/in',
    'nimble "nimbleway.com" sales OR "account executive" OR BDR site:linkedin.com/in',
    'nimble "web scraping" OR "data pipeline" startup Israel site:linkedin.com/in',
    'nimble "nimbleway.com" product OR marketing site:linkedin.com/in',
    '"nimbledata" OR "nimbleway.com" site:linkedin.com/in',
]

# Keywords that confirm a profile is from the real Nimble (nimbleway.com)
# At least one must appear in the profile text or URL.
_NIMBLE_SIGNALS = [
    "nimbleway.com", "nimbleway", "nimbledata",
    "web data", "web scraping", "data extraction", "scraping api",
    "@nimble",
]


def _parse_company_page(content: str) -> list[dict]:
    """
    Extract employee profiles from the LinkedIn company people page.
    The extracted text contains names, roles, and linkedin.com/in/ links.
    """
    profiles = []
    seen: set = set()

    # Find every linkedin.com/in/ URL in the extracted content
    urls = re.findall(r'https?://(?:www\.)?linkedin\.com/in/[\w%-]+', content)

    for url in urls:
        # Normalise trailing slashes / query params
        url = url.rstrip('/').split('?')[0]
        if url in seen:
            continue
        seen.add(url)

        # Try to extract a name near this URL in the surrounding text
        idx = content.find(url)
        surrounding = content[max(0, idx - 200): idx + 200]

        # LinkedIn page text usually has "Name\nTitle at Company" before the URL
        lines = [l.strip() for l in surrounding.split('\n') if l.strip()]
        name = lines[0] if lines else ''
        headline = lines[1] if len(lines) > 1 else ''

        profiles.append({"url": url, "name": name, "headline": headline, "snippet": ""})

    print(f"  [company_page] Parsed {len(profiles)} profiles from LinkedIn company page")
    return profiles


def _hash_employees(profiles: list) -> str:
    """Stable 12-char hash of employee URLs — detects when team composition changes."""
    urls = sorted(p.get("url", "") for p in profiles if p.get("url"))
    return hashlib.md5(json.dumps(urls).encode()).hexdigest()[:12]


def fetch_employees(state: TalentState) -> dict:
    force = state.get("force_refresh", False)

    cached_profiles, cached_context = cache.get_employees(force_refresh=force)
    if cached_profiles is not None:
        print(f"[fetch_employees] Cache hit — {len(cached_profiles)} employees")
        return {"company_context": cached_context, "employee_raw": cached_profiles, "_from_cache": True}

    print("[fetch_employees] Cache miss — fetching in parallel...")

    tasks = {
        "extract_about":   ("extract", "https://www.nimbleway.com/about"),
        "extract_linkedin": ("extract", _LINKEDIN_COMPANY_URL),
    }
    for q in _EMPLOYEE_QUERIES:
        tasks[q] = ("search", q)

    company_context = ""
    profiles = []
    seen_urls: set = set()

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
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
                    if len(company_context) < 200:
                        company_context = nimble.extract("https://www.nimbleway.com")
                elif kind == "extract_linkedin":
                    # Parse employee profiles directly from the LinkedIn company people page
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
                            name = title.split(" - ")[0].strip() if " - " in title else title.split("|")[0].strip()
                            profiles.append({
                                "url": url,
                                "name": name,
                                "headline": title,
                                "snippet": r.get("description", ""),
                            })
            except Exception as e:
                print(f"  [fetch_employees] Error ({kind}): {e}")

    # Validate: only keep profiles that actually reference the real Nimble
    def _is_real_nimble_employee(p: dict) -> bool:
        text = (p.get("url", "") + " " + p.get("headline", "") + " " + p.get("snippet", "")).lower()
        return any(sig in text for sig in _NIMBLE_SIGNALS)

    before = len(profiles)
    profiles = [p for p in profiles if _is_real_nimble_employee(p)]
    print(f"[fetch_employees] Validation: {before} → {len(profiles)} confirmed Nimble employees")

    cache.save_employees(profiles, company_context)
    print(f"[fetch_employees] Found {len(profiles)} employees — saved to cache")
    return {"company_context": company_context, "employee_raw": profiles, "_from_cache": False}


def _merge_with_base(auto: dict) -> dict:
    """
    Merge the auto-built employee ICP with BASE_CANDIDATE_ICP.
    Base fields always win on conflict — they represent the real Nimble ICP.
    Employee-analysis fields (typical_roles, career_trajectory_patterns, etc.)
    are kept as supplementary data since they reflect real observed patterns.
    """
    merged = dict(auto)

    # String fields: base always wins
    for field in ("company_summary", "location_context", "culture_notes"):
        if field in BASE_CANDIDATE_ICP:
            merged[field] = BASE_CANDIDATE_ICP[field]

    # List fields: base items first, then any unique items from employee analysis
    for field in ("key_skills", "green_flags", "red_flags"):
        base_items = BASE_CANDIDATE_ICP.get(field, [])
        auto_items = auto.get(field, [])
        base_lower = {s.lower() for s in base_items}
        extra = [s for s in auto_items if s.lower() not in base_lower]
        merged[field] = base_items + extra

    return merged


def build_icp(state: TalentState) -> dict:
    force    = state.get("force_refresh", False)
    profiles = state.get("employee_raw", [])

    # Hash-based cache check: only skip rebuild if team composition unchanged
    emp_hash   = _hash_employees(profiles)
    cached_icp = cache.get_icp(force_refresh=force, employee_hash=emp_hash)
    if cached_icp is not None:
        print(f"[build_icp] Cache hit (hash={emp_hash}) — skipping rebuild")
        return {"icp": _merge_with_base(cached_icp), "_icp_from_cache": True}

    print(f"[build_icp] Cache miss (hash={emp_hash}) — building ICP...")

    company_context = state.get("company_context", "")

    prompt = f"""You are analyzing current Nimble (nimbleway.com) employees to build an Ideal Candidate Profile (ICP) for talent sourcing.

COMPANY CONTEXT (from nimbleway.com):
{company_context[:2000]}

CURRENT EMPLOYEE LINKEDIN PROFILES ({len(profiles)} found):
{json.dumps(profiles[:30], indent=2)}

Analyze these profiles carefully. Extract patterns from what you can actually observe — do not invent.

Focus especially on CAREER TRAJECTORY: What did these people do BEFORE Nimble? What companies, what roles, what career stage?
This is the most predictive signal for finding new candidates who would actually join.

Return ONLY valid JSON:
{{
  "company_summary": "2-3 sentence description of Nimble and its culture based on the data",
  "typical_roles": ["role type observed at Nimble", "..."],
  "typical_backgrounds": ["background pattern observed before joining Nimble", "..."],
  "common_company_types": ["types of companies people came FROM before Nimble", "..."],
  "career_trajectory_patterns": ["e.g. 'Enterprise SaaS AE → Nimble AE', 'Big data company engineer → Nimble engineer'"],
  "education_patterns": ["education pattern observed", "..."],
  "key_skills": ["skill 1", "skill 2"],
  "green_flags": ["signal that strongly suggests someone would thrive at Nimble", "..."],
  "red_flags": ["signal that suggests someone would NOT fit", "..."],
  "location_context": "where Nimble employees are based based on what you see",
  "seniority_range": "typical seniority level observed",
  "culture_notes": "what the data suggests about Nimble's working culture and values"
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

    # Base ICP (from Nimble's real ICP) always overrides the employee analysis
    icp = _merge_with_base(icp)

    cache.save_icp(icp, employee_hash=emp_hash)
    print(f"[build_icp] Done — {len(icp.get('key_skills', []))} skills, saved to cache (hash={emp_hash})")
    return {"icp": icp, "_icp_from_cache": False}

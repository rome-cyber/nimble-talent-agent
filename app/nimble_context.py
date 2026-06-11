"""
Company context utilities — builds the prompt context string from TalentState.

All company-specific values (name, website, description, candidate ICP) come from
the user via the UI and flow through TalentState. Nothing is hardcoded here.
"""

from __future__ import annotations


def build_company_context(state: dict) -> str:
    """
    Build the company context block injected into every agent prompt.
    Uses only what the user actually provided — never invents details.
    """
    name         = (state.get("company_name") or "").strip()
    website      = (state.get("company_website") or "").strip()
    about        = (state.get("company_context") or "").strip()
    candidate_icp = (state.get("candidate_icp") or "").strip()

    if not name and not candidate_icp:
        return ""

    parts: list[str] = []

    if name:
        line = f"COMPANY: {name}"
        if website:
            line += f"  ({website})"
        parts.append(line)

    if about:
        parts.append(f"\nABOUT THE COMPANY:\n{about[:1200]}")

    if candidate_icp:
        parts.append(
            f"\nIDEAL CANDIDATE PROFILE (provided by the hiring team — treat as authoritative):\n"
            f"{candidate_icp}"
        )

    return "\n".join(parts)

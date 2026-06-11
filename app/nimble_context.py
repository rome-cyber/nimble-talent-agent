"""
Nimble company context and ICP — source of truth for all agent prompts.
This file is law. Do not override or soften anything written here.
"""

# ── Company description ────────────────────────────────────────────────────────

COMPANY_DESCRIPTION = """\
Nimble is redefining AI search for the era of autonomous agents.

Generic AI search tools are optimized for broad, one-size-fits-all answers. But when AI
systems move into production, enterprises run into the limitations of generic search:
incomplete results, inconsistent accuracy, limited control, and little visibility into how
answers are generated.

Nimble solves this by giving AI agents reliable access to live web data with the accuracy,
control, governance, completeness, and ease-of-use required for real production workflows.
Our platform combines web search, deep site navigation, structured extraction, and
configurable retrieval into a unified system built specifically for AI agents and enterprise
use cases.

The result is higher-quality, more trustworthy outputs tailored to your exact use case —
enabling enterprises and AI-native companies to deploy agents with confidence.

Nimble is the AI search platform for production agents.
Generic AI search works for broad answers. Nimble is built for agents that need accurate,
complete, and trustworthy web data in real-world workflows. Nimble combines live web search,
deep site navigation, and structured extraction into a unified system optimized for
production use cases — giving teams more control, governance, completeness, and reliability
than traditional AI search systems. The result is higher-quality data retrieval for your
exact use case, enabling agents to reason, automate, and make decisions with confidence.
"""

# ── Customer ICP ───────────────────────────────────────────────────────────────

CUSTOMER_ICP = """\
Nimble's ideal customer is an AI-native company or enterprise AI platform operationalizing
agents in production that depends on reliable live web data to power workflows, agents, or
AI applications.

PRIMARY ICP:
- Series A → public growth-stage AI startups
- Companies building / deploying on AI-native infrastructure
- Enterprise AI platform companies (platforms helping enterprises deploy AI)

Typical characteristics:
- 20–2,000 employees or 10k+ followers on LinkedIn
- Raised $10M–$500M
- Already have dedicated AI/platform engineering teams
- Already deploying AI beyond experimentation

Verticals (to be validated):
- CPG, Insurance, Finance, FinTech, PE
- Data vendors, Legal tech, GTM tech

BUYER PERSONAS (Technical Champion):
- Head of AI Engineering
- Staff AI Engineer
- Applied AI Lead
- AI Infrastructure Engineer
- Founding / Senior Engineer
- Director / VP / Head of R&D
- Product leaders
- CTO (startup)

TECHNICAL MATURITY:
Our ICP is NOT: "People trying AI."
Our ICP IS: "Teams operationalizing autonomous agents."

Indicators a company fits the ICP:
- AI Engineering titles in the org
- Using an agent framework (LangChain, LlamaIndex, CrewAI, etc.)
- .ai URL
- Bay Area based
- Using Tavily or Exa
- Founded after 2021
- Sponsoring key AI events
- Homepage mentions AI
- Building verticalized AI that needs web search
- Strong monitoring use case

GEOGRAPHIC FOCUS:
Very Silicon Valley / enterprise-tech concentrated.
Strongest: SF Bay Area, NYC, Seattle, London.

Customer profile strongly overlaps with:
- OpenAI ecosystem
- Anthropic ecosystem
- LangChain / LlamaIndex ecosystem
- YC AI companies

ORGANIZATIONAL SIGNALS that a company fits Nimble's ICP:
A company probably fits if they:
- Already have AI engineers
- Already run agents in production
- Complain about retrieval quality
- Need multi-step reasoning
- Care about citations / provenance
- Require controlled browsing
- Operate in regulated workflows
- Run expensive research operations
- Need autonomous web interaction
"""

# ── Authoritative candidate ICP ───────────────────────────────────────────────
# These fields are law. build_icp() merges the auto-built employee analysis with
# this dict — BASE_CANDIDATE_ICP wins on every conflict.

BASE_CANDIDATE_ICP = {
    "company_summary": (
        "Nimble is the AI search platform for production agents — combining live web search, "
        "deep site navigation, and structured extraction for AI agents that need accurate, "
        "complete, and trustworthy web data in real-world workflows. The team is deeply "
        "technical, production-focused, and AI-native. SF Bay Area / Israel HQ, global team, "
        "growth-stage startup serving AI-native companies and enterprise AI platforms."
    ),
    "key_skills": [
        "AI agent frameworks (LangChain, LlamaIndex, CrewAI)",
        "production LLM systems / RAG pipelines",
        "web data / scraping / data extraction",
        "B2B SaaS or API product experience",
        "retrieval quality and data provenance",
        "enterprise or technical sales",
        "distributed systems / backend engineering",
        "Python",
        "data pipelines",
        "AI infrastructure",
    ],
    "green_flags": [
        "Operationalizing AI agents in production — not just experimentation",
        "Background at AI-native companies: OpenAI, Anthropic, Bright Data, Exa, Tavily, Apify, Oxylabs, Zyte, YC AI companies",
        "Used or built with agent frameworks: LangChain, LlamaIndex, CrewAI",
        "Series A–C startup or scaleup background",
        "B2B enterprise or API product experience",
        "Open to work or recently changed jobs (strong interest signal)",
        "Located in SF Bay Area, NYC, Seattle, London, or Israel",
        "Shipped things at production scale — not just research",
        "Domain overlap: web data, retrieval, AI search, data infrastructure",
        "Worked in regulated workflows, research automation, or data-intensive AI",
    ],
    "red_flags": [
        "Zero connection to AI, data, APIs, or production software",
        "Pure academic / research background with no production deployment",
        "Only large enterprise, government, or non-tech experience with no startup history",
        "No evidence of operating in fast-moving startup environments",
    ],
    "location_context": "SF Bay Area, NYC, Seattle, London, Israel (Tel Aviv)",
    "culture_notes": (
        "Nimble operates at the intersection of AI agents and enterprise data. The culture "
        "values deep technical craft, production reliability, fast shipping, and B2B customer "
        "obsession. The ICP customer is teams operationalizing autonomous agents — so the ideal "
        "hire deeply understands that world. NOT a research culture. NOT a try-AI culture. "
        "A ship-AI-in-production culture."
    ),
}


# ── Condensed version for prompt injection ────────────────────────────────────

NIMBLE_FOR_PROMPT = f"""\
ABOUT NIMBLE (nimbleway.com):
{COMPANY_DESCRIPTION.strip()}

NIMBLE'S CUSTOMER ICP (who they sell to — reveals what domain expertise and culture fit look like internally):
{CUSTOMER_ICP.strip()}

WHAT THIS MEANS FOR HIRING — GREEN FLAGS FOR ANY NIMBLE CANDIDATE:
- Experience with AI agents, LLM systems, retrieval pipelines, or production ML
- Background at companies in Nimble's ecosystem: OpenAI, Anthropic, Bright Data, Exa, Tavily,
  LangChain, LlamaIndex, Apify, Oxylabs, Zyte, YC AI startups, or similar AI-native companies
- Production engineering mindset — shipped things at scale, not just research
- Familiarity with agent frameworks (LangChain, LlamaIndex, CrewAI, etc.)
- Enterprise / B2B SaaS or API product experience
- Startup or scaleup background (3+ employees at companies <200 people)
- Located in SF Bay Area, NYC, Seattle, or London — or remote-first

RED FLAGS:
- Zero connection to AI, data, APIs, or production software
- Pure research / academic background with no production deployment
- Only big enterprise / government / non-tech experience
"""

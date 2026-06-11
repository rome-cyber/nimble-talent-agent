# AI Talent Sourcing Agent

An open-source AI recruiting agent that searches LinkedIn at scale, builds an Ideal Candidate Profile (ICP) from your current team, and scores every candidate bidirectionally — would your company want them, and would they want your company?

Works for any company. You bring your own API keys and company context; the agent handles the rest.

## How it works

1. **ICP builder** — Scrapes your LinkedIn company page to learn who's on your team, then synthesizes a structured Ideal Candidate Profile. You can enrich this with your own free-text ICP.
2. **Role researcher** — Reads your careers page to build a real job description for the role you're filling.
3. **Candidate finder** — Generates 8 Boolean LinkedIn search strategies from the ICP and fans them out in parallel.
4. **Scorer** — Pre-ranks with Claude Haiku, then batch-scores the top candidates with Claude Sonnet. Each candidate gets role fit, culture fit, and interest scores plus narrative explanations.

## Prerequisites

- Python 3.9+
- Node 18+
- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)
- **Nimble API key** — used for LinkedIn search and web extraction — [nimbleway.com](https://nimbleway.com)

## Local setup

```bash
git clone https://github.com/rome-cyber/nimble-talent-agent
cd nimble-talent-agent

# Backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Create .env in project root
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
echo "NIMBLE_API_KEY=..." >> .env

# Start backend (port 8000)
cd backend && uvicorn api:app --port 8000 --reload
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev
# Opens at http://localhost:5174
```

Then open the app and fill in the **Company Setup** card at the top:
- **Company name** — required
- **Company website** — used to scrape careers page and /about
- **LinkedIn company URL** — `linkedin.com/company/your-company`
- **Candidate ICP** — optional free-text description of your ideal hire; treated as authoritative and merged into the AI-generated ICP

## Deploy to Railway (one-click self-hosted)

Railway hosts the backend (Python + SSE) and serves the built React frontend from the same process — no Vercel needed.

**1. Fork or push the repo to GitHub.**

**2. Create a new Railway project:**
- Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
- Select your fork

**3. Add environment variables** in the Railway dashboard:
```
ANTHROPIC_API_KEY=sk-ant-...
NIMBLE_API_KEY=...
```

**4. Deploy.** Railway picks up `nixpacks.toml` automatically, builds the frontend, installs Python deps, and starts uvicorn. Your URL is shown in the dashboard (e.g. `https://your-app.railway.app`).

> **SQLite note:** Railway's filesystem is ephemeral — the cache resets on each deploy. First run after a deploy will refetch employees and rebuild the ICP; subsequent runs within the same deployment are fully cached. For a persistent cache, add a Railway Volume mounted at `/app` (the project root).

## Architecture

| File | What it does |
|---|---|
| `graph.py` | LangGraph StateGraph — 6-node pipeline with routing logic |
| `app/agents/icp_builder.py` | Fetches employee profiles from LinkedIn, builds ICP |
| `app/agents/role_researcher.py` | Researches the role via the careers page |
| `app/agents/candidate_finder.py` | Generates 8 Boolean search queries, fans out parallel searches |
| `app/agents/scorer.py` | Haiku pre-filter → Sonnet batch scoring with prompt caching |
| `app/nimble_context.py` | `build_company_context()` — assembles company context from state |
| `app/nimble_client.py` | Thin wrapper around Nimble Search + Extract APIs |
| `app/cache.py` | SQLite cache with TTL management |
| `backend/api.py` | FastAPI — SSE streaming, run management, serves React build |
| `frontend/` | React + Vite + Tailwind |

## Caching

Three caches in `talent_cache.db` (SQLite):

| Cache | TTL / key | Invalidated when |
|---|---|---|
| Employee profiles | 7 days | Stale or force-refreshed |
| ICP | Hash of employee URLs + candidate ICP text | Employee list or ICP text changes |
| Role description | Job title | New title searched |

Check cache state: `GET /api/cache`. Force full refresh: toggle **Force refresh** in the UI.

## Cost estimate

A typical run (8 queries × 30 results, 240 candidates) costs roughly $0.05–$0.15 depending on cache state:
- Haiku pre-filter: ~$0.01
- Sonnet batch scoring (top 40 candidates): ~$0.04–$0.12
- ICP build: ~$0.01 (cached after first run)

Prompt caching is enabled by default — cache hit rates of 70–80% on repeated runs.

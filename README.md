# Nimble Talent Agent

Internal AI-powered talent sourcing tool for Nimble. Given a job title, it searches LinkedIn at scale, builds an Ideal Candidate Profile from current Nimble employees, and scores every candidate bidirectionally — would Nimble want them, and would they want Nimble?

## Prerequisites

- Python 3.9+
- Node 18+
- A `.env` file in the project root with:
  ```
  ANTHROPIC_API_KEY=sk-ant-...
  NIMBLE_API_KEY=...
  ```

## Start the backend

```bash
cd nimble-talent-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd backend && uvicorn api:app --port 8000 --reload
```

## Start the frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5174
```

## How caching works

The agent caches aggressively to avoid re-fetching and re-scoring on every run. Three separate caches live in `talent_cache.db` (SQLite):

- **Employee profiles** (TTL: 7 days) — LinkedIn company page extract + search results. Only refetched when stale or force-refreshed.
- **ICP** (keyed by employee hash) — rebuilt only when the employee list changes.
- **Role descriptions** (keyed by job title) — rebuilt when a new title is searched.

Check current cache state: `GET /api/cache`. Force a full refresh: enable "Force refresh" in the UI.

## Architecture

| File | What it does |
|---|---|
| `graph.py` | LangGraph StateGraph — defines the 6-node pipeline and routing logic |
| `app/agents/icp_builder.py` | Fetches Nimble employee profiles, builds ICP from them |
| `app/agents/role_researcher.py` | Researches the role using Nimble search + careers page |
| `app/agents/candidate_finder.py` | Generates 8 Boolean search queries, fans out 8 parallel searches |
| `app/agents/scorer.py` | Pre-ranks → Haiku filter → Sonnet batch scoring with prompt caching |
| `app/nimble_context.py` | Source of truth for Nimble's company description and candidate ICP |
| `app/nimble_client.py` | Thin wrapper around Nimble Search + Extract APIs |
| `app/cache.py` | SQLite cache layer with TTL management |
| `backend/api.py` | FastAPI server — SSE streaming, run management |
| `frontend/` | React + Vite + Tailwind frontend |

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import threading
import time
import traceback
import uuid
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from graph import build_graph
from app.cache import get_cache_info
from app.search_db import (
    auto_save_search, name_search, list_searches, get_search, delete_search,
    get_role_signals,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_runs: dict[str, Queue] = {}
_run_users: dict[str, str] = {}  # run_id → user_id

# ── Timing ─────────────────────────────────────────────────────────────────────

_PHASE_SECS = {
    "fetch_employees": 12,
    "build_icp": 5,
    "research_role": 5,
    "generate_queries": 4,
    "search_one_query": 18,
    "score_candidates": 10,
}
_TOTAL_SECS = float(sum(_PHASE_SECS.values()))


class _Tracker:
    def __init__(self):
        self._start = time.monotonic()
        self._total = _TOTAL_SECS
        self._done = 0.0
        self._n_expected = 5

    def phase_done(self, phase: str, n_searches: int = 0):
        if phase == "search_one_query":
            self._done += _PHASE_SECS["search_one_query"] / max(self._n_expected, 1)
        else:
            self._done += _PHASE_SECS.get(phase, 0)
            if phase == "generate_queries" and n_searches:
                self._n_expected = n_searches

    def add_refinement(self, n: int):
        self._total += _PHASE_SECS["generate_queries"] + _PHASE_SECS["score_candidates"] + _PHASE_SECS["search_one_query"] * n // 5
        self._n_expected += n

    def snapshot(self) -> dict:
        elapsed = time.monotonic() - self._start
        f = min(self._done / self._total, 0.98)
        remaining = max(0.0, (elapsed / f) - elapsed) if f > 0.04 else self._total - elapsed
        return {"fraction": round(f, 3), "elapsed": int(elapsed), "remaining": max(1, int(remaining))}


# ── Graph runner ───────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    job_title: str
    additional_notes: str = ""
    force_refresh: bool = False
    company_name: str = ""
    company_website: str = ""
    company_linkedin_url: str = ""
    candidate_icp: str = ""


class RenameRequest(BaseModel):
    name: str


def _run_graph(run_id: str, req: RunRequest, user_id: str = ""):
    q = _runs[run_id]

    def emit(event: dict):
        q.put(event)

    try:
        graph = build_graph()
        past_signals = get_role_signals(req.job_title)
        input_state = {
            "job_title": req.job_title,
            "additional_notes": req.additional_notes,
            "force_refresh": req.force_refresh,
            "company_name": req.company_name,
            "company_website": req.company_website,
            "company_linkedin_url": req.company_linkedin_url,
            "candidate_icp": req.candidate_icp,
            "raw_candidates": [],
            "all_queries_used": [],
            "iteration": 0,
            "past_signals": past_signals,
        }

        tracker = _Tracker()
        prev: dict = {}
        prev_raw = 0

        for current in graph.stream(input_state, stream_mode="values"):

            if "company_context" in current and "company_context" not in prev:
                from_cache = current.get("_from_cache", False)
                count = len(current.get("employee_raw", []))
                tracker.phase_done("fetch_employees")
                emit({"type": "phase", "phase": "fetch_employees",
                      "message": f"{count} employee profiles" + (" (cached)" if from_cache else ""),
                      "from_cache": from_cache, **tracker.snapshot()})

            if "icp" in current and "icp" not in prev:
                from_cache = current.get("_icp_from_cache", False)
                tracker.phase_done("build_icp")
                emit({"type": "phase", "phase": "build_icp",
                      "message": "Ideal candidate profile built" + (" (cached)" if from_cache else ""),
                      "from_cache": from_cache, **tracker.snapshot()})
                emit({"type": "icp", "data": current["icp"]})

            if "job_description" in current and "job_description" not in prev:
                from_cache = current.get("_role_from_cache", False)
                tracker.phase_done("research_role")
                emit({"type": "phase", "phase": "research_role",
                      "message": "Role requirements researched" + (" (cached)" if from_cache else ""),
                      "from_cache": from_cache, **tracker.snapshot()})
                emit({"type": "job_description", "text": current["job_description"]})

            if "search_queries" in current:
                prev_q = len(prev.get("search_queries", []))
                curr_q = len(current.get("search_queries", []))
                if curr_q != prev_q:
                    iteration = current.get("iteration", 1)
                    tracker.phase_done("generate_queries", n_searches=curr_q)
                    if iteration > 1:
                        tracker.add_refinement(curr_q)
                    emit({"type": "phase", "phase": "generate_queries",
                          "message": f"{curr_q} queries · iteration {iteration}",
                          "from_cache": False, **tracker.snapshot()})
                    emit({"type": "queries", "data": current.get("all_queries_used", [])})

            curr_raw = len(current.get("raw_candidates", []))
            if curr_raw > prev_raw:
                new = curr_raw - prev_raw
                tracker.phase_done("search_one_query")
                emit({"type": "phase", "phase": "search_one_query",
                      "message": f"+{new} candidates  ({curr_raw} total)",
                      "from_cache": False, **tracker.snapshot()})
                prev_raw = curr_raw

            if "scored_candidates" in current:
                prev_s = len(prev.get("scored_candidates", []))
                curr_s = len(current.get("scored_candidates", []))
                if curr_s != prev_s:
                    top = sum(1 for c in current["scored_candidates"] if c.get("overall_score", 0) >= 7)
                    tracker.phase_done("score_candidates")
                    emit({"type": "phase", "phase": "score_candidates",
                          "message": f"{curr_s} scored · {top} strong",
                          "from_cache": False, **tracker.snapshot()})
                    emit({"type": "candidates", "data": current["scored_candidates"]})

            prev = current

        elapsed    = int(time.monotonic() - tracker._start)
        usage      = prev.get("usage_stats", {}) if prev else {}
        candidates = prev.get("scored_candidates", []) if prev else []
        queries    = prev.get("all_queries_used", []) if prev else []
        icp        = prev.get("icp") if prev else None
        try:
            auto_save_search(
                search_id=run_id,
                job_title=req.job_title,
                company_name=req.company_name,
                additional_notes=req.additional_notes,
                queries=queries,
                candidates=candidates,
                icp=icp,
                user_id=user_id,
            )
        except Exception as _e:
            print(f"[auto_save] warning: {_e}")
        emit({"type": "done", "elapsed": elapsed, "usage": usage, "search_id": run_id})

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[run_graph] ERROR:\n{tb}")
        emit({"type": "error", "message": str(e) or type(e).__name__, "traceback": tb})
    finally:
        q.put(None)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/api/run")
async def start_run(req: RunRequest, x_user_id: str = Header(default="")):
    if not req.job_title.strip():
        raise HTTPException(400, "job_title is required")
    run_id = str(uuid.uuid4())
    _runs[run_id] = Queue()
    _run_users[run_id] = x_user_id
    threading.Thread(target=_run_graph, args=(run_id, req, x_user_id), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str):
    if run_id not in _runs:
        raise HTTPException(404, "Run not found")

    q = _runs[run_id]
    loop = asyncio.get_event_loop()

    async def generate():
        try:
            while True:
                event = await loop.run_in_executor(None, lambda: q.get(timeout=300))
                if event is None:
                    yield 'data: {"type":"sentinel"}\n\n'
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e) or type(e).__name__})}\n\n"
        finally:
            _runs.pop(run_id, None)
            _run_users.pop(run_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/cache")
async def cache_status():
    return get_cache_info()


@app.get("/api/searches")
async def get_searches(x_user_id: str = Header(default="")):
    return list_searches(user_id=x_user_id)


@app.get("/api/searches/{search_id}")
async def get_search_detail(search_id: str, x_user_id: str = Header(default="")):
    result = get_search(search_id)
    if not result:
        raise HTTPException(404, "Search not found")
    return result


@app.patch("/api/searches/{search_id}")
async def rename_search(search_id: str, req: RenameRequest, x_user_id: str = Header(default="")):
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    name_search(search_id, req.name, user_id=x_user_id)
    return {"ok": True}


@app.delete("/api/searches/{search_id}")
async def remove_search(search_id: str, x_user_id: str = Header(default="")):
    delete_search(search_id, user_id=x_user_id)
    return {"ok": True}


# ── Serve React SPA (production) ───────────────────────────────────────────────

_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/")
    async def root():
        return FileResponse(str(_dist / "index.html"))

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(str(_dist / "index.html"))

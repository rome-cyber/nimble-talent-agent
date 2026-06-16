"""
Supabase (PostgreSQL) storage for search history and candidate learning signals.

Tables:
  searches          — saved runs, scoped per user
  candidate_signals — signals from top-scoring candidates, shared across all users
                      for agent learning (not scoped by user)
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras

_DATABASE_URL = os.getenv("DATABASE_URL", "")


@contextmanager
def _conn():
    con = psycopg2.connect(_DATABASE_URL)
    con.autocommit = False
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def ensure_schema():
    if not _DATABASE_URL:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id               TEXT PRIMARY KEY,
                    name             TEXT NOT NULL,
                    named            BOOLEAN DEFAULT FALSE,
                    user_id          TEXT DEFAULT '',
                    job_title        TEXT NOT NULL,
                    company_name     TEXT DEFAULT '',
                    additional_notes TEXT DEFAULT '',
                    queries          JSONB DEFAULT '[]',
                    candidates       JSONB DEFAULT '[]',
                    icp              JSONB,
                    strong_count     INTEGER DEFAULT 0,
                    total_count      INTEGER DEFAULT 0,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS candidate_signals (
                    id                   SERIAL PRIMARY KEY,
                    search_id            TEXT NOT NULL,
                    linkedin_url         TEXT NOT NULL,
                    overall_score        INTEGER NOT NULL,
                    role_type            TEXT NOT NULL,
                    key_companies        JSONB DEFAULT '[]',
                    skills               JSONB DEFAULT '[]',
                    availability_signals JSONB DEFAULT '[]',
                    why_yes              JSONB DEFAULT '[]',
                    created_at           TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(search_id, linkedin_url)
                );

                CREATE INDEX IF NOT EXISTS idx_searches_user    ON searches(user_id, named, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_signals_score    ON candidate_signals(overall_score);
                CREATE INDEX IF NOT EXISTS idx_signals_role     ON candidate_signals(role_type);
            """)


try:
    ensure_schema()
except Exception as _e:
    print(f"[search_db] schema init skipped: {_e}")


# ── Write ───────────────────────────────────────────────────────────────────────

def auto_save_search(
    search_id: str,
    job_title: str,
    company_name: str,
    additional_notes: str,
    queries: list,
    candidates: list,
    icp: dict | None,
    user_id: str = "",
) -> str:
    if not _DATABASE_URL:
        return search_id
    d = datetime.utcnow()
    default_name = f"{job_title} · {d.strftime('%b %-d')}"
    strong = sum(1 for c in candidates if c.get("overall_score", 0) >= 7)
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """INSERT INTO searches
                   (id, name, named, user_id, job_title, company_name, additional_notes,
                    queries, candidates, icp, strong_count, total_count)
                   VALUES (%s,%s,FALSE,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    search_id, default_name, user_id,
                    job_title, company_name, additional_notes,
                    json.dumps(queries),
                    json.dumps(candidates),
                    json.dumps(icp) if icp else None,
                    strong, len(candidates),
                ),
            )
    _save_candidate_signals(search_id, job_title, candidates)
    return search_id


def _save_candidate_signals(search_id: str, job_title: str, candidates: list):
    if not _DATABASE_URL:
        return
    role_type = "_".join(job_title.lower().split())
    rows = []
    for c in candidates:
        if c.get("overall_score", 0) < 7:
            continue
        url = c.get("url", "")
        if not url:
            continue
        companies = [
            s.get("company", "") for s in (c.get("career_snapshot") or [])
            if s.get("company")
        ]
        headline = c.get("headline", "")
        skills = [w.strip("•|,") for w in headline.split() if len(w) > 4 and w[0].isupper()][:8]
        rows.append((
            search_id, url, c.get("overall_score", 0), role_type,
            json.dumps(companies[:6]),
            json.dumps(skills),
            json.dumps(c.get("availability_signals") or []),
            json.dumps((c.get("why_yes") or [])[:4]),
        ))
    if not rows:
        return
    with _conn() as con:
        with con.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO candidate_signals
                   (search_id, linkedin_url, overall_score, role_type,
                    key_companies, skills, availability_signals, why_yes)
                   VALUES %s ON CONFLICT (search_id, linkedin_url) DO NOTHING""",
                rows,
            )


def name_search(search_id: str, name: str, user_id: str = ""):
    if not _DATABASE_URL:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE searches SET name=%s, named=TRUE WHERE id=%s AND user_id=%s",
                (name.strip(), search_id, user_id),
            )


def delete_search(search_id: str, user_id: str = ""):
    if not _DATABASE_URL:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "DELETE FROM searches WHERE id=%s AND user_id=%s",
                (search_id, user_id),
            )
            # candidate_signals intentionally kept — shared learning data


# ── Read ────────────────────────────────────────────────────────────────────────

def list_searches(user_id: str = "") -> list:
    if not _DATABASE_URL:
        return []
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, name, job_title, additional_notes, queries,
                          icp, strong_count, total_count, created_at
                   FROM searches
                   WHERE named=TRUE AND user_id=%s
                   ORDER BY created_at DESC LIMIT 50""",
                (user_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "id":               r["id"],
            "name":             r["name"],
            "job_title":        r["job_title"],
            "additional_notes": r["additional_notes"] or "",
            "queries":          r["queries"] or [],
            "icp":              r["icp"],
            "strong_count":     r["strong_count"],
            "total_count":      r["total_count"],
            "saved_at":         int(r["created_at"].timestamp() * 1000),
            "candidates":       [],  # loaded on demand via get_search
        }
        for r in rows
    ]


def get_search(search_id: str) -> dict | None:
    if not _DATABASE_URL:
        return None
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM searches WHERE id=%s", (search_id,))
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id":               row["id"],
        "name":             row["name"],
        "job_title":        row["job_title"],
        "additional_notes": row["additional_notes"] or "",
        "queries":          row["queries"] or [],
        "icp":              row["icp"],
        "strong_count":     row["strong_count"],
        "total_count":      row["total_count"],
        "saved_at":         int(row["created_at"].timestamp() * 1000),
        "candidates":       row["candidates"] or [],
    }


def get_role_signals(job_title: str) -> list:
    """Return aggregated signals from past high-scoring candidates for similar roles.
    Not scoped by user — the agent learns from everyone's runs.
    """
    if not _DATABASE_URL:
        return []
    title_words = set(job_title.lower().split())
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT key_companies, skills, availability_signals, why_yes, role_type
                   FROM candidate_signals
                   WHERE overall_score >= 7
                   ORDER BY created_at DESC LIMIT 300"""
            )
            rows = cur.fetchall()

    relevant = []
    for r in rows:
        role_words = set(r["role_type"].replace("_", " ").split())
        if title_words & role_words:
            relevant.append(r)

    if not relevant:
        return []

    companies: dict[str, int] = {}
    skills: dict[str, int] = {}
    avail: dict[str, int] = {}
    why: list[str] = []

    for r in relevant:
        for c in (r["key_companies"] or []):
            companies[c] = companies.get(c, 0) + 1
        for s in (r["skills"] or []):
            skills[s] = skills.get(s, 0) + 1
        for a in (r["availability_signals"] or []):
            avail[a] = avail.get(a, 0) + 1
        why.extend(r["why_yes"] or [])

    return [{
        "top_companies":         sorted(companies, key=companies.get, reverse=True)[:8],
        "common_skills":         sorted(skills, key=skills.get, reverse=True)[:8],
        "availability_patterns": sorted(avail, key=avail.get, reverse=True)[:5],
        "why_yes_examples":      list(dict.fromkeys(why))[:6],
        "sample_count":          len(relevant),
    }]

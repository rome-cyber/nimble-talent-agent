"""
SQLite cache for data that doesn't need refreshing on every run:
  - Nimble employee LinkedIn profiles
  - Company context (nimbleway.com)
  - Built ICP  (keyed by employee hash — regenerates only when team changes)
  - Researched role descriptions (keyed by job title)
  - Careers page HTML (expensive extract — 12s — cached for 30 days)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "talent_cache.db"

_EMPLOYEES_TTL_DAYS = 30
_ROLE_TTL_DAYS      = 30
_CAREERS_TTL_DAYS   = 30
_ICP_TTL_DAYS       = 90   # long TTL when employee hash matches


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema():
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS employees (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT UNIQUE NOT NULL,
                name         TEXT,
                headline     TEXT,
                snippet      TEXT,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_context (
                id           INTEGER PRIMARY KEY,
                content      TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS icp (
                id            INTEGER PRIMARY KEY,
                icp_json      TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT UNIQUE NOT NULL,
                description  TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS careers_page (
                id           INTEGER PRIMARY KEY,
                content      TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
        """)
    # Migration: add employee_hash column if not yet present
    with _conn() as con:
        try:
            con.execute("ALTER TABLE icp ADD COLUMN employee_hash TEXT")
            con.commit()
        except Exception:
            pass  # column already exists


_ensure_schema()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat()


def _is_fresh(updated_at: str, ttl_days: int) -> bool:
    try:
        age = datetime.utcnow() - datetime.fromisoformat(updated_at)
        return age < timedelta(days=ttl_days)
    except Exception:
        return False


def _days_ago(updated_at: str) -> int:
    try:
        age = datetime.utcnow() - datetime.fromisoformat(updated_at)
        return age.days
    except Exception:
        return 999


# ── Employees ──────────────────────────────────────────────────────────────────

def get_employees(force_refresh: bool = False) -> tuple[list, str] | tuple[None, None]:
    """Return (profiles, company_context) from cache, or (None, None) if stale/missing."""
    with _conn() as con:
        ctx_row  = con.execute("SELECT content, updated_at FROM company_context WHERE id=1").fetchone()
        emp_rows = con.execute("SELECT url, name, headline, snippet, updated_at FROM employees").fetchall()

    if not ctx_row or not emp_rows:
        return None, None
    if force_refresh:
        return None, None
    if not _is_fresh(ctx_row["updated_at"], _EMPLOYEES_TTL_DAYS):
        return None, None

    profiles = [dict(r) for r in emp_rows]
    return profiles, ctx_row["content"]


def save_employees(profiles: list, company_context: str):
    now = _now()
    with _conn() as con:
        con.execute(
            "INSERT INTO company_context(id, content, updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
            (company_context, now),
        )
        for p in profiles:
            con.execute(
                "INSERT INTO employees(url, name, headline, snippet, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET name=excluded.name, headline=excluded.headline, "
                "snippet=excluded.snippet, updated_at=excluded.updated_at",
                (p.get("url", ""), p.get("name", ""), p.get("headline", ""), p.get("snippet", ""), now),
            )


# ── ICP ─────────────────────────────────────────────────────────────────────────
# Hash-based caching: only regenerate when the employee list actually changes.

def get_icp(force_refresh: bool = False, employee_hash: str | None = None) -> dict | None:
    """Return cached ICP dict, or None if stale/hash mismatch/missing."""
    with _conn() as con:
        row = con.execute("SELECT icp_json, updated_at, employee_hash FROM icp WHERE id=1").fetchone()
    if not row or force_refresh:
        return None

    # Hash-based validation: if employee list changed, regenerate
    if employee_hash:
        stored_hash = row["employee_hash"]
        if stored_hash and stored_hash != employee_hash:
            print(f"[cache] ICP hash mismatch (stored={stored_hash[:8]} current={employee_hash[:8]}) — regenerating")
            return None
        # Hash matches — use long TTL
        if not _is_fresh(row["updated_at"], _ICP_TTL_DAYS):
            return None
    else:
        # No hash available — fall back to time-based TTL
        if not _is_fresh(row["updated_at"], _EMPLOYEES_TTL_DAYS):
            return None

    try:
        return json.loads(row["icp_json"])
    except Exception:
        return None


def save_icp(icp: dict, employee_hash: str | None = None):
    with _conn() as con:
        con.execute(
            "INSERT INTO icp(id, icp_json, employee_hash, updated_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET icp_json=excluded.icp_json, "
            "employee_hash=excluded.employee_hash, updated_at=excluded.updated_at",
            (json.dumps(icp), employee_hash, _now()),
        )


# ── Role descriptions ──────────────────────────────────────────────────────────

def get_role(title: str, force_refresh: bool = False) -> str | None:
    """Return cached role description for this title, or None if stale/missing."""
    with _conn() as con:
        row = con.execute(
            "SELECT description, updated_at FROM roles WHERE title=?",
            (title.lower().strip(),),
        ).fetchone()
    if not row or force_refresh:
        return None
    if not _is_fresh(row["updated_at"], _ROLE_TTL_DAYS):
        return None
    return row["description"]


def save_role(title: str, description: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO roles(title, description, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(title) DO UPDATE SET description=excluded.description, updated_at=excluded.updated_at",
            (title.lower().strip(), description, _now()),
        )


# ── Careers page ──────────────────────────────────────────────────────────────

def get_careers_page(force_refresh: bool = False) -> str | None:
    """Return cached nimbleway.com/careers content, or None if stale/missing."""
    with _conn() as con:
        row = con.execute("SELECT content, updated_at FROM careers_page WHERE id=1").fetchone()
    if not row or force_refresh:
        return None
    if not _is_fresh(row["updated_at"], _CAREERS_TTL_DAYS):
        return None
    return row["content"]


def save_careers_page(content: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO careers_page(id, content, updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
            (content, _now()),
        )


# ── Cache info (for UI display) ────────────────────────────────────────────────

def get_cache_info() -> dict:
    """Returns human-readable cache status for the UI."""
    with _conn() as con:
        emp_row    = con.execute("SELECT updated_at FROM company_context WHERE id=1").fetchone()
        emp_count  = con.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        icp_row    = con.execute("SELECT updated_at FROM icp WHERE id=1").fetchone()
        role_count = con.execute("SELECT COUNT(*) FROM roles").fetchone()[0]

    has_employees = emp_row is not None and emp_count > 0
    has_icp       = icp_row is not None

    return {
        "has_employees":     has_employees,
        "employee_count":    emp_count,
        "employees_days_ago": _days_ago(emp_row["updated_at"]) if emp_row else None,
        "employees_fresh":   _is_fresh(emp_row["updated_at"], _EMPLOYEES_TTL_DAYS) if emp_row else False,
        "has_icp":           has_icp,
        "icp_days_ago":      _days_ago(icp_row["updated_at"]) if icp_row else None,
        "icp_fresh":         _is_fresh(icp_row["updated_at"], _EMPLOYEES_TTL_DAYS) if icp_row else False,
        "role_count":        role_count,
    }

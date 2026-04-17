from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from orchestrator.config import settings
from shared.types import EmailPayload, SessionContext

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'received',
    raw_email JSONB NOT NULL,
    context JSONB NOT NULL DEFAULT '{}',
    bpo_key TEXT,
    target_company TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by TEXT,
    rejected_by TEXT,
    reject_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
"""


def _conn():
    return psycopg2.connect(settings.DATABASE_URL)


def ensure_schema() -> None:
    if not settings.DATABASE_URL:
        logger.warning("No DATABASE_URL — session persistence disabled")
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


def create_session(email: EmailPayload, dry_run: bool = False) -> SessionContext:
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    ctx = SessionContext(
        session_id=sid,
        created_at=datetime.now(timezone.utc),
        raw_email=email,
        dry_run=dry_run,
    )
    if settings.DATABASE_URL:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (session_id, status, raw_email, context) VALUES (%s, %s, %s, %s)",
                    (sid, "received", json.dumps(email.model_dump()), "{}"),
                )
            conn.commit()
    return ctx


def save_session(ctx: SessionContext) -> None:
    if not settings.DATABASE_URL:
        return
    serializable = ctx.model_dump(mode="json", exclude={"all_artifacts"})
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sessions
                   SET status = %s, context = %s, bpo_key = %s, target_company = %s, updated_at = NOW()
                   WHERE session_id = %s""",
                (
                    ctx.status,
                    json.dumps(serializable),
                    ctx.bpo.key if ctx.bpo else None,
                    ctx.target_company,
                    ctx.session_id,
                ),
            )
        conn.commit()


def load_session(session_id: str) -> SessionContext | None:
    if not settings.DATABASE_URL:
        return None
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT context FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
    if not row:
        return None
    data = row["context"] if isinstance(row["context"], dict) else json.loads(row["context"])
    return SessionContext(**data)


def update_status(session_id: str, status: str, **extra) -> None:
    if not settings.DATABASE_URL:
        return
    sets = ["status = %s", "updated_at = NOW()"]
    vals: list = [status]
    for k, v in extra.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(session_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = %s", vals)
        conn.commit()


def list_sessions(limit: int = 50, status: str | None = None) -> list[dict]:
    if not settings.DATABASE_URL:
        return []
    q = "SELECT session_id, status, bpo_key, target_company, created_at, updated_at FROM sessions"
    vals: list = []
    if status:
        q += " WHERE status = %s"
        vals.append(status)
    q += " ORDER BY created_at DESC LIMIT %s"
    vals.append(limit)
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(q, vals)
            return [dict(r) for r in cur.fetchall()]


def get_session_detail(session_id: str) -> dict | None:
    if not settings.DATABASE_URL:
        return None
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
    return dict(row) if row else None

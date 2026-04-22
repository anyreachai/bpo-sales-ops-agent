"""Snapshot-based deliverable tracking.

Periodically reads BPO pipeline Google Sheets and compares to stored state
in Postgres. Logs deliverable completions and stage changes.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from orchestrator.config import settings

logger = logging.getLogger(__name__)

DELIVERABLE_COLUMNS = {
    "demo_link": "demo",
    "consumer_intelligence_report": "cx_intel",
    "company_deep_dive": "company_deep_dive",
    "stakeholder_intel": "stakeholder_intel",
    "presentation": "presentation",
}

ALL_SHEET_COLUMNS = [
    "date", "stage", "type", "source", "company", "account_executive",
    "latest_news", "website_url", "demo_number", "demo_link",
    "other_demo_resources", "consumer_intelligence_report", "google_drive",
    "company_deep_dive", "stakeholder_intel", "presentation",
]

_pool: pool.ThreadedConnectionPool | None = None


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL not configured")
        _pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=5,
            dsn=settings.DATABASE_URL,
            connect_timeout=10,
        )
    return _pool


def _get_conn():
    return _get_pool().getconn()


def _put_conn(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

TRACKER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_state (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT '',
    demo_link TEXT DEFAULT '',
    cx_intel_link TEXT DEFAULT '',
    deep_dive_link TEXT DEFAULT '',
    stakeholder_link TEXT DEFAULT '',
    presentation_link TEXT DEFAULT '',
    date TEXT DEFAULT '',
    type TEXT DEFAULT '',
    source TEXT DEFAULT '',
    account_executive TEXT DEFAULT '',
    latest_news TEXT DEFAULT '',
    website_url TEXT DEFAULT '',
    demo_number TEXT DEFAULT '',
    other_demo_resources TEXT DEFAULT '',
    google_drive TEXT DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(bpo_key, company)
);

CREATE TABLE IF NOT EXISTS deliverable_events (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    deliverable_type TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    link_value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stage_changes (
    id SERIAL PRIMARY KEY,
    bpo_key TEXT NOT NULL,
    company TEXT NOT NULL,
    old_stage TEXT DEFAULT '',
    new_stage TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ps_bpo_key ON pipeline_state(bpo_key);
CREATE INDEX IF NOT EXISTS idx_ps_last_checked ON pipeline_state(last_checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_ps_bpo_first_seen ON pipeline_state(bpo_key, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_de_completed ON deliverable_events(completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sc_changed ON stage_changes(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sc_bpo_company ON stage_changes(bpo_key, company, changed_at DESC);
"""


def ensure_tracker_schema() -> None:
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL not set — deliverable tracker disabled")
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(TRACKER_SCHEMA_SQL)
        conn.commit()
        logger.info("Deliverable tracker schema verified")
    finally:
        _put_conn(conn)


# ---------------------------------------------------------------------------
# Snapshot sync
# ---------------------------------------------------------------------------

_dashboard_cache: dict | None = None
_dashboard_cache_at: float = 0.0
_DASHBOARD_CACHE_TTL = 60


def invalidate_dashboard_cache():
    global _dashboard_cache, _dashboard_cache_at
    _dashboard_cache = None
    _dashboard_cache_at = 0.0


def sync_pipeline_snapshot(bpo_key: str, rows: list[dict]) -> dict:
    if not settings.DATABASE_URL:
        return {"skipped": True, "reason": "no DATABASE_URL"}

    conn = _get_conn()
    try:
        result = _sync_snapshot_inner(conn, bpo_key, rows)
        invalidate_dashboard_cache()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)


def _sync_snapshot_inner(conn, bpo_key: str, rows: list[dict]) -> dict:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    now = datetime.now(timezone.utc)

    new_requests = 0
    deliverables_completed = 0
    stage_changes_count = 0

    merged: dict[str, dict] = {}
    for row in rows:
        company = row.get("company", "").strip()
        if not company:
            continue
        if company not in merged:
            merged[company] = dict(row)
        else:
            for key, val in row.items():
                if val and isinstance(val, str) and val.strip():
                    existing = merged[company].get(key, "")
                    if not existing or not existing.strip():
                        merged[company][key] = val

    for company, row in merged.items():
        cur.execute(
            "SELECT * FROM pipeline_state WHERE bpo_key = %s AND company = %s",
            (bpo_key, company),
        )
        stored = cur.fetchone()

        if not stored:
            cur.execute(
                """INSERT INTO pipeline_state
                   (bpo_key, company, stage, demo_link, cx_intel_link,
                    deep_dive_link, stakeholder_link, presentation_link,
                    date, type, source, account_executive, latest_news,
                    website_url, demo_number, other_demo_resources, google_drive,
                    first_seen_at, last_checked_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    bpo_key, company, row.get("stage", ""),
                    row.get("demo_link", ""), row.get("consumer_intelligence_report", ""),
                    row.get("company_deep_dive", ""), row.get("stakeholder_intel", ""),
                    row.get("presentation", ""),
                    row.get("date", ""), row.get("type", ""), row.get("source", ""),
                    row.get("account_executive", ""), row.get("latest_news", ""),
                    row.get("website_url", ""), row.get("demo_number", ""),
                    row.get("other_demo_resources", ""), row.get("google_drive", ""),
                    now, now,
                ),
            )
            cur.execute(
                """INSERT INTO deliverable_events
                   (bpo_key, company, deliverable_type, completed_at, link_value)
                   VALUES (%s, %s, 'initial_request', %s, '')""",
                (bpo_key, company, now),
            )
            for col, dtype in DELIVERABLE_COLUMNS.items():
                val = row.get(col, "").strip()
                if val:
                    cur.execute(
                        """INSERT INTO deliverable_events
                           (bpo_key, company, deliverable_type, completed_at, link_value)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (bpo_key, company, dtype, now, val),
                    )
                    deliverables_completed += 1
            stage = row.get("stage", "").strip()
            if stage:
                cur.execute(
                    """INSERT INTO stage_changes
                       (bpo_key, company, old_stage, new_stage, changed_at)
                       VALUES (%s, %s, '', %s, %s)""",
                    (bpo_key, company, stage, now),
                )
            new_requests += 1
            continue

        state_col_map = {
            "demo_link": "demo_link",
            "consumer_intelligence_report": "cx_intel_link",
            "company_deep_dive": "deep_dive_link",
            "stakeholder_intel": "stakeholder_link",
            "presentation": "presentation_link",
        }
        for sheet_col, db_col in state_col_map.items():
            old_val = (stored[db_col] or "").strip()
            new_val = row.get(sheet_col, "").strip()
            if not old_val and new_val:
                dtype = DELIVERABLE_COLUMNS[sheet_col]
                cur.execute(
                    """SELECT 1 FROM deliverable_events
                       WHERE bpo_key = %s AND company = %s AND deliverable_type = %s LIMIT 1""",
                    (bpo_key, company, dtype),
                )
                if not cur.fetchone():
                    cur.execute(
                        """INSERT INTO deliverable_events
                           (bpo_key, company, deliverable_type, completed_at, link_value)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (bpo_key, company, dtype, now, new_val),
                    )
                    deliverables_completed += 1

        old_stage = (stored["stage"] or "").strip()
        new_stage = row.get("stage", "").strip()
        if old_stage != new_stage and new_stage:
            cur.execute(
                """SELECT new_stage FROM stage_changes
                   WHERE bpo_key = %s AND company = %s
                   ORDER BY changed_at DESC LIMIT 1""",
                (bpo_key, company),
            )
            last_change = cur.fetchone()
            if not last_change or last_change["new_stage"] != new_stage:
                cur.execute(
                    """INSERT INTO stage_changes
                       (bpo_key, company, old_stage, new_stage, changed_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (bpo_key, company, old_stage, new_stage, now),
                )
                stage_changes_count += 1

        cur.execute(
            """UPDATE pipeline_state
               SET stage=%s, demo_link=%s, cx_intel_link=%s,
                   deep_dive_link=%s, stakeholder_link=%s, presentation_link=%s,
                   date=%s, type=%s, source=%s, account_executive=%s,
                   latest_news=%s, website_url=%s, demo_number=%s,
                   other_demo_resources=%s, google_drive=%s, last_checked_at=%s
               WHERE bpo_key=%s AND company=%s""",
            (
                row.get("stage", ""), row.get("demo_link", ""),
                row.get("consumer_intelligence_report", ""),
                row.get("company_deep_dive", ""), row.get("stakeholder_intel", ""),
                row.get("presentation", ""),
                row.get("date", ""), row.get("type", ""), row.get("source", ""),
                row.get("account_executive", ""), row.get("latest_news", ""),
                row.get("website_url", ""), row.get("demo_number", ""),
                row.get("other_demo_resources", ""), row.get("google_drive", ""),
                now, bpo_key, company,
            ),
        )

    conn.commit()
    cur.close()

    summary = {
        "bpo_key": bpo_key,
        "rows_checked": len(rows),
        "new_requests": new_requests,
        "deliverables_completed": deliverables_completed,
        "stage_changes": stage_changes_count,
    }
    if new_requests or deliverables_completed or stage_changes_count:
        logger.info("Pipeline changes for %s: %s", bpo_key, summary)
    return summary


# ---------------------------------------------------------------------------
# Read functions (serve dashboard API from Postgres)
# ---------------------------------------------------------------------------

def get_pipeline_summary(bpo_registry: dict) -> tuple[list[dict], str | None]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT bpo_key, COUNT(*) AS row_count FROM pipeline_state GROUP BY bpo_key")
    counts = {r["bpo_key"]: r["row_count"] for r in cur.fetchall()}

    cur.execute("SELECT MAX(last_checked_at) AS ts FROM pipeline_state")
    ts_row = cur.fetchone()
    last_snapshot = ts_row["ts"].isoformat() if ts_row and ts_row["ts"] else None

    cur.close()
    _put_conn(conn)

    summaries = []
    for bpo_key, entry in bpo_registry.items():
        sheet_id = entry.get("pipeline_sheet_id")
        item = {
            "bpo_key": bpo_key,
            "name": entry.get("name", bpo_key),
            "row_count": counts.get(bpo_key, 0),
            "has_sheet": bool(sheet_id),
        }
        if sheet_id:
            item["sheet_url"] = f"https://docs.google.com/spreadsheets/d/u/1/{sheet_id}"
        summaries.append(item)

    return summaries, last_snapshot


def get_pipeline_rows(bpo_key: str) -> tuple[list[dict], str | None]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """SELECT bpo_key, company, stage, demo_link, cx_intel_link,
                  deep_dive_link, stakeholder_link, presentation_link,
                  date, type, source, account_executive, latest_news,
                  website_url, demo_number, other_demo_resources, google_drive,
                  first_seen_at, last_checked_at
           FROM pipeline_state WHERE bpo_key = %s
           ORDER BY first_seen_at DESC""",
        (bpo_key,),
    )
    db_rows = cur.fetchall()

    cur.execute(
        "SELECT MAX(last_checked_at) AS ts FROM pipeline_state WHERE bpo_key = %s",
        (bpo_key,),
    )
    ts_row = cur.fetchone()
    last_snapshot = ts_row["ts"].isoformat() if ts_row and ts_row["ts"] else None

    cur.close()
    _put_conn(conn)

    rows = []
    for r in db_rows:
        row = {
            "date": r.get("date", ""),
            "stage": r.get("stage", ""),
            "type": r.get("type", ""),
            "source": r.get("source", ""),
            "company": r.get("company", ""),
            "account_executive": r.get("account_executive", ""),
            "latest_news": r.get("latest_news", ""),
            "website_url": r.get("website_url", ""),
            "demo_number": r.get("demo_number", ""),
            "demo_link": r.get("demo_link", ""),
            "other_demo_resources": r.get("other_demo_resources", ""),
            "consumer_intelligence_report": r.get("cx_intel_link", ""),
            "google_drive": r.get("google_drive", ""),
            "company_deep_dive": r.get("deep_dive_link", ""),
            "stakeholder_intel": r.get("stakeholder_link", ""),
            "presentation": r.get("presentation_link", ""),
        }
        row["deliverable_status"] = {
            "initial_request": True,
            "demo": bool((row.get("demo_link") or "").strip()),
            "cx_intel": bool((row.get("consumer_intelligence_report") or "").strip()),
            "company_deep_dive": bool((row.get("company_deep_dive") or "").strip()),
            "stakeholder_intel": bool((row.get("stakeholder_intel") or "").strip()),
            "presentation": bool((row.get("presentation") or "").strip()),
        }
        rows.append(row)

    return rows, last_snapshot


def get_deliverable_timeline(
    bpo_key: str | None = None,
    company: str | None = None,
    limit: int = 50,
) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = "SELECT * FROM deliverable_events WHERE 1=1"
    params: list = []
    if bpo_key:
        query += " AND bpo_key = %s"
        params.append(bpo_key)
    if company:
        query += " AND company = %s"
        params.append(company)
    query += " ORDER BY completed_at DESC LIMIT %s"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    _put_conn(conn)
    return [dict(r) for r in rows]


def get_stage_history(
    bpo_key: str | None = None,
    company: str | None = None,
    limit: int = 50,
) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = "SELECT * FROM stage_changes WHERE 1=1"
    params: list = []
    if bpo_key:
        query += " AND bpo_key = %s"
        params.append(bpo_key)
    if company:
        query += " AND company = %s"
        params.append(company)
    query += " ORDER BY changed_at DESC LIMIT %s"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    _put_conn(conn)
    return [dict(r) for r in rows]


def get_stale_pipeline(days_threshold: int = 7) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """WITH latest_stage AS (
               SELECT DISTINCT ON (bpo_key, company) bpo_key, company, changed_at
               FROM stage_changes
               ORDER BY bpo_key, company, changed_at DESC
           )
           SELECT ps.bpo_key, ps.company, ps.stage, ps.first_seen_at, ps.last_checked_at,
                  COALESCE(ls.changed_at, ps.first_seen_at) AS stage_since,
                  EXTRACT(EPOCH FROM (NOW() - COALESCE(ls.changed_at, ps.first_seen_at))) / 86400 AS days_in_stage
           FROM pipeline_state ps
           LEFT JOIN latest_stage ls USING (bpo_key, company)
           WHERE EXTRACT(EPOCH FROM (NOW() - COALESCE(ls.changed_at, ps.first_seen_at))) / 86400 > %s
           ORDER BY days_in_stage DESC""",
        (days_threshold,),
    )
    rows = cur.fetchall()
    cur.close()
    _put_conn(conn)
    return [dict(r) for r in rows]


def get_dashboard_data(bpo_registry: dict) -> dict:
    global _dashboard_cache, _dashboard_cache_at
    now = time.time()
    if _dashboard_cache and (now - _dashboard_cache_at) < _DASHBOARD_CACHE_TTL:
        return _dashboard_cache
    result = _get_dashboard_data_impl(bpo_registry)
    _dashboard_cache = result
    _dashboard_cache_at = now
    return result


def _get_dashboard_data_impl(bpo_registry: dict) -> dict:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT bpo_key, COUNT(*) AS row_count FROM pipeline_state GROUP BY bpo_key")
    counts = {r["bpo_key"]: r["row_count"] for r in cur.fetchall()}

    cur.execute("SELECT MAX(last_checked_at) AS ts FROM pipeline_state")
    ts_row = cur.fetchone()
    last_snapshot = ts_row["ts"].isoformat() if ts_row and ts_row["ts"] else None

    cur.execute(
        """SELECT bpo_key, company, stage, demo_link, cx_intel_link,
                  deep_dive_link, stakeholder_link, presentation_link,
                  date, type, source, account_executive, latest_news,
                  website_url, demo_number, other_demo_resources, google_drive,
                  first_seen_at, last_checked_at
           FROM pipeline_state ORDER BY bpo_key, first_seen_at DESC"""
    )
    all_rows_raw = cur.fetchall()

    cur.execute("SELECT * FROM deliverable_events ORDER BY completed_at DESC LIMIT 50")
    timeline_raw = cur.fetchall()

    cur.execute("SELECT * FROM stage_changes ORDER BY changed_at DESC LIMIT 50")
    stage_history_raw = cur.fetchall()

    cur.execute(
        """WITH latest_stage AS (
               SELECT DISTINCT ON (bpo_key, company) bpo_key, company, changed_at
               FROM stage_changes
               ORDER BY bpo_key, company, changed_at DESC
           )
           SELECT ps.bpo_key, ps.company, ps.stage, ps.first_seen_at, ps.last_checked_at,
                  COALESCE(ls.changed_at, ps.first_seen_at) AS stage_since,
                  EXTRACT(EPOCH FROM (NOW() - COALESCE(ls.changed_at, ps.first_seen_at))) / 86400 AS days_in_stage
           FROM pipeline_state ps
           LEFT JOIN latest_stage ls USING (bpo_key, company)
           WHERE EXTRACT(EPOCH FROM (NOW() - COALESCE(ls.changed_at, ps.first_seen_at))) / 86400 > 7
           ORDER BY days_in_stage DESC"""
    )
    stale_raw = cur.fetchall()

    cur.close()
    _put_conn(conn)

    partners = []
    for bpo_key, entry in bpo_registry.items():
        sheet_id = entry.get("pipeline_sheet_id")
        item = {
            "bpo_key": bpo_key,
            "name": entry.get("name", bpo_key),
            "row_count": counts.get(bpo_key, 0),
            "has_sheet": bool(sheet_id),
        }
        if sheet_id:
            item["sheet_url"] = f"https://docs.google.com/spreadsheets/d/u/1/{sheet_id}"
        partners.append(item)

    pipeline_by_partner: dict[str, list] = {}
    for r in all_rows_raw:
        bpo_key = r["bpo_key"]
        if bpo_key not in pipeline_by_partner:
            pipeline_by_partner[bpo_key] = []
        row = {
            "date": r.get("date", ""), "stage": r.get("stage", ""),
            "type": r.get("type", ""), "source": r.get("source", ""),
            "company": r.get("company", ""),
            "account_executive": r.get("account_executive", ""),
            "latest_news": r.get("latest_news", ""),
            "website_url": r.get("website_url", ""),
            "demo_number": r.get("demo_number", ""),
            "demo_link": r.get("demo_link", ""),
            "other_demo_resources": r.get("other_demo_resources", ""),
            "consumer_intelligence_report": r.get("cx_intel_link", ""),
            "google_drive": r.get("google_drive", ""),
            "company_deep_dive": r.get("deep_dive_link", ""),
            "stakeholder_intel": r.get("stakeholder_link", ""),
            "presentation": r.get("presentation_link", ""),
        }
        row["deliverable_status"] = {
            "initial_request": True,
            "demo": bool((row.get("demo_link") or "").strip()),
            "cx_intel": bool((row.get("consumer_intelligence_report") or "").strip()),
            "company_deep_dive": bool((row.get("company_deep_dive") or "").strip()),
            "stakeholder_intel": bool((row.get("stakeholder_intel") or "").strip()),
            "presentation": bool((row.get("presentation") or "").strip()),
        }
        pipeline_by_partner[bpo_key].append(row)

    def _serialize(rows):
        out = []
        for r in rows:
            d = dict(r)
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif isinstance(v, float):
                    d[k] = round(v, 1)
            out.append(d)
        return out

    return {
        "partners": partners,
        "pipeline": pipeline_by_partner,
        "timeline": _serialize(timeline_raw),
        "stage_history": _serialize(stage_history_raw),
        "stale": _serialize(stale_raw),
        "last_snapshot_at": last_snapshot,
    }


def cleanup_duplicate_events() -> dict:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM deliverable_events
        WHERE id NOT IN (
            SELECT MIN(id) FROM deliverable_events
            GROUP BY bpo_key, company, deliverable_type
        )
    """)
    events_deleted = cur.rowcount

    cur.execute("""
        DELETE FROM stage_changes
        WHERE id NOT IN (
            SELECT MIN(id) FROM stage_changes
            GROUP BY bpo_key, company, old_stage, new_stage
        )
    """)
    stages_deleted = cur.rowcount

    conn.commit()
    cur.close()
    _put_conn(conn)
    return {"events_deleted": events_deleted, "stages_deleted": stages_deleted}

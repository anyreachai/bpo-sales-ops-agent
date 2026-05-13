"""BPO Sales Ops Pipeline — FastAPI Application."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from orchestrator.config import settings
from orchestrator.dag import DAGRunner, NODES, Phase
from orchestrator.session import (
    create_session,
    ensure_schema,
    get_session_detail,
    list_sessions,
    load_session,
    save_session,
    update_status,
)
from shared.types import EmailPayload, SessionContext

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

_poller_task: asyncio.Task | None = None
_tracker_task: asyncio.Task | None = None
_watch_task: asyncio.Task | None = None

BPO_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "bpo_registry.json"

# Drive push notification state — maps channel_id → {sheet_id, bpo_key, resource_id, expiration}
_active_watches: dict[str, dict] = {}


def _load_bpo_registry() -> dict:
    if BPO_REGISTRY_PATH.exists():
        return json.loads(BPO_REGISTRY_PATH.read_text(encoding="utf-8"))
    return {}


async def _pipeline_snapshot_loop() -> None:
    """Periodically sync BPO pipeline Google Sheets to Postgres + Attio.

    Calls the same chokepoint as the Drive push-notification webhook so
    behavior stays in sync between real-time and fallback-poll paths.
    """
    from orchestrator.deliverable_tracker import ensure_tracker_schema

    logger.info("Pipeline snapshot loop started (interval=300s)")
    ensure_tracker_schema()

    while True:
        await asyncio.sleep(300)
        try:
            summary = await _run_snapshot_sync()
            logger.info(
                "Pipeline snapshot cycle complete: %d partners synced, "
                "%d Attio links added",
                summary.get("partners_synced", 0),
                summary.get("attio_added", 0),
            )
        except Exception:
            logger.exception("Pipeline snapshot error")


async def _register_sheet_watches() -> None:
    """Register Drive API push notifications for all BPO tracker sheets.

    Google Drive push notifications POST to our webhook when a file changes.
    Watches expire (max 7 days), so we renew them every 6 hours.
    """
    import uuid
    from shared.google_auth import get_access_token

    public_url = settings.PUBLIC_URL.rstrip("/") if settings.PUBLIC_URL else ""
    if not public_url:
        logger.info("Sheet watches skipped — PUBLIC_URL not configured")
        return

    webhook_url = f"{public_url}/api/webhook/sheet-update?secret={settings.SHEET_WEBHOOK_SECRET}"
    registry = _load_bpo_registry()
    token = get_access_token(
        settings.GOOGLE_OAUTH_CLIENT_ID,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
    )

    for bpo_key, entry in registry.items():
        sheet_id = entry.get("pipeline_sheet_id")
        if not sheet_id:
            continue

        channel_id = f"bpo-watch-{bpo_key}-{uuid.uuid4().hex[:8]}"
        expiration = int((time.time() + 86400) * 1000)  # 24 hours in ms

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://www.googleapis.com/drive/v3/files/{sheet_id}/watch",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "id": channel_id,
                        "type": "web_hook",
                        "address": webhook_url,
                        "expiration": expiration,
                    },
                )

            if resp.status_code == 200:
                data = resp.json()
                _active_watches[channel_id] = {
                    "sheet_id": sheet_id,
                    "bpo_key": bpo_key,
                    "resource_id": data.get("resourceId", ""),
                    "expiration": data.get("expiration", expiration),
                }
                logger.info("Registered Drive watch for %s (sheet %s)", bpo_key, sheet_id[:12])
            else:
                logger.warning(
                    "Failed to register watch for %s: %d %s",
                    bpo_key, resp.status_code, resp.text[:200],
                )
        except Exception:
            logger.exception("Error registering watch for %s", bpo_key)


async def _sheet_watch_renewal_loop() -> None:
    """Renew Drive watches every 6 hours to prevent expiration."""
    while True:
        await asyncio.sleep(21600)  # 6 hours
        try:
            logger.info("Renewing sheet watches (%d active)", len(_active_watches))
            # Stop old watches, then re-register
            await _stop_all_watches()
            await _register_sheet_watches()
        except Exception:
            logger.exception("Sheet watch renewal error")


async def _stop_all_watches() -> None:
    """Stop all active Drive push notification channels."""
    from shared.google_auth import get_access_token

    if not _active_watches:
        return

    token = get_access_token(
        settings.GOOGLE_OAUTH_CLIENT_ID,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
    )

    for channel_id, info in list(_active_watches.items()):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://www.googleapis.com/drive/v3/channels/stop",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "id": channel_id,
                        "resourceId": info["resource_id"],
                    },
                )
        except Exception:
            pass
    _active_watches.clear()


async def _gmail_poll_loop() -> None:
    """Periodically poll Gmail and feed new BPO emails into the pipeline."""
    logger.info("Gmail poller background loop started (interval=%ds)", settings.POLL_INTERVAL_SECONDS)
    while True:
        try:
            from gmail_poller.poller import poll_once

            emails = await poll_once()
            for email in emails:
                try:
                    ctx = create_session(email)
                    logger.info("Poller created session %s for <%s> — %s", ctx.session_id, email.from_address, email.subject)
                    asyncio.create_task(run_phase_1(ctx.session_id))
                except Exception:
                    logger.exception("Failed to create session for polled email: %s", email.subject)
            if emails:
                logger.info("Gmail poll cycle: %d new emails processed", len(emails))
        except ImportError:
            logger.warning("gmail_poller.poller not available — skipping poll cycle")
        except Exception:
            logger.exception("Gmail poller error")
        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)


def _phase_1_node_names() -> set[str]:
    return {name for name, node in NODES.items() if node["phase"] == Phase.PHASE_1}


async def _post_slack_approval(ctx: SessionContext) -> None:
    """Post a Block Kit approval message to Slack."""
    if not settings.SLACK_BOT_TOKEN:
        logger.warning("No SLACK_BOT_TOKEN — skipping Slack approval post")
        return

    if ctx.dry_run:
        logger.info("Dry-run: skipping Slack approval post for %s", ctx.session_id)
        return

    company = ctx.target_company or "Unknown Company"
    bpo_name = ctx.bpo.name if ctx.bpo else "Unknown BPO"
    artifact_lines = [f"• {a.filename} ({a.artifact_type})" for a in ctx.all_artifacts]
    artifacts_text = "\n".join(artifact_lines) if artifact_lines else "_No artifacts yet_"
    deliverables_text = ", ".join(ctx.deliverables_requested) if ctx.deliverables_requested else "All standard"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"New BPO Package: {company} | {bpo_name}", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Company:*\n{company}"},
                {"type": "mrkdwn", "text": f"*BPO Partner:*\n{bpo_name}"},
                {"type": "mrkdwn", "text": f"*Deliverables:*\n{deliverables_text}"},
                {"type": "mrkdwn", "text": f"*Session:*\n`{ctx.session_id}`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Artifacts Generated:*\n{artifacts_text}"},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Deliver"},
                    "style": "primary",
                    "action_id": "bpo_approve",
                    "value": ctx.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "bpo_reject",
                    "value": ctx.session_id,
                },
            ],
        },
    ]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={
                "channel": settings.SLACK_NOTIFY_CHANNEL,
                "text": f"Approval needed: {company} | {bpo_name}",
                "blocks": blocks,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("Slack approval message posted (ts=%s)", data.get("ts"))
        else:
            logger.error("Slack postMessage failed: %s", data.get("error"))


async def run_phase_1(session_id: str) -> None:
    """Background task: run Phase 1 of the DAG, then post Slack approval."""
    logger.info("Phase 1 starting for %s", session_id)
    ctx = load_session(session_id)
    if ctx is None:
        logger.error("Session %s not found — aborting Phase 1", session_id)
        return

    try:
        update_status(session_id, "classifying")
        ctx.status = "classifying"

        runner = DAGRunner(ctx)
        await runner.run_phase(Phase.PHASE_1)

        ctx.status = "awaiting_approval"
        update_status(session_id, "awaiting_approval")
        save_session(ctx)

        await _post_slack_approval(ctx)
        logger.info("Phase 1 complete for %s — awaiting approval", session_id)

    except Exception:
        logger.exception("Phase 1 failed for %s", session_id)
        ctx.status = "error"
        update_status(session_id, "error")
        save_session(ctx)


async def run_phase_2(session_id: str) -> None:
    """Background task: run Phase 2 of the DAG after approval."""
    logger.info("Phase 2 starting for %s", session_id)
    ctx = load_session(session_id)
    if ctx is None:
        logger.error("Session %s not found — aborting Phase 2", session_id)
        return

    try:
        ctx.status = "delivering"
        update_status(session_id, "delivering")

        runner = DAGRunner(ctx)
        # Pre-populate Phase 1 nodes as completed so DAG resolves dependencies
        runner.completed = _phase_1_node_names()
        runner.results = {name: ctx.module_results[name] for name in runner.completed if name in ctx.module_results}

        await runner.run_phase(Phase.PHASE_2)

        ctx.status = "complete"
        update_status(session_id, "complete")
        save_session(ctx)
        logger.info("Phase 2 complete for %s", session_id)

    except Exception:
        logger.exception("Phase 2 failed for %s", session_id)
        ctx.status = "error"
        update_status(session_id, "error")
        save_session(ctx)


# ---------------------------------------------------------------------------
# Slack interaction helpers
# ---------------------------------------------------------------------------

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack request signature using signing secret."""
    if not settings.SLACK_SIGNING_SECRET:
        return True  # Skip verification if no secret configured
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.HMAC(
        settings.SLACK_SIGNING_SECRET.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _update_slack_message(channel: str, ts: str, text: str) -> None:
    """Update the original Slack message after approval/rejection."""
    if not settings.SLACK_BOT_TOKEN:
        return
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.update",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={
                "channel": channel,
                "ts": ts,
                "text": text,
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": text},
                    }
                ],
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("Slack chat.update failed: %s", data.get("error"))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    from_address: str
    subject: str
    body: str
    message_id: str | None = None
    cc: list[str] = []
    dry_run: bool | None = None


class ApproveRequest(BaseModel):
    approved_by: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None
    rejected_by: str | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("BPO Sales Ops Pipeline starting up")

    # Ensure DB schema
    try:
        ensure_schema()
        logger.info("Database schema verified")
    except Exception:
        logger.exception("Failed to ensure DB schema — continuing without persistence")

    # Ensure deliverable tracker schema
    try:
        from orchestrator.deliverable_tracker import ensure_tracker_schema
        ensure_tracker_schema()
    except Exception:
        logger.exception("Failed to ensure tracker schema — continuing without tracking")

    # Register all pipeline modules
    from modules import register_all

    register_all()
    logger.info("All pipeline modules registered")

    # Start Gmail poller if credentials are configured
    global _poller_task, _tracker_task, _watch_task
    if settings.GOOGLE_OAUTH_REFRESH_TOKEN and not settings.DRY_RUN:
        _poller_task = asyncio.create_task(_gmail_poll_loop())
        logger.info("Gmail poller task created")
    elif settings.DRY_RUN:
        logger.info("Gmail poller skipped — DRY_RUN mode")
    else:
        logger.info("Gmail poller skipped — no GOOGLE_OAUTH_REFRESH_TOKEN")

    # Start pipeline snapshot tracker (fallback polling)
    if settings.DATABASE_URL and settings.GOOGLE_OAUTH_REFRESH_TOKEN and not settings.DRY_RUN:
        _tracker_task = asyncio.create_task(_pipeline_snapshot_loop())
        logger.info("Pipeline snapshot tracker task created")
    else:
        logger.info("Pipeline snapshot tracker skipped (no DB or no Google creds or DRY_RUN)")

    # Register Drive push notifications for real-time sheet sync
    if (settings.DATABASE_URL and settings.GOOGLE_OAUTH_REFRESH_TOKEN
            and settings.PUBLIC_URL and not settings.DRY_RUN):
        try:
            await _register_sheet_watches()
            _watch_task = asyncio.create_task(_sheet_watch_renewal_loop())
            logger.info("Sheet watch renewal loop started")
        except Exception:
            logger.exception("Failed to register sheet watches — falling back to polling")
    else:
        logger.info("Sheet watches skipped (no PUBLIC_URL or no DB or DRY_RUN)")

    yield

    # Shutdown
    try:
        await _stop_all_watches()
    except Exception:
        pass
    for task in (_poller_task, _tracker_task, _watch_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("BPO Sales Ops Pipeline shut down")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BPO Sales Ops Pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # CORS preflight: let CORSMiddleware answer. Browsers send OPTIONS
    # without an Authorization header by spec, so gating preflight on auth
    # returns 401 with no CORS headers and the browser blocks the real GET.
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # Skip auth for non-API routes, health, Slack webhook, and sheet webhook.
    # `/health` is the legacy poller shape and stays no-auth like its sibling.
    if (
        path.startswith("/slack/")
        or path in ("/health", "/api/health", "/api/webhook/sheet-update")
        or not path.startswith("/api/")
    ):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return Response(
            content=json.dumps({"detail": "Missing Bearer token"}),
            status_code=401,
            media_type="application/json",
        )

    token = auth_header[len("Bearer "):]
    if token != settings.API_AUTH_TOKEN:
        return Response(
            content=json.dumps({"detail": "Invalid token"}),
            status_code=403,
            media_type="application/json",
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Legacy compatibility shims
#
# The Lovable dashboard at preview--faithful-snapshot-mirror.lovable.app was
# built against the older `bpo-sales-ops-poller` service. These adapters keep
# it working unchanged against this service so the legacy Cloud Run service
# can be retired.
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_legacy():
    """Legacy poller `/health` shape. Counts come from the sessions table."""
    total = list_sessions(limit=10000)
    pending = [r for r in total if r.get("status") == "awaiting_approval"]
    return {
        "status": "ok",
        "tracked_messages": len(total),
        "pending_approvals": len(pending),
        "total_sessions": len(total),
    }


@app.get("/api/dashboard")
async def dashboard_legacy():
    """Alias for /api/pipeline/tracker — same combined-data payload."""
    return await pipeline_tracker_summary()


@app.get("/api/approvals")
async def approvals_legacy(limit: int = 50):
    """Sessions awaiting approval, shaped as legacy {approvals, count}."""
    rows = list_sessions(limit=limit, status="awaiting_approval")
    return {"approvals": rows, "count": len(rows)}


@app.post("/api/approvals/{session_id}/approve")
async def approve_legacy(session_id: str, req: ApproveRequest | None = None):
    return await approve_session(session_id, req)


@app.post("/api/approvals/{session_id}/reject")
async def reject_legacy(session_id: str, req: RejectRequest | None = None):
    return await reject_session(session_id, req)


@app.post("/api/process")
async def process_email(req: ProcessRequest):
    """Accept an email payload, create a session, and kick off Phase 1."""
    email = EmailPayload(
        from_address=req.from_address,
        subject=req.subject,
        body=req.body,
        message_id=req.message_id,
        cc=req.cc,
    )
    dry_run = req.dry_run if req.dry_run is not None else settings.DRY_RUN
    ctx = create_session(email, dry_run=dry_run)
    logger.info("Session %s created for <%s> — %s", ctx.session_id, email.from_address, email.subject)

    asyncio.create_task(run_phase_1(ctx.session_id))
    return {"session_id": ctx.session_id, "status": "received"}


@app.get("/api/sessions")
async def get_sessions(limit: int = 50, status: str | None = None):
    """List sessions, optionally filtered by status."""
    rows = list_sessions(limit=limit, status=status)
    return {"sessions": rows, "count": len(rows)}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Full session detail."""
    detail = get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@app.post("/api/sessions/{session_id}/approve")
async def approve_session(session_id: str, req: ApproveRequest | None = None):
    """Approve a session and kick off Phase 2."""
    ctx = load_session(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if ctx.status not in ("awaiting_approval", "error"):
        raise HTTPException(
            status_code=409,
            detail=f"Session is '{ctx.status}' — can only approve from 'awaiting_approval' or 'error'",
        )

    approved_by = req.approved_by if req else None
    update_status(session_id, "approved", approved_by=approved_by or "api")
    logger.info("Session %s approved by %s", session_id, approved_by or "api")

    asyncio.create_task(run_phase_2(session_id))
    return {"session_id": session_id, "status": "approved"}


@app.post("/api/sessions/{session_id}/reject")
async def reject_session(session_id: str, req: RejectRequest | None = None):
    """Reject a session."""
    ctx = load_session(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Session not found")

    reason = req.reason if req else None
    rejected_by = req.rejected_by if req else None
    update_status(
        session_id,
        "rejected",
        rejected_by=rejected_by or "api",
        reject_reason=reason or "",
    )
    logger.info("Session %s rejected by %s (reason: %s)", session_id, rejected_by or "api", reason or "none")
    return {"session_id": session_id, "status": "rejected"}


@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    """Handle Slack interactive button payloads (approve/reject)."""
    raw_body = await request.body()

    # Verify Slack signature if signing secret is configured
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if settings.SLACK_SIGNING_SECRET:
        # Reject requests older than 5 minutes
        if abs(time.time() - int(timestamp or "0")) > 300:
            raise HTTPException(status_code=403, detail="Request too old")
        if not _verify_slack_signature(raw_body, timestamp, signature):
            raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse the URL-encoded payload
    from urllib.parse import parse_qs

    parsed = parse_qs(raw_body.decode("utf-8"))
    payload_str = parsed.get("payload", [None])[0]
    if not payload_str:
        raise HTTPException(status_code=400, detail="Missing payload")

    payload: dict[str, Any] = json.loads(payload_str)
    actions = payload.get("actions", [])
    if not actions:
        return Response(status_code=200)

    action = actions[0]
    action_id = action.get("action_id", "")
    session_id = action.get("value", "")
    user_name = payload.get("user", {}).get("username", "unknown")
    channel = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")

    logger.info("Slack interaction: action=%s session=%s user=%s", action_id, session_id, user_name)

    if action_id == "bpo_approve":
        ctx = load_session(session_id)
        if ctx is None:
            logger.error("Slack approve: session %s not found", session_id)
            return Response(status_code=200)

        update_status(session_id, "approved", approved_by=user_name)

        # Update the Slack message to show approval
        company = ctx.target_company or "Unknown"
        asyncio.create_task(
            _update_slack_message(
                channel,
                message_ts,
                f"*Approved* by @{user_name} — delivering package for *{company}* (`{session_id}`)",
            )
        )

        # Kick off Phase 2
        asyncio.create_task(run_phase_2(session_id))

    elif action_id == "bpo_reject":
        ctx = load_session(session_id)
        company = ctx.target_company if ctx else "Unknown"

        update_status(session_id, "rejected", rejected_by=user_name)

        asyncio.create_task(
            _update_slack_message(
                channel,
                message_ts,
                f"*Rejected* by @{user_name} — package for *{company}* (`{session_id}`) will not be delivered",
            )
        )

    # Return 200 immediately to Slack
    return Response(status_code=200)


@app.get("/api/pipeline")
async def pipeline_summary():
    """Aggregate pipeline summary — count sessions by status."""
    all_rows = list_sessions(limit=10000)
    status_counts: dict[str, int] = {}
    for row in all_rows:
        s = row.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "total": len(all_rows),
        "by_status": status_counts,
    }


@app.get("/api/config")
async def config_view():
    """Redacted config — shows which keys are set, not their values."""
    fields = [
        "ANTHROPIC_API_KEY",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
        "SLACK_NOTIFY_CHANNEL",
        "DATABASE_URL",
        "BRAND_DEV_API_KEY",
        "OPENAI_API_KEY",
        "API_AUTH_TOKEN",
        "BPO_DOMAINS",
    ]
    redacted: dict[str, Any] = {}
    for f in fields:
        val = getattr(settings, f, "")
        if f in ("SLACK_NOTIFY_CHANNEL", "BPO_DOMAINS"):
            redacted[f] = val  # Non-secret, show full value
        else:
            redacted[f] = bool(val) if isinstance(val, str) else val

    redacted["CORS_ORIGINS"] = settings.CORS_ORIGINS
    redacted["POLL_INTERVAL_SECONDS"] = settings.POLL_INTERVAL_SECONDS
    redacted["DRY_RUN"] = settings.DRY_RUN

    return {"config": redacted}


# ---------------------------------------------------------------------------
# Pipeline analytics endpoints (from deliverable tracker)
# ---------------------------------------------------------------------------

@app.get("/api/pipeline/tracker")
async def pipeline_tracker_summary():
    """Full dashboard data from deliverable tracker — partners, pipeline rows, timeline, stale."""
    if not settings.DATABASE_URL:
        return {"error": "No DATABASE_URL configured"}
    from orchestrator.deliverable_tracker import get_dashboard_data
    registry = _load_bpo_registry()
    return get_dashboard_data(registry)


@app.get("/api/pipeline/{bpo_key}")
async def pipeline_partner_detail(bpo_key: str):
    """All pipeline rows for a specific BPO partner."""
    if not settings.DATABASE_URL:
        return {"error": "No DATABASE_URL configured"}
    from orchestrator.deliverable_tracker import get_pipeline_rows
    rows, last_snapshot = get_pipeline_rows(bpo_key)
    return {"bpo_key": bpo_key, "rows": rows, "count": len(rows), "last_snapshot_at": last_snapshot}


@app.get("/api/timeline")
async def deliverable_timeline(
    bpo_key: str | None = None,
    company: str | None = None,
    limit: int = 50,
):
    """Deliverable completion events."""
    if not settings.DATABASE_URL:
        return {"events": []}
    from orchestrator.deliverable_tracker import get_deliverable_timeline
    events = get_deliverable_timeline(bpo_key=bpo_key, company=company, limit=limit)
    for e in events:
        for k, v in e.items():
            if hasattr(v, "isoformat"):
                e[k] = v.isoformat()
    return {"events": events, "count": len(events)}


@app.get("/api/stage-history")
async def stage_history(
    bpo_key: str | None = None,
    company: str | None = None,
    limit: int = 50,
):
    """Stage transition history."""
    if not settings.DATABASE_URL:
        return {"changes": []}
    from orchestrator.deliverable_tracker import get_stage_history
    changes = get_stage_history(bpo_key=bpo_key, company=company, limit=limit)
    for c in changes:
        for k, v in c.items():
            if hasattr(v, "isoformat"):
                c[k] = v.isoformat()
    return {"changes": changes, "count": len(changes)}


@app.get("/api/stale")
async def stale_entries(days: int = 7):
    """Pipeline entries stuck in the same stage for more than N days."""
    if not settings.DATABASE_URL:
        return {"entries": []}
    from orchestrator.deliverable_tracker import get_stale_pipeline
    entries = get_stale_pipeline(days_threshold=days)
    for e in entries:
        for k, v in e.items():
            if hasattr(v, "isoformat"):
                e[k] = v.isoformat()
            elif isinstance(v, float):
                e[k] = round(v, 1)
    return {"entries": entries, "count": len(entries), "threshold_days": days}


@app.post("/api/snapshot")
async def force_snapshot():
    """Force an immediate pipeline snapshot refresh from Google Sheets."""
    if not settings.DATABASE_URL:
        raise HTTPException(status_code=400, detail="No DATABASE_URL configured")
    if not settings.GOOGLE_OAUTH_REFRESH_TOKEN:
        raise HTTPException(status_code=400, detail="No Google credentials configured")

    from orchestrator.deliverable_tracker import sync_pipeline_snapshot
    from shared.google_auth import get_access_token

    registry = _load_bpo_registry()
    token = get_access_token(
        settings.GOOGLE_OAUTH_CLIENT_ID,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
    )

    results = {}
    for bpo_key, entry in registry.items():
        sheet_id = entry.get("pipeline_sheet_id")
        if not sheet_id:
            results[bpo_key] = {"skipped": True, "reason": "no sheet"}
            continue

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A:P",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code != 200:
            results[bpo_key] = {"error": f"Sheet read failed: {resp.status_code}"}
            continue

        raw_rows = resp.json().get("values", [])
        if len(raw_rows) < 2:
            results[bpo_key] = {"skipped": True, "reason": "empty sheet"}
            continue

        headers_row = raw_rows[0]
        from orchestrator.deliverable_tracker import ALL_SHEET_COLUMNS
        col_map = {}
        for i, h in enumerate(headers_row):
            normalized = h.strip().lower().replace(" ", "_")
            for col in ALL_SHEET_COLUMNS:
                if col in normalized or normalized in col:
                    col_map[i] = col
                    break

        parsed = []
        for data_row in raw_rows[1:]:
            row_dict: dict[str, str] = {}
            for i, val in enumerate(data_row):
                if i in col_map:
                    row_dict[col_map[i]] = val
            if row_dict.get("company", "").strip():
                parsed.append(row_dict)

        results[bpo_key] = sync_pipeline_snapshot(bpo_key, parsed)

    return {"snapshot": results}


# Debounce: ignore rapid-fire edits within 10 seconds
_last_webhook_sync: float = 0.0


async def _run_snapshot_sync(only_bpo_key: str | None = None) -> dict[str, Any]:
    """Shared sync logic used by both the webhook and the manual snapshot endpoint.

    For each BPO with a pipeline_sheet_id (or just one when ``only_bpo_key``
    is given), fetches the sheet, writes a deliverable snapshot to Postgres,
    and appends any new prospects to the BPO's Attio ``bpo_referred_account``.

    Returns a summary dict: {"partners_synced", "attio_added", "attio_results"}.
    """
    from orchestrator.deliverable_tracker import sync_pipeline_snapshot, ALL_SHEET_COLUMNS
    from modules.bpo_sheet_sync import sync_bpo_referred_accounts
    from shared.google_auth import get_access_token

    registry = _load_bpo_registry()
    token = get_access_token(
        settings.GOOGLE_OAUTH_CLIENT_ID,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
    )

    partners_synced = 0
    attio_added_total = 0
    attio_results: list[dict[str, Any]] = []

    for bpo_key, entry in registry.items():
        if only_bpo_key and bpo_key != only_bpo_key:
            continue
        sheet_id = entry.get("pipeline_sheet_id")
        if not sheet_id:
            continue

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A:P",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code != 200:
            continue

        raw_rows = resp.json().get("values", [])
        if len(raw_rows) < 2:
            continue

        headers_row = raw_rows[0]
        col_map = {}
        for i, h in enumerate(headers_row):
            normalized = h.strip().lower().replace(" ", "_")
            for col in ALL_SHEET_COLUMNS:
                if col in normalized or normalized in col:
                    col_map[i] = col
                    break

        parsed = []
        for data_row in raw_rows[1:]:
            row_dict: dict[str, str] = {}
            for i, val in enumerate(data_row):
                if i in col_map:
                    row_dict[col_map[i]] = val
            if row_dict.get("company", "").strip():
                parsed.append(row_dict)

        if parsed:
            try:
                sync_pipeline_snapshot(bpo_key, parsed)
                partners_synced += 1
            except Exception:
                logger.exception("Postgres snapshot failed for %s", bpo_key)

            try:
                attio_outcome = await sync_bpo_referred_accounts(
                    bpo_key,
                    entry.get("attio_record_id"),
                    parsed,
                )
                attio_results.append(attio_outcome)
                attio_added_total += int(attio_outcome.get("added", 0))
            except Exception:
                logger.exception(
                    "Attio bpo_referred_account sync failed for %s", bpo_key
                )

    return {
        "partners_synced": partners_synced,
        "attio_added": attio_added_total,
        "attio_results": attio_results,
    }


@app.post("/api/webhook/sheet-update")
async def webhook_sheet_update(request: Request):
    """Webhook for real-time Google Sheet sync.

    Accepts two auth modes:
    1. Google Drive push notification — identified by X-Goog-Channel-ID header,
       validated against our registered watches. No secret needed.
    2. Secret-based — query param or JSON body with "secret" field.
       Used by manual calls or Apps Script fallback.
    """
    global _last_webhook_sync

    # Mode 1: Google Drive push notification
    goog_channel = request.headers.get("x-goog-channel-id", "")
    goog_state = request.headers.get("x-goog-resource-state", "")

    if goog_channel:
        # Validate it's one of our registered channels
        if goog_channel not in _active_watches:
            return {"status": "ignored", "reason": "unknown channel"}
        # "sync" is the initial verification ping — acknowledge but don't sync
        if goog_state == "sync":
            logger.info("Drive watch sync ping received for channel %s", goog_channel)
            return {"status": "ok", "message": "sync acknowledged"}
        # "update" / "change" means the file was modified
        logger.info("Drive push notification: %s state=%s", goog_channel, goog_state)
    else:
        # Mode 2: Secret-based auth
        secret = request.query_params.get("secret", "")
        if not secret:
            try:
                body = await request.json()
                secret = body.get("secret", "")
            except Exception:
                secret = ""

        if secret != settings.SHEET_WEBHOOK_SECRET:
            return Response(
                content=json.dumps({"detail": "Invalid webhook secret"}),
                status_code=403,
                media_type="application/json",
            )

    # Debounce: skip if we synced within the last 10 seconds
    now = time.time()
    if now - _last_webhook_sync < 10:
        return {"status": "debounced", "message": "Sync already ran within 10s"}

    _last_webhook_sync = now

    if not settings.DATABASE_URL or not settings.GOOGLE_OAUTH_REFRESH_TOKEN:
        return {"status": "skipped", "message": "No DB or Google creds configured"}

    summary = await _run_snapshot_sync()
    logger.info(
        "Sheet webhook sync complete: %d partners synced, %d Attio links added",
        summary.get("partners_synced", 0),
        summary.get("attio_added", 0),
    )
    return {"status": "ok", **summary}


@app.post("/api/cleanup-duplicates")
async def cleanup_duplicates():
    """Remove duplicate deliverable events and flip-flop stage changes."""
    if not settings.DATABASE_URL:
        raise HTTPException(status_code=400, detail="No DATABASE_URL configured")
    from orchestrator.deliverable_tracker import cleanup_duplicate_events
    return cleanup_duplicate_events()


@app.post("/api/attio/sync-bpo-referred")
async def sync_bpo_referred(request: Request):
    """Manually trigger the Sheet → Attio bpo_referred_account sync.

    Query params:
      bpo_key (optional) — sync only this BPO. Omit to sync all eligible BPOs.

    Useful for ad-hoc backfills and verification without waiting for a
    sheet edit. Honors DRY_RUN.
    """
    if not settings.GOOGLE_OAUTH_REFRESH_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_OAUTH_REFRESH_TOKEN not configured",
        )
    if not settings.ATTIO_API_KEY:
        raise HTTPException(
            status_code=400, detail="ATTIO_API_KEY not configured",
        )

    bpo_key = request.query_params.get("bpo_key") or None
    summary = await _run_snapshot_sync(only_bpo_key=bpo_key)
    return {"status": "ok", **summary}


# ---------------------------------------------------------------------------
# Convenience endpoints
# ---------------------------------------------------------------------------

@app.get("/api/bpo-registry")
async def bpo_registry():
    """Return the BPO partner registry."""
    return _load_bpo_registry()


@app.get("/api/settings/dry-run")
async def get_dry_run():
    return {"dry_run": settings.DRY_RUN}


@app.post("/api/settings/dry-run")
async def set_dry_run(request: Request):
    body = await request.json()
    settings.DRY_RUN = bool(body.get("dry_run", False))
    logger.info("DRY_RUN set to %s", settings.DRY_RUN)
    return {"dry_run": settings.DRY_RUN}


@app.get("/api/architecture")
async def architecture():
    """Live system documentation — modules, DAG structure, status."""
    from orchestrator.registry import all_modules

    modules_info = []
    for name, mod in all_modules().items():
        node = NODES.get(name, {})
        modules_info.append({
            "name": name,
            "phase": node.get("phase", Phase.PHASE_1).value if node else "unknown",
            "deps": node.get("deps", []),
        })

    return {
        "version": "2.0.0",
        "architecture": "DAG-based modular pipeline",
        "modules": modules_info,
        "phases": {
            "phase_1": "Content generation (classify → research → deck)",
            "phase_2": "Delivery (Drive upload → tracking → email → Slack)",
        },
        "background_tasks": {
            "gmail_poller": bool(_poller_task and not _poller_task.done()),
            "pipeline_tracker": bool(_tracker_task and not _tracker_task.done()),
        },
        "endpoints_count": len(app.routes),
    }


class ResearchRequest(BaseModel):
    company_url: str
    company_name: str = ""
    model: str = "o4-mini-deep-research-2025-06-26"


@app.post("/api/research")
async def on_demand_research(req: ResearchRequest):
    """Run OpenAI deep research on demand (not tied to a session)."""
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured")

    from modules.openai_research.module import OpenAIResearchModule, O4_MINI_MODEL
    from shared.types import EmailPayload, SessionContext

    ctx = SessionContext(
        session_id=f"research_{int(time.time())}",
        raw_email=EmailPayload(from_address="api", subject="On-demand research", body=""),
        target_company=req.company_name or req.company_url,
        target_url=req.company_url,
        deliverables_requested=["deep_research"],
    )

    module = OpenAIResearchModule()
    asyncio.create_task(_run_research_and_notify(module, ctx))

    return {"status": "started", "session_id": ctx.session_id, "model": req.model}


async def _run_research_and_notify(module, ctx: SessionContext) -> None:
    try:
        result = await module.run(ctx)
        company = ctx.target_company or "Unknown"
        if result.status == "success" and settings.SLACK_BOT_TOKEN:
            meta = result.metadata
            text = (
                f"*OpenAI Research Complete: {company}*\n"
                f"Duration: {meta.get('duration_seconds', 0):.0f}s | "
                f"Sources: {meta.get('sources', 0)} | "
                f"Length: {meta.get('content_length', 0)} chars"
            )
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
                    json={"channel": settings.SLACK_NOTIFY_CHANNEL, "text": text},
                    timeout=15,
                )
        logger.info("On-demand research complete for %s — %s", company, result.status)
    except Exception:
        logger.exception("On-demand research failed for %s", ctx.target_company)

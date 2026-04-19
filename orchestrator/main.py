"""BPO Sales Ops Pipeline — FastAPI Application."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
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

    # Register all pipeline modules
    from modules import register_all

    register_all()
    logger.info("All pipeline modules registered")

    # Start Gmail poller if credentials are configured
    global _poller_task
    if settings.GOOGLE_OAUTH_REFRESH_TOKEN and not settings.DRY_RUN:
        _poller_task = asyncio.create_task(_gmail_poll_loop())
        logger.info("Gmail poller task created")
    elif settings.DRY_RUN:
        logger.info("Gmail poller skipped — DRY_RUN mode")
    else:
        logger.info("Gmail poller skipped — no GOOGLE_OAUTH_REFRESH_TOKEN")

    yield

    # Shutdown
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        try:
            await _poller_task
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
    path = request.url.path

    # Skip auth for non-API routes, health, and Slack webhook
    if path.startswith("/slack/") or path == "/api/health" or not path.startswith("/api/"):
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

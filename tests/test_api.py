"""Tests for FastAPI API endpoints."""

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient

from orchestrator.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(in_memory_sessions, registered_modules):
    """Synchronous test client with in-memory session store."""
    return TestClient(app, raise_server_exceptions=False)


# ── Health ──────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


# ── Auth ────────────────────────────────────────────────────────────────

def test_auth_missing_token(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 401

def test_auth_invalid_token(client):
    resp = client.get("/api/sessions", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403

def test_auth_valid_token(client):
    resp = client.get("/api/sessions", headers=AUTH)
    assert resp.status_code == 200


# ── Process ─────────────────────────────────────────────────────────────

def test_process_creates_session(client):
    resp = client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "GameStop demo request",
        "body": "Please set up materials for GameStop.",
    }, headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["status"] == "received"


# ── Sessions ────────────────────────────────────────────────────────────

def test_list_sessions(client):
    # Create a session first
    client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "Test",
        "body": "Test body",
    }, headers=AUTH)

    resp = client.get("/api/sessions", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert len(data["sessions"]) >= 1

def test_session_not_found(client):
    resp = client.get("/api/sessions/nonexistent", headers=AUTH)
    assert resp.status_code == 404


# ── Approve / Reject ────────────────────────────────────────────────────

def test_reject_session(client, in_memory_sessions):
    # Create session
    resp = client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "Test reject",
        "body": "Test body",
    }, headers=AUTH)
    session_id = resp.json()["session_id"]

    # Manually set status to awaiting_approval (skip Phase 1)
    in_memory_sessions[session_id]["ctx"].status = "awaiting_approval"
    in_memory_sessions[session_id]["detail"]["status"] = "awaiting_approval"

    resp = client.post(f"/api/sessions/{session_id}/reject", json={
        "reason": "Not ready",
        "rejected_by": "test_user",
    }, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ── Slack Interactions ──────────────────────────────────────────────────

def test_slack_interaction_approve(client, in_memory_sessions):
    # Create session and set to awaiting_approval
    resp = client.post("/api/process", json={
        "from_address": "smukherjee@startek.com",
        "subject": "Wayfair materials",
        "body": "Need materials for Wayfair.",
    }, headers=AUTH)
    session_id = resp.json()["session_id"]
    in_memory_sessions[session_id]["ctx"].status = "awaiting_approval"
    in_memory_sessions[session_id]["detail"]["status"] = "awaiting_approval"

    # Build Slack payload
    payload = json.dumps({
        "type": "block_actions",
        "user": {"username": "richard"},
        "channel": {"id": "C0AQN1FNXNE"},
        "message": {"ts": "123456.789"},
        "actions": [{
            "action_id": "bpo_approve",
            "value": session_id,
        }],
    })

    resp = client.post("/slack/interactions", data=f"payload={payload}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 200

def test_slack_interaction_reject(client, in_memory_sessions):
    resp = client.post("/api/process", json={
        "from_address": "smukherjee@startek.com",
        "subject": "Test Slack reject",
        "body": "Test",
    }, headers=AUTH)
    session_id = resp.json()["session_id"]
    in_memory_sessions[session_id]["ctx"].status = "awaiting_approval"
    in_memory_sessions[session_id]["detail"]["status"] = "awaiting_approval"

    payload = json.dumps({
        "type": "block_actions",
        "user": {"username": "richard"},
        "channel": {"id": "C0AQN1FNXNE"},
        "message": {"ts": "123456.789"},
        "actions": [{
            "action_id": "bpo_reject",
            "value": session_id,
        }],
    })

    resp = client.post("/slack/interactions", data=f"payload={payload}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 200
    # Verify session was rejected
    assert in_memory_sessions[session_id]["detail"]["status"] == "rejected"


# ── Config ──────────────────────────────────────────────────────────────

def test_config_view(client):
    resp = client.get("/api/config", headers=AUTH)
    assert resp.status_code == 200
    config = resp.json()["config"]
    assert "ANTHROPIC_API_KEY" in config
    assert "POLL_INTERVAL_SECONDS" in config


# ── BPO Registry ───────────────────────────────────────────────────────

def test_bpo_registry(client):
    resp = client.get("/api/bpo-registry", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


# ── Dry-Run Toggle ─────────────────────────────────────────────────────

def test_dry_run_get(client):
    resp = client.get("/api/settings/dry-run", headers=AUTH)
    assert resp.status_code == 200
    assert "dry_run" in resp.json()


def test_dry_run_set(client):
    resp = client.post("/api/settings/dry-run", json={"dry_run": True}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True


# ── Architecture ───────────────────────────────────────────────────────

def test_architecture(client, registered_modules):
    resp = client.get("/api/architecture", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["architecture"] == "DAG-based modular pipeline"
    module_names = [m["name"] for m in data["modules"]]
    assert "classifier" in module_names
    assert "openai_research" in module_names


# ── Timeline / Stage / Stale (graceful without DB) ─────────────────────

def test_timeline_no_db(client):
    resp = client.get("/api/timeline", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_stage_history_no_db(client):
    resp = client.get("/api/stage-history", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["changes"] == []


def test_stale_no_db(client):
    resp = client.get("/api/stale", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["entries"] == []


def test_pipeline_tracker_summary_no_db(client):
    resp = client.get("/api/pipeline/tracker", headers=AUTH)
    assert resp.status_code == 200
    assert "error" in resp.json()


# ── Legacy compatibility shims (Lovable dashboard) ──────────────────────

def test_options_preflight_returns_cors_headers(client):
    """OPTIONS preflight must bypass auth so CORSMiddleware can answer.

    Regression guard: auth_middleware previously returned 401 with no CORS
    headers on OPTIONS, which the browser surfaced as a CORS error and
    blocked every authed call from the Lovable dashboard.
    """
    origin = "https://preview--faithful-snapshot-mirror.lovable.app"
    resp = client.options(
        "/api/dashboard",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin


def test_health_legacy_no_auth_and_shape(client):
    """/health is no-auth and matches the legacy poller payload shape."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    for key in ("tracked_messages", "pending_approvals", "total_sessions"):
        assert key in data
        assert isinstance(data[key], int)


def test_dashboard_alias_matches_pipeline_tracker(client):
    """/api/dashboard returns the same payload as /api/pipeline/tracker."""
    a = client.get("/api/dashboard", headers=AUTH)
    b = client.get("/api/pipeline/tracker", headers=AUTH)
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()


def test_approvals_legacy_filters_by_status(client, in_memory_sessions):
    """/api/approvals returns only sessions in awaiting_approval state."""
    # Seed two sessions: one received, one awaiting_approval
    r1 = client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "Pending",
        "body": "Pending body",
    }, headers=AUTH)
    pending_id = r1.json()["session_id"]
    in_memory_sessions[pending_id]["detail"]["status"] = "awaiting_approval"

    client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "Received",
        "body": "Received body",
    }, headers=AUTH)

    resp = client.get("/api/approvals", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    ids = [row["session_id"] for row in data["approvals"]]
    assert pending_id in ids
    assert all(row["status"] == "awaiting_approval" for row in data["approvals"])
    assert data["count"] == len(data["approvals"])


def test_approvals_legacy_reject_routes_through(client, in_memory_sessions):
    """POST /api/approvals/{id}/reject forwards to the new reject handler."""
    resp = client.post("/api/process", json={
        "from_address": "jarmstrong@resultscx.com",
        "subject": "Legacy reject test",
        "body": "body",
    }, headers=AUTH)
    session_id = resp.json()["session_id"]
    in_memory_sessions[session_id]["ctx"].status = "awaiting_approval"
    in_memory_sessions[session_id]["detail"]["status"] = "awaiting_approval"

    resp = client.post(
        f"/api/approvals/{session_id}/reject",
        json={"reason": "test", "rejected_by": "tester"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

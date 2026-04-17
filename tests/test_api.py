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

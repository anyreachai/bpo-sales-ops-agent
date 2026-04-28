"""Tests for the attio_sync module."""

from __future__ import annotations

import httpx
import pytest

from modules.attio_sync.attio_client import (
    COMPANIES_ASSERT_URL,
    build_assert_payload,
    extract_domain,
)
from modules.attio_sync.module import AttioSyncModule
from orchestrator.config import settings


# ── extract_domain ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com", "example.com"),
        ("https://example.com/", "example.com"),
        ("https://example.com/about/team", "example.com"),
        ("https://www.example.com", "example.com"),
        ("https://WWW.Example.COM/path", "example.com"),
        ("http://example.com", "example.com"),
        ("example.com", "example.com"),
        ("www.example.com/path", "example.com"),
        ("  https://example.com  ", "example.com"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_extract_domain(url, expected):
    assert extract_domain(url) == expected


# ── build_assert_payload ───────────────────────────────────────────────


def test_build_assert_payload_shape():
    payload = build_assert_payload(
        name="GameStop",
        domain="gamestop.com",
        connector_record_id="6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf",
    )
    values = payload["data"]["values"]
    assert values["name"] == [{"value": "GameStop"}]
    assert values["domains"] == [{"domain": "gamestop.com"}]
    assert values["connector_bpo_channel_partner"] == [
        {
            "target_object": "companies",
            "target_record_id": "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf",
        }
    ]


# ── should_run ─────────────────────────────────────────────────────────


@pytest.fixture
def attio_settings(monkeypatch):
    monkeypatch.setattr(settings, "ATTIO_API_KEY", "attio-test-key")
    monkeypatch.setattr(settings, "ATTIO_SYNC_ENABLED", True)


def _attach_attio_id(ctx, record_id="6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"):
    if ctx.bpo:
        ctx.bpo.attio_record_id = record_id
    return ctx


def test_should_run_happy_path(attio_settings, session_factory):
    ctx = _attach_attio_id(session_factory())
    assert AttioSyncModule().should_run(ctx) is True


def test_should_run_disabled_flag(attio_settings, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "ATTIO_SYNC_ENABLED", False)
    ctx = _attach_attio_id(session_factory())
    assert AttioSyncModule().should_run(ctx) is False


def test_should_run_no_api_key(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "ATTIO_API_KEY", "")
    ctx = _attach_attio_id(session_factory())
    assert AttioSyncModule().should_run(ctx) is False


def test_should_run_no_bpo(attio_settings, session_factory):
    ctx = session_factory(include_bpo=False)
    ctx.bpo = None
    assert AttioSyncModule().should_run(ctx) is False


def test_should_run_no_attio_record_id(attio_settings, session_factory):
    ctx = session_factory()  # bpo created without attio_record_id → None default
    assert AttioSyncModule().should_run(ctx) is False


def test_should_run_no_target_company(attio_settings, session_factory):
    ctx = _attach_attio_id(session_factory())
    ctx.target_company = None
    assert AttioSyncModule().should_run(ctx) is False


def test_should_run_no_target_url(attio_settings, session_factory):
    ctx = _attach_attio_id(session_factory())
    ctx.target_url = None
    assert AttioSyncModule().should_run(ctx) is False


# ── run: dry-run path ──────────────────────────────────────────────────


async def test_run_dry_run_does_not_call_attio(
    attio_settings, session_factory, monkeypatch
):
    """Dry-run must short-circuit before any HTTP call."""
    calls: list[tuple[str, str]] = []

    class ExplodingClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def put(self, url, **kwargs):
            calls.append(("PUT", url))
            raise AssertionError("Dry-run must not make HTTP calls")

    monkeypatch.setattr("httpx.AsyncClient", ExplodingClient)

    ctx = _attach_attio_id(session_factory(dry_run=True))
    result = await AttioSyncModule().run(ctx)

    assert result.status == "success"
    assert result.metadata["dry_run"] is True
    assert result.metadata["company"] == "GameStop"
    assert result.metadata["domain"] == "gamestop.com"
    assert (
        result.metadata["connector_id"]
        == "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"
    )
    assert result.metadata["would_send"]["url"] == COMPANIES_ASSERT_URL
    assert (
        result.metadata["would_send"]["payload"]["data"]["values"][
            "connector_bpo_channel_partner"
        ][0]["target_record_id"]
        == "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"
    )
    assert calls == []


# ── run: live path with recording client ──────────────────────────────


class _RecordingClient:
    """Custom httpx.AsyncClient stand-in that records the PUT call."""

    last_instance: "_RecordingClient | None" = None

    def __init__(self, **kwargs):
        self.calls: list[dict] = []
        _RecordingClient.last_instance = self
        self._status = 200
        self._json: dict = {
            "data": {"id": {"record_id": "company_record_xyz"}}
        }

    @classmethod
    def configure(cls, *, status: int = 200, json_data: dict | None = None):
        # Side-channel for tests to set the next response.
        cls._next_status = status
        cls._next_json = json_data or {
            "data": {"id": {"record_id": "company_record_xyz"}}
        }

    async def __aenter__(self):
        # Pull configured response if present
        self._status = getattr(_RecordingClient, "_next_status", 200)
        self._json = getattr(
            _RecordingClient,
            "_next_json",
            {"data": {"id": {"record_id": "company_record_xyz"}}},
        )
        return self

    async def __aexit__(self, *args):
        pass

    async def put(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        request = httpx.Request("PUT", url)
        return httpx.Response(
            self._status, json=self._json, request=request
        )


@pytest.fixture
def recording_client(monkeypatch):
    _RecordingClient.configure(status=200)
    monkeypatch.setattr("httpx.AsyncClient", _RecordingClient)
    return _RecordingClient


async def test_run_live_success(attio_settings, session_factory, recording_client):
    ctx = _attach_attio_id(session_factory(dry_run=False))
    result = await AttioSyncModule().run(ctx)

    assert result.status == "success"
    assert result.metadata["connector_set"] is True
    assert result.metadata["attio_record_id"] == "company_record_xyz"
    assert result.metadata["domain"] == "gamestop.com"

    # Verify the actual PUT shape
    instance = recording_client.last_instance
    assert instance is not None
    assert len(instance.calls) == 1
    call = instance.calls[0]
    assert call["url"] == COMPANIES_ASSERT_URL
    assert call["headers"]["Authorization"] == "Bearer attio-test-key"
    assert call["headers"]["Content-Type"] == "application/json"
    body = call["json"]["data"]["values"]
    assert body["name"] == [{"value": "GameStop"}]
    assert body["domains"] == [{"domain": "gamestop.com"}]
    assert body["connector_bpo_channel_partner"][0]["target_record_id"] == (
        "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"
    )


async def test_run_live_4xx_failure(
    attio_settings, session_factory, recording_client
):
    recording_client.configure(status=422, json_data={"error": "validation"})
    ctx = _attach_attio_id(session_factory(dry_run=False))
    result = await AttioSyncModule().run(ctx)

    assert result.status == "failed"
    assert "422" in (result.error or "")
    assert result.metadata["status_code"] == 422


# ── execute() also returns skipped when should_run gates fail ─────────


async def test_execute_skipped_when_no_attio_id(attio_settings, session_factory):
    """End-to-end execute() (the BaseModule wrapper) honors should_run."""
    ctx = session_factory()  # no attio_record_id
    result = await AttioSyncModule().execute(ctx)
    assert result.status == "skipped"

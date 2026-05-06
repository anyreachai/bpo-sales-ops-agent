"""Tests for the bpo_sheet_sync module (Sheet → Attio bpo_referred_account)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from modules.attio_sync.attio_client import (
    BPO_REFERRED_SLUG,
    extract_referred_ids,
    build_referred_patch_payload,
)
from modules.bpo_sheet_sync import sync_bpo_referred_accounts
from orchestrator.config import settings


BPO_RECORD_ID = "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"
PROSPECT_A = "11111111-1111-1111-1111-111111111111"
PROSPECT_B = "22222222-2222-2222-2222-222222222222"
PROSPECT_C = "33333333-3333-3333-3333-333333333333"


# ── extract_referred_ids unit tests ──────────────────────────────────


def test_extract_referred_ids_handles_write_shape():
    record = {
        "data": {
            "values": {
                BPO_REFERRED_SLUG: [
                    {"target_object": "companies", "target_record_id": PROSPECT_A},
                    {"target_object": "companies", "target_record_id": PROSPECT_B},
                ]
            }
        }
    }
    assert extract_referred_ids(record) == {PROSPECT_A, PROSPECT_B}


def test_extract_referred_ids_handles_read_shape_nested_target_object():
    record = {
        "data": {
            "values": {
                BPO_REFERRED_SLUG: [
                    {"target_object": {"record_id": PROSPECT_A}},
                    {"target_object": {"record_id": PROSPECT_B}},
                ]
            }
        }
    }
    assert extract_referred_ids(record) == {PROSPECT_A, PROSPECT_B}


def test_extract_referred_ids_handles_read_shape_nested_target_record():
    record = {
        "data": {
            "values": {
                BPO_REFERRED_SLUG: [
                    {"target_record": {"record_id": PROSPECT_A}},
                ]
            }
        }
    }
    assert extract_referred_ids(record) == {PROSPECT_A}


def test_extract_referred_ids_missing_attribute_returns_empty():
    assert extract_referred_ids({"data": {"values": {}}}) == set()
    assert extract_referred_ids({}) == set()
    assert extract_referred_ids(None) == set()


def test_build_referred_patch_payload_shape():
    payload = build_referred_patch_payload({PROSPECT_A, PROSPECT_B})
    items = payload["data"]["values"][BPO_REFERRED_SLUG]
    assert len(items) == 2
    rids = {entry["target_record_id"] for entry in items}
    assert rids == {PROSPECT_A, PROSPECT_B}
    for entry in items:
        assert entry["target_object"] == "companies"


# ── Recording httpx client for live-path tests ───────────────────────


class _RecordingAttioClient:
    """Records GET / PUT / PATCH calls and returns configured responses.

    Replaces the global httpx.AsyncClient mock from conftest because we
    need fine-grained per-call control over fetch / assert / patch.
    """

    last_instance: "_RecordingAttioClient | None" = None
    _next_assert_responses: list[dict] = []
    _next_assert_status: int = 200
    _next_fetch_response: dict | None = None
    _next_fetch_status: int = 200
    _next_patch_status: int = 200

    def __init__(self, **kwargs):
        self.calls: list[dict[str, Any]] = []
        type(self).last_instance = self
        self._assert_idx = 0

    @classmethod
    def configure(
        cls,
        *,
        assert_responses: list[dict] | None = None,
        assert_status: int = 200,
        fetch_response: dict | None = None,
        fetch_status: int = 200,
        patch_status: int = 200,
    ):
        cls._next_assert_responses = list(assert_responses or [])
        cls._next_assert_status = assert_status
        cls._next_fetch_response = fetch_response
        cls._next_fetch_status = fetch_status
        cls._next_patch_status = patch_status
        cls.last_instance = None  # reset per-test so leak between tests can't mask bugs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def put(self, url: str, **kwargs):
        self.calls.append({"method": "PUT", "url": url, **kwargs})
        if self._assert_idx < len(self._next_assert_responses):
            body = self._next_assert_responses[self._assert_idx]
        else:
            body = {"data": {"id": {"record_id": f"prospect_{self._assert_idx}"}}}
        self._assert_idx += 1
        request = httpx.Request("PUT", url)
        return httpx.Response(self._next_assert_status, json=body, request=request)

    async def get(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        body = self._next_fetch_response or {"data": {"values": {}}}
        request = httpx.Request("GET", url)
        return httpx.Response(self._next_fetch_status, json=body, request=request)

    async def patch(self, url: str, **kwargs):
        self.calls.append({"method": "PATCH", "url": url, **kwargs})
        request = httpx.Request("PATCH", url)
        return httpx.Response(self._next_patch_status, json={"ok": True}, request=request)


@pytest.fixture
def recording_attio(monkeypatch):
    _RecordingAttioClient.configure()
    monkeypatch.setattr("httpx.AsyncClient", _RecordingAttioClient)
    return _RecordingAttioClient


@pytest.fixture
def attio_settings(monkeypatch):
    monkeypatch.setattr(settings, "ATTIO_API_KEY", "attio-test-key")
    monkeypatch.setattr(settings, "ATTIO_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "DRY_RUN", False)


def _row(company: str = "GameStop", website: str = "https://gamestop.com", **extra) -> dict:
    return {"company": company, "website_url": website, **extra}


# ── Skip-path tests (no HTTP) ────────────────────────────────────────


async def test_skipped_when_sync_disabled(attio_settings, monkeypatch, recording_attio):
    monkeypatch.setattr(settings, "ATTIO_SYNC_ENABLED", False)
    result = await sync_bpo_referred_accounts(
        "resultscx", BPO_RECORD_ID, [_row()],
    )
    assert result["skipped_reason"] == "ATTIO_SYNC_ENABLED is False"
    assert _RecordingAttioClient.last_instance is None or len(
        _RecordingAttioClient.last_instance.calls
    ) == 0


async def test_skipped_when_no_api_key(attio_settings, monkeypatch, recording_attio):
    monkeypatch.setattr(settings, "ATTIO_API_KEY", "")
    result = await sync_bpo_referred_accounts(
        "resultscx", BPO_RECORD_ID, [_row()],
    )
    assert result["skipped_reason"] == "ATTIO_API_KEY not configured"


async def test_skipped_when_bpo_record_id_none(attio_settings, recording_attio):
    result = await sync_bpo_referred_accounts(
        "cgs", None, [_row()],
    )
    assert result["skipped_reason"] == "BPO has no attio_record_id"


async def test_skipped_when_no_rows(attio_settings, recording_attio):
    result = await sync_bpo_referred_accounts(
        "resultscx", BPO_RECORD_ID, [],
    )
    assert result["skipped_reason"] == "no rows to sync"


async def test_skips_rows_without_company_or_domain(attio_settings, recording_attio):
    rows = [
        _row(company="", website="https://valid.com"),       # no company
        _row(company="NoDomain", website=""),                  # no domain
        _row(company="BadUrl", website="   "),                 # whitespace domain
        _row(company="GoodCo", website="https://good.com"),    # eligible
    ]
    _RecordingAttioClient.configure(
        assert_responses=[
            {"data": {"id": {"record_id": PROSPECT_A}}},
        ],
        fetch_response={"data": {"values": {}}},
    )
    result = await sync_bpo_referred_accounts("resultscx", BPO_RECORD_ID, rows)

    assert result["skipped_no_company"] == 1
    assert result["skipped_no_domain"] == 2
    assert result["asserted"] == 1
    # PUT (1 assert) + GET (1 fetch) + PATCH (1 update) = 3 calls
    calls = _RecordingAttioClient.last_instance.calls
    assert sum(1 for c in calls if c["method"] == "PUT") == 1
    assert sum(1 for c in calls if c["method"] == "GET") == 1
    assert sum(1 for c in calls if c["method"] == "PATCH") == 1


# ── Dry-run path ──────────────────────────────────────────────────────


async def test_dry_run_makes_no_http_calls(attio_settings, monkeypatch, recording_attio):
    monkeypatch.setattr(settings, "DRY_RUN", True)
    result = await sync_bpo_referred_accounts(
        "resultscx", BPO_RECORD_ID, [_row(), _row(company="Acme", website="https://acme.com")],
    )
    assert result["dry_run"] is True
    assert result["skipped_reason"] == "DRY_RUN"
    assert len(result["would_assert"]) == 2
    assert result["would_patch_bpo"] == BPO_RECORD_ID
    # The recording client may exist but should have no calls
    inst = _RecordingAttioClient.last_instance
    assert inst is None or len(inst.calls) == 0


# ── Live append-only path ────────────────────────────────────────────


async def test_append_only_merges_existing_with_new(attio_settings, recording_attio):
    """Existing {A,B} + new asserts {B,C} → PATCH list {A,B,C}."""
    _RecordingAttioClient.configure(
        assert_responses=[
            {"data": {"id": {"record_id": PROSPECT_B}}},  # row 1 → already linked
            {"data": {"id": {"record_id": PROSPECT_C}}},  # row 2 → new
        ],
        fetch_response={
            "data": {
                "values": {
                    BPO_REFERRED_SLUG: [
                        {"target_object": "companies", "target_record_id": PROSPECT_A},
                        {"target_object": "companies", "target_record_id": PROSPECT_B},
                    ]
                }
            }
        },
    )
    result = await sync_bpo_referred_accounts(
        "resultscx",
        BPO_RECORD_ID,
        [
            _row(company="B Co", website="https://b.com"),
            _row(company="C Co", website="https://c.com"),
        ],
    )

    assert result["asserted"] == 2
    assert result["existing"] == 2
    assert result["added"] == 1  # only C is new
    assert result["patched"] is True

    # Inspect the PATCH payload
    patches = [c for c in _RecordingAttioClient.last_instance.calls if c["method"] == "PATCH"]
    assert len(patches) == 1
    patch_body = patches[0]["json"]
    rids_sent = {e["target_record_id"] for e in patch_body["data"]["values"][BPO_REFERRED_SLUG]}
    assert rids_sent == {PROSPECT_A, PROSPECT_B, PROSPECT_C}


async def test_no_op_when_nothing_new(attio_settings, recording_attio):
    """If all asserted prospects already linked, no PATCH is issued."""
    _RecordingAttioClient.configure(
        assert_responses=[
            {"data": {"id": {"record_id": PROSPECT_A}}},
        ],
        fetch_response={
            "data": {
                "values": {
                    BPO_REFERRED_SLUG: [
                        {"target_object": "companies", "target_record_id": PROSPECT_A},
                    ]
                }
            }
        },
    )
    result = await sync_bpo_referred_accounts(
        "resultscx",
        BPO_RECORD_ID,
        [_row(company="A Co", website="https://a.com")],
    )

    assert result["added"] == 0
    assert result["patched"] is False
    patches = [c for c in _RecordingAttioClient.last_instance.calls if c["method"] == "PATCH"]
    assert patches == []


# ── Error-path tests ─────────────────────────────────────────────────


async def test_fetch_bpo_4xx_records_error_and_skips_patch(attio_settings, recording_attio):
    _RecordingAttioClient.configure(
        assert_responses=[
            {"data": {"id": {"record_id": PROSPECT_A}}},
        ],
        fetch_status=404,
        fetch_response={"error": "not found"},
    )
    result = await sync_bpo_referred_accounts(
        "resultscx",
        BPO_RECORD_ID,
        [_row(company="A Co", website="https://a.com")],
    )

    assert result["asserted"] == 1
    assert result["patched"] is False
    assert any("fetch BPO" in e for e in result["errors"])
    patches = [c for c in _RecordingAttioClient.last_instance.calls if c["method"] == "PATCH"]
    assert patches == []


async def test_assert_4xx_recorded_others_proceed(attio_settings, recording_attio):
    """A failing assert on one row doesn't kill the whole sync."""
    _RecordingAttioClient.configure(
        assert_status=422,
        assert_responses=[{"error": "validation"}, {"error": "validation"}],
        fetch_response={"data": {"values": {}}},
    )
    result = await sync_bpo_referred_accounts(
        "resultscx",
        BPO_RECORD_ID,
        [
            _row(company="A Co", website="https://a.com"),
            _row(company="B Co", website="https://b.com"),
        ],
    )

    assert result["assert_errors"] == 2
    assert result["asserted"] == 0
    assert len(result["errors"]) == 2
    # No new IDs to add → no PATCH
    patches = [c for c in _RecordingAttioClient.last_instance.calls if c["method"] == "PATCH"]
    assert patches == []

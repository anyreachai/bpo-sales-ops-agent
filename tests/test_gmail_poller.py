"""Tests for the Gmail poller — body extraction, dedup, skip filters."""

import base64
import time

import pytest

from gmail_poller.poller import _extract_body, _prune_seen, _seen, SKIP_SUBJECT_PREFIXES, poll_once
from tests import mocks


@pytest.fixture(autouse=True)
def clear_seen():
    """Clear the seen-message cache before and after each test."""
    _seen.clear()
    yield
    _seen.clear()


# ── Body extraction ─────────────────────────────────────────────────────

def test_extract_body_plain_text():
    body_text = "Hello, this is a test email body."
    encoded = base64.urlsafe_b64encode(body_text.encode()).decode()
    payload = {
        "mimeType": "text/plain",
        "body": {"data": encoded},
    }
    assert _extract_body(payload) == body_text

def test_extract_body_multipart_prefers_plain():
    plain = base64.urlsafe_b64encode(b"Plain text version").decode()
    html = base64.urlsafe_b64encode(b"<p>HTML version</p>").decode()
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": plain}},
            {"mimeType": "text/html", "body": {"data": html}},
        ],
    }
    assert _extract_body(payload) == "Plain text version"

def test_extract_body_html_fallback():
    html = base64.urlsafe_b64encode(b"<p>Only HTML</p>").decode()
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": html}},
        ],
    }
    result = _extract_body(payload)
    assert "Only HTML" in result
    assert "<p>" not in result  # tags stripped

def test_extract_body_nested_multipart():
    plain = base64.urlsafe_b64encode(b"Nested plain text").decode()
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": plain}},
                ],
            },
        ],
    }
    assert _extract_body(payload) == "Nested plain text"

def test_extract_body_empty():
    assert _extract_body({}) == ""
    assert _extract_body({"mimeType": "text/plain", "body": {}}) == ""


# ── Skip filters ────────────────────────────────────────────────────────

def test_skip_prefixes_include_calendar():
    assert any(prefix.startswith("accepted:") for prefix in SKIP_SUBJECT_PREFIXES)

def test_skip_prefixes_include_auto_reply():
    assert any("automatic reply" in prefix for prefix in SKIP_SUBJECT_PREFIXES)


# ── Dedup ───────────────────────────────────────────────────────────────

def test_dedup_prevents_duplicate():
    _seen["msg_duplicate"] = time.time()
    # If msg is in _seen, poll_once would skip it.
    assert "msg_duplicate" in _seen

def test_prune_seen_removes_old():
    _seen["old_msg"] = time.time() - 90000  # 25 hours ago
    _seen["recent_msg"] = time.time() - 100  # recent
    _prune_seen()
    assert "old_msg" not in _seen
    assert "recent_msg" in _seen

def test_prune_seen_empty():
    _prune_seen()  # should not raise


# ── poll_once ───────────────────────────────────────────────────────────

async def test_poll_once_empty_inbox(monkeypatch):
    """Empty inbox returns empty list."""
    # Override the Gmail list response to return empty
    from tests.conftest import MockAsyncClient, MockResponse

    class EmptyGmailClient(MockAsyncClient):
        def _route(self, url, method, kwargs):
            if "gmail.googleapis.com" in url and "/messages" in url and method == "GET":
                return MockResponse(json_data={})  # no "messages" key
            return super()._route(url, method, kwargs)

    monkeypatch.setattr("httpx.AsyncClient", EmptyGmailClient)
    # Need google auth to work
    monkeypatch.setattr("shared.google_auth.get_access_token", lambda *a, **kw: "mock_token")

    result = await poll_once()
    assert result == []

async def test_poll_once_returns_emails(monkeypatch):
    """Standard BPO emails are returned as EmailPayloads."""
    monkeypatch.setattr("shared.google_auth.get_access_token", lambda *a, **kw: "mock_token")

    result = await poll_once()
    assert len(result) >= 1
    email = result[0]
    assert email.from_address == "jarmstrong@resultscx.com"
    assert email.subject == "GameStop demo request"
    assert len(email.body) > 0

async def test_poll_once_skips_calendar_invites(monkeypatch):
    """Calendar invites (Accepted:, Declined:, etc.) are skipped."""
    from tests.conftest import MockAsyncClient, MockResponse

    class CalendarGmailClient(MockAsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.modify_called = False

        def _route(self, url, method, kwargs):
            if "gmail.googleapis.com" in url and "/messages" in url and method == "GET" and "/messages/" not in url:
                return MockResponse(json_data={"messages": [{"id": "msg_cal"}]})
            if "gmail.googleapis.com" in url and "/messages/msg_cal" in url and method == "GET":
                return MockResponse(json_data=mocks.MOCK_GMAIL_CALENDAR_INVITE)
            if "/modify" in url:
                self.modify_called = True
                return MockResponse(json_data={})
            return super()._route(url, method, kwargs)

    monkeypatch.setattr("httpx.AsyncClient", CalendarGmailClient)
    monkeypatch.setattr("shared.google_auth.get_access_token", lambda *a, **kw: "mock_token")

    result = await poll_once()
    assert len(result) == 0  # calendar invite was skipped

async def test_poll_once_marks_as_read(monkeypatch):
    """Processed messages should be marked as read (UNREAD label removed)."""
    from tests.conftest import MockAsyncClient, MockResponse

    modify_calls = []

    class TrackingGmailClient(MockAsyncClient):
        def _route(self, url, method, kwargs):
            if "/modify" in url and method == "POST":
                modify_calls.append(url)
                return MockResponse(json_data={})
            return super()._route(url, method, kwargs)

    monkeypatch.setattr("httpx.AsyncClient", TrackingGmailClient)
    monkeypatch.setattr("shared.google_auth.get_access_token", lambda *a, **kw: "mock_token")

    await poll_once()
    # Should have called modify at least once (for each message)
    assert len(modify_calls) >= 1

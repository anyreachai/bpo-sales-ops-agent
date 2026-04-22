"""Shared test fixtures — intercepts all external service calls."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests import mocks


# ── pytest-asyncio config ──────────────────────────────────────────────
# asyncio_mode = "auto" is set in pyproject.toml


# ── Temp directory for artifacts ───────────────────────────────────────

@pytest.fixture(autouse=True)
def artifacts_dir(tmp_path, monkeypatch):
    """Redirect all artifact writes to a temp directory."""
    monkeypatch.setattr("shared.storage.TEMP_DIR", tmp_path)
    return tmp_path


# ── Settings overrides ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """Set test-safe config values — no real API keys or DB."""
    from orchestrator.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr(settings, "BRAND_DEV_API_KEY", "bdev-test-key")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_REFRESH_TOKEN", "")  # Prevents poller from starting
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "")
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai-test-key")
    monkeypatch.setattr(settings, "DRY_RUN", False)


# ── Anthropic API mocks ───────────────────────────────────────────────

async def _mock_call_sonnet(api_key, prompt, system="", max_tokens=4096):
    """Route mock responses based on system prompt content."""
    sys_lower = system.lower()
    prompt_lower = prompt.lower()
    if "email classifier" in sys_lower or "bpo sales operations" in sys_lower:
        return mocks.MOCK_CLASSIFICATION_RESPONSE
    if "slide" in sys_lower or "pitch" in sys_lower or "deck" in prompt_lower:
        return mocks.MOCK_SLIDE_JSON
    if "email reply" in sys_lower or "email assistant" in sys_lower or "professional email" in sys_lower:
        return mocks.MOCK_EMAIL_BODY
    # Default
    return mocks.MOCK_CLASSIFICATION_RESPONSE


async def _mock_call_opus(api_key, prompt, system="", max_tokens=16000):
    """Route mock responses for Opus calls."""
    prompt_lower = prompt.lower()
    if "stakeholder" in prompt_lower or "contact" in prompt_lower:
        return mocks.MOCK_STAKEHOLDER_MARKDOWN
    # Default to research
    return mocks.MOCK_RESEARCH_MARKDOWN


def _mock_get_client(api_key):
    """Return a mock Anthropic client for CX Intel scraper."""
    mock_client = MagicMock()

    # Build a mock response object that looks like Anthropic's response
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = mocks.MOCK_SCRAPER_RAW_JSON

    mock_response = MagicMock()
    mock_response.content = [mock_text_block]

    # Make messages.create async
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    return mock_client


@pytest.fixture(autouse=True)
def mock_anthropic(monkeypatch):
    """Patch all Anthropic API calls at source AND usage sites.

    Modules use ``from shared.anthropic_client import call_sonnet`` which
    creates local bindings unaffected by patching the source module alone.
    """
    monkeypatch.setattr("shared.anthropic_client.call_sonnet", _mock_call_sonnet)
    monkeypatch.setattr("shared.anthropic_client.call_opus_with_search", _mock_call_opus)
    monkeypatch.setattr("shared.anthropic_client.get_client", _mock_get_client)
    # Patch at each module's usage site (from-import local bindings)
    monkeypatch.setattr("modules.classifier.module.call_sonnet", _mock_call_sonnet)
    monkeypatch.setattr("modules.deck_generator.module.call_sonnet", _mock_call_sonnet)
    monkeypatch.setattr("modules.email_composer.module.call_sonnet", _mock_call_sonnet)
    monkeypatch.setattr("modules.deep_research.module.call_opus_with_search", _mock_call_opus)
    monkeypatch.setattr("modules.stakeholder_intel.module.call_opus_with_search", _mock_call_opus)
    monkeypatch.setattr("modules.cx_intel.scraper.get_client", _mock_get_client)
    # Also mock the high-level scrape_reviews at the module import site so multi-pass
    # scraper doesn't make multiple calls during tests
    async def _mock_scrape_reviews(company_name, company_url, api_key):
        return dict(mocks.MOCK_REVIEW_DATA)
    monkeypatch.setattr("modules.cx_intel.module.scrape_reviews", _mock_scrape_reviews)
    # Reset singleton client
    monkeypatch.setattr("shared.anthropic_client._client", None)


# ── Google Auth mock ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_google_auth(monkeypatch):
    """Patch Google OAuth token refresh at source AND usage sites."""
    _mock_token = lambda *args, **kwargs: "mock_access_token"
    monkeypatch.setattr("shared.google_auth.get_access_token", _mock_token)
    # Patch at each module's usage site (from-import local bindings)
    monkeypatch.setattr("modules.drive_manager.module.get_access_token", _mock_token)
    monkeypatch.setattr("modules.email_composer.module.get_access_token", _mock_token)
    monkeypatch.setattr("modules.pipeline_tracker.module.get_access_token", _mock_token)
    monkeypatch.setattr("gmail_poller.poller.get_access_token", _mock_token)
    # Reset cached token
    monkeypatch.setattr("shared.google_auth._cached_token", None)
    monkeypatch.setattr("shared.google_auth._token_expires_at", 0.0)


# ── httpx mock (covers Drive, Sheets, Gmail, Slack, Brand.dev) ────────

class MockResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace") if content else json.dumps(self._json_data)

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://mock")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"Mock {self.status_code}", request=request, response=response,
            )


class MockAsyncClient:
    """Drop-in replacement for httpx.AsyncClient that routes by URL."""

    def __init__(self, **kwargs):
        self.requests: list[tuple[str, str, dict]] = []
        self._timeout = kwargs.get("timeout", 30)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url: str, **kwargs) -> MockResponse:
        self.requests.append(("GET", url, kwargs))
        return self._route(url, "GET", kwargs)

    async def post(self, url: str, **kwargs) -> MockResponse:
        self.requests.append(("POST", url, kwargs))
        return self._route(url, "POST", kwargs)

    async def put(self, url: str, **kwargs) -> MockResponse:
        self.requests.append(("PUT", url, kwargs))
        return self._route(url, "PUT", kwargs)

    def _route(self, url: str, method: str, kwargs: dict) -> MockResponse:
        # Logo / image downloads (check before domain routes to avoid
        # brand.dev logo URLs being caught by the API route)
        if any(ext in url.lower() for ext in [".png", ".jpg", ".svg"]):
            return MockResponse(content=mocks.FAKE_PNG_BYTES)

        # Brand.dev API
        if "brand.dev" in url:
            return MockResponse(json_data=mocks.MOCK_BRAND_DATA)

        # Google Drive — file search
        if "googleapis.com/drive/v3/files" in url and method == "GET":
            return MockResponse(json_data=mocks.MOCK_DRIVE_FOLDER_FOUND)

        # Google Drive — create folder or file metadata
        if "googleapis.com/drive/v3/files" in url and method == "POST":
            return MockResponse(json_data=mocks.MOCK_DRIVE_CREATE_RESPONSE)

        # Google Drive — multipart upload
        if "googleapis.com/upload/drive" in url:
            return MockResponse(json_data=mocks.MOCK_DRIVE_UPLOAD_RESPONSE)

        # Google Sheets — read
        if "sheets.googleapis.com" in url and method == "GET":
            return MockResponse(json_data=mocks.MOCK_SHEETS_READ_RESPONSE)

        # Google Sheets — append/update
        if "sheets.googleapis.com" in url and method in ("POST", "PUT"):
            return MockResponse(json_data=mocks.MOCK_SHEETS_APPEND_RESPONSE)

        # Gmail — list messages
        if "gmail.googleapis.com" in url and "/messages" in url and method == "GET" and "/messages/" not in url:
            return MockResponse(json_data=mocks.MOCK_GMAIL_LIST_RESPONSE)

        # Gmail — get message detail
        if "gmail.googleapis.com" in url and "/messages/" in url and method == "GET":
            return MockResponse(json_data=mocks.MOCK_GMAIL_MESSAGE_DETAIL)

        # Gmail — modify (mark as read)
        if "gmail.googleapis.com" in url and "/modify" in url:
            return MockResponse(json_data={})

        # Gmail — create draft
        if "gmail.googleapis.com" in url and "/drafts" in url and method == "POST":
            return MockResponse(json_data=mocks.MOCK_GMAIL_DRAFT_RESPONSE)

        # Slack API
        if "slack.com/api" in url:
            return MockResponse(json_data=mocks.MOCK_SLACK_OK_RESPONSE)

        # OpenAI API — submit
        if "api.openai.com/v1/responses" in url and method == "POST":
            return MockResponse(json_data=mocks.MOCK_OPENAI_SUBMIT_RESPONSE)

        # OpenAI API — poll
        if "api.openai.com/v1/responses/" in url and method == "GET":
            return MockResponse(json_data=mocks.MOCK_OPENAI_POLL_COMPLETED)

        # Default: 200 empty
        return MockResponse(json_data={})


@pytest.fixture(autouse=True)
def mock_httpx(monkeypatch):
    """Replace httpx.AsyncClient globally with our mock."""
    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)


# ── Module registry ────────────────────────────────────────────────────

@pytest.fixture
def registered_modules():
    """Register all pipeline modules (clears registry first)."""
    import orchestrator.registry as reg
    from modules import register_all
    reg._registry.clear()
    register_all()
    return reg.all_modules()


# ── Session factory ────────────────────────────────────────────────────

@pytest.fixture
def session_factory(artifacts_dir):
    """Factory for creating test SessionContext objects."""
    from shared.types import (
        BPOPartner, EmailPayload, IntakeAnswers, SessionContext,
    )

    def make_session(
        from_address: str = "jarmstrong@resultscx.com",
        subject: str = "GameStop demo request",
        body: str = "Can you set up a demo for GameStop? The contact is Jane Smith, VP of CX.",
        deliverables: list[str] | None = None,
        bpo_key: str = "resultscx",
        target_company: str = "GameStop",
        target_url: str = "https://gamestop.com",
        contact_name: str = "Jane Smith",
        contact_title: str = "VP of Customer Experience",
        dry_run: bool = False,
        include_bpo: bool = True,
        include_intake: bool = True,
    ) -> SessionContext:
        email = EmailPayload(
            from_address=from_address,
            subject=subject,
            body=body,
            message_id=f"msg_test_{uuid.uuid4().hex[:8]}",
        )

        bpo = None
        if include_bpo:
            bpo = BPOPartner(
                key=bpo_key,
                name={"resultscx": "ResultsCX", "startek": "Startek", "esal": "eSAL", "cgs": "CGS", "cp360": "CP360"}.get(bpo_key, bpo_key),
                domains=[f"{bpo_key}.com"],
                drive_folder_id=f"folder_{bpo_key}_test",
                pipeline_sheet_id=f"sheet_{bpo_key}_test",
            )

        intake = None
        if include_intake:
            intake = IntakeAnswers(
                contact_name=contact_name,
                contact_title=contact_title,
                target_business_area="Customer Support",
                pain_points="Long hold times",
                current_setup="Genesys Cloud CX",
            )

        return SessionContext(
            session_id=f"sess_test_{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(timezone.utc),
            status="received",
            raw_email=email,
            bpo=bpo,
            target_company=target_company,
            target_url=target_url,
            deliverables_requested=deliverables or ["demo", "deep_research", "stakeholder_intel", "cx_intel", "pitch_deck"],
            intake=intake,
            dry_run=dry_run,
        )

    return make_session


# ── In-memory session store ────────────────────────────────────────────

@pytest.fixture
def in_memory_sessions(monkeypatch):
    """Replace orchestrator.session functions with an in-memory dict store."""
    from shared.types import EmailPayload, SessionContext

    store: dict[str, dict] = {}  # session_id -> {ctx_dict, status, ...}

    def _create_session(email: EmailPayload, dry_run: bool = False):
        sid = f"sess_test_{uuid.uuid4().hex[:8]}"
        ctx = SessionContext(
            session_id=sid,
            created_at=datetime.now(timezone.utc),
            raw_email=email,
            dry_run=dry_run,
        )
        store[sid] = {
            "ctx": ctx,
            "detail": {
                "session_id": sid,
                "status": "received",
                "bpo_key": None,
                "target_company": None,
                "created_at": ctx.created_at.isoformat(),
                "updated_at": ctx.created_at.isoformat(),
            },
        }
        return ctx

    def _save_session(ctx: SessionContext):
        if ctx.session_id in store:
            store[ctx.session_id]["ctx"] = ctx
            store[ctx.session_id]["detail"]["status"] = ctx.status
            store[ctx.session_id]["detail"]["bpo_key"] = ctx.bpo.key if ctx.bpo else None
            store[ctx.session_id]["detail"]["target_company"] = ctx.target_company

    def _load_session(session_id: str):
        entry = store.get(session_id)
        return entry["ctx"] if entry else None

    def _update_status(session_id: str, status: str, **extra):
        if session_id in store:
            store[session_id]["ctx"].status = status
            store[session_id]["detail"]["status"] = status
            for k, v in extra.items():
                store[session_id]["detail"][k] = v

    def _list_sessions(limit: int = 50, status: str | None = None):
        rows = list(store.values())
        if status:
            rows = [r for r in rows if r["detail"]["status"] == status]
        return [r["detail"] for r in rows[:limit]]

    def _get_session_detail(session_id: str):
        entry = store.get(session_id)
        if not entry:
            return None
        detail = dict(entry["detail"])
        ctx = entry["ctx"]
        detail["context"] = ctx.model_dump(mode="json", exclude={"all_artifacts"})
        return detail

    def _ensure_schema():
        pass

    monkeypatch.setattr("orchestrator.session.ensure_schema", _ensure_schema)
    monkeypatch.setattr("orchestrator.session.create_session", _create_session)
    monkeypatch.setattr("orchestrator.session.save_session", _save_session)
    monkeypatch.setattr("orchestrator.session.load_session", _load_session)
    monkeypatch.setattr("orchestrator.session.update_status", _update_status)
    monkeypatch.setattr("orchestrator.session.list_sessions", _list_sessions)
    monkeypatch.setattr("orchestrator.session.get_session_detail", _get_session_detail)

    # Also patch the imports in main.py
    monkeypatch.setattr("orchestrator.main.ensure_schema", _ensure_schema)
    monkeypatch.setattr("orchestrator.main.create_session", _create_session)
    monkeypatch.setattr("orchestrator.main.save_session", _save_session)
    monkeypatch.setattr("orchestrator.main.load_session", _load_session)
    monkeypatch.setattr("orchestrator.main.update_status", _update_status)
    monkeypatch.setattr("orchestrator.main.list_sessions", _list_sessions)
    monkeypatch.setattr("orchestrator.main.get_session_detail", _get_session_detail)

    return store


# ── Fixture loader ─────────────────────────────────────────────────────

@pytest.fixture
def load_fixture():
    """Load a JSON test fixture by name (without .json extension)."""
    fixtures_dir = Path(__file__).parent / "fixtures"

    def _load(name: str) -> dict:
        path = fixtures_dir / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return _load

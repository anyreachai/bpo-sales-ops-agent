"""Regression tests for session persistence (JSON serialization roundtrip).

Production uses Postgres JSONB for the ``context`` column. The serialized
SessionContext must be reconstructable via ``SessionContext(**data)`` — a
prior bug stored ``{}`` at create time, which caused load_session to crash
with two missing-required-field validation errors before any module ran.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from shared.types import (
    BPOPartner,
    EmailPayload,
    IntakeAnswers,
    ModuleResult,
    SessionContext,
)


def _make_email() -> EmailPayload:
    return EmailPayload(
        from_address="jarmstrong@resultscx.com",
        subject="Demo for GameStop",
        body="Please prepare a demo for GameStop.",
        message_id="<msg-1@resultscx.com>",
        cc=["someone@resultscx.com"],
    )


def _serialize_for_db(ctx: SessionContext) -> str:
    """Mirror what create_session/save_session write to the Postgres context column."""
    return json.dumps(ctx.model_dump(mode="json", exclude={"all_artifacts"}))


def _deserialize_from_db(blob: str) -> SessionContext:
    """Mirror what load_session does when reading the context column."""
    return SessionContext(**json.loads(blob))


def test_minimal_session_roundtrips():
    """A freshly-created session (no bpo, no target) must roundtrip through JSON."""
    ctx = SessionContext(
        session_id="sess_test_minimal",
        created_at=datetime.now(timezone.utc),
        raw_email=_make_email(),
        dry_run=True,
    )
    reloaded = _deserialize_from_db(_serialize_for_db(ctx))

    assert reloaded.session_id == ctx.session_id
    assert reloaded.raw_email.from_address == "jarmstrong@resultscx.com"
    assert reloaded.dry_run is True
    assert reloaded.bpo is None
    assert reloaded.target_company is None


def test_fully_populated_session_roundtrips():
    """A session with bpo + intake + module_results must roundtrip without losing fields."""
    ctx = SessionContext(
        session_id="sess_test_full",
        created_at=datetime.now(timezone.utc),
        raw_email=_make_email(),
        bpo=BPOPartner(
            key="resultscx",
            name="ResultsCX",
            domains=["resultscx.com"],
            drive_folder_id="folder_abc",
            pipeline_sheet_id="sheet_abc",
            attio_record_id="6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf",
        ),
        target_company="GameStop",
        target_url="https://gamestop.com",
        deliverables_requested=["demo", "deep_research"],
        intake=IntakeAnswers(contact_name="Jane Smith", contact_title="VP CX"),
        module_results={
            "classifier": ModuleResult(
                module_name="classifier",
                status="success",
                metadata={"bpo_key": "resultscx"},
            ),
        },
        drive_links={"folder": "https://drive.google.com/test"},
        demo_link="https://demo.example.com/x",
        dry_run=False,
    )
    reloaded = _deserialize_from_db(_serialize_for_db(ctx))

    assert reloaded.bpo is not None
    assert reloaded.bpo.key == "resultscx"
    assert reloaded.bpo.attio_record_id == "6cfc06d6-7ed2-4cf1-bdc1-b276dbd53acf"
    assert reloaded.target_company == "GameStop"
    assert reloaded.target_url == "https://gamestop.com"
    assert reloaded.intake is not None
    assert reloaded.intake.contact_name == "Jane Smith"
    assert "classifier" in reloaded.module_results
    assert reloaded.module_results["classifier"].status == "success"
    assert reloaded.drive_links["folder"] == "https://drive.google.com/test"
    assert reloaded.demo_link == "https://demo.example.com/x"


def test_empty_context_does_not_roundtrip():
    """Regression: storing '{}' (the prior bug) makes load_session fail.

    This codifies the failure mode so a future change can't silently re-introduce it.
    """
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _deserialize_from_db("{}")

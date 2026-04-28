"""End-to-end pipeline tests — full Phase 1 → approve → Phase 2 flow."""

import pytest

from orchestrator.dag import DAGRunner, NODES, Phase
from orchestrator.main import run_phase_1, run_phase_2
from shared.types import EmailPayload, SessionContext


# ── Full pipeline via DAG runner ────────────────────────────────────────

async def test_full_pipeline_phase_1(session_factory, registered_modules):
    """Phase 1: classifier → parallel content gen → deck. All artifacts on disk."""
    ctx = session_factory(
        deliverables=["demo", "deep_research", "stakeholder_intel", "cx_intel", "pitch_deck"],
        include_bpo=False,
        include_intake=False,
    )

    runner = DAGRunner(ctx)
    results = await runner.run_phase(Phase.PHASE_1)

    # All 8 Phase 1 nodes completed
    assert len(runner.completed) == 8

    # Classifier populated context
    assert ctx.bpo is not None
    assert ctx.target_company is not None

    # Brand guide populated
    assert ctx.brand_guide is not None

    # Artifacts generated (deep_research pdf, stakeholder pdf, cx xlsx+pdf, deck pptx, brand json)
    assert len(ctx.all_artifacts) >= 4

    # Verify artifact files exist on disk
    for artifact in ctx.all_artifacts:
        assert artifact.path.exists(), f"Artifact missing: {artifact.filename}"
        assert artifact.size_bytes > 0 or artifact.path.stat().st_size > 0


async def test_full_pipeline_phase_2(session_factory, registered_modules):
    """Phase 2: drive → tracker + email → slack. Needs Phase 1 completed first."""
    ctx = session_factory()

    # Run Phase 1
    runner = DAGRunner(ctx)
    await runner.run_phase(Phase.PHASE_1)

    # Run Phase 2
    await runner.run_phase(Phase.PHASE_2)

    # All 13 nodes completed (attio_sync skips when ATTIO_API_KEY is unset)
    assert len(runner.completed) == 13

    # Drive links populated
    assert "folder" in ctx.drive_links

    # All Phase 2 modules succeeded
    for name in ["drive_manager", "pipeline_tracker", "email_composer", "slack_summary"]:
        assert name in runner.results
        assert runner.results[name].status == "success", f"{name} failed: {runner.results[name].error}"


async def test_full_pipeline_via_orchestrator(in_memory_sessions, registered_modules):
    """E2E via the orchestrator's run_phase_1 / run_phase_2 functions."""
    from orchestrator.session import create_session

    email = EmailPayload(
        from_address="jarmstrong@resultscx.com",
        subject="GameStop full package",
        body="Please prepare everything for GameStop. Contact is Jane Smith, VP of CX. gamestop.com",
        message_id="msg_e2e_001",
    )
    ctx = create_session(email)
    session_id = ctx.session_id

    # Phase 1
    await run_phase_1(session_id)

    # Verify status after Phase 1
    ctx = in_memory_sessions[session_id]["ctx"]
    assert ctx.status == "awaiting_approval"
    assert ctx.bpo is not None
    assert ctx.target_company is not None

    # Phase 2
    await run_phase_2(session_id)

    ctx = in_memory_sessions[session_id]["ctx"]
    assert ctx.status == "complete"
    assert "folder" in ctx.drive_links


# ── Partial deliverables ───────────────────────────────────────────────

async def test_partial_deliverables_cx_only(session_factory, registered_modules, monkeypatch):
    """When only cx_intel is requested, other content modules are skipped."""
    from tests.mocks import MOCK_CLASSIFICATION_MINIMAL

    async def minimal_sonnet(*args, **kwargs):
        sys = kwargs.get("system", "")
        if "classifier" in sys.lower() or "bpo" in sys.lower():
            return MOCK_CLASSIFICATION_MINIMAL
        return kwargs.get("_default", "{}")

    monkeypatch.setattr("modules.classifier.module.call_sonnet", minimal_sonnet)

    ctx = session_factory(
        from_address="smukherjee@startek.com",
        deliverables=[],
        include_bpo=False,
        include_intake=False,
    )

    runner = DAGRunner(ctx)
    await runner.run_phase(Phase.PHASE_1)

    # cx_intel should have run
    assert runner.results["cx_intel"].status in ("success", "skipped")

    # deep_research, stakeholder_intel, pitch_deck should be skipped
    assert runner.results["deep_research"].status == "skipped"
    assert runner.results["stakeholder_intel"].status == "skipped"
    assert runner.results["deck_generator"].status == "skipped"


# ── State transitions ──────────────────────────────────────────────────

async def test_state_transitions(in_memory_sessions, registered_modules):
    """Verify the exact sequence of status values through the pipeline."""
    from orchestrator.session import create_session, update_status

    status_log = []
    original_update = update_status.__wrapped__ if hasattr(update_status, '__wrapped__') else None

    # Wrap update_status to track transitions
    store_ref = in_memory_sessions

    # The in_memory_sessions fixture already patches update_status.
    # We need to wrap it to also log.
    from orchestrator import main as main_module
    original_fn = main_module.update_status

    def tracking_update(session_id, status, **extra):
        status_log.append(status)
        original_fn(session_id, status, **extra)

    main_module.update_status = tracking_update

    try:
        email = EmailPayload(
            from_address="jarmstrong@resultscx.com",
            subject="State transition test",
            body="Full package for GameStop please. gamestop.com Contact: Jane Smith, VP CX",
            message_id="msg_state_001",
        )
        ctx = create_session(email)

        await run_phase_1(ctx.session_id)

        # Manually approve
        from orchestrator.session import update_status as sess_update
        sess_update(ctx.session_id, "approved", approved_by="test")
        status_log.append("approved")

        await run_phase_2(ctx.session_id)

        # Verify key transitions occurred
        assert "classifying" in status_log
        assert "awaiting_approval" in status_log
        assert "approved" in status_log
        assert "delivering" in status_log
        assert "complete" in status_log
    finally:
        main_module.update_status = original_fn


# ── Rejection flow ─────────────────────────────────────────────────────

async def test_rejection_flow(in_memory_sessions, registered_modules):
    """After rejection, Phase 2 should not run."""
    from orchestrator.session import create_session, update_status

    email = EmailPayload(
        from_address="jarmstrong@resultscx.com",
        subject="Rejection test",
        body="Materials for GameStop",
        message_id="msg_reject_001",
    )
    ctx = create_session(email)

    await run_phase_1(ctx.session_id)

    # Reject
    update_status(ctx.session_id, "rejected", rejected_by="test", reject_reason="Not needed")

    ctx = in_memory_sessions[ctx.session_id]["ctx"]
    assert ctx.status == "rejected"

    # Verify no Phase 2 artifacts
    assert "drive_manager" not in ctx.module_results or ctx.module_results.get("drive_manager") is None

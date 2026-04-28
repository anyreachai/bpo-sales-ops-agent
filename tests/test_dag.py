"""Tests for the DAG runner — execution order, dependency resolution, error handling."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from orchestrator.dag import DAGRunner, NODES, Phase
from shared.types import ModuleResult


# ── Node structure tests ────────────────────────────────────────────────

def test_phase_1_has_8_nodes():
    phase_1 = {k for k, v in NODES.items() if v["phase"] == Phase.PHASE_1}
    assert len(phase_1) == 8
    assert "classifier" in phase_1
    assert "openai_research" in phase_1
    assert "deck_generator" in phase_1

def test_phase_2_has_5_nodes():
    phase_2 = {k for k, v in NODES.items() if v["phase"] == Phase.PHASE_2}
    assert len(phase_2) == 5
    assert "drive_manager" in phase_2
    assert "slack_summary" in phase_2
    assert "attio_sync" in phase_2

def test_classifier_has_no_deps():
    assert NODES["classifier"]["deps"] == []

def test_deck_generator_deps():
    deps = set(NODES["deck_generator"]["deps"])
    assert deps == {"brand_extractor", "deep_research", "stakeholder_intel", "cx_intel"}

def test_slack_summary_deps():
    deps = set(NODES["slack_summary"]["deps"])
    assert deps == {"pipeline_tracker", "email_composer"}


# ── Phase 1 execution ──────────────────────────────────────────────────

async def test_phase_1_runs_all_nodes(session_factory, registered_modules):
    ctx = session_factory()
    runner = DAGRunner(ctx)
    results = await runner.run_phase(Phase.PHASE_1)
    assert len(runner.completed) == 8
    for name in ["classifier", "brand_extractor", "demo_generator", "deep_research",
                  "openai_research", "stakeholder_intel", "cx_intel", "deck_generator"]:
        assert name in runner.completed

async def test_phase_1_classifier_runs_first(session_factory, registered_modules):
    """Verify classifier completes before any other module starts."""
    execution_order = []

    original_run_node = DAGRunner._run_node

    async def tracked_run_node(self, name):
        execution_order.append(name)
        await original_run_node(self, name)

    DAGRunner._run_node = tracked_run_node
    try:
        ctx = session_factory()
        runner = DAGRunner(ctx)
        await runner.run_phase(Phase.PHASE_1)
        assert execution_order[0] == "classifier"
    finally:
        DAGRunner._run_node = original_run_node

async def test_phase_1_deck_generator_runs_last(session_factory, registered_modules):
    """deck_generator depends on 4 modules so must run in the last batch."""
    execution_order = []

    original_run_node = DAGRunner._run_node

    async def tracked_run_node(self, name):
        execution_order.append(name)
        await original_run_node(self, name)

    DAGRunner._run_node = tracked_run_node
    try:
        ctx = session_factory()
        runner = DAGRunner(ctx)
        await runner.run_phase(Phase.PHASE_1)
        assert execution_order[-1] == "deck_generator"
    finally:
        DAGRunner._run_node = original_run_node


# ── Phase 2 execution ──────────────────────────────────────────────────

async def test_phase_2_runs_all_nodes(session_factory, registered_modules):
    """Phase 2 with pre-populated Phase 1 results."""
    ctx = session_factory()
    runner = DAGRunner(ctx)
    # Run Phase 1 first
    await runner.run_phase(Phase.PHASE_1)
    # Then Phase 2
    results = await runner.run_phase(Phase.PHASE_2)
    phase_2_names = {k for k, v in NODES.items() if v["phase"] == Phase.PHASE_2}
    for name in phase_2_names:
        assert name in runner.completed


# ── Error handling ──────────────────────────────────────────────────────

async def test_failed_module_doesnt_block_siblings(session_factory, registered_modules, monkeypatch):
    """If deep_research fails, other parallel modules should still complete."""
    from modules.deep_research.module import DeepResearchModule

    original_run = DeepResearchModule.run

    async def failing_run(self, ctx):
        raise RuntimeError("Simulated deep_research failure")

    monkeypatch.setattr(DeepResearchModule, "run", failing_run)

    ctx = session_factory()
    runner = DAGRunner(ctx)
    await runner.run_phase(Phase.PHASE_1)

    # deep_research should be marked as failed (BaseModule.execute catches exceptions)
    assert "deep_research" in runner.completed
    assert runner.results["deep_research"].status == "failed"

    # Other parallel modules should still succeed
    assert runner.results["brand_extractor"].status == "success"
    assert runner.results["cx_intel"].status == "success"


# ── Context accumulation ───────────────────────────────────────────────

async def test_context_accumulation(session_factory, registered_modules):
    ctx = session_factory(include_bpo=False, include_intake=False, deliverables=[])
    runner = DAGRunner(ctx)
    await runner.run_phase(Phase.PHASE_1)

    # Classifier should have populated bpo
    assert ctx.bpo is not None
    # Brand extractor should have populated brand_guide
    assert ctx.brand_guide is not None


# ── Skipped modules ───────────────────────────────────────────────────

async def test_skipped_modules_demo_only(session_factory, registered_modules, monkeypatch):
    """When only 'demo' is requested, most modules should be skipped."""
    # Override classifier mock to return only "demo" deliverable
    from tests.mocks import MOCK_CLASSIFICATION_MINIMAL
    async def minimal_sonnet(*args, **kwargs):
        sys = kwargs.get("system", "")
        if "classifier" in sys.lower() or "bpo" in sys.lower():
            return MOCK_CLASSIFICATION_MINIMAL
        return "{}"
    monkeypatch.setattr("modules.classifier.module.call_sonnet", minimal_sonnet)

    ctx = session_factory(deliverables=["demo"], include_bpo=False, include_intake=False)
    runner = DAGRunner(ctx)
    await runner.run_phase(Phase.PHASE_1)

    # deep_research, stakeholder_intel, cx_intel, deck_generator should be skipped
    assert runner.results["deep_research"].status == "skipped"
    assert runner.results["deck_generator"].status == "skipped"

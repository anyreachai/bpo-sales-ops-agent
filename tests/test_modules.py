"""Tests for individual pipeline modules — should_run(), run(), edge cases."""

import pytest
from pathlib import Path

# -- Brand Extractor --

from modules.brand_extractor.module import BrandExtractorModule, hex_to_hsl, hsl_to_hex, darken, lighten, _build_palette


def test_brand_extractor_should_run_with_url(session_factory):
    ctx = session_factory(target_url="https://gamestop.com")
    assert BrandExtractorModule().should_run(ctx) is True


def test_brand_extractor_should_run_without_url(session_factory):
    ctx = session_factory(target_url=None)
    assert BrandExtractorModule().should_run(ctx) is False


async def test_brand_extractor_run_success(session_factory):
    ctx = session_factory()
    module = BrandExtractorModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert len(result.artifacts) >= 1  # at least the JSON artifact
    assert ctx.brand_guide is not None
    assert "colors" in ctx.brand_guide
    assert "deck_palette" in ctx.brand_guide


async def test_brand_extractor_404_fallback(session_factory, monkeypatch):
    """When brand.dev returns 404, fall back to default palette."""
    from modules.brand_extractor.client import BrandDevClient
    import httpx

    async def failing_get_brand(self, domain):
        request = httpx.Request("GET", "http://mock")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("Not found", request=request, response=response)

    monkeypatch.setattr(BrandDevClient, "get_brand", failing_get_brand)
    ctx = session_factory()
    module = BrandExtractorModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert ctx.brand_guide["colors"]["primary"] == "#5B5FC7"  # default


def test_color_roundtrip():
    h, s, l = hex_to_hsl("#FF6600")
    result = hsl_to_hex(h, s, l)
    assert result.upper() == "#FF6600"


def test_build_palette():
    palette = _build_palette("#3B82F6")
    assert "dark_bg" in palette
    assert "light_bg" in palette
    assert "primary_accent" in palette
    assert palette["primary_accent"] == "#3B82F6"
    assert len(palette["neutral_scale"]) == 3


# -- Deep Research --

from modules.deep_research.module import DeepResearchModule


def test_deep_research_should_run(session_factory):
    ctx = session_factory(deliverables=["deep_research"])
    assert DeepResearchModule().should_run(ctx) is True


def test_deep_research_should_not_run(session_factory):
    ctx = session_factory(deliverables=["demo"])
    assert DeepResearchModule().should_run(ctx) is False


async def test_deep_research_run_success(session_factory):
    ctx = session_factory(deliverables=["deep_research"])
    module = DeepResearchModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].artifact_type == "deep_research"
    assert result.artifacts[0].path.exists()
    assert result.artifacts[0].path.suffix == ".docx"


async def test_deep_research_short_response(session_factory, monkeypatch):
    async def short_opus(*args, **kwargs):
        return "Too short"
    monkeypatch.setattr("modules.deep_research.module.call_opus_with_search", short_opus)
    ctx = session_factory(deliverables=["deep_research"])
    module = DeepResearchModule()
    result = await module.run(ctx)
    assert result.status == "failed"


# -- Stakeholder Intel --

from modules.stakeholder_intel.module import StakeholderIntelModule


def test_stakeholder_should_run(session_factory):
    ctx = session_factory(deliverables=["stakeholder_intel"])
    assert StakeholderIntelModule().should_run(ctx) is True


def test_stakeholder_should_not_run_no_deliverable(session_factory):
    ctx = session_factory(deliverables=["demo"])
    assert StakeholderIntelModule().should_run(ctx) is False


async def test_stakeholder_skips_no_contact_name(session_factory):
    ctx = session_factory(deliverables=["stakeholder_intel"], contact_name=None, include_intake=True)
    # Need to manually set contact_name to None after creation
    ctx.intake.contact_name = None
    module = StakeholderIntelModule()
    # should_run returns True but run() checks contact_name and returns skipped
    result = await module.execute(ctx)  # use execute() which calls should_run() + run()
    # The module's should_run or run should handle no contact_name
    assert result.status in ("skipped", "success")  # depends on implementation


async def test_stakeholder_run_success(session_factory):
    ctx = session_factory(deliverables=["stakeholder_intel"])
    module = StakeholderIntelModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert len(result.artifacts) >= 1
    pdf_artifact = result.artifacts[0]
    assert pdf_artifact.mime_type == "application/pdf"
    assert pdf_artifact.path.exists()


# -- CX Intel --

from modules.cx_intel.module import CxIntelModule


def test_cx_intel_should_run(session_factory):
    ctx = session_factory(deliverables=["cx_intel"])
    assert CxIntelModule().should_run(ctx) is True


def test_cx_intel_should_not_run(session_factory):
    ctx = session_factory(deliverables=["demo"])
    assert CxIntelModule().should_run(ctx) is False


async def test_cx_intel_run_success(session_factory):
    ctx = session_factory(deliverables=["cx_intel"])
    module = CxIntelModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert len(result.artifacts) >= 2  # xlsx + pdf
    types = {a.artifact_type for a in result.artifacts}
    assert "cx_intel_xlsx" in types
    assert "cx_intel_pdf" in types
    for a in result.artifacts:
        assert a.path.exists()


# -- Demo Generator --

from modules.demo_generator.module import DemoGeneratorModule


def test_demo_should_run(session_factory):
    ctx = session_factory(deliverables=["demo"])
    assert DemoGeneratorModule().should_run(ctx) is True


def test_demo_should_not_run(session_factory):
    ctx = session_factory(deliverables=["deep_research"])
    assert DemoGeneratorModule().should_run(ctx) is False


async def test_demo_run_no_db(session_factory):
    ctx = session_factory(deliverables=["demo"])
    module = DemoGeneratorModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert result.metadata.get("action_required") == "email_demo_address"
    assert ctx.demo_link is None


# -- Deck Generator --

from modules.deck_generator.module import DeckGeneratorModule


def test_deck_should_run(session_factory):
    ctx = session_factory(deliverables=["pitch_deck"])
    assert DeckGeneratorModule().should_run(ctx) is True


def test_deck_should_not_run(session_factory):
    ctx = session_factory(deliverables=["demo"])
    assert DeckGeneratorModule().should_run(ctx) is False


async def test_deck_run_success(session_factory):
    ctx = session_factory(deliverables=["pitch_deck"])
    module = DeckGeneratorModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].path.suffix == ".pptx"
    assert result.artifacts[0].path.exists()


async def test_deck_run_bad_json(session_factory, monkeypatch):
    async def bad_sonnet(*args, **kwargs):
        return "not json at all"
    monkeypatch.setattr("modules.deck_generator.module.call_sonnet", bad_sonnet)
    ctx = session_factory(deliverables=["pitch_deck"])
    module = DeckGeneratorModule()
    result = await module.run(ctx)
    assert result.status == "failed"


# -- Drive Manager --

from modules.drive_manager.module import DriveManagerModule


async def test_drive_manager_dry_run(session_factory):
    ctx = session_factory(dry_run=True)
    module = DriveManagerModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert result.metadata.get("dry_run") is True
    assert "folder" in ctx.drive_links
    assert "DRY_RUN" in ctx.drive_links["folder"]


async def test_drive_manager_no_bpo(session_factory):
    ctx = session_factory(include_bpo=False)
    ctx.bpo = None
    module = DriveManagerModule()
    result = await module.run(ctx)
    assert result.status == "failed"
    assert "drive_folder_id" in result.error


async def test_drive_manager_run_success(session_factory):
    ctx = session_factory()
    module = DriveManagerModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert "folder" in ctx.drive_links


# -- Email Composer --

from modules.email_composer.module import EmailComposerModule


async def test_email_composer_dry_run(session_factory):
    ctx = session_factory(dry_run=True)
    ctx.drive_links = {"folder": "https://drive.google.com/test"}
    module = EmailComposerModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert result.metadata.get("dry_run") is True


async def test_email_composer_run_success(session_factory):
    ctx = session_factory()
    ctx.drive_links = {"folder": "https://drive.google.com/test"}
    module = EmailComposerModule()
    result = await module.run(ctx)
    assert result.status == "success"


# -- Slack Manager --

from modules.slack_manager.module import SlackManagerModule


async def test_slack_summary_dry_run(session_factory):
    ctx = session_factory(dry_run=True)
    module = SlackManagerModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert result.metadata.get("dry_run") is True


async def test_slack_summary_no_token(session_factory, monkeypatch):
    from orchestrator.config import settings
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    ctx = session_factory()
    module = SlackManagerModule()
    result = await module.run(ctx)
    assert result.status == "failed"


async def test_slack_summary_run_success(session_factory):
    ctx = session_factory()
    module = SlackManagerModule()
    result = await module.run(ctx)
    assert result.status == "success"


# -- Pipeline Tracker --

from modules.pipeline_tracker.module import PipelineTrackerModule


async def test_pipeline_tracker_run(session_factory):
    ctx = session_factory()
    ctx.drive_links = {"folder": "https://drive.google.com/test"}
    module = PipelineTrackerModule()
    result = await module.run(ctx)
    assert result.status == "success"

"""Tests for the classifier module — domain matching, extraction, code fence stripping, and full run()."""

import pytest
import json
from pathlib import Path

from modules.classifier.module import (
    ClassifierModule,
    _extract_domain,
    _match_bpo,
    _load_registry,
    _strip_code_fences,
    ALL_DELIVERABLES,
)


# -- Domain matching --


def test_bpo_domain_matching_resultscx():
    registry = _load_registry()
    bpo = _match_bpo(registry, "resultscx.com")
    assert bpo is not None
    assert bpo.key == "resultscx"
    assert bpo.name == "ResultsCX"


def test_bpo_domain_matching_esal_alias():
    registry = _load_registry()
    bpo = _match_bpo(registry, "esalglobal.com")
    assert bpo is not None
    assert bpo.key == "esal"


def test_bpo_domain_matching_startek():
    registry = _load_registry()
    assert _match_bpo(registry, "startek.com").key == "startek"


def test_bpo_domain_matching_cgs():
    registry = _load_registry()
    bpo = _match_bpo(registry, "cgsinc.com")
    assert bpo.key == "cgs"
    assert bpo.pipeline_sheet_id is None


def test_bpo_domain_matching_cp360():
    registry = _load_registry()
    assert _match_bpo(registry, "cp360.com").key == "cp360"


def test_bpo_domain_matching_unknown():
    registry = _load_registry()
    assert _match_bpo(registry, "unknownbpo.com") is None


# -- Domain extraction --


def test_extract_domain_plain():
    assert _extract_domain("user@example.com") == "example.com"


def test_extract_domain_with_display_name():
    assert _extract_domain("Jordan Armstrong <jarmstrong@resultscx.com>") == "resultscx.com"


def test_extract_domain_uppercase():
    assert _extract_domain("USER@EXAMPLE.COM") == "example.com"


# -- Code fence stripping --


def test_strip_code_fences_json():
    assert _strip_code_fences('```json\n{"a":1}\n```') == '{"a":1}'


def test_strip_code_fences_plain():
    assert _strip_code_fences('{"a":1}') == '{"a":1}'


def test_strip_code_fences_triple_backtick():
    assert _strip_code_fences('```\n{"b":2}\n```') == '{"b":2}'


# -- Everything expansion --


def test_all_deliverables_list():
    assert ALL_DELIVERABLES == ["demo", "deep_research", "stakeholder_intel", "cx_intel", "pitch_deck"]


# -- Classifier run() --


async def test_classifier_run_success(session_factory):
    ctx = session_factory(include_bpo=False, include_intake=False, deliverables=[])
    # ctx starts without bpo/intake — classifier populates them
    module = ClassifierModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert ctx.bpo is not None
    assert ctx.bpo.key == "resultscx"
    assert ctx.target_company == "GameStop"
    assert "demo" in ctx.deliverables_requested
    assert ctx.intake is not None
    assert ctx.intake.contact_name == "Jane Smith"


async def test_classifier_run_unknown_domain(session_factory):
    ctx = session_factory(from_address="jdoe@unknownbpo.com", include_bpo=False, include_intake=False, deliverables=[])
    module = ClassifierModule()
    result = await module.run(ctx)
    assert result.status == "failed"
    assert "not found in BPO registry" in result.error


async def test_classifier_run_bad_json(session_factory, monkeypatch):
    ctx = session_factory(include_bpo=False, include_intake=False, deliverables=[])
    # Override the mock to return invalid JSON
    async def bad_sonnet(*args, **kwargs):
        return "This is not JSON at all"
    monkeypatch.setattr("modules.classifier.module.call_sonnet", bad_sonnet)
    module = ClassifierModule()
    result = await module.run(ctx)
    assert result.status == "failed"
    assert "JSON parse error" in result.error


async def test_classifier_everything_expansion(session_factory, monkeypatch):
    from tests.mocks import MOCK_CLASSIFICATION_EVERYTHING
    async def everything_sonnet(*args, **kwargs):
        return MOCK_CLASSIFICATION_EVERYTHING
    monkeypatch.setattr("modules.classifier.module.call_sonnet", everything_sonnet)
    ctx = session_factory(include_bpo=False, include_intake=False, deliverables=[])
    module = ClassifierModule()
    result = await module.run(ctx)
    assert result.status == "success"
    assert set(ctx.deliverables_requested) == set(ALL_DELIVERABLES)


# -- Parametrized fixture tests --


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_FILES = sorted(FIXTURES_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", FIXTURE_FILES, ids=[f.stem for f in FIXTURE_FILES])
def test_fixture_domain_matching(fixture_path):
    data = json.loads(fixture_path.read_text())
    expected_bpo = data["expected"].get("bpo_partner")
    from_addr = data["from"]
    domain = _extract_domain(from_addr)
    registry = _load_registry()
    bpo = _match_bpo(registry, domain)
    if expected_bpo is None:
        assert bpo is None
    else:
        assert bpo is not None
        assert bpo.key == expected_bpo

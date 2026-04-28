from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from modules._base import BaseModule
from modules.classifier.prompts import CLASSIFY_SYSTEM, build_classify_prompt
from orchestrator.config import settings
from shared.anthropic_client import call_sonnet
from shared.types import BPOPartner, IntakeAnswers, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent.parent / "config" / "bpo_registry.json"

ALL_DELIVERABLES = [
    "demo",
    "deep_research",
    "stakeholder_intel",
    "cx_intel",
    "pitch_deck",
]


def _load_registry() -> dict:
    """Load the BPO partner registry from disk."""
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def _extract_domain(email_address: str) -> str:
    """Extract the domain part from an email address."""
    # Handle "Name <email>" format
    match = re.search(r"<([^>]+)>", email_address)
    addr = match.group(1) if match else email_address
    return addr.strip().split("@")[-1].lower()


def _match_bpo(registry: dict, sender_domain: str) -> BPOPartner | None:
    """Match a sender domain against the BPO registry and return a BPOPartner."""
    for key, entry in registry.items():
        domains = [d.lower() for d in entry.get("email_domains", [])]
        if sender_domain in domains:
            return BPOPartner(
                key=key,
                name=entry["name"],
                domains=entry["email_domains"],
                drive_folder_id=entry["drive_folder_id"],
                pipeline_sheet_id=entry.get("pipeline_sheet_id"),
                key_contacts=entry.get("key_contacts", []),
                slack_channel=entry.get("slack_channel"),
                attio_record_id=entry.get("attio_record_id"),
            )
    return None


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) if present."""
    stripped = text.strip()
    # Match ```json ... ``` or ``` ... ```
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


class ClassifierModule(BaseModule):
    name = "classifier"

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        # 1. Load BPO registry and match sender
        registry = _load_registry()
        sender_domain = _extract_domain(ctx.raw_email.from_address)
        bpo = _match_bpo(registry, sender_domain)

        if bpo is None:
            logger.warning(f"Unknown sender domain: {sender_domain}")
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"Sender domain '{sender_domain}' not found in BPO registry",
                metadata={"sender_domain": sender_domain},
            )

        logger.info(f"Matched BPO partner: {bpo.name} (key={bpo.key})")

        # 2. Call Claude to classify the email
        prompt = build_classify_prompt(ctx.raw_email)
        raw_response = await call_sonnet(
            api_key=settings.ANTHROPIC_API_KEY,
            prompt=prompt,
            system=CLASSIFY_SYSTEM,
            max_tokens=2048,
        )

        # 3. Parse JSON response
        cleaned = _strip_code_fences(raw_response)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse classifier response: {e}\nRaw: {raw_response}")
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"JSON parse error: {e}",
                metadata={"raw_response": raw_response, "sender_domain": sender_domain},
            )

        # 4. Expand "everything" deliverable
        deliverables = parsed.get("deliverables", [])
        if "everything" in deliverables:
            deliverables = list(ALL_DELIVERABLES)

        # 5. Build intake answers
        intake = IntakeAnswers(
            contact_name=parsed.get("contact_name"),
            contact_title=parsed.get("contact_title"),
            target_business_area=parsed.get("business_area"),
            pain_points=parsed.get("pain_points"),
            current_setup=parsed.get("current_setup"),
        )

        # 6. Update session context
        ctx.bpo = bpo
        ctx.target_company = parsed.get("target_company")
        ctx.target_url = parsed.get("target_url")
        ctx.deliverables_requested = deliverables
        ctx.intake = intake

        # 7. Build metadata for the result
        metadata = {
            "sender_domain": sender_domain,
            "bpo_key": bpo.key,
            "bpo_name": bpo.name,
            "target_company": ctx.target_company,
            "target_url": ctx.target_url,
            "deliverables": deliverables,
            "contact_name": parsed.get("contact_name"),
            "contact_title": parsed.get("contact_title"),
            "business_area": parsed.get("business_area"),
            "pain_points": parsed.get("pain_points"),
            "current_setup": parsed.get("current_setup"),
            "intake_complete": parsed.get("intake_complete", False),
            "confidence": parsed.get("confidence", "low"),
            "notes": parsed.get("notes"),
        }

        logger.info(
            f"Classification complete: {ctx.target_company} | "
            f"deliverables={deliverables} | confidence={metadata['confidence']}"
        )

        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata=metadata,
        )

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from modules._base import BaseModule
from modules.report_templates import render_pdf
from modules.stakeholder_intel.prompts import STAKEHOLDER_SYSTEM, build_stakeholder_prompt
from orchestrator.config import settings
from shared.anthropic_client import call_opus_with_search
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)


def _parse_sections(content_text: str) -> list[dict]:
    """Split LLM markdown output into [{heading, body}] dicts.

    Handles ## Heading lines as section delimiters. Text before the first
    heading is captured under "Overview".
    """
    sections: list[dict] = []
    current_heading = "Overview"
    current_lines: list[str] = []

    for line in content_text.split("\n"):
        heading_match = re.match(r"^##\s+(.+)$", line.strip())
        if heading_match:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append({"heading": current_heading, "body": body})
            current_heading = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body:
        sections.append({"heading": current_heading, "body": body})

    return sections


class StakeholderIntelModule(BaseModule):
    name = "stakeholder_intel"

    def should_run(self, ctx: SessionContext) -> bool:
        return "stakeholder_intel" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        if not ctx.intake or not ctx.intake.contact_name:
            logger.info("stakeholder_intel requested but no contact_name — skipping")
            return ModuleResult(
                module_name=self.name,
                status="skipped",
                metadata={"reason": "no_contact_name_provided"},
            )

        contact_name = ctx.intake.contact_name
        contact_title = ctx.intake.contact_title
        company_name = ctx.target_company
        company_url = ctx.target_url

        bpo_context = ""
        if ctx.bpo:
            bpo_context = (
                f"This research is being prepared for {ctx.bpo.name}, "
                f"a BPO partner exploring an opportunity with {company_name or 'the target company'}."
            )

        prompt = build_stakeholder_prompt(
            contact_name=contact_name,
            contact_title=contact_title,
            company_name=company_name,
            company_url=company_url,
            bpo_context=bpo_context,
        )

        logger.info(
            "Researching stakeholder: %s (%s) at %s",
            contact_name,
            contact_title or "unknown title",
            company_name or "unknown company",
        )

        raw_response = await call_opus_with_search(
            api_key=settings.ANTHROPIC_API_KEY,
            prompt=prompt,
            system=STAKEHOLDER_SYSTEM,
            max_tokens=16000,
        )

        if not raw_response or len(raw_response.strip()) < 100:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="LLM returned an insufficient response",
                metadata={"response_length": len(raw_response) if raw_response else 0},
            )

        from slugify import slugify

        person_slug = slugify(contact_name, max_length=40)
        pdf_path = artifact_path(
            session_id=ctx.session_id,
            company=company_name or contact_name,
            suffix=f"{person_slug}_Stakeholder_Intel",
            ext="pdf",
        )

        sections = _parse_sections(raw_response)

        render_pdf(
            template_name="stakeholder_intel.html",
            context={
                "contact_name": contact_name,
                "contact_title": contact_title,
                "company_name": company_name or "Unknown",
                "sections": sections,
                "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
            },
            output_path=pdf_path,
            brand_guide=ctx.brand_guide,
        )

        artifact = Artifact(
            filename=pdf_path.name,
            path=pdf_path,
            artifact_type="stakeholder_intel",
            mime_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
        )

        logger.info(
            "Stakeholder intel complete for %s — PDF at %s (%d bytes)",
            contact_name,
            pdf_path,
            artifact.size_bytes,
        )

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=[artifact],
            metadata={
                "contact_name": contact_name,
                "contact_title": contact_title,
                "company_name": company_name,
                "pdf_path": str(pdf_path),
                "pdf_size_bytes": artifact.size_bytes,
                "response_length": len(raw_response),
                "sections_found": [s["heading"] for s in sections],
            },
        )

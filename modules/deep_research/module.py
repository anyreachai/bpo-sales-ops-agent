from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from modules._base import BaseModule
from modules.deep_research.prompts import build_research_prompt
from modules.report_templates import render_pdf
from orchestrator.config import settings
from shared.anthropic_client import call_opus_with_search
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)


def _parse_sections(markdown_text: str) -> list[dict]:
    """Split markdown into [{heading, body}] dicts by ## headings."""
    sections: list[dict] = []
    current_heading = "Overview"
    current_lines: list[str] = []

    for line in markdown_text.split("\n"):
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append({"heading": current_heading, "body": body})
            current_heading = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body:
        sections.append({"heading": current_heading, "body": body})

    return sections


class DeepResearchModule(BaseModule):
    name = "deep_research"

    def should_run(self, ctx: SessionContext) -> bool:
        return "deep_research" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "Unknown Company"
        logger.info("Starting deep research for %s", company)

        intake_dict = None
        if ctx.intake:
            intake_dict = {
                "contact_name": ctx.intake.contact_name,
                "contact_title": ctx.intake.contact_title,
                "target_business_area": ctx.intake.target_business_area,
                "pain_points": ctx.intake.pain_points,
                "current_setup": ctx.intake.current_setup,
            }

        bpo_context = ""
        if ctx.bpo:
            bpo_context = (
                f"Requesting BPO partner: {ctx.bpo.name} "
                f"(key contacts: {', '.join(ctx.bpo.key_contacts) if ctx.bpo.key_contacts else 'N/A'})"
            )

        prompt = build_research_prompt(
            company_name=company,
            company_url=ctx.target_url,
            intake=intake_dict,
            bpo_context=bpo_context,
        )

        system = (
            "You are a corporate intelligence analyst with deep expertise in BPO, "
            "contact center operations, and customer experience outsourcing. "
            "Produce thorough, evidence-backed research reports. Use web search "
            "extensively to gather current information. Be candid and analytical."
        )

        logger.info("Calling Claude Opus with web search for %s", company)
        markdown_response = await call_opus_with_search(
            api_key=settings.ANTHROPIC_API_KEY,
            prompt=prompt,
            system=system,
            max_tokens=16000,
        )

        if not markdown_response or len(markdown_response.strip()) < 200:
            logger.error("Research response too short or empty for %s", company)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="Research response was too short or empty",
                metadata={"response_length": len(markdown_response) if markdown_response else 0},
            )

        logger.info(
            "Received research response for %s (%d chars)",
            company,
            len(markdown_response),
        )

        pdf_path = artifact_path(
            session_id=ctx.session_id,
            company=company,
            suffix="Deep_Research",
            ext="pdf",
        )

        try:
            sections = _parse_sections(markdown_response)
            render_pdf(
                template_name="deep_research.html",
                context={
                    "company_name": company,
                    "sections": sections,
                    "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
                },
                output_path=pdf_path,
                brand_guide=ctx.brand_guide,
            )
        except Exception as e:
            logger.exception("Failed to generate PDF for %s", company)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"PDF generation failed: {e}",
                metadata={"response_length": len(markdown_response)},
            )

        file_size = pdf_path.stat().st_size
        logger.info("Generated %s (%d bytes)", pdf_path.name, file_size)

        artifact = Artifact(
            filename=pdf_path.name,
            path=pdf_path,
            artifact_type="deep_research",
            mime_type="application/pdf",
            size_bytes=file_size,
        )

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=[artifact],
            metadata={
                "company": company,
                "url": ctx.target_url,
                "response_length": len(markdown_response),
                "pdf_size_bytes": file_size,
                "pdf_path": str(pdf_path),
            },
        )

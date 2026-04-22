"""Deck Generator module — produces a branded .pptx pitch deck from pipeline context."""
from __future__ import annotations

import json
import logging
import re

from modules._base import BaseModule
from modules.deck_generator.templates import DEFAULT_PALETTE, TITLE_FONT, BODY_FONT, create_presentation
from orchestrator.config import settings
from shared.anthropic_client import call_sonnet
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt for Claude Sonnet — generates structured slide JSON
# ---------------------------------------------------------------------------

DECK_SYSTEM = """\
You are a pitch-deck content strategist for Anyreach.ai, an AI-powered voice \
and CX automation platform. You write concise, high-impact slide content \
targeted at enterprise BPO decision-makers. Output ONLY valid JSON — no \
markdown fences, no commentary."""

SLIDE_SCHEMA_HINT = """\
Return a JSON array of exactly 10 slide objects. Each object must have:
  - "slide_number": int (1-10)
  - "type": one of "title", "content", "stats_grid", "comparison", "quote", "cta"
  - "heading": string (short slide title)

Type-specific fields:
  title  -> "title", "subtitle", "prepared_for"
  content -> "heading", "subheading" (optional), "bullets" (list of strings, 3-6 items)
  stats_grid -> "heading", "stats" (list of {{"value": "XX%", "label": "..."}}, 3-4 items)
  comparison -> "heading", "left": {{"title": str, "bullets": [...]}}, "right": {{"title": str, "bullets": [...]}}
  quote -> "heading", "quote" (customer/analyst quote), "attribution" (source)
  cta -> "heading", "bullets" (3-4 next-step items), "contact" (string)

Slide plan (follow this order exactly):
  1. title — "{company_name}: Transforming CX with AI"
  2. content — "The CX Challenge" (prospect pain points, be specific)
  3. content — "Market Context" (industry trends with data)
  4. content — "Voice of the Customer" (themes from review data)
  5. content — "The Anyreach Solution" (capabilities matched to pain points)
  6. comparison — "Current State vs. Anyreach" (before/after contrast)
  7. stats_grid — "Results & Proof Points" (concrete metrics, 3-4 stats)
  8. content — "Implementation Approach" (timeline & methodology)
  9. content — "Why Anyreach + {bpo_name}" (partnership value)
  10. cta — "Next Steps" (clear CTA with contact info)
"""


def _build_context_string(ctx: SessionContext) -> str:
    """Collect upstream module outputs into a single context string for the prompt."""
    sections: list[str] = []

    # Deep research summary
    dr = ctx.module_results.get("deep_research")
    if dr and dr.status == "success":
        summary = dr.metadata.get("summary", dr.metadata.get("report_preview", ""))
        if summary:
            sections.append(f"## Deep Research\n{summary}")

    # Stakeholder intel
    si = ctx.module_results.get("stakeholder_intel")
    if si and si.status == "success":
        stakeholders = si.metadata.get("stakeholders", [])
        if stakeholders:
            lines = []
            for s in stakeholders[:8]:
                name = s.get("name", "Unknown")
                title = s.get("title", "")
                lines.append(f"- {name}, {title}")
            sections.append(f"## Key Stakeholders\n" + "\n".join(lines))

    # CX Intel themes
    cx = ctx.module_results.get("cx_intel")
    if cx and cx.status == "success":
        themes = cx.metadata.get("themes", [])
        sentiment = cx.metadata.get("overall_sentiment", "")
        review_count = cx.metadata.get("review_count", 0)
        cx_text = ""
        if sentiment:
            cx_text += f"Overall sentiment: {sentiment}\n"
        if review_count:
            cx_text += f"Reviews analyzed: {review_count}\n"
        if themes:
            cx_text += "Key themes:\n" + "\n".join(f"- {t}" for t in themes[:6])
        if cx_text:
            sections.append(f"## CX Intelligence\n{cx_text}")

    # Intake answers
    if ctx.intake:
        intake_parts = []
        if ctx.intake.pain_points:
            intake_parts.append(f"Pain points: {ctx.intake.pain_points}")
        if ctx.intake.current_setup:
            intake_parts.append(f"Current setup: {ctx.intake.current_setup}")
        if ctx.intake.target_business_area:
            intake_parts.append(f"Business area: {ctx.intake.target_business_area}")
        if ctx.intake.contact_name:
            intake_parts.append(f"Contact: {ctx.intake.contact_name} ({ctx.intake.contact_title or 'N/A'})")
        if intake_parts:
            sections.append(f"## Intake Info\n" + "\n".join(intake_parts))

    return "\n\n".join(sections) if sections else "No upstream module data available."


def _build_prompt(ctx: SessionContext) -> str:
    """Assemble the full prompt for Claude Sonnet."""
    company = ctx.target_company or "the prospect"
    bpo = ctx.bpo.name if ctx.bpo else "BPO Partner"
    url = ctx.target_url or "(no URL provided)"
    context_str = _build_context_string(ctx)

    return f"""\
Generate pitch deck content for a sales presentation.

Target company: {company}
Target URL: {url}
BPO partner: {bpo}

--- RESEARCH & INTEL CONTEXT ---
{context_str}
--- END CONTEXT ---

{SLIDE_SCHEMA_HINT.replace("{company_name}", company).replace("{bpo_name}", bpo)}

Make the content specific to {company}. Reference their actual pain points, \
industry, and CX challenges from the research context above. Keep bullet \
points concise (under 15 words each). Use concrete metrics where available."""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) if present."""
    stripped = text.strip()
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _resolve_palette(ctx: SessionContext) -> dict:
    """Return the deck palette from brand_guide if available, else default."""
    if ctx.brand_guide and isinstance(ctx.brand_guide, dict):
        custom_palette = ctx.brand_guide.get("deck_palette")
        if custom_palette and isinstance(custom_palette, dict):
            merged = dict(DEFAULT_PALETTE)
            merged.update(custom_palette)
            # Normalize neutral_scale → neutral for backward compat
            if "neutral_scale" in merged and "neutral" not in merged:
                merged["neutral"] = merged.pop("neutral_scale")
            return merged
    return dict(DEFAULT_PALETTE)


def _resolve_fonts(ctx: SessionContext) -> dict:
    """Extract font names from brand_guide, or return defaults."""
    if ctx.brand_guide and isinstance(ctx.brand_guide, dict):
        fonts = ctx.brand_guide.get("fonts", {})
        return {
            "heading": fonts.get("heading") or TITLE_FONT,
            "body": fonts.get("body") or BODY_FONT,
        }
    return {"heading": TITLE_FONT, "body": BODY_FONT}


class DeckGeneratorModule(BaseModule):
    name = "deck_generator"

    def should_run(self, ctx: SessionContext) -> bool:
        return "pitch_deck" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "prospect"
        bpo_name = ctx.bpo.name if ctx.bpo else "BPO Partner"

        # 1. Build prompt and call Claude Sonnet for slide content
        prompt = _build_prompt(ctx)
        logger.info("Generating deck content for %s via Claude Sonnet", company)
        raw_response = await call_sonnet(
            api_key=settings.ANTHROPIC_API_KEY,
            prompt=prompt,
            system=DECK_SYSTEM,
            max_tokens=4096,
        )

        # 2. Parse JSON response
        cleaned = _strip_code_fences(raw_response)
        try:
            slides_data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse deck JSON: %s\nRaw: %s", e, raw_response[:500])
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"Slide content JSON parse error: {e}",
                metadata={"raw_response_preview": raw_response[:500]},
            )

        if not isinstance(slides_data, list):
            logger.error("Expected JSON array of slides, got %s", type(slides_data).__name__)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="Slide content was not a JSON array",
                metadata={"raw_response_preview": raw_response[:500]},
            )

        # 3. Resolve palette and fonts
        palette = _resolve_palette(ctx)
        fonts = _resolve_fonts(ctx)

        # 4. Determine logo path (if brand_guide provides one)
        logo_path = None
        if ctx.brand_guide and isinstance(ctx.brand_guide, dict):
            logo_path = ctx.brand_guide.get("logo_path")

        # 5. Generate .pptx
        logger.info("Building .pptx with %d slides", len(slides_data))
        prs = create_presentation(
            slides_data=slides_data,
            palette=palette,
            company_name=company,
            bpo_name=bpo_name,
            logo_path=logo_path,
            fonts=fonts,
        )

        # 6. Save to disk
        out_path = artifact_path(ctx.session_id, company, "pitch_deck", "pptx")
        prs.save(str(out_path))
        size = out_path.stat().st_size

        artifact = Artifact(
            filename=out_path.name,
            path=out_path,
            artifact_type="pitch_deck",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            size_bytes=size,
        )

        logger.info("Pitch deck saved: %s (%d bytes)", out_path, size)

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=[artifact],
            metadata={
                "slide_count": len(slides_data),
                "palette_source": "brand_guide" if ctx.brand_guide and ctx.brand_guide.get("deck_palette") else "default",
                "company": company,
                "bpo": bpo_name,
            },
        )

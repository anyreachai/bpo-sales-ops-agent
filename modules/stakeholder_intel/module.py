from __future__ import annotations

import logging
import re
from pathlib import Path

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)

from modules._base import BaseModule
from modules.stakeholder_intel.prompts import STAKEHOLDER_SYSTEM, build_stakeholder_prompt
from orchestrator.config import settings
from shared.anthropic_client import call_opus_with_search
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
NAVY = HexColor("#161631")
INDIGO = HexColor("#5B5FC7")
LIGHT_BG = HexColor("#F4F4FB")
DARK_CALLOUT = HexColor("#1E1E3F")
MUTED_TEXT = HexColor("#8888AA")
WHITE = white
BLACK = black

# ---------------------------------------------------------------------------
# Section headings we expect in the LLM response
# ---------------------------------------------------------------------------
EXPECTED_SECTIONS = [
    "Career Arc",
    "What They Control",
    "LinkedIn Intelligence",
    "Network & Orbit",
    "Company Context & Timing",
    "Psychological Profile",
    "Tactical Playbook",
    "Conclusion",
]


# ===================================================================
# PDF generation
# ===================================================================

def _build_styles() -> dict[str, ParagraphStyle]:
    """Create the branded paragraph styles used throughout the PDF."""
    base = getSampleStyleSheet()

    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=28,
            leading=34,
            textColor=WHITE,
            alignment=TA_CENTER,
            spaceAfter=16,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=16,
            leading=22,
            textColor=HexColor("#CCCCEE"),
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            textColor=MUTED_TEXT,
            alignment=TA_CENTER,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=22,
            textColor=INDIGO,
            spaceBefore=20,
            spaceAfter=10,
            borderWidth=0,
            borderPadding=0,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=BLACK,
            alignment=TA_JUSTIFY,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=BLACK,
            leftIndent=18,
            bulletIndent=6,
            spaceAfter=3,
        ),
        "sub_bullet": ParagraphStyle(
            "sub_bullet",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=HexColor("#333333"),
            leftIndent=36,
            bulletIndent=24,
            spaceAfter=2,
        ),
        "bold_body": ParagraphStyle(
            "bold_body",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=BLACK,
            spaceAfter=4,
        ),
        "callout": ParagraphStyle(
            "callout",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=HexColor("#DDDDEE"),
            backColor=DARK_CALLOUT,
            borderPadding=(8, 10, 8, 10),
            spaceAfter=10,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=MUTED_TEXT,
            alignment=TA_CENTER,
        ),
    }


def _draw_cover_background(canvas, doc):
    """Draw the dark navy cover page background."""
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
    # Subtle accent line
    canvas.setStrokeColor(INDIGO)
    canvas.setLineWidth(3)
    canvas.line(
        letter[0] * 0.2, letter[1] * 0.42,
        letter[0] * 0.8, letter[1] * 0.42,
    )
    canvas.restoreState()


def _draw_page_footer(canvas, doc):
    """Draw branded footer on content pages."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED_TEXT)
    canvas.drawCentredString(
        letter[0] / 2,
        0.5 * inch,
        f"Stakeholder Intelligence Brief  |  Anyreach.ai  |  Confidential  |  Page {doc.page}",
    )
    # Top accent bar
    canvas.setFillColor(INDIGO)
    canvas.rect(0, letter[1] - 4, letter[0], 4, fill=1, stroke=0)
    canvas.restoreState()


def _build_cover(
    contact_name: str,
    contact_title: str | None,
    company_name: str | None,
    styles: dict,
) -> list:
    """Build the cover page flowables."""
    flowables: list = []
    flowables.append(Spacer(1, 2.2 * inch))
    flowables.append(Paragraph("STAKEHOLDER INTELLIGENCE BRIEF", styles["cover_title"]))
    flowables.append(Spacer(1, 0.3 * inch))
    flowables.append(Paragraph(contact_name, styles["cover_subtitle"]))
    if contact_title:
        flowables.append(Paragraph(contact_title, styles["cover_meta"]))
    if company_name:
        flowables.append(Spacer(1, 4))
        flowables.append(Paragraph(company_name, styles["cover_meta"]))
    flowables.append(Spacer(1, 1.2 * inch))
    flowables.append(
        Paragraph(
            "Prepared by Anyreach Sales Intelligence",
            styles["cover_meta"],
        )
    )
    flowables.append(Paragraph("Confidential", styles["cover_meta"]))
    flowables.append(PageBreak())
    return flowables


def _md_inline(text: str) -> str:
    """Convert inline markdown bold (**text**) to reportlab <b> tags.

    Also escapes any bare ampersands and angle brackets that would break XML parsing.
    """
    # Escape XML-unsafe characters first
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    # Now convert **bold** markers
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def _parse_sections(content_text: str) -> list[tuple[str, str]]:
    """Split the LLM markdown output into (heading, body) pairs.

    Handles ## Heading lines as section delimiters.  Any text before the first
    heading is captured under "Overview".
    """
    sections: list[tuple[str, str]] = []
    current_heading = "Overview"
    current_lines: list[str] = []

    for line in content_text.split("\n"):
        heading_match = re.match(r"^##\s+(.+)$", line.strip())
        if heading_match:
            # Save previous section
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_heading, body))
            current_heading = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Final section
    body = "\n".join(current_lines).strip()
    if body:
        sections.append((current_heading, body))

    return sections


def _section_to_flowables(heading: str, body: str, styles: dict) -> list:
    """Convert a single section (heading + markdown body) into reportlab flowables."""
    flowables: list = []

    # Section heading
    flowables.append(Paragraph(_md_inline(heading), styles["section_heading"]))

    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            flowables.append(Spacer(1, 4))
            continue

        # Sub-bullet:  - text  or  * text  (indented)
        sub_bullet_match = re.match(r"^\s{2,}[-*]\s+(.+)$", line)
        if sub_bullet_match:
            text = _md_inline(sub_bullet_match.group(1))
            flowables.append(
                Paragraph(f"\u2022 {text}", styles["sub_bullet"])
            )
            continue

        # Top-level bullet
        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            text = _md_inline(bullet_match.group(1))
            flowables.append(
                Paragraph(f"\u2022 {text}", styles["bullet"])
            )
            continue

        # Numbered list
        numbered_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if numbered_match:
            text = _md_inline(numbered_match.group(1))
            flowables.append(
                Paragraph(f"\u2022 {text}", styles["bullet"])
            )
            continue

        # Regular paragraph
        flowables.append(Paragraph(_md_inline(stripped), styles["body"]))

    return flowables


def _build_summary_table(
    contact_name: str,
    contact_title: str | None,
    company_name: str | None,
    styles: dict,
) -> list:
    """Build a summary info table at the top of the content pages."""
    data = [
        ["Contact", contact_name or "N/A"],
        ["Title", contact_title or "N/A"],
        ["Company", company_name or "N/A"],
    ]

    table = Table(data, colWidths=[1.5 * inch, 4.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                ("TEXTCOLOR", (0, 0), (0, -1), INDIGO),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, HexColor("#DDDDEE")),
                ("LINEBELOW", (0, -1), (-1, -1), 1, INDIGO),
            ]
        )
    )

    return [table, Spacer(1, 0.3 * inch)]


def generate_stakeholder_pdf(
    content_text: str,
    output_path: Path | str,
    contact_name: str,
    contact_title: str | None = None,
    company_name: str | None = None,
) -> Path:
    """Generate a branded stakeholder intelligence PDF from the LLM markdown output.

    Args:
        content_text: Raw markdown-formatted text from the LLM.
        output_path: Destination file path for the PDF.
        contact_name: Name of the researched contact.
        contact_title: Title / role of the contact.
        company_name: Company the contact works for.

    Returns:
        The Path to the written PDF file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title=f"Stakeholder Intel — {contact_name}",
        author="Anyreach Sales Intelligence",
    )

    # -- Build flowable list --
    story: list = []

    # Cover page
    story.extend(_build_cover(contact_name, contact_title, company_name, styles))

    # Summary table
    story.extend(_build_summary_table(contact_name, contact_title, company_name, styles))

    # Parse and render sections
    sections = _parse_sections(content_text)

    for heading, body in sections:
        # Skip the "Overview" catch-all if it's trivially empty
        if heading == "Overview" and len(body.strip()) < 10:
            continue
        section_flowables = _section_to_flowables(heading, body, styles)
        story.extend(section_flowables)
        story.append(Spacer(1, 8))

    # Build the PDF with custom page templates
    def cover_template(canvas, doc):
        _draw_cover_background(canvas, doc)

    def content_template(canvas, doc):
        _draw_page_footer(canvas, doc)

    doc.build(
        story,
        onFirstPage=cover_template,
        onLaterPages=content_template,
    )

    logger.info("Stakeholder PDF written to %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


# ===================================================================
# Module
# ===================================================================


class StakeholderIntelModule(BaseModule):
    name = "stakeholder_intel"

    def should_run(self, ctx: SessionContext) -> bool:
        return "stakeholder_intel" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        # Guard: need a contact name to research
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

        # Build BPO context string for the prompt
        bpo_context = ""
        if ctx.bpo:
            bpo_context = (
                f"This research is being prepared for {ctx.bpo.name}, "
                f"a BPO partner exploring an opportunity with {company_name or 'the target company'}."
            )

        # 1. Build the research prompt
        prompt = build_stakeholder_prompt(
            contact_name=contact_name,
            contact_title=contact_title,
            company_name=company_name,
            company_url=company_url,
            bpo_context=bpo_context,
        )

        # 2. Call Claude Opus with web search
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
            thinking_budget=10000,
        )

        if not raw_response or len(raw_response.strip()) < 100:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="LLM returned an insufficient response",
                metadata={"response_length": len(raw_response) if raw_response else 0},
            )

        # 3. Generate the branded PDF
        from slugify import slugify

        person_slug = slugify(contact_name, max_length=40)
        pdf_path = artifact_path(
            session_id=ctx.session_id,
            company=company_name or contact_name,
            suffix=f"{person_slug}_Stakeholder_Intel",
            ext="pdf",
        )

        generate_stakeholder_pdf(
            content_text=raw_response,
            output_path=pdf_path,
            contact_name=contact_name,
            contact_title=contact_title,
            company_name=company_name,
        )

        # 4. Build the artifact record
        artifact = Artifact(
            filename=pdf_path.name,
            path=pdf_path,
            artifact_type="stakeholder_intel",
            mime_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
        )

        ctx.all_artifacts.append(artifact)

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
                "sections_found": [
                    h for h, _ in _parse_sections(raw_response)
                ],
            },
        )

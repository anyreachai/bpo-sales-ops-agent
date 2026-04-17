"""Generate a branded CX Intelligence PDF report from scraped review data."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ── Default brand colours ──────────────────────────────────────────────
NAVY = colors.HexColor("#161631")
DARK_BLUE = colors.HexColor("#1F3864")
MED_BLUE = colors.HexColor("#2F5496")
LIGHT_BLUE = colors.HexColor("#D6E4F0")
ACCENT_BLUE = colors.HexColor("#4472C4")
WHITE = colors.white
POSITIVE_GREEN = colors.HexColor("#006100")
MIXED_ORANGE = colors.HexColor("#9C5700")
NEGATIVE_RED = colors.HexColor("#9C0006")
POSITIVE_BG = colors.HexColor("#C6EFCE")
MIXED_BG = colors.HexColor("#FFEB9C")
NEGATIVE_BG = colors.HexColor("#FFC7CE")
LIGHT_GRAY = colors.HexColor("#F2F2F2")


def _sentiment_color(sentiment: str) -> colors.Color:
    s = (sentiment or "").lower()
    if s == "positive":
        return POSITIVE_GREEN
    if s == "negative":
        return NEGATIVE_RED
    return MIXED_ORANGE


def _sentiment_bg(sentiment: str) -> colors.Color:
    s = (sentiment or "").lower()
    if s == "positive":
        return POSITIVE_BG
    if s == "negative":
        return NEGATIVE_BG
    return MIXED_BG


def _get_accent(brand_guide: dict | None) -> colors.Color:
    """Extract an accent colour from the brand guide if available."""
    if brand_guide:
        brand_colors = brand_guide.get("colors", {})
        primary = brand_colors.get("primary")
        if primary and isinstance(primary, str) and primary.startswith("#"):
            try:
                return colors.HexColor(primary)
            except Exception:
                pass
    return ACCENT_BLUE


# ======================================================================
# Styles
# ======================================================================

def _build_styles(accent: colors.Color):
    base = getSampleStyleSheet()

    styles = {
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=28,
            textColor=WHITE,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=14,
            textColor=colors.HexColor("#B0B0CC"),
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "section_title": ParagraphStyle(
            "section_title",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=DARK_BLUE,
            spaceBefore=20,
            spaceAfter=10,
        ),
        "subsection": ParagraphStyle(
            "subsection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=MED_BLUE,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=colors.black,
            leading=14,
            spaceAfter=6,
        ),
        "body_bold": ParagraphStyle(
            "body_bold",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=colors.black,
            leading=14,
            spaceAfter=6,
        ),
        "quote": ParagraphStyle(
            "quote",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=9,
            textColor=colors.HexColor("#444444"),
            leftIndent=20,
            rightIndent=20,
            leading=13,
            spaceAfter=8,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=colors.gray,
            alignment=TA_RIGHT,
        ),
        "stat_big": ParagraphStyle(
            "stat_big",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=24,
            textColor=accent,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "stat_label": ParagraphStyle(
            "stat_label",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.gray,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
    }
    return styles


# ======================================================================
# Section builders
# ======================================================================

def _build_cover(story: list, company_name: str, styles: dict) -> None:
    """Full-page dark navy cover."""
    # We use a table with a navy background to simulate a cover page
    cover_content = [
        [Paragraph("CX Intelligence Report", styles["cover_title"])],
        [Paragraph(company_name, styles["cover_title"])],
        [Spacer(1, 20)],
        [Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y')}", styles["cover_subtitle"])],
        [Paragraph("Confidential", styles["cover_subtitle"])],
    ]
    cover_table = Table(cover_content, colWidths=[6.5 * inch])
    cover_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (0, 0), 180),
            ("BOTTOMPADDING", (0, -1), (0, -1), 180),
        ])
    )
    story.append(cover_table)
    story.append(PageBreak())


def _build_exec_summary(story: list, data: dict, styles: dict, accent: colors.Color) -> None:
    """Executive summary with key stats."""
    story.append(Paragraph("Executive Summary", styles["section_title"]))

    summary = data.get("summary", "No summary available.")
    story.append(Paragraph(summary, styles["body"]))
    story.append(Spacer(1, 12))

    # Stat cards row
    overall = data.get("overall_rating", "N/A")
    total = data.get("total_reviews_found", 0)
    dist = data.get("sentiment_distribution", {})
    pos_pct = 0
    total_sent = sum(dist.values()) if dist else 0
    if total_sent > 0:
        pos_pct = round(dist.get("positive", 0) / total_sent * 100)

    stat_data = [
        [
            Paragraph(str(overall), styles["stat_big"]),
            Paragraph(str(total), styles["stat_big"]),
            Paragraph(f"{pos_pct}%", styles["stat_big"]),
        ],
        [
            Paragraph("Overall Rating", styles["stat_label"]),
            Paragraph("Total Reviews", styles["stat_label"]),
            Paragraph("Positive Sentiment", styles["stat_label"]),
        ],
    ]
    stat_table = Table(stat_data, colWidths=[2.1 * inch, 2.1 * inch, 2.1 * inch])
    stat_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 1, LIGHT_BLUE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, LIGHT_BLUE),
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
            ("TOPPADDING", (0, 0), (-1, 0), 14),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ])
    )
    story.append(stat_table)
    story.append(Spacer(1, 16))


def _build_platform_ratings(story: list, data: dict, styles: dict) -> None:
    """Platform ratings overview table."""
    ratings = data.get("ratings_summary", {})
    if not ratings:
        return

    story.append(Paragraph("Platform Ratings", styles["section_title"]))

    table_data = [["Platform", "Rating"]]
    for platform, rating in ratings.items():
        table_data.append([platform, str(rating)])

    table = Table(table_data, colWidths=[3.5 * inch, 2.0 * inch])
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("BACKGROUND", (0, 1), (-1, -1), LIGHT_GRAY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
            ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_BLUE),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(table)
    story.append(Spacer(1, 16))


def _build_theme_analysis(story: list, data: dict, styles: dict) -> None:
    """Recurring themes with frequency and sentiment."""
    themes = data.get("themes", [])
    if not themes:
        return

    story.append(Paragraph("Theme Analysis", styles["section_title"]))
    story.append(Paragraph(
        "Recurring patterns identified across all review platforms:",
        styles["body"],
    ))

    table_data = [["Theme", "Frequency", "Sentiment", "Platforms"]]
    for t in themes:
        table_data.append([
            t.get("theme", ""),
            t.get("frequency", "").title(),
            t.get("sentiment", "mixed").title(),
            ", ".join(t.get("platforms", [])),
        ])

    table = Table(table_data, colWidths=[2.0 * inch, 1.1 * inch, 1.1 * inch, 2.3 * inch])

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_BLUE),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
    ]

    # Colour sentiment cells
    for i, t in enumerate(themes, 1):
        bg = _sentiment_bg(t.get("sentiment", "mixed"))
        fg = _sentiment_color(t.get("sentiment", "mixed"))
        style_commands.append(("BACKGROUND", (2, i), (2, i), bg))
        style_commands.append(("TEXTCOLOR", (2, i), (2, i), fg))

    table.setStyle(TableStyle(style_commands))
    story.append(table)
    story.append(Spacer(1, 16))


def _build_review_highlights(story: list, data: dict, styles: dict) -> None:
    """Selected review quotes."""
    reviews = data.get("reviews", [])
    if not reviews:
        return

    story.append(Paragraph("Review Highlights", styles["section_title"]))

    # Pick up to 8 diverse reviews
    selected = reviews[:8]
    for rev in selected:
        platform = rev.get("platform", "Unknown")
        rating = rev.get("rating", "")
        sentiment = rev.get("sentiment", "mixed")
        text = rev.get("text", "")
        date = rev.get("date", "")

        color = _sentiment_color(sentiment)
        header_text = (
            f'<font color="{color.hexval()}">[{sentiment.title()}]</font> '
            f'<b>{platform}</b> — {rating}/5'
        )
        if date:
            header_text += f" ({date})"

        story.append(Paragraph(header_text, styles["body"]))
        story.append(Paragraph(f'"{text}"', styles["quote"]))

    story.append(Spacer(1, 12))


def _build_employee_sentiment(story: list, data: dict, styles: dict) -> None:
    """Employee review summary."""
    emp_reviews = data.get("employee_reviews", [])
    if not emp_reviews:
        return

    story.append(Paragraph("Employee Sentiment", styles["section_title"]))

    table_data = [["Platform", "Rating", "Title", "Pros", "Cons"]]
    for er in emp_reviews[:8]:
        table_data.append([
            er.get("platform", ""),
            str(er.get("rating", "")),
            er.get("title", ""),
            er.get("pros", ""),
            er.get("cons", ""),
        ])

    col_widths = [1.0 * inch, 0.6 * inch, 1.5 * inch, 1.7 * inch, 1.7 * inch]
    table = Table(table_data, colWidths=col_widths)

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_BLUE),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
    ]
    table.setStyle(TableStyle(style_commands))
    story.append(table)
    story.append(Spacer(1, 16))


def _build_recommendations(story: list, data: dict, styles: dict) -> None:
    """Actionable recommendations based on review themes."""
    themes = data.get("themes", [])
    story.append(Paragraph("Recommendations", styles["section_title"]))

    if not themes:
        story.append(Paragraph(
            "Insufficient review data to generate specific recommendations. "
            "Consider broadening the search or checking alternate company names.",
            styles["body"],
        ))
        return

    story.append(Paragraph(
        "Based on the CX intelligence gathered, the following areas present "
        "opportunities for AI-powered automation and improvement:",
        styles["body"],
    ))
    story.append(Spacer(1, 6))

    negative_themes = [t for t in themes if t.get("sentiment") == "negative"]
    mixed_themes = [t for t in themes if t.get("sentiment") == "mixed"]
    priority_themes = negative_themes + mixed_themes

    if not priority_themes:
        priority_themes = themes[:5]

    for i, t in enumerate(priority_themes[:6], 1):
        theme_name = t.get("theme", "")
        freq = t.get("frequency", "medium")
        story.append(Paragraph(
            f"<b>{i}. {theme_name}</b> (frequency: {freq})",
            styles["body_bold"],
        ))
        story.append(Paragraph(
            f"This theme was identified across {', '.join(t.get('platforms', []))}. "
            f"Addressing this through targeted automation or process improvement could "
            f"significantly impact customer satisfaction scores.",
            styles["body"],
        ))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<i>Report generated by BPO Sales Ops Pipeline</i>",
        styles["small"],
    ))


# ======================================================================
# Public entry point
# ======================================================================

def generate_cx_pdf(
    data: dict,
    company_name: str,
    output_path: Path | str,
    brand_guide: dict | None = None,
) -> Path:
    """Build a CX Intelligence PDF and save it to *output_path*."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    accent = _get_accent(brand_guide)
    styles = _build_styles(accent)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"CX Intelligence Report — {company_name}",
        author="BPO Sales Ops Pipeline",
    )

    story: list = []

    _build_cover(story, company_name, styles)
    _build_exec_summary(story, data, styles, accent)
    _build_platform_ratings(story, data, styles)
    _build_theme_analysis(story, data, styles)
    _build_review_highlights(story, data, styles)
    _build_employee_sentiment(story, data, styles)
    _build_recommendations(story, data, styles)

    doc.build(story)
    logger.info("CX PDF saved to %s", output_path)
    return output_path

"""Design system constants and slide builder functions for pitch deck generation."""
from __future__ import annotations

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# ---------------------------------------------------------------------------
# Default palette (used when no brand_guide.deck_palette is provided)
# ---------------------------------------------------------------------------

DEFAULT_PALETTE = {
    "dark_bg": "#1A1A2E",
    "light_bg": "#F5F6FA",
    "primary_accent": "#5B5FC7",
    "secondary_accent": "#818CF8",
    "neutral": ["#6B7280", "#9CA3AF", "#D1D5DB"],
}

# ---------------------------------------------------------------------------
# Typography constants
# ---------------------------------------------------------------------------

TITLE_FONT = "Georgia"
BODY_FONT = "Calibri"

TITLE_SIZE = Pt(36)
SUBTITLE_SIZE = Pt(20)
HEADING_SIZE = Pt(28)
SUBHEADING_SIZE = Pt(18)
BODY_SIZE = Pt(14)
BULLET_SIZE = Pt(13)
STAT_NUMBER_SIZE = Pt(40)
STAT_LABEL_SIZE = Pt(12)
FOOTER_SIZE = Pt(9)

# ---------------------------------------------------------------------------
# Layout constants (16:9 widescreen at 13.333 x 7.5 in)
# ---------------------------------------------------------------------------

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

ACCENT_BAR_HEIGHT = Inches(0.06)
CONTENT_LEFT = Inches(0.9)
CONTENT_TOP = Inches(1.3)
CONTENT_WIDTH = Inches(11.5)
CONTENT_HEIGHT = Inches(5.2)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_str: str) -> RGBColor:
    """Convert '#RRGGBB' to an RGBColor."""
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _set_slide_bg(slide, hex_color: str) -> None:
    """Set the background color of a slide."""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = _hex_to_rgb(hex_color)


def _add_accent_bar(slide, palette: dict) -> None:
    """Add a thin accent-colored rectangle at the top of the slide."""
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(0),
        Inches(0),
        SLIDE_W,
        ACCENT_BAR_HEIGHT,
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _hex_to_rgb(palette["primary_accent"])
    shape.line.fill.background()


def _add_text_box(
    slide,
    left: Inches,
    top: Inches,
    width: Inches,
    height: Inches,
    text: str,
    font_name: str = BODY_FONT,
    font_size: Pt = BODY_SIZE,
    font_color: str = "#333333",
    bold: bool = False,
    alignment: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    """Add a simple single-run text box."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.name = font_name
    p.font.size = font_size
    p.font.color.rgb = _hex_to_rgb(font_color)
    p.font.bold = bold
    p.alignment = alignment


def _add_bullets(
    slide,
    left,
    top,
    width,
    height,
    items: list[str],
    font_color: str = "#333333",
    font_size: Pt = BULLET_SIZE,
) -> None:
    """Add a text box with bullet points."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True

    for i, item in enumerate(items):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = item
        p.font.name = BODY_FONT
        p.font.size = font_size
        p.font.color.rgb = _hex_to_rgb(font_color)
        p.space_after = Pt(8)
        # Bullet indentation
        p.level = 0
        pPr = p._pPr
        if pPr is None:
            from pptx.oxml.ns import qn
            pPr = p._p.get_or_add_pPr()
        # Add bullet character
        from pptx.oxml.ns import qn
        buChar = pPr.makeelement(qn("a:buChar"), {"char": "\u2022"})
        # Remove any existing bullet elements first
        for existing in pPr.findall(qn("a:buChar")):
            pPr.remove(existing)
        for existing in pPr.findall(qn("a:buNone")):
            pPr.remove(existing)
        pPr.append(buChar)


def _add_footer(slide, palette: dict, text: str = "Confidential | Anyreach.ai") -> None:
    """Add a small footer at the bottom-right of the slide."""
    neutral = palette.get("neutral", DEFAULT_PALETTE["neutral"])
    color = neutral[0] if isinstance(neutral, list) and neutral else "#6B7280"
    _add_text_box(
        slide,
        left=Inches(9.5),
        top=Inches(7.05),
        width=Inches(3.5),
        height=Inches(0.35),
        text=text,
        font_size=FOOTER_SIZE,
        font_color=color,
        alignment=PP_ALIGN.RIGHT,
    )


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------


def add_title_slide(
    prs: Presentation,
    slide_data: dict,
    palette: dict,
    logo_path: str | None = None,
) -> None:
    """Slide 1 — dark background, prospect-branded title."""
    slide_layout = prs.slide_layouts[6]  # Blank layout
    slide = prs.slides.add_slide(slide_layout)

    _set_slide_bg(slide, palette["dark_bg"])
    _add_accent_bar(slide, palette)

    # Title
    _add_text_box(
        slide,
        left=Inches(1.2),
        top=Inches(2.0),
        width=Inches(10.5),
        height=Inches(1.5),
        text=slide_data.get("title", ""),
        font_name=TITLE_FONT,
        font_size=Pt(44),
        font_color="#FFFFFF",
        bold=True,
        alignment=PP_ALIGN.LEFT,
    )

    # Subtitle
    subtitle = slide_data.get("subtitle", "")
    if subtitle:
        _add_text_box(
            slide,
            left=Inches(1.2),
            top=Inches(3.6),
            width=Inches(10.5),
            height=Inches(1.0),
            text=subtitle,
            font_name=BODY_FONT,
            font_size=SUBTITLE_SIZE,
            font_color=palette.get("secondary_accent", "#818CF8"),
            alignment=PP_ALIGN.LEFT,
        )

    # Prepared-for line
    prepared_for = slide_data.get("prepared_for", "")
    if prepared_for:
        _add_text_box(
            slide,
            left=Inches(1.2),
            top=Inches(5.0),
            width=Inches(10.5),
            height=Inches(0.6),
            text=prepared_for,
            font_name=BODY_FONT,
            font_size=BODY_SIZE,
            font_color="#9CA3AF",
            alignment=PP_ALIGN.LEFT,
        )

    # Logo placeholder
    if logo_path:
        try:
            slide.shapes.add_picture(logo_path, Inches(10.5), Inches(5.8), Inches(2.0), Inches(1.0))
        except Exception:
            pass  # gracefully skip if logo file is invalid

    _add_footer(slide, palette)


def add_content_slide(prs: Presentation, slide_data: dict, palette: dict) -> None:
    """Standard content slide — heading + bullet points on light background."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    _set_slide_bg(slide, palette["light_bg"])
    _add_accent_bar(slide, palette)

    # Heading
    heading = slide_data.get("heading", "")
    _add_text_box(
        slide,
        left=CONTENT_LEFT,
        top=Inches(0.6),
        width=CONTENT_WIDTH,
        height=Inches(0.7),
        text=heading,
        font_name=TITLE_FONT,
        font_size=HEADING_SIZE,
        font_color=palette["dark_bg"],
        bold=True,
    )

    # Subheading (optional)
    subheading = slide_data.get("subheading", "")
    body_top = CONTENT_TOP
    if subheading:
        _add_text_box(
            slide,
            left=CONTENT_LEFT,
            top=Inches(1.1),
            width=CONTENT_WIDTH,
            height=Inches(0.5),
            text=subheading,
            font_name=BODY_FONT,
            font_size=SUBHEADING_SIZE,
            font_color=palette.get("primary_accent", "#5B5FC7"),
        )
        body_top = Inches(1.7)

    # Bullet points
    bullets = slide_data.get("bullets", [])
    if bullets:
        _add_bullets(
            slide,
            left=CONTENT_LEFT,
            top=body_top,
            width=CONTENT_WIDTH,
            height=CONTENT_HEIGHT,
            items=bullets,
            font_color="#333333",
        )

    # Body text (fallback if no bullets)
    body = slide_data.get("body", "")
    if body and not bullets:
        _add_text_box(
            slide,
            left=CONTENT_LEFT,
            top=body_top,
            width=CONTENT_WIDTH,
            height=CONTENT_HEIGHT,
            text=body,
            font_color="#444444",
        )

    _add_footer(slide, palette)


def add_stats_slide(prs: Presentation, slide_data: dict, palette: dict) -> None:
    """Stats grid slide — 2x2 or 1x3 stat boxes on light background."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    _set_slide_bg(slide, palette["light_bg"])
    _add_accent_bar(slide, palette)

    # Heading
    heading = slide_data.get("heading", "")
    _add_text_box(
        slide,
        left=CONTENT_LEFT,
        top=Inches(0.6),
        width=CONTENT_WIDTH,
        height=Inches(0.7),
        text=heading,
        font_name=TITLE_FONT,
        font_size=HEADING_SIZE,
        font_color=palette["dark_bg"],
        bold=True,
    )

    stats = slide_data.get("stats", [])
    if not stats:
        _add_footer(slide, palette)
        return

    # Lay out up to 4 stat boxes in a grid
    cols = min(len(stats), 4)
    box_w = Inches(2.5)
    gap = Inches(0.4)
    total_w = cols * box_w + (cols - 1) * gap
    start_left = (SLIDE_W - total_w) / 2  # Center horizontally

    for i, stat in enumerate(stats[:4]):
        col = i % cols
        left = start_left + col * (box_w + gap)
        top = Inches(2.2)

        # Stat number
        _add_text_box(
            slide,
            left=left,
            top=top,
            width=box_w,
            height=Inches(1.2),
            text=stat.get("value", ""),
            font_name=TITLE_FONT,
            font_size=STAT_NUMBER_SIZE,
            font_color=palette["primary_accent"],
            bold=True,
            alignment=PP_ALIGN.CENTER,
        )

        # Stat label
        _add_text_box(
            slide,
            left=left,
            top=top + Inches(1.2),
            width=box_w,
            height=Inches(0.8),
            text=stat.get("label", ""),
            font_name=BODY_FONT,
            font_size=STAT_LABEL_SIZE,
            font_color="#6B7280",
            alignment=PP_ALIGN.CENTER,
        )

    _add_footer(slide, palette)


def add_comparison_slide(prs: Presentation, slide_data: dict, palette: dict) -> None:
    """Two-column comparison slide (Before/After or Current/Proposed)."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    _set_slide_bg(slide, palette["light_bg"])
    _add_accent_bar(slide, palette)

    # Heading
    heading = slide_data.get("heading", "")
    _add_text_box(
        slide,
        left=CONTENT_LEFT,
        top=Inches(0.6),
        width=CONTENT_WIDTH,
        height=Inches(0.7),
        text=heading,
        font_name=TITLE_FONT,
        font_size=HEADING_SIZE,
        font_color=palette["dark_bg"],
        bold=True,
    )

    col_width = Inches(5.2)
    left_col_left = Inches(0.9)
    right_col_left = Inches(7.0)
    col_top = Inches(1.6)
    col_height = Inches(5.0)

    # Left column
    left_data = slide_data.get("left", {})
    left_title = left_data.get("title", "Current State")
    _add_text_box(
        slide,
        left=left_col_left,
        top=col_top,
        width=col_width,
        height=Inches(0.5),
        text=left_title,
        font_name=BODY_FONT,
        font_size=SUBHEADING_SIZE,
        font_color="#6B7280",
        bold=True,
    )
    left_bullets = left_data.get("bullets", [])
    if left_bullets:
        _add_bullets(
            slide,
            left=left_col_left,
            top=col_top + Inches(0.6),
            width=col_width,
            height=col_height - Inches(0.6),
            items=left_bullets,
            font_color="#444444",
        )

    # Right column
    right_data = slide_data.get("right", {})
    right_title = right_data.get("title", "With Anyreach")
    _add_text_box(
        slide,
        left=right_col_left,
        top=col_top,
        width=col_width,
        height=Inches(0.5),
        text=right_title,
        font_name=BODY_FONT,
        font_size=SUBHEADING_SIZE,
        font_color=palette["primary_accent"],
        bold=True,
    )
    right_bullets = right_data.get("bullets", [])
    if right_bullets:
        _add_bullets(
            slide,
            left=right_col_left,
            top=col_top + Inches(0.6),
            width=col_width,
            height=col_height - Inches(0.6),
            items=right_bullets,
            font_color="#333333",
        )

    # Divider line
    from pptx.enum.shapes import MSO_SHAPE
    divider = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(6.5),
        Inches(1.6),
        Inches(0.03),
        Inches(5.0),
    )
    neutral = palette.get("neutral", DEFAULT_PALETTE["neutral"])
    divider_color = neutral[2] if isinstance(neutral, list) and len(neutral) > 2 else "#D1D5DB"
    divider.fill.solid()
    divider.fill.fore_color.rgb = _hex_to_rgb(divider_color)
    divider.line.fill.background()

    _add_footer(slide, palette)


def add_cta_slide(
    prs: Presentation,
    slide_data: dict,
    palette: dict,
    logo_path: str | None = None,
) -> None:
    """Final CTA slide — dark background, call-to-action."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    _set_slide_bg(slide, palette["dark_bg"])
    _add_accent_bar(slide, palette)

    # Heading
    heading = slide_data.get("heading", "Next Steps")
    _add_text_box(
        slide,
        left=Inches(1.5),
        top=Inches(1.5),
        width=Inches(10.0),
        height=Inches(1.0),
        text=heading,
        font_name=TITLE_FONT,
        font_size=Pt(36),
        font_color="#FFFFFF",
        bold=True,
        alignment=PP_ALIGN.CENTER,
    )

    # CTA bullets or body
    bullets = slide_data.get("bullets", [])
    if bullets:
        _add_bullets(
            slide,
            left=Inches(2.5),
            top=Inches(2.8),
            width=Inches(8.0),
            height=Inches(3.0),
            items=bullets,
            font_color="#D1D5DB",
            font_size=BODY_SIZE,
        )

    body = slide_data.get("body", "")
    if body and not bullets:
        _add_text_box(
            slide,
            left=Inches(2.5),
            top=Inches(2.8),
            width=Inches(8.0),
            height=Inches(3.0),
            text=body,
            font_name=BODY_FONT,
            font_size=BODY_SIZE,
            font_color="#D1D5DB",
            alignment=PP_ALIGN.CENTER,
        )

    # Contact line
    contact = slide_data.get("contact", "")
    if contact:
        _add_text_box(
            slide,
            left=Inches(1.5),
            top=Inches(5.8),
            width=Inches(10.0),
            height=Inches(0.5),
            text=contact,
            font_name=BODY_FONT,
            font_size=SUBHEADING_SIZE,
            font_color=palette.get("secondary_accent", "#818CF8"),
            alignment=PP_ALIGN.CENTER,
        )

    if logo_path:
        try:
            slide.shapes.add_picture(logo_path, Inches(5.7), Inches(6.3), Inches(2.0), Inches(0.8))
        except Exception:
            pass

    _add_footer(slide, palette, text="Anyreach.ai | Let's build the future of CX")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def create_presentation(
    slides_data: list[dict],
    palette: dict,
    company_name: str,
    bpo_name: str,
    logo_path: str | None = None,
) -> Presentation:
    """Build the full .pptx from structured slide data and a color palette."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    for slide_data in slides_data:
        slide_type = slide_data.get("type", "content")
        if slide_type == "title":
            add_title_slide(prs, slide_data, palette, logo_path)
        elif slide_type == "stats_grid":
            add_stats_slide(prs, slide_data, palette)
        elif slide_type == "content":
            add_content_slide(prs, slide_data, palette)
        elif slide_type == "comparison":
            add_comparison_slide(prs, slide_data, palette)
        elif slide_type == "cta":
            add_cta_slide(prs, slide_data, palette, logo_path)
        else:
            add_content_slide(prs, slide_data, palette)

    return prs

"""Generate a branded CX Intelligence XLSX workbook from scraped review data."""

from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# ── Brand colours ──────────────────────────────────────────────────────
DARK_BLUE_FILL = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
MED_BLUE_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
LIGHT_BLUE_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")

POSITIVE_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
MIXED_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
NEGATIVE_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

POSITIVE_FONT = Font(name="Calibri", color="006100")
MIXED_FONT = Font(name="Calibri", color="9C5700")
NEGATIVE_FONT = Font(name="Calibri", color="9C0006")

WHITE_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
WHITE_FONT_LG = Font(name="Calibri", bold=True, color="FFFFFF", size=14)
HEADER_FONT = Font(name="Calibri", bold=True, color="1F3864", size=10)
BODY_FONT = Font(name="Calibri", size=10)

THIN_BORDER = Border(
    left=Side(style="thin", color="B4C6E7"),
    right=Side(style="thin", color="B4C6E7"),
    top=Side(style="thin", color="B4C6E7"),
    bottom=Side(style="thin", color="B4C6E7"),
)

WRAP_ALIGN = Alignment(wrap_text=True, vertical="top")
CENTER_ALIGN = Alignment(horizontal="center", vertical="center")


def _sentiment_style(sentiment: str) -> tuple[PatternFill, Font]:
    s = (sentiment or "").lower()
    if s == "positive":
        return POSITIVE_FILL, POSITIVE_FONT
    if s == "negative":
        return NEGATIVE_FILL, NEGATIVE_FONT
    return MIXED_FILL, MIXED_FONT


def _set_col_widths(ws, widths: dict[int, int]) -> None:
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def _write_title_row(ws, row: int, text: str, col_span: int = 6) -> None:
    """Write a dark-blue title row spanning *col_span* columns."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_span)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = WHITE_FONT_LG
    cell.fill = DARK_BLUE_FILL
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 30
    # Fill merged area
    for c in range(2, col_span + 1):
        ws.cell(row=row, column=c).fill = DARK_BLUE_FILL


def _write_header_row(ws, row: int, headers: list[str], fill=LIGHT_BLUE_FILL) -> None:
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=i, value=h)
        cell.font = HEADER_FONT
        cell.fill = fill
        cell.border = THIN_BORDER
        cell.alignment = CENTER_ALIGN


def _write_data_cell(ws, row: int, col: int, value, font=BODY_FONT, alignment=WRAP_ALIGN) -> None:
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font
    cell.border = THIN_BORDER
    cell.alignment = alignment


# ======================================================================
# Public entry point
# ======================================================================

def generate_cx_xlsx(data: dict, company_name: str, output_path: Path | str) -> Path:
    """Build a CX Intelligence XLSX and save it to *output_path*."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()

    # ── Sheet 1: Review Summary ──────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Review Summary"
    _set_col_widths(ws1, {1: 22, 2: 14, 3: 30, 4: 14, 5: 14, 6: 40})

    row = 1
    _write_title_row(ws1, row, f"CX Intelligence Report — {company_name}")
    row += 2

    # Ratings by platform
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    cell = ws1.cell(row=row, column=1, value="Ratings by Platform")
    cell.font = WHITE_FONT
    cell.fill = MED_BLUE_FILL
    ws1.cell(row=row, column=2).fill = MED_BLUE_FILL
    row += 1

    _write_header_row(ws1, row, ["Platform", "Rating"])
    row += 1

    ratings = data.get("ratings_summary", {})
    for platform, rating in ratings.items():
        _write_data_cell(ws1, row, 1, platform)
        _write_data_cell(ws1, row, 2, str(rating), alignment=CENTER_ALIGN)
        row += 1

    row += 1  # spacer

    # Overall rating
    overall = data.get("overall_rating")
    if overall is not None:
        _write_data_cell(ws1, row, 1, "Overall Rating", font=Font(name="Calibri", bold=True, size=11))
        _write_data_cell(ws1, row, 2, str(overall), font=Font(name="Calibri", bold=True, size=11), alignment=CENTER_ALIGN)
        row += 1

    total = data.get("total_reviews_found", 0)
    _write_data_cell(ws1, row, 1, "Total Reviews Found", font=Font(name="Calibri", bold=True, size=11))
    _write_data_cell(ws1, row, 2, str(total), font=Font(name="Calibri", bold=True, size=11), alignment=CENTER_ALIGN)
    row += 2

    # Sentiment distribution
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    cell = ws1.cell(row=row, column=1, value="Sentiment Distribution")
    cell.font = WHITE_FONT
    cell.fill = MED_BLUE_FILL
    ws1.cell(row=row, column=2).fill = MED_BLUE_FILL
    row += 1

    _write_header_row(ws1, row, ["Sentiment", "Count"])
    row += 1

    dist = data.get("sentiment_distribution", {})
    for sentiment_key in ("positive", "mixed", "negative"):
        count = dist.get(sentiment_key, 0)
        fill, font = _sentiment_style(sentiment_key)
        cell_label = ws1.cell(row=row, column=1, value=sentiment_key.title())
        cell_label.font = font
        cell_label.fill = fill
        cell_label.border = THIN_BORDER
        cell_count = ws1.cell(row=row, column=2, value=count)
        cell_count.font = font
        cell_count.fill = fill
        cell_count.border = THIN_BORDER
        cell_count.alignment = CENTER_ALIGN
        row += 1

    row += 1

    # Key themes
    themes = data.get("themes", [])
    if themes:
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        cell = ws1.cell(row=row, column=1, value="Key Themes")
        cell.font = WHITE_FONT
        cell.fill = MED_BLUE_FILL
        for c in range(2, 5):
            ws1.cell(row=row, column=c).fill = MED_BLUE_FILL
        row += 1

        _write_header_row(ws1, row, ["Theme", "Frequency", "Sentiment", "Platforms"])
        row += 1

        for t in themes:
            _write_data_cell(ws1, row, 1, t.get("theme", ""))
            _write_data_cell(ws1, row, 2, t.get("frequency", ""), alignment=CENTER_ALIGN)
            sentiment = t.get("sentiment", "mixed")
            fill, font = _sentiment_style(sentiment)
            cell_s = ws1.cell(row=row, column=3, value=sentiment.title())
            cell_s.font = font
            cell_s.fill = fill
            cell_s.border = THIN_BORDER
            cell_s.alignment = CENTER_ALIGN
            platforms_str = ", ".join(t.get("platforms", []))
            _write_data_cell(ws1, row, 4, platforms_str)
            row += 1

    row += 1

    # Summary
    summary = data.get("summary", "")
    if summary:
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        cell = ws1.cell(row=row, column=1, value="Summary")
        cell.font = WHITE_FONT
        cell.fill = MED_BLUE_FILL
        for c in range(2, 7):
            ws1.cell(row=row, column=c).fill = MED_BLUE_FILL
        row += 1
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        cell = ws1.cell(row=row, column=1, value=summary)
        cell.font = BODY_FONT
        cell.alignment = WRAP_ALIGN
        ws1.row_dimensions[row].height = 45

    # ── Sheet 2: Third-Party Reviews ─────────────────────────────────
    ws2 = wb.create_sheet("Third-Party Reviews")
    _set_col_widths(ws2, {1: 18, 2: 10, 3: 60, 4: 14, 5: 14})

    row = 1
    _write_title_row(ws2, row, f"Third-Party Reviews — {company_name}", col_span=5)
    row += 2

    _write_header_row(ws2, row, ["Platform", "Rating", "Review Text", "Date", "Sentiment"])
    row += 1

    reviews = data.get("reviews", [])
    for rev in reviews:
        _write_data_cell(ws2, row, 1, rev.get("platform", ""))
        _write_data_cell(ws2, row, 2, str(rev.get("rating", "")), alignment=CENTER_ALIGN)
        _write_data_cell(ws2, row, 3, rev.get("text", ""))
        _write_data_cell(ws2, row, 4, rev.get("date", ""), alignment=CENTER_ALIGN)

        sentiment = rev.get("sentiment", "mixed")
        fill, font = _sentiment_style(sentiment)
        cell_s = ws2.cell(row=row, column=5, value=sentiment.title())
        cell_s.font = font
        cell_s.fill = fill
        cell_s.border = THIN_BORDER
        cell_s.alignment = CENTER_ALIGN

        ws2.row_dimensions[row].height = 30
        row += 1

    if not reviews:
        ws2.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        ws2.cell(row=row, column=1, value="No third-party reviews found.").font = BODY_FONT

    # ── Sheet 3: Employee Reviews ────────────────────────────────────
    ws3 = wb.create_sheet("Employee Reviews")
    _set_col_widths(ws3, {1: 16, 2: 10, 3: 30, 4: 35, 5: 35, 6: 14, 7: 14})

    row = 1
    _write_title_row(ws3, row, f"Employee Reviews — {company_name}", col_span=7)
    row += 2

    _write_header_row(ws3, row, ["Platform", "Rating", "Title", "Pros", "Cons", "Date", "Sentiment"])
    row += 1

    emp_reviews = data.get("employee_reviews", [])
    for er in emp_reviews:
        _write_data_cell(ws3, row, 1, er.get("platform", ""))
        _write_data_cell(ws3, row, 2, str(er.get("rating", "")), alignment=CENTER_ALIGN)
        _write_data_cell(ws3, row, 3, er.get("title", ""))
        _write_data_cell(ws3, row, 4, er.get("pros", ""))
        _write_data_cell(ws3, row, 5, er.get("cons", ""))
        _write_data_cell(ws3, row, 6, er.get("date", ""), alignment=CENTER_ALIGN)

        sentiment = er.get("sentiment", "mixed")
        fill, font = _sentiment_style(sentiment)
        cell_s = ws3.cell(row=row, column=7, value=sentiment.title())
        cell_s.font = font
        cell_s.fill = fill
        cell_s.border = THIN_BORDER
        cell_s.alignment = CENTER_ALIGN

        ws3.row_dimensions[row].height = 35
        row += 1

    if not emp_reviews:
        ws3.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        ws3.cell(row=row, column=1, value="No employee reviews found.").font = BODY_FONT

    # ── Save ─────────────────────────────────────────────────────────
    wb.save(str(output_path))
    logger.info("CX XLSX saved to %s", output_path)
    return output_path

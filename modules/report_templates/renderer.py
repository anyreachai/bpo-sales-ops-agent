"""Render Jinja2 HTML templates to PDF via xhtml2pdf."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import markdown as md
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent

_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
        )
        _env.filters["markdown"] = _md_filter
        _env.filters["b64image"] = _b64image_filter
        _env.filters["sentiment_class"] = _sentiment_class
    return _env


def _md_filter(text: str) -> str:
    """Convert markdown text to HTML."""
    if not text:
        return ""
    return md.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br"],
    )


def _b64image_filter(path_str: str) -> str:
    """Convert a file path to a base64 data URI."""
    try:
        p = Path(path_str)
        if not p.exists():
            return ""
        data = p.read_bytes()
        suffix = p.suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}.get(suffix, "image/png")
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def _sentiment_class(sentiment: str) -> str:
    """Return CSS class name for a sentiment value."""
    s = (sentiment or "").lower()
    if s == "positive":
        return "positive"
    if s == "negative":
        return "negative"
    return "mixed"


def _brand_vars(brand_guide: dict | None) -> dict:
    """Extract CSS-friendly variables from brand_guide."""
    defaults = {
        "primary": "#1A1F3D",
        "secondary": "#2F5496",
        "accent": "#5B5FC7",
        "font_heading": "Helvetica, Arial, sans-serif",
        "font_body": "Helvetica, Arial, sans-serif",
        "logo_data_uri": "",
        "company_name": "",
    }
    if not brand_guide:
        return defaults

    colors = brand_guide.get("colors", {})
    fonts = brand_guide.get("fonts", {})
    logo_path = brand_guide.get("logo_path", "")

    defaults["primary"] = colors.get("primary", defaults["primary"])
    defaults["secondary"] = colors.get("secondary", defaults["secondary"])
    defaults["accent"] = colors.get("accent", defaults["accent"])
    defaults["company_name"] = brand_guide.get("company_name", "")

    if logo_path:
        defaults["logo_data_uri"] = _b64image_filter(logo_path)

    return defaults


def render_pdf(
    template_name: str,
    context: dict,
    output_path: Path,
    brand_guide: dict | None = None,
) -> Path:
    """Render an HTML template to PDF.

    Args:
        template_name: Template filename (e.g. "stakeholder_intel.html")
        context: Template variables
        output_path: Where to write the PDF
        brand_guide: Optional brand colors/fonts/logo

    Returns:
        The output_path for chaining.
    """
    env = _get_env()
    template = env.get_template(template_name)

    brand = _brand_vars(brand_guide)
    context["brand"] = brand

    html_str = template.render(**context)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(str(output_path), "w+b") as f:
        status = pisa.CreatePDF(html_str, dest=f)

    if status.err:
        logger.error("PDF generation had errors for %s", template_name)

    logger.info("Rendered %s -> %s (%d bytes)", template_name, output_path.name, output_path.stat().st_size)
    return output_path

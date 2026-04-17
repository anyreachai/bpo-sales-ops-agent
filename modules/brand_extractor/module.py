"""Brand Extractor module — pulls brand assets from Brand.dev and builds a deck palette."""

from __future__ import annotations

import colorsys
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from modules._base import BaseModule
from modules.brand_extractor.client import BrandDevClient
from orchestrator.config import settings
from shared.storage import artifact_path, ensure_session_dir
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

# ── Default fallback palette (Cool Indigo) ────────────────────────────
DEFAULT_PALETTE = {
    "dark_bg": "#1A1A2E",
    "light_bg": "#F5F6FA",
    "primary_accent": "#5B5FC7",
    "secondary_accent": "#818CF8",
    "neutral_scale": ["#6B7280", "#9CA3AF", "#D1D5DB"],
}

DEFAULT_BRAND_GUIDE = {
    "company_name": "Unknown",
    "domain": "",
    "colors": {"primary": "#5B5FC7", "secondary": "#818CF8", "accent": "#6366F1"},
    "logos": {"full": None, "icon": None},
    "fonts": {"heading": "Inter", "body": "Inter"},
    "description": "",
}


# ── Color utility functions ───────────────────────────────────────────

def hex_to_hsl(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex color string to (H, S, L) where each value is 0-1."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)  # noqa: E741 — stdlib names
    return h, s, l


def hsl_to_hex(h: float, s: float, l: float) -> str:  # noqa: E741
    """Convert (H, S, L) (each 0-1) back to a hex color string."""
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02X}{:02X}{:02X}".format(
        int(round(r * 255)),
        int(round(g * 255)),
        int(round(b * 255)),
    )


def darken(hex_color: str, amount: float = 0.15) -> str:
    """Return *hex_color* darkened by *amount* (0-1 fraction of lightness removed)."""
    h, s, l = hex_to_hsl(hex_color)
    return hsl_to_hex(h, s, max(0.0, l - amount))


def lighten(hex_color: str, amount: float = 0.20) -> str:
    """Return *hex_color* lightened by *amount*."""
    h, s, l = hex_to_hsl(hex_color)
    return hsl_to_hex(h, s, min(1.0, l + amount))


def desaturate(hex_color: str, amount: float = 0.40) -> str:
    """Return *hex_color* with saturation reduced by *amount*."""
    h, s, l = hex_to_hsl(hex_color)
    return hsl_to_hex(h, max(0.0, s - amount), l)


# ── Module implementation ─────────────────────────────────────────────

class BrandExtractorModule(BaseModule):
    name = "brand_extractor"

    def should_run(self, ctx: SessionContext) -> bool:
        return bool(ctx.target_url)

    async def run(self, ctx: SessionContext) -> ModuleResult:
        domain = _extract_domain(ctx.target_url or "")
        if not domain:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="Could not extract a valid domain from target_url",
            )

        client = BrandDevClient(api_key=settings.BRAND_DEV_API_KEY)

        # ── 1. Fetch brand data from Brand.dev ────────────────────────
        raw: dict | None = None
        try:
            raw = await client.get_brand(domain)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Brand.dev HTTP %s for %s — falling back to defaults",
                exc.response.status_code,
                domain,
            )
        except Exception as exc:
            logger.warning("Brand.dev request failed for %s: %s — falling back", domain, exc)

        # ── 2. Parse into brand_guide ─────────────────────────────────
        if raw:
            brand_guide = _parse_brand_response(raw, domain)
        else:
            brand_guide = {**DEFAULT_BRAND_GUIDE, "domain": domain}

        # ── 3. Download primary logo ──────────────────────────────────
        logo_artifact: Artifact | None = None
        logo_url = (brand_guide.get("logos") or {}).get("full")
        if logo_url:
            try:
                logo_save = artifact_path(ctx.session_id, brand_guide["company_name"], "logo", "png")
                await client.download_logo(logo_url, logo_save)
                logo_artifact = Artifact(
                    filename=logo_save.name,
                    path=logo_save,
                    artifact_type="brand_guide",
                    mime_type="image/png",
                    size_bytes=logo_save.stat().st_size,
                )
            except Exception as exc:
                logger.warning("Logo download failed: %s", exc)

        # ── 4. Build deck palette via HSL manipulation ────────────────
        primary_hex = (brand_guide.get("colors") or {}).get("primary")
        if primary_hex and _is_valid_hex(primary_hex):
            deck_palette = _build_palette(primary_hex)
        else:
            deck_palette = {**DEFAULT_PALETTE}

        brand_guide["deck_palette"] = deck_palette

        # ── 5. Save brand_guide JSON artifact ─────────────────────────
        json_path = artifact_path(ctx.session_id, brand_guide["company_name"], "brand_guide", "json")
        json_path.write_text(json.dumps(brand_guide, indent=2), encoding="utf-8")
        json_artifact = Artifact(
            filename=json_path.name,
            path=json_path,
            artifact_type="brand_guide",
            mime_type="application/json",
            size_bytes=json_path.stat().st_size,
        )

        # ── 6. Populate ctx for downstream modules ────────────────────
        ctx.brand_guide = brand_guide

        # ── 7. Return result ──────────────────────────────────────────
        artifacts = [json_artifact]
        if logo_artifact:
            artifacts.append(logo_artifact)

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=artifacts,
            metadata=brand_guide,
        )


# ── Private helpers ───────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Strip protocol, www prefix, and path to yield a bare domain."""
    url = url.strip()
    if not url:
        return ""
    # Ensure a scheme so urlparse works
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Strip leading "www."
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_valid_hex(value: str) -> bool:
    return bool(_HEX_RE.match(value))


def _safe_hex(value: str | None, fallback: str = "#5B5FC7") -> str:
    """Return *value* if it looks like a valid hex color, else *fallback*."""
    if value and _is_valid_hex(value):
        return value
    return fallback


def _parse_brand_response(raw: dict, domain: str) -> dict:
    """Normalise the Brand.dev JSON payload into our standard brand_guide shape."""
    colors = raw.get("colors") or {}
    logos = raw.get("logos") or {}
    fonts = raw.get("fonts") or {}

    # Colors — Brand.dev may nest differently; try flat keys first.
    primary = _safe_hex(colors.get("primary"))
    secondary = _safe_hex(colors.get("secondary"), lighten(primary, 0.15))
    accent = _safe_hex(colors.get("accent"), lighten(primary, 0.25))

    # Logos — prefer the "full" or first available URL.
    full_logo = None
    icon_logo = None
    if isinstance(logos, dict):
        full_logo = logos.get("full") or logos.get("default") or logos.get("url")
        icon_logo = logos.get("icon") or logos.get("favicon")
    elif isinstance(logos, list) and logos:
        full_logo = logos[0].get("url") if isinstance(logos[0], dict) else logos[0]

    # Fonts
    heading_font = fonts.get("heading") or fonts.get("title") or "Inter"
    body_font = fonts.get("body") or fonts.get("paragraph") or "Inter"

    return {
        "company_name": raw.get("name") or raw.get("company") or domain,
        "domain": domain,
        "colors": {"primary": primary, "secondary": secondary, "accent": accent},
        "logos": {"full": full_logo, "icon": icon_logo},
        "fonts": {"heading": heading_font, "body": body_font},
        "description": raw.get("description") or "",
    }


def _build_palette(primary_hex: str) -> dict:
    """Derive a full deck palette from a single primary hex color."""
    h, s, l = hex_to_hsl(primary_hex)

    dark_bg = hsl_to_hex(h, s, 0.085)          # ~85 % dark
    light_bg = hsl_to_hex(h, s * 0.3, 0.95)    # 95 % light, low saturation
    secondary_accent = lighten(primary_hex, 0.20)

    # Three desaturated midtones at different lightness levels
    neutral_scale = [
        desaturate(hsl_to_hex(h, s, 0.45), 0.40),
        desaturate(hsl_to_hex(h, s, 0.60), 0.45),
        desaturate(hsl_to_hex(h, s, 0.80), 0.50),
    ]

    return {
        "dark_bg": dark_bg,
        "light_bg": light_bg,
        "primary_accent": primary_hex,
        "secondary_accent": secondary_accent,
        "neutral_scale": neutral_scale,
    }

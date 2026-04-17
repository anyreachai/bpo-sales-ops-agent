"""Brand.dev API client for fetching brand assets (logos, colors, fonts)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class BrandDevClient:
    """Thin async wrapper around the Brand.dev REST API."""

    BASE_URL = "https://api.brand.dev/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get_brand(self, domain: str) -> dict:
        """Fetch brand data for *domain*.

        Returns the raw JSON dict from Brand.dev.
        Raises ``httpx.HTTPStatusError`` on 4xx/5xx responses.
        """
        url = f"{self.BASE_URL}/brand"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"domain": domain}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            logger.info("Brand.dev returned data for %s", domain)
            return data

    async def download_logo(self, logo_url: str, save_path: Path) -> Path:
        """Download a logo image from *logo_url* and write it to *save_path*.

        Returns the *save_path* on success.
        """
        save_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(logo_url)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
            logger.info("Downloaded logo to %s (%d bytes)", save_path, len(resp.content))
            return save_path

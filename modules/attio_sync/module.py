"""Attio Sync module — links the prospect Company in Attio to the BPO partner.

Runs in PHASE_2. For each session with a BPO that has an attio_record_id,
upserts the prospect Company by domain and sets the
``connector_bpo_channel_partner`` relationship to the BPO's record.
"""

from __future__ import annotations

import logging

import httpx

from modules._base import BaseModule
from modules.attio_sync.attio_client import (
    COMPANIES_ASSERT_URL,
    assert_company,
    build_assert_payload,
    extract_domain,
)
from orchestrator.config import settings
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)


class AttioSyncModule(BaseModule):
    name = "attio_sync"

    def should_run(self, ctx: SessionContext) -> bool:
        if not settings.ATTIO_SYNC_ENABLED:
            return False
        if not settings.ATTIO_API_KEY:
            return False
        if ctx.bpo is None or not ctx.bpo.attio_record_id:
            return False
        if not ctx.target_company:
            return False
        if not extract_domain(ctx.target_url):
            return False
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company_name = ctx.target_company or ""
        domain = extract_domain(ctx.target_url)
        connector_id = ctx.bpo.attio_record_id if ctx.bpo else None
        bpo_name = ctx.bpo.name if ctx.bpo else "Unknown"

        # Defensive — should_run gates this, but guard anyway.
        if not (domain and connector_id and company_name):
            return ModuleResult(
                module_name=self.name,
                status="skipped",
                metadata={
                    "reason": "missing prerequisites",
                    "company": company_name,
                    "domain": domain,
                    "connector_id": connector_id,
                },
            )

        payload = build_assert_payload(
            name=company_name,
            domain=domain,
            connector_record_id=connector_id,
        )

        if ctx.dry_run:
            logger.info(
                "Dry-run: would upsert Attio Company %s (domain=%s) with connector → %s (%s)",
                company_name,
                domain,
                bpo_name,
                connector_id,
            )
            return ModuleResult(
                module_name=self.name,
                status="success",
                metadata={
                    "dry_run": True,
                    "company": company_name,
                    "domain": domain,
                    "connector_id": connector_id,
                    "bpo": bpo_name,
                    "would_send": {
                        "url": COMPANIES_ASSERT_URL,
                        "payload": payload,
                    },
                },
            )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await assert_company(
                    client,
                    settings.ATTIO_API_KEY,
                    name=company_name,
                    domain=domain,
                    connector_record_id=connector_id,
                )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            body = exc.response.text[:500] if exc.response is not None else ""
            error = f"Attio HTTP {status_code}: {body}"
            logger.error(error)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                metadata={
                    "company": company_name,
                    "domain": domain,
                    "connector_id": connector_id,
                    "status_code": status_code,
                },
                error=error,
            )
        except Exception as exc:  # network errors, JSON parse, etc.
            error = f"Attio sync failed: {exc}"
            logger.error(error)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                metadata={
                    "company": company_name,
                    "domain": domain,
                    "connector_id": connector_id,
                },
                error=error,
            )

        record_id = (
            response.get("data", {}).get("id", {}).get("record_id")
            if isinstance(response, dict)
            else None
        )
        logger.info(
            "Attio company asserted: %s (record_id=%s, connector=%s)",
            company_name,
            record_id,
            bpo_name,
        )
        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata={
                "company": company_name,
                "domain": domain,
                "connector_id": connector_id,
                "bpo": bpo_name,
                "attio_record_id": record_id,
                "connector_set": True,
            },
        )

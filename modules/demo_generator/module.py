from __future__ import annotations

import logging

from modules._base import BaseModule
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)


class DemoGeneratorModule(BaseModule):
    name = "demo_generator"

    def should_run(self, ctx: SessionContext) -> bool:
        return "demo" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        demo_link = self._lookup_existing_demo(ctx.target_company)

        if demo_link:
            logger.info("Found existing demo link for %s: %s", ctx.target_company, demo_link)
            ctx.demo_link = demo_link
            return ModuleResult(
                module_name=self.name,
                status="success",
                metadata={
                    "demo_link": demo_link,
                    "source": "pipeline_state",
                },
            )

        # No demo exists yet — instruct the BPO to trigger the external demo system.
        logger.info("No existing demo for %s; flagging action required", ctx.target_company)
        ctx.demo_link = None
        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata={
                "action_required": "email_demo_address",
                "instructions": (
                    "Send an email to demo@mail.anyreach.ai with the prospect's "
                    f"website URL ({ctx.target_url or 'unknown'}) in the body. "
                    "The demo system will automatically build a personalized demo "
                    "and reply with the demo link."
                ),
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_existing_demo(company_name: str | None) -> str | None:
        """Query the pipeline_state table for an existing demo link.

        Returns the link string if found, otherwise None.  Gracefully
        handles missing DATABASE_URL, missing table, and connection errors.
        """
        if not company_name:
            return None

        from orchestrator.config import settings

        if not settings.DATABASE_URL:
            logger.debug("DATABASE_URL not configured — skipping demo lookup")
            return None

        try:
            import psycopg2  # noqa: F811

            with psycopg2.connect(settings.DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT demo_link FROM pipeline_state "
                        "WHERE company_name ILIKE %s AND demo_link IS NOT NULL "
                        "LIMIT 1",
                        (f"%{company_name}%",),
                    )
                    row = cur.fetchone()
                    if row:
                        return row[0]
        except ImportError:
            logger.warning("psycopg2 not installed — skipping demo lookup")
        except Exception as exc:
            # Catches UndefinedTable, connection errors, etc.
            logger.debug("Demo lookup query failed (table may not exist yet): %s", exc)

        return None

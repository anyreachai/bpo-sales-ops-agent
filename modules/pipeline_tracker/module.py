"""Pipeline Tracker module — updates Google Sheets and Postgres pipeline state."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from modules._base import BaseModule
from orchestrator.config import settings
from shared.google_auth import get_access_token
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _google_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class PipelineTrackerModule(BaseModule):
    name = "pipeline_tracker"

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "Unknown"
        bpo_key = ctx.bpo.key if ctx.bpo else "unknown"
        bpo_name = ctx.bpo.name if ctx.bpo else "Unknown"
        now = datetime.now(timezone.utc)

        # ── Build row data ───────────────────────────────────────────
        row = self._build_row(ctx, now)

        sheet_updated = False
        db_updated = False
        errors: list[str] = []

        # ── 1. Update Google Sheet (skip on dry_run) ─────────────────
        sheet_id = ctx.bpo.pipeline_sheet_id if ctx.bpo else None
        if sheet_id and not ctx.dry_run:
            try:
                token = get_access_token(
                    settings.GOOGLE_OAUTH_CLIENT_ID,
                    settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    settings.GOOGLE_OAUTH_REFRESH_TOKEN,
                )
                sheet_updated = await self._update_sheet(token, sheet_id, company, row)
            except Exception as exc:
                logger.error("Sheet update failed: %s", exc)
                errors.append(f"sheet: {exc}")
        elif sheet_id and ctx.dry_run:
            logger.info("Dry-run: skipping Sheet update for %s", company)
        else:
            logger.info("No pipeline_sheet_id configured — skipping Sheet update")

        # ── 2. Update Postgres ───────────────────────────────────────
        if settings.DATABASE_URL:
            try:
                db_updated = self._upsert_postgres(
                    bpo_key=bpo_key,
                    company_name=company,
                    stage="Materials Sent",
                    drive_folder_url=ctx.drive_links.get("folder", ""),
                    demo_link=ctx.demo_link or "",
                    draft_url=ctx.module_results.get("email_composer", ModuleResult(module_name="email_composer", status="skipped")).metadata.get("draft_url", ""),
                    dry_run=ctx.dry_run,
                    updated_at=now,
                )
            except Exception as exc:
                logger.error("Postgres upsert failed: %s", exc)
                errors.append(f"db: {exc}")
        else:
            logger.info("DATABASE_URL not configured — skipping Postgres upsert")

        status = "success" if not errors else "success"  # partial success is still success
        if errors and not sheet_updated and not db_updated:
            status = "failed"

        return ModuleResult(
            module_name=self.name,
            status=status,
            metadata={
                "company": company,
                "bpo_key": bpo_key,
                "stage": "Materials Sent",
                "sheet_updated": sheet_updated,
                "db_updated": db_updated,
                "errors": errors,
                "row_data": row,
            },
            error="; ".join(errors) if errors and status == "failed" else None,
        )

    # ── Row builder ──────────────────────────────────────────────────

    @staticmethod
    def _build_row(ctx: SessionContext, now: datetime) -> list[str]:
        """Build a flat row for the pipeline sheet.

        Columns: Date | Stage | BPO | Company | Contact | URL | Deliverables |
                 Drive Folder | Demo Link | Draft URL | Notes
        """
        contact = ""
        if ctx.intake:
            parts = [ctx.intake.contact_name or "", ctx.intake.contact_title or ""]
            contact = " — ".join(p for p in parts if p)

        deliverables = ", ".join(ctx.deliverables_requested)
        drive_folder = ctx.drive_links.get("folder", "")
        demo_link = ctx.demo_link or ""

        # Draft URL from email_composer result (may not exist yet)
        draft_url = ""
        ec_result = ctx.module_results.get("email_composer")
        if ec_result and ec_result.metadata:
            draft_url = ec_result.metadata.get("draft_url", "")

        return [
            now.strftime("%Y-%m-%d"),           # A: Date
            "Materials Sent",                    # B: Stage
            ctx.bpo.name if ctx.bpo else "",     # C: BPO
            ctx.target_company or "",            # D: Company
            contact,                             # E: Contact
            ctx.target_url or "",                # F: URL
            deliverables,                        # G: Deliverables
            drive_folder,                        # H: Drive Folder
            demo_link,                           # I: Demo Link
            draft_url,                           # J: Draft URL
            "",                                  # K: Notes
        ]

    # ── Google Sheets helpers ────────────────────────────────────────

    async def _update_sheet(
        self,
        token: str,
        sheet_id: str,
        company: str,
        row: list[str],
    ) -> bool:
        """Find or append a row in the pipeline sheet. Returns True on success."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Read column D (Company) to find existing row
            read_resp = await client.get(
                f"{SHEETS_API}/{sheet_id}/values/A:K",
                headers=_google_headers(token),
                params={"majorDimension": "ROWS"},
            )
            read_resp.raise_for_status()
            values = read_resp.json().get("values", [])

            # Search for existing row by company name (column D = index 3)
            existing_row_idx: int | None = None
            for idx, r in enumerate(values):
                if len(r) > 3 and r[3].strip().lower() == company.strip().lower():
                    existing_row_idx = idx + 1  # Sheets uses 1-based indexing
                    break

            if existing_row_idx is not None:
                # Update existing row
                range_str = f"A{existing_row_idx}:K{existing_row_idx}"
                update_resp = await client.put(
                    f"{SHEETS_API}/{sheet_id}/values/{range_str}",
                    headers={
                        **_google_headers(token),
                        "Content-Type": "application/json",
                    },
                    params={"valueInputOption": "USER_ENTERED"},
                    json={"range": range_str, "values": [row]},
                )
                update_resp.raise_for_status()
                logger.info("Updated row %d in sheet for %s", existing_row_idx, company)
            else:
                # Append new row
                append_resp = await client.post(
                    f"{SHEETS_API}/{sheet_id}/values/A:K:append",
                    headers={
                        **_google_headers(token),
                        "Content-Type": "application/json",
                    },
                    params={
                        "valueInputOption": "USER_ENTERED",
                        "insertDataOption": "INSERT_ROWS",
                    },
                    json={"values": [row]},
                )
                append_resp.raise_for_status()
                logger.info("Appended new row in sheet for %s", company)

        return True

    # ── Postgres helpers ─────────────────────────────────────────────

    @staticmethod
    def _upsert_postgres(
        bpo_key: str,
        company_name: str,
        stage: str,
        drive_folder_url: str,
        demo_link: str,
        draft_url: str,
        dry_run: bool,
        updated_at: datetime,
    ) -> bool:
        """Upsert pipeline state row. Returns True on success."""
        import psycopg2

        with psycopg2.connect(settings.DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Ensure table exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pipeline_state (
                        id SERIAL PRIMARY KEY,
                        bpo_key TEXT NOT NULL,
                        company_name TEXT NOT NULL,
                        stage TEXT NOT NULL DEFAULT 'received',
                        drive_folder_url TEXT,
                        demo_link TEXT,
                        draft_url TEXT,
                        dry_run BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE (bpo_key, company_name)
                    )
                """)

                cur.execute(
                    """
                    INSERT INTO pipeline_state
                        (bpo_key, company_name, stage, drive_folder_url, demo_link, draft_url, dry_run, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bpo_key, company_name) DO UPDATE SET
                        stage = EXCLUDED.stage,
                        drive_folder_url = EXCLUDED.drive_folder_url,
                        demo_link = EXCLUDED.demo_link,
                        draft_url = EXCLUDED.draft_url,
                        dry_run = EXCLUDED.dry_run,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (bpo_key, company_name, stage, drive_folder_url, demo_link, draft_url, dry_run, updated_at),
                )
            conn.commit()

        logger.info("Postgres upsert complete for %s / %s", bpo_key, company_name)
        return True

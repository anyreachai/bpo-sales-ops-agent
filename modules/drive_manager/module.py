"""Drive Manager module — uploads artifacts to Google Drive and returns shareable links."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from modules._base import BaseModule
from orchestrator.config import settings
from shared.google_auth import get_access_token
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

# Maps our artifact MIME types to Drive-friendly ones; pass-through for unknowns.
_MIME_FALLBACK = "application/octet-stream"


def _google_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class DriveManagerModule(BaseModule):
    name = "drive_manager"

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "Unknown"
        parent_id = ctx.bpo.drive_folder_id if ctx.bpo else None

        if not parent_id:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="BPO partner has no drive_folder_id configured",
            )

        # ── Dry-run shortcut ─────────────────────────────────────────
        if ctx.dry_run:
            drive_links = self._mock_drive_links(ctx.all_artifacts)
            ctx.drive_links = drive_links
            return ModuleResult(
                module_name=self.name,
                status="success",
                metadata={"drive_links": drive_links, "dry_run": True},
            )

        # ── Get auth token ───────────────────────────────────────────
        token = get_access_token(
            settings.GOOGLE_OAUTH_CLIENT_ID,
            settings.GOOGLE_OAUTH_CLIENT_SECRET,
            settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        )

        async with httpx.AsyncClient(timeout=60) as client:
            # ── 1. Find or create company subfolder ──────────────────
            folder_id = await self._find_or_create_folder(
                client, token, parent_id, company,
            )

            # ── 2. Upload each artifact ──────────────────────────────
            drive_links: dict[str, str] = {
                "folder": f"https://drive.google.com/drive/folders/{folder_id}",
            }

            for artifact in ctx.all_artifacts:
                try:
                    link = await self._upload_artifact(
                        client, token, folder_id, artifact,
                    )
                    drive_links[artifact.artifact_type] = link
                    logger.info("Uploaded %s -> %s", artifact.filename, link)
                except Exception as exc:
                    logger.error(
                        "Failed to upload %s: %s", artifact.filename, exc,
                    )
                    # Continue uploading remaining artifacts

        ctx.drive_links = drive_links

        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata={"drive_links": drive_links, "folder_id": folder_id},
        )

    # ── Internal helpers ─────────────────────────────────────────────

    async def _find_or_create_folder(
        self,
        client: httpx.AsyncClient,
        token: str,
        parent_id: str,
        folder_name: str,
    ) -> str:
        """Return the ID of the company subfolder, creating it if necessary."""
        # Search for existing subfolder
        q = f"'{parent_id}' in parents and name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        resp = await client.get(
            f"{DRIVE_API}/files",
            headers=_google_headers(token),
            params={"q": q, "fields": "files(id,name)", "pageSize": 1},
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])

        if files:
            folder_id = files[0]["id"]
            logger.info("Found existing folder '%s' (%s)", folder_name, folder_id)
            return folder_id

        # Create new subfolder
        create_resp = await client.post(
            f"{DRIVE_API}/files",
            headers={
                **_google_headers(token),
                "Content-Type": "application/json",
            },
            json={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
        )
        create_resp.raise_for_status()
        folder_id = create_resp.json()["id"]
        logger.info("Created folder '%s' (%s)", folder_name, folder_id)
        return folder_id

    async def _upload_artifact(
        self,
        client: httpx.AsyncClient,
        token: str,
        folder_id: str,
        artifact: Artifact,
    ) -> str:
        """Upload a single artifact via multipart upload and return webViewLink."""
        import json as json_mod

        metadata = {
            "name": artifact.filename,
            "parents": [folder_id],
        }
        metadata_bytes = json_mod.dumps(metadata).encode("utf-8")

        file_bytes = artifact.path.read_bytes()
        mime_type = artifact.mime_type or _MIME_FALLBACK

        # Build multipart/related body manually (Drive API requirement)
        boundary = "bpo_ops_boundary_2026"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode("utf-8")
        body += metadata_bytes
        body += (
            f"\r\n--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        body += file_bytes
        body += f"\r\n--{boundary}--".encode("utf-8")

        resp = await client.post(
            f"{DRIVE_UPLOAD_API}/files",
            params={
                "uploadType": "multipart",
                "fields": "id,webViewLink",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            content=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("webViewLink", f"https://drive.google.com/file/d/{data['id']}/view")

    @staticmethod
    def _mock_drive_links(artifacts: list[Artifact]) -> dict[str, str]:
        """Return placeholder Drive links for dry-run mode."""
        links: dict[str, str] = {
            "folder": "https://drive.google.com/drive/folders/DRY_RUN_FOLDER",
        }
        for artifact in artifacts:
            links[artifact.artifact_type] = (
                f"https://drive.google.com/file/d/DRY_RUN_{artifact.artifact_type}/view"
            )
        return links

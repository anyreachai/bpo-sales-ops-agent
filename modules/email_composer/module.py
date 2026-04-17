"""Email Composer module — generates a reply email via Claude and creates a Gmail draft."""

from __future__ import annotations

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from modules._base import BaseModule
from orchestrator.config import settings
from shared.anthropic_client import call_sonnet
from shared.google_auth import get_access_token
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

COMPOSE_SYSTEM = """\
You are a professional email assistant for Richard Lin, CEO of Anyreach.
Anyreach provides AI-powered customer experience solutions for BPO companies.

Write a reply email that:
- Thanks the BPO partner for their request
- Lists the deliverables prepared with their Google Drive links
- Mentions the personalized demo if one is available
- Offers to schedule a walkthrough call
- Is professional but warm in tone
- Signs off as "Richard Lin, CEO, Anyreach"

Output ONLY the HTML email body (no subject line, no wrapping explanation).
Use clean, professional HTML with inline styles. Keep it concise.\
"""


def _build_compose_prompt(ctx: SessionContext) -> str:
    """Build the prompt that tells Claude what to include in the email."""
    bpo_name = ctx.bpo.name if ctx.bpo else "the BPO partner"
    company = ctx.target_company or "the target company"
    contact = ""
    if ctx.intake:
        contact = ctx.intake.contact_name or ""

    # Build deliverable link list
    link_lines: list[str] = []
    for artifact in ctx.all_artifacts:
        link = ctx.drive_links.get(artifact.artifact_type, "")
        label = artifact.artifact_type.replace("_", " ").title()
        if link:
            link_lines.append(f"- {label}: {link}")
        else:
            link_lines.append(f"- {label}: (attached)")

    folder_link = ctx.drive_links.get("folder", "")
    demo_link = ctx.demo_link or ""

    return f"""\
Write a reply email for this context:

BPO Partner: {bpo_name}
Recipient: {contact or "the requester"} ({ctx.raw_email.from_address})
Target Company: {company}
Original Subject: {ctx.raw_email.subject}

Deliverables prepared:
{chr(10).join(link_lines) if link_lines else "- All requested materials"}

Google Drive folder: {folder_link or "N/A"}
Personalized Demo: {demo_link or "Not yet available — demo system will send separately"}

Additional context from the original email:
{ctx.raw_email.body[:500]}
"""


class EmailComposerModule(BaseModule):
    name = "email_composer"

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        # ── 1. Generate email body via Claude ────────────────────────
        prompt = _build_compose_prompt(ctx)
        html_body = await call_sonnet(
            api_key=settings.ANTHROPIC_API_KEY,
            prompt=prompt,
            system=COMPOSE_SYSTEM,
            max_tokens=2048,
        )

        logger.info("Generated email body (%d chars)", len(html_body))

        # ── Dry-run shortcut ─────────────────────────────────────────
        if ctx.dry_run:
            return ModuleResult(
                module_name=self.name,
                status="success",
                metadata={
                    "html_body": html_body,
                    "to": ctx.raw_email.from_address,
                    "subject": f"Re: {ctx.raw_email.subject}",
                    "dry_run": True,
                },
            )

        # ── 2. Create Gmail draft ────────────────────────────────────
        token = get_access_token(
            settings.GOOGLE_OAUTH_CLIENT_ID,
            settings.GOOGLE_OAUTH_CLIENT_SECRET,
            settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        )

        draft_url = await self._create_draft(
            token=token,
            to=ctx.raw_email.from_address,
            subject=f"Re: {ctx.raw_email.subject}",
            html_body=html_body,
            in_reply_to=ctx.raw_email.message_id,
        )

        logger.info("Created Gmail draft: %s", draft_url)

        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata={
                "draft_url": draft_url,
                "to": ctx.raw_email.from_address,
                "subject": f"Re: {ctx.raw_email.subject}",
                "html_body": html_body,
            },
        )

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    async def _create_draft(
        token: str,
        to: str,
        subject: str,
        html_body: str,
        in_reply_to: str | None = None,
    ) -> str:
        """Create a Gmail draft and return the draft URL."""
        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["Subject"] = subject
        msg["From"] = "richard@anyreach.ai"

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        # Attach HTML part
        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(html_part)

        # Encode as base64url (Gmail API requirement)
        raw_bytes = msg.as_bytes()
        raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

        # POST to Gmail drafts endpoint
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GMAIL_API}/drafts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "message": {
                        "raw": raw_b64,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()

        draft_id = data.get("id", "")
        message_id = data.get("message", {}).get("id", "")
        return f"https://mail.google.com/mail/u/0/#drafts/{message_id}"

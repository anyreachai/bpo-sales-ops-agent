"""Slack Manager module — posts a delivery completion summary to Slack."""

from __future__ import annotations

import logging

import httpx

from modules._base import BaseModule
from modules.slack_manager.blocks import build_completion_blocks
from orchestrator.config import settings
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


class SlackManagerModule(BaseModule):
    name = "slack_summary"

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or "Unknown"
        bpo_name = ctx.bpo.name if ctx.bpo else "Unknown BPO"

        # ── Build Block Kit message ──────────────────────────────
        blocks = build_completion_blocks(ctx)
        fallback_text = f"Delivered: {company} for {bpo_name}"

        # Determine channel: use BPO-specific channel if configured,
        # otherwise fall back to the default notification channel.
        channel = (
            ctx.bpo.slack_channel
            if ctx.bpo and ctx.bpo.slack_channel
            else settings.SLACK_NOTIFY_CHANNEL
        )

        # ── Dry-run shortcut ─────────────────────────────────────
        if ctx.dry_run:
            return ModuleResult(
                module_name=self.name,
                status="success",
                metadata={
                    "channel": channel,
                    "blocks": blocks,
                    "text": fallback_text,
                    "dry_run": True,
                },
            )

        # ── Post to Slack ────────────────────────────────────────
        if not settings.SLACK_BOT_TOKEN:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error="SLACK_BOT_TOKEN not configured",
            )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                SLACK_POST_URL,
                headers={
                    "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "channel": channel,
                    "blocks": blocks,
                    "text": fallback_text,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if not data.get("ok"):
            error_msg = data.get("error", "unknown Slack error")
            logger.error("Slack postMessage failed: %s", error_msg)
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"Slack API error: {error_msg}",
                metadata={"slack_response": data},
            )

        message_ts = data.get("ts", "")
        logger.info("Posted Slack summary to %s (ts=%s)", channel, message_ts)

        return ModuleResult(
            module_name=self.name,
            status="success",
            metadata={
                "channel": channel,
                "message_ts": message_ts,
                "blocks": blocks,
            },
        )

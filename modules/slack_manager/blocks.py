"""Block Kit helpers for Slack summary messages."""

from __future__ import annotations

from shared.types import SessionContext


def build_completion_blocks(ctx: SessionContext) -> list[dict]:
    """Build Block Kit blocks for the delivery completion summary."""
    company = ctx.target_company or "Unknown"
    bpo_name = ctx.bpo.name if ctx.bpo else "Unknown BPO"
    folder_link = ctx.drive_links.get("folder", "")

    blocks: list[dict] = []

    # ── Header ───────────────────────────────────────────────────
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"Delivered: {company} for {bpo_name}",
            "emoji": True,
        },
    })

    # ── Drive folder link ────────────────────────────────────────
    if folder_link:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":file_folder: *Drive Folder:* <{folder_link}|Open in Drive>",
            },
        })

    # ── Deliverable links ────────────────────────────────────────
    deliverable_lines: list[str] = []
    for artifact_type, link in ctx.drive_links.items():
        if artifact_type == "folder":
            continue
        label = artifact_type.replace("_", " ").title()
        deliverable_lines.append(f"• <{link}|{label}>")

    if deliverable_lines:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Deliverables:*\n" + "\n".join(deliverable_lines),
            },
        })

    # ── Demo link (if available) ─────────────────────────────────
    if ctx.demo_link:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":rocket: *Demo:* <{ctx.demo_link}|View Demo>",
            },
        })

    # ── Pipeline and draft status ────────────────────────────────
    status_parts: list[str] = []

    tracker_result = ctx.module_results.get("pipeline_tracker")
    if tracker_result and tracker_result.status == "success":
        sheet_ok = tracker_result.metadata.get("sheet_updated", False)
        db_ok = tracker_result.metadata.get("db_updated", False)
        parts = []
        if sheet_ok:
            parts.append("Sheet updated")
        if db_ok:
            parts.append("DB updated")
        status_parts.append(f":bar_chart: Pipeline: {', '.join(parts) if parts else 'tracked'}")

    composer_result = ctx.module_results.get("email_composer")
    if composer_result and composer_result.status == "success":
        draft_url = composer_result.metadata.get("draft_url", "")
        if draft_url:
            status_parts.append(f":email: Draft: <{draft_url}|Open in Gmail>")
        else:
            status_parts.append(":email: Draft created")

    if status_parts:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(status_parts),
            },
        })

    # ── Duration context ─────────────────────────────────────────
    total_duration = sum(
        r.duration_seconds
        for r in ctx.module_results.values()
        if r.duration_seconds
    )
    module_count = len([r for r in ctx.module_results.values() if r.status == "success"])

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f"Session `{ctx.session_id}` | "
                    f"{module_count} modules completed | "
                    f"Total: {total_duration:.1f}s"
                ),
            },
        ],
    })

    # ── Divider ──────────────────────────────────────────────────
    blocks.append({"type": "divider"})

    return blocks

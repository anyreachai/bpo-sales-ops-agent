import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from orchestrator.config import settings
from shared.google_auth import get_access_token
from shared.types import EmailPayload

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY_CHARS = 50_000
SKIP_SUBJECT_PREFIXES = (
    "accepted:", "declined:", "tentatively accepted:", "automatic reply:",
    "out of office:", "undeliverable:", "delivery status notification", "read:",
    "canceled:", "updated invitation:",
)

# 24-hour dedup window
_seen: dict[str, float] = {}


def _prune_seen():
    cutoff = time.time() - 86400
    stale = [k for k, v in _seen.items() if v < cutoff]
    for k in stale:
        del _seen[k]


async def poll_once() -> list[EmailPayload]:
    """Poll Gmail for unread BPO emails. Returns list of new EmailPayloads."""
    _prune_seen()

    token = get_access_token(
        settings.GOOGLE_OAUTH_CLIENT_ID,
        settings.GOOGLE_OAUTH_CLIENT_SECRET,
        settings.GOOGLE_OAUTH_REFRESH_TOKEN,
    )

    query = "is:unread (" + " OR ".join(
        f"from:@{d}" for d in settings.bpo_domain_list
    ) + ")"

    emails = []
    async with httpx.AsyncClient(timeout=30) as client:
        # List unread messages
        resp = await client.get(
            f"{GMAIL_API}/messages",
            params={"q": query, "maxResults": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error(f"Gmail list failed: {resp.status_code} {resp.text[:500]}")
            return []

        message_ids = [m["id"] for m in resp.json().get("messages", [])]
        if not message_ids:
            logger.debug("No unread BPO emails")
            return []

        logger.info(f"Found {len(message_ids)} unread BPO emails")

        for mid in message_ids:
            if mid in _seen:
                continue
            _seen[mid] = time.time()

            # Fetch message detail
            detail_resp = await client.get(
                f"{GMAIL_API}/messages/{mid}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if detail_resp.status_code != 200:
                logger.error(f"Failed to fetch message {mid}")
                continue

            msg = detail_resp.json()
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

            subject = headers.get("subject", "")
            from_addr = headers.get("from", "")
            cc = [c.strip() for c in headers.get("cc", "").split(",") if c.strip()]

            # Skip non-request emails
            if subject.lower().startswith(SKIP_SUBJECT_PREFIXES):
                logger.info(f"Skipping: {subject}")
                # Mark as read
                await client.post(
                    f"{GMAIL_API}/messages/{mid}/modify",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"removeLabelIds": ["UNREAD"]},
                )
                continue

            # Extract body
            body = _extract_body(msg.get("payload", {}))

            email = EmailPayload(
                from_address=from_addr,
                subject=subject,
                body=body[:MAX_BODY_CHARS],
                message_id=mid,
                cc=cc,
            )
            emails.append(email)

            # Mark as read
            await client.post(
                f"{GMAIL_API}/messages/{mid}/modify",
                headers={"Authorization": f"Bearer {token}"},
                json={"removeLabelIds": ["UNREAD"]},
            )

            logger.info(f"New BPO email: {from_addr} — {subject}")

    return emails


def _extract_body(payload: dict) -> str:
    """Extract text body from Gmail message payload, handling multipart."""
    import base64

    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain" and "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Fallback to HTML
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                # Strip HTML tags (basic)
                import re
                return re.sub(r'<[^>]+>', ' ', html).strip()
            # Recurse into nested multipart
            if part.get("mimeType", "").startswith("multipart/"):
                result = _extract_body(part)
                if result:
                    return result

    # Last resort: check body.data directly
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return ""

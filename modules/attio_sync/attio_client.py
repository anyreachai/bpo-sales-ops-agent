"""Thin Attio API client for the attio_sync module."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

ATTIO_BASE = "https://api.attio.com/v2"
COMPANIES_ASSERT_URL = f"{ATTIO_BASE}/objects/companies/records?matching_attribute=domains"
CONNECTOR_SLUG = "connector_bpo_channel_partner"


def extract_domain(url: str | None) -> str | None:
    """Best-effort extract the registrable host from a free-form URL.

    Returns lowercase host with leading 'www.' removed. None if input is empty.
    """
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    host = (parsed.netloc or parsed.path).lower().strip("/")
    host = host.split("/", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def build_assert_payload(
    *,
    name: str,
    domain: str,
    connector_record_id: str,
) -> dict:
    """Build the body for Attio's PUT /objects/companies/records assert call.

    Uses the connector_bpo_channel_partner record-reference attribute to point
    at the BPO's own Attio Company record.
    """
    return {
        "data": {
            "values": {
                "name": [{"value": name}],
                "domains": [{"domain": domain}],
                CONNECTOR_SLUG: [
                    {
                        "target_object": "companies",
                        "target_record_id": connector_record_id,
                    }
                ],
            }
        }
    }


async def assert_company(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    name: str,
    domain: str,
    connector_record_id: str,
) -> dict:
    """Upsert a Company record by domain, setting the connector relationship.

    Returns the parsed JSON response. Raises httpx.HTTPStatusError on 4xx/5xx.
    """
    payload = build_assert_payload(
        name=name,
        domain=domain,
        connector_record_id=connector_record_id,
    )
    resp = await client.put(
        COMPANIES_ASSERT_URL,
        headers=_headers(api_key),
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()

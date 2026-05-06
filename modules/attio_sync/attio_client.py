"""Thin Attio API client for the attio_sync module."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

ATTIO_BASE = "https://api.attio.com/v2"
COMPANIES_ASSERT_URL = f"{ATTIO_BASE}/objects/companies/records?matching_attribute=domains"
COMPANY_RECORD_URL = f"{ATTIO_BASE}/objects/companies/records/{{record_id}}"
CONNECTOR_SLUG = "connector_bpo_channel_partner"
BPO_REFERRED_SLUG = "bpo_referred_account"


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


def extract_company_record_id(assert_response: dict | None) -> str | None:
    """Pull the company record_id out of an assert_company response."""
    if not isinstance(assert_response, dict):
        return None
    record_id = (
        assert_response.get("data", {}).get("id", {}).get("record_id")
    )
    return record_id if isinstance(record_id, str) else None


async def fetch_company(
    client: httpx.AsyncClient,
    api_key: str,
    record_id: str,
) -> dict:
    """GET a single Company record by ID. Raises HTTPStatusError on 4xx/5xx."""
    url = COMPANY_RECORD_URL.format(record_id=record_id)
    resp = await client.get(url, headers=_headers(api_key))
    resp.raise_for_status()
    return resp.json()


def extract_referred_ids(company_record: dict | None) -> set[str]:
    """Read the bpo_referred_account multi-select from a company record GET response.

    Attio returns multiselect record-references under
    ``data.values.<slug>`` as a list of entries; each entry typically carries
    ``target_record_id`` (write shape) or a nested
    ``target_object.record_id`` (read shape). Try both.
    Returns an empty set if the attribute is absent or unrecognized.
    """
    if not isinstance(company_record, dict):
        return set()
    values = company_record.get("data", {}).get("values", {})
    raw = values.get(BPO_REFERRED_SLUG)
    if not isinstance(raw, list):
        return set()

    out: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        # Write-shape: {"target_object": "...", "target_record_id": "..."}
        rid = entry.get("target_record_id")
        if isinstance(rid, str) and rid:
            out.add(rid)
            continue
        # Read-shape: {"target_object": {"record_id": "..."}, ...}
        target = entry.get("target_object")
        if isinstance(target, dict):
            nested = target.get("record_id")
            if isinstance(nested, str) and nested:
                out.add(nested)
                continue
        # Some Attio responses nest under "target_record"
        nested_alt = entry.get("target_record")
        if isinstance(nested_alt, dict):
            rid_alt = nested_alt.get("record_id")
            if isinstance(rid_alt, str) and rid_alt:
                out.add(rid_alt)
    return out


def build_referred_patch_payload(prospect_record_ids: set[str]) -> dict:
    """Body for PATCH /objects/companies/records/{id} that replaces the
    bpo_referred_account multi-select with the given full list."""
    return {
        "data": {
            "values": {
                BPO_REFERRED_SLUG: [
                    {
                        "target_object": "companies",
                        "target_record_id": rid,
                    }
                    for rid in sorted(prospect_record_ids)
                ]
            }
        }
    }


async def patch_bpo_referred_account(
    client: httpx.AsyncClient,
    api_key: str,
    bpo_record_id: str,
    all_prospect_ids: set[str],
) -> dict:
    """Replace the BPO's bpo_referred_account list with the given full set.

    Caller is responsible for merging existing + new IDs before calling
    (this function is replace-semantics; never mutates partial state).
    """
    url = COMPANY_RECORD_URL.format(record_id=bpo_record_id)
    payload = build_referred_patch_payload(all_prospect_ids)
    resp = await client.patch(url, headers=_headers(api_key), json=payload)
    resp.raise_for_status()
    return resp.json()

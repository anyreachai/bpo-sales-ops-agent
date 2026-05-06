"""Sheet → Attio sync for ``bpo_referred_account``.

Reads parsed pipeline-tracker rows for one BPO, asserts each prospect
company in Attio by domain, then appends any newly-asserted prospect
record_ids onto the BPO's ``bpo_referred_account`` multi-select.

Append-only by design: never removes existing entries, even if a row
disappears from the sheet. This protects hand-curated entries (e.g.
EGS's 119 historic links) and tolerates accidental sheet edits.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from modules.attio_sync.attio_client import (
    BPO_REFERRED_SLUG,
    assert_company,
    build_referred_patch_payload,
    extract_company_record_id,
    extract_domain,
    extract_referred_ids,
    fetch_company,
    patch_bpo_referred_account,
)
from orchestrator.config import settings

logger = logging.getLogger(__name__)


def _row_company_name(row: dict) -> str:
    return (row.get("company") or "").strip()


def _row_domain(row: dict) -> str | None:
    return extract_domain(row.get("website_url") or row.get("website") or "")


async def sync_bpo_referred_accounts(
    bpo_key: str,
    bpo_attio_record_id: str | None,
    parsed_rows: list[dict],
) -> dict[str, Any]:
    """For one BPO: append all eligible prospects from the sheet to its
    Attio ``bpo_referred_account`` list.

    Returns a counter dict — never raises. Errors are logged and surfaced
    in ``errors`` so a single Attio failure can't break the caller.
    """
    result: dict[str, Any] = {
        "bpo_key": bpo_key,
        "skipped_no_company": 0,
        "skipped_no_domain": 0,
        "asserted": 0,
        "assert_errors": 0,
        "added": 0,
        "existing": 0,
        "patched": False,
        "errors": [],
        "skipped_reason": None,
    }

    if not settings.ATTIO_SYNC_ENABLED:
        result["skipped_reason"] = "ATTIO_SYNC_ENABLED is False"
        return result
    if not settings.ATTIO_API_KEY:
        result["skipped_reason"] = "ATTIO_API_KEY not configured"
        return result
    if not bpo_attio_record_id:
        result["skipped_reason"] = "BPO has no attio_record_id"
        return result
    if not parsed_rows:
        result["skipped_reason"] = "no rows to sync"
        return result

    # ── 1. Filter rows ────────────────────────────────────────────────
    eligible: list[tuple[str, str]] = []  # (name, domain)
    for row in parsed_rows:
        name = _row_company_name(row)
        if not name:
            result["skipped_no_company"] += 1
            continue
        domain = _row_domain(row)
        if not domain:
            result["skipped_no_domain"] += 1
            continue
        eligible.append((name, domain))

    if not eligible:
        result["skipped_reason"] = "no eligible rows after filter"
        return result

    if settings.DRY_RUN:
        # In dry-run, report what would happen but issue zero HTTP calls.
        logger.info(
            "[bpo_sheet_sync] DRY-RUN %s: would assert %d prospect(s) and "
            "PATCH bpo_referred_account on %s",
            bpo_key, len(eligible), bpo_attio_record_id,
        )
        result["skipped_reason"] = "DRY_RUN"
        result["dry_run"] = True
        result["would_assert"] = [
            {"name": n, "domain": d} for n, d in eligible
        ]
        result["would_patch_bpo"] = bpo_attio_record_id
        return result

    api_key = settings.ATTIO_API_KEY

    # ── 2. Assert each prospect; collect their record_ids ─────────────
    new_ids: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as client:
        for name, domain in eligible:
            try:
                response = await assert_company(
                    client,
                    api_key,
                    name=name,
                    domain=domain,
                    connector_record_id=bpo_attio_record_id,
                )
            except httpx.HTTPStatusError as exc:
                result["assert_errors"] += 1
                result["errors"].append(
                    f"assert {domain}: HTTP {exc.response.status_code}"
                )
                continue
            except Exception as exc:  # noqa: BLE001
                result["assert_errors"] += 1
                result["errors"].append(f"assert {domain}: {exc}")
                continue

            rid = extract_company_record_id(response)
            if rid:
                new_ids.add(rid)
                result["asserted"] += 1
            else:
                result["assert_errors"] += 1
                result["errors"].append(
                    f"assert {domain}: no record_id in response"
                )

        # ── 3. Read current bpo_referred_account on the BPO ───────────
        try:
            bpo_record = await fetch_company(client, api_key, bpo_attio_record_id)
        except httpx.HTTPStatusError as exc:
            result["errors"].append(
                f"fetch BPO: HTTP {exc.response.status_code}"
            )
            return result
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"fetch BPO: {exc}")
            return result

        existing_ids = extract_referred_ids(bpo_record)
        result["existing"] = len(existing_ids)

        # ── 4. Compute append-only diff ───────────────────────────────
        to_add = new_ids - existing_ids
        if not to_add:
            logger.info(
                "[bpo_sheet_sync] %s no-op: %d asserted, %d already linked",
                bpo_key, len(new_ids), len(existing_ids),
            )
            return result

        # ── 5. PATCH BPO with merged full list (append semantics) ────
        merged = existing_ids | new_ids
        try:
            await patch_bpo_referred_account(
                client, api_key, bpo_attio_record_id, merged,
            )
        except httpx.HTTPStatusError as exc:
            result["errors"].append(
                f"patch BPO: HTTP {exc.response.status_code}"
            )
            return result
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"patch BPO: {exc}")
            return result

    result["patched"] = True
    result["added"] = len(to_add)
    logger.info(
        "[bpo_sheet_sync] %s: +%d prospect(s) appended to bpo_referred_account "
        "(existing=%d, asserted=%d, errors=%d)",
        bpo_key, result["added"], result["existing"],
        result["asserted"], result["assert_errors"],
    )
    return result


__all__ = [
    "sync_bpo_referred_accounts",
    "BPO_REFERRED_SLUG",
    "build_referred_patch_payload",
]

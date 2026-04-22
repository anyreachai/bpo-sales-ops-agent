"""OpenAI Deep Research module — runs in parallel with Claude deep_research.

Submits a background research job to OpenAI's deep research models and polls
until complete. Produces a markdown artifact with sourced intelligence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from modules._base import BaseModule
from orchestrator.config import settings
from shared.storage import artifact_path
from shared.types import Artifact, ModuleResult, SessionContext

logger = logging.getLogger(__name__)

O4_MINI_MODEL = "o4-mini-deep-research-2025-06-26"
O3_MODEL = "o3-deep-research-2025-06-26"
POLL_INTERVAL = 15
POLL_TIMEOUT = 2400

RESEARCH_PROMPT = """Produce a comprehensive Company Intelligence Dossier on: {target}

Research this company thoroughly across all available public sources.

## 1. COMPANY OVERVIEW
| Field | Detail |
|-------|--------|
| Company Name | |
| Website | |
| Industry / Vertical | |
| Founded | |
| Headquarters | |
| Employees | |
| Revenue | |
| Ownership | |
| Funding | |

## 2. STRATEGIC CONTEXT
Recent events (last 12-18 months): acquisitions, partnerships, leadership changes, funding, rebrands, layoffs, product launches, AI initiatives. Include dates and sources.

## 3. BUSINESS MODEL & REVENUE STREAMS
How they make money. Key products, services, customer segments, pricing, geography, named customers.

## 4. TECHNOLOGY & AI POSTURE
Tech stack, AI/automation investments, digital transformation status, key vendor relationships.

## 5. PAIN POINTS & AI VOICE/CHAT OPPORTUNITIES
Where could AI voice agents or chat automation create value? Consider: customer service volume, sales/lead qualification, appointment scheduling, after-hours coverage, multilingual needs, cost pressure, quality gaps.

## 6. LEADERSHIP & KEY DECISION MAKERS
For each relevant executive:
- Background & career trajectory
- Psychographic profile (decision-making style, risk tolerance, motivations)
- Recommended engagement approach

## 7. COMPETITIVE LANDSCAPE
Direct competitors, differentiation, recent competitive moves, market position.

## 8. RECOMMENDED SALES STRATEGY
- Best entry point (title/role)
- Messaging frame
- Timing/urgency signals
- Winning strategy (2-3 sentences)

Be specific, tactical, and evidence-based. Cite sources."""

SYSTEM_MSG = (
    "You are a strategic intelligence analyst producing a Company Intelligence "
    "Dossier for Anyreach, an AI voice and chat automation platform that sells "
    "to BPO companies, contact centers, and enterprises. Be evidence-based — cite sources."
)


class OpenAIResearchModule(BaseModule):
    name = "openai_research"

    def should_run(self, ctx: SessionContext) -> bool:
        if not settings.OPENAI_API_KEY:
            return False
        return "deep_research" in ctx.deliverables_requested

    async def run(self, ctx: SessionContext) -> ModuleResult:
        company = ctx.target_company or ""
        url = ctx.target_url or ""
        target = f"{company} ({url})" if company else url

        prompt = RESEARCH_PROMPT.format(target=target)

        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        start = time.time()
        logger.info("Submitting OpenAI deep research for %s", target)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json={
                    "model": O4_MINI_MODEL,
                    "input": [
                        {"role": "developer", "content": [{"type": "input_text", "text": SYSTEM_MSG}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                    ],
                    "tools": [{"type": "web_search_preview"}],
                    "background": True,
                },
            )

        if resp.status_code != 200:
            return ModuleResult(
                module_name=self.name,
                status="failed",
                error=f"OpenAI API error: {resp.status_code} {resp.text[:300]}",
            )

        response_id = resp.json()["id"]
        logger.info("OpenAI research submitted — id=%s", response_id)

        content, annotations, usage = await self._poll_until_done(
            response_id, headers
        )
        duration = round(time.time() - start, 1)

        out_path = artifact_path(ctx.session_id, company or "company", "openai_research", "md")
        out_path.write_text(content, encoding="utf-8")

        artifact = Artifact(
            filename=out_path.name,
            path=out_path,
            artifact_type="deep_research",
            mime_type="text/markdown",
            size_bytes=out_path.stat().st_size,
        )

        return ModuleResult(
            module_name=self.name,
            status="success",
            artifacts=[artifact],
            metadata={
                "openai_response_id": response_id,
                "model": O4_MINI_MODEL,
                "duration_seconds": duration,
                "content_length": len(content),
                "sources": len(annotations),
                "usage": usage,
                "annotations": annotations[:20],
            },
        )

    async def _poll_until_done(
        self, response_id: str, headers: dict
    ) -> tuple[str, list[dict], dict]:
        elapsed = 0.0
        start = time.time()
        async with httpx.AsyncClient(timeout=60) as client:
            while elapsed < POLL_TIMEOUT:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed = time.time() - start

                resp = await client.get(
                    f"https://api.openai.com/v1/responses/{response_id}",
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.warning("OpenAI poll error: %d", resp.status_code)
                    continue

                data = resp.json()
                status = data.get("status", "")

                if status == "completed":
                    content = data.get("output_text", "")
                    annotations = self._extract_annotations(data)
                    usage = data.get("usage", {})
                    return content, annotations, {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    }

                if status == "failed":
                    raise RuntimeError(f"OpenAI deep research failed: {data.get('error', 'unknown')}")
                if status == "cancelled":
                    raise RuntimeError("OpenAI deep research was cancelled")

        raise TimeoutError(f"OpenAI deep research timed out after {POLL_TIMEOUT}s")

    @staticmethod
    def _extract_annotations(data: dict) -> list[dict]:
        annotations = []
        for item in data.get("output", []):
            for block in item.get("content") or []:
                for ann in block.get("annotations") or []:
                    if ann.get("url"):
                        annotations.append({"url": ann["url"], "title": ann.get("title", "")})
        return annotations

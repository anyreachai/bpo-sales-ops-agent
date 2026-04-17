from __future__ import annotations

import asyncio
import logging
from enum import Enum

from orchestrator.registry import get as get_module
from shared.types import ModuleResult, SessionContext

logger = logging.getLogger(__name__)


class Phase(Enum):
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"


NODES: dict[str, dict] = {
    "classifier":        {"deps": [],                                                       "phase": Phase.PHASE_1},
    "brand_extractor":   {"deps": ["classifier"],                                           "phase": Phase.PHASE_1},
    "demo_generator":    {"deps": ["classifier"],                                           "phase": Phase.PHASE_1},
    "deep_research":     {"deps": ["classifier"],                                           "phase": Phase.PHASE_1},
    "stakeholder_intel": {"deps": ["classifier"],                                           "phase": Phase.PHASE_1},
    "cx_intel":          {"deps": ["classifier"],                                           "phase": Phase.PHASE_1},
    "deck_generator":    {"deps": ["brand_extractor", "deep_research",
                                    "stakeholder_intel", "cx_intel"],                        "phase": Phase.PHASE_1},
    "drive_manager":     {"deps": ["deck_generator"],                                       "phase": Phase.PHASE_2},
    "pipeline_tracker":  {"deps": ["drive_manager"],                                        "phase": Phase.PHASE_2},
    "email_composer":    {"deps": ["drive_manager"],                                        "phase": Phase.PHASE_2},
    "slack_summary":     {"deps": ["pipeline_tracker", "email_composer"],                   "phase": Phase.PHASE_2},
}


class DAGRunner:
    def __init__(self, ctx: SessionContext):
        self.ctx = ctx
        self.completed: set[str] = set()
        self.results: dict[str, ModuleResult] = {}

    async def run_phase(self, phase: Phase) -> dict[str, ModuleResult]:
        phase_nodes = {k: v for k, v in NODES.items() if v["phase"] == phase}

        while len(self.completed & set(phase_nodes)) < len(phase_nodes):
            ready = [
                name for name, node in phase_nodes.items()
                if name not in self.completed
                and all(d in self.completed for d in node["deps"])
            ]
            if not ready:
                break

            logger.info(f"Running parallel batch: {ready}")
            tasks = [self._run_node(name) for name in ready]
            await asyncio.gather(*tasks)

        return {k: v for k, v in self.results.items() if NODES.get(k, {}).get("phase") == phase}

    async def _run_node(self, name: str) -> None:
        module = get_module(name)
        result = await module.execute(self.ctx)
        self.results[name] = result
        self.ctx.module_results[name] = result

        if result.status == "success":
            self.ctx.all_artifacts.extend(result.artifacts)

        self.completed.add(name)

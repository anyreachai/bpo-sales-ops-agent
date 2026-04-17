from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from shared.types import ModuleResult, SessionContext


class BaseModule(ABC):
    name: str = ""

    @abstractmethod
    async def run(self, ctx: SessionContext) -> ModuleResult: ...

    def should_run(self, ctx: SessionContext) -> bool:
        return True

    async def execute(self, ctx: SessionContext) -> ModuleResult:
        logger = logging.getLogger(f"module.{self.name}")
        if not self.should_run(ctx):
            logger.info(f"Skipping {self.name}")
            return ModuleResult(module_name=self.name, status="skipped")

        start = time.time()
        try:
            logger.info(f"Starting {self.name}")
            result = await self.run(ctx)
            result.duration_seconds = time.time() - start
            logger.info(f"Completed {self.name} in {result.duration_seconds:.1f}s — {result.status}")
            return result
        except Exception as e:
            logger.exception(f"Failed {self.name}")
            return ModuleResult(
                module_name=self.name,
                status="failed",
                duration_seconds=time.time() - start,
                error=str(e),
            )

"""Base class for pipeline steps."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict

from app.services.ai.pipeline.context import PipelineContext


class BasePipelineStep(ABC):
    """Abstract base class for all turn pipeline steps."""

    @abstractmethod
    async def run(
        self, context: PipelineContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute step logic, optionally yielding SSE events/chunks."""
        if False:
            yield {}

"""Inject tool_choice on the first model call of a repair round."""

from __future__ import annotations

import inspect
from typing import Any

from app.services.ai.runtime.agentscope.tool_choice_compat import (
    tool_choice_for_model,
)


class ForcedFirstToolChoiceModel:
    def __init__(self, inner: Any, tool_choice: Any):
        self._inner = inner
        self._tool_choice = tool_choice_for_model(inner, tool_choice)
        self._consumed = False

    @property
    def formatter(self) -> Any:
        if hasattr(self._inner, "formatter"):
            return self._inner.formatter
        return type("DefaultFormatter", (), {"supported_input_media_types": ["image", "text"]})()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self._consumed and self._tool_choice is not None:
            kwargs["tool_choice"] = self._tool_choice
            self._consumed = True
        result = self._inner(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

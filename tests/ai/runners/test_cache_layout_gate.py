"""测试 BaseExecutor._using_cache_layout 的灰度门控与失败回退."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.executors.base import BaseExecutor
from app.services.ai.prompt_assembler import PromptLayoutConfig


class _StubExecutor(BaseExecutor):
    def __init__(self, conversation_id="c-abc"):
        super().__init__(
            config=MagicMock(),
            trace_id="trace",
            trace_buffer=[],
            conversation_id=conversation_id,
        )

    async def execute(self, history):
        yield {}
        return


def _patch_layout(mode, percent):
    cfg = PromptLayoutConfig(mode=mode, rollout_percent=percent)
    return patch(
        "app.services.ai.prompt_assembler.resolve_prompt_layout_config",
        AsyncMock(return_value=cfg),
    )


@pytest.mark.asyncio
async def test_legacy_mode_never_uses_cache_layout():
    runner = _StubExecutor()
    with _patch_layout("legacy", 100):
        assert await runner._using_cache_layout() is False


@pytest.mark.asyncio
async def test_observe_mode_never_uses_cache_layout():
    runner = _StubExecutor()
    with _patch_layout("observe", 100):
        assert await runner._using_cache_layout() is False


@pytest.mark.asyncio
async def test_enabled_high_percent_uses_cache_layout():
    runner = _StubExecutor(conversation_id="c-abc")
    with _patch_layout("enabled", 100):
        assert await runner._using_cache_layout() is True


@pytest.mark.asyncio
async def test_enabled_empty_conversation_stays_legacy():
    # 空 conversation_id 不进入灰度，避免非粘性 rollout 决策。
    runner = _StubExecutor(conversation_id="")
    with _patch_layout("enabled", 100):
        assert await runner._using_cache_layout() is False


@pytest.mark.asyncio
async def test_config_read_exception_fails_closed():
    runner = _StubExecutor(conversation_id="c-abc")
    with patch(
        "app.services.ai.prompt_assembler.resolve_prompt_layout_config",
        AsyncMock(side_effect=RuntimeError("config down")),
    ):
        assert await runner._using_cache_layout() is False
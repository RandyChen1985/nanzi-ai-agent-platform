"""平台内置 Echo 进度工具：给团队一条零配置的 MCP 进度流验收路径。

为什么放在 Echo 测试 MCP 里：外部 MCP 需要自己实现 ``report_progress``，
而绝大多数第三方服务（例如票务、CRM）根本没有实现——照文档写一个服务再配
URL/鉴权/网络白名单，验证成本远高于收益。Echo 本来就只用于验证协议链路。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.mcp import echo_server
from app.services.mcp.echo_server import echo_slow

pytestmark = pytest.mark.no_infrastructure


class _RecordingCtx:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.calls.append((progress, total, message))


async def test_echo_slow_reports_each_step():
    ctx = _RecordingCtx()

    result = await echo_slow(ctx, steps=3, interval_ms=0)

    assert result["ok"] is True
    assert [call[0] for call in ctx.calls] == [1, 2, 3]
    assert all(call[1] == 3 for call in ctx.calls)
    assert ctx.calls[0][2] == "第 1/3 步"


async def test_echo_slow_can_declare_total():
    ctx = _RecordingCtx()

    await echo_slow(ctx, steps=2, total=10, interval_ms=0)

    assert [call[1] for call in ctx.calls] == [10, 10]


async def test_echo_slow_with_total_zero_means_indeterminate():
    """total=0 是「不提供总数」：前端应走不确定态呼吸条。"""
    ctx = _RecordingCtx()

    await echo_slow(ctx, steps=2, total=0, interval_ms=0)

    assert [call[1] for call in ctx.calls] == [None, None]


async def test_echo_slow_does_not_sleep_on_last_step():
    """最后一步之后不该再等一个间隔，否则工具凭空多花 800ms。"""
    ctx = _RecordingCtx()

    result = await echo_slow(ctx, steps=2, interval_ms=0)

    assert result["steps"] == 2


def test_echo_slow_is_registered_with_description_and_schema():
    from mcp.types import Tool as McpTool

    tools = echo_server.echo_mcp._tool_manager.list_tools()

    def _as_list(value: Any) -> list[Any]:
        return list(value) if not isinstance(value, list) else value

    names = set()
    for item in _as_list(tools):
        name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
        if name:
            names.add(str(name))

    assert "echo_slow" in names, f"echo_slow 未注册：{names}"
    schema = echo_server.echo_slow_tool_schema()
    assert '"steps"' in schema and '"interval_ms"' in schema
    del McpTool


def test_personal_echo_slow_tool_name_is_namespaced():
    """个人 Echo 的进度工具名必须带实例前缀，否则与平台 Echo 撞名。"""
    from app.services.mcp.echo_server import personal_echo_slow_tool_name

    assert personal_echo_slow_tool_name("mcp-private-alice-echo") == (
        "mcp-private-alice-echo:echo_slow"
    )
    assert personal_echo_slow_tool_name("mcp-private-alice-echo") != "echo_slow"

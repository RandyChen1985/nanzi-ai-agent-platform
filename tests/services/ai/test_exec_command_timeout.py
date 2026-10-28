"""``exec_command`` 的超时契约。

## 背景

这个工具原先写死 30 秒：``run_shell_command_capture(command, ..., timeout_seconds=30.0)``，
与平台「工具调用超时」配置（``agent_max_toolcall_timeout``，默认 180 秒、可配 1–3600）
完全脱钩。管理员把上限调到 300 秒，长命令仍然在第 30 秒被砍，错误文案还写死
「最大限制 30 秒」，与实际配置对不上。

## 现在的契约

* 内层超时**跟随** ``agent_max_toolcall_timeout``；
* 比外层 ``RuntimeToolSpec`` 的 deadline 少 0.5 秒——两层几乎同时开始计时，数值相同时
  外层会先到，模型只能看到泛化的「工具调用超时」；留一点余量让「命令执行超时（上限 N 秒）」
  这条更具体的错误先返回；
* 下限 1 秒，避免配置异常时把命令立刻砍掉；
* 配置读取失败回退 30 秒（保守，不放宽）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ai.tools import system_executive_tools as exec_tools

pytestmark = pytest.mark.no_infrastructure


def _configured(value):
    async def _fake() -> float:
        return float(value)

    return _fake


@pytest.fixture(autouse=True)
def _clear_timeout_cache():
    exec_tools._exec_command_timeout_cache = None
    yield
    exec_tools._exec_command_timeout_cache = None


# --- 超时解析 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_follows_configured_tool_timeout(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _configured(12),
    )

    # 比外层 deadline 少 0.5 秒，保证内层更具体的错误先返回
    assert await exec_tools._resolve_exec_command_timeout() == 11.5


@pytest.mark.asyncio
async def test_timeout_has_one_second_floor(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _configured(0.2),
    )

    assert await exec_tools._resolve_exec_command_timeout() == 1.0


@pytest.mark.asyncio
async def test_timeout_falls_back_to_thirty_seconds_on_config_failure(monkeypatch):
    async def _boom() -> float:
        raise RuntimeError("config down")

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _boom,
    )

    assert await exec_tools._resolve_exec_command_timeout() == 30.0


@pytest.mark.asyncio
async def test_resolved_timeout_is_cached_between_calls(monkeypatch):
    calls = {"n": 0}

    async def _fake() -> float:
        calls["n"] += 1
        return 12.0

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _fake,
    )

    await exec_tools._resolve_exec_command_timeout()
    await exec_tools._resolve_exec_command_timeout()

    # 一轮对话里 exec_command 可能被调几十次，不能每次都去读配置
    assert calls["n"] == 1


# --- 真实执行 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_message_reports_configured_limit(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _configured(1),
    )

    result = await exec_tools.exec_command.ainvoke({"command": "sleep 5"})

    assert "已强制终止命令进程" in result
    assert "上限 1 秒" in result


@pytest.mark.asyncio
async def test_exec_command_passes_resolved_timeout_to_runner(monkeypatch):
    captured: dict[str, float] = {}

    async def _fake_capture(command, *, cwd=None, timeout_seconds=30.0):
        captured["timeout"] = timeout_seconds
        return SimpleNamespace(
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
        )

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        _configured(60),
    )
    monkeypatch.setattr(exec_tools, "run_shell_command_capture", _fake_capture)

    result = await exec_tools.exec_command.ainvoke({"command": "echo hi"})

    assert captured["timeout"] == 59.5
    assert "ok" in result

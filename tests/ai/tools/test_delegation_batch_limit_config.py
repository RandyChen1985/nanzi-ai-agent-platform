"""委派上限配置化（`delegation_max_batch_calls` / `delegation_max_calls_per_agent`）。

这两个值原本是硬编码常量：4 个并发子智能体 = 4 个完整会话同时跑，不同规模的部署
需要不同上限。配置化以后有三条不能退化的行为：

1. 默认值与范围校验（1-20 / 1-10），非法值必须拒绝而不是静默截断；
2. 保存配置后立即热更新，并且工具描述同步变化（模型看到的约束要等于实际校验）；
3. 配置读不到/值损坏时不能把委派打挂，保持当前值继续工作。
"""

from __future__ import annotations

import json

import pytest

from app.services.ai.tools import agent_delegate_tool as tool

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _reset_limits():
    tool.set_max_batch_delegation_calls(4)
    tool.set_max_delegation_calls_per_agent(2)
    yield
    tool.set_max_batch_delegation_calls(4)
    tool.set_max_delegation_calls_per_agent(2)


def test_defaults_match_documented_values():
    assert tool.get_max_batch_delegation_calls() == 4
    assert tool.get_max_delegation_calls_per_agent() == 2


@pytest.mark.parametrize("value", [0, -1, 21, 100, "abc", "", None])
def test_batch_limit_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        tool.validate_max_batch_delegation_calls(value)


@pytest.mark.parametrize("value", [0, -1, 11, "abc", ""])
def test_per_agent_limit_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        tool.validate_max_delegation_calls_per_agent(value)


def test_limits_accept_bounds_and_numeric_strings():
    assert tool.validate_max_batch_delegation_calls("1") == 1
    assert tool.validate_max_batch_delegation_calls(20) == 20
    assert tool.validate_max_delegation_calls_per_agent("1") == 1
    assert tool.validate_max_delegation_calls_per_agent(10) == 10


def test_set_updates_tool_description():
    """模型读的是工具描述：上限改了描述不改，模型会继续按旧值调用。"""
    tool.set_max_batch_delegation_calls(8)
    tool.set_max_delegation_calls_per_agent(3)

    assert "1-8" in tool.BatchSubAgentTool.description
    assert "最多调用 3 次" in tool.BatchSubAgentTool.description
    assert "最多 8 个" in tool.BatchSubAgentArgs.model_fields["calls"].description


def test_schema_rejects_above_absolute_limit():
    """schema 层的绝对上限仍然存在，避免异常入参把 4 路并发放大到不可控。"""
    calls = [{"agent_name": f"a{i}", "query": "q"} for i in range(21)]
    with pytest.raises(Exception):
        tool.BatchSubAgentArgs.model_validate({"calls": calls})


async def test_ainvoke_rejects_over_configured_limit(monkeypatch):
    """配置上限小于 schema 绝对上限时，以配置为准（返回明确错误而不是执行）。"""

    async def no_refresh():
        tool.set_max_batch_delegation_calls(2)

    monkeypatch.setattr(tool, "refresh_delegation_limits_from_config", no_refresh)

    payload = json.loads(
        await tool.BatchSubAgentTool().ainvoke(
            {
                "calls": [
                    {"agent_name": "a", "query": "q1"},
                    {"agent_name": "b", "query": "q2"},
                    {"agent_name": "c", "query": "q3"},
                ]
            }
        )
    )

    assert payload["status"] == "error"
    assert "最多 2 个" in payload["error"]


async def test_refresh_reads_limits_from_config(monkeypatch):
    from app.services.config_service import ConfigService

    async def fake_get(key, default=None):
        return {
            "delegation_max_batch_calls": "6",
            "delegation_max_calls_per_agent": "3",
        }.get(key, default)

    monkeypatch.setattr(ConfigService, "get", staticmethod(fake_get))

    await tool.refresh_delegation_limits_from_config()

    assert tool.get_max_batch_delegation_calls() == 6
    assert tool.get_max_delegation_calls_per_agent() == 3


async def test_refresh_keeps_current_value_on_bad_config(monkeypatch):
    from app.services.config_service import ConfigService

    async def fake_get(key, default=None):
        return "999"  # 超出 1-20，必须拒绝并保持原值

    monkeypatch.setattr(ConfigService, "get", staticmethod(fake_get))

    await tool.refresh_delegation_limits_from_config()

    assert tool.get_max_batch_delegation_calls() == 4


async def test_refresh_survives_config_service_failure(monkeypatch):
    from app.services.config_service import ConfigService

    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ConfigService, "get", staticmethod(boom))

    await tool.refresh_delegation_limits_from_config()

    assert tool.get_max_batch_delegation_calls() == 4
    assert tool.get_max_delegation_calls_per_agent() == 2

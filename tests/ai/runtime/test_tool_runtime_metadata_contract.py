"""工具运行时元数据的取值契约。

``runtime_metadata`` 是可选 duck-typed 属性，读取方必须容忍非 Mapping 值：
一次脏属性不该让整轮 Agent 执行以 TypeError 结束。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope.tools import (
    _tool_runtime_metadata,
    runtime_tool_spec_from_legacy_tool,
)

pytestmark = pytest.mark.no_infrastructure


def test_runtime_metadata_returns_a_copy_of_mapping():
    tool = SimpleNamespace(runtime_metadata={"script_tool_config": {"name": "x"}})

    metadata = _tool_runtime_metadata(tool)

    assert metadata == {"script_tool_config": {"name": "x"}}
    metadata["injected"] = True
    assert "injected" not in tool.runtime_metadata, "必须返回副本，不能改写工具自身状态"


def test_runtime_metadata_treats_non_mapping_as_empty():
    """AsyncMock / 字符串 / 自定义对象都当空，不能抛 TypeError。"""
    for raw in (AsyncMock(), "not-a-mapping", object()):
        assert _tool_runtime_metadata(SimpleNamespace(runtime_metadata=raw)) == {}


def test_runtime_metadata_handles_missing_attribute():
    assert _tool_runtime_metadata(object()) == {}


def test_legacy_tool_with_non_mapping_metadata_still_builds_spec():
    """端到端：AsyncMock 工具（未显式设置 runtime_metadata）也能转成 RuntimeToolSpec。"""
    tool = AsyncMock()
    tool.name = "test_tool"
    tool.description = "Test tool"
    tool.args_schema = None

    spec = runtime_tool_spec_from_legacy_tool(tool, source_type="system")

    assert spec.name == "test_tool"
    assert spec.metadata == {}

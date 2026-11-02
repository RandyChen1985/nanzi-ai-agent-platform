"""运行时状态指纹的稳定性。

指纹决定「上一轮的运行时状态还能不能复用」。它原先按**本轮实际可用的工具**计算，
而工具集会被两条按内容裁剪的逻辑改动：

- ``filter_tools_for_reusable_result``：命中可复用结果时移除事实获取工具；
- ``filter_redundant_time_tools``：system prompt 已带时间锚点时移除取时工具。

于是「追问上一条结果」的下一轮会被误判成配置变更，丢掉上下文（含完整工具结果）与
压缩摘要。更麻烦的是正常轮次会裁剪、挂起恢复路径不裁剪，同一份配置算出两个指纹，
必然互相判为不一致。

现在指纹只描述「配置层工具集」：剔除会被内容裁剪掉的名字，剩下的只随
模型 / 智能体版本 / 工具绑定变化。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai.runtime.agentscope.agent_runtime import (
    _content_trimmed_tool_names,
    build_tools_fingerprint,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]


def _spec(name: str) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        name=name,
        description="",
        parameters_schema={},
        source_type="builtin",
        callable=lambda **kwargs: None,
    )


class _Config:
    agent_name = "main"
    agent_version = "v1"
    model_name = "deepseek-v4-flash"


STABLE_TOOLS = ["Bash", "Read", "Write", "my_mcp_tool"]
TRIMMED_TOOLS = ["execute_sql_query", "search_knowledge_base", "get_current_time"]


def test_trimmed_tool_names_covers_both_filters():
    names = _content_trimmed_tool_names()
    # 可复用结果裁剪移走的事实获取工具
    assert "execute_sql_query" in names
    assert "search_knowledge_base" in names
    # 时间工具去重移走的取时工具
    assert "get_current_time" in names


def test_fingerprint_is_immune_to_content_trimming():
    """正常轮次（完整）与追问轮次（裁剪后）必须算出同一个指纹。"""
    full = [_spec(name) for name in STABLE_TOOLS + TRIMMED_TOOLS]
    trimmed = [_spec(name) for name in STABLE_TOOLS]
    assert build_tools_fingerprint(_Config, full) == build_tools_fingerprint(_Config, trimmed)


def test_both_resolution_paths_agree():
    """挂起恢复路径不经过可复用结果裁剪，口径必须与正常轮次一致。"""
    normal_path = [_spec(name) for name in STABLE_TOOLS]  # 正常轮次：裁剪后
    resume_path = [_spec(name) for name in STABLE_TOOLS + ["execute_sql_query"]]  # 恢复路径：未裁剪
    assert build_tools_fingerprint(_Config, normal_path) == build_tools_fingerprint(_Config, resume_path)


def test_new_tool_still_triggers_reset():
    """真正的绑定变化仍要被检测到——剔除只针对内容裁剪那一批。"""
    before = [_spec(name) for name in STABLE_TOOLS]
    after = [_spec(name) for name in STABLE_TOOLS + ["another_mcp_tool"]]
    assert build_tools_fingerprint(_Config, before) != build_tools_fingerprint(_Config, after)


def test_removed_stable_tool_triggers_reset():
    before = [_spec(name) for name in STABLE_TOOLS]
    after = [_spec(name) for name in STABLE_TOOLS[:-1]]
    assert build_tools_fingerprint(_Config, before) != build_tools_fingerprint(_Config, after)


def test_model_and_version_still_trigger_reset():
    tools = [_spec(name) for name in STABLE_TOOLS]

    class OtherVersion(_Config):
        agent_version = "v2"

    class OtherModel(_Config):
        model_name = "gpt-5"

    base = build_tools_fingerprint(_Config, tools)
    assert build_tools_fingerprint(OtherVersion, tools) != base
    assert build_tools_fingerprint(OtherModel, tools) != base


def test_order_does_not_matter():
    forward = [_spec(name) for name in STABLE_TOOLS]
    backward = [_spec(name) for name in reversed(STABLE_TOOLS)]
    assert build_tools_fingerprint(_Config, forward) == build_tools_fingerprint(_Config, backward)


def test_empty_tool_list_is_tolerated():
    assert build_tools_fingerprint(_Config, []) == build_tools_fingerprint(_Config, [])


def test_fingerprint_implementation_strips_trimmed_names():
    from app.services.ai.runtime.agentscope import agent_runtime

    source = Path(agent_runtime.__file__).read_text(encoding="utf-8")
    start = source.index("def build_tools_fingerprint(")
    body = source[start:]
    # 该函数目前是文件末尾的最后一个定义，按需截到下一个顶层 def 为止
    tail = body.find("\n\ndef ", 10)
    if tail != -1:
        body = body[:tail]
    assert "unstable = _content_trimmed_tool_names()" in body
    assert "if name and name not in unstable" in body


# --------------------------------------------------------------------------
# 前端：warning 行必须有区别于普通步骤的视觉
# --------------------------------------------------------------------------


def test_warning_rows_are_not_rendered_as_ordinary_steps():
    source = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    # 图标：warning 不能落回默认的对话气泡（那会让人以为只是又一步）
    assert 'if (item.status === "warning") return ExclamationTriangleIcon;' in source
    # 配色：主行与子项都要有 amber 分支
    assert source.count("'bg-amber-50/60 text-amber-700 dark:bg-amber-950/20 dark:text-amber-300'") == 2
    # 默认灰行不能把 warning 一起吃掉
    assert source.count("&& item.status !== 'warning'") == 1
    assert source.count("&& child.status !== 'warning'") == 1

"""提示词里的「本轮可用工具」必须与运行时真实 tools 对齐。

背景：清单在 preflight 阶段算好写进 System Prompt，而运行时的裁剪
（``filter_tools_for_reusable_result`` / ``filter_redundant_time_tools``）只改
``tools``。过去两边的判定条件不同源——清单只看 ``turn_decision.reusable_result_mode``，
工具却用 ``should_inject_session_artifact``（覆盖「继续」「这个呢」等弱上下文追问）。
于是命中「可复用上次结果」的轮次里，提示词仍列着 ``sub_agent_call`` /
``memory_search`` 等 9 个已被摘掉的事实获取工具，模型照着清单调用必然撞上 ghost
tool 白名单，白跑一轮才自我纠正。

这里锁两件事：
1. 提示词裁剪与工具裁剪同源（A）；
2. 即便两边再次错位，runner 也会按最终 tools 重写清单段（B）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai.agent_prompts import (
    TOOL_INVENTORY_HEADER,
    prune_unregistered_tool_inventory,
)
from app.services.ai.prompt_assembler import resolve_effective_prompt_tool_names_for_turn
from app.services.ai.session_tool_artifact import REUSABLE_RESULT_ACQUISITION_TOOLS

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "app/services/ai/runners/assistant_agent_runner.py"

ARTIFACT = {
    "tool_name": "execute_sql_query",
    "text_excerpt": "总共 12 条",
    "structured": None,
    "saved_at": "2026-10-29 09:00:00",
    "user_question": "统计用户状态",
}


class _Decision:
    def __init__(self, mode: str = "none", result_id: str | None = None):
        self.reusable_result_mode = mode
        self.reusable_result_id = result_id


class _Config:
    """主助手配置：显式绑定子代理工具，模拟真实的 v37 配置。"""

    agent_id = "sys-agent-chat"
    agent_name = "main"
    tools = ["sub_agent_call", "todo_write", "memory_search", "get_myinfo"]


# ---- 单元：清单段裁剪 ----


def test_prune_removes_only_unregistered_rows():
    prompt = "\n".join(
        [
            "## 固定守则",
            "内容",
            "",
            TOOL_INVENTORY_HEADER,
            "- sub_agent_call: 委派子代理",
            "- memory_search: 检索记忆",
            "- todo_write: 待办",
            "",
            "## 下一段",
            "保留",
        ]
    )
    pruned = prune_unregistered_tool_inventory(prompt, {"todo_write"})
    assert "sub_agent_call" not in pruned
    assert "memory_search" not in pruned
    assert "- todo_write: 待办" in pruned
    assert "## 固定守则" in pruned and "## 下一段" in pruned
    assert "保留" in pruned


def test_prune_drops_whole_section_when_nothing_left():
    prompt = f"{TOOL_INVENTORY_HEADER}\n- sub_agent_call: x\n\n## 下一段\n内容"
    pruned = prune_unregistered_tool_inventory(prompt, set())
    assert TOOL_INVENTORY_HEADER not in pruned
    assert pruned.lstrip().startswith("## 下一段")


def test_prune_is_noop_without_inventory_section():
    prompt = "## 其它\n文本"
    assert prune_unregistered_tool_inventory(prompt, {"a"}) == prompt


def test_prune_keeps_section_at_end_of_prompt():
    prompt = f"## 前\nabc\n\n{TOOL_INVENTORY_HEADER}\n- a: x\n- b: y"
    pruned = prune_unregistered_tool_inventory(prompt, {"a"})
    assert pruned.strip().endswith("- a: x")
    assert "- b: y" not in pruned


# ---- A：提示词裁剪与工具裁剪同源 ----


@pytest.mark.asyncio
async def test_prompt_names_prune_on_weak_context_followup_without_reuse_mode():
    """「继续」这类弱上下文追问会触发工具裁剪，提示词清单必须同步裁掉。

    回归点：过去只认 ``reusable_result_mode == "reuse"``，这里 mode 是 none。
    """
    names = await resolve_effective_prompt_tool_names_for_turn(
        _Config(),
        current_user_query="继续",
        turn_decision=_Decision(mode="none"),
        session_artifact=ARTIFACT,
    )
    assert "sub_agent_call" not in names
    assert "memory_search" not in names
    assert "todo_write" in names


@pytest.mark.asyncio
async def test_prompt_names_keep_tools_when_artifact_not_injected():
    names = await resolve_effective_prompt_tool_names_for_turn(
        _Config(),
        current_user_query="帮我统计用户状态",
        turn_decision=_Decision(mode="none"),
        session_artifact=ARTIFACT,
    )
    assert "sub_agent_call" in names


@pytest.mark.asyncio
async def test_prompt_names_prune_when_force_reuse():
    names = await resolve_effective_prompt_tool_names_for_turn(
        _Config(),
        current_user_query="随便",
        turn_decision=_Decision(mode="reuse"),
    )
    assert not names & REUSABLE_RESULT_ACQUISITION_TOOLS


# ---- B：runner 以最终 tools 为准兜底 ----


def test_runner_prunes_inventory_on_both_execute_and_resume_paths():
    source = RUNNER.read_text(encoding="utf-8")
    # 正常执行路径 + 挂起恢复路径各一处
    assert source.count("prune_unregistered_tool_inventory(") == 2
    assert "from app.services.ai.agent_prompts import prune_unregistered_tool_inventory" in source

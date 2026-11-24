"""清单型工具跨轮重建上下文时不得被截成「看起来完整」的残缺 JSON。

现场症状（生产）：同一份技能清单，**同一轮**里完整可见（64Ki 闸门），到了**下一轮**
重建上下文时只剩开头几条（800 字符闸门），末尾是 ``… [输出已截断]``，截断点还落在
条目中间。模型据此断言「某个技能不存在」，随后又因为 ``read_skill_instruction`` 的
三选一模糊文案把它读成「被禁用」，向用户报告了一个根本不存在的问题。

本文件锁住三件事：
1. 清单型工具用独立配额，并按**条目**裁剪（结果仍是合法 JSON）；
2. 裁剪后必须带 total/returned/truncated 与可执行说明（"未展示 ≠ 不存在"）；
3. 非清单型工具仍是原来的 800 字符截断（不放大上下文预算）。
"""

import json

import pytest

from app.services.ai.runtime.agentscope.tool_result import (
    CATALOG_TOOL_OUTPUT_MAX_CHARS,
    CATALOG_TOOL_NAMES,
    build_final_tool_result_context,
)

pytestmark = pytest.mark.no_infrastructure


def _skill_listing(count: int, *, desc_len: int = 40) -> str:
    """造一份 list_available_skills 形状的返回（对象形态，含 total/returned/truncated）。"""
    skills = [
        {
            "id": f"skill-{index:03d}",
            "name": f"技能{index:03d}",
            "description": "说明" * (desc_len // 2),
            "scope": "personal" if index == count - 1 else "global",
        }
        for index in range(count)
    ]
    return json.dumps(
        {
            "total": count,
            "returned": count,
            "truncated": False,
            "skills": skills,
        },
        ensure_ascii=False,
    )


def _meta(tool_name: str, output: str) -> dict:
    return {
        "tool_names": {"c1": tool_name},
        "tool_args_text": {"c1": "{}"},
        "tool_outputs": {"c1": output},
        "tool_data": {},
        "tool_result_states": {"c1": "success"},
    }


def _payload_of(rendered: str) -> tuple[dict, str]:
    """拆出「JSON 正文」与「尾部的计数说明」。"""
    body = rendered.split(" -> ", 1)[1]
    payload_text, separator, rest = body.partition("\n（共")
    return json.loads(payload_text), (separator + rest) if separator else ""


def test_catalog_tool_is_registered_and_has_own_budget():
    """清单型工具必须在白名单里、且配额仍然小于跨轮总预算。"""
    assert "list_available_skills" in CATALOG_TOOL_NAMES
    assert "list_accessible_datasets" in CATALOG_TOOL_NAMES
    assert 800 < CATALOG_TOOL_OUTPUT_MAX_CHARS < 4000


def test_tail_skill_survives_cross_turn_context():
    """回归主症状：排在最后的个人技能，跨轮后不能消失。"""
    listing = _skill_listing(11)
    rendered = build_final_tool_result_context(_meta("list_available_skills", listing))

    payload, note = _payload_of(rendered)
    ids = [item["id"] for item in payload["skills"]]
    assert ids[-1] == "skill-010", "个人技能排在公共技能之后，正是旧实现里最先被砍掉的那条"
    assert "skill-010" in rendered
    assert payload["total"] == 11
    assert payload["truncated"] is False
    # 未触发裁剪时不该出现任何截断声明
    assert note == ""
    assert "… [输出已截断]" not in rendered


def test_oversized_catalog_is_clipped_by_item_not_by_char():
    """超配额时按条目裁剪：JSON 仍合法，且明确告知「共几条、给了几条」。"""
    listing = _skill_listing(80, desc_len=200)
    rendered = build_final_tool_result_context(_meta("list_available_skills", listing))

    body = rendered.split(" -> ", 1)[1]
    payload, note = _payload_of(rendered)

    # ① 仍是合法 JSON（旧实现会在此处拦腰截断，json.loads 直接失败）
    assert isinstance(payload["skills"], list)
    # ② 真实总数不得因裁剪而缩水
    assert payload["total"] == 80
    assert payload["returned"] == len(payload["skills"])
    assert payload["returned"] < 80
    assert payload["truncated"] is True
    # ③ 配额生效，且说明可执行
    assert len(body) <= CATALOG_TOOL_OUTPUT_MAX_CHARS + 200
    assert "共 80 条" in note
    assert "keyword" in note
    # 工具自带的 note（"另有 N 个技能未展示…"）描述的是上一层的截断状态，跨轮必须丢弃，
    # 否则模型会以为自己还在看一份被收窄过的清单。
    assert "另有" not in note


def test_catalog_keeps_declared_total_beyond_returned_page():
    """上一层已按 limit 截过时，跨轮不能把「共 N 条」再缩小成「共 returned 条」。"""
    listing = json.dumps(
        {
            "total": 300,
            "returned": 50,
            "truncated": True,
            "skills": [
                {"id": f"s-{index}", "name": f"s{index}", "description": "d" * 120, "scope": "global"}
                for index in range(50)
            ],
        },
        ensure_ascii=False,
    )
    rendered = build_final_tool_result_context(_meta("list_available_skills", listing))
    payload, note = _payload_of(rendered)
    assert payload["total"] == 300
    assert "共 300 条" in note


def test_plain_tool_still_clipped_at_800():
    """非清单型工具保持原有 800 字符闸门——本修复不放宽上下文预算。"""
    rendered = build_final_tool_result_context(_meta("exec_command", "x" * 3000))
    assert "… [输出已截断]" in rendered
    assert len(rendered) < 1000


def test_catalog_line_wins_budget_over_plain_output():
    """清单行优先入上下文：被普通输出挤掉就等于模型看不见清单。"""
    listing = _skill_listing(11)
    meta = {
        "tool_names": {"c1": "exec_command", "c2": "list_available_skills"},
        "tool_args_text": {"c1": "{}", "c2": "{}"},
        "tool_outputs": {"c1": "y" * 3000, "c2": listing},
        "tool_data": {},
        "tool_result_states": {"c1": "success", "c2": "success"},
    }
    rendered = build_final_tool_result_context(meta)
    assert rendered.startswith("list_available_skills:"), "清单行必须排在前面"
    assert "skill-010" in rendered
    assert len(rendered) <= 4000 + 200


def test_total_budget_is_enforced_without_mid_line_cut():
    """总预算仍生效；被挤掉的行要留下「还有多少条未保留」的说明。"""
    meta = {
        "tool_names": {"c1": "exec_command", "c2": "exec_command2"},
        "tool_args_text": {"c1": "{}", "c2": "{}"},
        "tool_outputs": {"c1": "a" * 900, "c2": "b" * 900},
        "tool_data": {},
        "tool_result_states": {"c1": "success", "c2": "success"},
    }
    rendered = build_final_tool_result_context(meta, max_total_chars=1000)
    assert len(rendered) <= 1000 + 200
    assert "未保留" in rendered


def test_non_json_catalog_output_falls_back_to_plain_truncation():
    """工具走了错误文案等非 JSON 分支时不能抛异常，退回通用截断。"""
    # 注意不能用"错误：…"开头：那会被判成失败结果而不进跨轮上下文（另一条既有规则）。
    plain_text = "技能清单渲染回退： " + "e" * 5000
    rendered = build_final_tool_result_context(_meta("list_available_skills", plain_text))
    assert "… [输出已截断]" in rendered
    assert len(rendered) <= CATALOG_TOOL_OUTPUT_MAX_CHARS + 200

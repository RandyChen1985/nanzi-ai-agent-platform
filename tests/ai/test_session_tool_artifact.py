import json

import pytest

from app.services.ai.session_tool_artifact import (
    SESSION_ARTIFACT_BLOCK_MARKER,
    append_session_tool_artifact_to_system_prompt,
    artifact_candidate_score,
    build_artifact_payload,
    build_session_artifact_prompt_block,
    consider_turn_artifact_candidate,
    should_inject_session_artifact,
)

pytestmark = pytest.mark.no_infrastructure


def test_artifact_candidate_score_prefers_mcp_over_clock():
    long_text = "x" * 200
    mcp_score = artifact_candidate_score(
        tool_name="analytics_report",
        source_type="mcp",
        permission_scope="read",
        text=long_text,
        structured=None,
    )
    clock_score = artifact_candidate_score(
        tool_name="get_current_time",
        source_type="system",
        permission_scope="read",
        text=long_text,
        structured=None,
    )
    assert mcp_score > 0
    assert clock_score == 0


def test_consider_turn_artifact_keeps_highest_score_candidate():
    turn = {"user_question": "查报表", "trace_id": "t1", "best": None}
    small = {"rows": [{"a": 1}]}
    consider_turn_artifact_candidate(
        turn,
        tool_name="low_value",
        tool_args={},
        tool_output=json.dumps(small),
        source_type="static",
        permission_scope="read",
    )
    first_score = turn["best"]["_score"]
    consider_turn_artifact_candidate(
        turn,
        tool_name="mcp_report",
        tool_args={},
        tool_output="y" * 500,
        source_type="mcp",
        permission_scope="read",
    )
    assert turn["best"]["tool_name"] == "mcp_report"
    assert turn["best"]["_score"] >= first_score


def test_should_inject_on_pure_followup_not_on_fresh_data_request():
    artifact = build_artifact_payload(
        tool_name="mcp_x",
        tool_args={},
        tool_output="z" * 300,
        source_type="mcp",
        user_question="上一轮",
        trace_id="1",
    )
    assert should_inject_session_artifact("把刚才的结果画成柱状图", artifact) is True
    assert should_inject_session_artifact("请重新查询最新数据", artifact) is False


def test_should_inject_returns_false_when_artifact_is_none():
    assert should_inject_session_artifact("把刚才的结果画成柱状图", None) is False
    assert append_session_tool_artifact_to_system_prompt("base", "总结一下", None) == "base"


def test_append_session_artifact_injects_block():
    artifact = build_artifact_payload(
        tool_name="api_tool",
        tool_args={"q": "test"},
        tool_output="result " * 50,
        source_type="generic_api",
        user_question="原始问题",
        trace_id="1",
    )
    out = append_session_tool_artifact_to_system_prompt(
        "系统提示",
        "总结一下上面的结果",
        artifact,
    )
    assert out.startswith(SESSION_ARTIFACT_BLOCK_MARKER)
    assert "api_tool" in out
    assert "系统提示" in out


def test_append_skips_greeting_without_context_ref():
    artifact = build_artifact_payload(
        tool_name="api_tool",
        tool_args={},
        tool_output="a" * 200,
        source_type="generic_api",
        user_question="q",
        trace_id="1",
    )
    assert append_session_tool_artifact_to_system_prompt("base", "你好", artifact) == "base"


def test_build_session_artifact_prompt_block_contains_rules():
    block = build_session_artifact_prompt_block(
        {
            "tool_name": "demo",
            "saved_at": "2026-01-01",
            "text_excerpt": "data",
        }
    )
    assert "不要对同一工具重复" in block

import pytest

from app.services.ai.intent_service import (
    looks_like_meta_action,
    looks_like_skill_execution,
    looks_like_compound_query_with_viz,
    looks_like_pure_result_followup,
    looks_like_data_followup,
)
from app.services.ai.skill_resolver import (
    _extract_skill_hints,
    _score_skill_match,
    is_main_general_agent,
    lexical_relevance_score,
    scan_relevant_skills,
    should_scan_skills_for_query,
)
from app.schemas.agent import ChatConfig


pytestmark = pytest.mark.no_infrastructure


def test_extract_skill_hint_from_use_skill_query():
    hints = _extract_skill_hints("使用用户列表查询技能查询一次")
    assert "用户列表查询" in hints


def test_looks_like_skill_execution_not_meta_action():
    q = "使用用户列表查询技能查询一次"
    assert looks_like_skill_execution(q) is True
    assert looks_like_meta_action(q) is False


def test_score_skill_match_by_name():
    meta = {"id": "user-list-query", "name": "用户列表查询", "description": "查询用户列表"}
    assert _score_skill_match("用户列表查询", meta) >= 0.9


def test_compound_query_with_viz_is_not_pure_result_followup():
    q = "查询用户列表并可视化分析"
    assert looks_like_compound_query_with_viz(q) is True
    assert looks_like_pure_result_followup(q) is False
    assert looks_like_data_followup(q) is False


def test_user_list_viz_without_chaxun_still_new_query_compound():
    q = "用户列表并可视化分析"
    assert looks_like_compound_query_with_viz(q) is True
    assert looks_like_pure_result_followup(q) is False


def test_pure_viz_followup_without_prior_query_verbs():
    q = "可视化分析一下"
    assert looks_like_compound_query_with_viz(q) is False
    assert looks_like_pure_result_followup(q) is True


def test_chart_display_followup_is_not_compound_new_query():
    q = "柱状图显示吧"
    assert looks_like_compound_query_with_viz(q) is False
    assert looks_like_pure_result_followup(q) is True


def test_lexical_relevance_score_by_skill_name():
    meta = {"id": "user-list-query", "name": "用户列表查询", "description": "查询系统用户列表"}
    assert lexical_relevance_score("帮我查一下用户列表", meta) >= 0.45


def test_lexical_relevance_score_ignores_generic_query():
    meta = {"id": "user-list-query", "name": "用户列表查询", "description": "查询系统用户列表"}
    assert lexical_relevance_score("你好", meta) == 0.0


def test_should_scan_skills_skips_greeting():
    assert should_scan_skills_for_query("你好") is False
    assert should_scan_skills_for_query("帮我查用户列表") is True


def test_scan_relevant_skills_returns_top_match(monkeypatch):
    metas = [
        {"id": "user-list-query", "name": "用户列表查询", "description": "查询系统用户列表"},
        {"id": "pdf-helper", "name": "PDF处理", "description": "处理 PDF 文档"},
    ]
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.list_skill_metas",
        lambda: metas,
    )
    results = scan_relevant_skills("查一下用户列表", max_results=1, min_score=0.45)
    assert len(results) == 1
    assert results[0]["id"] == "user-list-query"
    assert results[0]["match_source"] == "scan"


def _chat_config(**kwargs):
    defaults = dict(
        agent_id="sys-agent-chat",
        agent_name="main",
        model_name="test-model",
        temperature=0.0,
        system_prompt="prompt",
        tools=[],
        capabilities=["general_chat"],
    )
    defaults.update(kwargs)
    return ChatConfig(**defaults)


def test_is_main_general_agent_matches_sys_agent_chat():
    assert is_main_general_agent(_chat_config()) is True


def test_is_main_general_agent_matches_fallback_slug():
    assert is_main_general_agent(_chat_config(agent_id="general-chat", agent_name="General Chat")) is True


def test_is_main_general_agent_rejects_chatbi():
    assert is_main_general_agent(
        _chat_config(
            agent_id="sys-agent-chatbi",
            agent_name="chat-bi",
            capabilities=["data_query"],
        )
    ) is False

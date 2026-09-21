from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/chat/MessageContinueAnalysis.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
AGENT_SCHEMA = ROOT / "app/schemas/agent.py"
AGENT_MANAGER = ROOT / "app/services/ai/agent_manager.py"
AGENT_SERVICE = ROOT / "app/services/ai/agent_service.py"
EXECUTION_STEP = ROOT / "app/services/ai/pipeline/steps/execution_step.py"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"


def test_generic_continue_analysis_contains_asset_prompts_and_emits_queries():
    source = COMPONENT.read_text(encoding="utf-8")

    assert '<span aria-hidden="true">✨</span>' not in source
    for label in ("生成可视化分析报告", "保存为 Markdown", "保存为 Word", "提炼生成 Skill"):
        assert label in source
    assert 'emit("select", action.query)' in source
    assert "直接调用 create_skills 工具" in source
    assert "scope 使用 personal" in source
    assert "不要只输出 Skill 草稿，必须实际调用工具创建" in source
    assert "当前工作区的 docs 目录" in source
    assert "完整路径" in source
    assert "合法的 ```chart ECharts 代码块" in source
    assert "图表数据必须完全来自当前回复" in source
    assert "数据不足以生成可靠图表" in source
    assert "fixed inset-0" in source
    assert "absolute bottom-full" in source
    assert 'aria-label="关闭继续分析"' in source


def test_generic_continue_analysis_is_wired_to_non_chatbi_agent_messages():
    for source_path in (EMBED, DEBUG):
        source = source_path.read_text(encoding="utf-8")
        assert 'import MessageContinueAnalysis from "@/components/chat/MessageContinueAnalysis.vue"' in source
        assert "<MessageContinueAnalysis" in source
        assert ":is-mobile=" in source
        assert "@select=\"(query) => handleQuickQuestion(query, 'send', visibleStreamBody(msg))\"" in source
        assert "msg.agentType === 'GENERAL'" in source or 'msg.agentType === "GENERAL"' in source
        assert "isGeneralAgentMessage(msg)" in source
        assert "!msg.chatbiInsight?.actions?.length" in source
        assert "if (data.agent_type)" in source
        assert "!msg.isThinking" in source


def test_saved_report_execute_attaches_chatbi_continue_analysis():
    for source_path in (EMBED, DEBUG):
        source = source_path.read_text(encoding="utf-8")
        assert "chatbi_insight" in source
        assert "agentMsg.value.chatbiInsight = execResult.chatbi_insight" in source
        assert "<ChatBIContinueAnalysis" in source
        assert "msg.chatbiInsight?.actions?.length" in source
        # 黄金报表「继续分析」须强制走查数智能体，并保证会话 ID 以写入 last_data_result
        assert "@select=\"handleChatBIContinueSelect\"" in source
        assert "forceDataQueryAgentOnce" in source
        assert "armDataQueryAgentForFollowup" in source
        assert "forcedDataAgentIdForTurn" in source
        assert "Redis last_data_result" in source or "last_data_result" in source
        assert "if (!conversationId.value)" in source


def test_generic_continue_analysis_preserves_selected_message_context():
    for source_path in (EMBED, DEBUG):
        source = source_path.read_text(encoding="utf-8")
        assert "sourceContent?: string" in source
        assert "USER_MESSAGE_CONTEXT_DIVIDER" in source
        assert "【被点击的 AI 回复】" in source


def test_agent_type_is_carried_from_resolved_config_to_sse_meta():
    schema = AGENT_SCHEMA.read_text(encoding="utf-8")
    manager = AGENT_MANAGER.read_text(encoding="utf-8")
    service = AGENT_SERVICE.read_text(encoding="utf-8")
    execution_step = EXECUTION_STEP.read_text(encoding="utf-8")

    assert "agent_type: AgentType = AgentType.GENERAL" in schema
    assert "agent_type=resolve_agent_type(agent)" in manager
    assert "agent_type=resolve_agent_type(version.agent)" in manager
    assert "def _public_agent_type(agent_config: Any) -> str:" in service
    # meta 事件组装已搬入 pipeline execution_step，SSE meta 仍携带公开 agent_type。
    assert '"type": "meta"' in execution_step
    assert '"agent_type": _public_agent_type(agent_config)' in execution_step


def test_conversation_history_preserves_agent_type_for_generic_actions():
    endpoint = CHAT_ENDPOINT.read_text(encoding="utf-8")
    assert "agent_type_by_name" in endpoint
    assert 'message["agent_type"] = agent_type' in endpoint
    assert '"agent_type": agent_type_by_id.get(str(r.agent_id))' in endpoint

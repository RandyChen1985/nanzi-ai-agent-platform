from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_chat_input_exposes_context_usage_indicator_and_both_chat_surfaces_refresh_it():
    chat_input = _read("frontend/src/components/embed/ChatInput.vue")
    composable = _read("frontend/src/composables/useContextUsage.ts")
    embed_chat = _read("frontend/src/views/EmbedChat.vue")
    agent_debug = _read("frontend/src/views/AgentDebug.vue")
    input_box = chat_input[chat_input.index("<!-- Input Box -->"):]

    assert "contextUsage" in chat_input
    assert "请求输入上限" in chat_input
    assert 'data-testid="context-usage-indicator"' in chat_input
    assert 'data-testid="context-usage-details"' in chat_input
    assert 'data-testid="context-usage-bar"' not in input_box
    # 输入框为右侧浮标动态预留内边距：textarea 绑定 textareaPaddingRightClass（pr-28 / pr-48）。
    assert "textareaPaddingRightClass" in input_box
    assert '"pr-28"' in chat_input
    assert "contextUsageDetailsPlacement" in chat_input
    assert "bottom-[calc(100%+0.5rem)]" in chat_input
    assert "top-[calc(100%+0.5rem)]" in chat_input
    assert "contextUsagePercentLabel" in chat_input
    assert "{{ contextUsagePercentLabel }}" in chat_input
    assert "sandbox_policy" in composable
    assert "sandbox_runtime_env" in composable
    assert "sandboxPolicyLabel" in chat_input
    assert "sandboxRuntimeEnvLabel" in chat_input
    assert "sandboxPolicyIcon" in chat_input
    assert "ComputerDesktopIcon" in chat_input
    assert "CubeIcon" in chat_input
    assert "CloudIcon" in chat_input
    assert "ServerIcon" in chat_input
    assert ':is="sandboxPolicyIcon"' in chat_input
    assert "contextUsageStatusLabel" in chat_input
    assert "使用正常" in chat_input
    assert "接近上限" in chat_input
    assert "已达输入上限" in chat_input
    assert "自动压缩触发线" in chat_input
    assert "达到此水位后，系统会整理较早对话，优先压缩成摘要。" in chat_input
    assert "border-slate-200/80" in chat_input
    assert "bg-slate-50/80" in chat_input
    assert "contextUsageTone.dot" in chat_input
    assert "sandboxPolicyBadgeClass" in chat_input
    assert "grid-cols-2" in chat_input
    assert "w-72" in chat_input
    assert 'runtimeEnv === "docker"' in chat_input
    assert 'runtimeEnv === "host"' in chat_input
    assert 'return "平台 Docker 容器内"' in chat_input
    assert 'return "宿主机"' in chat_input
    assert '`local（${sandboxRuntimeEnvLabel.value}）`' in chat_input
    assert "Sandbox 策略" in chat_input
    assert "最近一次实际请求" not in chat_input
    assert "会话整体构成" in chat_input
    assert "sessionContextBreakdownItems" in chat_input
    assert "context_breakdown" in composable
    assert "contextBreakdownSegmentWidth" in chat_input
    assert "session-context-breakdown-segment" in chat_input
    assert "system_prompt_tokens" in chat_input
    assert "tools_tokens" in chat_input
    assert "conversation_tokens" in chat_input
    assert "/api/v1/chat/conversation/" in composable
    assert "context-usage" in composable
    assert "model_calls" not in composable
    assert "last_model_call_context_breakdown" not in composable
    assert "useContextUsage" in embed_chat
    assert "useContextUsage" in agent_debug
    assert ':context-usage="contextUsage"' in embed_chat
    assert ':context-usage="contextUsage"' in agent_debug

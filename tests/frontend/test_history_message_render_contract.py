"""历史消息渲染契约：必须与聊天页共用同一个渲染器。

背景：聊天日志审计页（ChatLogs）与智能体「对话历史」弹窗（AgentHistoryModal）原先都是
`v-html="renderMarkdown(...)"` —— 只走 markdown-it + highlight.js，没有分段结构，于是
` ```chart ` 在日志里退化成裸 JSON 代码块，` ````mermaid `、`<sql_plan>`、`:::analysis`
同样失效，而 EmbedChat 里这些都能正常渲染成图表/流程图/计划卡。

本文件把「历史消息必须走 MessageRenderer」钉在源码层：两处入口一旦回退成
`renderMarkdown(summary)` 直出，这里的断言会直接失败。
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

CHAT_LOGS = "frontend/src/views/ChatLogs.vue"
AGENT_HISTORY_MODAL = "frontend/src/components/agent/AgentHistoryModal.vue"
MESSAGE_RENDERER = "frontend/src/components/MessageRenderer.vue"
HOST_STYLES = "frontend/src/styles/messageMarkdownHost.css"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("source_path", [CHAT_LOGS, AGENT_HISTORY_MODAL])
def test_history_reply_uses_shared_message_renderer(source_path: str):
    source = _read(source_path)

    assert "import MessageRenderer from '@/components/MessageRenderer.vue'" in source
    assert "<MessageRenderer" in source
    # 历史回放只读：不接引用面板、产物下载与画布，也不渲染 quick 按钮。
    assert "readonly" in source
    assert "hide-quick-buttons" in source


@pytest.mark.parametrize("source_path", [CHAT_LOGS, AGENT_HISTORY_MODAL])
def test_history_reply_never_renders_raw_markdown(source_path: str):
    """回复正文不得再直出 renderMarkdown，否则 ```chart 又变回 JSON 代码块。"""
    source = _read(source_path)

    assert "v-html=\"renderTurnReply(" not in source
    assert "v-html=\"replyHtml\"" not in source
    assert "const replyHtml = computed(" not in source
    assert "const renderTurnReply = (" not in source


@pytest.mark.parametrize("source_path", [CHAT_LOGS, AGENT_HISTORY_MODAL])
def test_history_reply_imports_shared_host_styles(source_path: str):
    source = _read(source_path)

    assert "import '@/styles/messageMarkdownHost.css'" in source
    assert 'class="message-host' in source


def test_message_renderer_still_segments_chart_and_mermaid_fences():
    """对齐的前提是组件确实把围栏转成了图表/图，而不是代码块。"""
    source = _read(MESSAGE_RENDERER)

    assert "(?:chart|echarts)" in source
    assert "```\\s*mermaid" in source
    assert "v-chart" in source
    assert "MermaidRenderer" in source
    assert "enhanceMarkdownTablesForMobile" in source


def test_message_renderer_readonly_skips_login_dependent_enhancements():
    source = _read(MESSAGE_RENDERER)

    assert "if (props.readonly) return text;" in source
    assert "if (props.readonly) {" in source


def test_host_styles_cover_code_copy_and_table_card_view():
    """结构由组件产出，样式由宿主提供：复制按钮与移动端表格卡片化不能缺。"""
    styles = _read(HOST_STYLES)

    assert ".message-host .markdown-body .code-copy-btn" in styles
    assert ".message-host .markdown-body .code-block-wrapper" in styles
    assert ".markdown-table-mobile-cards" in styles
    assert "--md-table-background" in styles


def test_chat_logs_payload_preview_keeps_its_own_styles():
    """链路里 tool payload 的预览不属于消息渲染，样式不能被并到 message-host 里。"""
    source = _read(CHAT_LOGS)

    assert "payload-markdown" in source
    assert ".payload-markdown :deep(pre)" in source

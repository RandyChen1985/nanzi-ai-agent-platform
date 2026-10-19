"""EmbedChat「会话历史 → 导出 Markdown」取数路径的源码级契约。

## 背景

会话历史侧栏的下载按钮曾请求
``GET /api/v1/chat/conversation/{conversation_id}/history``。后端从未注册过这个
路由（会话详情是 ``GET /api/v1/chat/conversation/{conversation_id}``，且以 Redis
为主、默认只回 50 条），于是每次点击都稳定 404，前端统一提示「导出对话失败」。

## 约定

- 导出必须按 ``conversation_id`` 从 ``GET /api/v1/chat/history`` 分页取数；
- 空结果、无会话 ID 时要给出明确提示，不能静默返回或谎报成功；
- 不允许再出现 ``/chat/conversation/{id}/history`` 这种后端不存在的路径。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_ENDPOINTS = ROOT / "app/api/v1/endpoints/chat.py"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _export_body() -> str:
    source = _source()
    start = source.index("const handleExportChatFromSidebar = async")
    end = source.index("const handleGlobalKeydown", start)
    return source[start:end]


def test_export_uses_the_existing_history_endpoint_with_conversation_filter():
    body = _export_body()

    # 取数入口必须是真实存在的列表端点，并带上会话过滤与分页参数。
    assert 'axios.get("/api/v1/chat/history"' in body
    assert "params: { conversation_id: targetConvId, page, page_size: pageSize }" in body
    # 分页循环必须能看到终止条件，避免全量拉取无限打转。
    assert "collected >= total" in body
    assert "page <= 50" in body


def test_export_never_calls_the_phantom_conversation_history_route():
    source = _source()

    assert "/api/v1/chat/conversation/${targetConvId}/history" not in source
    assert "/chat/conversation/${targetConvId}/history" not in source
    # 后端同样不应存在该路由（前后端任何一侧新增都要在这里同步）。
    assert 'conversation/{conversation_id}/history"' not in CHAT_ENDPOINTS.read_text(
        encoding="utf-8"
    )


def test_export_reports_empty_and_idless_cases_instead_of_silent_success():
    body = _export_body()

    assert 'showToast("该记录没有会话 ID，暂不支持导出", "warning")' in body
    assert 'showToast("该会话暂无可导出的消息", "warning")' in body
    # 空结果必须提前 return，不能继续走到「导出成功」。
    empty_guard = body[body.index("if (turns.length === 0)") :]
    assert empty_guard.index("return;") < empty_guard.index("导出成功")


def test_export_falls_back_to_on_screen_messages_when_server_has_nothing_yet():
    body = _export_body()

    # 流式输出尚未落库时，用当前界面的消息兜底，而不是产出空文件。
    assert "turns.length === 0 && targetConvId === conversationId.value" in body
    assert "messages.value.forEach" in body

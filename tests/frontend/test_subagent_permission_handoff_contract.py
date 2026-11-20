"""子智能体工具授权交回主会话的前端契约。

后端把子代理的 `permission_required` 转发进父流、确认后回一条
`subagent_permission_handoff` 带续聊指令；前端必须：卡上标清是哪个子智能体在请求、
把指令收在卡片上、并在确认流收尾时把指令发回父会话让它 resume 子代理。
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

HANDLERS = ROOT / "frontend/src/utils/agentscopeSseHandlers.ts"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
CARD = ROOT / "frontend/src/components/chat/ToolPermissionCard.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_permission_card_type_carries_subagent_and_resume_directive():
    source = _read(HANDLERS)
    block = source.split("export interface PendingToolPermission {", 1)[1].split(
        "export interface PendingExternalExecution", 1
    )[0]
    assert "subagent?:" in block
    assert "resumeDirective?:" in block


def test_permission_required_saves_subagent_and_labels_log():
    source = _read(HANDLERS)
    handler = source.split("export function handlePermissionRequired", 1)[1].split(
        "export function handleExternalExecutionRequired", 1
    )[0]
    # 卡片要能显示「来自子智能体 X」，事件里的 subagent 必须落到 pendingPermission 上
    assert "subagent: data.subagent" in handler
    # 过程日志同样标出子代理名，否则父会话里只看到一条无主的「需要确认」
    assert "subagentLabel" in handler


def test_permission_card_shows_subagent_badge():
    source = _read(CARD)
    assert 'v-if="payload.subagent?.display_name"' in source
    assert "来自子智能体" in source


def test_embed_chat_stores_directive_and_resumes_subagent_after_confirm_flow():
    source = _read(EMBED)
    # 1) 收到 handoff 事件时把指令收在卡片上
    assert 'data?.type === "subagent_permission_handoff"' in source
    assert "resumeDirective = data.directive" in source
    # 2) 确认流收尾时把指令发回父会话；发完必须清空，否则重渲染会反复发
    assert "if (resumeDirective && !pending.discarded)" in source
    assert "pending.resumeDirective = undefined;" in source
    # 3) 发送要等运行态释放：直接 sendMessage 会被静默 return，指令就丢了
    assert "void dispatchResumeDirective(msg, resumeDirective);" in source
    assert "const dispatchResumeDirective = async" in source
    assert "await sendMessage({ content: directive });" in source


def test_revoke_session_grant_uses_the_scope_backend_reported():
    """子智能体的授权写在 `父会话::sub::子代理`，撤销必须用这个键。"""
    handlers = _read(HANDLERS)
    assert "sessionGrantScope?: string;" in handlers
    assert "msg.pendingPermission.sessionGrantScope = String(sessionGrant.conversation_id);" in handlers

    source = _read(EMBED)
    assert "pending?.sessionGrantScope || conversationId.value" in source

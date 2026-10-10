"""契约：知识库问答的过程状态项必须能显示为「进行中」并带实时秒表。

背景：后端新增 `category: "knowledge"` 的 pending 状态项「检索完成，正在组织回答」，
用于填补「检索已结束、模型正文尚未到达」的空档。若前端把该类别从超时保护名单摘掉，
异常中断的条目会永远显示「进行中」；若把它放进 NON_LIVE_TIMER 名单，则会失去秒表
（用户正需要「已经等了多久」这个信号）。本文件不测新功能，只防止这三项既有能力回退。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PROCESS_TIMELINE = ROOT / "frontend/src/utils/processTimeline.ts"
SSE_HANDLERS = ROOT / "frontend/src/utils/agentscopeSseHandlers.ts"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_knowledge_category_is_protected_from_stale_pending():
    source = _read(SSE_HANDLERS)

    line = next(
        (row for row in source.splitlines() if "STALE_PENDING_CATEGORIES" in row and "new Set(" in row),
        "",
    )
    assert line, "必须保留 STALE_PENDING_CATEGORIES 定义"
    assert '"knowledge"' in line, "knowledge 挂起项必须有超时保护，否则永远转圈"


def test_knowledge_category_shows_live_timer():
    """knowledge 是「机器正在跑」的类别，必须显示实时秒表。"""
    source = _read(PROCESS_TIMELINE)
    block = source[source.index("export const NON_LIVE_TIMER_CATEGORIES") :]
    block = block[: block.index("]);")]

    assert '"knowledge"' not in block, "knowledge 不得进入「等待用户操作」名单"
    assert '"permission"' in block and '"external"' in block, "该名单只装等待用户操作的类别"


def test_embed_dispatches_agentscope_events_for_process_items():
    """过程事件实时化后，「模型调用: <模型名>」pending 项由该分发器渲染。"""
    embed = _read(EMBED)
    handlers = _read(SSE_HANDLERS)

    assert embed.count("dispatchAgentscopeStreamEvent(") >= 3, "EmbedChat 的三处流式入口都要分发"
    assert 'case "model_call":' in handlers
    assert "handleModelCallEvent" in handlers

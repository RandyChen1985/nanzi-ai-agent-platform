"""切会话收尾不得把历史加载堵死。

历史侧栏是可以来回连点的。旧链路里每点一次都会同步发一条
``POST /chat/conversation/{id}/finalize``，而服务端那次请求要跑 LLM + embedding 生成
会话摘要（线上实测均值 21.8s、最坏 57s）。浏览器对同源只有 6 条 HTTP/1.1 连接，
连点几次就全被这些长请求占满，最后一次点击的 ``GET /chat/history`` 只能排队，
界面一直停在「正在加载历史记录...」，几十秒后才恢复。

修复分成两层，这里把两层都锁住：
- 服务端：接口立即答复，摘要合并转后台，不再占住调用方的连接；
- 前端：连点合并成一次收尾，只对「真正离开」的会话发。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _finalize_endpoint_body() -> str:
    source = _source(CHAT_ENDPOINT)
    start = source.index("async def finalize_conversation(")
    # 端点之后的第一个同级装饰器即可视为边界；这里取固定长度的窗口足够覆盖函数体。
    return source[start:start + 1600]


def test_finalize_endpoint_does_not_await_summary_merge():
    body = _finalize_endpoint_body()
    assert "asyncio.create_task(" in body
    # 同步等待摘要就是本次卡顿的根因，不能再出现
    assert "await SessionSummaryService.finalize_session(" not in body
    assert "result = await SessionSummaryService.finalize_session(" not in body
    # 后端任务必须被强引用持有，否则可能在执行前被 GC 回收
    assert "_finalize_session_tasks.add(task)" in body
    assert "task.add_done_callback(_finalize_session_tasks.discard)" in body


def test_finalize_background_task_swallows_errors():
    source = _source(CHAT_ENDPOINT)
    handler_start = source.index("async def _run_finalize_session(")
    handler = source[handler_start:handler_start + 700]
    assert "except Exception as exc:" in handler
    assert "logger.warning(" in handler


def test_embed_chat_debounces_finalize_on_rapid_switching():
    source = _source(EMBED_CHAT)
    start = source.index("const finalizeConversationInBackground = (cid: string) => {")
    body = source[start:start + 900]
    # 有防抖：定时器 + 覆盖上一个待收尾会话
    assert "finalizeConversationTimer = setTimeout(" in body
    assert "clearTimeout(finalizeConversationTimer)" in body
    # 定时器到期时又切回来了就不收尾
    assert "target === conversationId.value" in body
    # 连点不应每次都直接打网络
    assert "void finalizeConversation(cid, embedAuthHeaders());" not in body


def test_pending_finalize_is_cancelled_on_identity_and_unmount():
    source = _source(EMBED_CHAT)
    assert "const cancelPendingConversationFinalize = () => {" in source
    # 换身份：不能拿新身份去 finalize 上一位用户的会话
    identity_start = source.index("const resetSessionForIdentityChange = (")
    identity_body = source[identity_start:identity_start + 2600]
    assert "cancelPendingConversationFinalize();" in identity_body
    # 卸载：定时器不该在组件销毁后再发请求
    assert source.count("cancelPendingConversationFinalize();") >= 3
    assert "cancelPendingConversationFinalize();" in source[
        source.index("embedUnmounted = true;"):source.index("embedUnmounted = true;") + 1800
    ]

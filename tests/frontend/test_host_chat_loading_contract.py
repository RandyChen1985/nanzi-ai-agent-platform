"""宿主 Chat.vue 的 loading 遮罩必须在超时后被撤掉。

遮罩是 `absolute inset-0 bg-gray-50/50 backdrop-blur-sm z-20`，**必定压在 iframe
之上**。挂件内部的深色渲染本身是正确的（`.dark` 生效、根容器 `#111827`、高度铺满），
但只要这层遮罩不撤，用户看到的就是「一片白雾、内容隐约可见」。

原先的超时分支只设 `isInitTimedOut = true`，**从不关闭 `loading`**：一旦宿主的
`NANZI_WIDGET_READY` 没被收到、`sendInitConfig` 从未执行，遮罩就会永久停留。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CHAT = ROOT / "frontend/src/views/Chat.vue"


def _timeout_block() -> str:
    source = CHAT.read_text(encoding="utf-8")
    start = source.index("timeoutTimer = setTimeout")
    return source[start : source.index("}, 5000);", start)]


def test_loading_overlay_is_dismissed_on_timeout():
    assert "loading.value = false" in _timeout_block()


def test_timeout_still_flags_the_slow_connection():
    """反向锁：撤遮罩不能把「连接有点慢」的提示一起弄丢。"""
    assert "isInitTimedOut.value = true" in _timeout_block()

"""契约：生成中「上下文用量」浮标仍可点击。

用户诉求（原文）：「发消息后，输入框都锁定了，我想那个 上下文的浮标不要锁定，不然我无法点击」

根因：`toggleContextUsageDetails` 开头有 `if (isInteractionLocked.value) return;`，而
`isInteractionLocked = isProcessing || isSubmitting`——生成中点击直接被吞掉。按钮自身没有
`disabled`、容器还显式给了 `pointer-events-auto`，所以样式上是可点的，表现成「点了没反应」，
比明确置灰更难判断。

这个浮标是**只读信息**：不参与发送、不撞会话锁、点了也不会改变正在跑的那一轮，没有理由
跟着输入框一起锁。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
INDICATOR = 'data-testid="context-usage-indicator"'


def _source() -> str:
    return CHAT_INPUT.read_text(encoding="utf-8")


def _body(signature: str) -> str:
    source = _source()
    start = source.index(signature)
    return source[start : source.index("\n};", start)]


def test_toggling_context_usage_is_not_gated_by_the_interaction_lock():
    body = _body("const toggleContextUsageDetails = async () => {")
    # 断言具体语句而非「是否出现该标识」：修复后的注释里正当地提到了它。
    assert "if (isInteractionLocked.value) return;" not in body, "生成中点击仍被交互锁吞掉"
    assert "showContextUsageDetails.value = !showContextUsageDetails.value" in body, (
        "去掉守卫时把切换逻辑本身也弄丢了"
    )


def test_the_indicator_button_is_not_disabled():
    """防另一种改法：把锁加到按钮上，效果一样是点不了。"""
    source = _source()
    start = source.index(INDICATOR)
    block = source[start : source.index("</button>", start)]
    assert "disabled" not in block
    assert "pointer-events-none" not in block


def test_the_indicator_keeps_its_accessibility_hooks():
    source = _source()
    start = source.index(INDICATOR)
    block = source[start : source.index("</button>", start)]
    assert ':aria-expanded="showContextUsageDetails"' in block
    assert 'aria-haspopup="dialog"' in block

"""密钥输入框「小眼睛」的垂直居中契约。

眼睛按钮是 `absolute inset-y-0 ... flex items-center`：它的居中基准是**最近的 relative
祖先**。通用密钥字段那个 `relative` 之前裹着输入框 + 下方说明文字（`image_api_key` 的
脱敏说明）+ 「测试连接」按钮，`inset-y-0` 于是按整块高度算，眼睛被推到输入框下沿，
看起来像没对齐。

修法与 `embed_api_key` 保持一致：`relative` 只包输入框和眼睛，说明文字、测试按钮放到
外层。这个契约盯的就是这条边界——以后往里加说明或按钮，不能再塞进 relative 里。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CONFIG = "frontend/src/views/SystemConfig.vue"


def _source() -> str:
    return (ROOT / CONFIG).read_text(encoding="utf-8")


def _div_block(source: str, start: int) -> str:
    """从 `<div ...>` 起点取到与之配对的 `</div>`，用来精确圈定「这个定位容器裹了谁」。"""
    depth = 0
    i = start
    while i < len(source):
        next_open = source.find("<div", i)
        next_close = source.find("</div>", i)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            i = next_open + 4
        else:
            depth -= 1
            i = next_close + 6
            if depth == 0:
                return source[start:i]
    return source[start:]


def test_generic_secret_wrapper_is_not_the_positioning_context():
    """外层容器不能带 relative，否则下面的说明文字会把眼睛的居中基准撑开。"""
    source = _source()
    assert (
        'v-else-if="item.is_secret && item.key !== \'embed_api_key\'" class="relative"'
        not in source
    )
    assert '<div v-else-if="item.is_secret && item.key !== \'embed_api_key\'">' in source


def test_relative_wraps_only_input_and_eye():
    """relative 必须紧包 input + 眼睛这一段。"""
    source = _source()
    # 页面里还有别处用 relative（下拉容器等），先定位这个字段，再往后找它的定位容器
    container_at = source.index(
        "v-else-if=\"item.is_secret && item.key !== 'embed_api_key'\""
    )
    block = _div_block(source, source.index('<div class="relative">', container_at))

    assert 'v-model="item.value"' in block
    assert "toggleSecret(item.key)" in block
    # 说明文字与测试按钮不许出现在这段里
    assert "image_api_key'" not in block
    assert "ragflow_api_key'" not in block


def test_helper_text_sits_outside_the_relative_container():
    """`image_api_key` 的脱敏说明在 relative 之外——顺序上晚于它的闭合。"""
    source = _source()
    relative_start = source.index('<div class="relative">')
    hint = source.index("<p v-if=\"item.key === 'image_api_key'\"")
    assert relative_start < hint

    # relative 到说明文字之间必须是「眼睛 div 收尾 + 包裹层收尾」两层闭合
    between = source[relative_start:hint]
    assert between.count("</div>") >= 2


def test_embed_api_key_keeps_the_same_shape():
    """两处密钥字段的结构要一致，否则下次只修一处。"""
    source = _source()
    field_at = source.index("item.key === 'embed_api_key'")
    block = _div_block(source, source.index('<div class="relative">', field_at))

    assert "inset-y-0 right-0" in block
    # 它的说明文字同样在容器外
    assert "<p " not in block

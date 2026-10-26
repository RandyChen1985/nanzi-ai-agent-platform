"""「失联」标记必须能解释清楚，而且悬停时不能消失。

原来的坑：标记挂在节点右侧并带 `group-hover:hidden`，鼠标一放上去标签就藏起来、
被操作按钮顶替 —— 用户想读状态、想看解释，结果"变成了按钮"。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
TIP = ROOT / "frontend/src/components/HoverTip.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tip() -> str:
    return TIP.read_text(encoding="utf-8")


def test_missing_badge_no_longer_hides_on_hover(page):
    """失联标记不能是 hover 即隐藏的那种。"""
    for line in page.splitlines():
        if "失联" in line and "group-hover:hidden" in line:
            pytest.fail(f"失联标记仍会在悬停时消失: {line.strip()}")


def test_missing_badge_lives_next_to_the_name(page):
    """常驻在名称旁，右侧让给操作按钮，避免两者抢占同一位置。"""
    block = page.split("<!-- Display name + creator -->", 1)[1].split("创建人:", 1)[0]
    assert "dataset.is_missing_in_ragflow" in block
    assert "HoverTip" in block
    assert "失联" in block


def test_tooltip_is_custom_not_native(page):
    """用自定义气泡，不用原生 title（原生一两秒才出现、样式不可控、触摸端没有）。"""
    assert "MISSING_DATASET_HINT" in page
    assert 'title="RAGFlow 侧已失联"' not in page


def test_explanation_covers_cause_and_action(page):
    hint = page.split("const MISSING_DATASET_HINT =", 1)[1].split("\n\n", 1)[0]
    # 成因（被删 / 重建 ID 变了）
    assert "RAGFlow 里删除" in hint
    assert "ID 变" in hint
    # 现状（只剩平台元数据，不能上传解析）
    assert "文档列表为空" in hint
    # 处理办法（可以直接删）
    assert "直接删除" in hint


def test_tooltip_survives_scroll_container_clipping(tip):
    """树是 overflow-y-auto 容器，气泡必须 Teleport 出去 + fixed 定位。

    这是"有 tooltip 但显示不出来"最常见的原因：absolute 子元素被祖先 overflow 裁掉。
    """
    assert "<Teleport" in tip
    assert "fixed" in tip
    assert "getBoundingClientRect" in tip
    # 视口边界收拢，避免贴边溢出
    assert "EDGE_GAP" in tip
    # 下方空间不足时翻到上方
    assert "'top'" in tip and "translateY(-100%)" in tip

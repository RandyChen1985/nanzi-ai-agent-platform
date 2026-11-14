"""发布审核弹窗的窄屏可用性契约。

原来固定两栏（左 w-72 + 右 flex-1）并排，375px 下右栏只剩 ~87px，
只读快照被裁成一条，审核人看不到内容也点不到通过/驳回。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/src/components/agent/AgentSystemReviewDrawer.vue"


def _read() -> str:
    return DRAWER.read_text(encoding="utf-8")


def test_drawer_is_single_column_on_mobile():
    """窄屏列表与详情互相让位，桌面仍并排。"""
    src = _read()

    # 左栏：窄屏全宽，选中后隐藏；桌面固定 288px
    left = src[src.index("<!-- 左：待审队列 -->") :]
    left = left[: left.index("<!-- 右：只读快照")]
    left_tag = left[left.index('class="min-h-0 w-full') : left.index(":class=")]
    assert "md:w-72" in left_tag, "桌面仍要固定宽度"
    assert "w-full" in left_tag
    assert ":class=\"selectedId ? 'hidden' : 'flex'\"" in left

    # 右栏：未选中时窄屏隐藏
    right = src[src.index("<!-- 右：只读快照") :]
    assert "hidden md:flex" in right[:400]


def test_detail_has_back_button_on_mobile():
    """窄屏点进详情后必须有返回，否则出不去。"""
    src = _read()
    back = src[src.index('aria-label="返回待审列表"') :]
    tag = src[src.rindex("<button", 0, src.index('aria-label="返回待审列表"')) :]
    tag = tag[: tag.index(">")]
    assert "md:hidden" in tag, "桌面不需要返回按钮"
    assert "? :" not in tag


def test_mobile_does_not_auto_select_first_item():
    """窄屏打开时停在列表。

    自动选中第一条在单栏布局下等于「一进来就跳进详情」，
    审核人看不到队列里还有别的申请。
    """
    src = _read()
    assert "const isNarrowScreen = ()" in src
    auto = src[src.index("if (!stillThere && firstId") :]
    line = auto[: auto.index("\n")]
    assert "!isNarrowScreen()" in line


def test_actions_stack_on_mobile():
    """窄屏下说明换行、通过/驳回均分一行，不再挤成两个小按钮。"""
    src = _read()
    assert "basis-full" in src
    assert "flex-1 rounded-lg border border-slate-200" in src
    assert "flex-1 rounded-lg bg-primary" in src

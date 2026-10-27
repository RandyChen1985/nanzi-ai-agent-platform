"""chat 页移动端不再常驻平台顶栏，改为「收起 + 把手唤出」。

## 现象

`/dashboard/chat` 整页只有一个 iframe（EmbedChat 挂件），挂件自己已经有一条标题栏
（产品名 / 就绪状态 / 会话菜单 / 「更多 ⋮」）。移动端外面再叠一条 Dashboard 顶栏
（汉堡 + 首页 + 通知 + 主题 + 退出，48px），等于两条 header 抢同一块屏幕：屏幕本来就窄，
顶栏高度还和挂件的标题栏重复，正文可视区被挤掉一大截。

## 采用方案

顶栏在「移动端 + chat 路由」下默认收起，只留 10px 高的布局内把手（居中一条灰条）：

- 点把手 → 顶栏滑出（汉堡 / 通知 / 主题 / 退出全部可用）；
- 点顶栏右侧的收起按钮 → 回到把手上；
- 桌面端、以及其它任何 dashboard 页面完全不受影响（条件里带 `!isMobile`、
  chat 之外的路由恒为显示）；
- 换页、跨断点时回到收起态，避免把上一次的唤出状态带到下一屏。

把手刻意用布局内元素而不是 `fixed` 覆盖：`fixed` 会压在挂件标题栏顶部，
挡住挂件自己的点击热区。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _normalized() -> str:
    source = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", source)


def test_chat_route_is_recognized_by_route_name():
    source = _normalized()

    assert 'const isChatRoute = computed(() => route.name === "AIChat")' in source


def test_header_hides_only_for_mobile_chat_route():
    source = _normalized()

    # 桌面端（!isMobile）恒显示：cond 必须同时包含移动端与 chat 路由两个否定条件
    assert "const showDashboardHeader = computed(" in source
    assert "() => !isMobile.value || !isChatRoute.value || chatChromeOpen.value," in source
    assert 'v-if="showDashboardHeader"' in source


def test_collapsed_state_shows_a_handle_that_expands_the_header():
    source = _normalized()

    assert 'aria-label="展开顶栏"' in source
    assert 'aria-label="收起顶栏"' in source
    assert "@click=\"chatChromeOpen = true\"" in source
    assert "@click=\"chatChromeOpen = false\"" in source


def test_handle_is_in_flow_so_it_does_not_cover_the_widget_header():
    """把手必须是布局内的一条，不能用 fixed 盖在挂件标题栏上。"""
    source = _normalized()
    handle = source[source.index('aria-label="展开顶栏"') : source.index("全局密码到期")]

    assert "fixed" not in handle
    assert "flex-shrink-0" in handle


def test_collapsed_state_resets_on_route_change_and_breakpoint_change():
    source = _normalized()

    # 脚本里两处重置（路由 watcher + 跨断点）；模板里的收起按钮是解包后的 `chatChromeOpen = false`
    assert source.count("chatChromeOpen.value = false") == 2

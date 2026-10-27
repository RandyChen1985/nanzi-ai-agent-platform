"""个人中心各 tab 的加载契约：深链直达任何一个 tab，都必须能自己拉到数据。

「我的权限」踩过的坑：它是页面内联实现，加载只挂在非 immediate 的 watch(activeTab) 上，
而深链会让 activeTab 的初始值就等于它 —— watch 不触发，面板永远是空的。
其余 tab 都是独立子组件，靠 onMounted 加载，换 tab 时随 v-if 卸载重挂，天然不受影响。

这里把两条口径都锁住：新增 tab 要么走子组件 + onMounted，要么在内联实现里显式加载。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/PersonalCenter.vue"

#: tab → 承载它的组件（相对 frontend/src）
TAB_COMPONENTS = {
    "tokens": "components/personal/PersonalTokenUsage.vue",
    "notifications": "components/personal/NotificationConfigs.vue",
    "data": "views/DataPortalHome.vue",
    "skills": "views/SkillsManagement.vue",
    "mcp": "views/McpManagement.vue",
    "tasks": "views/TaskCenter.vue",
    "memory": "components/personal/PersonalMemoryPanel.vue",
}


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("tab,rel_path", sorted(TAB_COMPONENTS.items()))
def test_tab_renders_a_dedicated_component(page, tab, rel_path):
    """每个重 tab 都渲染独立组件：换 tab 即卸载重挂，不依赖父级 watch。"""
    assert f'activeTab === \'{tab}\'' in page
    assert (ROOT / "frontend/src" / rel_path).exists()


@pytest.mark.parametrize("tab,rel_path", sorted(TAB_COMPONENTS.items()))
def test_component_loads_on_mount(tab, rel_path):
    """组件必须在 onMounted 里发起请求，否则深链直达时同样会空白。"""
    source = (ROOT / "frontend/src" / rel_path).read_text(encoding="utf-8")
    assert "onMounted(" in source, f"{rel_path} 没有 onMounted，深链进入时不会加载"
    # 一个文件可能有多处 onMounted（比如 TaskCenter 先挂事件监听、再拉数据），逐个看
    blocks = [b.split("\n})", 1)[0] for b in source.split("onMounted(")[1:]]
    loads = any(
        token in block
        for block in blocks
        for token in ("fetch", "load", "refresh", "apply")
    )
    assert loads, f"{rel_path} 的 onMounted 里都没有发起加载"


def test_permissions_is_the_only_inline_tab_with_custom_loading(page):
    """「我的权限」是内联实现，必须在挂载与切回时各自显式加载（见上一个契约文件）。"""
    mounted = page.split("onMounted(() => {", 1)[1].split("\n})", 1)[0]
    assert "fetchPermissions()" in mounted

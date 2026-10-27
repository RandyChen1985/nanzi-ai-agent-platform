"""个人中心「我的权限」：以它为首个 tab 进入时，权限必须真的加载。

现象：从「权限清单」切到「我的申请」再切回来，面板全空——连「角色标识」都没有；
重新从个人中心进入又正常。

根因不是"数据过期"，而是"从没加载"：负责加载的 watch(activeTab) 不是 immediate，
而深链 ?tab=requests 会让 activeTab 的初始值直接就是 permissions，watch 一次都不触发。
切子 tab 又不会改 activeTab，所以永远是空的。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/PersonalCenter.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_loads_permissions_when_mounted_on_permissions_tab(page):
    """深链 / 通知跳转直接落在「我的权限」时，挂载就要拉一次。"""
    mounted = page.split("onMounted(() => {", 1)[1].split("})", 1)[0]
    assert "fetchPermissions()" in mounted
    assert "activeTab.value === 'permissions'" in mounted


def test_switching_back_to_list_loads_if_never_loaded(page):
    sub_tab = page.split("const switchPermissionSubTab = ", 1)[1].split("\n}", 1)[0]
    assert "fetchPermissions()" in sub_tab
    assert "permissions.value.details" in sub_tab


def test_requests_branch_still_loads_requests(page):
    sub_tab = page.split("const switchPermissionSubTab = ", 1)[1].split("\n}", 1)[0]
    assert "fetchMyRequests()" in sub_tab


def test_tab_watch_keeps_the_fallback(page):
    """从别的 tab 切进「我的权限」仍要走 watch 兜底。"""
    watch_block = page.split("watch(activeTab, (val) => {", 1)[1].split("\n})", 1)[0]
    assert "if (!permissions.value.details)" in watch_block
    assert "fetchPermissions()" in watch_block


def test_deep_link_initial_tab_is_permissions(page):
    """这条断言保护上面的推断：深链确实会让 activeTab 初始就是 permissions。"""
    assert "wantsRequestView" in page
    block = page.split("const activeTab = ref<PersonalTab>(", 1)[1].split("const permissionSubTab", 1)[0]
    assert "wantsRequestView" in block
    assert "'permissions'" in block

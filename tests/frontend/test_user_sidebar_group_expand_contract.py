"""侧栏分组展开状态：只回答「我在哪一组」，不记历史。

曾经的实现把展开状态写进 localStorage，并在首帧（`firstRouteSync`）无条件恢复它。
后果：某个组只要被展开过一次——去过专家页，或者点过父项——之后在**任何页面**刷新都会
把它撑开。用户在聊天页刷新，看到「专家·技能·连接器」自己展开，第一反应就是
「我没点它，干嘛要张开」。

展开状态的信息来源应该只有一个：当前路由落在哪一组。盘里的历史状态回答不了
"我在哪"，它只回答"我以前在哪"。
"""

from pathlib import Path

SIDEBAR = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/components/layout/UserModeSidebar.vue"
)


def _source() -> str:
    return SIDEBAR.read_text(encoding="utf-8")


def test_expanded_state_is_not_persisted():
    """不再持久化：没有恢复方，写盘只会制造"我没点它"的困惑。"""
    source = _source()
    for dead in (
        "nanzi_user_mode_expanded",
        "persistExpandedState",
        "readExpandedCache",
        "expandedCacheKey",
        "EXPANDED_CACHE_PREFIX",
    ):
        assert dead not in source, f"{dead} 应该已经移除"


def test_groups_start_collapsed():
    """初始值必须是收起的，否则首帧会先撑开再被路由打回，肉眼看到一次闪动。"""
    source = _source()
    block = source.split("const expanded = reactive", 1)[1].split("})", 1)[0]
    assert "experts: false" in block
    assert "more: false" in block
    assert "readExpandedCache" not in block


def test_route_drives_expansion_without_keeping_history():
    """路由变化一律覆盖，不保留"上次展开过"的组。"""
    source = _source()
    watch_block = source.split("watch(\n  () => route.path", 1)[1].split("})", 1)[0]
    assert "firstRouteSync" not in source
    assert "const containsCurrent = item.children.some((child) => pathMatches(path, child.path))" in watch_block
    assert "expanded[item.key] = containsCurrent" in watch_block
    # 首帧就同步，保证第一帧渲染时展开状态已经正确
    assert "{ immediate: true }" in source


def test_current_group_still_expands():
    """当前页面所在的组必须展开，否则看不出自己在哪。"""
    source = _source()
    assert "item.children.some((child) => pathMatches(path, child.path))" in source


def test_manual_toggle_still_works():
    """点父项仍能手动展开/收起，且不写盘。"""
    source = _source()
    assert "expanded[item.key] = !isOpen" in source
    assert "expanded[item.key] = true" in source  # 「点即进入」时先展开

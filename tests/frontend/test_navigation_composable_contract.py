"""侧栏导航组合式函数的契约（`frontend/src/composables/useNavigation.ts`）。

菜单入库后，前端不再持有菜单数据，只持有一份「服务端已按权限过滤」的快照。
这里钉住四个容易退化的点：

1. 快照必须带版本号，并用 `version` 参数做条件请求（否则每次刷新都重传整棵树）；
2. 请求失败时**保留**旧快照（侧栏不能因为一次网络抖动变成空白）；
3. 登出要清快照（否则换账号后先闪出上一个身份的菜单）；
4. 落地页来自快照，缺省退回工作台——守卫是同步的，不能在这里发请求。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = Path("frontend/src/composables/useNavigation.ts")
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_snapshot_is_versioned_and_cached_locally():
    source = _source()

    assert "nav_snapshot_v1" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert "localStorage.getItem(STORAGE_KEY" in source
    assert "version" in source


def test_refresh_uses_conditional_request_and_handles_unchanged():
    """`?version=` + `{unchanged:true}` 是这套缓存的关键：命中时后端只回一个短响应。"""
    source = _source()

    assert "/api/portal/menus/nav" in source
    assert "params" in source
    assert "data.unchanged" in source


def test_failed_refresh_keeps_the_previous_snapshot():
    """失败只能是「stale」，不能清空菜单——清空的后果是用户登录后看到空侧栏。"""
    source = _source()

    assert "isStale" in source
    # 失败只置 stale/error：catch 分支里不得清空 nodes（清空就变成"侧栏空白"）
    catch_block = source[source.index("} catch (error) {"):]
    catch_block = catch_block[: catch_block.index("} finally")]
    assert "state.nodes" not in catch_block
    assert "state.stale = true" in catch_block


def test_logout_clears_the_snapshot():
    """快照属于某个身份：不清会让下一个人看到上一个人的菜单。"""
    session = Path("frontend/src/utils/userSession.ts").read_text(encoding="utf-8")

    assert "clearNavigationSnapshot" in session


def test_landing_path_is_read_from_the_snapshot_without_a_request():
    source = _source()

    assert "getSnapshotLandingPath" in source
    assert "landing_path" in source
    # 守卫是同步的，读快照的入口不能返回 Promise
    assert "export function getSnapshotLandingPath(): string" in source


def test_find_node_is_exported_for_the_external_menu_page():
    """外链容器页需要从快照反查 URL：查不到就不渲染 iframe（等于无权限）。"""
    source = _source()

    assert "findNode" in source
    assert "node_type" in source

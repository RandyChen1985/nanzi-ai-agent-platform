"""侧栏导航组合式函数的契约（`frontend/src/composables/useNavigation.ts`）。

菜单入库后，前端不再持有菜单数据，只持有一份「服务端已按权限过滤」的快照。
这里钉住几个容易退化的点：

1. 快照必须带版本号，并用 `version` + `signature` 做条件请求（否则每次刷新都重传整棵树，
   或者更糟：只比版本号，换账号后一直吃上一个人的快照）；
2. 请求失败时**保留**旧快照（侧栏不能因为一次网络抖动变成空白）；
3. 快照绑定登录身份（`user_id`），登出要清快照与本地身份数据；
4. 迟到的旧响应不能覆盖新身份（请求序号）；
5. 落地页来自快照，缺省退回工作台——守卫是同步的，不能在这里发请求。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = Path("frontend/src/composables/useNavigation.ts")
SESSION_CLEANUP = Path("frontend/src/utils/sessionCleanup.ts")
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_snapshot_is_versioned_and_cached_locally():
    source = _source()
    cleanup = SESSION_CLEANUP.read_text(encoding="utf-8")

    # 存储键与清理逻辑共用同一常量：两处各写一份字面量迟早会漂移
    assert "NAV_SNAPSHOT_STORAGE_KEY" in source
    assert "nav_snapshot_v1" in cleanup
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert "localStorage.getItem(STORAGE_KEY" in source
    assert "version" in source


def test_snapshot_is_bound_to_the_current_identity():
    """快照属于某个账号：没有 user_id 或与当前 user_info 不一致时必须丢弃。"""
    source = _source()

    assert "user_id: string" in source
    assert "snapshotOwner !== ownerId" in source
    assert "currentUserId" in source


def test_refresh_uses_conditional_request_and_handles_unchanged():
    """`?version=&signature=` + `{unchanged:true}` 是这套缓存的关键。"""
    source = _source()

    assert "/api/portal/menus/nav" in source
    assert "params" in source
    assert "data.unchanged" in source
    # 只带 version 的条件请求会被服务端拒绝（回完整树），前端这里也不该发
    assert "state.signature" in source


def test_late_response_from_another_identity_is_discarded():
    """登出后迟到的旧响应不能写回 state/快照（requestSeq 作废在途请求）。"""
    source = _source()

    assert "requestSeq" in source
    assert "seq !== requestSeq" in source
    assert "requestSeq += 1" in source


def test_failed_refresh_keeps_the_previous_snapshot():
    """失败只能是「stale」，不能清空菜单——清空的后果是用户登录后看到空侧栏。"""
    source = _source()

    assert "isStale" in source
    # 失败只置 stale/error：catch 分支里不得清空 nodes（清空就变成"侧栏空白"）
    catch_block = source[source.index("} catch (error) {"):]
    catch_block = catch_block[: catch_block.index("} finally")]
    assert "state.nodes" not in catch_block
    assert "state.stale = true" in catch_block


def test_logout_clears_the_snapshot_and_user_scoped_storage():
    """快照属于某个身份：不清会让下一个人看到上一个人的菜单。"""
    session = Path("frontend/src/utils/userSession.ts").read_text(encoding="utf-8")

    assert "clearNavigationSnapshot" in session
    assert "clearUserScopedStorage" in session


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

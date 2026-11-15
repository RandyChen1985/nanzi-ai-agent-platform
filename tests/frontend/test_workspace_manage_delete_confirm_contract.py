"""契约：管理员删公共目录，两个入口的确认文案都必须说「永久删除」。

背景（线上问题）：公共目录不属于任何人的回收站，后端 `/fs/delete-entry` 对
`admin + 公共路径` 直接 rmtree/remove（`trashed_path` 为空）。前端如果仍提示
「移到回收站」，管理员会以为还能还原；如果误派发到 `/fs/purge-entry`，则直接被
403「只能操作回收站内的文件或文件夹」挡住。

因此锁两件事：
1. 管理页 `askDelete` 按 `isPublicWorkspaceItem` 分流文案，但接口仍是 delete-entry；
2. 抽屉与管理页共用同一个公共条目判定，不得各自内联一份。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "WorkspaceManage.vue"
DRAWER = ROOT / "frontend" / "src" / "components" / "embed" / "WorkspaceBrowserDrawer.vue"
ENTRIES = ROOT / "frontend" / "src" / "utils" / "workspaceActionEntries.ts"
ROW_ACTIONS = ROOT / "frontend" / "src" / "components" / "workspace" / "WorkspaceRowActions.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ask_delete_body() -> str:
    text = _read(PAGE)
    start = text.index("const askDelete = (item: FsItem) => {")
    end = text.index("const askPurge = (item: FsItem) => {")
    return text[start:end]


def _batch_delete_body() -> str:
    text = _read(PAGE)
    start = text.index("const askBatchDelete = () => {")
    end = text.index("const runConfirmAction = async () => {")
    return text[start:end]


def test_manage_page_splits_confirm_copy_by_publicity():
    body = _ask_delete_body()

    assert 'scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value)' in body
    # 公共目录：明确永久删除 + 说明不会进回收站
    assert 'title: "永久删除"' in body
    assert 'confirmText: "永久删除"' in body
    assert "属于公共目录" in body
    assert "不会进入回收站" in body
    assert "无法恢复" in body
    # 私有目录：沿用回收站文案，不能被「永久删除」淹没
    assert 'title: "移到回收站"' in body
    assert "可以再还原回来" in body


def test_manage_page_delete_never_dispatches_to_purge_endpoint():
    """公共目录的删除必须走 delete-entry：purge-entry 只接受回收站内路径。"""
    body = _ask_delete_body()

    assert "await deleteWorkspaceEntry(item.path)" in body
    assert "purgeWorkspaceEntry" not in body


def test_manage_page_toast_matches_what_backend_actually_did():
    body = _ask_delete_body()

    assert 'showToast("已永久删除", "success")' in body
    assert 'showToast("已移到回收站", "success")' in body


def test_publicity_judgement_is_shared_not_reinlined():
    entries = _read(ENTRIES)
    drawer = _read(DRAWER)
    page = _read(PAGE)

    assert "export function isPublicWorkspaceItem(" in entries
    # 抽屉：委托纯函数模块
    assert drawer.count("isPublicWorkspaceItem") >= 2
    assert "return !isPathInUserWorkspace(item.path)" not in drawer
    # 管理页：同一个判定
    assert "isPublicWorkspaceItem(item, userWorkspaceRoot.value)" in page


def test_manage_page_rows_use_the_same_scene_builder_as_the_drawer():
    """管理页行操作曾把场景写死成 regular-item，普通用户因此看到点了就 403 的删除。"""
    rows = _read(ROW_ACTIONS)
    page = _read(PAGE)

    assert "buildWorkspaceItemMenuContext" in rows
    assert 'scene: props.inTrash ? "trash-item" : "regular-item"' not in rows
    # 列表与卡片两个视图都要把管理员视图 / 保护目录 / 本人工作区根传下去
    assert page.count(":is-admin-scope=\"scope === 'admin_all'\"") == 2
    assert page.count(':is-system-protected="SYSTEM_PROTECTED.has(item.name)"') == 2
    assert page.count(':user-workspace-root="userWorkspaceRoot"') == 2


def test_batch_delete_copy_separates_public_permanent_items():
    """管理页批量删除曾一律说「可以再还原回来」，勾选公共目录时与后端行为不符。"""
    body = _batch_delete_body()

    assert 'scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value)' in body
    assert "项将被移到回收站，可以再还原回来" in body
    assert "属于公共目录，将被永久删除且不会进入回收站" in body
    # 两种确认按钮文案都要在，且公共条目在时不能只说「移到回收站」
    assert 'confirmText: permanentCount > 0 ? "永久删除" : "移到回收站"' in body


def test_batch_delete_still_uses_delete_entry_and_reports_both_outcomes():
    body = _batch_delete_body()

    assert "deleteWorkspaceEntry" in body
    assert "purgeWorkspaceEntry" not in body
    assert "项已移到回收站" in body
    assert "项已永久删除" in body

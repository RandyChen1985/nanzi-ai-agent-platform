"""契约：dashboard/workspace Review 缺陷批量修复防回归。

锁定本次全面 Review 中发现并修复的关键回归点：
1. 面包屑走 buildWorkspaceBreadcrumbs，避免把公共目录绝对路径错拼进本人工作区；
2. 行内菜单 WorkspaceRowActions 透传 canPreviewWorkspaceFile / isTrashRoot / canManage，并使用 Teleport + 视口翻转；
3. 全目录搜索下批量删除从 sourceItems 取值，并接入 workspaceBatchSelection（含半选态与单条容错）；
4. 解压复用 WorkspaceDirectorySaveDialog（公共只读压缩包可解到本人工作区）；
5. 最近文件读写同步（PUT /api/v1/chat/fs/recent-files）且使用 displayFileName；
6. 画布默认非钉住（defaultPinned: false）并监听 @content-saved；
7. 卡片双击不再与内部 @click 冲突导致 3 连请求，且无后缀文件 extOf 不误判；
8. 后端 fs.py 保护系统保留根与用户工作区根，过滤被禁平台技能，且重 IO 走线程池。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "WorkspaceManage.vue"
ROW_ACTIONS = ROOT / "frontend" / "src" / "components" / "workspace" / "WorkspaceRowActions.vue"
CANVAS_COMPOSABLE = ROOT / "frontend" / "src" / "composables" / "chat" / "useWorkspaceCanvas.ts"
FS_ENDPOINT = ROOT / "app" / "api" / "v1" / "endpoints" / "fs.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_row_actions_wires_previewable_trash_root_can_manage_and_teleport():
    src = _read(ROW_ACTIONS)
    assert "canPreviewWorkspaceFile(props.item.name)" in src
    assert "isPreviewable: true" not in src
    assert "isTrashRoot: props.isTrashRoot" in src
    assert "canManage: props.canManage && !props.isSystemProtected" in src
    assert '<Teleport to="body">' in src
    assert "updateMenuPosition" in src
    assert 'emit("empty-trash")' in src


def test_page_uses_breadcrumbs_and_batch_selection_helpers():
    src = _read(PAGE)
    assert "buildWorkspaceBreadcrumbs(" in src
    assert "selectAllState(" in src
    assert "toggleSelectAllPaths(" in src
    assert "planBatchDelete(" in src
    assert ':indeterminate="selectionState === \'partial\'"' in src
    # 全目录搜索下批量删除必须查 sourceItems，不能只查 items.value
    assert "new Map(sourceItems.value.map(" in src


def test_page_extract_reuses_directory_save_dialog():
    src = _read(PAGE)
    assert "WorkspaceDirectorySaveDialog" in src
    assert "const submitExtract = async (" in src
    assert "targetDir: payload.parentPath" in src


def test_page_records_recent_files_and_displays_clean_name():
    src = _read(PAGE)
    assert 'axios.put("/api/v1/chat/fs/recent-files"' in src
    assert "void recordRecentFile(item)" in src
    assert "{{ displayFileName(recent.name) }}" in src


def test_page_canvas_defaults_unpinned_and_refreshes_on_save():
    page = _read(PAGE)
    composable = _read(CANVAS_COMPOSABLE)
    assert "defaultPinned?: boolean" in composable
    assert "defaultPinned: false" in page
    assert '@content-saved="handleCanvasSaved"' in page


def test_page_fixes_card_double_click_ext_of_and_upload_controls():
    src = _read(PAGE)
    # 卡片视图不能再同时给内层按钮绑 @click=handlePreview 导致双击三连触发
    assert '@click="item.is_dir ? navigate(item.path) : handlePreview(item)"' not in src
    # 无后缀文件不能把文件名本身当扩展名
    assert 'name.lastIndexOf(".")' in src
    assert '(name.split(".").pop() || "")' not in src
    # 上传队列控制与自动清理
    assert "shouldAutoDismissUploads(" in src
    assert "cancelUpload(task.id)" in src
    assert "retryUpload(task.id)" in src
    # 按显示名排序
    assert 'displayFileName(a.name).localeCompare(displayFileName(b.name), "zh-CN")' in src


def test_page_guards_async_races_and_search_loading_state():
    src = _read(PAGE)
    assert "let loadSeq = 0" in src
    assert "let searchSeq = 0" in src
    assert "if (seq !== loadSeq) return" in src
    assert "if (seq !== searchSeq) return" in src
    assert "const searching = ref(false)" in src


def test_backend_protects_reserved_roots_filters_skills_and_offloads_io():
    src = _read(FS_ENDPOINT)
    assert "def _assert_mutable_entry_path(" in src
    assert "def _is_protected_root_entry(" in src
    assert "is_forbidden_platform_skill_path(entry.path, user_info)" in src
    assert "is_forbidden_platform_skill_path(os.path.join(root, d), user_info)" in src
    assert "await run_in_threadpool(_search_files_sync)" in src
    assert "await run_in_threadpool(_empty_trash_sync, trash_dir)" in src
    # data 根下的平台级 uploads / sessions / sandbox 与前端保护口径一致
    assert "or parent == norm_base" in src


def test_page_trash_scope_covers_subdirectories():
    """回收站按子树判定。

    删掉的是文件夹时它会整个躺在 `.trash/` 里，用户点进去如果仍按普通目录处理，
    行菜单会退化成「重命名 / 删除」，后端也只拦 `.trash` 根 —— 等于能把条目再套一层。
    """
    src = _read(PAGE)
    assert "isWithinDirectory(currentPath.value, `${userWorkspaceRoot.value}/.trash`)" in src
    # 回收站子树内不允许批量勾选（canSelect 直接看 inTrash）
    assert (
        "const canSelect = (item: FsItem) => !inTrash.value && !isVirtualRoot.value && canManageItem(item)"
        in src
    )
    # 卡片不再保留误导性的手型光标（单击没有动作，只有双击才打开）
    assert "min-w-0 flex-1 cursor-pointer text-left" not in src


def test_page_keeps_failed_batch_delete_selection_and_reports_load_failure():
    src = _read(PAGE)
    # 批量删除失败的条目刷新后仍保留勾选，便于直接重试
    assert "const failedPaths: string[] = []" in src
    assert "await refreshCurrentViewAsync()" in src
    assert "failedPaths.forEach((path) => selectedPaths.add(path))" in src
    # 读取目录失败时保留旧列表（currentPath 未变，清空会让旧目录看起来「空了」）
    # load 已拆成 loadInner（真实读取）+ load（并发去重包装），断言仍锁「读取失败不清列表」
    load_block = src.split("const loadInner = async (")[1].split("const navigate =")[0]
    assert "items.value = []" not in load_block
    assert "loadFailed.value = true" in load_block
    assert 'loadFailed ? "目录读取失败，请重试"' in src


def test_page_persists_type_filter_with_other_prefs():
    src = _read(PAGE)
    assert "typeFilter?: TypeFilterKey" in src
    assert "typeFilter: TYPE_FILTER_KEYS.find(" in src
    # 四项偏好统一走一个 watch 落盘（此前两个 watch 会互相覆盖同一个 localStorage 键）
    assert "watch([viewMode, sortBy, sortDir, typeFilter], persistPrefs)" in src
    assert "typeFilter: typeFilter.value" in src

"""契约：dashboard/workspace 全面缺陷修复与生产力增强防回归。

锁定本次改造的关键点：
1. 卡片视图触屏可单击进入文件夹，行操作菜单恢复 open-dir 并补充移动/复制/打包下载/AI分析；
2. 回收站子目录清空后强制跳回 `.trash` 根，不刷新已删除子目录；
3. 刷新保留当前目录搜索词（preserveSearch），空状态区分类型筛选并提供重置入口；
4. 上传面板区分「清除已完成（clearCompleted）」与「全部清空」，拖入文件夹走 webkitGetAsEntry 拦截；
5. 搜索支持截断提示（searchTruncated）、单字提示与相对路径展示；
6. 回收站独立多选支持批量还原/批量彻底删除（与普通删除物理隔离）并剥离时间戳前缀展示；
7. URL `?path=` 同步、刷新按钮、用量统计、双向排序、右键菜单、快捷键与画布联动；
8. Node 真跑 `parseTrashedFileName`、`formatRelativeWorkspacePath`、`clearCompleted`、`setMaxBytes`。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "WorkspaceManage.vue"
ROW_ACTIONS = ROOT / "frontend" / "src" / "components" / "workspace" / "WorkspaceRowActions.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_manage_page_fixes_card_click_empty_trash_and_preserve_search():
    src = _read(PAGE)
    row = _read(ROW_ACTIONS)
    # 1. 行操作不再滤掉 open-dir，卡片目录支持单击进入
    assert '.filter((entry) => entry.key !== "open-dir")' not in row
    assert '@click="item.is_dir ? navigate(item.path) : undefined"' in src
    # 2. 清空回收站按「当前所在回收站根」传参并回到该根，避免子目录内清空 404；
    #    传参同时保证管理员浏览他人回收站时不会误清自己的
    empty_block = src.split("const askEmptyTrash = () => {")[1].split("const askBatchDelete = () => {")[0]
    assert "emptyWorkspaceTrash(trashRoot || undefined)" in empty_block
    assert "void load(trashRoot)" in empty_block
    # 3. 刷新保留当前搜索词
    assert "preserveSearch?: boolean" in src
    assert "preserveSearch: true" in src
    # 4. 类型筛选为空有独立提示和重置入口
    assert "显示全部类型" in src
    assert "resetFilters" in src


def test_manage_page_enhances_search_upload_and_trash_batch():
    src = _read(PAGE)
    # 搜索截断、单字提示与相对路径
    assert "searchTruncated" in src
    assert "searchTooShortHint" in src
    assert "formatRelativeWorkspacePath(" in src
    # 上传：clearCompleted 只清成功项，文件夹拖拽拦截，动态上限
    assert "clearCompletedUploads" in src
    assert "webkitGetAsEntry" in src
    assert "setMaxBytes(" in src
    # 回收站独立批量操作与干净展示名
    assert "selectedTrashPaths" in src
    assert "askBatchRestore" in src
    assert "askBatchPurge" in src
    assert "parseTrashedFileName(" in src


def test_manage_page_wires_url_sync_context_menu_move_zip_ai_and_shortcuts():
    src = _read(PAGE)
    assert "syncPathToUrl(" in src
    assert "openItemContextMenu(" in src
    assert "openEmptyContextMenu(" in src
    assert "moveWorkspaceEntry(" in src
    assert "batchDownloadWorkspaceEntries(" in src
    assert "fetchWorkspaceUsage(" in src
    assert "handleAskAi" in src
    assert '@analyze-output="handleCanvasAnalyzeOutput"' in src
    assert "syncCanvasOnPathAffected(" in src
    assert "onGlobalKeydown" in src
    assert "toggleHeaderSort(" in src
    assert "toggleSortDir" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_new_pure_helpers_behaviour_via_node():
    script = """
import assert from 'node:assert/strict'
import { parseTrashedFileName } from './frontend/src/utils/workspaceFileName.ts'
import { buildWorkspaceBreadcrumbs, formatRelativeWorkspacePath } from './frontend/src/utils/workspacePath.ts'
import { createWorkspaceUploadQueue } from './frontend/src/utils/workspaceUploadQueue.ts'

// 1. 回收站文件名解析（含产物 UUID 叠加）
const t1 = parseTrashedFileName('1732671234_a1b2c3d4_report.pdf')
assert.equal(t1.isTrashedFormat, true)
assert.equal(t1.displayName, 'report.pdf')
assert.equal(t1.originalName, 'report.pdf')
assert.equal(t1.deletedAt, 1732671234)

const t2 = parseTrashedFileName('1732671234_a1b2c3d4_26399a7a80ed41e58b2431a2ca949a34_chart.png')
assert.equal(t2.displayName, 'chart.png')
assert.equal(t2.originalName, '26399a7a80ed41e58b2431a2ca949a34_chart.png')

const t3 = parseTrashedFileName('plain.txt')
assert.equal(t3.isTrashedFormat, false)
assert.equal(t3.displayName, 'plain.txt')
assert.equal(t3.deletedAt, null)

// 2. 相对路径格式化 & .trash 面包屑友好显示
const ROOT = '/app/data/agent_workspaces/u1_alice'
assert.equal(formatRelativeWorkspacePath(`${ROOT}/docs/sub`, ROOT), '我的目录/docs/sub')
assert.equal(formatRelativeWorkspacePath(`${ROOT}/.trash/old_dir`, ROOT), '我的目录/回收站/old_dir')
assert.equal(formatRelativeWorkspacePath('/app/data/docs/manual', ROOT), '公共资源/docs/manual')
const crumbs = buildWorkspaceBreadcrumbs({ currentPath: `${ROOT}/.trash/sub`, userWorkspaceRoot: ROOT })
assert.equal(crumbs[1].label, '回收站')
assert.equal(crumbs[2].label, 'sub')

// 3. clearCompleted 只清 done，保留 failed；setMaxBytes 动态生效
const q = createWorkspaceUploadQueue({
  upload: async (file) => {
    if (file.name === 'bad.bin') throw new Error('boom')
    return { data: { data: { stored_name: file.name } } }
  },
})
q.setMaxBytes(100)
q.enqueue([{ name: 'ok.txt', size: 10 }, { name: 'too_big.bin', size: 200 }], '/ws')
await new Promise((r) => setTimeout(r, 20))
assert.equal(q.snapshot().aggregate.done, 1)
assert.equal(q.snapshot().aggregate.failed, 1)
q.clearCompleted()
assert.equal(q.snapshot().tasks.length, 1)
assert.equal(q.snapshot().tasks[0].name, 'too_big.bin')
assert.equal(q.snapshot().tasks[0].status, 'failed')
q.destroy()
console.log('WORK_ENHANCEMENTS_NODE_OK')
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WORK_ENHANCEMENTS_NODE_OK" in proc.stdout


def test_manage_page_regressions_load_dedupe_sort_dir_and_trash_scope():
    """锁定本轮的并发/排序/回收站归属修复，防止回退成「静默丢弃刷新请求」。"""
    src = _read(PAGE)
    # load 并发复用同一个 Promise：await 出去的语义必须是「等这次读取完成」
    assert "inflightLoad" in src
    assert "const loadInner = async" in src
    assert "loadingTargetPath" not in src
    # 排序字段切换时统一重置方向（表头与下拉同口径）
    assert 'watch(sortBy, (next) => {' in src
    assert 'sortDir.value = next === "name" ? "asc" : "desc"' in src
    # 清空回收站按实际所在回收站根定位（admin 浏览他人回收站）
    assert "const activeTrashRoot = computed(" in src
    # 上传上限动态下发仍保留
    assert "setMaxBytes(" in src

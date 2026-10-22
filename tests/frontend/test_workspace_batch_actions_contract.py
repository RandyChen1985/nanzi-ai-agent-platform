"""工作区多选批量动作（全选 + 批量删除）的契约。

锁定四件事：
1. 全选有独立纯逻辑模块，列表为空/半选/全选/取消全选的行为可被真实执行；
2. 表头出现全选框（含半选态），批量操作条同时提供全选、批量删除、批量挂载；
3. 批量删除必须二次确认，并复用仓库通用 ConfirmModal 列出待删项；
4. 删除逐项调用既有接口、失败不中断，且回收站视图不提供批量删除。

行为验证见 tests/frontend/workspace_batch_selection.mjs。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/src/components/embed/WorkspaceBrowserDrawer.vue"
SELECTION_MODULE = ROOT / "frontend/src/utils/workspaceBatchSelection.ts"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return DRAWER.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_batch_selection_helpers_behaviour_via_node():
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "tests/frontend/workspace_batch_selection.mjs",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工作区批量选择测试全部通过" in proc.stdout


def test_batch_selection_module_is_framework_free():
    text = SELECTION_MODULE.read_text(encoding="utf-8")

    assert "export function selectAllState" in text
    assert "export function toggleSelectAllPaths" in text
    assert "export function planBatchDelete" in text
    # 纯逻辑必须能被 node 直接加载：不得引入 vue / axios / 组件
    assert "from 'vue'" not in text
    assert "@/utils/axios" not in text


def test_list_header_exposes_select_all_checkbox_with_indeterminate_state():
    source = _source()
    # 只看列表表头那一段：行内的勾选框本来就有同样的 col-span-1 结构，不能拿它充数
    header = source.split('class="grid grid-cols-12 gap-2 px-4 py-2', 1)[1].split("名称", 1)[0]

    # 表头第一列原来只是一个占位；多选模式下要变成全选框
    assert 'aria-hidden="true"' not in header
    assert "<input" in header
    assert ':indeterminate="batchSelectAllState === \'partial\'"' in header
    assert ':checked="batchSelectAllState === \'all\'"' in header
    # 全选框要有可读名称：读屏用户听到的是「全选当前列表 / 取消全选」
    assert ':aria-label="batchSelectAllState === \'all\' ? \'取消全选\' : \'全选当前列表\'"' in header


def test_batch_bar_offers_select_all_delete_and_mount():
    source = _source()

    assert "toggleSelectAllItems" in source
    assert "{{ batchSelectAllState === 'all' ? '取消全选' : '全选' }}" in source
    assert "批量删除 ({{ batchDeletePlan.deletable.length }})" in source
    assert "批量挂载 ({{ batchSelectedItems.length }})" in source


def test_batch_delete_requires_confirmation_with_item_details():
    source = _source()

    assert "import ConfirmModal from '@/components/ConfirmModal.vue'" in source
    assert "<ConfirmModal" in source
    assert ':message="batchDeleteMessage"' in source
    assert ':details="batchDeleteDetails"' in source
    assert 'details-label="将删除以下项目"' in source
    assert ':loading="batchDeleteSubmitting"' in source
    assert '@confirm="submitBatchDelete"' in source
    # 入口只负责弹确认，不允许直接删
    request_body = source.split("const requestBatchDelete = () => {", 1)[1].split("\n}", 1)[0]
    assert "deleteWorkspaceEntry" not in request_body
    assert "batchDeleteConfirm.value = true" in request_body


def test_batch_delete_isolates_failures_and_keeps_failed_items_selected():
    source = _source()
    submit_body = source.split("const submitBatchDelete = async () => {", 1)[1]

    assert "for (const item of plan.deletable)" in submit_body
    assert "await deleteWorkspaceEntry(item.path)" in submit_body
    assert "failed.push" in submit_body
    # 逐项进度：确认框要能显示 已处理/总数
    assert "done: batchDeleteProgress.value.done + 1" in submit_body
    assert "total: plan.deletable.length" in submit_body
    # 成功项移出勾选，失败项保留供重试
    assert "for (const path of succeededPaths) next.delete(path)" in submit_body


def test_batch_delete_is_hidden_in_trash_view():
    source = _source()

    # 回收站走「清空回收站」；delete-entry 对回收站路径会被后端拒绝
    assert 'v-if="!isTrashView"' in source


def test_selection_is_cleared_when_listing_scope_changes():
    source = _source()

    assert "const resetBatchSelection = () => {" in source
    # 切目录 / 搜索 / 类型筛选 / 关闭抽屉都要清空，避免跨目录误删
    assert source.count("resetBatchSelection()") >= 5

"""按钮与权限的一致性：看不到的能力，按钮就不该亮着。

真正的漏洞从来不是「点不动」，而是「点得到、但一定失败」——
用户会怀疑是自己的操作有问题。这里按区域锁住容易漏的几处。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
WORKSPACE = ROOT / "frontend/src/components/knowledge/DocWorkspace.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workspace() -> str:
    return WORKSPACE.read_text(encoding="utf-8")


def test_doc_selection_follows_bulk_action_permissions(workspace: str) -> None:
    """勾选框跟「能不能批量操作」对齐，而不是跟「能不能管这个库」对齐。

    后端 `parse` / `DELETE documents` 只要元素权限（parse_document /
    delete_document）+ 内容级写权限，不要求是库创建人。这里原先写死 canManage，
    部门协作成员于是看得到批量按钮、却选不中任何文档，功能等于摆设。
    反过来只读库仍然安全：两个能力点都不满足时 canSelectDocs 为 false。
    """
    # 表头全选 + 行内勾选：两处都必须跟着同一个条件
    assert workspace.count('v-if="canSelectDocs"') >= 2
    assert "const canSelectDocs = computed(" in workspace
    assert "props.canManage || props.canDeleteDoc || props.canParse" in workspace
    # 行内勾选框绑定的是当前页选中的 docId
    assert ':checked="selectedIds.includes(doc.id)"' in workspace
    # 只读库（三者全 false）依然没有勾选框
    assert 'v-if="canManage"' in workspace


def test_bulk_bar_buttons_follow_their_own_permission(workspace: str) -> None:
    """批量条不能只看「有没有选中」，每个动作各有各的能力点。"""
    block = workspace.split("批量操作条", 1)[1].split("内容区", 1)[0]
    assert 'v-if="canParse"' in block
    assert 'v-if="canManage"' in block
    assert 'v-if="canDeleteDoc"' in block


def test_sync_button_requires_edit(page: str) -> None:
    """「从 RAGFlow 同步」打的是 POST /datasets/sync，后端要 element:knowledge:edit。"""
    marker = '>从 RAGFlow 同步</button>'
    assert marker in page
    button = page.split(marker, 1)[0][-600:]
    assert 'v-if="canEdit"' in button
    # 流程指引里的「同步」引导同样要挡一层
    assert "if (!canEdit.value) {" in page


def test_list_page_bulk_delete_requires_delete(page: str) -> None:
    """批量删除要 canDelete：能管库不等于能删库。"""
    assert 'v-if="librarySelectedCount && canDelete"' in page


def test_tree_context_menu_skips_missing_datasets(page: str) -> None:
    """失联库只剩「清理失效配置」：新建文件夹/改元数据点了必然 404。"""
    menu = page.split("库节点右键菜单", 1)[1].split("Right Side", 1)[0]
    assert menu.count("!datasetMenu.dataset.is_missing_in_ragflow") == 2


def test_read_only_and_missing_disable_doc_actions(page: str) -> None:
    """只读库、失联库的文档操作要禁用，而不是让用户点完看报错。"""
    assert "isParseButtonDisabled(selectedDocument) || selectedDataset?.is_missing_in_ragflow === true" in page
    for guard in ("canEdit && !isReadOnlyDataset(selectedDataset)",
                  "canParse && !isReadOnlyDataset(selectedDataset)",
                  "canDeleteDocument && !isReadOnlyDataset(selectedDataset)"):
        assert guard in page

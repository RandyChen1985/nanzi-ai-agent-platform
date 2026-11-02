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


def test_doc_selection_requires_manage(workspace: str) -> None:
    """文档勾选必须跟管理权绑定：只读库能勾上就等于能批量删。"""
    assert 'v-if="canManage"' in workspace          # 表头全选
    assert '''v-if="row.kind === 'doc' && canManage"''' in workspace


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

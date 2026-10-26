"""文档重命名 / 启停的前端契约。

这两个动作的坑在于「部分更新」：只想停用文档却把整个文档对象 PUT 回去，
名字、chunk_method 会被一起写成空。所以必须只传要改的字段。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_document_detail_has_rename_and_enable_toggle(page: str) -> None:
    assert "startRenameDocument" in page
    assert "toggleDocumentEnabled" in page
    assert "参与检索" in page


def test_disabled_document_is_marked_in_tree(page: str) -> None:
    assert "isDocDisabled" in page
    assert "已停用" in page
    # 树里要能一眼区分，不能只在详情页显示
    assert "line-through" in page


def test_update_document_sends_only_changed_fields(page: str) -> None:
    segment = page.split("const toggleDocumentEnabled", 1)[1].split("const canViewChunks", 1)[0]
    assert "enabled: next" in segment
    assert "name:" not in segment

    rename = page.split("const confirmRenameDocument", 1)[1].split("const toggleDocumentEnabled", 1)[0]
    assert "{ name }" in rename
    assert "enabled" not in rename

"""虚拟文件夹的写入契约。

目录结构曾经走整份 ``PUT /metadata``：移动一个文档会把前端缓存的
name/visibility 一起回写，并发操作就是 lost update，最坏情况是把公开库
改成私有。现在必须收敛成只写 folder_structure 的独立端点。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_folder_save_uses_dedicated_endpoint(page: str) -> None:
    assert "/folder-structure" in page
    assert "saveFolderStructure" in page


def test_folder_operations_no_longer_put_whole_metadata(page: str) -> None:
    start = page.index("const moveDocumentToFolder")
    end = page.index("const triggerDeleteDataset")
    segment = page[start:end]
    # 注释里可以提「不再走 PUT /metadata」，实际请求路径不能出现
    assert "/metadata`" not in segment
    assert "saveDatasetExtraConfig" not in segment


def test_deleting_document_cleans_folder_reference(page: str) -> None:
    assert "removeDocumentFromFolders" in page
    start = page.index("const confirmDeleteDocument")
    end = page.index("const parsingDocIds")
    assert "removeDocumentFromFolders" in page[start:end]

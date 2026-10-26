"""上传前端预校验契约：别让用户等整个文件传完才看到 413。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_upload_validates_size_and_extension_client_side(page: str) -> None:
    assert "KNOWLEDGE_UPLOAD_MAX_MB" in page
    assert "KNOWLEDGE_UPLOAD_EXTENSIONS" in page
    assert "已跳过" in page


def test_upload_hint_mentions_limit(page: str) -> None:
    assert "单文件不超过" in page

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
    # 抽屉排版压缩后文案从「单文件不超过 256MB」改成更短的「单文件 ≤ 256MB」，
    # 断言跟着文案走，但守的东西不变：提示里必须出现上限值。
    assert "单文件 ≤ {{ KNOWLEDGE_UPLOAD_MAX_MB }}MB" in page

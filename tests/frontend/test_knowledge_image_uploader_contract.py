"""图片 → Markdown 上传器的前端契约。

后端契约（`tests/api/portal/test_knowledge_image_markdown_contract.py`）管的是
「怎么生成、怎么入库」；这里管的是前端别把这条链路走回老路：

1. 图片**不能**再走 `documents/upload`——那条路 RAGFlow 会当图片解析，必然失败；
2. 解析（`images/parse`）与入库（`images/commit`）必须是两次调用，
   合并成一次就等于「模型说什么就直接进知识库」；
3. 多模态模型没配时入口要关掉，不能让用户传完再等报错。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
UPLOADER = ROOT / "frontend/src/components/knowledge/ImageToMarkdownUploader.vue"
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def uploader() -> str:
    return UPLOADER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_upload_area_has_image_tab(page: str) -> None:
    """上传区要有「文档 / 图片」两档，图片不能被做成独立页面。"""
    assert "uploadTab" in page
    assert 'uploadTab === \'doc\'' in page
    assert 'uploadTab === \'img\'' in page
    assert "<ImageToMarkdownUploader" in page


def test_image_parsing_does_not_go_through_document_upload(uploader: str) -> None:
    """图片走 documents/upload 会被 RAGFlow 当图片解析，必然停在解析失败。"""
    assert "/images/parse" in uploader
    assert "documents/upload" not in uploader


def test_parse_and_commit_are_two_calls(uploader: str) -> None:
    """解析只产 md，确认后才入库：合成一次调用就等于模型直接写库。"""
    assert "/images/parse" in uploader
    assert "/images/commit" in uploader
    parse_at = uploader.index("/images/parse")
    commit_at = uploader.index("/images/commit")
    assert parse_at < commit_at
    # 解析分支里不能出现上传动作
    parse_block = uploader[parse_at: commit_at]
    assert "upload_document" not in parse_block


def test_entry_disabled_when_vision_model_missing(uploader: str) -> None:
    """没配多模态模型就置灰入口，避免用户传完图才看到失败。"""
    assert "knowledge-images/vision-model" in uploader
    assert "visionState" in uploader
    assert "多模态模型" in uploader


def test_markdown_is_editable_before_commit(uploader: str) -> None:
    """md 必须可改：模型读错公章/手写时，只能靠人在入库前修正。"""
    assert "editing" in uploader
    assert 'v-model="activeItem.markdown"' in uploader


def test_large_images_are_downscaled(uploader: str) -> None:
    """长边超过 1600px 先缩：模型按固定分辨率缩放，大图只多花 token 不多读字。"""
    assert "canvas" in uploader
    assert "1600" in uploader


def test_failure_retry_is_per_image(uploader: str) -> None:
    """单张失败单独重试，不能整批重跑。"""
    assert "@click.stop=\"parseOne(item)\"" in uploader

"""知识库上传不再接受图片的契约。

RAGFlow 的图片解析要视觉模型配合，当前部署没打通：图片传上去只会停在
「解析失败」，用户还得自己回来看状态。与其事后解释，不如在入口挡掉，
并明确告诉用户「需要图片里的内容，先转成 PDF」。

两端白名单必须逐项一致：只改一边，前端会放行、后端 415，体验更差。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ENDPOINT = ROOT / "app/api/portal/endpoints/ragflow.py"
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "svg"}


@pytest.fixture(scope="module")
def endpoint() -> str:
    return ENDPOINT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _backend_extensions(endpoint: str) -> set[str]:
    start = endpoint.index("KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS = {")
    block = endpoint[start: endpoint.index("}", start)]
    return {item.lstrip(".") for item in re.findall(r'"\.(\w+)"', block)}


def _frontend_extensions(page: str) -> set[str]:
    start = page.index("const KNOWLEDGE_UPLOAD_EXTENSIONS = [")
    block = page[start: page.index("]", start)]
    return set(re.findall(r"'(\w+)'", block))


def test_backend_rejects_images(endpoint: str) -> None:
    assert not (_backend_extensions(endpoint) & IMAGE_EXTS)


def test_frontend_rejects_images(page: str) -> None:
    assert not (_frontend_extensions(page) & IMAGE_EXTS)
    accept = re.search(r'accept="([^"]+)"', page)
    assert accept is not None
    accepted = {item.lstrip(".") for item in accept.group(1).split(",")}
    assert not (accepted & IMAGE_EXTS), "accept 里还留着图片，选文件时仍会列出来"


def test_both_sides_list_the_same_types(endpoint: str, page: str) -> None:
    """两端口径必须一致：只改一边，前端放行、后端 415。"""
    assert _backend_extensions(endpoint) == _frontend_extensions(page)


def test_error_message_does_not_advertise_images(endpoint: str) -> None:
    """提示语还在写「支持图片」的话，用户会以为是自己操作错了。"""
    marker = "支持 PDF/Word/Excel/PPT/Markdown/TXT/HTML/EPUB"
    assert marker in endpoint
    assert "图片/EPUB" not in endpoint

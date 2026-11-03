"""图片 → Markdown 入库的契约。

图片不进 RAGFlow 的图片解析链路，而是先用系统多模态模型读成 md 再当普通文档入库。
这条链路有几个不能松的点：

1. **没配多模态模型必须前置拦住**——否则用户传完图，等到超时才看到失败；
2. **原图与 md 用同一个内容哈希命名**——这是不建关联表也能从文档反推原图的依据；
3. **解析不入库、确认才入库**——md 一进知识库就会被召回，模型编造的数字没人能分辨。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow
from app.services import knowledge_image_service as image_service

pytestmark = pytest.mark.no_infrastructure

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-body"


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.content_type = "image/png"
        self.size = len(payload)
        self._payload = payload
        self._read = False

    async def read(self, size: int = -1):
        if self._read:
            return b""
        self._read = True
        return self._payload


def _fake_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


async def _allow(*args, **kwargs):
    return None


async def _noop_audit(*args, **kwargs):
    return None


# ── 命名与去重 ──────────────────────────────────────────────────────────────

def test_source_and_markdown_share_hash_tail():
    """原图 `X-<hash>.png` 与文档 `X-<hash>.md` 必须同尾巴，否则反推不成立。"""
    src = image_service.source_image_rel_path("ds-1", "合同-第3页.png", PNG_BYTES)
    md = image_service.markdown_filename("合同-第3页.png", PNG_BYTES)
    hash8 = image_service.content_hash8(PNG_BYTES)

    assert src.endswith(f"合同-第3页-{hash8}.png")
    assert md == f"合同-第3页-{hash8}.md"
    assert md[: -len(".md")] == os.path.splitext(os.path.basename(src))[0]


def test_markdown_header_is_built_server_side():
    """时间与模型名由服务端写：模型经常编这两个字段，而它们是排查的唯一线索。"""
    md = image_service.compose_markdown(
        file_name="发票.png",
        source_name="发票.png",
        model="qwen-vl-max",
        body="# 发票\n\n## 内容\n增值税发票。",
        ts="2026-11-03 10:24",
    )
    assert md.startswith("# 发票\n\n> 源图：发票.png ｜ 解析模型：qwen-vl-max ｜ 2026-11-03 10:24")
    # 模型自带的 H1 被剥掉，避免一份文档两个一级标题
    assert md.count("# 发票") == 1


def test_only_png_jpg_webp_are_supported():
    assert image_service.is_supported_image("a.PNG")
    assert image_service.is_supported_image("a.jpeg")
    assert image_service.is_supported_image("a.webp")
    assert not image_service.is_supported_image("a.gif")
    assert not image_service.is_supported_image("a.pdf")


# ── 端点：前置校验 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_rejects_when_vision_model_missing(monkeypatch):
    """没配多模态模型时给的是「去系统配置」，不是等模型超时。"""
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "resolve_default_multimodal_model_name", lambda: _none())

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.parse_dataset_image(
            dataset_id="ds-1",
            request=_fake_request(),
            file=_FakeUpload("a.png", PNG_BYTES),
            user={"role": "user", "user_id": "1"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "多模态模型" in exc.value.detail


async def _none():
    return None


@pytest.mark.asyncio
async def test_parse_rejects_unsupported_format(monkeypatch):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.parse_dataset_image(
            dataset_id="ds-1",
            request=_fake_request(),
            file=_FakeUpload("扫描.gif", PNG_BYTES),
            user={"role": "user", "user_id": "1"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 415
    assert "PNG/JPG/WEBP" in exc.value.detail


# ── 端点：正常路径 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_returns_markdown_without_touching_ragflow(monkeypatch, tmp_path):
    """解析只产 md，不上传：确认之后才入库。"""
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_audit)
    monkeypatch.setattr(ragflow, "resolve_default_multimodal_model_name", lambda: _model())
    monkeypatch.setattr(image_service, "_SOURCE_DIR", str(tmp_path / "knowledge_images"))
    monkeypatch.setattr(image_service, "_LEGACY_SOURCE_DIR", str(tmp_path / "legacy_images"))

    async def _fake_describe(last_user, model):
        return "## 图片内容\n合同签署页。"

    monkeypatch.setattr(image_service, "describe_images_with_vision_model", _fake_describe)

    result = await ragflow.parse_dataset_image(
        dataset_id="ds-1",
        request=_fake_request(),
        file=_FakeUpload("合同.png", PNG_BYTES),
        user={"role": "user", "user_id": "1"},
        db=SimpleNamespace(),
    )

    data = result["data"]
    assert "## 图片内容" in data["markdown"]
    assert data["model"] == "qwen-vl-max"
    assert data["md_filename"].endswith(".md")
    # 原图已落盘留档，且路径与 md 同哈希
    assert os.path.exists(data["image_path"])
    assert os.path.splitext(os.path.basename(data["image_path"]))[0] == data["md_filename"][:-3]


async def _model():
    return "qwen-vl-max"


@pytest.mark.asyncio
async def test_parse_surfaces_model_failure_as_502(monkeypatch, tmp_path):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_audit)
    monkeypatch.setattr(ragflow, "resolve_default_multimodal_model_name", lambda: _model())
    monkeypatch.setattr(image_service, "_SOURCE_DIR", str(tmp_path / "knowledge_images"))
    monkeypatch.setattr(image_service, "_LEGACY_SOURCE_DIR", str(tmp_path / "legacy_images"))

    async def _boom(last_user, model):
        raise RuntimeError("多模态模型未返回图片解析结果")

    monkeypatch.setattr(image_service, "describe_images_with_vision_model", _boom)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.parse_dataset_image(
            dataset_id="ds-1",
            request=_fake_request(),
            file=_FakeUpload("合同.png", PNG_BYTES),
            user={"role": "user", "user_id": "1"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 502
    assert "未返回" in exc.value.detail


@pytest.mark.asyncio
async def test_commit_uploads_markdown_as_text_document(monkeypatch):
    """md 必须以 text/markdown 当普通文档上传，切片与检索才和文档一致。"""
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_audit)

    captured = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def upload_document(self, dataset_id, file_name, blob, content_type):
            captured.update(
                dataset_id=dataset_id,
                file_name=file_name,
                blob=blob,
                content_type=content_type,
            )
            return {"id": "doc-1", "name": file_name}

    class _FakeQuota:
        @staticmethod
        async def ensure_can_upload(*a, **kw):
            return None

    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeClient)
    monkeypatch.setattr(ragflow, "KnowledgeQuotaService", _FakeQuota)

    result = await ragflow.commit_dataset_image_markdown(
        dataset_id="ds-1",
        payload=ragflow.ImageCommitPayload(md_filename="合同-a3f9c2d1.md", markdown="# 合同\n内容"),
        request=_fake_request(),
        user={"role": "user", "user_id": "1"},
        db=SimpleNamespace(),
    )

    assert result["data"]["id"] == "doc-1"
    assert captured["content_type"] == "text/markdown"
    assert captured["blob"].decode("utf-8").startswith("# 合同")


@pytest.mark.asyncio
async def test_commit_rejects_non_markdown_name(monkeypatch):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.commit_dataset_image_markdown(
            dataset_id="ds-1",
            payload=ragflow.ImageCommitPayload(md_filename="合同.png", markdown="x"),
            request=_fake_request(),
            user={"role": "user", "user_id": "1"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert ".md" in exc.value.detail

"""知识库上传的硬限制。

以前 `await file.read()` 把整个文件读进内存，没有任何大小/类型校验：
一个几 GB 的文件或一个 .exe 都能一路打到底层解析器。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.content_type = "application/octet-stream"
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


def test_upload_limit_is_256mb():
    """口径写死在这里：改成别的值时，前端常量与文案要一起改。"""
    assert ragflow.KNOWLEDGE_UPLOAD_MAX_BYTES == 256 * 1024 * 1024
    assert ragflow.KNOWLEDGE_UPLOAD_MAX_MB == 256


@pytest.mark.asyncio
async def test_upload_rejects_too_large_file(monkeypatch):
    # 不想在单测里真的分配 256MB，把上限压到 1KB 验证逻辑本身
    monkeypatch.setattr(ragflow, "KNOWLEDGE_UPLOAD_MAX_BYTES", 1024)
    monkeypatch.setattr(ragflow, "KNOWLEDGE_UPLOAD_MAX_MB", 1)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    big = _FakeUpload("manual.pdf", b"x" * 2048)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=_fake_request(),
            file=big,
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 413
    assert "MB" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_extension(monkeypatch):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    bad = _FakeUpload("payload.exe", b"MZ")

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=_fake_request(),
            file=bad,
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 415
    assert "不支持的文件类型" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_rejects_oversize_even_when_size_header_is_wrong(monkeypatch):
    """有些客户端不传 Content-Length，只能靠读的时候边读边卡上限。"""
    monkeypatch.setattr(ragflow, "KNOWLEDGE_UPLOAD_MAX_BYTES", 1024)
    monkeypatch.setattr(ragflow, "KNOWLEDGE_UPLOAD_MAX_MB", 1)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    class _NoSizeUpload(_FakeUpload):
        def __init__(self, filename: str, payload: bytes):
            super().__init__(filename, payload)
            del self.size

    big = _NoSizeUpload("manual.pdf", b"x" * 5000)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=_fake_request(),
            file=big,
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 413

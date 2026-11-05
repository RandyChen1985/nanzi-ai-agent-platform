"""知识库上传的类型与体量口径。

页面接口（``POST /datasets/{id}/documents/upload``）与智能体工具
（``upload_document_to_knowledge_base``）必须用同一套白名单，否则会出现
「页面上传被拒、让智能体传却成功」这种谁也说不清的行为差异。

这里的常量是**默认值**；端点侧仍保留自己的同名常量（历史测试直接
monkeypatch 它们），两处一致性由 ``tests/frontend/test_knowledge_upload_policy_contract.py``
守着——改一处忘另一处会立刻红。
"""

from __future__ import annotations

import os

#: 超过这个量级的文档解析质量通常也不好，宁可让用户拆分。
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_MB = DEFAULT_MAX_BYTES // (1024 * 1024)

#: 与 RAGFlow 解析器支持的格式保持一致；不在白名单的直接拒绝，不做静默透传。
#:
#: 刻意不收图片（png/jpg/gif/bmp/tif）：RAGFlow 的图片解析需要视觉模型配合，
#: 当前部署没有可用链路，传上去只会停在「解析失败」。与其让用户传完再发现，
#: 不如在入口就挡掉——需要图片内容时先转成 PDF 再上传。
ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".txt",
        ".md",
        ".csv",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".html",
        ".htm",
        ".epub",
    }
)

SUPPORTED_TYPES_TEXT = "PDF/Word/Excel/PPT/Markdown/TXT/HTML/EPUB"

#: 拒绝原因码：调用方据此决定 HTTP 状态码（415 / 413）或工具错误码。
CODE_UNSUPPORTED_TYPE = "unsupported_type"
CODE_TOO_LARGE = "file_too_large"


class KnowledgeUploadRejected(ValueError):
    """上传前置校验不通过。``code`` 供上层映射状态码，``str(exc)`` 直接给人看。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def validate_upload(
    file_name: str,
    size: int,
    *,
    allowed_extensions: frozenset[str] | set[str] | None = None,
    max_bytes: int | None = None,
    max_mb: int | None = None,
) -> None:
    """类型 + 大小前置校验。

    顺序是先类型后大小：类型不符时没必要为一个几 GB 的文件先读一遍。

    参数留了覆盖口，是为了让历史测试仍能把上限压到 1KB 验证逻辑本身，
    而不必真的分配 256MB。
    """
    allowed = allowed_extensions if allowed_extensions is not None else ALLOWED_EXTENSIONS
    limit = max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES
    limit_mb = max_mb if max_mb is not None else limit // (1024 * 1024)

    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in allowed:
        raise KnowledgeUploadRejected(
            f"不支持的文件类型「{ext or '无扩展名'}」，支持 {SUPPORTED_TYPES_TEXT}",
            code=CODE_UNSUPPORTED_TYPE,
        )
    if size > limit:
        raise KnowledgeUploadRejected(
            f"文件超过 {limit_mb}MB 上限，请拆分后再上传",
            code=CODE_TOO_LARGE,
        )

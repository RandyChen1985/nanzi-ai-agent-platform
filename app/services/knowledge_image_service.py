"""图片 → Markdown：把图片交给系统默认多模态模型转成可检索的 md，再进 RAGFlow。

为什么不让 RAGFlow 直接吃图片：RAGFlow 的图片解析要视觉模型配合，当前部署没有
可用链路，图片传上去只会停在「解析失败」——这一点在知识库上传白名单里已经确认过
（`KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS` 不含图片）。所以图片走另一条路：平台自己
用多模态模型把图读成文字，产出 md，再当普通文档入库。检索侧完全不用改。

原图不传给 RAGFlow，只留在平台附件目录：图片会占文档配额，而且解析失败会在列表里
留红点。留档是为了模型读错时人能回看原图核对。

命名约定（不建表的关键）：原图与 md 用同一个内容哈希尾巴，

    data/uploads/knowledge_images/{dataset_id}/合同-第3页-a3f9c2d1.png
    RAGFlow 文档：                          合同-第3页-a3f9c2d1.md

于是从文档名就能反推原图，省掉一张关联表和一次迁移。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.services.ai.multimodal_support import describe_images_with_vision_model

logger = logging.getLogger(__name__)

#: 原图目录刻意不放在 ``data/uploads`` 下：那个目录整个挂到了 ``/static/uploads``
#: （见 app/main.py），放进去等于把原图变成无鉴权静态资源。要读原图只能走
#: ``GET /datasets/{id}/images/source/{name}``，那里有库的读权限校验。
_SOURCE_DIR = os.path.join("data", "knowledge_images")

#: 老位置。历史数据可能还在这里，读的时候兼容、启动时搬走。
_LEGACY_SOURCE_DIR = os.path.join("data", "uploads", "knowledge_images")

#: 平台侧单图上限。图片走 vision 模型，比文档更容易顶到模型输入上限，
#: 10MB 已经足够放一张 A4 300dpi 的扫描件。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_MB = MAX_IMAGE_BYTES // (1024 * 1024)

#: 只收这几种：gif/tiff/bmp 的兼容性在多模态模型上不统一，先在入口挡掉。
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: 元信息行由服务端拼，不交给模型写：模型写时间/模型名经常编，
#: 而这两个字段恰恰是排查「这份 md 是谁生成的」的唯一线索。
_HEADER_TEMPLATE = "# {title}\n\n> 源图：{source_name} ｜ 解析模型：{model} ｜ {ts}\n\n"

#: 结构化程度决定检索质量：分节让切片能各自命中，可信度提示让人知道哪里要核对。
_VISION_PROMPT = """请把这张图片转写成结构化的 Markdown 文档，用于知识库检索。

严格按下面的要求输出，不要输出 Markdown 代码块包裹，不要写开头寒暄：

1. 先输出 `## 图片内容`：一句话说明这是什么类型的图（合同签署页 / 组织架构图 / 发票 …），
   再补一句整体版面描述（几栏、有无表格、印章、手写等）。
2. 再输出 `## 文字内容`：按图片上的实际版面顺序转写全部文字，保留层级与编号；
   看不清的单个字用 `□` 占位，不要凭上下文猜字形。
3. 图片里有表格时，输出 `## 表格`，用标准 Markdown 表格还原，表头原样保留。
4. 最后必须输出 `## 可信度提示`：
   - 逐条列出你不确定的地方（印章遮挡、反光、字迹模糊、被截断的行）；
   - 没有不确定的地方就写「文字清晰，未见异常」；
   - 禁止在这一节之外加入任何推断或补充说明。

不要编造图片里没有的内容。数字、日期、编号、金额必须照抄，宁可标注看不清也不要推测。"""


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def image_extension(file_name: str) -> str:
    return os.path.splitext(file_name or "")[1].lower()


def is_supported_image(file_name: str) -> bool:
    return image_extension(file_name) in SUPPORTED_IMAGE_EXTENSIONS


def content_hash8(blob: bytes) -> str:
    """8 位内容哈希：同一张图重复上传会落到同一个文件名，天然去重。"""
    return hashlib.sha256(blob).hexdigest()[:8]


def _safe_stem(file_name: str) -> str:
    """文件名主干做轻量清洗：只去掉路径分隔符等危险字符，中文原样保留。"""
    stem = os.path.splitext(os.path.basename(file_name or "image"))[0]
    for ch in ("/", "\\", "\x00"):
        stem = stem.replace(ch, "_")
    stem = stem.strip().strip(".") or "image"
    return stem[:80]


def source_image_rel_path(dataset_id: str, file_name: str, blob: bytes) -> str:
    ext = image_extension(file_name) or ".png"
    return os.path.join(
        _SOURCE_DIR, dataset_id, f"{_safe_stem(file_name)}-{content_hash8(blob)}{ext}"
    )


def markdown_filename(file_name: str, blob: bytes) -> str:
    """md 文件名带同一个哈希尾巴，于是能从文档名反推原图。"""
    stem = _safe_stem(file_name)
    return f"{stem}-{content_hash8(blob)}.md"


def save_source_image(dataset_id: str, file_name: str, blob: bytes) -> str:
    """原图落盘留档，返回相对路径。同内容重复上传直接复用已有文件。"""
    rel_path = source_image_rel_path(dataset_id, file_name, blob)
    abs_path = os.path.abspath(rel_path)
    if os.path.exists(abs_path):
        return rel_path
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as fh:
        fh.write(blob)
    return rel_path


def dataset_dir(dataset_id: str) -> str:
    """某个库的原图目录（新位置）。"""
    return os.path.join(_SOURCE_DIR, str(dataset_id))


def migrate_legacy_dir() -> int:
    """把 ``data/uploads/knowledge_images`` 下的历史原图搬到新目录。

    老位置的每个文件都能被 ``/static/uploads/...`` 直接取到，搬走才算真正收口。
    逐库搬而不是整目录 rename：新目录里可能已经有同名库（升级后又传过图），
    整目录 rename 会把旧文件盖掉或直接失败。

    返回搬动的文件数；异常只记日志——启动流程不能因为搬文件挂住。
    """
    if not os.path.isdir(_LEGACY_SOURCE_DIR):
        return 0
    moved = 0
    try:
        for entry in os.listdir(_LEGACY_SOURCE_DIR):
            src_dir = os.path.join(_LEGACY_SOURCE_DIR, entry)
            if not os.path.isdir(src_dir):
                continue
            dst_dir = dataset_dir(entry)
            os.makedirs(dst_dir, exist_ok=True)
            for name in os.listdir(src_dir):
                src = os.path.join(src_dir, name)
                dst = os.path.join(dst_dir, name)
                if not os.path.isfile(src) or os.path.exists(dst):
                    continue
                shutil.move(src, dst)
                moved += 1
            try:
                os.rmdir(src_dir)
            except OSError:
                pass  # 还有没搬走的（重名跳过），留着下次再试
    except Exception as exc:  # noqa: BLE001 - 启动流程不能因搬文件中断
        logger.warning("迁移历史原图目录失败（已忽略）: %s", exc)
    if moved:
        logger.info("历史原图迁移完成：%s 个文件", moved)
    return moved


def absolute_source_path(dataset_id: str, file_name: str) -> Optional[str]:
    """鉴权下载用：只接受纯文件名，挡住 ``../`` 穿越。

    新目录找不到再回退老目录，这样升级过程中还没搬走的图也能打开。
    """
    raw = file_name or ""
    name = os.path.basename(raw)
    if not name or raw != name:
        return None
    if image_extension(name) not in SUPPORTED_IMAGE_EXTENSIONS:
        return None
    for root in (dataset_dir(dataset_id), os.path.join(_LEGACY_SOURCE_DIR, str(dataset_id))):
        root_abs = os.path.abspath(root)
        candidate = os.path.abspath(os.path.join(root_abs, name))
        try:
            if os.path.commonpath([candidate, root_abs]) != root_abs:
                continue  # 拼出来跑到目录外了
        except ValueError:
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def delete_dataset_images(dataset_id: str) -> int:
    """删库时清空该库的原图与 md 快照，返回删除的文件数。

    不清理就等于每删一个库都留一份没人引用、也不会再被访问的文件。
    """
    removed = 0
    for root in (dataset_dir(dataset_id), os.path.join(_LEGACY_SOURCE_DIR, str(dataset_id))):
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
            os.rmdir(root)
        except Exception as exc:  # noqa: BLE001 - 清理失败不能影响删除结果
            logger.warning("清理知识库原图失败 dataset=%s: %s", dataset_id, exc)
    return removed


def delete_source_for_markdown(dataset_id: str, md_filename: str) -> int:
    """删文档时按 md 名反查并删掉原图，连带磁盘上的 md 快照。

    RAGFlow 的删除接口只吃 document_id，所以调用方要先拿文档名。
    历史文档（不是图片转来的）反查不到，返回 0。
    """
    stem = os.path.splitext(os.path.basename(md_filename or ""))[0]
    if not stem:
        return 0
    targets = [stem + ext for ext in SUPPORTED_IMAGE_EXTENSIONS] + [stem + ".md"]
    removed = 0
    for root in (dataset_dir(dataset_id), os.path.join(_LEGACY_SOURCE_DIR, str(dataset_id))):
        for name in targets:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                logger.warning("删除原图失败 dataset=%s name=%s: %s", dataset_id, name, exc)
    return removed


def mime_for_extension(file_name: str) -> str:
    """按扩展名猜 MIME；下载原图时交给 FileResponse。"""
    return _MIME_BY_EXT.get(image_extension(file_name), "application/octet-stream")


def to_data_url(file_name: str, blob: bytes) -> str:
    """模型入参用 data-uri，不依赖静态目录挂载路径。

    `_resolve_image_local_path` 只认 `data/uploads` 下的扁平文件名，多一层
    `knowledge_images/{dataset_id}/` 就解析不到；直接把 base64 塞进 url 最稳。
    """
    mime = _MIME_BY_EXT.get(image_extension(file_name), "image/png")
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def compose_markdown(
    *,
    file_name: str,
    source_name: str,
    model: str,
    body: str,
    ts: Optional[str] = None,
) -> str:
    """把模型产出的正文套上服务端头部。

    模型正文里若自带一级标题先剥掉：标题统一用文件名，避免同一份文档里出现两个 H1。
    """
    title = _safe_stem(file_name)
    cleaned = (body or "").strip()
    lines = cleaned.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    cleaned = "\n".join(lines).strip()
    header = _HEADER_TEMPLATE.format(
        title=title, source_name=source_name, model=model, ts=ts or _now_stamp()
    )
    return header + cleaned + "\n"


async def image_to_markdown(
    *,
    dataset_id: str,
    file_name: str,
    blob: bytes,
    model: str,
) -> Dict[str, Any]:
    """图片 → md。产出不入库，调用方确认后才上传。

    异常原样抛出：上层要把「模型未配置 / 返回空 / 超时」翻译成用户能处理的提示，
    而不是在这里吞成兜底文案。
    """
    started = time.time()
    last_user = {
        "role": "user",
        "content": _VISION_PROMPT,
        "files": [{"ext": image_extension(file_name), "url": to_data_url(file_name, blob)}],
    }
    body = await describe_images_with_vision_model(last_user, model)
    md = compose_markdown(
        file_name=file_name,
        source_name=os.path.basename(file_name),
        model=model,
        body=body,
    )
    return {
        "markdown": md,
        "md_filename": markdown_filename(file_name, blob),
        "model": model,
        "image_path": source_image_rel_path(dataset_id, file_name, blob),
        "char_count": len(md),
        "cost_ms": int((time.time() - started) * 1000),
    }


def source_path_for_markdown(dataset_id: str, md_filename: str) -> Optional[str]:
    """从 md 文件名反推原图路径；找不到就返回 None（历史文档不会有）。

    md 名形如 `合同-第3页-a3f9c2d1.md`，原图是 `合同-第3页-a3f9c2d1.<任意图片扩展名>`。
    """
    stem = os.path.splitext(os.path.basename(md_filename or ""))[0]
    if not stem:
        return None
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        found = absolute_source_path(dataset_id, f"{stem}{ext}")
        if found:
            return found
    return None

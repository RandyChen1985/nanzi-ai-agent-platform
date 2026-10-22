"""Path validation shared by Office document tools."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from app.utils.fs_paths import get_data_base_dir

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

# 邮件附件：类型白名单 + 数量/合计上限。
# 白名单不是「懒得判断」而是刻意收窄：模型可能被诱导把 .env、密钥、脚本当附件外发，
# 而这类文件恰好也是邮箱网关最爱拦的，放开等于两头挨打。
EMAIL_ATTACHMENT_EXTENSIONS = frozenset(
    {
        ".docx",
        ".xlsx",
        ".pptx",
        ".pdf",
        ".csv",
        ".txt",
        ".md",
        ".json",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".zip",
    }
)
MAX_EMAIL_ATTACHMENTS = 5
# 与 MAX_DOCUMENT_BYTES 同口径：单文件与合计都不超过 20MB 原始字节。
# 注意 base64 编码后报文约膨胀到 26.7MB，超出部分邮箱服务商的 25MB 单封上限时
# 由 SMTP 服务端返回真实错误——不做静默截断。
MAX_EMAIL_ATTACHMENT_TOTAL_BYTES = MAX_DOCUMENT_BYTES


class DocumentPathError(ValueError):
    """A document path is not safe for the current request."""


class EmailAttachmentError(DocumentPathError):
    """邮件附件不合法：类型、体量或数量不满足约定。"""


async def resolve_workspace_root() -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root as resolver

    return await resolver()


def resolve_session_workdir(
    *,
    root: str,
    user_id: int | str | None,
    conversation_id: str,
    user_name: str | None = None,
    user_info: dict | None = None,
) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_session_workdir as resolver

    return resolver(
        root=root,
        user_id=user_id,
        conversation_id=conversation_id,
        user_name=user_name,
        user_info=user_info,
    )


def resolve_user_docs_dir(
    *,
    root: str,
    user_id: int | str | None,
    user_name: str | None = None,
    user_info: dict | None = None,
) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_user_docs_dir as resolver

    return resolver(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )


def resolve_context_user_name(context: Any) -> str | None:
    """从 AgentContext 的 user_dimensions 取用户名，用于工作区目录解析。

    这段取值规则原先在 word / excel / notification 三个工具里各抄一份。它直接决定
    代码去哪个用户目录找文件，一旦某处漂移就会「同一个文件在 A 工具能读、在 B 工具
    读不到」，所以收敛到这里。
    """
    dimensions = getattr(context, "user_dimensions", None) or {}
    raw = dimensions.get("user_name") or dimensions.get("username")
    if not raw:
        return None
    return str(raw).strip() or None


def _path_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _normalize_platform_path(path: str) -> Path:
    data_root = Path(get_data_base_dir()).resolve()
    raw_path = str(path or "")
    if raw_path == "/app/data":
        return data_root
    if raw_path.startswith("/app/data/"):
        raw_path = str(data_root / raw_path.removeprefix("/app/data/"))
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = data_root / candidate
    return candidate.resolve()


def _validate_extension(path: Path, allowed_extensions: Iterable[str]) -> None:
    allowed = {str(ext).lower() for ext in allowed_extensions}
    if path.suffix.lower() not in allowed:
        supported = ", ".join(sorted(allowed))
        raise DocumentPathError(f"不支持的文件类型，仅支持：{supported}")


async def resolve_document_input_path(
    path: str,
    *,
    allowed_attachment_paths: Iterable[str],
    user_id: int | str | None,
    conversation_id: str | None,
    allowed_extensions: Iterable[str],
    user_name: str | None = None,
) -> Path:
    """Resolve one existing document, limited to this request's attachments or workspace."""
    candidate = _normalize_platform_path(path)
    if not candidate.is_file():
        raise DocumentPathError("文件不存在或不可访问")
    if candidate.stat().st_size > MAX_DOCUMENT_BYTES:
        raise DocumentPathError("文件大小超出 20MB 限制")
    _validate_extension(candidate, allowed_extensions)

    allowed_resolved = {_normalize_platform_path(item) for item in allowed_attachment_paths}
    if candidate in allowed_resolved:
        return candidate

    data_root = Path(get_data_base_dir()).resolve()
    uploads_root = (data_root / "uploads").resolve()
    allowed_uploads = allowed_resolved
    if _path_under(candidate, uploads_root):
        if candidate not in allowed_uploads:
            raise DocumentPathError("该上传文件不属于当前会话附件")
        return candidate

    workspace_root = Path(await resolve_workspace_root()).resolve()
    from app.services.ai.runtime.agentscope.workspace import resolve_user_workspace_root

    user_workspace_dir = resolve_user_workspace_root(
        root=str(workspace_root),
        user_id=user_id,
        user_name=user_name,
    )
    if user_workspace_dir and _path_under(candidate, Path(user_workspace_dir).resolve()):
        return candidate

    if not conversation_id:
        raise DocumentPathError("当前会话缺少工作目录，无法访问该文件")
    user_docs_dir = Path(
        resolve_user_docs_dir(
            root=str(workspace_root),
            user_id=user_id,
            user_name=user_name,
        )
    ).resolve()
    if _path_under(candidate, user_docs_dir):
        return candidate
    session_workdir = Path(
        resolve_session_workdir(
            root=str(workspace_root),
            user_id=user_id,
            conversation_id=conversation_id,
            user_name=user_name,
        )
    ).resolve()
    if _path_under(candidate, session_workdir):
        return candidate
    from app.services.ai.runtime.agentscope.workspace import resolve_legacy_session_workdir

    legacy_workdir = Path(
        resolve_legacy_session_workdir(
            root=str(workspace_root),
            user_id=user_id,
            conversation_id=conversation_id,
            user_name=user_name,
        )
    ).resolve()
    if legacy_workdir != session_workdir and _path_under(candidate, legacy_workdir):
        return candidate
    raise DocumentPathError("文件不在当前会话允许访问的目录中")


async def resolve_email_attachments(
    paths: Iterable[str],
    *,
    allowed_attachment_paths: Iterable[str],
    user_id: int | str | None,
    conversation_id: str | None,
    user_name: str | None = None,
) -> list[Path]:
    """校验邮件附件，返回可直接读取的绝对路径。

    单个文件的授权范围、扩展名与 20MB 上限全部复用 ``resolve_document_input_path``，
    这里只叠三条批量规则：去重保序、数量上限、合计大小上限。

    **任一附件不合法就整批抛错**：调用方据此整封不发。若改成「跳过坏附件照发」，
    用户会收到一封「看起来发了三个附件、实际只发了两个」的邮件，而发信人无从察觉。
    """
    requested = [str(item).strip() for item in paths or [] if str(item).strip()]
    if not requested:
        return []

    # 先按原始条目粗判数量：模型可能一口气给几十上百个路径，而每条都会触发一次
    # 「解析工作区根目录（内含读配置）+ stat」，等到全跑完再报数量错纯属浪费。
    # 用字符串去重而不是解析后去重，是为了这一步不碰磁盘。
    unique_requested = list(dict.fromkeys(requested))
    if len(unique_requested) > MAX_EMAIL_ATTACHMENTS:
        raise EmailAttachmentError(
            f"附件数量超限：最多 {MAX_EMAIL_ATTACHMENTS} 个，"
            f"当前 {len(unique_requested)} 个"
        )

    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw in requested:
        try:
            path = await resolve_document_input_path(
                raw,
                allowed_attachment_paths=allowed_attachment_paths,
                user_id=user_id,
                conversation_id=conversation_id,
                allowed_extensions=EMAIL_ATTACHMENT_EXTENSIONS,
                user_name=user_name,
            )
        except DocumentPathError as exc:
            # 必须点名是哪个文件：多附件时只说「不支持的文件类型」，用户得逐个去猜。
            # raw 可能是目录写法（结尾带 /），此时 basename 为空，退回原串。
            label = Path(raw).name or raw
            raise EmailAttachmentError(f"附件 {label} 不可用：{exc}") from None
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)

    total_bytes = 0
    for path in resolved:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            # 校验与汇总之间文件被删：包成业务错误，别让 OSError 穿透工具边界。
            raise EmailAttachmentError(
                f"附件 {path.name} 不可用：文件已不存在或无法读取"
            ) from None
        if total_bytes > MAX_EMAIL_ATTACHMENT_TOTAL_BYTES:
            total_mb = MAX_EMAIL_ATTACHMENT_TOTAL_BYTES // (1024 * 1024)
            raise EmailAttachmentError(
                f"附件合计大小超限：最多 {total_mb}MB，"
                f"当前 {total_bytes / (1024 * 1024):.1f}MB"
            )
    return resolved


def sanitize_output_filename(filename: str, allowed_extensions: Iterable[str]) -> str:
    raw_name = os.path.basename(str(filename or "")).strip()
    clean_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", raw_name)
    if not clean_name or clean_name in {".", ".."}:
        raise DocumentPathError("输出文件名不能为空")
    _validate_extension(Path(clean_name), allowed_extensions)
    return clean_name


async def resolve_document_output_path(
    filename: str,
    *,
    user_id: int | str | None,
    conversation_id: str | None,
    allowed_extensions: Iterable[str],
    user_name: str | None = None,
) -> Path:
    if not conversation_id:
        raise DocumentPathError("当前会话缺少工作目录，无法生成文件")
    clean_name = sanitize_output_filename(filename, allowed_extensions)
    workspace_root = Path(await resolve_workspace_root()).resolve()
    docs_dir = Path(
        resolve_user_docs_dir(
            root=str(workspace_root),
            user_id=user_id,
            user_name=user_name,
        )
    ).resolve()
    docs_dir.mkdir(parents=True, exist_ok=True)
    output_path = (docs_dir / clean_name).resolve()
    if not _path_under(output_path, docs_dir):
        raise DocumentPathError("输出文件路径不安全")
    return output_path

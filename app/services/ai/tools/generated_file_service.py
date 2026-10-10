"""Private publication and capability-link download support for generated files."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.artifact import AiArtifact
from app.core.orm import AsyncSessionLocal
from app.services.config_service import ConfigService
from app.utils.fs_paths import get_data_base_dir

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(days=30)

_GENERATED_DOWNLOAD_URL_RE = re.compile(
    r"(?P<url>(?:https?://[^\s<>\"'`]+)?/api/v1/chat/generated-files/"
    r"[0-9a-f]{32}\?token=[A-Za-z0-9_-]+"
    r"(?:#[A-Za-z0-9._~-]+)?)",
    re.IGNORECASE,
)
_UNTRUSTED_DOWNLOAD_URL_MESSAGE = "下载地址未通过文件工具确认"

_OFFICE_MIME_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _normalize_public_base_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


async def get_download_url_prefix() -> str:
    """Read the system-configured download prefix with environment fallback."""
    try:
        configured = await ConfigService.get("download_url_prefix")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read download_url_prefix from system config: %s", exc)
        configured = None
    configured_base = _normalize_public_base_url(configured)
    return configured_base or _normalize_public_base_url(settings.APP_PUBLIC_URL)


def build_download_url(
    artifact_id: str,
    token: str,
    *,
    public_base_url: str | None = None,
) -> str:
    """Build a download URL from a resolved public prefix."""
    path = f"/api/v1/chat/generated-files/{artifact_id}?token={token}"
    public_base = _normalize_public_base_url(
        settings.APP_PUBLIC_URL if public_base_url is None else public_base_url
    )
    return f"{public_base}{path}" if public_base else path


@dataclass(frozen=True)
class PublishedArtifact:
    artifact_id: str
    token: str
    filename: str
    mime_type: str
    size: int
    expires_at: datetime
    public_base_url: str = ""

    @property
    def download_url(self) -> str:
        return build_download_url(
            self.artifact_id,
            self.token,
            public_base_url=self.public_base_url,
        )

    def to_tool_payload(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "download_url": self.download_url,
        }


@dataclass(frozen=True)
class GeneratedFile:
    artifact_id: str
    path: Path
    filename: str
    mime_type: str
    size: int
    expires_at: datetime


def generated_files_root() -> Path:
    return Path(get_data_base_dir()) / "generated_files"


def _mime_type_for(filename: str) -> str:
    return _OFFICE_MIME_TYPES.get(Path(filename).suffix.lower()) or (
        mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _artifact_token_secret() -> bytes:
    """签名密钥：优先专用配置，回退平台加密密钥。"""
    dedicated = str(getattr(settings, "ARTIFACT_TOKEN_SECRET", "") or "").strip()
    return (dedicated or settings.ENCRYPTION_KEY).encode("utf-8")


def _as_utc(value: datetime) -> datetime:
    """naive 时间按既有习惯视为 UTC（与下载校验里的过期判断保持一致）。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_artifact_token(artifact_id: str, expires_at: datetime | None) -> str:
    """确定性派生产物下载 token：同一 artifact_id + 同一 expires_at 恒得同一 token。

    为什么不用随机 token：`ai_artifacts` 只存 token 哈希、无法还原明文，产物列表接口
    为了给出可用的 download_url 只能「重新签发并覆盖哈希」—— 而登记时已经写进消息正文
    的链接用的是当时那个 token，于是「一打开我的产出抽屉，正文里的链接就全部失效」。
    改为确定性签名后，列表接口直接重算同一个 token，无需写库，链接不再互相吊销。
    """
    if expires_at is None:
        raise ValueError("缺少过期时间，无法派生产物下载 token")
    message = f"{artifact_id}:{int(_as_utc(expires_at).timestamp())}".encode("utf-8")
    digest = hmac.new(_artifact_token_secret(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_artifact_token(
    artifact_id: str,
    expires_at: datetime | None,
    token: str,
    stored_hash: str | None,
) -> bool:
    """先验签名（新机制），失败再回退历史哈希比对（兼容升级前签发的随机 token）。"""
    if not token:
        return False
    if expires_at is not None:
        if hmac.compare_digest(build_artifact_token(artifact_id, expires_at), token):
            return True
    if stored_hash:
        return hmac.compare_digest(str(stored_hash), _token_hash(token))
    return False


def record_published_download_url(download_url: str) -> None:
    """Record a URL issued by a successful artifact publication in the current turn."""
    url = str(download_url or "").strip()
    if not url or _GENERATED_DOWNLOAD_URL_RE.fullmatch(url) is None:
        return

    from app.core.context import get_current_agent_context

    context = get_current_agent_context()
    if context is None:
        return
    urls = getattr(context, "published_download_urls", None)
    if not isinstance(urls, list):
        context.published_download_urls = []
        urls = context.published_download_urls
    if url not in urls:
        urls.append(url)


def filter_untrusted_download_urls(
    text: str,
    *,
    allowed_urls: set[str] | list[str] | tuple[str, ...] | None = None,
) -> str:
    """Keep only generated-file URLs issued by the current execution chain.

    Ordinary external URLs are deliberately untouched. Only URLs matching the
    platform's generated-file endpoint are subject to the allowlist.
    """
    if not text:
        return text

    if allowed_urls is None:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        allowed_urls = getattr(context, "published_download_urls", []) if context else []
    allowed = {str(url).strip() for url in allowed_urls if str(url).strip()}

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        return url if url in allowed else _UNTRUSTED_DOWNLOAD_URL_MESSAGE

    return _GENERATED_DOWNLOAD_URL_RE.sub(replace, str(text))


def _manifest_path(artifact_id: str) -> Path:
    return generated_files_root() / artifact_id / "manifest.json"


def _remove_artifact(artifact_id: str) -> None:
    shutil.rmtree(generated_files_root() / artifact_id, ignore_errors=True)


def _purge_expired() -> None:
    root = generated_files_root()
    if not root.is_dir():
        return
    now = datetime.now(timezone.utc)
    for artifact_dir in root.iterdir():
        manifest_path = artifact_dir / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                shutil.rmtree(artifact_dir, ignore_errors=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            shutil.rmtree(artifact_dir, ignore_errors=True)


def _path_under(path: Path, parent: Path) -> bool:
    """True when `path` is strictly inside `parent` (resolved, no symlink escape)."""
    try:
        rpath = path.resolve()
        rparent = Path(parent).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    try:
        return rpath == rparent or rparent in rpath.parents
    except (OSError, ValueError, RuntimeError):
        return False


async def _workspace_root() -> Path:
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root

    return Path(await resolve_workspace_root())


async def register_artifact(
    *,
    source_path: str | Path,
    filename: str,
    owner_user_id: int | str | None,
    artifact_type: str,
    conversation_id: str | None = None,
    trace_id: str | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> PublishedArtifact:
    """登记一个已落在用户工作区内的产物到 ai_artifacts，不复制文件。

    产物内容本身已存在于用户工作区目录，这里只写一条指向该文件的元信息
    （storage_path），由下载端点校验归属后直接以 FileResponse 返回原文件，
    避免重复占用磁盘。

    用于 Word/Excel 生成文件与导出数据（artifact_type 取 word/excel/export）。
    """
    source = Path(source_path).resolve()
    if not source.is_file():
        raise ValueError("待发布文件不存在")

    display_name = Path(filename).name
    if not display_name or display_name in {".", ".."}:
        raise ValueError("生成文件名无效")

    root = await _workspace_root()
    if not _path_under(source, root):
        raise ValueError("待发布文件必须位于用户工作区目录内")

    if owner_user_id is None:
        raise ValueError("登记工作区产物需要 owner_user_id")

    # 规整到整秒：DB 的 DateTime 列不保存微秒（MySQL DATETIME(0) 还会四舍五入），
    # 而 token 由 expires_at 的秒值派生 —— 不规整就会出现「登记时派生 43 秒、
    # 从库里读回 44 秒」的 1 秒漂移，导致重算出的 token 与登记时不符。
    expires_at = (datetime.now(timezone.utc) + ttl).replace(microsecond=0)
    # 签名 token 由 (artifact_id, expires_at) 派生：产物列表接口稍后能重算同一个值，
    # 因此不需要（也不允许）在列表时重新签发并覆盖哈希。
    artifact_id = uuid.uuid4().hex
    token = build_artifact_token(artifact_id, expires_at)

    async with AsyncSessionLocal() as session:
        artifact = AiArtifact(
            id=artifact_id,
            owner_user_id=int(owner_user_id),
            conversation_id=conversation_id or None,
            trace_id=trace_id or None,
            artifact_type=artifact_type,
            filename=display_name,
            mime_type=_mime_type_for(display_name),
            size=source.stat().st_size,
            storage_path=str(source),
            token_hash=_token_hash(token),
            expires_at=expires_at,
        )
        session.add(artifact)
        await session.commit()

    public_base_url = await get_download_url_prefix()
    published = PublishedArtifact(
        artifact_id=artifact.id,
        token=token,
        filename=display_name,
        mime_type=artifact.mime_type,
        size=artifact.size,
        expires_at=expires_at,
        public_base_url=public_base_url,
    )
    record_published_download_url(published.download_url)
    return published


async def publish(
    source_path: str | Path,
    filename: str,
    *,
    owner_user_id: int | str | None = None,
    user_name: str | None = None,
    conversation_id: str | None = None,
    trace_id: str | None = None,
    artifact_type: str = "document",
    ttl: timedelta = DEFAULT_TTL,
) -> PublishedArtifact:
    """Copy an external generated file into the user's workspace and register it.

    This is the compatibility entry point for browser/PDF/brief producers whose
    source file is created outside ``agent_workspaces``. It intentionally shares
    the DB artifact path with ``register_artifact`` instead of creating a second
    manifest-based download protocol.
    """
    source = Path(source_path).resolve()
    if not source.is_file():
        raise ValueError("待发布文件不存在")

    display_name = Path(filename).name
    if not display_name or display_name in {".", ".."}:
        raise ValueError("生成文件名无效")

    if owner_user_id is None:
        raise ValueError("统一登记下载文件需要 owner_user_id")

    workspace_root = await _workspace_root()
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_user_key

    user_key = resolve_workspace_user_key(
        user_id=owner_user_id,
        user_name=user_name,
    )
    publish_dir = workspace_root / user_key / "generated"
    publish_dir.mkdir(parents=True, exist_ok=True)
    staged_path = publish_dir / f"{uuid.uuid4().hex}_{display_name}"
    shutil.copy2(source, staged_path)

    try:
        return await register_artifact(
            source_path=staged_path,
            filename=display_name,
            owner_user_id=owner_user_id,
            artifact_type=artifact_type,
            conversation_id=conversation_id,
            trace_id=trace_id,
            ttl=ttl,
        )
    except Exception:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove staged generated file %s", staged_path)
        raise


def resolve_for_download(artifact_id: str, token: str) -> GeneratedFile | None:
    if len(artifact_id) != 32 or any(char not in "0123456789abcdef" for char in artifact_id):
        return None
    if not token:
        return None
    try:
        payload = json.loads(_manifest_path(artifact_id).read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            _remove_artifact(artifact_id)
            return None
        if not hmac.compare_digest(str(payload["token_hash"]), _token_hash(token)):
            return None
        filename = Path(str(payload["filename"])).name
        path = (generated_files_root() / artifact_id / filename).resolve()
        artifact_dir = (generated_files_root() / artifact_id).resolve()
        if not path.is_file() or path.parent != artifact_dir:
            return None
        return GeneratedFile(
            artifact_id=artifact_id,
            path=path,
            filename=filename,
            mime_type=str(payload["mime_type"]),
            size=int(payload["size"]),
            expires_at=expires_at,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


async def resolve_workspace_artifact(artifact_id: str, token: str) -> GeneratedFile | None:
    """从 ai_artifacts 解析工作区产物，校验归属后返回可供 FileResponse 的实体。

    校验项：artifact_id 必须为 32 位十六进制、token 与库中 token_hash 匹配、
    未过期、storage_path 解析后位于用户工作区根之内，且文件确实存在。
    """
    if not artifact_id or len(artifact_id) != 32 or any(char not in "0123456789abcdef" for char in artifact_id):
        return None
    if not token:
        return None

    try:
        workspace_root = await _workspace_root()
    except Exception:
        return None

    async with AsyncSessionLocal() as session:
        try:
            record = await session.get(AiArtifact, artifact_id)
        except Exception:
            return None

    if record is None:
        return None

    try:
        if record.expires_at is not None:
            expires = record.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                return None

        if not verify_artifact_token(artifact_id, record.expires_at, token, record.token_hash):
            return None

        storage = Path(record.storage_path).resolve()
        # 归属校验：文件必须位于用户工作区根之下，防止穿越到工作区外的任意文件。
        if not _path_under(storage, workspace_root):
            return None
        filename = Path(record.filename).name
        if not filename or not storage.is_file():
            return None

        return GeneratedFile(
            artifact_id=artifact_id,
            path=storage,
            filename=filename,
            mime_type=record.mime_type or "application/octet-stream",
            size=record.size or 0,
            expires_at=record.expires_at,
        )
    except (OSError, ValueError, TypeError):
        return None

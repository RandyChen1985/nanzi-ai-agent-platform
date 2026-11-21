"""工作区文件在线分享服务。

与会话分享（``conversation_share_service``）共用同一套访问控制约定，能复用的都复用，
不重新发明：

- 访问密码：``generate_secret`` / ``normalize_secret`` / ``encrypt_secret`` / ``verify_secret``
- 有效期：``resolve_expires_at``（预设档位 + 自定义）
- 解锁令牌与限流：``issue_access_token`` / ``verify_access_token`` /
  ``assert_unlock_allowed`` / ``register_unlock_failure`` / ``clear_unlock_failures``
- 可达性推导：``derive_share_status`` / ``is_share_reachable``

文件分享独有的只有三件事：

1. **路径冻结**：存 ``(workspace_key, rel_path)`` 而不是绝对路径，工作区根目录配置变了也
   还能找回文件；
2. **内容指纹**：创建时算 sha256，每次下载前重新算，对不上就拒绝——这是「不复制文件也能
   保证分享内容不被悄悄换掉」的全部依据；
3. **下载流**：不是返回 JSON，而是 ``FileResponse``，所以安全边界必须比会话分享更硬：
   只认分享记录里的路径，**不接受任何来自请求的路径参数**。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file_share import FileShare
from app.services import conversation_share_service as conv_share

#: 分享链接 ID 的随机字节数，与会话分享保持一致（token_urlsafe(18) → 24 字符）
SHARE_ID_BYTES = 18

#: 单文件分享上限：超大的文件做哈希要读全量，1 GB 级会明显拖慢创建请求
MAX_SHARE_FILE_BYTES = 512 * 1024 * 1024

#: 哈希分块大小
_HASH_CHUNK = 1024 * 1024

#: 文件名长度上限（展示用，落库列宽 255）
FILE_NAME_MAX_LENGTH = 255

#: 在线预览时最大读取的文本字节数：超过这个量级在浏览器里渲染只会卡死，
#: 与其假装能预览，不如直接告诉访客「文件太大，请下载」。
MAX_PREVIEW_TEXT_BYTES = 2 * 1024 * 1024

#: 可在线渲染的图片扩展名。**不含 svg**：svg 能内嵌脚本，直接访问网址时会以应用同源
#: 执行，所以它单独走 ``image``（用 <img> 展示时脚本不执行）而不是文本路径。
IMAGE_EXTENSIONS: Tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif",
)

#: 可在页内当作文本渲染的扩展名
TEXT_EXTENSIONS: Tuple[str, ...] = (
    ".md", ".markdown", ".txt", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".sh", ".sql", ".ini",
    ".toml", ".conf", ".xml", ".css",
)

#: html 的沙箱响应头。
#:
#: 这三行是「分享出去的 HTML 可以在页内直接跑，但拿不到平台身份」的全部依据：
#: - ``sandbox`` 让文档处于**不透明源**（opaque origin）：读不到 cookie/localStorage、
#:   跨域请求被 CORS 拦、无法导航顶层窗口；
#: - 给 ``allow-scripts`` 是必须的（否则生成的图表/交互全废），但**绝不能**同时给
#:   ``allow-same-origin``——二者并存等于没有沙箱；
#: - 放在**响应头**而不是只靠外层 ``<iframe sandbox>``：访客把 iframe 的 src 复制到
#:   地址栏直接打开时，iframe 属性就绕过了，响应头里的 sandbox 仍然生效。
HTML_SANDBOX_CSP = (
    "sandbox allow-scripts allow-forms; "
    "default-src 'unsafe-inline' 'unsafe-eval' data: blob: https: http:; "
    "img-src data: blob: https: http:; "
    "media-src data: blob: https: http:"
)

#: svg 只当图片看待，因此连脚本都不给
SVG_SANDBOX_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'" 


# ---------------------------------------------------------------------------
# 异常：对外语义与会话分享保持一致
# ---------------------------------------------------------------------------


class FileShareNotFoundError(Exception):
    """链接不存在 / 已作废 / 已过期——对外统一 404，不区分原因。"""


class FileShareAccessDeniedError(Exception):
    """密钥模式未解锁，或令牌无效。"""


class FileShareRateLimitedError(Exception):
    """同一 IP 在窗口内连续试错太多次。"""


class FileShareTargetError(Exception):
    """要分享的文件本身有问题：不存在、越权、超大、已变更。"""


# ---------------------------------------------------------------------------
# 纯函数：路径、指纹、媒体类型
# ---------------------------------------------------------------------------


def generate_share_id() -> str:
    """生成对外链接 ID：与会话分享同规格。"""
    return secrets.token_urlsafe(SHARE_ID_BYTES)


def normalize_rel_path(raw: str) -> str:
    """把任意形态的路径归一成「相对用户工作区根」的相对路径。

    规则：去掉前导分隔符，折叠重复分隔符，拒绝 ``..`` 与盘符——分享记录一旦被写进
    ``..``，下载侧无论怎么校验都在替攻击者做遍历，干脆在入口就断掉。
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        raise FileShareTargetError("路径为空")
    if text.startswith("/"):
        raise FileShareTargetError("只接受相对路径")
    parts: List[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise FileShareTargetError("路径不允许包含 ..")
        if ":" in segment:
            # 盘符或 NTFS 备用数据流，都不该出现在工作区文件名里
            raise FileShareTargetError("路径包含非法字符")
        parts.append(segment)
    if not parts:
        raise FileShareTargetError("路径为空")
    return "/".join(parts)


def split_workspace_path(rel_from_root: str) -> Tuple[str, str]:
    """把「相对工作区根的路径」拆成 ``(workspace_key, rel_path)``。

    ``rel_from_root`` 形如 ``admin__1/docs/报表.xlsx``：第一段是用户目录名，其余是文件。
    """
    normalized = normalize_rel_path(rel_from_root)
    head, _, tail = normalized.partition("/")
    if not tail:
        raise FileShareTargetError("路径必须位于用户目录内")
    return head, tail


def assert_share_path_belongs_to_owner(workspace_key: str, owner_key: str) -> None:
    """分享只能落在**本人**工作区目录下。

    为什么光校验「在 workspace_root 之内」不够：``workspace_root`` 是**所有用户**工作区的
    共同父目录（``data/agent_workspaces/``），里面的 ``owner__1``、``owner__2`` 是并列的。
    只做前缀校验时，两种入参都能指向别人的文件：

    - 绝对路径 ``/data/agent_workspaces/other__2/docs/a.xlsx``；
    - 相对路径 ``other__2/docs/a.xlsx``（首段目录存在时不会被补成本人目录名）。

    结果是任何登录用户只要知道路径，就能把他人文件做成**公开可访问**的分享链接，
    而且分享人还会被记成他自己。

    管理员持有 ``admin_all`` 的**浏览**范围，但浏览不等于可以生成公开链接：
    要分享他人/公共内容，走「复制到我的工作区再分享」这条既有路径。

    用严格相等而不是前缀比较：``admin__1`` 与 ``admin__10``、``admin__1x`` 是不同用户。
    """
    if not owner_key:
        raise FileShareTargetError("无法确定分享人")
    if workspace_key != owner_key:
        raise FileShareTargetError("只能分享本人工作区内的文件")


def compute_sha256(abs_path: str) -> str:
    """分块计算文件内容指纹：大文件也不会一次性读进内存。"""
    digest = hashlib.sha256()
    with open(abs_path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def guess_mime_type(file_name: str, file_ext: str) -> str:
    """按扩展名推断媒体类型，未知一律 octet-stream（浏览器会当下载处理）。"""
    guessed, _ = mimetypes.guess_type(file_name)
    if guessed:
        return guessed
    if file_ext in (".md", ".txt", ".log", ".csv", ".json", ".yaml", ".yml"):
        # 文本类统一带 charset，否则浏览器会把中文按 latin-1 显示成乱码
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def classify_preview_kind(file_ext: str, mime_type: str) -> str:
    """决定访客打开链接后怎么呈现：``html`` / ``svg`` / ``image`` / ``pdf`` / ``text`` / ``download``。

    这个判断只放在后端一处，前端读 ``preview_kind`` 即可——两边各写一套「哪些算文本」，
    迟早出现「列表说能看、打开是下载」。

    ``svg`` 与 ``html`` 分开：两者都可能是「能执行脚本的文档」，但呈现方式不同
    （svg 用 ``<img>`` 时脚本不执行，html 必须进沙箱 iframe）。
    """
    ext = (file_ext or "").lower()
    mime = (mime_type or "").lower()
    if ext in (".html", ".htm") or mime.startswith("text/html"):
        return "html"
    if ext == ".svg" or mime.startswith("image/svg"):
        return "svg"
    if ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        return "text"
    return "download"


def resolve_share_abs_path(*, workspace_root: str, workspace_key: str, rel_path: str) -> Optional[str]:
    """由分享记录还原绝对路径，并再次确认它没跑出该用户的工作区目录。

    分享记录是可信的（创建时校验过），但磁盘状态会变：目录可能被换成符号链接。
    因此这里用 ``realpath`` 复核一次，越界即视为文件不可用。
    """
    user_root = os.path.realpath(os.path.join(workspace_root, workspace_key))
    candidate = os.path.realpath(os.path.join(user_root, rel_path.replace("/", os.sep)))
    if not candidate.startswith(user_root + os.sep):
        return None
    return candidate


def build_download_meta(row: FileShare) -> Dict[str, Any]:
    """访客页面/接口共用的一份文件元信息：不含任何磁盘路径。"""
    return {
        "share_id": row.share_id,
        "file_name": row.file_name,
        "file_ext": row.file_ext or "",
        "file_size": int(row.file_size or 0),
        "mime_type": row.mime_type or "",
        "access_mode": row.access_mode,
        "status": conv_share.derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "owner_display": row.owner_username or "",
        "view_count": int(row.view_count or 0),
        "download_count": int(row.download_count or 0),
        # 访客页据此决定渲染方式，不在前端重复一套扩展名清单
        "preview_kind": classify_preview_kind(row.file_ext or "", row.mime_type or ""),
    }


# ---------------------------------------------------------------------------
# 业务层
# ---------------------------------------------------------------------------


async def _resolve_shared_file_path(
    db: AsyncSession,
    *,
    row: FileShare,
) -> str:
    """把分享记录还原成磁盘路径；文件已变更/被删一律抛出目标异常。"""
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root

    workspace_root = await resolve_workspace_root(ensure_exists=False)
    abs_path = resolve_share_abs_path(
        workspace_root=workspace_root,
        workspace_key=row.workspace_key,
        rel_path=row.rel_path,
    )
    if not abs_path or not os.path.isfile(abs_path):
        raise FileShareTargetError("文件已不存在")
    try:
        size = os.path.getsize(abs_path)
    except OSError as exc:
        raise FileShareTargetError("文件无法读取") from exc
    if row.file_size and size != int(row.file_size):
        raise FileShareTargetError("文件已更新，请重新分享")
    if row.content_sha256:
        try:
            digest = compute_sha256(abs_path)
        except OSError as exc:
            raise FileShareTargetError("文件无法读取") from exc
        if digest != row.content_sha256:
            raise FileShareTargetError("文件已更新，请重新分享")
    return abs_path


async def _artifact_storage_path(
    db: AsyncSession,
    *,
    user_id: str,
    artifact_id: str,
) -> str:
    """按产物 ID 找回它在工作区里的真实路径。

    生成类产物（图片、导出件）落盘在 ``<user_key>/generated/`` 下，但工具交给模型/前端的
    只有 ``download_url``（带 artifact_id + token），路径本身从没出过后端。前端因此把它
    判成「未落盘」，分享入口置灰——文件其实早就在工作区里了。这里让后端自己认 ID，
    既补上这段链路，也不必把服务器路径暴露给浏览器。

    归属不符与不存在报同一句话：不能拿 artifact_id 试探别人的产物。
    """
    from app.models.artifact import AiArtifact

    artifact_key = str(artifact_id or "").strip()
    if not artifact_key:
        raise FileShareTargetError("产物 ID 为空")

    try:
        owner_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise FileShareTargetError("无法确定分享人") from exc

    result = await db.execute(
        select(AiArtifact.storage_path).where(
            AiArtifact.id == artifact_key,
            AiArtifact.owner_user_id == owner_id,
        )
    )
    storage_path = result.scalar_one_or_none()
    if not storage_path:
        raise FileShareTargetError("产物不存在或不属于当前用户")
    return str(storage_path)


async def create_share(
    db: AsyncSession,
    *,
    user_info: Dict[str, Any],
    raw_path: Optional[str] = None,
    access_mode: str = "secret",
    secret: Optional[str] = None,
    expiry: Optional[Dict[str, Any]] = None,
    artifact_id: Optional[str] = None,
) -> FileShare:
    """创建一条文件分享。

    ``raw_path`` 允许两种形态，都会落到同一个 ``(workspace_key, rel_path)``：

    - 绝对路径，且必须位于工作区根之内；
    - 相对工作区根的路径，如 ``admin__1/docs/报表.xlsx`` 或 ``docs/报表.xlsx``（后者按
      当前用户的目录名补齐）。

    ``artifact_id`` 是另一条入口：生成类产物由前端只拿到 ``download_url``，后端按 ID 反查
    ``ai_artifacts.storage_path`` 得到同一个绝对路径，后面走完全相同的校验。两者给其一即可。
    """
    from app.services.ai.runtime.agentscope.workspace import (
        extract_workspace_identity,
        resolve_workspace_root,
        resolve_workspace_user_key,
    )

    workspace_root = await resolve_workspace_root(ensure_exists=False)
    user_id = str((user_info or {}).get("user_id") or (user_info or {}).get("id") or "").strip()
    if not user_id:
        raise FileShareTargetError("无法确定分享人")

    raw_text = str(raw_path or "").strip().replace("\\", "/")
    if artifact_id:
        # 产物形态：前端只有 download_url，路径由后端自己查
        raw_text = await _artifact_storage_path(db, user_id=user_id, artifact_id=artifact_id)
    elif not raw_text:
        raise FileShareTargetError("路径为空")

    # 本人工作区目录名：两种路径形态都要用它做归属校验，所以先算出来
    resolved_id, resolved_name = extract_workspace_identity(
        user_id=user_id,
        user_name=str((user_info or {}).get("username") or "") or None,
        user_info=user_info,
    )
    owner_key = resolve_workspace_user_key(
        user_id=resolved_id,
        user_name=resolved_name,
    )

    if os.path.isabs(raw_text):
        real_root = os.path.realpath(workspace_root)
        real_target = os.path.realpath(raw_text)
        if not real_target.startswith(real_root + os.sep):
            raise FileShareTargetError("文件不在工作区目录内")
        rel_from_root = os.path.relpath(real_target, real_root).replace(os.sep, "/")
    else:
        rel_from_root = normalize_rel_path(raw_text)
        # 前端可能只给「相对用户目录」的路径（如 docs/报表.xlsx），补齐用户目录名
        if "/" not in rel_from_root:
            raise FileShareTargetError("路径必须指向文件")
        user_root_prefixed = os.path.join(workspace_root, rel_from_root.split("/")[0])
        if not os.path.isdir(user_root_prefixed):
            rel_from_root = f"{owner_key}/{rel_from_root}"

    workspace_key, rel_path = split_workspace_path(rel_from_root)
    # 过了「在 workspace_root 之内」还不够：root 是所有用户的共同父目录
    assert_share_path_belongs_to_owner(workspace_key, owner_key)

    abs_path = resolve_share_abs_path(
        workspace_root=workspace_root,
        workspace_key=workspace_key,
        rel_path=rel_path,
    )
    if not abs_path or not os.path.isfile(abs_path):
        raise FileShareTargetError("文件不存在")

    file_size = os.path.getsize(abs_path)
    if file_size <= 0:
        raise FileShareTargetError("空文件无需分享")
    if file_size > MAX_SHARE_FILE_BYTES:
        raise FileShareTargetError("文件过大，暂不支持分享")

    file_name = os.path.basename(abs_path)[:FILE_NAME_MAX_LENGTH]
    file_ext = os.path.splitext(file_name)[1].lower()

    mode = "public" if str(access_mode or "").strip().lower() == "public" else "secret"
    plaintext_secret: Optional[str] = None
    if mode == "secret":
        plaintext_secret = conv_share.normalize_secret(secret) if secret else conv_share.generate_secret()

    row = FileShare(
        share_id=generate_share_id(),
        owner_user_id=user_id,
        owner_username=str((user_info or {}).get("username") or "") or None,
        workspace_key=workspace_key,
        rel_path=rel_path,
        file_name=file_name,
        file_ext=file_ext or None,
        file_size=file_size,
        content_sha256=compute_sha256(abs_path),
        mime_type=guess_mime_type(file_name, file_ext),
        access_mode=mode,
        secret_cipher=(
            conv_share.encrypt_secret(plaintext_secret) if plaintext_secret else None
        ),
        expires_at=conv_share.resolve_expires_at(expiry),
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    # 明文密码只在创建响应里回一次，之后只有密文；调用方拿它去展示「复制分享信息」
    row.__dict__["plaintext_secret"] = plaintext_secret
    return row


async def list_shares(
    db: AsyncSession,
    *,
    user_id: str,
    include_revoked: bool = False,
) -> List[FileShare]:
    """我分享出去的文件，按创建时间倒序。"""
    stmt = select(FileShare).where(FileShare.owner_user_id == str(user_id))
    if not include_revoked:
        stmt = stmt.where(FileShare.status == "active")
    stmt = stmt.order_by(FileShare.created_at.desc(), FileShare.id.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def get_share(db: AsyncSession, share_id: str) -> Optional[FileShare]:
    """按 share_id 取记录；不存在返回 None。"""
    return (
        await db.execute(select(FileShare).where(FileShare.share_id == str(share_id)))
    ).scalars().first()


async def revoke_share(db: AsyncSession, *, user_id: str, share_id: str) -> bool:
    """作废我的某条分享；不是本人的返回 False（调用方按 404 处理，避免探测）。"""
    row = await get_share(db, share_id)
    if row is None or str(row.owner_user_id) != str(user_id):
        return False
    if str(row.status) == "revoked":
        return True
    row.status = "revoked"
    row.revoked_at = datetime.now()
    await db.commit()
    return True


async def publish_public_meta(db: AsyncSession, share_id: str) -> Dict[str, Any]:
    """访客页首屏：文件名、大小、有效期；**不含磁盘路径**，因此不计数。"""
    row = await get_share(db, share_id)
    if row is None or not conv_share.is_share_reachable(row):
        raise FileShareNotFoundError(share_id)
    return build_download_meta(row)


async def unlock_share(
    db: AsyncSession, *, share_id: str, secret: str, client_ip: str
) -> Tuple[str, int]:
    """校验访问密码并签发短期令牌：返回 ``(token, expires_in_seconds)``。"""
    row = await get_share(db, share_id)
    if row is None or not conv_share.is_share_reachable(row):
        raise FileShareNotFoundError(share_id)
    if row.access_mode != "secret":
        raise ValueError("public share does not need unlocking")

    await conv_share.assert_unlock_allowed(share_id, client_ip)
    if not conv_share.verify_secret(row.secret_cipher or "", secret):
        await conv_share.register_unlock_failure(share_id, client_ip)
        raise FileShareAccessDeniedError("invalid secret")

    await conv_share.clear_unlock_failures(share_id, client_ip)
    return conv_share.issue_access_token(row.share_id, row.expires_at)


async def _authorize(
    db: AsyncSession,
    *,
    share_id: str,
    token: Optional[str],
    viewer_user_id: Optional[str],
) -> FileShare:
    """三条访客通路（预览 / 文本 / 下载）共用的一套可达性与令牌校验。

    抽出来是为了不让「下载校验严、预览校验松」这种差一处的漏洞出现：
    只要有一条路漏了令牌检查，设了密码的分享就等于没设。
    """
    row = await get_share(db, share_id)
    if row is None or not conv_share.is_share_reachable(row):
        raise FileShareNotFoundError(share_id)

    is_owner = bool(viewer_user_id) and str(viewer_user_id) == str(row.owner_user_id)
    if row.access_mode == "secret" and not is_owner:
        if not token or not conv_share.verify_access_token(token, row.share_id):
            raise FileShareAccessDeniedError("token required")
    return row


async def _bump_view(db: AsyncSession, *, row: FileShare) -> None:
    """在线预览计数 +1（与下载计数分开：看了一眼和存走了不是一回事）。"""
    await db.execute(
        update(FileShare)
        .where(FileShare.id == row.id)
        .values(view_count=FileShare.view_count + 1, last_viewed_at=datetime.now())
    )
    await db.commit()


async def resolve_inline(
    db: AsyncSession,
    *,
    share_id: str,
    token: Optional[str],
    viewer_user_id: Optional[str],
    count: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """在线预览取文件：返回 ``(绝对路径, 元信息)``，默认给预览计数 +1。

    与 ``resolve_download`` 的区别只有计数语义与响应头（inline vs attachment）；
    路径解析、指纹复核完全相同——预览不能成为比下载更宽松的入口。
    """
    row = await _authorize(db, share_id=share_id, token=token, viewer_user_id=viewer_user_id)
    abs_path = await _resolve_shared_file_path(db, row=row)
    meta = build_download_meta(row)
    if count:
        await _bump_view(db, row=row)
    return abs_path, meta


async def read_share_text(
    db: AsyncSession,
    *,
    share_id: str,
    token: Optional[str],
    viewer_user_id: Optional[str],
) -> Tuple[str, bool, Dict[str, Any]]:
    """读文本内容供页内渲染：返回 ``(text, truncated, meta)``。

    超限时只读前 ``MAX_PREVIEW_TEXT_BYTES`` 字节并标记 ``truncated``：几十 MB 的日志塞进
    DOM 只会让访客的浏览器卡死，不如老实说「只显示前 2 MB」。
    """
    import asyncio

    row = await _authorize(db, share_id=share_id, token=token, viewer_user_id=viewer_user_id)
    abs_path = await _resolve_shared_file_path(db, row=row)

    def _read() -> Tuple[str, bool]:
        with open(abs_path, "rb") as fh:
            raw = fh.read(MAX_PREVIEW_TEXT_BYTES + 1)
        truncated = len(raw) > MAX_PREVIEW_TEXT_BYTES
        if truncated:
            raw = raw[:MAX_PREVIEW_TEXT_BYTES]
        # 用 replace 而不是忽略错误：预览页把个别坏字节显示成 � 比整篇报错有用
        return raw.decode("utf-8", errors="replace"), truncated

    text, truncated = await asyncio.to_thread(_read)
    meta = build_download_meta(row)
    await _bump_view(db, row=row)
    return text, truncated, meta


async def resolve_download(
    db: AsyncSession,
    *,
    share_id: str,
    token: Optional[str],
    viewer_user_id: Optional[str],
    client_ip: str,
    count: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """校验通过后返回 ``(绝对路径, 元信息)``；这是唯一会给下载计数 +1 的入口。

    ``count=False`` 供 HEAD 探测使用：落地页要用它判断「令牌还能不能用、文件还是不是那一版」，
    这类探测不该被算成一次下载，否则打开几次页面计数就虚高。
    """
    row = await _authorize(db, share_id=share_id, token=token, viewer_user_id=viewer_user_id)
    abs_path = await _resolve_shared_file_path(db, row=row)
    meta = build_download_meta(row)
    if not count:
        return abs_path, meta

    # 原子自增，避免并发下载丢计数（与会话分享同一写法）
    await db.execute(
        update(FileShare)
        .where(FileShare.id == row.id)
        .values(
            download_count=FileShare.download_count + 1,
            last_downloaded_at=datetime.now(),
        )
    )
    await db.commit()
    return abs_path, meta

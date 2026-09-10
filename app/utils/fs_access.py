"""文件系统浏览/预览的用户级访问控制（公共目录 + 本人私有工作区）。"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, BinaryIO

from fastapi import HTTPException

from app.utils.fs_paths import get_data_base_dir, normalize_under_base

PUBLIC_DATA_SUBDIRS: tuple[str, ...] = ("branding", "skills", "docs")
PUBLIC_RUNTIME_FILE_NAMES: tuple[str, ...] = ("FAQ.md", "README.md")
USER_WORKSPACE_RESERVED_DIR_NAMES = frozenset({"docs", "uploads", "sandbox", ".trash", "skills", "sessions"})
SESSION_DIR_NAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
UPLOAD_NAME_ALLOCATION_ATTEMPTS = 100


def build_upload_storage_name(filename: str | None, suffix: str | None = None) -> str:
    """保留可读原名，仅清理路径/控制字符，并追加短唯一后缀。"""
    raw_name = str(filename or "").replace("/", "_").replace("\\", "_")
    clean_name = re.sub(r"[\x00-\x1f\x7f]", "_", raw_name).strip()
    if not clean_name:
        clean_name = "upload"

    stem, extension = os.path.splitext(clean_name)
    short_suffix = (suffix or uuid.uuid4().hex[:4]).strip()
    return f"{stem}_{short_suffix}{extension}"


def open_upload_storage_file(directory: str, filename: str | None) -> tuple[str, BinaryIO]:
    """原子地分配上传文件名，避免短后缀碰撞覆盖已有文件。"""
    for _ in range(UPLOAD_NAME_ALLOCATION_ATTEMPTS):
        storage_name = build_upload_storage_name(filename)
        path = os.path.normpath(os.path.join(directory, storage_name))
        try:
            return path, open(path, "xb")
        except FileExistsError:
            continue
    raise FileExistsError("无法为上传文件分配唯一存储名")


def get_platform_skills_root() -> str | None:
    """Platform skills directory (Docker: data/skills, local dev: ~/.agents/skills)."""
    try:
        from app.core.config import settings

        raw = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return None
    if not raw:
        return None
    skills_root = os.path.normpath(os.path.abspath(str(raw)))
    if os.path.isdir(skills_root):
        return skills_root
    return None


def is_fs_admin(user_info: dict[str, Any] | None) -> bool:
    if not user_info:
        return False
    return str(user_info.get("role") or "").strip().lower() == "admin"


def get_public_data_roots(base: str | None = None) -> list[str]:
    data_base = base or get_data_base_dir()
    roots: list[str] = []
    for name in PUBLIC_DATA_SUBDIRS:
        candidate = os.path.normpath(os.path.join(data_base, name))
        if os.path.isdir(candidate):
            roots.append(candidate)
    return sorted(set(roots))


def get_public_fs_roots(base: str | None = None) -> list[str]:
    """Public browse roots: data subdirs plus configured platform skills dir."""
    roots = get_public_data_roots(base)
    skills_root = get_platform_skills_root()
    if skills_root and skills_root not in roots:
        roots.append(skills_root)
    return sorted(set(roots))


def get_public_runtime_file_targets() -> list[str]:
    """Return exact application-root files exposed through public read links."""
    project_root = get_public_runtime_help_root()
    return [os.path.join(project_root, name) for name in PUBLIC_RUNTIME_FILE_NAMES]


def get_user_private_workspace_root(user_info: dict[str, Any] | None) -> str | None:
    if not user_info:
        return None
    from app.services.ai.runtime.agentscope.workspace import (
        default_workspace_root,
        extract_workspace_identity,
        resolve_workspace_user_key,
    )

    user_id, user_name = extract_workspace_identity(user_info=user_info)
    if user_id is None:
        return None
    user_key = resolve_workspace_user_key(user_id=user_id, user_name=user_name)
    return os.path.normpath(os.path.join(default_workspace_root(), user_key))


def get_user_uploads_dir(user_info: dict[str, Any] | None) -> str | None:
    """用户会话附件目录：agent_workspaces/{user_key}/uploads（仅本人可访问）。"""
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return None
    return os.path.normpath(os.path.join(private_root, "uploads"))


def get_user_docs_dir(user_info: dict[str, Any] | None) -> str | None:
    """用户文档目录：agent_workspaces/{user_key}/docs（AI 默认落盘，跨会话共享）。"""
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return None
    return os.path.normpath(os.path.join(private_root, "docs"))


def get_user_sessions_dir(user_info: dict[str, Any] | None) -> str | None:
    """用户会话目录容器：agent_workspaces/{user_key}/sessions。"""
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return None
    return os.path.normpath(os.path.join(private_root, "sessions"))


def is_session_dir_name(name: str) -> bool:
    """判断目录名是否为会话 ID（sessions/ 下或旧版用户根下的直接子目录）。"""
    cleaned = str(name or "").strip()
    if not cleaned or cleaned in USER_WORKSPACE_RESERVED_DIR_NAMES:
        return False
    return bool(SESSION_DIR_NAME_RE.match(cleaned)) or cleaned.startswith("conv_")


def is_session_workdir_path(user_root: str, target_path: str) -> bool:
    """判断目标路径是否为可自动创建的会话工作目录（含旧版平铺路径）。"""
    norm_target = os.path.normpath(target_path)
    norm_root = os.path.normpath(user_root)
    if not norm_target.startswith(norm_root + os.sep):
        return False
    rel = os.path.relpath(norm_target, norm_root)
    parts = rel.split(os.sep)
    if len(parts) == 2 and parts[0] == "sessions" and is_session_dir_name(parts[1]):
        return True
    if len(parts) == 1 and is_session_dir_name(parts[0]):
        return True
    return False


def get_user_sandbox_dir(user_info: dict[str, Any] | None) -> str | None:
    """用户 SQLite 临时沙箱目录：agent_workspaces/{user_key}/sandbox（仅本人可访问）。"""
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return None
    return os.path.normpath(os.path.join(private_root, "sandbox"))


def get_allowed_fs_roots(user_info: dict[str, Any] | None) -> list[str]:
    data_base = get_data_base_dir()
    if is_fs_admin(user_info):
        return [data_base]

    roots = get_public_fs_roots(data_base)
    private_root = get_user_private_workspace_root(user_info)
    if private_root:
        roots.append(private_root)
    return sorted(set(roots))


def _canonical_runtime_path(path: str, base: str | None = None) -> str:
    """Resolve runtime paths without applying legacy workspace-prefix aliases."""
    raw_path = str(path or "")
    data_base = os.path.normpath(base or get_data_base_dir())
    if raw_path == "/app/data":
        raw_path = data_base
    elif raw_path.startswith("/app/data/"):
        raw_path = os.path.join(data_base, raw_path.removeprefix("/app/data/"))
    return os.path.normpath(os.path.realpath(os.path.abspath(raw_path)))


def get_public_runtime_help_root() -> str:
    """Return the service application root whose direct ``*.md`` files are help sources."""
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def is_public_runtime_help_path(path: str) -> bool:
    """Allow only a canonical, direct child Markdown file under the service root."""
    normalized = _canonical_runtime_path(path)
    root = get_public_runtime_help_root()
    return (
        os.path.dirname(normalized) == root
        and os.path.basename(normalized).endswith(".md")
    )


def get_public_runtime_help_files() -> list[str]:
    """List existing direct service-root Markdown files exposed as read-only help."""
    root = get_public_runtime_help_root()
    try:
        names = os.listdir(root)
    except OSError:
        return []

    files: list[str] = []
    for name in sorted(names):
        if not name.endswith(".md"):
            continue
        candidate = _canonical_runtime_path(os.path.join(root, name))
        if is_public_runtime_help_path(candidate) and os.path.isfile(candidate):
            files.append(candidate)
    return sorted(set(files))


def _is_path_within_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def is_runtime_path_allowed(
    path: str,
    user_info: dict[str, Any] | None,
) -> bool:
    """Strictly authorize a path already destined for a native runtime tool.

    Unlike the browser/API compatibility resolver, this function never
    rewrites arbitrary absolute paths containing ``agent_workspaces/``.
    Authorization is evaluated against the same canonical path passed to the
    native file tool.
    """
    normalized = _canonical_runtime_path(path)
    if (
        normalized in get_public_runtime_file_targets()
        or is_public_runtime_help_path(normalized)
    ):
        return True
    return any(
        _is_path_within_root(normalized, os.path.realpath(root))
        for root in get_allowed_fs_roots(user_info)
    )


def is_runtime_path_writable(
    path: str,
    user_info: dict[str, Any] | None,
) -> bool:
    """Strictly allow native runtime writes only inside the user's root."""
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return False
    normalized = _canonical_runtime_path(path)
    return _is_path_within_root(normalized, os.path.realpath(private_root))


def is_fs_virtual_root(path: str | None, base: str | None = None) -> bool:
    data_base = base or get_data_base_dir()
    if not path:
        return True
    return os.path.normpath(path) == os.path.normpath(data_base)


def normalize_fs_path(path: str, base: str | None = None) -> str | None:
    normalized = normalize_under_base(path, base or get_data_base_dir())
    if normalized:
        # Resolve symlink components before checking authorization boundaries.
        return os.path.normpath(os.path.realpath(normalized))

    skills_root = get_platform_skills_root()
    if not skills_root:
        return None

    raw_path = str(path or "")
    if raw_path.startswith("/app/data/skills/"):
        raw_path = os.path.join(get_data_base_dir(), "skills", raw_path.removeprefix("/app/data/skills/"))
    elif raw_path == "/app/data/skills":
        data_skills = os.path.join(get_data_base_dir(), "skills")
        if os.path.isdir(data_skills):
            return os.path.normpath(os.path.realpath(data_skills))

    candidate = os.path.realpath(os.path.abspath(raw_path))
    if candidate == skills_root or candidate.startswith(skills_root + os.sep):
        return os.path.normpath(candidate)

    if not os.path.isabs(raw_path):
        joined = os.path.normpath(
            os.path.realpath(os.path.join(skills_root, raw_path.lstrip("./")))
        )
        if joined == skills_root or joined.startswith(skills_root + os.sep):
            return joined
    return None


def is_public_fs_path(path: str | None, base: str | None = None) -> bool:
    """判断路径是否属于公共目录（branding, skills, docs 等平台公共资源）。"""
    if not path:
        return False
    data_base = base or get_data_base_dir()
    under_base = normalize_under_base(path, data_base)
    if under_base:
        for root in get_public_fs_roots(data_base):
            norm_root = os.path.normpath(root)
            if under_base == norm_root or under_base.startswith(norm_root + os.sep):
                return True

    normalized = normalize_fs_path(path, base)
    if not normalized:
        return False
    for root in get_public_fs_roots(base):
        norm_root = os.path.normpath(root)
        if normalized == norm_root or normalized.startswith(norm_root + os.sep):
            return True
    return False


def is_path_allowed(path: str, user_info: dict[str, Any] | None) -> bool:
    data_base = get_data_base_dir()
    under_base = normalize_under_base(path, data_base)
    if is_public_runtime_help_path(path):
        return True
    if is_fs_admin(user_info):
        if under_base:
            return True
        normalized = normalize_fs_path(path)
        return bool(normalized)

    # 1. 检查物理规范化路径
    normalized = normalize_fs_path(path)
    if normalized:
        for root in get_allowed_fs_roots(user_info):
            if normalized == root or normalized.startswith(root + os.sep):
                return True

    # 2. 检查逻辑挂载路径（支持公共目录如 data/docs 下软链接至平台根目录官方文档）
    if under_base:
        if is_other_user_workspace_path(under_base, user_info):
            return False
        for root in get_allowed_fs_roots(user_info):
            if under_base == root or under_base.startswith(root + os.sep):
                return True

    return False


def assert_path_allowed(path: str, user_info: dict[str, Any] | None) -> str:
    if not is_path_allowed(path, user_info):
        data_base = get_data_base_dir()
        if not normalize_under_base(path, data_base) and not normalize_fs_path(path):
            raise HTTPException(
                status_code=403,
                detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。",
            )
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：无权访问其他用户的私有目录或非授权路径。",
        )
    normalized = normalize_fs_path(path)
    if normalized and (
        is_fs_admin(user_info)
        or any(normalized == r or normalized.startswith(r + os.sep) for r in get_allowed_fs_roots(user_info))
    ):
        return normalized
    under_base = normalize_under_base(path, get_data_base_dir())
    if under_base:
        return under_base
    return normalized or path


def is_path_writable(path: str, user_info: dict[str, Any] | None) -> bool:
    """判断路径是否可写。

    - admin：路径在 data 根下（含公共目录）均可写。
    - 普通用户：仅允许写入本人 agent_workspaces 私有目录。
    """
    normalized = normalize_fs_path(path)
    if not normalized:
        return False
    if is_fs_admin(user_info):
        data_base = os.path.normpath(get_data_base_dir())
        return normalized == data_base or normalized.startswith(data_base + os.sep)
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return False
    return normalized == private_root or normalized.startswith(private_root + os.sep)


def assert_path_writable(path: str, user_info: dict[str, Any] | None) -> str:
    normalized = normalize_fs_path(path)
    if not normalized:
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。",
        )
    if not is_path_writable(normalized, user_info):
        raise HTTPException(
            status_code=403,
            detail="仅允许写入本人 AI 工作目录内的文件。",
        )
    return normalized


def resolve_parent_path(current_path: str, user_info: dict[str, Any] | None) -> str | None:
    data_base = get_data_base_dir()
    normalized = assert_path_allowed(current_path, user_info)
    if is_fs_admin(user_info):
        if normalized == data_base:
            return None
        parent = os.path.dirname(normalized)
        if parent == normalized or not parent.startswith(data_base):
            return None
        return parent

    allowed_roots = get_allowed_fs_roots(user_info)
    if normalized in allowed_roots:
        return None

    parent = os.path.dirname(normalized)
    if parent in allowed_roots or is_path_allowed(parent, user_info):
        return parent
    return None


def is_other_user_workspace_path(path: str, user_info: dict[str, Any] | None) -> bool:
    """True when path is under agent_workspaces but not the current user's root."""
    normalized = normalize_fs_path(path)
    if not normalized:
        return False
    workspaces_root = os.path.normpath(
        os.path.join(get_data_base_dir(), "agent_workspaces")
    )
    if not (normalized == workspaces_root or normalized.startswith(workspaces_root + os.sep)):
        return False
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return True
    return not (
        normalized == private_root or normalized.startswith(private_root + os.sep)
    )

"""文件系统浏览/预览的用户级访问控制（公共目录 + 本人私有工作区）。"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from app.utils.fs_paths import get_data_base_dir, normalize_under_base

PUBLIC_DATA_SUBDIRS: tuple[str, ...] = ("uploads", "branding", "sandbox", "skills")


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


def get_allowed_fs_roots(user_info: dict[str, Any] | None) -> list[str]:
    data_base = get_data_base_dir()
    if is_fs_admin(user_info):
        return [data_base]

    roots = get_public_data_roots(data_base)
    private_root = get_user_private_workspace_root(user_info)
    if private_root:
        roots.append(private_root)
    return sorted(set(roots))


def is_fs_virtual_root(path: str | None, base: str | None = None) -> bool:
    data_base = base or get_data_base_dir()
    if not path:
        return True
    return os.path.normpath(path) == os.path.normpath(data_base)


def normalize_fs_path(path: str, base: str | None = None) -> str | None:
    return normalize_under_base(path, base or get_data_base_dir())


def is_path_allowed(path: str, user_info: dict[str, Any] | None) -> bool:
    normalized = normalize_fs_path(path)
    if not normalized:
        return False
    if is_fs_admin(user_info):
        return True

    for root in get_allowed_fs_roots(user_info):
        if normalized == root or normalized.startswith(root + os.sep):
            return True
    return False


def assert_path_allowed(path: str, user_info: dict[str, Any] | None) -> str:
    normalized = normalize_fs_path(path)
    if not normalized:
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。",
        )
    if not is_path_allowed(normalized, user_info):
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：无权访问其他用户的私有目录或非授权路径。",
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

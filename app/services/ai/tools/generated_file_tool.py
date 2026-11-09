"""Agent-facing tool for publishing an existing workspace file for download."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from app.core.context import get_current_agent_context
from app.services.ai.tools.generated_file_service import (
    register_artifact,
    resolve_delegation_parent_scope,
)
from app.services.ai.tools.tool_compat import tool

logger = logging.getLogger(__name__)

_DOCKER_WORKSPACE_ROOT = "/workspace"
# 子代理产物在父会话工作区里的共享目录：主代理可直接读取，无需二次发布。
_DELIVERABLES_DIR_NAME = "deliverables"

_EXTENSION_ARTIFACT_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".csv": "csv",
    ".pdf": "pdf",
    ".docx": "word",
    ".xlsx": "excel",
    ".xlsm": "excel",
}


def _user_name(context: Any) -> str | None:
    dimensions = getattr(context, "user_dimensions", None) or {}
    raw_name = dimensions.get("user_name") or dimensions.get("username")
    if not raw_name:
        return None
    name = str(raw_name).strip()
    return name or None


def _path_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _artifact_type(path: Path, requested: str | None) -> str:
    explicit = str(requested or "").strip().lower()
    if explicit:
        return explicit
    return _EXTENSION_ARTIFACT_TYPES.get(path.suffix.lower(), "document")


async def _resolve_source_path(path: str, context: Any) -> Path:
    from app.services.ai.runtime.agentscope.workspace import (
        resolve_session_workdir,
        resolve_user_workspace_root,
        resolve_workspace_root,
    )

    raw = str(path or "").strip()
    if not raw:
        raise ValueError("发布文件时必须提供 path")
    if getattr(context, "user_id", None) is None:
        raise ValueError("当前会话缺少用户身份，无法发布下载文件")

    root = Path(await resolve_workspace_root()).resolve()
    user_root_text = resolve_user_workspace_root(
        root=str(root),
        user_id=context.user_id,
        user_name=_user_name(context),
    )
    user_root = Path(user_root_text or (root / _workspace_user_key(context))).resolve()

    raw_path = Path(raw)
    if raw == _DOCKER_WORKSPACE_ROOT or raw.startswith(f"{_DOCKER_WORKSPACE_ROOT}/"):
        candidate = user_root / raw[len(_DOCKER_WORKSPACE_ROOT):].lstrip("/\\")
    elif raw_path.is_absolute():
        candidate = raw_path
    else:
        conversation_id = getattr(context, "conversation_id", None)
        if not conversation_id:
            raise ValueError("相对路径发布需要当前会话工作目录")
        session_workdir = Path(
            resolve_session_workdir(
                root=str(root),
                user_id=context.user_id,
                user_name=_user_name(context),
                conversation_id=conversation_id,
            )
        )
        candidate = session_workdir / raw
        if not candidate.exists():
            user_candidate = user_root / raw
            if user_candidate.exists():
                candidate = user_candidate

    resolved = candidate.resolve()
    if not _path_under(resolved, user_root):
        raise ValueError("文件必须位于当前用户工作区内")
    if not resolved.is_file():
        raise ValueError("文件不存在或不可访问")
    return resolved


def _workspace_user_key(context: Any) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_user_key

    return resolve_workspace_user_key(
        user_id=context.user_id,
        user_name=_user_name(context),
    )


def _delegation_parent_context(context: Any) -> tuple[str | None, str | None, str | None]:
    """返回委派父归属 ``(parent_conversation_id, parent_trace_id, run_id)``。

    非委派场景（``delegation_depth <= 0``）三项均为 None：产物按当前会话登记。
    口径统一在 ``generated_file_service.resolve_delegation_parent_scope``，与
    Office 写工具等其它登记入口保持一致。
    """
    return resolve_delegation_parent_scope(context)


async def _mirror_to_parent_session(
    source: Path,
    *,
    context: Any,
    filename: str,
    parent_conversation_id: str,
    run_id: str | None,
) -> tuple[Path, str] | None:
    """把子代理产物复制到父会话 ``deliverables/<run>/``，供主代理直接读取。

    返回 ``(副本绝对路径, 相对父会话工作区的路径)``；复制失败只告警并返回 None，
    回退到原路径登记 —— 发布下载地址不能因为镜像失败而失败。
    """
    from app.services.ai.runtime.agentscope.workspace import (
        resolve_session_workdir,
        resolve_user_workspace_root,
        resolve_workspace_root,
    )

    try:
        root = Path(await resolve_workspace_root()).resolve()
        user_name = _user_name(context)
        user_root = Path(
            resolve_user_workspace_root(
                root=str(root),
                user_id=context.user_id,
                user_name=user_name,
            )
        ).resolve()
        parent_workdir = Path(
            resolve_session_workdir(
                root=str(root),
                user_id=context.user_id,
                user_name=user_name,
                conversation_id=parent_conversation_id,
            )
        ).resolve()
        target_dir = parent_workdir / _DELIVERABLES_DIR_NAME / (run_id or "subagent")
        if not _path_under(target_dir, user_root):
            logger.warning(
                "[publish_generated_file] refusing to mirror outside user workspace: %s",
                target_dir,
            )
            return None
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if target.resolve() != source:
            shutil.copy2(source, target)
        return target, str(target.relative_to(parent_workdir))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[publish_generated_file] failed to mirror %s to parent session %s: %s",
            source,
            parent_conversation_id,
            exc,
        )
        return None


@tool
async def publish_generated_file(
    path: str,
    filename: str | None = None,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    """Publish an existing current-user workspace file and return its download URL.

    Call this after Write/Bash or another file-producing tool has created the
    deliverable. The returned ``download_url`` must be copied verbatim into the
    final response; never replace it with a physical path or an invented URL.
    Supports local workspace paths, session-relative paths, and Docker
    ``/workspace/...`` paths. It never creates or modifies the source file.

    In a delegated subagent turn the artifact is additionally registered against
    the parent conversation and mirrored to ``deliverables/<run>/`` inside the
    parent session workspace, returned as ``parent_workspace_path``; the parent
    agent can read that relative path directly and must not publish it again.
    When publishing as a delegated subagent, mention ``parent_workspace_path``
    in your final answer so the parent can reuse the file without guessing.
    """
    context = get_current_agent_context()
    if context is None:
        raise ValueError("当前会话缺少执行上下文，无法发布下载文件")

    source = await _resolve_source_path(path, context)
    display_filename = Path(str(filename or source.name)).name
    if not display_filename or display_filename in {".", ".."}:
        raise ValueError("下载文件名无效")

    parent_conversation_id, parent_trace_id, delegation_run_id = _delegation_parent_context(
        context
    )
    publish_source = source
    parent_workspace_path: str | None = None
    if parent_conversation_id:
        mirrored = await _mirror_to_parent_session(
            source,
            context=context,
            filename=display_filename,
            parent_conversation_id=parent_conversation_id,
            run_id=delegation_run_id,
        )
        if mirrored is not None:
            publish_source, parent_workspace_path = mirrored

    try:
        artifact = await register_artifact(
            source_path=publish_source,
            filename=display_filename,
            owner_user_id=context.user_id,
            artifact_type=_artifact_type(publish_source, artifact_type),
            conversation_id=context.conversation_id,
            parent_conversation_id=parent_conversation_id,
            trace_id=context.trace_id,
            parent_trace_id=parent_trace_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"文件已存在，但下载地址登记失败：{exc}") from exc

    payload = artifact.to_tool_payload()
    result: dict[str, Any] = {
        "status": "ok",
        "summary": "已生成文件下载地址",
        "artifact_type": _artifact_type(publish_source, artifact_type),
        "filename": payload["filename"],
        "mime_type": payload["mime_type"],
        "size": payload["size"],
        "download_url": payload["download_url"],
        "expires_at": artifact.expires_at.isoformat(),
    }
    if parent_workspace_path:
        result["parent_workspace_path"] = parent_workspace_path
    return result

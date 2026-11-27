import asyncio
import errno
import fnmatch
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from typing import Annotated, List, Optional, Dict, Any, Literal
from fastapi import APIRouter, HTTPException, Depends, Query, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from app.schemas.response import StandardResponse
from app.core.dependencies import require_api_key
from app.core.redis import get_redis
from app.core.upload_limits import MAX_FILES_PER_DIRECTORY, count_files_under
from app.utils.fs_paths import get_data_base_dir, normalize_under_base
from app.utils.fs_access import (
    USER_WORKSPACE_RESERVED_DIR_NAMES,
    assert_path_allowed,
    assert_path_writable,
    get_allowed_fs_roots,
    get_platform_skills_root,
    get_public_fs_roots,
    get_user_docs_dir,
    get_user_private_workspace_root,
    get_user_sessions_dir,
    get_user_uploads_dir,
    open_upload_storage_file,
    is_forbidden_platform_skill_path,
    is_session_workdir_path,
    is_fs_admin,
    is_fs_virtual_root,
    is_path_allowed,
    is_path_writable,
    is_public_fs_path,
    is_public_runtime_help_path,
    normalize_fs_path,
    resolve_parent_path,
)

logger = logging.getLogger(__name__)


async def _prime_forbidden_skill_cache(
    user_info: Dict[str, Any] = Depends(require_api_key),
) -> None:
    """文件系统接口的前置预热：被禁技能的读判定是同步函数，只能读进程内缓存。

    不带这层预热时，用户直接调 /api/v1/chat/fs/* 会落在"缓存未命中即放行"的窗口里，
    被禁技能的 SKILL.md 仍然读得到。依赖对象与各端点自身的 require_api_key 相同，
    FastAPI 复用同一份认证结果（不会重复认证）；查询失败只降级放行，不阻断接口。
    """
    try:
        from app.services.ai.skills.policy import prime_forbidden_skill_cache

        await prime_forbidden_skill_cache(user_info=user_info)
    except Exception:
        logger.debug("[fs] priming forbidden skill cache failed", exc_info=True)


router = APIRouter(dependencies=[Depends(_prime_forbidden_skill_cache)])

TRASH_DIR_NAME = ".trash"
TRASH_META_FILENAME = ".trash_meta.json"
WORKSPACE_RECENT_FILES_MAX = 20
WORKSPACE_RECENT_REDIS_PREFIX = "agent:workspace_recent_files:"
WORKSPACE_BROWSER_PREFS_REDIS_PREFIX = "agent:workspace_browser_prefs:"
SEARCH_SKIP_DIR_NAMES = frozenset({
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
})
SEARCH_MAX_SCANNED_NODES = 50_000
BATCH_DOWNLOAD_MAX_FILES = 2000
BATCH_DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
USAGE_CACHE_TTL_SECONDS = 30.0
_workspace_usage_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_trash_meta_lock = threading.Lock()


def _invalidate_usage_cache(path_or_root: Optional[str] = None) -> None:
    """失效工作区容量统计缓存：传入具体路径时仅失效所属工作区，否则清空全部。"""
    if not path_or_root:
        _workspace_usage_cache.clear()
        return
    owner_root = _resolve_owner_workspace_root(path_or_root)
    if owner_root:
        _workspace_usage_cache.pop(owner_root, None)
    else:
        _workspace_usage_cache.pop(os.path.normpath(path_or_root), None)
WORKSPACE_BROWSER_TYPE_FILTERS = frozenset({
    "all",
    "folder",
    "image",
    "markdown",
    "document",
    "html",
    "code",
    "spreadsheet",
    "presentation",
    "archive",
    "video",
    "audio",
    "data",
})

IMAGE_PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

class FileItem(BaseModel):
    name: str = Field(..., description="文件名或目录名")
    path: str = Field(..., description="绝对路径")
    is_dir: bool = Field(..., description="是否是目录")
    size: int = Field(..., description="文件大小（字节），目录为0")
    mtime: float = Field(..., description="修改时间戳")
    is_user_workspace: bool = Field(False, description="是否为当前用户的 AI 工作目录根")
    is_public: bool = Field(False, description="是否为公共只读目录或文件")
    readonly: bool = Field(False, description="是否为只读项")

class FileListResponse(BaseModel):
    current_path: str = Field(..., description="当前浏览的绝对路径")
    parent_path: Optional[str] = Field(None, description="上级目录绝对路径，若在根目录则为 None")
    is_root: bool = Field(..., description="是否已在安全根目录")
    items: List[FileItem] = Field(..., description="子文件和子目录列表")
    scope: str = Field(..., description="当前可见范围：admin_all / user_scoped")
    writable: bool = Field(False, description="当前目录是否允许新建文件/文件夹")
    user_workspace_root: Optional[str] = Field(None, description="当前用户私有工作区根路径")
    is_virtual_root: bool = Field(False, description="是否为授权根目录汇总视图（非真实单一路径目录）")


class FileSearchResponse(BaseModel):
    items: List[FileItem] = Field(..., description="匹配的文件/目录")
    query: str = Field(..., description="搜索关键词")
    search_root: str = Field(..., description="搜索起始目录")
    truncated: bool = Field(False, description="结果是否因上限被截断")


def _is_user_workspace_path(path: str, user_info: Dict[str, Any] | None) -> bool:
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return False
    return os.path.normpath(path) == os.path.normpath(private_root)


def _append_fs_entry(
    results: List[FileItem],
    entry_path: str,
    is_dir: bool,
    *,
    user_info: Dict[str, Any] | None = None,
) -> None:
    try:
        stat = os.stat(entry_path)
        is_user_ws = _is_user_workspace_path(entry_path, user_info)
        is_pub = is_public_fs_path(entry_path)
        is_writable = is_path_writable(entry_path, user_info)
        results.append(
            FileItem(
                name=os.path.basename(entry_path),
                path=entry_path,
                is_dir=is_dir,
                size=0 if is_dir else stat.st_size,
                mtime=stat.st_mtime,
                is_user_workspace=is_user_ws,
                is_public=is_pub,
                readonly=not is_writable,
            )
        )
    except OSError:
        pass


def _resolve_owner_workspace_root(path: str) -> Optional[str]:
    """根据物理路径解析其归属的私有工作区根目录（agent_workspaces/<user_key>）。

    管理员浏览/删除其他用户工作区内的条目时，需要据此定位原用户的 .trash，
    避免把他人文件误丢进管理员自己的回收站。

    两侧统一走 realpath：admin 的路径来自 `assert_path_allowed()`，返回值已被解引用，
    而 agent_workspaces 前缀若按 normpath 拼（data 根或任一层是软链时）会与本函数
    失配，导致归属推断静默失效——管理员删他人文件就又落回自己的回收站。
    """
    if not path:
        return None
    real_path = os.path.realpath(path)
    workspaces_root = _workspaces_root_realpath()
    if not real_path.startswith(workspaces_root + os.sep):
        return None
    rel = os.path.relpath(real_path, workspaces_root)
    parts = rel.split(os.sep)
    if not parts or parts[0] in ("", ".", ".."):
        return None
    return os.path.normpath(os.path.join(workspaces_root, parts[0]))


def _workspaces_root_realpath() -> str:
    """agent_workspaces 根的真实物理路径（与 `assert_path_allowed()` 的 realpath 口径一致）。"""
    return os.path.normpath(os.path.realpath(os.path.join(get_base_dir(), "agent_workspaces")))


def _is_trash_path(path: str, user_info: Dict[str, Any]) -> bool:
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return False
    trash = os.path.normpath(os.path.join(os.path.realpath(private_root), TRASH_DIR_NAME))
    normalized = os.path.normpath(os.path.realpath(path)) if path else ""
    return bool(normalized) and (normalized == trash or normalized.startswith(trash + os.sep))


def _is_any_trash_path(path: str, user_info: Dict[str, Any] | None = None) -> bool:
    """判断路径是否落在当前用户或任意用户工作区的 .trash 根目录或其子树内。"""
    if not path:
        return False
    normalized = os.path.normpath(os.path.realpath(path))
    if user_info and _is_trash_path(normalized, user_info):
        return True
    owner_root = _resolve_owner_workspace_root(normalized)
    if owner_root:
        owner_trash = os.path.normpath(os.path.join(owner_root, TRASH_DIR_NAME))
        if normalized == owner_trash or normalized.startswith(owner_trash + os.sep):
            return True
    return False


def _list_directory_entries(target_path: str, user_info: Dict[str, Any]) -> List[FileItem]:
    items: List[FileItem] = []
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if is_forbidden_platform_skill_path(entry.path, user_info):
                    continue
                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    is_user_ws = _is_user_workspace_path(entry.path, user_info)
                    is_pub = is_public_fs_path(entry.path)
                    is_writable = is_path_writable(entry.path, user_info)
                    items.append(
                        FileItem(
                            name=entry.name,
                            path=entry.path,
                            is_dir=is_dir,
                            size=0 if is_dir else stat.st_size,
                            mtime=stat.st_mtime,
                            is_user_workspace=is_user_ws,
                            is_public=is_pub,
                            readonly=not is_writable,
                        )
                    )
                except Exception:
                    continue
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取目录失败: {str(exc)}") from exc

    private_root = get_user_private_workspace_root(user_info)
    if private_root and os.path.normpath(target_path) == os.path.normpath(private_root):
        trash_path = os.path.join(private_root, TRASH_DIR_NAME)
        if not any(i.path == trash_path for i in items):
            try:
                os.makedirs(trash_path, exist_ok=True)
                stat = os.stat(trash_path)
                items.append(
                    FileItem(
                        name=TRASH_DIR_NAME,
                        path=trash_path,
                        is_dir=True,
                        size=0,
                        mtime=stat.st_mtime,
                        is_user_workspace=False,
                        is_public=False,
                        readonly=False,
                    )
                )
            except OSError:
                pass

    items.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    return items


def _list_virtual_root_entries(user_info: Dict[str, Any]) -> List[FileItem]:
    items: List[FileItem] = []
    for root in get_allowed_fs_roots(user_info):
        if not os.path.isdir(root):
            continue
        try:
            stat = os.stat(root)
            is_user_ws = _is_user_workspace_path(root, user_info)
            is_pub = is_public_fs_path(root)
            is_writable = is_path_writable(root, user_info)
            items.append(
                FileItem(
                    name=os.path.basename(root),
                    path=root,
                    is_dir=True,
                    size=0,
                    mtime=stat.st_mtime,
                    is_user_workspace=is_user_ws,
                    is_public=is_pub,
                    readonly=not is_writable,
                )
            )
        except OSError:
            continue

    def _sort_key(item: FileItem) -> tuple[int, str]:
        return (0 if item.is_user_workspace else 1, item.name.lower())

    items.sort(key=_sort_key)
    return items


def _contains_parent_path_segment(path: str | None) -> bool:
    if not path:
        return False
    return ".." in str(path).replace("\\", "/").split("/")


def _name_matches_search_keyword(name: str, keyword: str) -> bool:
    """Substring match by default; fnmatch when query contains glob wildcards."""
    lower_name = name.lower()
    lower_keyword = keyword.lower()
    if any(ch in lower_keyword for ch in "*?"):
        return fnmatch.fnmatch(lower_name, lower_keyword)
    return lower_keyword in lower_name


def get_base_dir() -> str:
    return get_data_base_dir()


def _user_workspace_root_for_response(user_info: Dict[str, Any]) -> Optional[str]:
    return get_user_private_workspace_root(user_info)


def _validate_new_entry_basename(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="名称不能为空。")
    if cleaned in (".", ".."):
        raise HTTPException(status_code=400, detail="非法名称。")
    if cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="不能以 . 开头。")
    if "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="名称不能包含路径分隔符。")
    if len(cleaned) > 200:
        raise HTTPException(status_code=400, detail="名称过长。")
    return cleaned


@router.get("/list",
    response_model=StandardResponse[FileListResponse],
    summary="浏览服务器文件系统",
    description="普通用户仅可浏览公共目录与本人 agent_workspaces；管理员可浏览完整 data 沙箱。",
)
async def list_files(
    path: Optional[str] = Query(None, description="要浏览的绝对路径，如果不传默认返回授权根目录"),
    prefer: Optional[Literal["user_root"]] = Query(
        None,
        description="默认落点偏好：user_root 表示 path 为空时直接解析到本人私有工作区（工作空间页首屏用）。",
    ),
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    base_dir = get_base_dir()

    if _contains_parent_path_segment(path):
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。",
        )

    # 工作空间页首屏：带 prefer=user_root 且未指定 path 时，直接落本人私有工作区，
    # 不再返回「授权根汇总」。文件选择器 / 保存对话框 / 聊天抽屉都不传这个参数，
    # 所以空 path 的既有语义（虚拟根）保持不变。
    if prefer == "user_root" and is_fs_virtual_root(path, base_dir):
        preferred_root = get_user_private_workspace_root(user_info)
        if preferred_root:
            try:
                os.makedirs(preferred_root, mode=0o700, exist_ok=True)
            except OSError:
                # 建不出来就退回原有默认（管理员落 data 根，普通用户落虚拟根），不打断浏览。
                logger.warning(
                    "[fs] 无法创建用户工作区目录 %s，prefer=user_root 降级",
                    preferred_root,
                    exc_info=True,
                )
            else:
                path = preferred_root

    if is_fs_virtual_root(path, base_dir) and not is_fs_admin(user_info):
        return StandardResponse(data=FileListResponse(
            current_path=base_dir,
            parent_path=None,
            is_root=True,
            scope="user_scoped",
            items=_list_virtual_root_entries(user_info),
            writable=False,
            user_workspace_root=_user_workspace_root_for_response(user_info),
            is_virtual_root=True,
        ))

    target_path = assert_path_allowed(path or base_dir, user_info)
    private_root = get_user_private_workspace_root(user_info)
    if private_root:
        trash_path = os.path.normpath(os.path.join(private_root, TRASH_DIR_NAME))
        if os.path.normpath(target_path) == trash_path:
            os.makedirs(trash_path, exist_ok=True)
    uploads_path = get_user_uploads_dir(user_info)
    if uploads_path and os.path.normpath(target_path) == os.path.normpath(uploads_path):
        os.makedirs(uploads_path, mode=0o700, exist_ok=True)
    docs_path = get_user_docs_dir(user_info)
    if docs_path and os.path.normpath(target_path) == os.path.normpath(docs_path):
        os.makedirs(docs_path, mode=0o700, exist_ok=True)
    sessions_path = get_user_sessions_dir(user_info)
    if sessions_path and os.path.normpath(target_path) == os.path.normpath(sessions_path):
        os.makedirs(sessions_path, mode=0o700, exist_ok=True)
    if not os.path.exists(target_path) and private_root:
        if is_session_workdir_path(private_root, target_path):
            os.makedirs(target_path, mode=0o700, exist_ok=True)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="请求的路径不存在。")
    if not os.path.isdir(target_path):
        raise HTTPException(status_code=400, detail="请求的路径不是一个目录。")

    parent_path = resolve_parent_path(target_path, user_info)
    is_root = parent_path is None
    scope = "admin_all" if is_fs_admin(user_info) else "user_scoped"

    return StandardResponse(data=FileListResponse(
        current_path=target_path,
        parent_path=parent_path,
        is_root=is_root,
        scope=scope,
        items=_list_directory_entries(target_path, user_info),
        writable=is_path_writable(target_path, user_info) and not _is_any_trash_path(target_path, user_info) and not (is_fs_admin(user_info) and target_path == get_base_dir()),
        user_workspace_root=_user_workspace_root_for_response(user_info),
        is_virtual_root=False,
    ))


TEXT_PREVIEW_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".sql", ".py", ".js", ".ts",
    ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".sh", ".bash", ".xml", ".html", ".css",
    ".yaml", ".yml", ".ini", ".conf", ".toml", ".log", ".env", ".htm",
    ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".rb", ".diff", ".patch",
}

TEXT_PREVIEW_BASENAMES = frozenset({
    "dockerfile",
    "makefile",
    "readme",
    "license",
    "procfile",
    "gemfile",
    "rakefile",
})


def _is_previewable_text_file(path_or_name: str) -> bool:
    basename = os.path.basename(str(path_or_name or ""))
    lowered = basename.lower()
    if not lowered:
        return False
    if lowered in TEXT_PREVIEW_BASENAMES:
        return True
    # 「整名即扩展名」的点文件（.env / .log / .md）：os.path.splitext(".env") 返回空扩展名，
    # 而前端 getWorkspaceFileExtension 按 split(".") 认为它带 .env 扩展名。两边必须同口径，
    # 否则前端判「可编辑可预览」、后端一律 400，用户看到的编辑器一保存就报错。
    if lowered.startswith(".") and lowered.count(".") == 1:
        return lowered in TEXT_PREVIEW_EXTENSIONS
    ext = os.path.splitext(lowered)[1]
    if ext:
        return ext in TEXT_PREVIEW_EXTENSIONS
    return False

OFFICE_PREVIEW_EXTENSIONS = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
}

MAX_WRITE_BYTES = 5 * 1024 * 1024


class FileWriteRequest(BaseModel):
    path: str = Field(..., description="文件绝对路径，须在本人工作目录内")
    content: str = Field(..., description="文件文本内容")
    conversation_id: Optional[str] = Field(None, description="所属会话 ID（用于解析相对路径）")


class FileWriteResponse(BaseModel):
    path: str = Field(..., description="已写入文件的绝对路径")
    size: int = Field(..., description="写入后文件大小（字节）")
    mtime: float = Field(..., description="写入后修改时间戳")


class CopyToWorkspaceRequest(BaseModel):
    source_path: str = Field(..., description="要复制的文件绝对路径（本人可读即可，含公共只读目录）")
    target_dir: Optional[str] = Field(
        None,
        description="目标目录绝对路径，须在本人工作区内；不传则落在本人工作区根目录",
    )


class CopyToWorkspaceResponse(BaseModel):
    path: str = Field(..., description="复制后新文件的绝对路径")
    name: str = Field(..., description="复制后新文件名（重名时会带短后缀）")
    size: int = Field(..., description="文件大小（字节）")


class FileEntryCreateRequest(BaseModel):
    parent_path: str = Field(..., description="父目录绝对路径，须在本人工作目录内")
    name: str = Field(..., description="新建文件或文件夹名称")
    kind: Literal["file", "dir"] = Field("file", description="file=文件，dir=文件夹")
    content: str = Field("", description="新建文件的初始文本内容")


class FileEntryCreateResponse(BaseModel):
    path: str = Field(..., description="新建项绝对路径")
    name: str = Field(..., description="名称")
    is_dir: bool = Field(..., description="是否为目录")
    size: int = Field(..., description="大小（字节）")
    mtime: float = Field(..., description="修改时间戳")


def _resolve_fs_file_path(
    path: str,
    conversation_id: Optional[str],
    user_info: Dict[str, Any],
    *,
    workspace_root: str,
    must_exist: bool = True,
) -> str:
    from app.services.ai.runtime.agentscope.workspace import (
        resolve_legacy_session_workdir,
        resolve_session_workdir,
        resolve_user_docs_dir,
        resolve_user_workspace_root,
    )

    safe_path: str | None = None
    root = workspace_root
    raw_path = str(path or "").strip()
    user_id = user_info.get("user_id") or user_info.get("id")
    user_name = user_info.get("user_name") or user_info.get("username")

    user_workspaces_root = resolve_user_workspace_root(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )

    # 1. 优先处理 /workspace 容器沙箱逻辑路径（自动映射到当前用户的宿主机工作区）
    is_logical_workspace = (
        raw_path == "/workspace"
        or raw_path.startswith("/workspace/")
        or raw_path == "workspace"
        or raw_path.startswith("workspace/")
    )
    if is_logical_workspace and user_workspaces_root:
        if raw_path in {"/workspace", "workspace"}:
            rel_part = ""
        elif raw_path.startswith("/workspace/"):
            rel_part = raw_path[len("/workspace/"):].lstrip("/\\")
        else:
            rel_part = raw_path[len("workspace/"):].lstrip("/\\")

        # 1.1 直接在 user_workspaces_root 下匹配物理文件
        if os.path.isdir(user_workspaces_root) or not must_exist:
            candidate = normalize_under_base(rel_part, user_workspaces_root)
            if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                safe_path = candidate

        # 1.2 若未直接命中，尝试在当前会话的 session_workdir 中匹配
        if not safe_path and conversation_id and rel_part:
            session_workdir = resolve_session_workdir(
                root=root,
                user_id=user_id,
                user_name=user_name,
                user_info=user_info,
                conversation_id=conversation_id,
            )
            if os.path.isdir(session_workdir):
                candidate = normalize_under_base(rel_part, session_workdir)
                if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                    safe_path = candidate

        # 1.3 若未命中，尝试在 docs_workdir 中匹配
        if not safe_path and rel_part:
            docs_workdir = resolve_user_docs_dir(
                root=root,
                user_id=user_id,
                user_name=user_name,
                user_info=user_info,
            )
            if os.path.isdir(docs_workdir):
                candidate = normalize_under_base(rel_part, docs_workdir)
                if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                    safe_path = candidate

    if not safe_path and conversation_id and not raw_path.startswith("/") and not raw_path.startswith("/app/data"):
        docs_workdir = resolve_user_docs_dir(
            root=root,
            user_id=user_id,
            user_name=user_name,
            user_info=user_info,
        )
        if os.path.isdir(docs_workdir) or not must_exist:
            candidate = normalize_under_base(raw_path, docs_workdir)
            if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                safe_path = candidate

        if not safe_path:
            session_workdir = resolve_session_workdir(
                root=root,
                user_id=user_id,
                user_name=user_name,
                user_info=user_info,
                conversation_id=conversation_id,
            )
            if os.path.isdir(session_workdir):
                candidate = normalize_under_base(raw_path, session_workdir)
                if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                    safe_path = candidate

        if not safe_path:
            legacy_session_workdir = resolve_legacy_session_workdir(
                root=root,
                user_id=user_id,
                user_name=user_name,
                user_info=user_info,
                conversation_id=conversation_id,
            )
            if legacy_session_workdir != session_workdir and os.path.isdir(legacy_session_workdir):
                candidate = normalize_under_base(raw_path, legacy_session_workdir)
                if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                    safe_path = candidate

        if not safe_path:
            if user_workspaces_root and os.path.isdir(user_workspaces_root):
                for cid_dir in os.listdir(user_workspaces_root):
                    candidate_dir = os.path.join(user_workspaces_root, cid_dir)
                    if not os.path.isdir(candidate_dir):
                        continue
                    candidate = normalize_under_base(raw_path, candidate_dir)
                    if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                        safe_path = candidate
                        break

    if not safe_path:
        candidate = normalize_under_base(raw_path) or normalize_fs_path(raw_path)
        if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
            safe_path = candidate

    if not safe_path:
        candidate = normalize_under_base(raw_path) or normalize_fs_path(raw_path)
        if candidate and not is_path_allowed(candidate, user_info):
            raise HTTPException(
                status_code=403,
                detail="安全越权拦截：无权访问其他用户的私有目录或非授权路径。",
            )
        raise HTTPException(status_code=404, detail="文件不存在。")

    return safe_path


@router.get(
    "/preview",
    summary="预览服务器文件内容",
    description="在安全根目录内读取图片或常规文本/PDF文件内容，供 EmbedChat 画布和数据渲染使用。",
)
async def preview_file(
    path: str = Query(..., description="文件绝对路径，须在安全根目录内"),
    conversation_id: Optional[str] = Query(None, description="所属会话 ID"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root

    workspace_root = await resolve_workspace_root()
    safe_path = _resolve_fs_file_path(
        path, conversation_id, user_info, workspace_root=workspace_root, must_exist=True,
    )

    ext = os.path.splitext(safe_path)[1].lower()
    if ext in IMAGE_PREVIEW_EXTENSIONS:
        return FileResponse(
            safe_path,
            media_type=IMAGE_MEDIA_TYPES.get(ext, "application/octet-stream"),
        )
    if _is_previewable_text_file(safe_path):
        return FileResponse(
            safe_path,
            media_type="text/plain; charset=utf-8",
        )
    if ext == ".pdf":
        return FileResponse(safe_path, media_type="application/pdf")
    if ext in OFFICE_PREVIEW_EXTENSIONS:
        return FileResponse(
            safe_path,
            media_type=OFFICE_PREVIEW_EXTENSIONS[ext],
            filename=os.path.basename(safe_path),
        )
    raise HTTPException(status_code=400, detail="不支持预览该类型的文件。")


@router.get(
    "/download",
    summary="下载工作空间文件（不限文件格式）",
    description=(
        "与 /preview 共用路径归属校验，但不做扩展名白名单：zip 等二进制文件、"
        "无扩展名文件都以 attachment 原样返回。预览白名单只约束「能否在画布渲染」，"
        "不应连带剥夺用户下载自己工作区文件的能力。"
    ),
)
async def download_file(
    path: str = Query(..., description="文件绝对路径，须在安全根目录内"),
    conversation_id: Optional[str] = Query(None, description="所属会话 ID"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root

    workspace_root = await resolve_workspace_root()
    safe_path = _resolve_fs_file_path(
        path, conversation_id, user_info, workspace_root=workspace_root, must_exist=True,
    )
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=400, detail="目标不是文件，无法下载。")

    return FileResponse(
        safe_path,
        media_type="application/octet-stream",
        filename=os.path.basename(safe_path),
        content_disposition_type="attachment",
    )


@router.put(
    "/write",
    response_model=StandardResponse[FileWriteResponse],
    summary="写入工作空间文本文件",
    description="仅允许写入本人 agent_workspaces 目录内的文本类文件。",
)
async def write_file(
    body: FileWriteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root

    workspace_root = await resolve_workspace_root()
    safe_path = _resolve_fs_file_path(
        body.path,
        body.conversation_id,
        user_info,
        workspace_root=workspace_root,
        must_exist=True,
    )
    writable_path = assert_path_writable(safe_path, user_info)
    if _is_any_trash_path(writable_path, user_info):
        raise HTTPException(status_code=403, detail="回收站内的文件只读，请先还原后再编辑。")

    if not _is_previewable_text_file(writable_path):
        raise HTTPException(status_code=400, detail="不支持写入该类型的文件。")

    encoded = body.content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        raise HTTPException(status_code=400, detail="文件内容过大，超过写入上限。")

    try:
        with open(writable_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(body.content)
        stat = os.stat(writable_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {exc}") from exc

    _invalidate_usage_cache(writable_path)
    return StandardResponse(
        data=FileWriteResponse(
            path=writable_path,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
    )


@router.post(
    "/create-entry",
    response_model=StandardResponse[FileEntryCreateResponse],
    summary="在工作空间新建文件或文件夹",
    description="仅允许在本人 agent_workspaces 目录内新建。",
)
async def create_fs_entry(
    body: FileEntryCreateRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    parent_path = assert_path_allowed(body.parent_path, user_info)
    if is_fs_admin(user_info):
        parent_writable = parent_path
    else:
        parent_writable = assert_path_writable(parent_path, user_info)
    if not os.path.isdir(parent_writable):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")
    if _is_any_trash_path(parent_writable, user_info):
        raise HTTPException(status_code=403, detail="不能在回收站内新建文件或文件夹。")

    name = _validate_new_entry_basename(body.name)
    target = os.path.normpath(os.path.join(parent_writable, name))
    # Validate the final resolved target as well as the parent. This blocks a
    # dangling or existing symlink inside the workspace from redirecting a
    # create/write operation outside the user's private workspace.
    # Admin 跳过私有目录的限制，但仍需确保在 data 根目录内。
    if not is_fs_admin(user_info):
        assert_path_writable(os.path.realpath(target), user_info)
    if os.path.lexists(target):
        raise HTTPException(status_code=409, detail="同名文件或目录已存在。")

    # 新建文件同样占目录的文件个数预算（目录立不占文件预算）。
    if body.kind == "file":
        existing_files = await asyncio.to_thread(
            count_files_under, parent_writable, stop_above=MAX_FILES_PER_DIRECTORY
        )
        if existing_files + 1 > MAX_FILES_PER_DIRECTORY:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"目标目录文件数已达上限（{MAX_FILES_PER_DIRECTORY} 个），"
                    "请先清理不再需要的文件后再创建"
                ),
            )

    try:
        if body.kind == "dir":
            os.makedirs(target, exist_ok=False)
        else:
            ext = os.path.splitext(name)[1].lower()
            if ext and not _is_previewable_text_file(name):
                raise HTTPException(status_code=400, detail="不支持创建该类型的文件。")
            encoded = body.content.encode("utf-8")
            if len(encoded) > MAX_WRITE_BYTES:
                raise HTTPException(status_code=400, detail="文件内容过大，超过写入上限。")
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(body.content)
        stat = os.stat(target)
    except HTTPException:
        raise
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="同名文件或目录已存在。") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"创建失败: {exc}") from exc

    _invalidate_usage_cache(target)
    return StandardResponse(
        data=FileEntryCreateResponse(
            path=target,
            name=name,
            is_dir=body.kind == "dir",
            size=0 if body.kind == "dir" else stat.st_size,
            mtime=stat.st_mtime,
        )
    )


# 上传上限来自配置（WORKSPACE_UPLOAD_MAX_MB），默认 200MB。
DEFAULT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024
# 分块落盘大小：避免把整个文件读进内存（旧实现一次性 read 会造成与文件等量的内存峰值）。
UPLOAD_CHUNK_BYTES = 1024 * 1024
FORBIDDEN_UPLOAD_EXTENSIONS = {".exe", ".bat", ".sh", ".cmd", ".com", ".msi", ".php", ".jsp", ".asp", ".py", ".pl"}
# 公共目录（如 skills）内本身包含可读的 .py/.sh 脚本，复制进本人工作区时仅拦截高危可执行/服务端脚本扩展名
FORBIDDEN_COPY_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".msi", ".php", ".jsp", ".asp"}


def _workspace_upload_max_bytes() -> int:
    """读取上传上限。配置缺失或非法时回退默认值，保证接口始终可用。"""
    try:
        from app.core.config import settings

        megabytes = int(getattr(settings, "WORKSPACE_UPLOAD_MAX_MB", 0) or 0)
    except Exception:
        megabytes = 0
    if megabytes <= 0:
        return DEFAULT_UPLOAD_MAX_BYTES
    return megabytes * 1024 * 1024


def _format_size_limit(limit_bytes: int) -> str:
    if limit_bytes >= 1024 * 1024 * 1024:
        return f"{max(1, limit_bytes // (1024 * 1024 * 1024))}GB"
    return f"{max(1, limit_bytes // (1024 * 1024))}MB"


def _resolve_writable_dir_path(path: str, user_info: Dict[str, Any]) -> str:
    """解析目标容器目录的可写真实路径（upload / extract / move target_dir 专用）。

    必须经 realpath 完全解引用并校验真实目录可写：绝不允许工作区内的目录符号链接
    把写入/解压/移动重定向到安全根目录或其他用户目录之外。
    """
    safe_path = assert_path_allowed(path, user_info)
    if is_fs_admin(user_info):
        return safe_path
    return assert_path_writable(safe_path, user_info)


def _resolve_writable_entry_path(path: str, user_info: Dict[str, Any]) -> str:
    """解析待删/改/移源条目的可写路径。admin 可操作全部授权目录，普通用户仅限私有目录。

    若源条目本身是符号链接（含悬空软链），只校验其所在父目录的写权限并返回链接本身路径，
    避免 `realpath` 解引用导致悬空软链报 404 或误删链接指向的目标。
    注意：不得用于校验 upload/extract/move 的目标容器目录（容器目录必须走 `_resolve_writable_dir_path`）。
    """
    raw_candidate = normalize_under_base(path, get_base_dir())
    if raw_candidate and os.path.islink(raw_candidate):
        parent_dir = os.path.dirname(raw_candidate)
        safe_parent = assert_path_allowed(parent_dir, user_info)
        if not is_fs_admin(user_info):
            safe_parent = assert_path_writable(safe_parent, user_info)
        return os.path.normpath(os.path.join(safe_parent, os.path.basename(raw_candidate)))

    safe_path = assert_path_allowed(path, user_info)
    if is_fs_admin(user_info):
        return safe_path
    return assert_path_writable(safe_path, user_info)


def _parse_trashed_entry_name(trashed_name: str) -> str:
    match = re.match(r"^\d+_[a-f0-9]{8}_(.+)$", trashed_name)
    if match:
        return match.group(1)
    return trashed_name


def _resolve_restore_target_path(restore_dir: str, original_name: str) -> str:
    target = os.path.normpath(os.path.join(restore_dir, original_name))
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(original_name)
    for index in range(1, 100):
        if ext:
            candidate_name = f"{base}_restored{index}{ext}"
        else:
            candidate_name = f"{original_name}_restored{index}"
        candidate = os.path.normpath(os.path.join(restore_dir, candidate_name))
        if not os.path.exists(candidate):
            return candidate
    raise HTTPException(status_code=409, detail="恢复失败：目标位置存在过多同名文件。")


def _trash_root_for(user_info: Dict[str, Any]) -> str:
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        raise HTTPException(status_code=403, detail="无法解析用户工作目录。")
    # 与 assert_path_allowed() 的返回值（realpath）保持同一口径，否则 data 根为软链时
    # 回收站保护与归属推断会双双失配。
    return os.path.normpath(os.path.join(os.path.realpath(private_root), TRASH_DIR_NAME))


def _trash_meta_path(trash_dir: str) -> str:
    return os.path.join(trash_dir, TRASH_META_FILENAME)


def _load_trash_meta(trash_dir: str) -> Dict[str, Dict[str, Any]]:
    meta_path = _trash_meta_path(trash_dir)
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_trash_meta(trash_dir: str, meta: Dict[str, Dict[str, Any]]) -> None:
    meta_path = _trash_meta_path(trash_dir)
    tmp_path = f"{meta_path}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        os.makedirs(trash_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False)
        os.replace(tmp_path, meta_path)
    except OSError:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _record_trash_meta(trash_dir: str, trashed_name: str, original_dir: str, original_name: str) -> None:
    with _trash_meta_lock:
        meta = _load_trash_meta(trash_dir)
        meta[trashed_name] = {
            "original_dir": os.path.normpath(original_dir),
            "original_name": original_name,
            "deleted_at": time.time(),
        }
        _save_trash_meta(trash_dir, meta)


def _peek_trash_meta(trash_dir: str, trashed_name: str) -> Optional[Dict[str, Any]]:
    """只读读取一条回收站元数据（不删除记录）。

    恢复流程必须「先 peek、搬文件成功后再 pop」：此前直接在搬文件前 pop，
    一旦 shutil.move 抛错（原目录暂不可写、跨设备、重名过多触发 409），
    记录已经落盘删除，该条目就永久失去原目录信息，重试只能回落到工作区根。
    """
    with _trash_meta_lock:
        record = _load_trash_meta(trash_dir).get(trashed_name)
    return record if isinstance(record, dict) else None


def _pop_trash_meta(trash_dir: str, trashed_name: str) -> Optional[Dict[str, Any]]:
    with _trash_meta_lock:
        meta = _load_trash_meta(trash_dir)
        record = meta.pop(trashed_name, None)
        if record is not None:
            _save_trash_meta(trash_dir, meta)
        return record if isinstance(record, dict) else None


def _clear_trash_meta(trash_dir: str) -> None:
    with _trash_meta_lock:
        meta_path = _trash_meta_path(trash_dir)
        try:
            if os.path.lexists(meta_path):
                os.remove(meta_path)
        except OSError:
            pass


def _assert_not_trash_root(path: str, user_info: Dict[str, Any]) -> str:
    raw_candidate = normalize_under_base(path, get_base_dir())
    if raw_candidate and os.path.islink(raw_candidate):
        parent_dir = os.path.dirname(raw_candidate)
        safe_parent = assert_path_allowed(parent_dir, user_info)
        safe_path = os.path.normpath(os.path.join(safe_parent, os.path.basename(raw_candidate)))
    else:
        safe_path = assert_path_allowed(path, user_info)
    norm_safe = os.path.normpath(safe_path)
    if norm_safe == _trash_root_for(user_info):
        raise HTTPException(status_code=400, detail="回收站目录不可重命名或删除。")
    owner_root = _resolve_owner_workspace_root(norm_safe)
    if owner_root and norm_safe == os.path.normpath(os.path.join(owner_root, TRASH_DIR_NAME)):
        raise HTTPException(status_code=400, detail="回收站目录不可重命名或删除。")
    return safe_path


def _is_protected_root_entry(norm_path: str, user_info: Dict[str, Any]) -> bool:
    """判断路径是否为系统根目录、用户工作区根目录或根级系统保留目录。

    只拦截根级结构，不误伤用户在项目子目录下自建的同名 `docs`/`uploads` 等目录。
    """
    norm_base = os.path.normpath(get_base_dir())
    workspaces_root = os.path.normpath(os.path.join(norm_base, "agent_workspaces"))
    public_roots = {os.path.normpath(root) for root in get_public_fs_roots(norm_base)}

    if norm_path in {norm_base, workspaces_root} or norm_path in public_roots:
        return True

    parent = os.path.dirname(norm_path)
    # 任意用户的私有工作区根目录（agent_workspaces/<user_key>）本身不可删改
    if parent == workspaces_root:
        return True

    private_root = get_user_private_workspace_root(user_info)
    norm_private = os.path.normpath(private_root) if private_root else None
    if norm_private and norm_path == norm_private:
        return True

    # 根级系统保留目录不可删改：本人/他人工作区根的直接子级，以及平台 data 根的直接子级
    # （data 根下的 docs / skills 已由 public_roots 覆盖，这里补上 uploads / sessions / sandbox，
    #  它们在 data 根下是平台级目录，与前端 WORKSPACE_ROOT_PROTECTED_NAMES 保持同一口径）
    basename = os.path.basename(norm_path)
    if basename in USER_WORKSPACE_RESERVED_DIR_NAMES:
        if (
            (norm_private and parent == norm_private)
            or parent == norm_base
            or os.path.dirname(parent) == workspaces_root
        ):
            return True
    return False


def _assert_mutable_entry_path(path: str, user_info: Dict[str, Any]) -> str:
    """校验待重命名/删除/移动路径：先保留回收站根专属提示，再拦截回收站子树与系统保留目录。"""
    _assert_not_trash_root(path, user_info)
    source = _resolve_writable_entry_path(path, user_info)
    norm_source = os.path.normpath(source)
    if _is_any_trash_path(norm_source, user_info):
        raise HTTPException(status_code=400, detail="回收站内的条目不支持重命名、移动或普通删除，请使用恢复或彻底删除。")
    if _is_protected_root_entry(norm_source, user_info):
        raise HTTPException(status_code=400, detail="系统保留目录或工作区根目录不可重命名或删除。")
    return source


def _resolve_trash_entry_context(path: str, user_info: Dict[str, Any]) -> tuple[str, str, str]:
    """校验回收站条目路径，返回 (条目绝对路径, 归属工作区根目录, 归属回收站根目录)。"""
    raw_candidate = normalize_under_base(path, get_base_dir())
    if raw_candidate and os.path.islink(raw_candidate):
        safe_parent = assert_path_allowed(os.path.dirname(raw_candidate), user_info)
        safe_path = os.path.normpath(os.path.join(safe_parent, os.path.basename(raw_candidate)))
    else:
        safe_path = assert_path_allowed(path, user_info)
    norm_safe = os.path.normpath(safe_path)
    if _is_trash_path(norm_safe, user_info):
        private_root = get_user_private_workspace_root(user_info)
        if not private_root:
            raise HTTPException(status_code=403, detail="无法解析用户工作目录。")
        trash_root = _trash_root_for(user_info)
        if norm_safe == trash_root:
            raise HTTPException(status_code=400, detail="不能对回收站目录本身执行该操作。")
        return norm_safe, os.path.normpath(os.path.realpath(private_root)), trash_root

    # 管理员可操作其他用户工作区回收站内的条目，还原也落回原用户工作区
    if is_fs_admin(user_info):
        owner_root = _resolve_owner_workspace_root(norm_safe)
        if owner_root:
            owner_trash = os.path.normpath(os.path.join(owner_root, TRASH_DIR_NAME))
            if norm_safe == owner_trash:
                raise HTTPException(status_code=400, detail="不能对回收站目录本身执行该操作。")
            if norm_safe.startswith(owner_trash + os.sep):
                return norm_safe, owner_root, owner_trash

    raise HTTPException(status_code=403, detail="只能操作回收站内的文件或文件夹。")


def _assert_trash_entry_path(path: str, user_info: Dict[str, Any]) -> str:
    source, _, _ = _resolve_trash_entry_context(path, user_info)
    return source


def _trash_dir_for(user_info: Dict[str, Any], source_path: Optional[str] = None) -> str:
    """定位删除时应落入的回收站目录：管理员删他人文件时落回原用户工作区回收站。"""
    if source_path and is_fs_admin(user_info):
        owner_root = _resolve_owner_workspace_root(source_path)
        if owner_root:
            trash = os.path.normpath(os.path.join(owner_root, TRASH_DIR_NAME))
            os.makedirs(trash, exist_ok=True)
            return trash
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        raise HTTPException(status_code=403, detail="无法解析用户工作目录。")
    trash = os.path.join(os.path.realpath(private_root), TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    return trash


def _resolve_trash_dir_to_empty(path: Optional[str], user_info: Dict[str, Any]) -> str:
    """定位「清空回收站」的目标目录。

    `delete-entry` 已支持 admin 把他人文件落回原用户 `.trash`、`restore-entry` 支持 admin
    还原他人回收站条目；清空也必须按归属定位，否则管理员在他人回收站视图点「清空回收站」
    实际清掉的是自己的回收站，而他人文件原封不动。
    """
    if not path:
        return _trash_dir_for(user_info)
    safe_path = assert_path_allowed(path, user_info)
    norm_safe = os.path.normpath(safe_path)
    if _is_trash_path(norm_safe, user_info):
        return _trash_root_for(user_info)
    if is_fs_admin(user_info):
        owner_root = _resolve_owner_workspace_root(norm_safe)
        if owner_root:
            owner_trash = os.path.normpath(os.path.join(owner_root, TRASH_DIR_NAME))
            if norm_safe == owner_trash or norm_safe.startswith(owner_trash + os.sep):
                return owner_trash
    raise HTTPException(status_code=403, detail="只能清空有权访问的回收站目录。")


class FileRenameRequest(BaseModel):
    path: str = Field(..., description="待重命名项绝对路径")
    new_name: str = Field(..., description="新名称（不含路径）")


class FileDeleteRequest(BaseModel):
    path: str = Field(..., description="待删除项绝对路径")


class FileRenameResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    mtime: float


class FileDeleteResponse(BaseModel):
    path: str
    trashed_path: str


class FileUploadResponse(BaseModel):
    path: str
    # 用户选择的原始文件名
    name: str
    # 实际落盘名（默认等于 name，仅在重名时追加短后缀）：UI 据此解释「为什么名字变了」
    stored_name: Optional[str] = None
    size: int
    mtime: float


@router.post(
    "/rename-entry",
    response_model=StandardResponse[FileRenameResponse],
    summary="重命名工作空间文件或文件夹",
)
async def rename_fs_entry(
    body: FileRenameRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source = _assert_mutable_entry_path(body.path, user_info)
    if not os.path.lexists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    new_name = _validate_new_entry_basename(body.new_name)
    target = os.path.normpath(os.path.join(os.path.dirname(source), new_name))
    if os.path.lexists(target):
        raise HTTPException(status_code=409, detail="目标名称已存在。")
    try:
        os.rename(source, target)
        stat = os.lstat(target) if os.path.islink(target) else os.stat(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"重命名失败: {exc}") from exc
    return StandardResponse(
        data=FileRenameResponse(
            path=target,
            name=new_name,
            is_dir=os.path.isdir(target),
            mtime=stat.st_mtime,
        )
    )


def _remove_path_sync(path: str) -> None:
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _move_entry_exclusive_sync(src: str, dest_dir: str, basename: str) -> str:
    """把条目移入目标目录，且绝不覆盖同名条目。

    `os.path.lexists()` 检查与 `shutil.move` 之间存在竞态窗口（并发请求、或 AI agent 同时在
    同一工作区写同名文件），POSIX 的 `os.rename` 对已存在目标会直接替换，导致用户文件被
    静默销毁且接口返回 200。这里用 O_CREAT|O_EXCL 占位 + `os.replace` 把「检查 + 移动」
    收进同一次原子操作：
    - 文件/软链：先排他占位，再原子替换占位文件（跨设备 EXDEV 时回退 shutil.move）；
    - 目录：`os.rename` 到已存在目录会失败（EEXIST/ENOTEMPTY），本身不会覆盖，无需占位。
    """
    target = os.path.normpath(os.path.join(dest_dir, basename))
    is_real_dir = os.path.isdir(src) and not os.path.islink(src)
    if is_real_dir:
        if os.path.lexists(target):
            raise FileExistsError(errno.EEXIST, "destination exists", target)
        os.rename(src, target)
        return target

    placeholder_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(placeholder_fd)
    try:
        os.replace(src, target)
    except OSError as exc:
        try:
            os.remove(target)
        except OSError:
            pass
        if exc.errno != errno.EXDEV:
            raise
        # 极少数跨文件系统场景（data 卷内不同挂载点）：回退 shutil.move，占位文件已撤销
        shutil.move(src, target)
    return target


@router.post(
    "/delete-entry",
    response_model=StandardResponse[FileDeleteResponse],
    summary="删除工作空间文件或文件夹（移入回收站）",
)
async def delete_fs_entry(
    body: FileDeleteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source = _assert_mutable_entry_path(body.path, user_info)
    if not os.path.lexists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")

    # admin 删除公共目录内容时直接永久删除（公共目录不属于任何人的回收站）
    if is_fs_admin(user_info) and is_public_fs_path(source):
        try:
            await run_in_threadpool(_remove_path_sync, source)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
        _invalidate_usage_cache(source)
        return StandardResponse(
            data=FileDeleteResponse(path=source, trashed_path="")
        )

    basename = os.path.basename(source)
    original_dir = os.path.dirname(source)
    trash_dir = _trash_dir_for(user_info, source_path=source)
    trashed_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{basename}"
    trashed_path = os.path.normpath(os.path.join(trash_dir, trashed_name))
    try:
        await run_in_threadpool(shutil.move, source, trashed_path)
        await run_in_threadpool(_record_trash_meta, trash_dir, trashed_name, original_dir, basename)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
    _invalidate_usage_cache(source)
    return StandardResponse(
        data=FileDeleteResponse(path=source, trashed_path=trashed_path)
    )


class FileRestoreResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    mtime: float
    restored_to_original_dir: bool = False
    original_name: Optional[str] = None


class FilePurgeResponse(BaseModel):
    path: str


class FileEmptyTrashResponse(BaseModel):
    deleted_count: int


@router.post(
    "/restore-entry",
    response_model=StandardResponse[FileRestoreResponse],
    summary="从回收站恢复文件或文件夹",
    description="优先恢复至删除前的原目录，原目录不存在时恢复至所属工作区根目录；重名自动追加 _restored 后缀。",
)
async def restore_fs_entry(
    body: FileDeleteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source, owner_root, trash_root = _resolve_trash_entry_context(body.path, user_info)
    if not os.path.lexists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")

    trashed_basename = os.path.basename(source)
    is_direct_trash_child = os.path.normpath(os.path.dirname(source)) == trash_root
    # 只读读取元数据：必须等 shutil.move 成功后才 pop，否则一次瞬时失败就会永久丢掉原目录信息
    meta = await run_in_threadpool(_peek_trash_meta, trash_root, trashed_basename) if is_direct_trash_child else None
    original_name = (
        str(meta.get("original_name") or "").strip() if meta else ""
    ) or _parse_trashed_entry_name(trashed_basename)
    # 元数据里的原名必须是单段文件名：库内记录虽由服务端写入，仍按不可信输入校验，
    # 避免记录被外部改写后借 `../` 把恢复目标挪出工作区。
    if os.path.basename(original_name) != original_name:
        original_name = _parse_trashed_entry_name(trashed_basename)

    restore_dir = owner_root
    restored_to_original_dir = False
    if meta and meta.get("original_dir"):
        candidate_dir = os.path.normpath(os.path.realpath(str(meta["original_dir"])))
        is_under_owner = candidate_dir == owner_root or candidate_dir.startswith(owner_root + os.sep)
        if is_under_owner and not _is_any_trash_path(candidate_dir, user_info) and os.path.isdir(candidate_dir):
            restore_dir = candidate_dir
            restored_to_original_dir = True

    target = _resolve_restore_target_path(restore_dir, original_name)
    try:
        await run_in_threadpool(shutil.move, source, target)
        stat = os.lstat(target) if os.path.islink(target) else os.stat(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"恢复失败: {exc}") from exc
    if is_direct_trash_child:
        # 搬移成功后再提交元数据删除：失败路径保留记录，用户重试仍能回到原目录
        await run_in_threadpool(_pop_trash_meta, trash_root, trashed_basename)
    _invalidate_usage_cache(owner_root)
    return StandardResponse(
        data=FileRestoreResponse(
            path=target,
            name=os.path.basename(target),
            is_dir=os.path.isdir(target),
            mtime=stat.st_mtime,
            restored_to_original_dir=restored_to_original_dir,
            original_name=original_name,
        )
    )


@router.post(
    "/purge-entry",
    response_model=StandardResponse[FilePurgeResponse],
    summary="永久删除回收站内的文件或文件夹",
)
async def purge_fs_entry(
    body: FileDeleteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source, owner_root, trash_root = _resolve_trash_entry_context(body.path, user_info)
    if not os.path.lexists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    try:
        await run_in_threadpool(_remove_path_sync, source)
        if os.path.normpath(os.path.dirname(source)) == trash_root:
            await run_in_threadpool(_pop_trash_meta, trash_root, os.path.basename(source))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"永久删除失败: {exc}") from exc
    _invalidate_usage_cache(owner_root)
    return StandardResponse(data=FilePurgeResponse(path=source))


def _empty_trash_sync(trash_dir: str) -> int:
    deleted_count = 0
    for name in os.listdir(trash_dir):
        if name.startswith("."):
            continue
        entry = os.path.join(trash_dir, name)
        if os.path.islink(entry):
            os.unlink(entry)
        elif os.path.isdir(entry):
            shutil.rmtree(entry)
        elif os.path.isfile(entry):
            os.remove(entry)
        else:
            continue
        deleted_count += 1
    _clear_trash_meta(trash_dir)
    return deleted_count


@router.post(
    "/empty-trash",
    response_model=StandardResponse[FileEmptyTrashResponse],
    summary="清空回收站（永久删除全部回收项）",
    description="缺省清空当前用户自己的回收站；显式传 path 时按该路径归属（含管理员浏览他人工作区）清空对应回收站。",
)
async def empty_trash(
    path: Annotated[
        Optional[str],
        Query(description="目标回收站目录（回收站根或其子树）；缺省为当前用户自己的回收站"),
    ] = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    trash_dir = _resolve_trash_dir_to_empty(path, user_info)
    try:
        deleted_count = await run_in_threadpool(_empty_trash_sync, trash_dir)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"清空回收站失败: {exc}") from exc
    _invalidate_usage_cache(trash_dir)
    return StandardResponse(data=FileEmptyTrashResponse(deleted_count=deleted_count))


@router.post(
    "/copy-to-workspace",
    response_model=StandardResponse[CopyToWorkspaceResponse],
    summary="把公共只读文件复制到本人工作区",
    description=(
        "公共目录（branding / skills / docs 等）只读，分享只支持本人工作区内的文件。"
        "本接口把公共文件复制一份到本人工作区，让「复制后分享」成为一步可达的操作，"
        "而不是逼用户先下载再上传。源文件不受影响，复制得到独立副本。"
    ),
)
async def copy_to_workspace(
    body: CopyToWorkspaceRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    import shutil

    source = assert_path_allowed(body.source_path, user_info)
    # 先单独判存在：assert_path_allowed 只校验前缀归属，不校验存在性，
    # 否则不存在的路径会一路走到下面的 isfile 判断，被误报成「不能复制目录」。
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="源文件不存在。")
    # 只处理文件：目录复制涉及递归遍历与体积预算，不在这个「复制后分享」的场景里
    if not os.path.isfile(source):
        raise HTTPException(status_code=400, detail="只能复制文件，不能复制目录。")

    # 目标只能落在**本人**私有工作区内——这是本接口的语义（「复制到我的工作区」）。
    # 不能复用 _resolve_writable_entry_path：它对 admin 直接放行，会允许管理员把文件
    # 复制进公共目录，那就和「公共目录只读」自相矛盾了。
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        raise HTTPException(status_code=403, detail="当前账号没有私有工作区。")
    private_norm = os.path.normpath(private_root)
    if body.target_dir:
        target_dir = os.path.normpath(assert_path_allowed(body.target_dir, user_info))
        if target_dir != private_norm and not target_dir.startswith(private_norm + os.sep):
            raise HTTPException(status_code=403, detail="只能复制到本人工作区内。")
        target_dir = assert_path_writable(target_dir, user_info)
    else:
        target_dir = assert_path_writable(private_root, user_info)

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")
    if _is_any_trash_path(target_dir, user_info):
        raise HTTPException(status_code=403, detail="不能复制到回收站内。")

    size = os.path.getsize(source)
    if size > _workspace_upload_max_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超出 {_format_size_limit(_workspace_upload_max_bytes())} 上限",
        )

    existing_files = await asyncio.to_thread(
        count_files_under, target_dir, stop_above=MAX_FILES_PER_DIRECTORY
    )
    if existing_files + 1 > MAX_FILES_PER_DIRECTORY:
        raise HTTPException(
            status_code=413,
            detail=(
                f"目标目录文件数已达上限（{MAX_FILES_PER_DIRECTORY} 个），"
                "请先清理不再需要的文件后再复制"
            ),
        )

    source_name = os.path.basename(source)
    ext = os.path.splitext(source_name)[1].lower()
    if ext in FORBIDDEN_COPY_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"禁止复制该类型文件: {ext}")

    # 复用上传的命名分配：原名优先、重名追加短后缀、排他创建保证不覆盖已有文件
    def _copy_file_sync() -> tuple[str, int]:
        target, handle = open_upload_storage_file(target_dir, source_name)
        try:
            with handle:
                # 分块拷贝：公共目录里可能有几十 MB 的手册，不该整个读进内存
                with open(source, "rb") as src_handle:
                    shutil.copyfileobj(src_handle, handle, length=UPLOAD_CHUNK_BYTES)
            return target, os.stat(target).st_size
        except OSError:
            # 半途失败要清掉残留，否则用户会看到 0 字节的「副本」
            try:
                os.remove(target)
            except OSError:
                pass
            raise

    try:
        target, copied_size = await run_in_threadpool(_copy_file_sync)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"复制失败: {exc}") from exc

    _invalidate_usage_cache(target)
    return StandardResponse(
        data=CopyToWorkspaceResponse(
            path=target,
            name=os.path.basename(target),
            size=copied_size,
        )
    )


@router.post(
    "/upload",
    response_model=StandardResponse[FileUploadResponse],
    summary="上传文件到工作空间指定目录",
)
async def upload_to_workspace(
    parent_path: str = Query(..., description="目标目录绝对路径"),
    file: UploadFile = File(...),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    parent = _resolve_writable_dir_path(parent_path, user_info)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")
    if _is_any_trash_path(parent, user_info):
        raise HTTPException(status_code=403, detail="不能上传文件到回收站。")

    # 扩展名校验必须早于任何写盘动作，避免「先落盘再拒绝」留下垃圾文件
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in FORBIDDEN_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"禁止上传该类型文件: {ext}")

    max_bytes = _workspace_upload_max_bytes()
    original_name = os.path.basename(file.filename or "") or "upload"

    # 文件个数按「目录累计」判：单文件上传没有次数限制，不判累计就等于没有上限
    # （循环上传一万个小文件，体积上限一次都不会触发，但 inode 会被吃光）。
    existing_files = await asyncio.to_thread(
        count_files_under, parent, stop_above=MAX_FILES_PER_DIRECTORY
    )
    if existing_files + 1 > MAX_FILES_PER_DIRECTORY:
        raise HTTPException(
            status_code=413,
            detail=(
                f"目标目录文件数已达上限（{MAX_FILES_PER_DIRECTORY} 个），"
                "请先清理不再需要的文件后再上传"
            ),
        )

    try:
        target, handle = await asyncio.to_thread(open_upload_storage_file, parent, original_name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc

    stored_name = os.path.basename(target)
    written = 0
    try:
        # 分块流式落盘：内存占用与文件大小无关，写盘放线程池避免大文件阻塞事件循环
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件大小超出 {_format_size_limit(max_bytes)} 上限",
                )
            await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.flush)
    except HTTPException:
        # 超限即中止，并且不留下半截文件
        if not handle.closed:
            await asyncio.to_thread(handle.close)
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    except OSError as exc:
        if not handle.closed:
            await asyncio.to_thread(handle.close)
        try:
            os.remove(target)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc
    finally:
        if not handle.closed:
            await asyncio.to_thread(handle.close)

    stat = os.stat(target)
    _invalidate_usage_cache(target)
    return StandardResponse(
        data=FileUploadResponse(
            path=target,
            name=original_name,
            stored_name=stored_name,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
    )


class FileArchiveExtractRequest(BaseModel):
    archive_path: str = Field(..., description="压缩包绝对路径（只需可读）")
    target_dir: str = Field(..., description="解压目标目录绝对路径（必须可写）")
    subdir: Optional[str] = Field(
        None,
        description="目标目录下的子文件夹名；缺省取压缩包名去后缀，空串表示直接解到目标目录",
    )


class FileArchiveExtractResponse(BaseModel):
    path: str
    file_count: int
    total_bytes: int
    skipped: List[Dict[str, str]] = Field(default_factory=list)


@router.post(
    "/extract",
    response_model=StandardResponse[FileArchiveExtractResponse],
    summary="解压工作空间内的压缩包",
    description=(
        "支持 zip / tar / tar.gz / tgz。源压缩包只需可读（公共只读目录也可），"
        "目标目录必须可写；路径穿越、符号链接与压缩炸弹在服务端拦截，失败会回滚。"
    ),
)
async def extract_fs_archive(
    body: FileArchiveExtractRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from starlette.concurrency import run_in_threadpool

    from app.services.workspace_archive import (
        ArchiveExtractError,
        extract_archive,
        extract_limits_from_settings,
    )

    source = assert_path_allowed(body.archive_path, user_info)
    if not os.path.isfile(source):
        raise HTTPException(status_code=404, detail="压缩包不存在或不是文件。")

    target_dir = _resolve_writable_dir_path(body.target_dir, user_info)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")
    if _is_any_trash_path(target_dir, user_info):
        raise HTTPException(status_code=403, detail="不能解压到回收站内。")
    # 与 create-entry 一致：工作区内的符号链接不能把写入引到授权目录之外
    if not is_fs_admin(user_info):
        assert_path_writable(os.path.realpath(target_dir), user_info)

    max_total_bytes, max_entries = extract_limits_from_settings()
    try:
        # 解压是同步 IO：丢线程池，避免大包把事件循环堵住
        result = await run_in_threadpool(
            extract_archive,
            source,
            os.path.realpath(target_dir),
            body.subdir,
            max_total_bytes=max_total_bytes,
            max_entries=max_entries,
        )
    except ArchiveExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _invalidate_usage_cache(target_dir)
    return StandardResponse(
        data=FileArchiveExtractResponse(
            path=result.path,
            file_count=result.file_count,
            total_bytes=result.total_bytes,
            skipped=result.skipped,
        )
    )


@router.get(
    "/search",
    response_model=StandardResponse[FileSearchResponse],
    summary="搜索服务器文件",
    description="在授权目录及其子目录中按文件名模糊搜索。",
)
async def search_files(
    q: str = Query(..., min_length=1, max_length=100, description="搜索关键词"),
    path: Optional[str] = Query(None, description="搜索起始目录，默认在全部授权目录内搜索"),
    max_results: int = Query(80, ge=1, le=200, description="最大返回条数"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    keyword = q.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="搜索关键词不能为空。")

    base_dir = get_base_dir()
    # 非管理员在虚拟根（data 根）下搜索时，应遍历全部授权目录而非 data 根本身
    if path and is_fs_virtual_root(path, base_dir) and not is_fs_admin(user_info):
        path = None

    if path:
        search_roots = [assert_path_allowed(os.path.abspath(path), user_info)]
        search_root_label = search_roots[0]
    elif is_fs_admin(user_info):
        search_roots = [get_base_dir()]
        search_root_label = search_roots[0]
    else:
        search_roots = [root for root in get_allowed_fs_roots(user_info) if os.path.isdir(root)]
        search_root_label = get_base_dir()

    if not search_roots:
        raise HTTPException(status_code=404, detail="没有可搜索的授权目录。")

    def _search_files_sync() -> tuple[List[FileItem], bool]:
        found: List[FileItem] = []
        is_truncated = False
        scanned_nodes = 0

        for search_root in search_roots:
            for root, dirs, files in os.walk(search_root):
                norm_root = os.path.normpath(root)
                if not is_path_allowed(norm_root, user_info):
                    dirs[:] = []
                    continue

                dirs[:] = sorted(
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in SEARCH_SKIP_DIR_NAMES
                    and not is_forbidden_platform_skill_path(os.path.join(root, d), user_info)
                )

                for name in dirs:
                    scanned_nodes += 1
                    if scanned_nodes > SEARCH_MAX_SCANNED_NODES:
                        is_truncated = True
                        break
                    if _name_matches_search_keyword(name, keyword):
                        _append_fs_entry(found, os.path.join(root, name), True, user_info=user_info)
                        if len(found) >= max_results:
                            is_truncated = True
                            break
                if is_truncated:
                    break

                for name in sorted(files):
                    if name.startswith("."):
                        continue
                    scanned_nodes += 1
                    if scanned_nodes > SEARCH_MAX_SCANNED_NODES:
                        is_truncated = True
                        break
                    file_path = os.path.join(root, name)
                    if is_forbidden_platform_skill_path(file_path, user_info):
                        continue
                    if _name_matches_search_keyword(name, keyword):
                        _append_fs_entry(found, file_path, False, user_info=user_info)
                        if len(found) >= max_results:
                            is_truncated = True
                            break
                if is_truncated:
                    break
            if is_truncated:
                break

        found.sort(key=lambda x: (not x.is_dir, x.name.lower()))
        return found, is_truncated

    results, truncated = await run_in_threadpool(_search_files_sync)

    return StandardResponse(
        data=FileSearchResponse(
            items=results,
            query=q.strip(),
            search_root=search_root_label,
            truncated=truncated,
        )
    )


class WorkspaceRecentFileItem(BaseModel):
    path: str = Field(..., description="文件绝对路径")
    name: str = Field(..., description="展示用文件名")
    mtime: float = Field(..., description="最后访问时间戳（秒）")


class WorkspaceRecentFilesPayload(BaseModel):
    items: List[WorkspaceRecentFileItem] = Field(default_factory=list)


class WorkspaceRecentFilesResponse(BaseModel):
    items: List[WorkspaceRecentFileItem] = Field(default_factory=list)


def _workspace_recent_redis_key(user_id: int) -> str:
    return f"{WORKSPACE_RECENT_REDIS_PREFIX}{user_id}"


def _is_trash_path_segment(path: str) -> bool:
    norm = os.path.normpath(path)
    return f"/{TRASH_DIR_NAME}/" in f"{norm}/" or norm.endswith(f"/{TRASH_DIR_NAME}")


def _sanitize_workspace_recent_files(
    items: List[WorkspaceRecentFileItem],
    user_info: Dict[str, Any],
) -> List[WorkspaceRecentFileItem]:
    seen: set[str] = set()
    cleaned: List[WorkspaceRecentFileItem] = []
    for raw in items:
        path = str(raw.path or "").strip()
        if not path or _is_trash_path_segment(path):
            continue
        normalized = normalize_fs_path(path)
        if not normalized or not is_path_allowed(normalized, user_info):
            continue
        if not os.path.isfile(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        name = str(raw.name or os.path.basename(normalized) or normalized).strip()
        if not name:
            name = os.path.basename(normalized)
        if len(name) > 255:
            name = name[:255]
        try:
            mtime = float(raw.mtime)
        except (TypeError, ValueError):
            mtime = time.time()
        if mtime <= 0:
            mtime = time.time()
        cleaned.append(WorkspaceRecentFileItem(path=normalized, name=name, mtime=mtime))
        if len(cleaned) >= WORKSPACE_RECENT_FILES_MAX:
            break
    return cleaned


@router.get(
    "/recent-files",
    response_model=StandardResponse[WorkspaceRecentFilesResponse],
    summary="获取当前用户的工作空间最近访问文件",
)
async def get_workspace_recent_files(
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        return StandardResponse(data=WorkspaceRecentFilesResponse(items=[]))

    user_id = int(user_info["user_id"])
    key = _workspace_recent_redis_key(user_id)
    try:
        raw = await redis.get(key)
    except Exception as e:
        logger.error("Failed to get workspace recent files from Redis: %s", e)
        return StandardResponse(data=WorkspaceRecentFilesResponse(items=[]))

    if not raw:
        return StandardResponse(data=WorkspaceRecentFilesResponse(items=[]))

    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        parsed = json.loads(decoded)
        raw_items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(raw_items, list):
            raw_items = []
        items = _sanitize_workspace_recent_files(
            [WorkspaceRecentFileItem.model_validate(item) for item in raw_items],
            user_info,
        )
    except Exception as e:
        logger.warning("Failed to parse workspace recent files JSON: %s", e)
        items = []

    return StandardResponse(data=WorkspaceRecentFilesResponse(items=items))


@router.put(
    "/recent-files",
    response_model=StandardResponse[WorkspaceRecentFilesResponse],
    summary="保存当前用户的工作空间最近访问文件",
)
async def save_workspace_recent_files(
    body: WorkspaceRecentFilesPayload,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis 服务不可用")

    user_id = int(user_info["user_id"])
    key = _workspace_recent_redis_key(user_id)
    items = _sanitize_workspace_recent_files(body.items, user_info)
    payload = WorkspaceRecentFilesResponse(items=items)

    try:
        await redis.set(key, payload.model_dump_json())
    except Exception as e:
        logger.error("Failed to save workspace recent files to Redis: %s", e)
        raise HTTPException(status_code=500, detail="保存最近文件记录失败")

    return StandardResponse(data=payload, message="最近文件记录已保存")


class WorkspaceBrowserPrefs(BaseModel):
    include_subdirs: bool = Field(True, description="搜索时是否包含子目录")
    type_filter: str = Field("all", description="文件类型筛选")


class WorkspaceBrowserPrefsPayload(BaseModel):
    include_subdirs: Optional[bool] = Field(None, description="搜索时是否包含子目录")
    type_filter: Optional[str] = Field(None, description="文件类型筛选")


def _workspace_browser_prefs_redis_key(user_id: int) -> str:
    return f"{WORKSPACE_BROWSER_PREFS_REDIS_PREFIX}{user_id}"


def _sanitize_workspace_browser_prefs(
    raw: WorkspaceBrowserPrefsPayload | WorkspaceBrowserPrefs | Dict[str, Any] | None,
) -> WorkspaceBrowserPrefs:
    include_subdirs = True
    type_filter = "all"
    if raw is not None:
        if isinstance(raw, dict):
            include_subdirs = raw.get("include_subdirs", True)
            type_filter = raw.get("type_filter", "all")
        else:
            include_subdirs = getattr(raw, "include_subdirs", True)
            type_filter = getattr(raw, "type_filter", "all")
    if not isinstance(include_subdirs, bool):
        include_subdirs = True
    type_filter = str(type_filter or "all").strip().lower()
    if type_filter not in WORKSPACE_BROWSER_TYPE_FILTERS:
        type_filter = "all"
    return WorkspaceBrowserPrefs(include_subdirs=include_subdirs, type_filter=type_filter)


@router.get(
    "/browser-prefs",
    response_model=StandardResponse[WorkspaceBrowserPrefs],
    summary="获取当前用户的工作空间浏览偏好",
)
async def get_workspace_browser_prefs(
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        return StandardResponse(data=WorkspaceBrowserPrefs())

    user_id = int(user_info["user_id"])
    key = _workspace_browser_prefs_redis_key(user_id)
    try:
        raw = await redis.get(key)
    except Exception as e:
        logger.error("Failed to get workspace browser prefs from Redis: %s", e)
        return StandardResponse(data=WorkspaceBrowserPrefs())

    if not raw:
        return StandardResponse(data=WorkspaceBrowserPrefs())

    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        parsed = json.loads(decoded)
        prefs = _sanitize_workspace_browser_prefs(parsed)
    except Exception as e:
        logger.warning("Failed to parse workspace browser prefs JSON: %s", e)
        prefs = WorkspaceBrowserPrefs()

    return StandardResponse(data=prefs)


@router.put(
    "/browser-prefs",
    response_model=StandardResponse[WorkspaceBrowserPrefs],
    summary="保存当前用户的工作空间浏览偏好",
)
async def save_workspace_browser_prefs(
    body: WorkspaceBrowserPrefsPayload,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis 服务不可用")

    user_id = int(user_info["user_id"])
    key = _workspace_browser_prefs_redis_key(user_id)

    existing = WorkspaceBrowserPrefs()
    try:
        raw = await redis.get(key)
        if raw:
            decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            existing = _sanitize_workspace_browser_prefs(json.loads(decoded))
    except Exception as e:
        logger.warning("Failed to read existing workspace browser prefs: %s", e)

    merged = WorkspaceBrowserPrefs(
        include_subdirs=body.include_subdirs if body.include_subdirs is not None else existing.include_subdirs,
        type_filter=body.type_filter if body.type_filter is not None else existing.type_filter,
    )
    prefs = _sanitize_workspace_browser_prefs(merged)

    try:
        await redis.set(key, prefs.model_dump_json())
    except Exception as e:
        logger.error("Failed to save workspace browser prefs to Redis: %s", e)
        raise HTTPException(status_code=500, detail="保存浏览偏好失败")

    return StandardResponse(data=prefs, message="浏览偏好已保存")


class FileMoveRequest(BaseModel):
    path: str = Field(..., description="待移动的文件或目录绝对路径")
    target_dir: str = Field(..., description="目标父目录绝对路径")


class FileMoveResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    mtime: float


@router.post(
    "/move-entry",
    response_model=StandardResponse[FileMoveResponse],
    summary="移动工作空间文件或文件夹到指定目录",
)
async def move_fs_entry(
    body: FileMoveRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source = _assert_mutable_entry_path(body.path, user_info)
    if not os.path.lexists(source):
        raise HTTPException(status_code=404, detail="源文件或目录不存在。")
    # 公共目录是平台只读资源：admin 删除它们是「永久删除」，但移动等于把公共资源搬进私有工作区，
    # 前端 canMoveItem 已排除 public，后端必须同口径拦住。
    if is_public_fs_path(source):
        raise HTTPException(status_code=403, detail="公共目录为只读资源，不支持移动；请使用「复制到我的目录」。")

    target_dir = _resolve_writable_dir_path(body.target_dir, user_info)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")
    if _is_any_trash_path(target_dir, user_info):
        raise HTTPException(status_code=403, detail="不能移动到回收站内，请使用删除操作。")
    if not is_fs_admin(user_info):
        assert_path_writable(os.path.realpath(target_dir), user_info)

    norm_source = os.path.normpath(source)
    norm_target_dir = os.path.normpath(target_dir)
    if os.path.dirname(norm_source) == norm_target_dir:
        raise HTTPException(status_code=400, detail="目标目录与当前所在目录相同。")
    if os.path.isdir(norm_source) and (
        norm_target_dir == norm_source or norm_target_dir.startswith(norm_source + os.sep)
    ):
        raise HTTPException(status_code=400, detail="不能将目录移动到自身或其子目录中。")

    basename = os.path.basename(norm_source)
    destination = os.path.normpath(os.path.join(norm_target_dir, basename))
    if os.path.lexists(destination):
        raise HTTPException(status_code=409, detail=f"目标目录已存在同名项：{basename}")

    try:
        destination = await run_in_threadpool(_move_entry_exclusive_sync, norm_source, norm_target_dir, basename)
        stat = os.lstat(destination) if os.path.islink(destination) else os.stat(destination)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"目标目录已存在同名项：{basename}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"移动失败: {exc}") from exc

    _invalidate_usage_cache(norm_source)
    _invalidate_usage_cache(destination)
    return StandardResponse(
        data=FileMoveResponse(
            path=destination,
            name=basename,
            is_dir=os.path.isdir(destination),
            mtime=stat.st_mtime,
        )
    )


class BatchDownloadRequest(BaseModel):
    paths: List[str] = Field(..., min_length=1, max_length=200, description="要打包下载的文件或目录路径列表")
    archive_name: Optional[str] = Field(None, max_length=120, description="可选的压缩包显示文件名")


def _cleanup_temp_file(path: str) -> None:
    try:
        if path and os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _assert_no_symlink_escape(path: str, user_info: Dict[str, Any]) -> str:
    """校验路径解引用（realpath）后仍落在授权范围内，拒绝软链把读取引向沙箱之外。

    普通用户在工作区里放一个指向 `.env`、宿主卷或其他挂载点的软链时，`assert_path_allowed`
    会命中「逻辑挂载路径」分支、放行**未解引用**的路径；随后 `os.path.isfile` / `open` /
    `zf.write` 都会跟随软链读到 `data/` 之外的内容。这里再做一次真实路径归属校验。

    平台官方帮助文档（服务根下的直连 .md，公共 docs 里以软链形式暴露）按既有口径放行，
    平台技能根（skills）也属公共可读资源。
    """
    if not path:
        raise HTTPException(status_code=400, detail="非法路径。")
    if is_public_runtime_help_path(path):
        return path
    real_path = os.path.realpath(path)
    scope_roots = [os.path.realpath(root) for root in get_allowed_fs_roots(user_info)]
    skills_root = get_platform_skills_root()
    if skills_root:
        scope_roots.append(os.path.realpath(skills_root))
    if not any(real_path == root or real_path.startswith(root + os.sep) for root in scope_roots):
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：符号链接指向授权范围之外，已拒绝访问。",
        )
    return path


class _CleanupFileResponse(FileResponse):
    """下载结束（正常 / 416 / 客户端断连）后必定清理临时文件。

    starlette 的 `FileResponse` 只会在正常发完 body 后执行 `background`：Range 不可满足时
    提前 `return` 416、客户端中途断开时 `send` 抛异常，这两条路径都不会执行 background，
    `/tmp/workspace_dl_*.zip` 会永久残留（单次畸形 Range 就能钉住一个最大 500MB 的包）。
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        try:
            await super().__call__(scope, receive, send)
        finally:
            _cleanup_temp_file(self.path)


@router.post(
    "/batch-download",
    summary="打包下载多个文件或目录（ZIP）",
)
async def batch_download_files(
    body: BatchDownloadRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    resolved_sources: List[str] = []
    seen_sources: set[str] = set()
    for raw_path in body.paths:
        safe = assert_path_allowed(raw_path, user_info)
        safe = _assert_no_symlink_escape(safe, user_info)
        norm_safe = os.path.normpath(safe)
        if norm_safe in seen_sources:
            continue
        if not os.path.exists(norm_safe):
            raise HTTPException(status_code=404, detail=f"路径不存在：{os.path.basename(norm_safe) or norm_safe}")
        seen_sources.add(norm_safe)
        resolved_sources.append(norm_safe)

    if not resolved_sources:
        raise HTTPException(status_code=400, detail="请至少选择一个可下载的文件或目录。")

    def _build_zip_sync() -> str:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="workspace_dl_", suffix=".zip")
        os.close(tmp_fd)
        total_files = 0
        total_bytes = 0
        used_arcnames: set[str] = set()

        def _unique_arcname(candidate: str) -> str:
            clean = candidate.replace("\\", "/").lstrip("/")
            if clean not in used_arcnames:
                used_arcnames.add(clean)
                return clean
            base, ext = os.path.splitext(clean)
            for idx in range(1, 1000):
                alt = f"{base}_{idx}{ext}"
                if alt not in used_arcnames:
                    used_arcnames.add(alt)
                    return alt
            return f"{base}_{uuid.uuid4().hex[:6]}{ext}"

        try:
            written_entries = 0
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for src in resolved_sources:
                    base_label = os.path.basename(src) or "workspace"
                    # 直接选中的文件已通过 assert_path_allowed 校验（兼容公共 docs 下的官方软链文档）
                    if os.path.isfile(src):
                        if is_forbidden_platform_skill_path(src, user_info):
                            continue
                        size = os.path.getsize(src)
                        total_files += 1
                        total_bytes += size
                        if total_files > BATCH_DOWNLOAD_MAX_FILES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"打包文件数超出上限（{BATCH_DOWNLOAD_MAX_FILES} 个），请分批下载。",
                            )
                        if total_bytes > BATCH_DOWNLOAD_MAX_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"打包总大小超出上限（{_format_size_limit(BATCH_DOWNLOAD_MAX_BYTES)}），请分批下载。",
                            )
                        zf.write(src, _unique_arcname(base_label))
                        written_entries += 1
                    elif os.path.isdir(src):
                        has_any = False
                        for walk_root, dirs, files in os.walk(src, followlinks=False):
                            dirs[:] = [
                                d
                                for d in sorted(dirs)
                                if not d.startswith(".")
                                and not os.path.islink(os.path.join(walk_root, d))
                                and not is_forbidden_platform_skill_path(os.path.join(walk_root, d), user_info)
                            ]
                            for fname in sorted(files):
                                if fname.startswith("."):
                                    continue
                                fpath = os.path.join(walk_root, fname)
                                # 目录内软链一律跳过：目录本身已通过 _assert_no_symlink_escape 的
                                # realpath 校验，任何内部软链都可能是通往外部的跳板。
                                if os.path.islink(fpath):
                                    continue
                                if is_forbidden_platform_skill_path(fpath, user_info):
                                    continue
                                size = os.path.getsize(fpath)
                                total_files += 1
                                total_bytes += size
                                if total_files > BATCH_DOWNLOAD_MAX_FILES:
                                    raise HTTPException(
                                        status_code=413,
                                        detail=f"打包文件数超出上限（{BATCH_DOWNLOAD_MAX_FILES} 个），请分批下载。",
                                    )
                                if total_bytes > BATCH_DOWNLOAD_MAX_BYTES:
                                    raise HTTPException(
                                        status_code=413,
                                        detail=f"打包总大小超出上限（{_format_size_limit(BATCH_DOWNLOAD_MAX_BYTES)}），请分批下载。",
                                    )
                                rel = os.path.relpath(fpath, src).replace("\\", "/")
                                zf.write(fpath, _unique_arcname(f"{base_label}/{rel}"))
                                has_any = True
                                written_entries += 1
                        if not has_any:
                            # 空目录保留目录条目，避免生成空包引发困惑
                            zf.writestr(f"{base_label}/", "")
                            written_entries += 1
            if written_entries == 0:
                raise HTTPException(status_code=400, detail="选中的项目中没有可打包下载的文件。")
            return tmp_path
        except Exception:
            _cleanup_temp_file(tmp_path)
            raise

    zip_path = await run_in_threadpool(_build_zip_sync)
    raw_name = (body.archive_name or "").strip()
    if not raw_name:
        if len(resolved_sources) == 1:
            raw_name = f"{os.path.basename(resolved_sources[0]) or 'workspace'}.zip"
        else:
            raw_name = f"workspace-files-{len(resolved_sources)}.zip"
    if not raw_name.lower().endswith(".zip"):
        raw_name = f"{raw_name}.zip"
    safe_download_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", raw_name)

    return _CleanupFileResponse(
        zip_path,
        media_type="application/zip",
        filename=safe_download_name,
        content_disposition_type="attachment",
    )


class WorkspaceUsageResponse(BaseModel):
    total_bytes: int = Field(0, description="当前用户工作区已用总字节数（不含回收站）")
    file_count: int = Field(0, description="当前用户工作区文件总数（不含回收站）")
    trash_bytes: int = Field(0, description="回收站占用字节数")
    trash_count: int = Field(0, description="回收站顶层条目数")
    upload_max_bytes: int = Field(DEFAULT_UPLOAD_MAX_BYTES, description="单文件上传大小上限（字节）")


def _compute_workspace_usage_sync(private_root: str) -> Dict[str, int]:
    total_bytes = 0
    file_count = 0
    trash_bytes = 0
    trash_count = 0
    if not os.path.isdir(private_root):
        return {
            "total_bytes": 0,
            "file_count": 0,
            "trash_bytes": 0,
            "trash_count": 0,
        }

    norm_root = os.path.normpath(private_root)
    trash_root = os.path.normpath(os.path.join(norm_root, TRASH_DIR_NAME))
    for walk_root, dirs, files in os.walk(norm_root, followlinks=False):
        norm_walk = os.path.normpath(walk_root)
        in_trash = norm_walk == trash_root or norm_walk.startswith(trash_root + os.sep)
        if norm_walk == trash_root:
            trash_count = sum(1 for n in dirs if not n.startswith(".")) + sum(
                1 for n in files if not n.startswith(".")
            )
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(walk_root, d))]
        for fname in files:
            if in_trash and fname == TRASH_META_FILENAME:
                continue
            fpath = os.path.join(walk_root, fname)
            if os.path.islink(fpath):
                continue
            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                continue
            if in_trash:
                trash_bytes += fsize
            else:
                total_bytes += fsize
                file_count += 1

    return {
        "total_bytes": total_bytes,
        "file_count": file_count,
        "trash_bytes": trash_bytes,
        "trash_count": trash_count,
    }


@router.get(
    "/usage",
    response_model=StandardResponse[WorkspaceUsageResponse],
    summary="获取当前用户工作空间容量与上传配额信息",
)
async def get_workspace_usage(
    refresh: bool = Query(False, description="是否忽略短缓存强制重算"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    private_root = get_user_private_workspace_root(user_info)
    upload_max_bytes = _workspace_upload_max_bytes()
    if not private_root:
        return StandardResponse(data=WorkspaceUsageResponse(upload_max_bytes=upload_max_bytes))

    # 缓存键必须与 _invalidate_usage_cache()（经 _resolve_owner_workspace_root 得到的 realpath）同口径，
    # 否则 data 根为软链时写入的缓存永远失效不掉，容量统计会一直停在旧值。
    norm_root = os.path.normpath(os.path.realpath(private_root))
    now = time.monotonic()
    cached = _workspace_usage_cache.get(norm_root)
    if not refresh and cached and (now - cached[0]) < USAGE_CACHE_TTL_SECONDS:
        stats = cached[1]
    else:
        stats = await run_in_threadpool(_compute_workspace_usage_sync, norm_root)
        _workspace_usage_cache[norm_root] = (now, stats)

    return StandardResponse(
        data=WorkspaceUsageResponse(
            total_bytes=stats["total_bytes"],
            file_count=stats["file_count"],
            trash_bytes=stats["trash_bytes"],
            trash_count=stats["trash_count"],
            upload_max_bytes=upload_max_bytes,
        )
    )

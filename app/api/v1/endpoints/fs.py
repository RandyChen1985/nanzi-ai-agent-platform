import fnmatch
import json
import logging
import os
import re
import shutil
import time
import uuid
from typing import List, Optional, Dict, Any, Literal
from fastapi import APIRouter, HTTPException, Depends, Query, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.schemas.response import StandardResponse
from app.core.dependencies import require_api_key
from app.core.redis import get_redis
from app.utils.fs_paths import get_data_base_dir, normalize_under_base
from app.utils.fs_access import (
    assert_path_allowed,
    assert_path_writable,
    get_allowed_fs_roots,
    get_user_docs_dir,
    get_user_private_workspace_root,
    get_user_uploads_dir,
    is_session_dir_name,
    is_fs_admin,
    is_fs_virtual_root,
    is_path_allowed,
    is_path_writable,
    normalize_fs_path,
    resolve_parent_path,
)

router = APIRouter()

logger = logging.getLogger(__name__)

TRASH_DIR_NAME = ".trash"
WORKSPACE_RECENT_FILES_MAX = 20
WORKSPACE_RECENT_REDIS_PREFIX = "agent:workspace_recent_files:"
WORKSPACE_BROWSER_PREFS_REDIS_PREFIX = "agent:workspace_browser_prefs:"
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
        results.append(
            FileItem(
                name=os.path.basename(entry_path),
                path=entry_path,
                is_dir=is_dir,
                size=0 if is_dir else stat.st_size,
                mtime=stat.st_mtime,
                is_user_workspace=_is_user_workspace_path(entry_path, user_info),
            )
        )
    except OSError:
        pass


def _is_trash_path(path: str, user_info: Dict[str, Any]) -> bool:
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        return False
    trash = os.path.normpath(os.path.join(private_root, TRASH_DIR_NAME))
    normalized = os.path.normpath(path)
    return normalized == trash or normalized.startswith(trash + os.sep)


def _list_directory_entries(target_path: str, user_info: Dict[str, Any]) -> List[FileItem]:
    items: List[FileItem] = []
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    items.append(
                        FileItem(
                            name=entry.name,
                            path=entry.path,
                            is_dir=is_dir,
                            size=0 if is_dir else stat.st_size,
                            mtime=stat.st_mtime,
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
            items.append(
                FileItem(
                    name=os.path.basename(root),
                    path=root,
                    is_dir=True,
                    size=0,
                    mtime=stat.st_mtime,
                    is_user_workspace=_is_user_workspace_path(root, user_info),
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
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    base_dir = get_base_dir()

    if _contains_parent_path_segment(path):
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。",
        )

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
    if not os.path.exists(target_path) and private_root:
        norm_target = os.path.normpath(target_path)
        norm_root = os.path.normpath(private_root)
        if norm_target.startswith(norm_root + os.sep):
            rel = os.path.relpath(norm_target, norm_root)
            if "/" not in rel and is_session_dir_name(rel):
                os.makedirs(norm_target, mode=0o700, exist_ok=True)
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
        writable=is_path_writable(target_path, user_info) and not _is_trash_path(target_path, user_info),
        user_workspace_root=_user_workspace_root_for_response(user_info),
        is_virtual_root=False,
    ))


TEXT_PREVIEW_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".sql", ".py", ".js", ".ts",
    ".sh", ".xml", ".html", ".css", ".yaml", ".yml", ".ini", ".conf",
    ".log", ".env"
}

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
        resolve_session_workdir,
        resolve_user_docs_dir,
        resolve_user_workspace_root,
    )

    safe_path: str | None = None

    if conversation_id and not path.startswith("/") and not path.startswith("/app/data"):
        root = workspace_root
        docs_workdir = resolve_user_docs_dir(
            root=root,
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
        )
        if os.path.isdir(docs_workdir) or not must_exist:
            candidate = normalize_under_base(path, docs_workdir)
            if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                safe_path = candidate

        if not safe_path:
            session_workdir = resolve_session_workdir(
                root=root,
                user_id=user_info.get("user_id") or user_info.get("id"),
                user_name=user_info.get("user_name") or user_info.get("username"),
                user_info=user_info,
                conversation_id=conversation_id,
            )
            if os.path.isdir(session_workdir):
                candidate = normalize_under_base(path, session_workdir)
                if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                    safe_path = candidate

        if not safe_path:
            user_workspaces_root = resolve_user_workspace_root(
                root=root,
                user_id=user_info.get("user_id") or user_info.get("id"),
                user_name=user_info.get("user_name") or user_info.get("username"),
                user_info=user_info,
            )
            if user_workspaces_root and os.path.isdir(user_workspaces_root):
                for cid_dir in os.listdir(user_workspaces_root):
                    candidate_dir = os.path.join(user_workspaces_root, cid_dir)
                    if not os.path.isdir(candidate_dir):
                        continue
                    candidate = normalize_under_base(path, candidate_dir)
                    if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
                        safe_path = candidate
                        break

    if not safe_path:
        candidate = normalize_fs_path(path)
        if candidate and (not must_exist or os.path.isfile(candidate)) and is_path_allowed(candidate, user_info):
            safe_path = candidate

    if not safe_path:
        candidate = normalize_fs_path(path)
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
    if ext in TEXT_PREVIEW_EXTENSIONS:
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

    ext = os.path.splitext(writable_path)[1].lower()
    if ext not in TEXT_PREVIEW_EXTENSIONS:
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
    parent_writable = assert_path_writable(parent_path, user_info)
    if not os.path.isdir(parent_writable):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")

    name = _validate_new_entry_basename(body.name)
    target = os.path.normpath(os.path.join(parent_writable, name))
    if os.path.exists(target):
        raise HTTPException(status_code=409, detail="同名文件或目录已存在。")

    try:
        if body.kind == "dir":
            os.makedirs(target, exist_ok=False)
        else:
            ext = os.path.splitext(name)[1].lower()
            if ext and ext not in TEXT_PREVIEW_EXTENSIONS:
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

    return StandardResponse(
        data=FileEntryCreateResponse(
            path=target,
            name=name,
            is_dir=body.kind == "dir",
            size=0 if body.kind == "dir" else stat.st_size,
            mtime=stat.st_mtime,
        )
    )


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
FORBIDDEN_UPLOAD_EXTENSIONS = {".exe", ".bat", ".sh", ".cmd", ".com", ".msi", ".php", ".jsp", ".asp", ".py", ".pl"}


def _resolve_writable_entry_path(path: str, user_info: Dict[str, Any]) -> str:
    safe_path = assert_path_allowed(path, user_info)
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
    return os.path.normpath(os.path.join(private_root, TRASH_DIR_NAME))


def _assert_not_trash_root(path: str, user_info: Dict[str, Any]) -> str:
    safe_path = assert_path_allowed(path, user_info)
    if os.path.normpath(safe_path) == _trash_root_for(user_info):
        raise HTTPException(status_code=400, detail="回收站目录不可重命名或删除。")
    return safe_path


def _assert_trash_entry_path(path: str, user_info: Dict[str, Any]) -> str:
    safe_path = assert_path_allowed(path, user_info)
    if not _is_trash_path(safe_path, user_info):
        raise HTTPException(status_code=403, detail="只能操作回收站内的文件或文件夹。")
    if os.path.normpath(safe_path) == _trash_root_for(user_info):
        raise HTTPException(status_code=400, detail="不能对回收站目录本身执行该操作。")
    return safe_path


def _trash_dir_for(user_info: Dict[str, Any]) -> str:
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        raise HTTPException(status_code=403, detail="无法解析用户工作目录。")
    trash = os.path.join(private_root, TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    return trash


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
    name: str
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
    _assert_not_trash_root(body.path, user_info)
    source = _resolve_writable_entry_path(body.path, user_info)
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    new_name = _validate_new_entry_basename(body.new_name)
    target = os.path.normpath(os.path.join(os.path.dirname(source), new_name))
    if os.path.exists(target):
        raise HTTPException(status_code=409, detail="目标名称已存在。")
    try:
        os.rename(source, target)
        stat = os.stat(target)
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


@router.post(
    "/delete-entry",
    response_model=StandardResponse[FileDeleteResponse],
    summary="删除工作空间文件或文件夹（移入回收站）",
)
async def delete_fs_entry(
    body: FileDeleteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    _assert_not_trash_root(body.path, user_info)
    source = _resolve_writable_entry_path(body.path, user_info)
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    basename = os.path.basename(source)
    trash_dir = _trash_dir_for(user_info)
    trashed_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{basename}"
    trashed_path = os.path.normpath(os.path.join(trash_dir, trashed_name))
    try:
        shutil.move(source, trashed_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
    return StandardResponse(
        data=FileDeleteResponse(path=source, trashed_path=trashed_path)
    )


class FileRestoreResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    mtime: float


class FilePurgeResponse(BaseModel):
    path: str


class FileEmptyTrashResponse(BaseModel):
    deleted_count: int


@router.post(
    "/restore-entry",
    response_model=StandardResponse[FileRestoreResponse],
    summary="从回收站恢复文件或文件夹",
    description="恢复至本人工作目录根下；若同名已存在则自动追加 _restored 后缀。",
)
async def restore_fs_entry(
    body: FileDeleteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    source = _assert_trash_entry_path(body.path, user_info)
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    private_root = get_user_private_workspace_root(user_info)
    if not private_root:
        raise HTTPException(status_code=403, detail="无法解析用户工作目录。")
    original_name = _parse_trashed_entry_name(os.path.basename(source))
    target = _resolve_restore_target_path(private_root, original_name)
    try:
        shutil.move(source, target)
        stat = os.stat(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"恢复失败: {exc}") from exc
    return StandardResponse(
        data=FileRestoreResponse(
            path=target,
            name=os.path.basename(target),
            is_dir=os.path.isdir(target),
            mtime=stat.st_mtime,
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
    source = _assert_trash_entry_path(body.path, user_info)
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="文件或目录不存在。")
    try:
        if os.path.isdir(source):
            shutil.rmtree(source)
        else:
            os.remove(source)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"永久删除失败: {exc}") from exc
    return StandardResponse(data=FilePurgeResponse(path=source))


@router.post(
    "/empty-trash",
    response_model=StandardResponse[FileEmptyTrashResponse],
    summary="清空回收站（永久删除全部回收项）",
)
async def empty_trash(
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    trash_dir = _trash_dir_for(user_info)
    deleted_count = 0
    try:
        for name in os.listdir(trash_dir):
            if name.startswith("."):
                continue
            entry = os.path.join(trash_dir, name)
            if os.path.isdir(entry):
                shutil.rmtree(entry)
            elif os.path.isfile(entry) or os.path.islink(entry):
                os.remove(entry)
            else:
                continue
            deleted_count += 1
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"清空回收站失败: {exc}") from exc
    return StandardResponse(data=FileEmptyTrashResponse(deleted_count=deleted_count))


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
    parent = _resolve_writable_entry_path(parent_path, user_info)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")

    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件大小超出 20MB 限制")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in FORBIDDEN_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"禁止上传该类型文件: {ext}")

    clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "")
    if not clean_name or clean_name.startswith("."):
        clean_name = f"upload{ext or '.bin'}"
    unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{clean_name}"
    target = os.path.normpath(os.path.join(parent, unique_name))

    try:
        with open(target, "wb") as handle:
            handle.write(contents)
        stat = os.stat(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc

    return StandardResponse(
        data=FileUploadResponse(
            path=target,
            name=unique_name,
            size=stat.st_size,
            mtime=stat.st_mtime,
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

    results: List[FileItem] = []
    truncated = False

    for search_root in search_roots:
        for root, dirs, files in os.walk(search_root):
            norm_root = os.path.normpath(root)
            if not is_path_allowed(norm_root, user_info):
                continue

            dirs[:] = sorted(d for d in dirs if not d.startswith("."))

            for name in sorted(dirs):
                if _name_matches_search_keyword(name, keyword):
                    _append_fs_entry(results, os.path.join(root, name), True, user_info=user_info)
                    if len(results) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

            for name in sorted(files):
                if name.startswith("."):
                    continue
                if _name_matches_search_keyword(name, keyword):
                    _append_fs_entry(results, os.path.join(root, name), False, user_info=user_info)
                    if len(results) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break

    results.sort(key=lambda x: (not x.is_dir, x.name.lower()))

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

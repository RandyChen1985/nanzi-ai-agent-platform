import os
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.schemas.response import StandardResponse
from app.core.dependencies import require_api_key
from app.utils.fs_paths import get_data_base_dir, normalize_under_base
from app.utils.fs_access import (
    assert_path_allowed,
    assert_path_writable,
    get_allowed_fs_roots,
    get_user_private_workspace_root,
    is_fs_admin,
    is_fs_virtual_root,
    is_path_allowed,
    normalize_fs_path,
    resolve_parent_path,
)

router = APIRouter()

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


def get_base_dir() -> str:
    return get_data_base_dir()


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
        ))

    target_path = assert_path_allowed(path or base_dir, user_info)
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
        resolve_user_workspace_root,
    )

    safe_path: str | None = None

    if conversation_id and not path.startswith("/") and not path.startswith("/app/data"):
        root = workspace_root
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
                if keyword in name.lower():
                    _append_fs_entry(results, os.path.join(root, name), True, user_info=user_info)
                    if len(results) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

            for name in sorted(files):
                if name.startswith("."):
                    continue
                if keyword in name.lower():
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

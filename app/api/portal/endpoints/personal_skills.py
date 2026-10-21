"""个人技能管理 API — agent_workspaces/{user_key}/skills/{skill_id}/

权限：仅需已登录（require_api_key），用户只能操作自己的技能，无需 skills_management 菜单权限。
路由挂载于 /api/portal/skills/personal（在 api.py 中注册为 personal_skills router）。
"""

import os
import shutil
import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.ai.skill_resolver import (
    get_user_personal_skills_dir,
    iter_personal_skill_dirs,
    resolve_personal_skill_dir,
)
from app.services.skill_rename_service import (
    SkillRenameError,
    remove_skill_references,
    rename_skill,
    validate_skill_id,
)
from app.services.skill_publication_service import (
    PublicationConflictError,
    PublicationNotFoundError,
    list_my_publication_summaries,
    submit_personal_skill,
    withdraw_personal_skill_publication,
)
from app.services.skill_share_service import (
    ShareConflictError,
    ShareNotFoundError,
    SharePermissionError,
    accept_share,
    annotate_personal_skills,
    list_received_shares,
    list_sent_shares,
    reject_share,
    search_share_targets,
    share_menu_counts,
    share_skill,
    withdraw_share,
)

# 复用平台技能中的辅助函数
from app.api.portal.endpoints.skills import (
    SkillCreateRequest,
    FileEditRequest,
    SkillAssetCreateRequest,
    EDITABLE_TEXT_EXTENSIONS,
    parse_skill_metadata,
    get_file_tree,
    get_skill_modified_at,
    atomic_import_skill_package,
    build_skill_archive,
    safe_extract_archive,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# 内部辅助：推导并校验个人技能目录
# ---------------------------------------------------------------------------

def _get_personal_skills_root(user_info: dict) -> str:
    """获取当前用户的个人技能根目录，不存在则自动创建。"""
    skills_dir = get_user_personal_skills_dir(user_info)
    if not skills_dir:
        raise HTTPException(status_code=400, detail="无法解析用户工作区，请确认账号信息完整")
    os.makedirs(skills_dir, exist_ok=True)
    return skills_dir


def _validate_personal_skill_path(
    user_info: dict,
    skill_id: str,
    relative_path: str = "",
) -> str:
    """校验 skill_id 和可选的 relative_path，防御路径穿越攻击，返回绝对路径。

    ID 的格式/长度/保留字校验收敛到 :func:`skill_rename_service.validate_skill_id`
    （与平台技能同一份实现）：此前这里只有 ``^[a-zA-Z0-9_-]+$``，既没有 128 长度上限
    （超长 ID 落库到 ``VARCHAR(128)`` 的发布血缘列会被截断/报错），也不挡保留字。
    """
    try:
        skill_id = validate_skill_id(skill_id)
    except SkillRenameError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    skills_root = os.path.abspath(_get_personal_skills_root(user_info))
    # 存量技能可能位于历史遗留的 .seed 布局下（K8s 未修复期的产物）：先定位真实
    # 目录，找不到再回退到规范平铺位置（新建技能走这条）。否则编辑 .seed 里的技能
    # 会在平铺处新建一份副本，出现「同一技能两份」。
    skill_base = resolve_personal_skill_dir(skills_root, skill_id) or os.path.abspath(
        os.path.join(skills_root, skill_id)
    )

    if os.path.commonpath([skills_root, skill_base]) != skills_root:
        raise HTTPException(status_code=403, detail="安全拦截：个人技能路径越界")

    if not relative_path:
        return skill_base

    target = os.path.abspath(os.path.join(skill_base, relative_path.lstrip("/")))
    if os.path.commonpath([skill_base, target]) != skill_base:
        raise HTTPException(status_code=403, detail="安全拦截：文件子路径越界")
    return target


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

@router.get("", summary="列出当前用户的个人技能")
async def list_personal_skills(
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    """扫描用户个人技能目录，解析 SKILL.md 返回列表。

    列表同时带上 ``shared_from``（该技能是否由「收到的分享」落地而来）与
    ``share_count``（该技能当前有效分享人数），前端卡片据此展示来源与分享入口。
    """
    skills_root = _get_personal_skills_root(user)
    skills_list = []
    try:
        # 兼容历史遗留的 .seed 布局：直接 os.listdir 会跳过点开头目录，导致
        # 「文件都在、列表却为空」
        for item, item_path in iter_personal_skill_dirs(skills_root):
            skill_md = os.path.join(item_path, "SKILL.md")
            meta = parse_skill_metadata(item, skill_md)
            meta["scope"] = "personal"
            meta["modified_at"] = get_skill_modified_at(item_path, skill_md)
            skills_list.append(meta)
    except Exception as e:
        logger.error("[PersonalSkills] Failed to list skills: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    try:
        annotations = await annotate_personal_skills(session, user=user)
    except Exception as exc:  # 分享标注失败不该让整个「我的技能」列表不可用
        logger.warning("[PersonalSkills] Failed to annotate shares: %s", exc)
        annotations = {"shared_from": {}, "share_counts": {}}
    for meta in skills_list:
        meta["shared_from"] = annotations["shared_from"].get(meta.get("id"))
        meta["share_count"] = annotations["share_counts"].get(meta.get("id"), 0)
    return {"status": "success", "data": skills_list}


# ---------------------------------------------------------------------------
# 点对点分享
#
# 路由顺序有讲究：``/share-targets`` 只有一段，必须注册在 ``/{skill_id}`` 之前，
# 否则会被当成「技能 ID 叫 share-targets」。
# ---------------------------------------------------------------------------

def _share_http_exception(exc: Exception) -> HTTPException:
    """把分享服务的领域异常翻译成 HTTP 状态码。"""
    if isinstance(exc, ShareNotFoundError):
        return HTTPException(status_code=404, detail="分享记录不存在")
    if isinstance(exc, SharePermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ShareConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="个人技能不存在")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="技能分享操作失败")


@router.get("/share-targets", summary="搜索可以接收分享的用户")
async def search_personal_skill_share_targets(
    keyword: str = "",
    limit: int = 20,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await search_share_targets(session, user=user, keyword=keyword, limit=limit)
        return {"status": "success", "data": data}
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise _share_http_exception(exc)
        logger.error("[PersonalSkills] Failed to search share targets: %s", exc)
        raise HTTPException(status_code=500, detail="搜索用户失败")


@router.get("/shares/summary", summary="技能分享的菜单计数")
async def get_personal_skill_share_summary(
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await share_menu_counts(session, user=user)
        return {"status": "success", "data": data}
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise _share_http_exception(exc)
        logger.error("[PersonalSkills] Failed to load share summary: %s", exc)
        raise HTTPException(status_code=500, detail="加载分享计数失败")


@router.get("/shares/sent", summary="我发出的技能分享")
async def list_personal_skill_sent_shares(
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await list_sent_shares(
            session, user=user, status=status or None, limit=limit, offset=offset
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to list sent shares: %s", exc)
        raise HTTPException(status_code=500, detail="加载我的分享失败")


@router.delete("/shares/sent/{share_id}", summary="撤回尚未被接受的技能分享")
async def withdraw_personal_skill_share(
    share_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await withdraw_share(session, user=user, share_id=share_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to withdraw share %s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="撤回分享失败")


@router.get("/shares/received", summary="我收到的技能分享")
async def list_personal_skill_received_shares(
    status: str = "PENDING",
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await list_received_shares(
            session, user=user, status=status, limit=limit, offset=offset
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to list received shares: %s", exc)
        raise HTTPException(status_code=500, detail="加载收到的分享失败")


@router.post("/shares/received/{share_id}/accept", summary="接受技能分享并安装到我的技能")
async def accept_personal_skill_share(
    share_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await accept_share(session, user=user, share_id=share_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to accept share %s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="接受分享失败")


@router.post("/shares/received/{share_id}/reject", summary="拒绝技能分享")
async def reject_personal_skill_share(
    share_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await reject_share(session, user=user, share_id=share_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to reject share %s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="拒绝分享失败")


def _raise_if_domain(exc: Exception) -> None:
    if isinstance(
        exc,
        (ShareNotFoundError, SharePermissionError, ShareConflictError, FileNotFoundError, ValueError),
    ):
        raise _share_http_exception(exc)


@router.post("/{skill_id}/shares", summary="把个人技能分享给指定用户")
async def create_personal_skill_share(
    skill_id: str,
    payload: dict,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await share_skill(
            session,
            user=user,
            skill_id=skill_id,
            target_user_ids=payload.get("target_user_ids") or [],
            message=payload.get("message"),
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_domain(exc)
        logger.error("[PersonalSkills] Failed to share skill %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="分享技能失败")


# ---------------------------------------------------------------------------
# 新建
# ---------------------------------------------------------------------------

@router.post("", summary="新建个人技能")
async def create_personal_skill(
    req: SkillCreateRequest,
    user: dict = Depends(require_api_key),
):
    """在用户个人目录下新建技能文件夹并初始化默认 SKILL.md。"""
    try:
        skill_dir = _validate_personal_skill_path(user, req.id)
        if os.path.exists(skill_dir):
            raise HTTPException(status_code=400, detail="该个人技能 ID 已存在")

        os.makedirs(skill_dir, exist_ok=True)
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        default_content = (
            f"---\n"
            f"name: {req.name}\n"
            f"description: {req.description}\n"
            f"---\n\n"
            f"# {req.name}\n\n"
            f"在此处编写该技能的具体战术规范、操作流程与 <HARD-GATE> 红线守则。\n"
        )
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(default_content)
        return {"status": "success", "message": f"个人技能 {req.id} 创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to create skill %s: %s", req.id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

@router.get("/{skill_id}", summary="获取个人技能详情")
async def get_personal_skill_detail(
    skill_id: str,
    user: dict = Depends(require_api_key),
):
    """获取个人技能的 SKILL.md 内容和文件目录树。"""
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能未找到")

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        skill_md_content = ""
        if os.path.exists(skill_md_path):
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()

        meta = parse_skill_metadata(skill_id, skill_md_path)
        file_tree = get_file_tree(skill_dir, skill_dir)
        return {
            "status": "success",
            "data": {
                "id": skill_id,
                "name": meta.get("name", skill_id),
                "description": meta.get("description", ""),
                "scope": "personal",
                "skill_md_content": skill_md_content,
                "file_tree": file_tree,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to fetch detail for %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 预览（供聊天页技能抽屉使用）
# ---------------------------------------------------------------------------

@router.put("/{skill_id}/rename", summary="重命名个人技能并同步引用")
async def rename_personal_skill(
    skill_id: str,
    req: dict,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    """重命名个人技能。

    除目录改名外，还会同步发布谱系里的来源技能 ID（``skill_publications.
    source_personal_skill_id``）与会话里已挂载的技能 chip（Redis）。发布谱系不同步的话，
    该技能的历史发布记录会变成「源技能已不存在」的孤儿。
    """
    try:
        new_skill_id = str((req or {}).get("new_skill_id") or "")
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能不存在")
        report = await rename_skill(
            session,
            current_dir=skill_dir,
            old_skill_id=skill_id,
            new_skill_id=new_skill_id,
            scope="personal",
            actor=user,
        )
        return {
            "status": "success",
            "data": report,
            "message": f"个人技能已重命名为 {report['skill_id']}",
        }
    except SkillRenameError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to rename skill %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{skill_id}/export", summary="导出个人技能为 zip 压缩包")
async def export_personal_skill_archive(skill_id: str, user: dict = Depends(require_api_key)):
    """把个人技能目录打包下载，便于备份、迁移或分享给同事。

    个人技能此前只能上传进来、没有任何取回通道：想备份或换个账号复用，只能去服务器
    磁盘上手工找目录。导出与导入（``upload-archive`` / ``import``）互为逆向操作。
    """
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能不存在")
        payload = build_skill_archive(skill_dir, arc_prefix=f"{skill_id}/")
        headers = {
            "Content-Disposition": f'attachment; filename="{skill_id}.zip"',
            "Content-Length": str(len(payload)),
        }
        return Response(content=payload, media_type="application/zip", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to export skill %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{skill_id}/download", summary="下载个人技能内的单个文件")
async def download_personal_skill_file(
    skill_id: str,
    path: str,
    user: dict = Depends(require_api_key),
):
    """下载个人技能包内的单个文件。"""
    try:
        file_path = _validate_personal_skill_path(user, skill_id, path)
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as handle:
            payload = handle.read()
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
        }
        return Response(content=payload, media_type="application/octet-stream", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to download file %s in %s: %s", path, skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{skill_id}/preview", summary="预览个人技能 SKILL.md")
async def preview_personal_skill(
    skill_id: str,
    user: dict = Depends(require_api_key),
):
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能未找到")

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        skill_md_content = ""
        if os.path.exists(skill_md_path):
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()

        meta = parse_skill_metadata(skill_id, skill_md_path)
        return {
            "status": "success",
            "data": {
                "id": skill_id,
                "name": meta.get("name", skill_id),
                "description": meta.get("description", ""),
                "scope": "personal",
                "skill_md_content": skill_md_content,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to preview %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 读取文件
# ---------------------------------------------------------------------------

@router.get("/{skill_id}/files", summary="读取个人技能文件内容")
async def get_personal_skill_file(
    skill_id: str,
    path: str,
    user: dict = Depends(require_api_key),
):
    try:
        file_path = _validate_personal_skill_path(user, skill_id, path)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")

        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线读取文本类文件")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"status": "success", "content": content}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to read file %s in %s: %s", path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 编辑文件
# ---------------------------------------------------------------------------

@router.put("/{skill_id}/files", summary="编辑个人技能文件")
async def edit_personal_skill_file(
    skill_id: str,
    req: FileEditRequest,
    user: dict = Depends(require_api_key),
):
    try:
        file_path = _validate_personal_skill_path(user, skill_id, req.path)
        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线编辑文本类文件")
        if len(req.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小超出 2MB 在线编辑限制")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "success", "message": "保存成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to edit file %s in %s: %s", req.path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 新建资产（文件/文件夹）
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/files", summary="在个人技能目录内新建文件或文件夹")
async def create_personal_skill_asset(
    skill_id: str,
    req: SkillAssetCreateRequest,
    user: dict = Depends(require_api_key),
):
    try:
        if not req.path or not req.path.strip():
            raise HTTPException(status_code=400, detail="资产名称不能为空")
        if os.path.isabs(req.path) or req.path.startswith("/"):
            raise HTTPException(status_code=400, detail="资产路径必须是相对路径")

        target_path = _validate_personal_skill_path(user, skill_id, req.path)
        if req.type == "file":
            _, ext = os.path.splitext(target_path.lower())
            if ext not in EDITABLE_TEXT_EXTENSIONS:
                raise HTTPException(status_code=400, detail="只允许新建可在线编辑的文本类文件")

        parent_path = os.path.dirname(target_path)
        if not os.path.isdir(parent_path):
            raise HTTPException(status_code=400, detail="目标父文件夹不存在")
        if os.path.exists(target_path):
            raise HTTPException(status_code=409, detail="同名文件或文件夹已存在")

        if req.type == "folder":
            os.mkdir(target_path)
        else:
            with open(target_path, "x", encoding="utf-8"):
                pass
        return {"status": "success", "message": "创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to create asset %s in %s: %s", req.path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 上传文件
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/upload", summary="上传文件到个人技能目录")
async def upload_personal_skill_file(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_api_key),
):
    try:
        sub_dir = folder if folder else ""
        file_name = file.filename if file.filename else "uploaded_file"
        target_path = _validate_personal_skill_path(
            user, skill_id, os.path.join(sub_dir, file_name)
        )
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="上传文件大小超出 10MB 限制")

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": f"文件 {file_name} 上传成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to upload file in %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_id}/upload-archive", summary="上传压缩包并解压到个人技能目录")
async def upload_personal_skill_archive(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_api_key),
):
    try:
        sub_dir = folder if folder else ""
        target_dir = _validate_personal_skill_path(user, skill_id, sub_dir)
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="上传的压缩文件大小不能超出 20MB 限制")

        file_name = file.filename if file.filename else "archive.zip"
        safe_extract_archive(content, file_name, target_dir)
        return {"status": "success", "message": f"技能压缩包 {file_name} 上传解压成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to upload archive in %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 删除文件
# ---------------------------------------------------------------------------

@router.delete("/{skill_id}/files", summary="删除个人技能目录下的文件或子文件夹")
async def delete_personal_skill_file(
    skill_id: str,
    path: str,
    user: dict = Depends(require_api_key),
):
    try:
        target_path = _validate_personal_skill_path(user, skill_id, path)
        if not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="目标文件不存在")
        if path.strip().lower() == "skill.md":
            raise HTTPException(status_code=400, detail="禁止直接删除核心规范文件 SKILL.md")

        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return {"status": "success", "message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to delete %s in %s: %s", path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 删除整个技能
# ---------------------------------------------------------------------------

@router.delete("/{skill_id}", summary="彻底删除个人技能")
async def delete_personal_skill(
    skill_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    """彻底删除个人技能目录，并清理会话挂载里的引用。

    个人技能同样会被挂进会话（Redis ``resource_scope_v1.skills``，带 30 天 TTL）：只删目录
    不清理，用户下次打开历史会话会看到一个指向不存在技能的空 chip。发布血缘**不在这里
    处理**：个人副本删掉不影响已经发布的平台技能（那是一份独立的快照与目录），要下架得走
    技能管理端的下架功能——清理函数会在这种情况下明确提示，而不是替用户悄悄下架。
    """
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能目录不存在")
        shutil.rmtree(skill_dir)
        report = await remove_skill_references(
            session,
            skill_id=skill_id,
            scope="personal",
            actor=user,
        )
        return {
            "status": "success",
            "data": report,
            "message": f"个人技能 {skill_id} 已物理删除",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to delete skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 提交为平台技能
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/publication-requests", summary="提交个人技能为平台技能")
async def submit_personal_skill_publication(
    skill_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await submit_personal_skill(session, user=user, skill_id=skill_id)
        return {"status": "success", "data": data}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="个人技能不存在或缺少 SKILL.md")
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to submit publication for %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="提交平台技能审核失败")


@router.post("/{skill_id}/publication-requests/withdraw", summary="撤销个人技能发布审核")
async def withdraw_personal_skill_publication_request(
    skill_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await withdraw_personal_skill_publication(session, user=user, skill_id=skill_id)
        return {"status": "success", "data": data}
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to withdraw publication for %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="撤销平台技能审核失败")


@router.get("/{skill_id}/publication-status", summary="获取个人技能发布状态")
async def get_personal_skill_publication_status(
    skill_id: str,
    user: dict = Depends(require_api_key),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        try:
            user_id = int(user.get("user_id") or user.get("id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="用户身份无效")
        summaries = await list_my_publication_summaries(
            session,
            user_id=user_id,
            skill_ids=[skill_id],
        )
        data = summaries.get(
            skill_id,
            {
                "skill_id": skill_id,
                "publication_status": "UNPUBLISHED",
                "current_public_version": None,
                "pending_version": None,
            },
        )
        return {"status": "success", "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[PersonalSkills] Failed to fetch publication status for %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="获取技能发布状态失败")


# ---------------------------------------------------------------------------
# 导入压缩包
# ---------------------------------------------------------------------------

@router.post("/import", summary="导入压缩包为个人技能")
async def import_personal_skill_package(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    user: dict = Depends(require_api_key),
):
    """上传 zip/tar 压缩包，解压到个人技能目录。解包后必须包含 SKILL.md。"""
    try:
        filename = file.filename if file.filename else "imported_skill.zip"
        base_name, _ = os.path.splitext(filename)
        if base_name.lower().endswith(".tar"):
            base_name, _ = os.path.splitext(base_name)

        skill_id = re.sub(r"[^a-zA-Z0-9_\-]", "", base_name).strip()
        if not skill_id:
            raise HTTPException(status_code=400, detail="无法从文件名中提取合法的技能 ID")

        skill_dir = _validate_personal_skill_path(user, skill_id)
        if os.path.exists(skill_dir) and not overwrite:
            raise HTTPException(
                status_code=400,
                detail=f"个人技能 {skill_id} 已存在，请开启 overwrite 以覆盖导入",
            )

        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="导入文件大小超出 20MB 限制")

        atomic_import_skill_package(content, filename, skill_dir)

        return {"status": "success", "message": f"个人技能 {skill_id} 导入成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to import skill package: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{skill_id}/toggle", summary="启用或禁用个人技能")
async def toggle_personal_skill(
    skill_id: str,
    enabled: bool,
    user: dict = Depends(require_api_key),
):
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能不存在")
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        
        from app.utils.skill_metadata import toggle_skill_enabled_in_file
        success = toggle_skill_enabled_in_file(skill_md_path, enabled)
        if not success:
            # 这是文件内容问题（缺 SKILL.md 或缺可识别的 frontmatter），不是服务端故障：
            # 返回 400 让用户看到原因，此前报 500 会让人以为平台坏了，且技能永远禁不掉。
            raise HTTPException(
                status_code=400,
                detail="无法切换启用状态：SKILL.md 缺失或缺少可识别的 YAML frontmatter",
            )
        return {"status": "success", "message": f"技能已{'启用' if enabled else '禁用'}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PersonalSkills] Failed to toggle skill {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

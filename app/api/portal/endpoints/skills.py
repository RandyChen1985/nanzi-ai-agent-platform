import os
import shutil
import logging
import re
import io
import tempfile
import uuid
import zipfile
import tarfile
from io import BytesIO
from typing import List, Dict, Any, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.upload_limits import SKILL_PACKAGE_MAX_BYTES, SKILL_PACKAGE_MAX_MB
from app.core.dependencies import require_api_key, require_permission
from app.core.orm import get_db_session
from app.services.ai.agent_manager import AgentManagerService
from app.services.skill_rename_service import (
    RESERVED_SKILL_IDS,
    SKILL_ID_PATTERN,
    SkillRenameError,
    remove_skill_references,
    rename_skill,
    validate_skill_id,
)
from app.services.skill_publication_service import (
    PublicationConflictError,
    PublicationNotFoundError,
    SnapshotFileError,
    approve_publication,
    get_publication_version,
    list_pending_publications,
    list_published_publications,
    read_snapshot_text_file,
    reject_publication,
    revoke_published_version,
)
from app.utils.skill_metadata import parse_skill_frontmatter

logger = logging.getLogger(__name__)
router = APIRouter()
skill_platform_admin = require_permission("element", "element:skills:admin")
skill_publication_reviewer = skill_platform_admin

@router.get("/stats", summary="获取技能调用统计数据")
async def get_skills_stats(
    user_info: dict = Depends(require_api_key),
):
    """只读统计：登录用户均可查看，不要求 element:skills:admin。"""
    from app.services.ai.skills_stats_service import skills_stats_service
    return await skills_stats_service.get_stats()


@router.get("/bindings", summary="获取公共技能显式绑定的智能体")
async def get_skill_bindings(
    user_info: dict = Depends(skill_platform_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    反查平台技能被哪些智能体显式白名单绑定（skills_custom=true）。
    每个智能体优先取 DRAFT，否则 PUBLISHED。与调用统计无关。
    """
    bindings = await AgentManagerService.get_skill_explicit_bindings(session)
    return {"status": "success", "bindings": bindings}


# Schema defined for creating new skills
class SkillCreateRequest(BaseModel):
    id: str
    name: str
    description: str

class FileEditRequest(BaseModel):
    path: str
    content: str

class SkillRenameRequest(BaseModel):
    new_skill_id: str


class SkillAssetCreateRequest(BaseModel):
    path: str
    type: Literal["file", "folder"]


class RejectPublicationRequest(BaseModel):
    comment: str


class RevokePublicationRequest(BaseModel):
    comment: str


EDITABLE_TEXT_EXTENSIONS = {
    ".md", ".py", ".js", ".ts", ".json", ".txt", ".yaml", ".yml",
    ".ini", ".conf", ".sql", ".sh",
}

#: 技能 ID 白名单与保留字：**唯一实现**在 skill_rename_service，这里只做别名，
#: 避免端点/重命名/发布服务三份口径各自漂移。运行时按同一正则过滤扫描结果，
#: 放行正则之外的名字只会造出「管理页看得见、模型永远看不到」的幽灵技能。
_SKILL_ID_PATTERN = SKILL_ID_PATTERN

#: 与 /skills 下更早注册的字面路由同名的保留字。放行会被路由遮蔽（例如名为 stats 的技能
#: 详情请求会命中统计接口），因此一律不允许创建。
_RESERVED_SKILL_IDS = RESERVED_SKILL_IDS


def validate_secure_skill_path(skill_id: str, relative_path: str = "") -> str:
    """严格校验 skill_id 与 relative_path，防御路径穿越。

    改为**白名单**而不是黑名单：此前只挡 ``/`` ``\\`` ``..``，于是 ``skill_id="."`` 能通过
    ——``os.path.join(SKILLS_DIR, ".")`` 就是技能库根目录本身，``commonpath`` 自比也相等，
    DELETE 端点随后 ``rmtree`` 会删掉**整个公共技能库**。白名单顺带挡住中文、点开头、
    空格等运行时本来就不认的名字。

    ID 的格式/长度/保留字校验收敛到 :func:`skill_rename_service.validate_skill_id`
    （create / rename / delete / 导入 / 上传全部走它），这里只负责路径边界。
    """
    try:
        skill_id = validate_skill_id(skill_id)
    except SkillRenameError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    skills_dir_abs = os.path.abspath(settings.SKILLS_DIR)
    skill_base_dir = os.path.abspath(os.path.join(skills_dir_abs, skill_id))

    # 白名单已排除 "."/".."，这里再显式要求严格位于技能库之下（纵深防御）
    if skill_base_dir == skills_dir_abs or os.path.commonpath(
        [skills_dir_abs, skill_base_dir]
    ) != skills_dir_abs:
        raise HTTPException(status_code=403, detail="安全拦截：技能路径越界")
        
    if not relative_path:
        return skill_base_dir
        
    target_path = os.path.abspath(os.path.join(skill_base_dir, relative_path.lstrip("/")))
    if os.path.commonpath([skill_base_dir, target_path]) != skill_base_dir:
         raise HTTPException(status_code=403, detail="安全拦截：文件子路径越界")
         
    return target_path

def validate_new_asset_path(skill_id: str, relative_path: str, asset_type: str) -> str:
    """校验新建资产路径，并返回技能根目录内的绝对路径。"""
    if not relative_path or not relative_path.strip():
        raise HTTPException(status_code=400, detail="资产名称不能为空")
    if os.path.isabs(relative_path) or relative_path.startswith("/"):
        raise HTTPException(status_code=400, detail="资产路径必须是相对路径")

    target_path = validate_secure_skill_path(skill_id, relative_path)
    if "\\" in relative_path:
        raise HTTPException(status_code=400, detail="资产路径不允许包含反斜杠")
    normalized_path = relative_path.replace("\\", "/")
    path_parts = [part for part in normalized_path.split("/") if part]
    if any(part in {".", ".."} for part in path_parts):
        raise HTTPException(status_code=400, detail="资产路径不允许包含相对目录段")
    if any(part.startswith(".") and part not in {".", ".."} for part in path_parts):
        raise HTTPException(status_code=400, detail="不允许创建隐藏文件或文件夹")

    if asset_type == "file":
        _, ext = os.path.splitext(target_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许新建可在线编辑的文本类文件")
    return target_path

def parse_skill_metadata(skill_id: str, skill_md_path: str) -> dict:
    """读取技能元数据（列表/详情/资源范围共用），解析委托给唯一实现。

    这里曾是**第三份** frontmatter 解析器：不剥 BOM、还自带一套「默认 name=id、
    description=暂无技能描述、enabled=true」的兜底，于是同一个 SKILL.md 在列表页与运行时
    可能给出不同结果（BOM 文件在运行时是禁用、在列表页永远是启用）。现在统一委托
    :func:`app.utils.skill_metadata.parse_skill_frontmatter`，只在其上补齐本页需要的字段
    与默认值语义：

    * ``path``：前端展示用的相对路径；
    * ``name``：缺省回落技能 ID；``description``：缺省回落「暂无技能描述」；
    * ``enabled``：缺省 ``"true"``（字符串，与 frontmatter 原生形态一致，判定统一走
      :func:`app.utils.skill_metadata.is_skill_enabled`）。

    代价是 YAML 块标量（``description: >-`` 多行）不再被拼接——该能力只有这份解析器有，
    而所有消费方（运行时、注入、列表）都以 skill_metadata 的单行口径为准，保留两套只会
    继续漂移。多行描述请写成单行或改用 ``\\n``。
    """
    meta = parse_skill_frontmatter(skill_id, skill_md_path)
    metadata = {
        "id": skill_id,
        "name": (meta.get("name") or skill_id),
        "description": (meta.get("description") or "").strip() or "暂无技能描述",
        "path": f"data/skills/{skill_id}",
        "enabled": str(meta.get("enabled", "true")),
    }
    return metadata

def get_path_modified_at(path: str) -> int:
    try:
        if os.path.exists(path):
            return int(os.path.getmtime(path))
    except OSError:
        pass
    return 0

def get_skill_modified_at(skill_dir: str, skill_md_path: str) -> int:
    return max(get_path_modified_at(skill_dir), get_path_modified_at(skill_md_path))

def get_file_tree(dir_path: str, base_path: str) -> list:
    """
    递归读取技能文件夹物理文件树结构
    """
    tree = []
    if not os.path.exists(dir_path):
        return tree
    try:
        for item in sorted(os.listdir(dir_path)):
            if item.startswith("."):
                continue  # 忽略隐藏文件
            full_path = os.path.join(dir_path, item)
            rel_path = os.path.relpath(full_path, base_path)
            is_dir = os.path.isdir(full_path)
            
            node = {
                "name": item,
                "path": rel_path,
                "is_dir": is_dir,
                "modified_at": get_path_modified_at(full_path),
            }
            if is_dir:
                node["children"] = get_file_tree(full_path, base_path)
            else:
                try:
                    node["size"] = os.path.getsize(full_path)
                except:
                    node["size"] = 0
            tree.append(node)
    except Exception as e:
        logger.error(f"[Skills] Error generating tree for {dir_path}: {e}")
    return tree

@router.get("", response_model=Dict[str, Any])
async def list_skills(
    user: Dict = Depends(require_api_key),
    agent_id: Optional[str] = Query(
        None,
        description="会话所属智能体；传入后 forbidden 标记并入该智能体的智能体级禁用",
    ),
):
    """
    扫描技能物理目录，解析 SKILL.md 返回技能列表。
    平台技能目录可供登录用户查询和使用；创建、编辑、删除等管理操作
    仍由 element:skills:admin 单独保护。

    每条带 ``forbidden`` 标记：被权限侧禁用（forbidden_skills）的公共技能**仍会返回**——
    前端要显示"技能还在、只是被管理员禁用"而不是让它凭空消失，但会据此置灰不可挂载。

    ``agent_id`` 决定 ``forbidden`` 的作用域：传了就是「全局 ∪ 该智能体」，不传只有全局。
    这个参数不能省——漏传时前端会把"只在该智能体被禁"的技能显示为可挂载，用户挂上后
    被后端 ``drop_forbidden_skill_items`` 静默丢弃（只写 warning 日志），界面全无反馈，
    用户只会以为技能坏了。带上它，技能中心在该智能体里直接置灰，连试都试不了。
    """
    skills_list = []
    if not os.path.exists(settings.SKILLS_DIR):
        return {"status": "success", "data": []}

    # 策略查询失败不能让整个技能列表 500：退化为"不标记"，可用性优先
    try:
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        forbidden_skill_ids = await get_forbidden_skill_ids(
            user.get("user_id") or user.get("id"), agent_id
        )
    except Exception:
        logger.warning("[Skills] Failed to resolve forbidden skills for the catalog", exc_info=True)
        forbidden_skill_ids = set()

    try:
        for item in sorted(os.listdir(settings.SKILLS_DIR)):
            item_path = os.path.join(settings.SKILLS_DIR, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                skill_md = os.path.join(item_path, "SKILL.md")
                meta = parse_skill_metadata(item, skill_md)
                meta["modified_at"] = get_skill_modified_at(item_path, skill_md)
                meta["forbidden"] = str(item) in forbidden_skill_ids
                skills_list.append(meta)
    except Exception as e:
        logger.error(f"[Skills] Failed to list skills: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success", "data": skills_list}


@router.get("/publication-requests", summary="列出待审核的平台技能发布申请")
async def list_skill_publication_requests(
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    return {"status": "success", "data": await list_pending_publications(session)}


@router.get("/publication-requests/{version_id}", summary="查看平台技能发布申请")
async def get_skill_publication_request(
    version_id: str,
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await get_publication_version(session, version_id=version_id, include_snapshot=True)
        snapshot_path = data.get("snapshot_path")
        if snapshot_path and os.path.isdir(snapshot_path):
            data["file_tree"] = get_file_tree(snapshot_path, snapshot_path)
            skill_md_path = os.path.join(snapshot_path, "SKILL.md")
            if os.path.exists(skill_md_path):
                with open(skill_md_path, "r", encoding="utf-8") as handle:
                    data["skill_md_content"] = handle.read()
        return {"status": "success", "data": data}
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/publication-requests/{version_id}/file", summary="读取发布申请快照内的文本文件")
async def get_skill_publication_request_file(
    version_id: str,
    path: str,
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    """读取审核快照里的单个文本文件，用于核验 scripts/ 等可执行内容。

    审核面板此前只看得到 SKILL.md，而技能包允许携带任意脚本与资源文件——等于
    「审核通过」会把从未被人看过的可执行代码发布进公共技能库，而界面文案却写着
    「审查文件 Diff 与代码合规性」。这里按**快照**（提交那一刻的不可变副本）读取，
    保证审的与将要发布的确实是同一份内容。

    安全约束：只读、只允许文本扩展名、单文件 1MB 上限，且路径在 ``realpath`` 解析后
    必须仍落在该 version 的快照目录内（软链接同样无法越界）。
    """
    try:
        data = await get_publication_version(session, version_id=version_id, include_snapshot=True)
        content = read_snapshot_text_file(data.get("snapshot_path") or "", path)
        return {"status": "success", "path": path, "content": content}
    except HTTPException:
        raise
    except SnapshotFileError as exc:
        # 越界 / 不存在 / 非文本 / 过大：都是调用方问题，不该报 500
        status_code = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[Skills] Failed to read publication snapshot file %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/publication-requests/{version_id}/approve", summary="通过并发布平台技能")
async def approve_skill_publication_request(
    version_id: str,
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        data = await approve_publication(session, version_id=version_id, reviewer=user)
        return {"status": "success", "data": data}
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/publication-requests/{version_id}/reject", summary="驳回平台技能发布申请")
async def reject_skill_publication_request(
    version_id: str,
    req: RejectPublicationRequest,
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    if not req.comment.strip():
        raise HTTPException(status_code=422, detail="驳回原因不能为空")
    try:
        data = await reject_publication(
            session,
            version_id=version_id,
            reviewer=user,
            comment=req.comment,
        )
        return {"status": "success", "data": data}
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/publications", summary="列出当前已发布的平台技能（供下架使用）")
async def list_publications(
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    """列出**当前已发布**的公共技能，权限沿用技能管理端的 ``element:skills:admin``。

    只返回「当前线上版本」那一条：``SUPERSEDED`` 历史版本、已驳回/已撤销/已下架的版本都
    不在可下架范围内，列出来只会让管理员点了得到 409。
    """
    return {"status": "success", "data": {"items": await list_published_publications(session)}}


@router.post("/publications/{version_id}/revoke", summary="下架已发布的平台技能")
async def revoke_skill_publication(
    version_id: str,
    req: RevokePublicationRequest,
    user: Dict = Depends(skill_publication_reviewer),
    session: AsyncSession = Depends(get_db_session),
):
    """下架：撤回已发布版本，技能从公共库消失，但保留可重新发布的能力。

    与删除的区别：**保留**发布谱系（``platform_skill_id`` / ``snapshot_path``），作者可以
    在原技能上重新提交、重新审核，复用同一个平台技能 ID 重新物化目录。

    返回该发布谱系的 payload 加上 ``synced`` / ``warnings``（与重命名、删除同一风格）：
    下架会让公共技能目录真的消失，因此智能体白名单与会话挂载里的引用必须同步清理，
    清理不了的要在 ``warnings`` 里如实告知，不能静默留下断链。
    """
    if not (req.comment or "").strip():
        raise HTTPException(status_code=422, detail="下架原因不能为空")
    try:
        data = await revoke_published_version(
            session,
            version_id=version_id,
            reviewer=user,
            comment=req.comment,
        )
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublicationConflictError as exc:
        # 重复下架 / 非当前发布版本 / 未发布：都是状态冲突，必须 409 而不是 500
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    platform_skill_id = data.get("platform_skill_id")
    report = await remove_skill_references(
        session, skill_id=platform_skill_id or "", scope="global", actor=user
    )
    data.update({"synced": report.get("synced", {}), "warnings": report.get("warnings", [])})
    return {
        "status": "success",
        "data": data,
        "message": f"平台技能 {data.get('name') or platform_skill_id} 已下架",
    }


@router.get("/{skill_id}/preview", response_model=Dict[str, Any])
async def preview_skill_md(
    skill_id: str,
    user: Dict = Depends(skill_platform_admin),
):
    """
    只读预览 SKILL.md，供聊天页技能抽屉使用；仅需 API Key，不要求技能管理菜单权限。
    """
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="技能未找到")

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
                "skill_md_content": skill_md_content,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to preview SKILL.md for {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("", response_model=Dict[str, Any])
async def create_skill(
    req: SkillCreateRequest,
    user: Dict = Depends(skill_platform_admin)
):
    """
    在线新建技能目录并初始化默认 SKILL.md 模版
    """
    try:
        skill_dir = validate_secure_skill_path(req.id)
        if os.path.exists(skill_dir):
            raise HTTPException(status_code=400, detail="该技能ID已存在")
            
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
            
        return {"status": "success", "message": f"技能 {req.id} 创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to create skill {req.id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{skill_id}", response_model=Dict[str, Any])
async def get_skill_detail(
    skill_id: str,
    user: Dict = Depends(skill_platform_admin)
):
    """
    获取指定技能的详情，包括 SKILL.md 文本和物理目录树
    """
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="技能未找到")
            
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
                "skill_md_content": skill_md_content,
                "file_tree": file_tree
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to fetch detail for {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{skill_id}/files", response_model=Dict[str, Any])
async def get_skill_file_content(
    skill_id: str,
    path: str,
    user: Dict = Depends(skill_platform_admin)
):
    """
    在线读取指定技能目录下的文本文件内容 (如脚本文件)
    """
    try:
        file_path = validate_secure_skill_path(skill_id, path)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")
            
        # 安全阻断：禁止读取非文本类型
        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线读取文本类配置文件及脚本")
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        return {"status": "success", "content": content}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to read file {path} in {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{skill_id}/export", summary="导出平台技能为 zip 压缩包")
async def export_skill_archive(
    skill_id: str,
    user: Dict = Depends(skill_platform_admin),
):
    """把整个技能目录打包下载，便于备份或迁移到其它环境/账号。"""
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="技能不存在")
        payload = build_skill_archive(skill_dir, arc_prefix=f"{skill_id}/")
        headers = {
            "Content-Disposition": f'attachment; filename="{skill_id}.zip"',
            "Content-Length": str(len(payload)),
        }
        return Response(content=payload, media_type="application/zip", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] Failed to export skill %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{skill_id}/download", summary="下载平台技能内的单个文件")
async def download_skill_file(
    skill_id: str,
    path: str,
    user: Dict = Depends(skill_platform_admin),
):
    """下载技能包内的单个文件（脚本、资源、说明文档等）。"""
    try:
        file_path = validate_secure_skill_path(skill_id, path)
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
        logger.error("[Skills] Failed to download file %s in %s: %s", path, skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{skill_id}/files", response_model=Dict[str, Any])
async def edit_skill_file(
    skill_id: str,
    req: FileEditRequest,
    user: Dict = Depends(skill_platform_admin)
):
    """
    编辑修改指定技能目录下的文本文件内容 (如 SKILL.md 或脚本)
    """
    try:
        file_path = validate_secure_skill_path(skill_id, req.path)
        
        # 安全阻断：禁止操作非文本类型
        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线编辑保存文本类配置文件及脚本")
            
        # 限制文件大小在 2MB 以内
        if len(req.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小超出 2MB 在线编辑限制")
            
        # 确保父级文件夹存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
            
        return {"status": "success", "message": "保存成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to edit file {req.path} in {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{skill_id}/files", response_model=Dict[str, Any])
async def create_skill_asset(
    skill_id: str,
    req: SkillAssetCreateRequest,
    user: Dict = Depends(skill_platform_admin)
):
    """在技能目录内显式创建空文本文件或文件夹。"""
    try:
        target_path = validate_new_asset_path(skill_id, req.path, req.type)
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
        logger.error(f"[Skills] Failed to create asset {req.path} in {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{skill_id}/upload", response_model=Dict[str, Any])
async def upload_skill_file(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: Dict = Depends(skill_platform_admin)
):
    """
    上传物理文件（如 Python 辅助脚本）到指定的技能子目录下，单文件上限 10MB
    """
    try:
        sub_dir = folder if folder else ""
        file_name = file.filename if file.filename else "uploaded_file"
        target_path = validate_secure_skill_path(skill_id, os.path.join(sub_dir, file_name))
        
        # 强行校验文件大小 (10MB)
        # 通过在内存中读取一部分或整体判定大小
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
        logger.error(f"[Skills] Failed to upload file in {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{skill_id}/files", response_model=Dict[str, Any])
async def delete_skill_file(
    skill_id: str,
    path: str,
    user: Dict = Depends(skill_platform_admin)
):
    """
    删除技能文件夹下的单个指定文件或子文件夹
    """
    try:
        target_path = validate_secure_skill_path(skill_id, path)
        if not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="目标文件不存在")
            
        # 安全防御：禁止直接删除技能根文件 SKILL.md
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
        logger.error(f"[Skills] Failed to delete file {path} in {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{skill_id}", response_model=Dict[str, Any])
async def delete_entire_skill(
    skill_id: str,
    user: Dict = Depends(skill_platform_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """彻底注销并递归清空删除技能根目录，同时清理三处引用。

    删除与重命名面对的是**同一套引用**，只做 ``shutil.rmtree`` 会留下三类断链：

    * ``ai_agent_versions.skills`` 白名单里留着已消失的 ID（管理员看不出智能体为什么少一个技能）；
    * Redis 会话挂载里的技能 chip 指向不存在的技能（用户点开是空的）；
    * ``skill_publications`` 里仍有一条指向不存在平台技能的「已发布」记录。

    发布血缘的处理取舍是**标记下架而不是物理删除**（见
    :func:`skill_publication_service.mark_platform_skill_publications_revoked` 的说明）：
    删记录会毁掉审核审计轨迹，并让作者重新提交时拿到一个全新的平台技能 ID（唯一索引），
    历史发布、智能体白名单、会话挂载就再也回不来了。标记下架则保留
    ``platform_skill_id`` 与 ``snapshot_path``，重新提交 → 重新审核即可复用同一 ID 重新物化。
    **快照目录刻意保留**：它是重新上架的唯一来源，删掉技能就真的不可恢复了。

    返回与重命名同样的 ``synced`` / ``warnings``：主操作（删目录）不回滚，引用同步尽力而为
    并如实回报，不静默成功。
    """
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="技能目录不存在")

        shutil.rmtree(skill_dir)
        # 少数老单测直接调用本函数（拿到的是 Depends 占位对象而非真实会话），
        # remove_skill_references 用鸭子类型识别并跳过依赖数据库的同步、写进 warnings。
        report = await remove_skill_references(
            session,
            skill_id=skill_id,
            scope="global",
            actor=user,
        )
        return {
            "status": "success",
            "data": report,
            "message": f"技能 {skill_id} 已被物理彻底移除",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to delete skill directory {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


#: 压缩包解压后的总量与文件数上限。上传本身已限 100MB，但高压缩比归档（zip bomb）
#: 可以把它膨胀成数 GB——个人技能上传是普通用户可用的入口，因此必须卡住解压侧。
#: 按上传上限的 5 倍留余量：正常技能包里压缩率本来就高，卡太紧会误伤。
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_EXTRACTED_FILES = 2000


def _guard_extract_budget(written_bytes: int, written_files: int) -> None:
    """解压预算校验：超限即中止（避免高压缩比归档撑爆磁盘）。"""
    if written_files > MAX_EXTRACTED_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"压缩包内文件数超过上限（{MAX_EXTRACTED_FILES} 个）",
        )
    if written_bytes > MAX_EXTRACTED_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "压缩包解压后体积超过上限"
                f"（{MAX_EXTRACTED_BYTES // (1024 * 1024)}MB），请拆分后上传"
            ),
        )


def safe_extract_archive(content: bytes, filename: str, target_dir: str):
    """
    安全解包 ZIP 或 TAR 压缩文件到指定的目标目录下。
    对解压出来的所有路径进行防路径穿越 (Zip Slip) 安全校验。
    """
    target_dir_abs = os.path.abspath(target_dir)
    os.makedirs(target_dir_abs, exist_ok=True)
    
    file_like = BytesIO(content)
    lower_filename = filename.lower()
    
    if lower_filename.endswith(".zip"):
        try:
            with zipfile.ZipFile(file_like) as zf:
                written_bytes = 0
                written_files = 0
                for member in zf.infolist():
                    # 规范化路径分割符并分割
                    parts = [p for p in member.filename.replace("\\", "/").split("/") if p]
                    if any(part == ".." for part in parts):
                        raise HTTPException(status_code=400, detail="压缩包内包含非法越权路径")
                    if any(part.startswith(".") for part in parts):
                        continue
                    if "__MACOSX" in parts:
                        continue
                    
                    # 防范路径穿越：拼接并验证
                    target_path = os.path.abspath(os.path.join(target_dir_abs, *parts))
                    if os.path.commonpath([target_dir_abs, target_path]) != target_dir_abs:
                        raise HTTPException(status_code=400, detail="压缩包内包含非法越权路径")
                    
                    if member.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                    else:
                        # 先按归档里声明的大小拦一道（少写盘），写盘时再按实际字节复核
                        written_files += 1
                        written_bytes += max(0, int(member.file_size or 0))
                        _guard_extract_budget(written_bytes, written_files)
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        actual = 0
                        with zf.open(member) as source, open(target_path, "wb") as target:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                actual += len(chunk)
                                if written_bytes - int(member.file_size or 0) + actual > MAX_EXTRACTED_BYTES:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=(
                                            "压缩包解压后体积超过上限"
                                            f"（{MAX_EXTRACTED_BYTES // (1024 * 1024)}MB），请拆分后上传"
                                        ),
                                    )
                                target.write(chunk)
                        written_bytes += actual - max(0, int(member.file_size or 0))
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="无效或损坏的 ZIP 文件")
            
    elif lower_filename.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz")):
        try:
            with tarfile.open(fileobj=file_like, mode="r:*") as tf:
                written_bytes = 0
                written_files = 0
                for member in tf.getmembers():
                    parts = [p for p in member.name.replace("\\", "/").split("/") if p]
                    if any(part == ".." for part in parts):
                        raise HTTPException(status_code=400, detail="压缩包内包含非法越权路径")
                    if any(part.startswith(".") for part in parts):
                        continue
                    if "__MACOSX" in parts:
                        continue
                    
                    target_path = os.path.abspath(os.path.join(target_dir_abs, *parts))
                    if os.path.commonpath([target_dir_abs, target_path]) != target_dir_abs:
                        raise HTTPException(status_code=400, detail="压缩包内包含非法越权路径")
                    
                    if member.isdir():
                        os.makedirs(target_path, exist_ok=True)
                    elif member.isreg():  # 仅解压普通文件（symlink/hardlink 一律不落盘）
                        written_files += 1
                        written_bytes += max(0, int(member.size or 0))
                        _guard_extract_budget(written_bytes, written_files)
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        fileobj = tf.extractfile(member)
                        if fileobj:
                            actual = 0
                            with open(target_path, "wb") as target:
                                while True:
                                    chunk = fileobj.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    actual += len(chunk)
                                    if (
                                        written_bytes - max(0, int(member.size or 0)) + actual
                                        > MAX_EXTRACTED_BYTES
                                    ):
                                        raise HTTPException(
                                            status_code=400,
                                            detail=(
                                                "压缩包解压后体积超过上限"
                                                f"（{MAX_EXTRACTED_BYTES // (1024 * 1024)}MB），请拆分后上传"
                                            ),
                                        )
                                    target.write(chunk)
                            written_bytes += actual - max(0, int(member.size or 0))
        except tarfile.TarError:
            raise HTTPException(status_code=400, detail="无效或损坏的 TAR 归档文件")
    else:
        raise HTTPException(status_code=400, detail="不支持的压缩格式。仅支持 .zip, .tar, .tar.gz, .tgz, .tar.bz2")


#: 导出/打包时一律跳过的目录与文件：隐藏项（含 .seed 快照）、Python 缓存
_EXPORT_SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git"})
_EXPORT_SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd")


def _export_should_skip(name: str) -> bool:
    return name.startswith(".") or name in _EXPORT_SKIP_DIRS or name.endswith(_EXPORT_SKIP_SUFFIXES)


def build_skill_archive(skill_dir: str, *, arc_prefix: str = "") -> bytes:
    """把技能目录打包为 zip 字节流，路径为包内相对路径。

    导出是「技能可以搬走、可以备份」的基本能力：此前平台只进不出，用户想复用只能
    手动去磁盘上找目录。隐藏项与 Python 缓存不打包（``.seed`` 是运行时快照，
    ``__pycache__``/``*.pyc`` 是编译产物，都属于「本来就不该跟着走」的东西）。
    """
    skill_dir = os.path.abspath(skill_dir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, dirnames, filenames in os.walk(skill_dir):
            dirnames[:] = sorted(d for d in dirnames if not _export_should_skip(d))
            for filename in sorted(filenames):
                if _export_should_skip(filename):
                    continue
                full_path = os.path.join(current, filename)
                if not os.path.isfile(full_path):
                    continue
                relative = os.path.relpath(full_path, skill_dir).replace(os.sep, "/")
                archive.write(full_path, arcname=f"{arc_prefix}{relative}")
    return buffer.getvalue()


def atomic_import_skill_package(content: bytes, filename: str, skill_dir: str) -> None:
    """把压缩包原子地导入为技能目录（解压 → 校验 → ``os.replace`` 替换）。

    此前两侧 import 端点都是「先 ``rmtree`` 旧技能、再解包、失败再 ``rmtree``」：一个损坏
    的包、或缺 ``SKILL.md`` 的包，会让**新旧两份同时消失**，且没有任何备份。这里改成与
    :func:`app.services.skill_publication_service.materialize_snapshot` 同样的 staged +
    backup 模式：校验全部通过后才替换，任何一步失败都保留原技能。

    ``SKILL.md`` 一律按**精确大小写**校验：消费方（resolver / 注入 / 沙箱）都只认
    ``SKILL.md``，放行 ``skill.md`` 会造出「列表里有、处处读不到」的幽灵技能。
    """
    skill_dir = os.path.abspath(skill_dir)
    parent = os.path.dirname(skill_dir)
    os.makedirs(parent, exist_ok=True)
    staged = tempfile.mkdtemp(prefix=f".{os.path.basename(skill_dir)}-import-", dir=parent)
    backup: str | None = None
    try:
        safe_extract_archive(content, filename, staged)

        # 平铺外层文件夹：解包后只有一个子目录且其内含 SKILL.md 时，把内容提上来
        visible_items = [i for i in os.listdir(staged) if not i.startswith(".")]
        if len(visible_items) == 1:
            single_sub = os.path.join(staged, visible_items[0])
            if os.path.isdir(single_sub) and os.path.isfile(
                os.path.join(single_sub, "SKILL.md")
            ):
                for item in os.listdir(single_sub):
                    shutil.move(os.path.join(single_sub, item), os.path.join(staged, item))
                os.rmdir(single_sub)

        if not os.path.isfile(os.path.join(staged, "SKILL.md")):
            raise HTTPException(
                status_code=400,
                detail="导入失败：压缩包根目录下必须包含核心规范文件 SKILL.md",
            )

        if os.path.exists(skill_dir):
            backup = f"{skill_dir}.backup-{uuid.uuid4().hex}"
            os.replace(skill_dir, backup)
        os.replace(staged, skill_dir)
        staged = ""
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if staged and os.path.exists(staged):
            shutil.rmtree(staged, ignore_errors=True)
        if backup and os.path.exists(backup):
            # 回滚：把原技能放回去，宁可留下备份也不丢用户数据
            if os.path.exists(skill_dir):
                shutil.rmtree(skill_dir, ignore_errors=True)
            os.replace(backup, skill_dir)
        raise


@router.post("/{skill_id}/upload-archive", response_model=Dict[str, Any])
async def upload_skill_archive(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: Dict = Depends(skill_platform_admin)
):
    """
    上传 zip/tar 压缩包并解压到指定技能的子目录下，单个压缩包上限 100MB
    """
    try:
        sub_dir = folder if folder else ""
        target_dir = validate_secure_skill_path(skill_id, sub_dir)
        
        content = await file.read()
        if len(content) > SKILL_PACKAGE_MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"上传的压缩文件大小不能超出 {SKILL_PACKAGE_MAX_MB}MB 限制",
            )
            
        file_name = file.filename if file.filename else "archive.zip"
        safe_extract_archive(content, file_name, target_dir)
        
        return {"status": "success", "message": f"技能压缩包 {file_name} 上传解压成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to upload and extract archive for skill {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import", response_model=Dict[str, Any])
async def import_skill_package(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    user: Dict = Depends(skill_platform_admin)
):
    """
    上传压缩包导入为新技能，解压至技能目录。解包后必须包含 SKILL.md。
    """
    try:
        filename = file.filename if file.filename else "imported_skill.zip"
        base_name, _ = os.path.splitext(filename)
        if base_name.lower().endswith(".tar"):
            base_name, _ = os.path.splitext(base_name)
            
        skill_id = re.sub(r"[^a-zA-Z0-9_\-]", "", base_name).strip()
        if not skill_id:
            raise HTTPException(status_code=400, detail="无法从文件名中提取合法的技能ID")
            
        skill_dir = validate_secure_skill_path(skill_id)
        
        if os.path.exists(skill_dir) and not overwrite:
            raise HTTPException(status_code=400, detail=f"技能 {skill_id} 已存在，请开启 overwrite 以覆盖导入")
            
        content = await file.read()
        if len(content) > SKILL_PACKAGE_MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"导入文件大小超出 {SKILL_PACKAGE_MAX_MB}MB 限制",
            )
            
        atomic_import_skill_package(content, filename, skill_dir)
            
        return {"status": "success", "message": f"技能 {skill_id} 导入成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to import skill package: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{skill_id}/rename", summary="重命名平台技能并同步引用")
async def rename_platform_skill(
    skill_id: str,
    req: SkillRenameRequest,
    user: Dict = Depends(skill_platform_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """重命名平台技能。

    技能 ID 就是目录名，同时被智能体的自定义技能白名单、以及会话里已挂载的技能
    chip（Redis）引用。这里一次性同步这三处，并把同步结果如实返回；重命名是低频
    管理操作，静默断链（技能还在但哪都挂不上）比不支持重命名更糟。
    """
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="技能不存在")
        report = await rename_skill(
            session,
            current_dir=skill_dir,
            old_skill_id=skill_id,
            new_skill_id=req.new_skill_id,
            scope="global",
        )
        return {
            "status": "success",
            "data": report,
            "message": f"技能已重命名为 {report['skill_id']}",
        }
    except SkillRenameError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] Failed to rename skill %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{skill_id}/toggle", summary="启用或禁用平台技能")
async def toggle_skill(
    skill_id: str,
    enabled: bool,
    user: dict = Depends(skill_platform_admin),
):
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="技能不存在")
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
        logger.error(f"[Skills] Failed to toggle skill {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

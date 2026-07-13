import os
import shutil
import logging
import re
import zipfile
import tarfile
from io import BytesIO
from typing import List, Dict, Any, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import require_api_key, require_permission

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/stats", summary="获取技能调用统计数据")
async def get_skills_stats(
    user_info: dict = Depends(require_api_key)
):
    from app.services.ai.skills_stats_service import skills_stats_service
    return await skills_stats_service.get_stats()

# Schema defined for creating new skills
class SkillCreateRequest(BaseModel):
    id: str
    name: str
    description: str

class FileEditRequest(BaseModel):
    path: str
    content: str

class SkillAssetCreateRequest(BaseModel):
    path: str
    type: Literal["file", "folder"]

EDITABLE_TEXT_EXTENSIONS = {
    ".md", ".py", ".js", ".ts", ".json", ".txt", ".yaml", ".yml",
    ".ini", ".conf", ".sql", ".sh",
}

def validate_secure_skill_path(skill_id: str, relative_path: str = "") -> str:
    """
    严格校验 skill_id 和 relative_path，防御路径穿越漏洞。
    如果检测到安全红线越界攻击，立即抛出 403 异常。
    """
    if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
        raise HTTPException(status_code=400, detail="非法智能体技能ID格式")
        
    skills_dir_abs = os.path.abspath(settings.SKILLS_DIR)
    skill_base_dir = os.path.abspath(os.path.join(skills_dir_abs, skill_id))
    
    if os.path.commonpath([skills_dir_abs, skill_base_dir]) != skills_dir_abs:
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
    """
    无三方依赖正则提取 SKILL.md Frontmatter 元数据
    """
    metadata = {
        "id": skill_id, 
        "name": skill_id, 
        "description": "暂无技能描述",
        "path": f"data/skills/{skill_id}"
    }
    if not os.path.exists(skill_md_path):
        return metadata
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            for line in yaml_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    metadata[k.strip().lower()] = v.strip().strip('"').strip("'")
    except Exception as e:
        logger.error(f"[Skills] Failed to parse SKILL.md for {skill_id}: {e}")
    return metadata

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
                "is_dir": is_dir
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
):
    """
    扫描技能物理目录，解析 SKILL.md 返回技能列表。
    仅校验 API Key（已登录）；不按 menu:skills_management 或用户角色过滤条目。
    技能管理能力由菜单权限与 POST/PUT/DELETE 等写接口单独控制。
    """
    skills_list = []
    if not os.path.exists(settings.SKILLS_DIR):
        return {"status": "success", "data": []}
        
    try:
        for item in sorted(os.listdir(settings.SKILLS_DIR)):
            item_path = os.path.join(settings.SKILLS_DIR, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                skill_md = os.path.join(item_path, "SKILL.md")
                meta = parse_skill_metadata(item, skill_md)
                skills_list.append(meta)
    except Exception as e:
        logger.error(f"[Skills] Failed to list skills: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success", "data": skills_list}

@router.get("/{skill_id}/preview", response_model=Dict[str, Any])
async def preview_skill_md(
    skill_id: str,
    user: Dict = Depends(require_api_key),
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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

@router.put("/{skill_id}/files", response_model=Dict[str, Any])
async def edit_skill_file(
    skill_id: str,
    req: FileEditRequest,
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
):
    """
    彻底注销并递归清空删除技能根目录
    """
    try:
        skill_dir = validate_secure_skill_path(skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="技能目录不存在")
            
        shutil.rmtree(skill_dir)
        return {"status": "success", "message": f"技能 {skill_id} 已被物理彻底移除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to delete skill directory {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with zf.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="无效或损坏的 ZIP 文件")
            
    elif lower_filename.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz")):
        try:
            with tarfile.open(fileobj=file_like, mode="r:*") as tf:
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
                    elif member.isreg():  # 仅解压普通文件
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        fileobj = tf.extractfile(member)
                        if fileobj:
                            with open(target_path, "wb") as target:
                                shutil.copyfileobj(fileobj, target)
        except tarfile.TarError:
            raise HTTPException(status_code=400, detail="无效或损坏的 TAR 归档文件")
    else:
        raise HTTPException(status_code=400, detail="不支持的压缩格式。仅支持 .zip, .tar, .tar.gz, .tgz, .tar.bz2")


@router.post("/{skill_id}/upload-archive", response_model=Dict[str, Any])
async def upload_skill_archive(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
):
    """
    上传 zip/tar 压缩包并解压到指定技能的子目录下，单文件上限 20MB
    """
    try:
        sub_dir = folder if folder else ""
        target_dir = validate_secure_skill_path(skill_id, sub_dir)
        
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="上传的压缩文件大小不能超出 20MB 限制")
            
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
    user: Dict = Depends(require_permission("menu", "menu:skills_management"))
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
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="导入文件大小超出 20MB 限制")
            
        if os.path.exists(skill_dir):
            shutil.rmtree(skill_dir)
            
        os.makedirs(skill_dir, exist_ok=True)
        
        try:
            safe_extract_archive(content, filename, skill_dir)
            
            # 平铺外层文件夹：若解包后只有一个子目录，且该子目录内含 SKILL.md
            top_items = os.listdir(skill_dir)
            visible_items = [i for i in top_items if not i.startswith(".")]
            if len(visible_items) == 1:
                single_sub = os.path.join(skill_dir, visible_items[0])
                if os.path.isdir(single_sub):
                    has_inner_skill_md = False
                    for item in os.listdir(single_sub):
                        if item.upper() == "SKILL.MD" and os.path.isfile(os.path.join(single_sub, item)):
                            has_inner_skill_md = True
                            break
                    if has_inner_skill_md:
                        for item in os.listdir(single_sub):
                            shutil.move(os.path.join(single_sub, item), os.path.join(skill_dir, item))
                        os.rmdir(single_sub)
            
            # 最终检验是否存在 SKILL.md
            has_skill_md = False
            for item in os.listdir(skill_dir):
                if item.upper() == "SKILL.MD" and os.path.isfile(os.path.join(skill_dir, item)):
                    has_skill_md = True
                    break
                    
            if not has_skill_md:
                shutil.rmtree(skill_dir)
                raise HTTPException(status_code=400, detail="导入失败：压缩包根目录下必须包含核心规范文件 SKILL.md")
                
        except Exception as e:
            if os.path.exists(skill_dir):
                shutil.rmtree(skill_dir)
            raise e
            
        return {"status": "success", "message": f"技能 {skill_id} 导入成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Skills] Failed to import skill package: {e}")
        raise HTTPException(status_code=500, detail=str(e))

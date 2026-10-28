"""技能广场（只读）。

一屏「有什么技能值得装」的目录页：数据来自 ``app/services/ai/skill_gallery``
的 ``catalog.json``（代码资产，不进数据库），**不提供安装文件**——卡片与详情只做
介绍，安装通过复制提示词交给对话里的智能体在沙箱执行（装完再由
``import_skill_from_sandbox`` 收进个人技能库）。

「已安装」状态是**按目录名比对**出来的：导入时的技能 ID 来自目录名（npx 装出来是
``.agents/skills/<slug>``，slug 与广场的 id 同口径），所以在个人技能库或平台技能库里
存在同名目录即视为已安装。这不是严格的版本追踪，够用且不会说谎——只判断「有没有」，
不声称「是不是最新版」。

路由前缀是 ``/skills/gallery``，而 ``skills.py`` 里有 ``GET /skills/{skill_id}``。
FastAPI 按注册顺序匹配，本模块的 ``include_router`` 必须排在 ``skills`` **之前**，
否则 ``/skills/gallery`` 会被那条路径参数路由吞掉（见 ``api.py`` 的注释）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.ai.skill_gallery import catalog as gallery_catalog
from app.services.ai.skill_resolver import (
    get_user_personal_skills_dir,
    iter_personal_skill_dirs,
    platform_skill_id_set,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _can_manage_platform_skills(user: Dict[str, Any], db: AsyncSession) -> bool:
    """当前用户能否管理平台技能（``element:skills:admin``）。

    与 ``require_permission`` / 技能工作台同一口径：admin 角色 bypass，其余查授权。
    查询失败按**无权限**裁剪——这个字段决定「安装到平台」那段提示词发不发，
    宁可少发也不要把写着「我确认有管理员权限」的话交给无权限的人。
    """
    if str(user.get("role") or "").strip() == "admin":
        return True
    try:
        user_id = int(user.get("user_id"))
    except (TypeError, ValueError):
        return False
    try:
        from app.services.permission_service import PermissionService

        return await PermissionService(db).check_permission(
            user_id, "element", "element:skills:admin"
        )
    except Exception as exc:  # noqa: BLE001 - 权限查询失败不该让广场打不开
        logger.warning("[SkillGallery] 查询平台技能管理权限失败，按无权限裁剪：%s", exc)
        return False


def _strip_platform_prompt(skill: Dict[str, Any]) -> Dict[str, Any]:
    """去掉仅管理员可见的全局安装提示词。"""
    skill.pop("install_prompt_global", None)
    return skill


def _platform_skill_ids() -> set[str]:
    """平台公共技能目录下的技能 ID 集合。

    直接复用 ``platform_skill_id_set``：那个函数已经把「目录名即 ID、跳过点目录」
    的口径定死了，广场再写一份 os.listdir 只会让两处慢慢漂移。
    """
    try:
        return platform_skill_id_set()
    except Exception as exc:  # noqa: BLE001 - 读不到就当作空库，不该让广场打不开
        logger.warning("[SkillGallery] 读取平台技能目录失败：%s", exc)
        return set()


def _personal_skill_ids(user: Dict[str, Any]) -> set[str]:
    """当前用户个人技能库里的技能 ID 集合。"""
    try:
        root = get_user_personal_skills_dir(user)
    except Exception as exc:  # noqa: BLE001 - 解析不了工作区不该让广场打不开
        logger.warning("[SkillGallery] 解析个人技能目录失败：%s", exc)
        return set()
    if not root or not os.path.isdir(root):
        return set()
    try:
        return {skill_id for skill_id, _path in iter_personal_skill_dirs(root)}
    except OSError as exc:
        logger.warning("[SkillGallery] 遍历个人技能目录失败：%s", exc)
        return set()


def _installed_map(user: Dict[str, Any]) -> Dict[str, str]:
    """返回 ``{skill_id: "personal" | "global"}``。

    同一个 ID 两处都有时按 **personal** 记：个人技能覆盖公共技能是平台的既有优先级，
    徽标要跟着实际生效的那份走。
    """
    installed: Dict[str, str] = {skill_id: "global" for skill_id in _platform_skill_ids()}
    for skill_id in _personal_skill_ids(user):
        installed[skill_id] = "personal"
    return installed


@router.get("", summary="技能广场目录")
async def list_gallery_skills(
    user: Dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session, scope="function"),
) -> Dict[str, Any]:
    """返回全部技能卡片与分类，供前端渲染网格。

    每条带 ``installed``（``"personal"`` / ``"global"`` / ``None``），前端据此显示
    「已在你的技能库」徽标，避免用户对已经装过的技能重复执行安装。
    """
    try:
        skills: List[Dict[str, Any]] = gallery_catalog.list_skills()
    except Exception as exc:  # noqa: BLE001 - 目录坏了不该 500，前端应能显示空态
        logger.error("[SkillGallery] 读取技能广场目录失败：%s", exc)
        raise HTTPException(status_code=500, detail="技能广场目录读取失败，请稍后重试")

    installed = _installed_map(user)
    can_manage_platform = await _can_manage_platform_skills(user, db)
    for skill in skills:
        skill["installed"] = installed.get(skill["id"])
        if not can_manage_platform:
            _strip_platform_prompt(skill)

    return {
        "skills": skills,
        "categories": gallery_catalog.list_categories(),
        "source_note": gallery_catalog.source_note(),
    }


@router.get("/{skill_id}", summary="技能广场技能详情")
async def get_gallery_skill(
    skill_id: str,
    user: Dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session, scope="function"),
) -> Dict[str, Any]:
    """返回单个技能的元数据与介绍正文（``intro``）。

    ``install_prompt_global`` 按 ``element:skills:admin`` 裁剪：那段提示词里写着
    「我确认有管理员权限」，原样下发会让模型把无权限用户的请求当成已授权。
    """
    detail: Optional[Dict[str, Any]] = gallery_catalog.get_skill(skill_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"技能广场里没有技能 {skill_id}")
    detail["installed"] = _installed_map(user).get(detail["id"])
    if not await _can_manage_platform_skills(user, db):
        _strip_platform_prompt(detail)
    return detail

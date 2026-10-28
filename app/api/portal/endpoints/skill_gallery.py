"""技能广场（只读 + 行为事件）。

一屏「有什么技能值得装」的目录页：数据来自 ``app/services/ai/skill_gallery``
的 ``catalog.json``（代码资产，不进数据库），**不提供安装文件**——卡片与详情只做
介绍，安装通过复制提示词交给对话里的智能体在沙箱执行（装完再由
``import_skill_from_sandbox`` 收进个人技能库）。

「已安装」状态是**按目录名比对**出来的：导入时的技能 ID 来自目录名（npx 装出来是
``.agents/skills/<slug>``，slug 与广场的 id 同口径），所以在个人技能库或平台技能库里
存在同名目录即视为已安装。这不是严格的版本追踪，够用且不会说谎——只判断「有没有」，
不声称「是不是最新版」；``installed_version`` 读的是已装技能 ``SKILL.md`` 的
frontmatter，读不到就留空，前端显示「未知」。

三个接口层面的取舍：

1. **列表不下发详情专用字段与平台提示词**。两份提示词各约 1.5KB，21 条就是 60KB，
   而卡片只需要摘要、标签与一份个人提示词；平台提示词只有详情页的「安装到平台」用得到。
2. **列表支持 ETag**。目录是静态代码资产，前端切 tab 会重复拉取，304 能省掉整次传输。
3. **已安装扫描缓存 30 秒**。判定要遍历平台库与个人库两个目录，同一用户连续翻列表
   不必重复扫；个人库通常只有个位数技能，30 秒的陈旧窗口对徽标显示无影响。

路由前缀是 ``/skills/gallery``，而 ``skills.py`` 里有 ``GET /skills/{skill_id}``。
FastAPI 按注册顺序匹配，本模块的 ``include_router`` 必须排在 ``skills`` **之前**，
否则 ``/skills/gallery`` 会被那条路径参数路由吞掉（见 ``api.py`` 的注释）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
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

#: 列表只下发卡片字段。``install_prompt_global`` 一律不进列表——列表上没有
#: 「安装到平台」按钮，管理员那份提示词只在详情页按权限下发。
_LIST_DROP_FIELDS = (
    "install_prompt_global",
    "intro_doc",
    "source_url",
    "license",
    "updated_at",
    "repository",
    "repository_url",
    "highlights",
)

#: 已安装扫描缓存 TTL：目录名比对要遍历两个技能根，同一用户连续翻列表没必要重复扫。
#:
#: 只给 5 秒：目录扫描本身很便宜（个人库通常个位数技能），但缓存会连带 ETag 一起
#: 变陈旧——用户刚在会话里装完技能、切回广场，30 秒的窗口内还会看到「未安装」。
#: 5 秒足够挡掉「首屏 + 切 tab」的重复扫描，又不至于让徽标明显滞后。
_INSTALLED_CACHE_TTL_SECONDS = 5.0
_INSTALLED_CACHE_MAX_ENTRIES = 256
_installed_cache: Dict[str, Tuple[float, Dict[str, Tuple[str, str, str]]]] = {}

#: 允许上报的行为事件（白名单，避免前端把任意字符串写进日志）。
_SKILL_EVENT_NAMES = frozenset({"detail_open", "prompt_copy", "platform_prompt_copy"})


class SkillGalleryEvent(BaseModel):
    """技能广场行为事件。``event`` 取 ``_SKILL_EVENT_NAMES`` 白名单。"""

    event: str = Field(..., max_length=64)
    skill_id: str = Field("", max_length=128)
    scope: str = Field("", max_length=32)


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


def _platform_skill_dirs() -> List[Tuple[str, str]]:
    """平台公共技能目录下的 ``(skill_id, skill_dir)``。

    目录名口径直接复用 ``platform_skill_id_set``，广场不再写一份 ``os.listdir``，
    避免两处判定慢慢漂移。
    """
    try:
        skill_ids = platform_skill_id_set()
    except Exception as exc:  # noqa: BLE001 - 读不到就当作空库，不该让广场打不开
        logger.warning("[SkillGallery] 读取平台技能目录失败：%s", exc)
        return []
    if not skill_ids:
        return []
    from app.core.config import settings

    root = str(getattr(settings, "SKILLS_DIR", "") or "")
    if not root:
        return []
    return [(skill_id, os.path.join(root, skill_id)) for skill_id in sorted(skill_ids)]


def _personal_skill_dirs(user: Dict[str, Any]) -> List[Tuple[str, str]]:
    """当前用户个人技能库里的 ``(skill_id, skill_dir)``。"""
    try:
        root = get_user_personal_skills_dir(user)
    except Exception as exc:  # noqa: BLE001 - 解析不了工作区不该让广场打不开
        logger.warning("[SkillGallery] 解析个人技能目录失败：%s", exc)
        return []
    if not root or not os.path.isdir(root):
        return []
    try:
        return list(iter_personal_skill_dirs(root))
    except OSError as exc:
        logger.warning("[SkillGallery] 遍历个人技能目录失败：%s", exc)
        return []


def _read_installed_version(skill_dir: str) -> str:
    """读已装技能 ``SKILL.md`` frontmatter 里的 ``version``；读不到返回空串。

    第三方技能大多不写 version，这里**不猜**：空串由前端渲染成「未知」，
    比把目录名当版本号诚实。
    """
    try:
        from app.utils.skill_metadata import parse_skill_frontmatter

        meta = parse_skill_frontmatter(
            os.path.basename(skill_dir), os.path.join(skill_dir, "SKILL.md")
        )
        return str(meta.get("version") or "").strip()
    except Exception as exc:  # noqa: BLE001 - 元数据读坏了只影响一行展示
        logger.debug("[SkillGallery] 读取已装技能版本失败 %s：%s", skill_dir, exc)
        return ""


def _installed_cache_key(user: Dict[str, Any]) -> str:
    try:
        personal_root = str(get_user_personal_skills_dir(user) or "")
    except Exception:  # noqa: BLE001
        personal_root = ""
    from app.core.config import settings

    platform_root = str(getattr(settings, "SKILLS_DIR", "") or "")
    return f"{platform_root}|{personal_root}"


def _installed_entries(user: Dict[str, Any]) -> Dict[str, Tuple[str, str, str]]:
    """返回 ``{skill_id: (scope, skill_dir, version)}``，个人优先于平台。

    同一个 ID 两处都有时按 **personal** 记：个人技能覆盖公共技能是平台的既有优先级，
    徽标要跟着实际生效的那份走。结果按「平台根 + 个人根」缓存 30 秒。
    """
    key = _installed_cache_key(user)
    now = time.monotonic()
    cached = _installed_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    entries: Dict[str, Tuple[str, str, str]] = {}
    for skill_id, skill_dir in _platform_skill_dirs():
        entries[skill_id] = ("global", skill_dir, _read_installed_version(skill_dir))
    for skill_id, skill_dir in _personal_skill_dirs(user):
        entries[skill_id] = ("personal", skill_dir, _read_installed_version(skill_dir))

    # 简单的容量控制：满了就整体清空。缓存是纯优化，清空不会影响正确性。
    if len(_installed_cache) >= _INSTALLED_CACHE_MAX_ENTRIES:
        _installed_cache.clear()
    _installed_cache[key] = (now + _INSTALLED_CACHE_TTL_SECONDS, entries)
    return entries


def _list_etag(installed: Dict[str, Tuple[str, str, str]]) -> str:
    """列表响应的弱 ETag：目录版本 + 已装技能签名（含库内版本）。"""
    signature = ";".join(
        f"{skill_id}:{scope}:{version}"
        for skill_id, (scope, _dir, version) in sorted(installed.items())
    )
    digest = hashlib.sha1(
        f"{gallery_catalog.catalog_version()}|{signature}".encode("utf-8")
    ).hexdigest()[:16]
    return f'W/"gallery-{digest}"'


def _card_payload(skill: Dict[str, Any]) -> Dict[str, Any]:
    """列表卡片：去掉详情专用字段，只留渲染卡片需要的东西。"""
    card = dict(skill)
    for field in _LIST_DROP_FIELDS:
        card.pop(field, None)
    return card


@router.post("/events", summary="技能广场行为事件")
async def report_skill_gallery_event(
    payload: SkillGalleryEvent,
    user: Dict = Depends(require_api_key),
) -> Dict[str, Any]:
    """记录「打开详情 / 复制提示词 / 复制平台版」事件。

    先落结构化日志（可被采集侧 grep ``[SkillGallery][event]``），不建表：
    这一层的目的是让「复制了多少次、哪个技能被看」有数可查，等真有分析需求
    再决定落到哪张表。
    """
    event = str(payload.event or "").strip().lower()
    if event not in _SKILL_EVENT_NAMES:
        raise HTTPException(status_code=422, detail=f"未知事件：{payload.event}")
    try:
        user_id: Optional[int] = int(user.get("user_id"))
    except (TypeError, ValueError):
        user_id = None
    logger.info(
        "[SkillGallery][event] %s",
        json.dumps(
            {
                "event": event,
                "skill_id": str(payload.skill_id or "").strip()[:128],
                "scope": str(payload.scope or "").strip()[:32],
                "user_id": user_id,
            },
            ensure_ascii=False,
        ),
    )
    return {"ok": True}


@router.get("", summary="技能广场目录")
async def list_gallery_skills(
    request: Request,
    response: Response,
    user: Dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session, scope="function"),
) -> Any:
    """返回全部技能卡片与分类，供前端渲染网格。

    每条带 ``installed``（``"personal"`` / ``"global"`` / ``None``）与
    ``installed_version``（库内版本，可能为空），前端据此显示徽标与版本，
    避免用户对已经装过的技能重复执行安装。
    """
    try:
        skills: List[Dict[str, Any]] = gallery_catalog.list_skills()
    except Exception as exc:  # noqa: BLE001 - 目录坏了不该 500，前端应能显示空态
        logger.error("[SkillGallery] 读取技能广场目录失败：%s", exc)
        raise HTTPException(status_code=500, detail="技能广场目录读取失败，请稍后重试")

    installed = _installed_entries(user)
    etag = _list_etag(installed)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if (request.headers.get("if-none-match") or "").strip() == etag:
        return Response(status_code=304, headers=headers)

    cards: List[Dict[str, Any]] = []
    for skill in skills:
        card = _card_payload(skill)
        entry = installed.get(card["id"])
        card["installed"] = entry[0] if entry else None
        card["installed_version"] = entry[2] if entry else ""
        cards.append(card)

    response.headers.update(headers)
    return {
        "skills": cards,
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
    entry = _installed_entries(user).get(detail["id"])
    detail["installed"] = entry[0] if entry else None
    detail["installed_version"] = entry[2] if entry else ""
    if not await _can_manage_platform_skills(user, db):
        _strip_platform_prompt(detail)
    return detail

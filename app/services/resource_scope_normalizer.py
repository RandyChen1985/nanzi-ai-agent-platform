"""资源范围收敛：把客户端提交的资源 token 收敛为用户可见目录中的可信快照。

会话（EmbedChat）与定时任务共用同一套语义：不在授权目录内的数据集 / 知识库 /
技能 / MCP 条目一律丢弃，范围只能收窄，不能借此扩大权限。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

def has_any_resource(scope: Dict[str, Any] | None) -> bool:
    """判断范围内是否挂载了任何资源（project_name 不算资源）。"""
    source = scope or {}
    return any(
        source.get(key)
        for key in ("datasets", "knowledge_bases", "skills", "mcp_tools")
    )


async def normalize_dataset_mounts(
    db: AsyncSession, user_id: int | None, *, is_admin: bool, items: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    from app.services.metadata_service import MetadataService

    if not items:
        return []
    datasets = await MetadataService.list_accessible_dataset_options(
        db, user_id=user_id, is_admin=is_admin, status=1,
    )
    by_token = {}
    for dataset in datasets:
        for token in (dataset.id, dataset.name, getattr(dataset, "dataset_name", None)):
            if token is not None and str(token).strip():
                by_token[str(token).strip().casefold()] = dataset

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dataset = next(
            (by_token[token] for key in ("id", "dataset_name", "name")
             if (token := str(item.get(key) or "").strip().casefold()) in by_token),
            None,
        )
        if dataset is not None:
            result.append({
                "id": str(dataset.id),
                "name": str(getattr(dataset, "display_name", None) or dataset.name or dataset.id),
                "dataset_name": str(dataset.name or ""),
                "description": str(getattr(dataset, "description", None) or ""),
            })
    return result


async def normalize_resource_scope_for_user(
    db: AsyncSession,
    user_info: Dict[str, Any],
    raw_scope: Dict[str, Any],
) -> Dict[str, Any]:
    """把客户端提交的资源 token 收敛为指定用户可见目录中的可信快照。"""
    from app.models.knowledge import KnowledgeBaseMetadata
    from app.core.config import settings
    from app.services.ai.skill_resolver import get_user_personal_skills_dir
    from app.services.permission_service import PermissionService
    # 直接依赖唯一解析器，不再反向 import 端点模块（此处曾 import
    # app.api.portal.endpoints.skills.parse_skill_metadata，服务层依赖 API 层会把整棵
    # 路由依赖拖进资源收敛路径，也让「解析口径」多绕一层）
    from app.utils.skill_metadata import is_skill_enabled, parse_skill_frontmatter
    from sqlalchemy import select

    raw_scope = raw_scope if isinstance(raw_scope, dict) else {}
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    try:
        user_id = int(raw_user_id) if raw_user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    datasets = await normalize_dataset_mounts(
        db, user_id, is_admin=user_info.get("role") == "admin", items=raw_scope.get("datasets") or [],
    )

    kb_access = await PermissionService(db).get_knowledge_base_access(
        int(user_id), user_info.get("user_name")
    ) if user_id is not None else {"is_admin": False, "accessible_ids": set()}
    kb_stmt = select(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.status != "deleted")
    kb_rows = list((await db.execute(kb_stmt)).scalars().all())
    allowed_kb_ids = kb_access.get("accessible_ids")
    kb_by_token: Dict[str, Any] = {}
    for kb in kb_rows:
        if allowed_kb_ids is not None and str(kb.ragflow_dataset_id) not in allowed_kb_ids:
            continue
        for token in (kb.ragflow_dataset_id, kb.name, kb.id):
            if token is not None and str(token).strip():
                kb_by_token[str(token).strip().casefold()] = kb

    def normalize_kb(item: Any) -> Dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        kb = next(
            (
                kb_by_token.get(str(item.get(key) or "").strip().casefold())
                for key in ("id", "dataset_id", "name")
                if str(item.get(key) or "").strip().casefold() in kb_by_token
            ),
            None,
        )
        if kb is None:
            return None
        return {
            "id": str(kb.ragflow_dataset_id),
            "name": str(kb.name or kb.ragflow_dataset_id),
            "description": str(kb.description or ""),
        }

    skill_by_token: Dict[tuple[str, str], Dict[str, Any]] = {}

    def collect_skills(root: str, scope_name: str, *, allow_seed: bool = False) -> None:
        if not root or not os.path.isdir(root):
            return
        if allow_seed:
            # 个人技能源可能仍是历史遗留的 .seed 布局（K8s 未修复期的产物）
            from app.services.ai.skill_resolver import iter_personal_skill_dirs

            candidates = list(iter_personal_skill_dirs(root))
        else:
            try:
                entries = os.listdir(root)
            except OSError:
                return
            candidates = [
                (skill_id, os.path.join(root, skill_id))
                for skill_id in entries
                if not skill_id.startswith(".")
            ]
        for skill_id, skill_dir in candidates:
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_md):
                continue
            meta = parse_skill_frontmatter(skill_id, skill_md)
            if not is_skill_enabled(meta):
                continue
            skill_by_token[(scope_name, skill_id.casefold())] = {
                "id": skill_id,
                "name": str(meta.get("name") or skill_id),
                "description": str(meta.get("description") or ""),
                "scope": scope_name,
            }

    collect_skills(str(getattr(settings, "SKILLS_DIR", "") or ""), "global")
    try:
        collect_skills(
            str(get_user_personal_skills_dir(user_info) or ""),
            "personal",
            allow_seed=True,
        )
    except Exception:
        pass

    normalized_skills: list[Dict[str, Any]] = []
    for item in raw_scope.get("skills", []) or []:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("id") or "").strip()
        requested_scope = str(item.get("scope") or "").strip().lower()
        candidates = (
            [(requested_scope, skill_id.casefold())]
            if requested_scope in {"global", "personal"}
            else [("global", skill_id.casefold()), ("personal", skill_id.casefold())]
        )
        skill = next((skill_by_token.get(key) for key in candidates if skill_by_token.get(key)), None)
        if skill:
            normalized_skills.append(skill)

    return {
        "project_name": str(raw_scope.get("project_name") or "").strip()[:100],
        "datasets": datasets,
        "knowledge_bases": [item for raw in raw_scope.get("knowledge_bases", []) or [] if (item := normalize_kb(raw))],
        "skills": normalized_skills,
        "mcp_tools": await normalize_personal_mcp_mounts(db, user_id, raw_scope.get("mcp_tools") or []),
    }


async def normalize_personal_mcp_mounts(
    db: AsyncSession, user_id: Any, items: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """校验会话请求中属于当前用户且可挂载的个人 MCP 工具。"""
    from sqlalchemy import select
    from sqlalchemy.orm import contains_eager

    from app.models.mcp import McpServer, McpToolCache

    if not items or user_id is None:
        return []
    try:
        owner_id = int(user_id)
    except (TypeError, ValueError):
        return []

    stmt = (
        select(McpToolCache)
        .join(McpToolCache.server)
        .options(contains_eager(McpToolCache.server))
        .where(
            McpServer.scope == "personal",
            McpServer.user_id == owner_id,
            McpServer.enabled_status == 1,
            McpToolCache.is_published.is_(True),
            McpToolCache.is_available.is_(True),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    by_id = {str(row.id).casefold(): row for row in rows}
    by_name = {row.tool_name.casefold(): row for row in rows if row.tool_name}
    mounts: dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "").strip().casefold()
        tool_name = str(item.get("name") or "").strip().casefold()
        row = by_id.get(tool_id) if tool_id else by_name.get(tool_name)
        if row is None or not row.tool_name or row.tool_name in mounts:
            continue
        mounts[row.tool_name] = {
            "id": str(row.id),
            "name": row.tool_name,
            "description": row.tool_description or "",
            "server_name": row.server.server_name,
            "scope": row.server.scope,
        }
    return list(mounts.values())

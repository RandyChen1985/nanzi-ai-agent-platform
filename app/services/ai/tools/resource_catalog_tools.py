"""System tools: list current user's accessible datasets and knowledge bases."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.tools.document_paths import resolve_context_user_name
from app.services.ai.tools.tool_compat import tool
from app.services.ai.knowledge_catalog import fetch_authorized_knowledge_catalog
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


def _dataset_item(row: Any) -> dict[str, Any]:
    """目录条目：兼容 ORM 行（老调用方）与已组装好的 dict（带权限摘要的新路径）。"""
    if isinstance(row, dict):
        item: dict[str, Any] = {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "display_name": row.get("display_name") or "",
            "description": row.get("description") or "",
            "status": row.get("status"),
        }
        permission = row.get("permission")
        if permission:
            item["permission"] = permission
        return item
    return {
        "id": getattr(row, "id", None),
        "name": getattr(row, "name", None) or "",
        "display_name": getattr(row, "display_name", None) or "",
        "description": getattr(row, "description", None) or "",
        "status": getattr(row, "status", None),
    }


def _knowledge_item(
    row: Any,
    *,
    can_edit: Optional[bool] = None,
    can_upload: Optional[bool] = None,
) -> dict[str, Any]:
    item = {
        "ragflow_dataset_id": getattr(row, "ragflow_dataset_id", None) or "",
        "name": getattr(row, "name", None) or "",
        "description": getattr(row, "description", None) or "",
        "notes": getattr(row, "notes", None) or "",
        "visibility": getattr(row, "visibility", None) or "",
        "owner": getattr(row, "owner", None) or "",
    }
    if can_edit is not None:
        item["can_edit"] = bool(can_edit)
    if can_upload is not None:
        item["can_upload"] = bool(can_upload)
    return item


#: 单条 description 的返回上限。整段业务介绍对「挑数据集」没有帮助，却能把上下文
#: 撑爆：实测一个数据集的介绍 80+ 字，50 个数据集就是 4000+ token。要看完整介绍，
#: 用 keyword 精确命中那一条即可。
_DATASET_DESCRIPTION_LIMIT = 80
_DATASET_LIMIT_DEFAULT = 30
_DATASET_LIMIT_MAX = 100


def _coerce_positive_int(raw: Any, *, maximum: Optional[int] = None) -> Optional[int]:
    """把模型传来的「数字或数字字符串」转成正整数；非法值返回 None。

    模型经常把 id/limit 写成字符串（``"54"``）或带空格，硬转 int 会直接抛异常把
    整次调用变成错误返回，所以这里统一容错。
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, maximum) if maximum else value


def _clip_dataset_description(text: str) -> tuple[str, bool]:
    """裁剪数据集介绍，返回 ``(文本, 是否被裁剪)``。"""
    value = str(text or "").strip()
    if len(value) <= _DATASET_DESCRIPTION_LIMIT:
        return value, False
    return value[:_DATASET_DESCRIPTION_LIMIT].rstrip() + "…", True


async def _describe_missing_dataset(db: Any, dataset_id: int) -> Optional[str]:
    """指定 id 没进可访问目录时，尽量说清是哪一种「没有」。

    三种情况的处置完全不同，混成一句「你没有这个数据集」会把用户带偏：

    - ``missing``：数据集不存在（多半已删除）→ 数据资产问题；
    - ``disabled``：存在但已停用 → 配置问题，启用即可；
    - ``no_grant``：存在且启用，但当前账号没被授权 → 权限问题，要找管理员。

    查询失败返回 ``None``，调用方退回模糊文案（少一句提示不算事故）。
    """
    try:
        from sqlalchemy import select

        from app.models.metadata import MetaDataset

        row = (
            await db.execute(
                select(MetaDataset.id, MetaDataset.status).where(
                    MetaDataset.id == int(dataset_id)
                )
            )
        ).first()
    except Exception as exc:  # noqa: BLE001 - 状态查询失败不影响主流程
        logger.warning("[list_accessible_datasets] 数据集状态查询失败: %s", exc)
        return None
    if row is None:
        return "missing"
    if int(row[1] or 0) != 1:
        return "disabled"
    return "no_grant"


@tool
async def list_accessible_datasets(
    keyword: Optional[str] = None,
    dataset_id: Optional[int] = None,
    limit: Optional[int] = _DATASET_LIMIT_DEFAULT,
) -> str:
    """列出当前用户有权限且已启用的 ChatBI 数据集目录（id/名称/备注/权限摘要，不含表结构）。

    使用规则：
    - 当用户问「我有哪些数据集」「能查哪些数据」「数据集列表」时调用。
    - 数据集多时用 ``keyword`` 收窄（匹配名称/展示名/备注），已知 id 时直接给
      ``dataset_id`` —— 不要为了找一个数据集把整份目录拉进上下文。
    - 用户问「我对某个数据集有没有权限」时用 ``dataset_id`` 精确查：查不到时 ``note``
      会区分「不存在」「已停用」「存在但未授权」三种原因，按实际原因回答，不要说成
      「没有数据集」或「没有权限」。
    - 默认最多返回 30 条（上限 100）；``truncated=true`` 表示还有未展示的条目。
    - 仅返回 status=1（启用）的目录级信息；未启用的数据集不会出现。
    - 条目里**没有** ``permission`` 字段 = 该数据集对你没有表/字段/行级收窄。
    - ``permission.tables.visible`` 与 ``total`` 不一致 = 只授权了其中几张表；
      ``visible=0`` 表示这个数据集下你没有可查询的表，不要再去取 Schema。
    - ``permission.row``：``deny_all`` 表示数据集开了行级策略但没匹配到你的规则，
      运行时一律 1=0，**实际查不到任何数据**，要先向用户说明原因；
      ``custom``/``filtered`` 表示结果会被行级条件收窄；``none`` 表示明确不做行级过滤。
    - ``permission.expires_at`` 是最近到期时间：到期后会回落到角色基线，权限可能变化。
    - ``permission.columns``：``restricted_tables`` 是有字段规则的表数，
      ``excluded`` 是单表**最多**被排除的字段数（不是总和），``whitelisted_tables``
      是用字段白名单的表数。``columns.tables`` 给明细：``mode=include`` 时 ``names``
      是**你能查的字段**（可以直接告诉用户「只能查 id/real_name/user_name」）；
      ``mode=exclude`` 或 ``mixed`` 时 ``names`` 恒为 null（屏蔽清单不公开），
      这时只能说「这张表有字段被屏蔽」，**不要**猜测或编造是哪几列。
    - ``permission.sources`` 是授权来源：``user``=用户直授、``role``=角色继承、
      ``all``=全员基线。
    - ``note`` 里会点出展示名重复的数据集：条目的 ``id`` 各不相同，回答时用 id
      区分，不要只报展示名。
    - ``note`` 是对空结果/截断/失效授权的解释，**必须**转述给用户：
      它区分了「没有授权」「授权指向的数据集已删除或停用」「关键字没匹配到」——
      这三种情况都不要笼统说成「你没有数据集」。
    - ``note`` 里还可能出现「本轮只挂载了…」：条目 ``mounted=true`` 表示它在本轮可查范围内。
      其余数据集虽在账号权限内，本轮也**查不了**，回答「我能查什么」时以挂载范围为准，
      不要承诺去查未挂载的数据集。
    - ``description_truncated=true`` 表示介绍被截断，需要全文时用 keyword 精确查该条。
    - 这里只有目录级信息，**不要据此编造表结构或查询结果**；要具体表/字段时，用当前可用的 Schema/取数工具再查一次。

    Args:
        keyword: 可选。按数据集 name / display_name / description 模糊匹配，不区分大小写。
        dataset_id: 可选。精确查某一个数据集（传正整数 id），用于回答「我对它有没有权限」；
            与 keyword 同时给时两者都要满足。
        limit: 可选。本次最多返回多少条，默认 30，最大 100。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出数据集。"

    wanted_id = _coerce_positive_int(dataset_id)
    if dataset_id is not None and wanted_id is None:
        return json.dumps(
            {
                "items": [],
                "count": 0,
                "total": 0,
                "note": f"dataset_id={dataset_id!r} 不是合法的数据集 ID，应传正整数 id。",
            },
            ensure_ascii=False,
        )

    needle = str(keyword or "").strip().lower()
    page_size = _coerce_positive_int(limit, maximum=_DATASET_LIMIT_MAX) or _DATASET_LIMIT_DEFAULT

    target_state: Optional[str] = None
    mounted_ids: Optional[set[int]] = None
    try:
        async with AsyncSessionLocal() as db:
            entries = await MetadataService.list_accessible_dataset_entries(
                db,
                user_id=ctx.user_id,
                is_admin=bool(ctx.is_admin),
                status=1,
            )
            # 授权 id 集合与「目录里真实存在的数据集」是两件事：授权行可能指向已删除
            # 或已停用的数据集，那样用户在权限页看得到授权、模型却说没有这个数据集。
            # 这一步失败只是少一条提示，不能把整份目录打成错误返回。
            try:
                granted_ids = await MetadataService.accessible_dataset_ids(
                    db, user_id=ctx.user_id, is_admin=bool(ctx.is_admin)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[list_accessible_datasets] 读取授权集合失败，跳过失效授权提示: %s", exc
                )
                granted_ids = None
            # 账号级权限 ≠ 本轮可查范围：ChatBI 执行 SQL 时还会按挂载范围再拦一次
            # （``execute_sql_query`` 的 mounted 门禁）。不把这件事说清，模型就会
            # 据「账号里有 B」承诺去查 B，然后被运行期拒绝。
            try:
                from app.services.ai.metadata_dataset_scope import (
                    resolve_mounted_dataset_id_set,
                )

                mounted_ids = await resolve_mounted_dataset_id_set(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[list_accessible_datasets] 读取本轮挂载范围失败，跳过该提示: %s", exc
                )
            if wanted_id is not None and not any(
                int(entry.get("id") or 0) == wanted_id for entry in entries
            ):
                target_state = await _describe_missing_dataset(db, wanted_id)
    except Exception as e:
        logger.error("[list_accessible_datasets] failed: %s", e, exc_info=True)
        return f"列出可访问数据集失败: {e}"

    matched: list[dict[str, Any]] = []
    for entry in entries:
        item = _dataset_item(entry)
        if wanted_id is not None and int(item.get("id") or 0) != wanted_id:
            continue
        if needle:
            haystack = " ".join(
                str(item.get(key) or "") for key in ("name", "display_name", "description")
            ).lower()
            if needle not in haystack:
                continue
        matched.append(item)

    # 展示名重复曾经靠给每条加 label 解决，但那是在**每一轮**都付固定 token 成本，
    # 目录越大越亏（实测 13 条目录里 11 条重名，label 就占 300+ 字符）。改成在 note
    # 里点一句「用 id 区分」：id 本来就在每个条目里，模型完全能指代。
    name_counts: dict[str, int] = {}
    for item in matched:
        key = str(item.get("display_name") or item.get("name") or "")
        name_counts[key] = name_counts.get(key, 0) + 1

    total = len(matched)
    page = matched[:page_size]
    items: list[dict[str, Any]] = []
    mounted_labels: list[str] = []
    for item in page:
        clipped, was_truncated = _clip_dataset_description(item.get("description") or "")
        item["description"] = clipped
        if was_truncated:
            item["description_truncated"] = True
        if mounted_ids is not None and int(item.get("id") or 0) in mounted_ids:
            item["mounted"] = True
            mounted_labels.append(str(item.get("display_name") or item.get("name") or item["id"]))
        items.append(item)

    notes: list[str] = []
    if mounted_ids is not None:
        if mounted_labels:
            notes.append(
                f"本轮只挂载了「{'、'.join(mounted_labels)}」（标了 mounted=true）；"
                "其余数据集虽在你的账号权限内，但本轮 SQL 只能查挂载的这些。"
            )
        elif not mounted_ids:
            notes.append(
                "本轮挂载的数据集已无法解析（可能已删除），本轮 SQL 可能查不到任何数据。"
            )
        else:
            notes.append(
                "本轮挂载的数据集不在本次结果里（可能被筛选条件排除、已停用或不在你的权限内）；"
                "本轮 SQL 只能查挂载范围内的数据集。"
            )
    if wanted_id is not None and not items:
        if target_state == "missing":
            notes.append(
                f"数据集 id={wanted_id} 不存在（可能已被删除）；这是数据资产问题，不是权限问题。"
            )
        elif target_state == "disabled":
            notes.append(
                f"数据集 id={wanted_id} 存在但已停用，因此不在可访问目录里；"
                "启用后即可查询，这是数据集状态问题。"
            )
        elif target_state == "no_grant":
            notes.append(
                f"数据集 id={wanted_id} 存在且已启用，但当前账号没有被授权访问它；"
                "这是权限问题，需要管理员在数据集权限里授权。"
            )
        else:
            notes.append(
                f"数据集 id={wanted_id} 不在可访问目录里（无权限 / 已删除 / 已停用）；"
                "可去掉 dataset_id 再查一次确认。"
            )
    elif not matched:
        if not entries:
            grant_count = len(granted_ids) if granted_ids is not None else 0
            if grant_count:
                notes.append(
                    f"该用户有 {grant_count} 条授权，但对应数据集都已删除或停用，目录才为空——"
                    "这是数据集状态问题，不是权限问题。"
                )
            else:
                notes.append("该账号没有任何启用的数据集授权，请如实告知用户，不要编造数据集名称。")
        else:
            sample = "、".join(
                str(entry.get("display_name") or entry.get("name")) for entry in entries[:5]
            )
            notes.append(
                f"关键字「{keyword}」没匹配到数据集。可访问的有：{sample}"
                + ("……" if len(entries) > 5 else "")
                + "。"
            )
    elif total > len(items):
        notes.append(
            f"共命中 {total} 个，只返回前 {len(items)} 个；可用 keyword 收窄或调大 limit（≤100）。"
        )

    duplicate_groups = [name for name, count in name_counts.items() if count > 1]
    if duplicate_groups and items:
        notes.append(
            f"有 {len(duplicate_groups)} 组数据集展示名重复（如「{duplicate_groups[0]}」），"
            "id 各不相同，回答时用 id 区分。"
        )

    if granted_ids is not None and wanted_id is None:
        present_ids = {int(item["id"]) for item in entries if item.get("id") is not None}
        missing = sorted(
            int(grant_id) for grant_id in granted_ids if int(grant_id) not in present_ids
        )
        if missing:
            shown = "、".join(str(value) for value in missing[:5]) + (
                "……" if len(missing) > 5 else ""
            )
            notes.append(
                f"另有 {len(missing)} 条授权指向已删除或已停用的数据集（id: {shown}），"
                "它们不在列表里；这不属于权限问题。"
            )

    payload: dict[str, Any] = {"items": items, "count": len(items), "total": total}
    if total > len(items):
        payload["truncated"] = True
    if notes:
        payload["note"] = " ".join(notes)
    return json.dumps(payload, ensure_ascii=False)


@tool
async def list_accessible_knowledge_bases() -> str:
    """列出当前用户有权限的知识库轻量目录（id/名称/备注/能力标记，不含文档正文）。

    使用规则：
    - 当用户问「我有哪些知识库」「能检索哪些文档库」「知识库列表」时调用。
    - 仅返回目录级信息，不含文档正文；本工具不承担正文检索。
    - 每条带两个能力标记，**按需过滤后再问用户**：
      ``can_edit`` 为 true 表示能改这个库的文档（改动上传/删除/重命名），
      ``can_upload`` 为 true 表示不仅能改、且当前账号有「上传文档」的功能权限。
      要把文件传进某个库时只看 ``can_upload=true`` 的；一个都没有就直说
      「你目前没有可上传的知识库」，不要拿只读库去试。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出知识库。"

    try:
        user_name = resolve_context_user_name(ctx)
        async with AsyncSessionLocal() as db:
            catalog = await fetch_authorized_knowledge_catalog(
                db,
                user_id=int(ctx.user_id),
                user_name=user_name,
                is_admin=bool(ctx.is_admin),
                permission_service=PermissionService(db),
            )
            is_admin = bool(getattr(ctx, "is_admin", False))
            # "能编辑"和"能上传"不是一回事：上传还要求角色具备
            # element:knowledge:upload_document。只标 can_edit 会让模型对着
            # 一个缺元素权限的账号反复尝试上传，最后拿一句 forbidden 收场。
            can_upload_role = is_admin or bool(
                await PermissionService(db).check_permission(
                    int(ctx.user_id), "element", "element:knowledge:upload_document"
                )
            )
            items = []
            for row in catalog.items:
                dataset_id = str(getattr(row, "ragflow_dataset_id", None) or "")
                can_edit = is_admin or dataset_id in catalog.content_writable_ids
                items.append(
                    _knowledge_item(
                        row,
                        can_edit=can_edit,
                        can_upload=bool(can_edit and can_upload_role),
                    )
                )
            items.sort(key=lambda x: x.get("ragflow_dataset_id") or "")
            return json.dumps({"items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_accessible_knowledge_bases] failed: %s", e, exc_info=True)
        return f"列出可访问知识库失败: {e}"


def _agent_item(agent: Any, *, current_agent_id: Optional[str] = None) -> dict[str, Any]:
    agent_id = str(getattr(agent, "id", "") or "")
    is_current = bool(current_agent_id and agent_id == str(current_agent_id))
    is_system = bool(getattr(agent, "is_system", False))
    return {
        "agent_name": getattr(agent, "name", "") or "",
        "display_name": getattr(agent, "display_name", "") or getattr(agent, "name", "") or "",
        "description": getattr(agent, "description", "") or "",
        "capabilities": list(getattr(agent, "capabilities", None) or []),
        "is_current": is_current,
        # 只有「调度范围」里显式加入的自建智能体才会带 is_mine=true 出现在这里，
        # 模型据此刻度说明「这是你自己的智能体，已被本智能体授权调用」。
        "is_mine": not is_system,
    }


@tool
async def list_available_agents() -> str:
    """列出智能体目录：本智能体当前可委派的，以及用户本人有访问权限的（含 agent_name、展示名称、职责描述、核心能力、当前会话标识 is_current 与来源标识 is_mine）。

    使用规则：
    - 当用户问「我有哪些智能体」「能调用哪些专家」「可用智能体列表」「我有那些智能体权限」时调用。
    - 在需要委派子任务前，若需查询或确认可委派的智能体标识 (agent_name) 与核心能力，可调用此工具。
    - 返回体分两段：`items` 是**当前可直接调用**的可委派智能体，委派工具只能调用其中出现的标识；
      `blocked` 是用户本人有访问权限、但当前调不了的（附 blocked_reason：no_tool / self /
      main_agent / not_in_allowlist / not_ready）。
    - 「我有那些智能体权限」看 `accessible_count` 与 `blocked`；「我能委派哪些」只看 `items`
      与 `delegable_count`。两个问题不要混着答。
    - 平台系统级智能体需要在角色权限里被授予访问权才会出现；用户自建智能体**默认不可委派**，
      只有被显式加入本智能体的「调度范围」后才出现（此时带 `is_mine=true`）。
    - 若本智能体**没有启用委派工具**，`delegation_available=false`：要明确告诉用户「当前无法委派」，
      但仍可照 `blocked` 回答他本人的智能体权限清单，**不要**答成「你没有任何智能体」。
    - 返回体含 scope/items/count；当前对话的智能体若在列则标注 is_current=true。
    - 空目录时返回体附带 note 说明原因，必须据此向用户解释（例如「你没被分配可委派的系统级智能体」或
      「调度名单为空」），不要简化成「你没有任何智能体」。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出可用智能体。"

    try:
        from app.services.ai.agent_manager import AgentManagerService
        from app.services.ai.agent_roster import (
            resolve_delegable_system_agents_for_user,
        )
        from app.services.ai.tools.agent_delegate_tool import (
            resolve_accessible_agents_for_user,
        )
        from app.services.ai.tools.agent_delegate_tool import (
            has_delegation_tool,
            is_main_general_agent_target,
            resolve_delegation_allowlist,
        )

        current_agent_id = getattr(ctx, "agent_id", None)
        async with AsyncSessionLocal() as db:
            # 候选口径与委派链其余环节共用同一出口（目录 / 专家清单 / 子代理通讯录 /
            # sub_agent_call 全部走 resolve_delegable_system_agents_for_user）。
            # 不能复用 AgentManagerService.list_agents：那是 UI 可见性口径，按 created_by
            # 归属过滤，而工具侧没有 user 可传，会把候选整批剃成空目录。
            caller_config = (
                await AgentManagerService.get_active_agent_config(db, agent_id=str(current_agent_id))
                if current_agent_id
                else None
            )
            # 「我能委派哪些」与「我有那些智能体权限」是两个问题，必须分开答：
            # 后者只看用户被授了什么，跟前一个智能体装没装委派工具无关；
            # 前者还要再叠「工具 → 白名单 → 就绪」三层。把两者混成一个清单，
            # 就会出现「没装委派工具 → 连我有权限的智能体都查不到」。
            accessible_agents = await resolve_accessible_agents_for_user(
                db,
                user_id=ctx.user_id,
                is_admin=bool(ctx.is_admin),
                user_name=(ctx.user_dimensions or {}).get("user_name"),
            )
            # caller_config 读不到（未发布 / 外部引擎）时不判定为「没装工具」，
            # 否则会把「查不到配置」误判成「没有委派能力」。
            delegation_available = caller_config is None or has_delegation_tool(caller_config)

            if delegation_available:
                available_agents = await resolve_delegable_system_agents_for_user(
                    db,
                    user_info={
                        "user_id": ctx.user_id,
                        "user_name": (ctx.user_dimensions or {}).get("user_name"),
                        "role": "admin" if ctx.is_admin else "user",
                    },
                    # 传 None 才能把自身也纳入目录（由 is_current 标识）；传当前 id 会把自己剔除。
                    current_agent_id=None,
                    # 配了调度白名单的智能体，目录也只能列名单内的，否则模型会照着目录
                    # 去调一个必被拒绝的目标。
                    configured_tools=getattr(caller_config, "tools", None) if caller_config else None,
                )
            else:
                available_agents = []

            allowlist_configured, allowlist = resolve_delegation_allowlist(
                getattr(caller_config, "tools", None) if caller_config else None
            )
            allowlist_ids = {str(a or "").strip() for a in (allowlist or []) if str(a or "").strip()}

            items = [_agent_item(a, current_agent_id=current_agent_id) for a in available_agents]
            delegable_ids = {str(getattr(a, "id", "")) for a in available_agents}

            # 「有权限但现在调不了」单独分组：模型直接调 items，别从 blocked 里挑；
            # 用户问「我有那些智能体权限」时，blocked 才是答案的主体。
            blocked: list[dict[str, Any]] = []
            for agent in accessible_agents:
                agent_id = str(getattr(agent, "id", ""))
                if agent_id in delegable_ids:
                    continue
                entry = _agent_item(agent, current_agent_id=current_agent_id)
                if not delegation_available:
                    reason, explain = "no_tool", "本智能体未启用委派工具，暂时无法委派"
                elif current_agent_id and agent_id == str(current_agent_id):
                    reason, explain = "self", "就是当前对话的智能体本身，不能委派给自己"
                elif is_main_general_agent_target(agent):
                    reason, explain = (
                        "main_agent",
                        "主助手是系统兜底入口，不能作为委派目标（候选池与运行时调用都已排除）",
                    )
                elif allowlist_configured and agent_id not in allowlist_ids:
                    reason, explain = "not_in_allowlist", "不在本智能体的「调度范围」内"
                else:
                    reason, explain = "not_ready", "运行不就绪（缺发布版本或所需资源）"
                entry["blocked_reason"] = reason
                entry["blocked_explain"] = explain
                blocked.append(entry)

            payload: dict[str, Any] = {
                "scope": "delegable_agents",
                "delegation_available": delegation_available,
                # items 才是可直接调用集合；blocked 是「你有权限但当前不行」。
                "items": items,
                "count": len(items),
                "delegable_count": len(items),
                "accessible_count": len(items) + len(blocked),
                "blocked": blocked,
            }
            if not items:
                # 空目录必须自带原因。只给 {"items": [], "count": 0} 时，模型很容易把它
                # 转述成「你没有任何智能体」，与智能体中心里用户自建的条目直接冲突——
                # 页面上有 3 个、对话里说 0 个。note 让模型能照着解释「为什么是 0」。
                if not delegation_available:
                    payload["note"] = (
                        "本智能体未启用委派工具（sub_agent_call / sub_agent_batch_call），"
                        "当前无法委派任何子智能体。blocked 中列出的是你本人有访问权限的智能体，"
                        "它们与此智能体的委派能力无关；如需委派，需要先在智能体配置里启用该工具并发布。"
                    )
                elif allowlist_configured and not allowlist:
                    payload["note"] = (
                        "本智能体已配置「调度白名单」且名单为空，任何子智能体都不可委派；"
                        "如需恢复，请在工具配置的「调度范围」中加入智能体。blocked 中列出的是"
                        "你本人有访问权限的智能体。"
                    )
                elif bool(ctx.is_admin):
                    payload["note"] = (
                        "当前平台没有可委派的系统级智能体（可能均未启用、未发布或运行不就绪）。"
                    )
                else:
                    payload["note"] = (
                        "你没有被分配可委派的系统级智能体；自己创建的智能体默认不可委派，"
                        "需由本智能体的「调度范围」显式加入后才会出现在本目录。"
                        "blocked 中列出的是你本人有访问权限的智能体。"
                    )
            return json.dumps(payload, ensure_ascii=False)
            return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_available_agents] failed: %s", e, exc_info=True)
        return f"列出可用智能体失败: {e}"


@tool
async def list_accessible_directories() -> str:
    """列出当前用户与会话可访问的文件目录清单、读写权限（只读/可写）及推荐用途说明。

    使用规则：
    - 当 AI 需要了解自身当前可访问哪些目录、需要确定文件落盘位置（如生成报告、导出 Excel/PDF、写入临时代码）、或查看公共与个人空间区别时调用此工具。
    - 读写或搜索文件前若不确定目标文件路径，或遇到文件找不到/写入被拒时，必须优先调用本工具查看全景映射，严禁盲猜路径。
    - 本工具只负责可访问目录、权限和路径映射；目标目录已经明确时无需重复调用本工具。
    - 清楚区分「平台公共文档与手册 (data/docs/)，只读」、「系统公共技能库 (skills/)，只读」、「用户专属持久化文档库 (docs/)，可写」、「当前会话临时工作区 (sessions/{cid}/)，可写」和「用户上传附件 (uploads/)，可读写」。
    - 当公共 docs 未命中时，可把 platform_help_files 中列出的服务根目录一级 *.md 作为平台帮助文档兜底来源；仍只能通过宿主 Read/Glob/Grep 只读访问。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出目录清单。"

    try:
        from app.services.config_service import ConfigService, resolve_effective_sandbox_policy
        from app.services.ai.runtime.agentscope.workspace import (
            resolve_workspace_root,
            resolve_user_workspace_root,
            resolve_user_docs_dir,
            resolve_session_workdir,
            resolve_user_sessions_dir,
            extract_workspace_identity,
            resolve_workspace_user_key,
            default_workspace_root,
            USER_SESSIONS_DIR_NAME,
            _resolve_docker_public_docs_source,
            resolve_sandbox_logical_root,
            sandbox_policy_is_sandbox,
            sandbox_skill_layout,
            sandbox_personal_skills_path,
            SANDBOX_POLICY_DOCKER,
            SANDBOX_POLICY_K8S,
            SANDBOX_POLICY_LOCAL,
        )
        from app.utils.fs_access import (
            get_platform_skills_root,
            get_public_data_roots,
            get_user_uploads_dir,
            get_user_docs_dir,
            get_user_sessions_dir,
            get_user_sandbox_dir,
            get_user_private_workspace_root,
            get_public_runtime_help_files,
        )

        user_name = resolve_context_user_name(ctx)
        user_info = {
            "user_id": ctx.user_id,
            "id": ctx.user_id,
            "user_name": user_name,
            "username": user_name,
            "role": "admin" if ctx.is_admin else "user",
        }
        conversation_id = str(getattr(ctx, "conversation_id", "") or "").strip() or None

        policy_raw = await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL)
        effective_policy = resolve_effective_sandbox_policy(policy_raw, SANDBOX_POLICY_LOCAL)
        # 是否「Bash 跑在隔离环境」统一走 workspace 侧口径（含 e2b / ssh）：只认 docker/k8s
        # 会让 e2b/ssh 报成 backend_service，模型据此去访问后端技能路径，方向反了。
        is_sandbox = sandbox_policy_is_sandbox(effective_policy)
        # Bash 侧逻辑工作区根：docker/k8s=/workspace、e2b=/home/user/workspace、
        # ssh=系统配置的 remote_workdir。local 时为空（Bash 直接用后端真实路径）。
        sandbox_root = (
            await resolve_sandbox_logical_root(effective_policy) if is_sandbox else None
        )
        if is_sandbox and not sandbox_root:  # 配置读失败时退回历史默认，避免拼出 None/
            sandbox_root = "/workspace"
        # 宿主用户工作区（docs/sessions/uploads）只挂进 docker/k8s；e2b/ssh 只同步技能副本。
        workspace_mounted = is_sandbox and effective_policy in (
            SANDBOX_POLICY_DOCKER,
            SANDBOX_POLICY_K8S,
        )
        personal_skills_container_path = sandbox_personal_skills_path(effective_policy)
        skill_layout = sandbox_skill_layout(effective_policy)
        # 机读命名空间：几种沙箱的值必须可区分，否则模型在 K8s 下读到的执行环境仍是
        # "docker_sandbox"，容易做出错误的路径/工具推断。
        sandbox_namespace = (
            f"{effective_policy}_sandbox" if is_sandbox else "backend_service"
        )

        workspace_root = await resolve_workspace_root(ensure_exists=False)
        resolved_user_id, resolved_user_name = extract_workspace_identity(
            user_id=ctx.user_id,
            user_name=user_name,
            user_info=user_info,
        )
        user_key = resolve_workspace_user_key(user_id=resolved_user_id, user_name=resolved_user_name)
        user_host_workspace = get_user_private_workspace_root(user_info) or os.path.join(workspace_root, user_key)

        from app.utils.fs_paths import get_data_base_dir
        data_base = get_data_base_dir()
        is_container_env = data_base.startswith("/app/data") or os.path.exists("/.dockerenv")

        def _tool_paths(
            *,
            container_path: str | None,
            backend_path: str,
            file_tool_path: str | None = None,
        ) -> dict[str, Any]:
            """Describe the path accepted by each execution backend."""
            return {
                "bash": container_path if is_sandbox else backend_path,
                "file_tools": file_tool_path or backend_path,
            }

        def _path_namespace(container_path: str | None) -> dict[str, str | None]:
            return {
                "bash": (
                    sandbox_namespace
                    if is_sandbox and container_path
                    else None
                    if is_sandbox
                    else "backend_service"
                ),
                "file_tools": "backend_service",
            }

        def _workspace_container_path(*parts: str) -> str | None:
            """宿主用户工作区子目录在沙箱内的路径。

            只有 docker/k8s 把宿主用户工作区（docs/sessions/uploads/.trash）挂进沙箱；
            e2b/ssh 只同步技能副本，这些子目录在沙箱内不存在，必须返回 None，
            否则模型会照着清单去 Bash 里访问一个不存在的目录。
            """
            if not workspace_mounted:
                return None
            suffix = "/".join(str(part).strip("/") for part in parts if str(part or "").strip("/"))
            return f"{sandbox_root.rstrip('/')}/{suffix}" if suffix else sandbox_root

        # 用户私有目录详细路径
        docs_service_path = os.path.join(user_host_workspace, "docs")
        uploads_service_path = os.path.join(user_host_workspace, "uploads")
        skills_service_path = os.path.join(user_host_workspace, "skills")
        trash_service_path = os.path.join(user_host_workspace, ".trash")

        user_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": (
                    _workspace_container_path("docs") if is_sandbox else docs_service_path
                ),
                "backend_service_path": docs_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path("docs"),
                    backend_path=docs_service_path,
                    file_tool_path="docs",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path("docs"),
                ),
                "permission": "read_write",
                "category": "user_persistent_docs",
                "description": "用户专属持久化文档库。跨会话共享，AI 生成的最终分析报告、Markdown、Excel、PDF、图表及长久保存文件请默认保存在此目录。",
                "recommended_for": ["AI 产物落盘", "生成报告", "导出表格与文档", "跨会话复用文件"],
            }
        ]

        if conversation_id:
            session_phys = resolve_session_workdir(
                root=workspace_root,
                user_id=resolved_user_id,
                user_name=resolved_user_name,
                conversation_id=conversation_id,
                user_info=user_info,
            )
            session_container_path = (
                _workspace_container_path("sessions", os.path.basename(session_phys))
                if is_sandbox
                else None
            )
            session_file_tool_path = os.path.join(
                USER_SESSIONS_DIR_NAME,
                os.path.basename(session_phys),
            )
            user_directories.append({
                "directory_name": f"sessions/{conversation_id}",
                "container_sandbox_path": session_container_path if is_sandbox else session_phys,
                "backend_service_path": session_phys,
                "paths": _tool_paths(
                    container_path=(
                        session_container_path
                    ),
                    backend_path=session_phys,
                    file_tool_path=session_file_tool_path,
                ),
                "path_namespace": _path_namespace(
                    session_container_path,
                ),
                "permission": "read_write",
                "category": "session_scratchpad",
                "description": "当前会话的专属临时工作区。存放仅限本次对话使用的中间过程文件、临时脚本、计算缓存等。",
                "recommended_for": ["当前会话临时脚本", "中间过程缓存", "单次任务草稿"],
            })

        user_directories.extend([
            {
                "directory_name": "uploads",
                "container_sandbox_path": (
                    _workspace_container_path("uploads") if is_sandbox else uploads_service_path
                ),
                "backend_service_path": uploads_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path("uploads"),
                    backend_path=uploads_service_path,
                    file_tool_path="uploads",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path("uploads"),
                ),
                "permission": "read_write",
                "category": "user_uploads",
                "description": "用户上传的会话附件目录。存放用户在聊天界面上传的文件与原始数据资料。",
                "recommended_for": ["读取用户上传的文件", "查找会话原始输入资料"],
            },
            {
                "directory_name": "skills",
                # 两个 skills 路径必须分清：
                #   - <逻辑根>/skills 是运行时合并后的**公共技能副本**（AgentScope 的 .seed
                #     模板 + 各智能体分区，平台按公共技能库指纹在下次容器启动时自动重建），
                #     归 platform_global_skills 条目；
                #   - <逻辑根>/personal-skills 是本用户**个人技能源目录**的只读挂载（仅 docker/k8s），实时
                #     包含用户当前所有个人技能，技能自带脚本可以直接用 Bash 执行，
                #     不需要先复制到会话目录。
                # 宿主文件工具仍然使用用户工作区下的 skills/（清单相对路径 skills）。
                "container_sandbox_path": (
                    personal_skills_container_path if is_sandbox else skills_service_path
                ),
                "backend_service_path": skills_service_path,
                "paths": _tool_paths(
                    container_path=personal_skills_container_path,
                    backend_path=skills_service_path,
                    file_tool_path="skills",
                ),
                "path_namespace": _path_namespace(
                    personal_skills_container_path,
                ),
                "path_semantics": "user_personal_skills_source",
                "access_via": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                # 用户自己的技能可以在宿主侧改（Write/Edit），只有沙箱内是只读挂载。
                "permission": "read_write",
                "category": "user_personal_skills",
                "description": (
                    "用户个人专属自定义技能目录（技能源目录，含 SKILL.md 与自带脚本）。"
                    + (
                        (
                            f"沙箱内以**只读**方式挂载到 {personal_skills_container_path}"
                            "（实时、不是副本），可用 Bash 直接执行技能自带脚本；写操作请用宿主文件工具。"
                            if personal_skills_container_path
                            else "当前沙箱策略只把技能复制进 skills/ 副本，没有个人技能源挂载；"
                            "技能文本请用 read_skill_instruction(skill_id) 读取，"
                            "需要改技能请用宿主文件工具。"
                        )
                        if is_sandbox
                        else "通过宿主文件工具读写。"
                    )
                ),
                "recommended_for": ["个人自定义技能", "执行个人技能自带脚本"],
            },
            {
                "directory_name": ".trash",
                "container_sandbox_path": (
                    _workspace_container_path(".trash") if is_sandbox else trash_service_path
                ),
                "backend_service_path": trash_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path(".trash"),
                    backend_path=trash_service_path,
                    file_tool_path=".trash",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path(".trash"),
                ),
                "permission": "read_write",
                "category": "trash",
                "description": "用户工作区回收站。存放已删除但可恢复的文件。",
                "recommended_for": ["已删除文件归档"],
            },
        ])

        # 构建公共目录（只读）
        global_skills_service_path = get_platform_skills_root() or os.path.join(data_base, "skills")
        # 沙箱内技能根随逻辑根走：docker/k8s -> /workspace/skills、e2b -> /home/user/workspace/skills、
        # ssh -> <remote_workdir>/skills。local 不用它（Bash 直接用上面那个后端路径）。
        sandbox_skills_root = (
            f"{sandbox_root.rstrip('/')}/skills" if is_sandbox else None
        )
        branding_service_path = os.path.join(data_base, "branding")
        global_docs_service_path = os.path.join(data_base, "docs")
        public_docs_mounted = bool(
            workspace_mounted
            and (
                # K8s 适配器固定挂载 subPath docs（k8s_workspace.py 传
                # public_docs_mounted=True），无需探测宿主 docs 目录。
                effective_policy == SANDBOX_POLICY_K8S
                or _resolve_docker_public_docs_source() is not None
            )
        )

        # 被禁技能不再作为可读目录列出：技能根整体仍是可读的（脚本等附件是正常用法），
        # 但清单必须点名哪些技能子目录读不到，否则模型会照着 /app/data/skills/<id>/SKILL.md
        # 的固定形状去猜、去试。
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        forbidden_skill_ids = await get_forbidden_skill_ids()
        available_skill_ids: list[str] = []
        try:
            from app.services.ai.skill_resolver import list_global_skill_metas

            available_skill_ids = sorted(
                {
                    str(meta.get("id") or "").strip()
                    for meta in list_global_skill_metas()
                    if str(meta.get("id") or "").strip()
                    and str(meta.get("id") or "").strip() not in forbidden_skill_ids
                }
            )
        except Exception as exc:
            # 清单是提示性信息：列举失败不能把整个目录工具打成不可用
            logger.warning("[list_accessible_directories] skill ids unavailable: %s", exc)

        public_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": (
                    f"{sandbox_root.rstrip('/')}/public/docs"
                    if public_docs_mounted
                    else None
                    if is_sandbox
                    else global_docs_service_path
                ),
                "backend_service_path": global_docs_service_path,
                "paths": _tool_paths(
                    container_path=(
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else None
                    ),
                    backend_path=global_docs_service_path,
                    # When the read-only Docker mount exists, expose the same
                    # logical namespace to Read/Glob/Grep. The workspace
                    # adapter maps it to the backend docs root; leaking the
                    # backend absolute path makes the model confuse it with a
                    # host physical path.
                    file_tool_path=(
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else global_docs_service_path
                    ),
                ),
                "path_namespace": _path_namespace(
                    (
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else None
                    ),
                ),
                "access_via": (
                    ["Bash", "Read", "Glob", "Grep"]
                    if public_docs_mounted
                    else ["Read", "Glob", "Grep"]
                ),
                "permission": "read_only",
                "category": "platform_global_docs",
                "description": (
                    "平台全局公共文档与模板库（data/docs）。"
                    + (
                        f"沙箱内以只读方式挂载到 {sandbox_root.rstrip('/')}/public/docs；"
                        if public_docs_mounted
                        else "当前 Docker 沙箱未挂载，通过宿主文件工具只读查阅；"
                        if is_sandbox
                        else "通过宿主文件工具只读查阅。"
                    )
                ),
                "recommended_for": ["查阅公共产品手册", "参考公共标准模板与制度文档"],
            },
            {
                "directory_name": "skills",
                "container_sandbox_path": (
                    sandbox_skills_root if is_sandbox else global_skills_service_path
                ),
                "backend_service_path": global_skills_service_path,
                "paths": _tool_paths(
                    container_path=sandbox_skills_root if is_sandbox else None,
                    backend_path=global_skills_service_path,
                    file_tool_path=global_skills_service_path,
                ),
                "path_namespace": _path_namespace(
                    sandbox_skills_root if is_sandbox else None,
                ),
                "path_semantics": (
                    "per_user_seeded_copy" if is_sandbox else "platform_directory"
                ),
                "permission": "read_only",
                "category": "platform_global_skills",
                # 按「当前用户可用技能」收口：被禁技能不得出现在任何一份可读清单里
                # （它们的 SKILL.md 在 fs 读判定上也会被直接拒绝）。
                "available_skill_ids": available_skill_ids,
                "forbidden_skill_ids": sorted(forbidden_skill_ids),
                "description": (
                    "平台全局公共技能库。包含所有预置的专业 Agent 技能与工作流模板，"
                    + (
                        (
                            f"沙箱内通过 {sandbox_skills_root} 下的副本访问，"
                            "不是直接挂载 /app/data/skills；副本沿用 AgentScope 的 seed 布局，"
                            "技能目录在其点开头的隐藏子目录 .seed/ 下，"
                            f"仅在需要执行技能自带脚本时才用 Bash `ls -a {sandbox_skills_root}` "
                            "定位实际布局，不要假设固定子路径；该副本按当前可用技能打包，"
                            "技能新增/修改/删除/禁用后会在下次沙箱启动时自动重建"
                            "（正在运行的沙箱不会立即刷新）。"
                            if skill_layout != "flat"
                            else
                            f"沙箱内通过 {sandbox_skills_root} 下的平铺副本访问"
                            f"（形如 {sandbox_skills_root}/<skill_id>/SKILL.md，平铺副本，没有隐藏的种子模板子目录）；"
                            "技能变更后会在下次沙箱启动时重新复制。"
                        )
                        if is_sandbox
                        else (
                            f"local 执行策略下 Bash 与宿主文件工具（Read/Glob/Grep）使用同一套后端真实路径 "
                            f"{global_skills_service_path}，每个技能一个平铺子目录"
                            "（<skill_id>/SKILL.md），没有容器路径命名空间、也不存在沙箱专属的隐藏技能目录布局；"
                            "技能文本一律用 read_skill_instruction(skill_id) 读取，"
                            "只有需要执行技能自带脚本时才用 Bash 进入技能目录。"
                        )
                    )
                    + (
                        "其中 forbidden_skill_ids 里的技能已被管理员禁用，"
                        "其目录不可读取，禁止尝试读取或检索。"
                        if forbidden_skill_ids
                        else ""
                    )
                ),
                "recommended_for": ["读取系统公共技能指令", "查看内置工作流"],
            },
            {
                "directory_name": "branding",
                "container_sandbox_path": (
                    None if is_sandbox else branding_service_path
                ),
                "backend_service_path": branding_service_path,
                "paths": _tool_paths(
                    container_path=None,
                    backend_path=branding_service_path,
                    file_tool_path=branding_service_path,
                ),
                "path_namespace": _path_namespace(None),
                "access_via": ["Read", "Glob", "Grep"],
                "permission": "read_only",
                "category": "platform_branding_assets",
                "description": (
                    "平台公共品牌与静态资产目录。包含 Logo、图标等公共资源，"
                    "Docker 沙箱 Bash 不直接挂载此目录，请通过宿主文件工具只读查阅。"
                ),
                "recommended_for": ["读取公共静态资源"],
            }
        ]

        platform_help_files = [
            {
                "path": path,
                "container_sandbox_path": None,
                "backend_service_path": path,
                "paths": {
                    "bash": None,
                    "file_tools": path,
                },
                "path_namespace": {
                    "bash": None,
                    "file_tools": "backend_service",
                },
                "path_semantics": "service_root_host_file_tool_path",
                "access_via": ["Read", "Glob", "Grep"],
                "permission": "read_only",
                "category": "platform_root_help",
                "description": (
                    "平台服务根目录帮助文档兜底来源；仅允许服务根目录一级 *.md，"
                    "禁止通过沙箱 Bash 访问或递归扫描 /app。"
                ),
                "recommended_for": ["公共 docs 未命中时查阅平台帮助与 FAQ"],
            }
            for path in get_public_runtime_help_files()
        ]

        result = {
            "deployment_environment": "docker_container" if is_container_env else "host_machine",
            "sandbox_execution_mode": sandbox_namespace if is_sandbox else "host_local",
            "user_identity": {
                "user_id": ctx.user_id,
                "user_name": user_name,
                "user_key": user_key,
                "is_admin": bool(ctx.is_admin),
            },
            "user_workspace": {
                "container_sandbox_root": (
                    sandbox_root if is_sandbox else user_host_workspace
                ),
                "backend_service_root": user_host_workspace,
                "paths": {
                    "bash": sandbox_root if is_sandbox else user_host_workspace,
                    "file_tools": ".",
                },
                "path_namespace": {
                    "bash": sandbox_namespace if is_sandbox else "backend_service",
                    "file_tools": "backend_service",
                },
                "access": "read_write",
                "subdirectories": user_directories,
            },
            "public_directories": {
                "access": "read_only",
                "directories": public_directories,
            },
            "platform_help_files": platform_help_files,
            "usage_guidelines": [
                (
                    f"1. 在沙箱环境（{effective_policy}）下执行 Bash 命令或 Python 脚本时，请使用 paths.bash"
                    f"（沙箱内以 {sandbox_root} 开头）；调用 Read/Write/Edit/Glob/Grep 时请使用 paths.file_tools，"
                    "不能把 Bash 的容器路径直接当作文件工具路径；"
                    if is_sandbox
                    else "1. 当前为 local 执行策略：Bash 与 Read/Write/Edit/Glob/Grep 使用同一套后端真实路径"
                    "（见各目录的 paths.bash / paths.file_tools），不存在 /workspace 容器命名空间；"
                ),
                "2. 生成给用户的分析报告、导出的 Excel/PDF 或需长期保存的文件，请统一写入 docs/ 目录；",
                "3. 当前会话的临时计算脚本、中间缓存请写入 sessions/{conversation_id}/ 目录；",
                "4. 公共技能库 skills/ 和 branding/ 为只读空间，禁止尝试写入；",
                (
                    f"5. 公共 docs 在沙箱中通过 paths.bash={sandbox_root.rstrip('/')}/public/docs 只读访问；"
                    if public_docs_mounted
                    else "5. 公共 docs 当前未挂载到沙箱 Bash；"
                    if is_sandbox
                    else "5. 公共 docs 通过宿主文件工具只读访问；"
                )
                + (
                    "Read/Glob/Grep 也使用 paths.file_tools 指向的公共 docs 路径，系统会自动映射到后端公共 docs；"
                    if public_docs_mounted
                    else "文件工具使用 paths.file_tools 对应的后端 data/docs 路径；"
                )
                + "未命中时，可按 platform_help_files 使用宿主 Read/Glob/Grep 查阅服务根目录一级 *.md，禁止 Bash 或递归扫描 /app；",
                "6. 严禁尝试访问或臆造其他用户的私有目录路径（系统底层安全沙箱会自动拦截）。",
                (
                    (
                        f"7. 注意两份 skills 的区别：公共技能库的 {sandbox_skills_root} 是按当前可用技能打包的副本"
                        + (
                            "（沿用 AgentScope 的 seed 布局，技能目录在其点开头的隐藏子目录 .seed/ 下，"
                            f"仅在需要执行技能自带脚本时才用 Bash `ls -a {sandbox_skills_root}` 定位实际目录、"
                            "不要假设固定子路径；技能变更后会在下次沙箱启动时自动重建，运行中的沙箱不会立即刷新）；"
                            if skill_layout != "flat"
                            else (
                                f"（平铺复制，形如 {sandbox_skills_root}/<skill_id>/SKILL.md，没有隐藏的种子模板子目录；"
                                "技能变更后会在下次沙箱启动时重新复制）；"
                            )
                        )
                        + (
                            f"个人技能（category=user_personal_skills）源目录实时只读挂载在 "
                            f"{personal_skills_container_path}，技能自带脚本可直接用 Bash 执行，无需先复制到会话目录；"
                            if personal_skills_container_path
                            else "个人技能（category=user_personal_skills）没有源目录挂载，已随技能副本一起打包；"
                        )
                        + "技能文本请用 read_skill_instruction(skill_id) 读取；写入技能文件请用宿主文件工具（paths.file_tools）。"
                    )
                )
                if is_sandbox
                else f"7. 注意两份 skills 的区别：public_directories.skills 就是平台公共技能库本身（paths.bash 与 paths.file_tools 同值，均为后端真实路径 {global_skills_service_path}），每个技能一个平铺子目录（<skill_id>/SKILL.md），没有容器路径命名空间也没有沙箱专属的隐藏技能目录布局；个人技能（category=user_personal_skills）源目录在本人工作区的 skills/ 下；技能文本一律用 read_skill_instruction(skill_id) 读取，只有需要执行技能自带脚本时才用 Bash 进入技能目录；写入技能文件请用宿主文件工具（paths.file_tools）。",
            ],
        }

        if ctx.is_admin:
            result["admin_notice"] = "当前用户具有管理员权限，可通过系统管理端或全量文件浏览器查看 data/ 下所有用户的目录与系统数据。"

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[list_accessible_directories] failed: %s", e, exc_info=True)
        return f"列出可访问目录失败: {e}"


# Grounding 证据声明（双重保障）：使工具不仅在 registry 查表可解析，其自身对象也显式具备元数据
from app.services.ai.grounding.models import EvidenceType

list_accessible_datasets.evidence_types = frozenset({EvidenceType.INTERNAL_DATA})
list_accessible_datasets.evidence_policy = "allow_empty_success"

list_accessible_knowledge_bases.evidence_types = frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
list_accessible_knowledge_bases.evidence_policy = "allow_empty_success"

list_available_agents.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
list_available_agents.evidence_policy = "allow_empty_success"

list_accessible_directories.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
list_accessible_directories.evidence_policy = "allow_empty_success"

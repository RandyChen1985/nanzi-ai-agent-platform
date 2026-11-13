"""系统工具：列出当前用户「可见」的凭据清单。

只有元数据（编号 / secret_key / 名称 / 备注 / 分类 / 状态），**没有任何凭据值**。
值只在注入阶段由平台解密、直接写进出站请求，既不进模型上下文，也不进工具出参。

为什么要这个工具：在它之前，「我有哪些凭据」在对话里是黑盒——用户问「我有几个
邮箱账号？」模型答不了，只能让他自己去凭据中心翻。它也让模型能先看清候选，再
决定这次调用怎么组织。

它和规则里的 `__secret_choice` 是配合关系，不是替代：凭据参数已经从模型可见的
schema 里移除，模型即使 list 出想要的那条也没有字段可以「指定用它」；本工具负责
「看见有哪些」，`__secret_choice` 负责「这一次用哪条」。两边用同一套编号。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.ai.tools.tool_compat import tool
from app.services.secrets.center import list_visible_secrets

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


def ref_of(index: int) -> str:
    """候选项的短编号。

    编号 = 「按 secret_key 排序后的序号」，所以调用方（工具出参、规则候选注入）
    只要用同一个排序就能重建出同一套编号——这是模型把 ``c3`` 回传给平台后还能
    找回同一条凭据的前提。
    """
    return f"c{index}"


def _catalog_sort_key(row: Any) -> tuple[str, str]:
    return (str(getattr(row, "secret_key", "") or ""), str(getattr(row, "id", "") or ""))


async def _catalog_enabled() -> bool:
    """平台开关，默认开。

    关掉的口子留给需要最严格合规的租户：这个能力会把「某用户有哪些凭据」写进
    对话上下文与 trace，该由平台决定要不要给。
    """
    from app.services.config_service import ConfigService

    try:
        configs = await ConfigService.get_all_from_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SecretCatalog] 读取平台开关失败，按默认（开启）处理: %s", exc)
        return True
    meta = configs.get("secret_policy_allow_catalog") if isinstance(configs, dict) else None
    value = meta.get("value") if isinstance(meta, dict) else meta
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@tool
async def list_my_credentials(keyword: str = "", limit: int = _DEFAULT_LIMIT) -> str:
    """列出当前用户「可见」的凭据清单（编号 / secret_key / 名称 / 备注 / 分类 / 状态）。

    使用规则：
    - 当用户问「我有哪些凭据」「我绑了几个邮箱账号」「有没有某个系统的 key」时调用。
    - **只返回元数据，没有任何凭据值**。不要据此编造值、也不要假装知道值；值由平台
      在调用工具时注入，模型看不到也不该问用户要。
    - ``secret_key`` 是这条凭据在配置里的标识（例如 ``A`` / ``B`` / ``CC``）；规则就是
      按它做精确匹配的。用户说「用 C 的账号」而清单里只有 ``CC`` 时，很可能指的就是它，
      但要让用户确认，不要自行认定。
    - ``remark`` 是用户自己写的备注，常能看出用途（例如「C 的邮箱账号」）。
    - ``ref`` 是本轮内的短编号，可以原样引用给用户看（例如「第 3 条」），
      但它只在本轮有效，不要跨对话使用。
    - 条目多时用 ``keyword`` 收窄（匹配 secret_key / 名称 / 备注 / 分类，不区分大小写），
      默认最多 20 条、上限 50 条；``truncated=true`` 表示还有未展示的条目。
    - 只看得到「你自己的凭据 + 已授权给你的系统级凭据」。未授权的系统级凭据
      **连名称都不会出现**——查不到就如实说「没有」，不要猜。
    - 输出里没有 ``value`` 字段是设计如此，不是缺字段。

    Args:
        keyword: 可选。按 secret_key / 名称 / 备注 / 分类模糊匹配，不区分大小写。
        limit: 可选。最多返回几条，默认 20，最大 50。
    """
    ctx = get_current_agent_context()
    user_id = getattr(ctx, "user_id", None)
    if not user_id:
        return json.dumps(
            {"items": [], "total": 0, "truncated": False, "note": "无法确定当前用户，未查询凭据清单"},
            ensure_ascii=False,
        )

    if not await _catalog_enabled():
        return json.dumps(
            {"items": [], "total": 0, "truncated": False, "note": "平台已关闭「列出我的凭据」能力"},
            ensure_ascii=False,
        )

    try:
        wanted = int(limit or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        wanted = _DEFAULT_LIMIT
    wanted = max(1, min(wanted, _MAX_LIMIT))

    try:
        async with AsyncSessionLocal() as session:
            # 部门授权也要算进来：少了 dept_code，通过部门获得的系统级凭据会「能用
            # 但列不出来」，用户会以为清单不全。
            # User.id 为整型主键；在 PostgreSQL/asyncpg 下直接传入 str 会报类型错误。
            try:
                uid_int: int | None = int(user_id)
            except (TypeError, ValueError):
                uid_int = None
            user = await session.get(User, uid_int) if uid_int is not None else None
            dept_code = getattr(user, "dept_code", None) if user is not None else None
            rows = await list_visible_secrets(
                session,
                user_id=str(user_id),
                dept_code=dept_code,
                only_active=True,
            )
    except Exception as exc:  # noqa: BLE001 - 清单读不到不该炸掉整轮对话
        logger.warning("[SecretCatalog] 读取可见凭据失败: %s", exc)
        return json.dumps(
            {"items": [], "total": 0, "truncated": False, "note": "凭据清单暂时读不到，请稍后再试"},
            ensure_ascii=False,
        )

    ordered = sorted(rows, key=_catalog_sort_key)
    needle = (keyword or "").strip().lower()

    items: list[dict[str, Any]] = []
    for index, row in enumerate(ordered, start=1):
        item = {
            "ref": ref_of(index),
            "secret_key": str(getattr(row, "secret_key", "") or ""),
            "name": str(getattr(row, "name", "") or ""),
            "remark": (getattr(row, "remark", None) or None),
            "category": (getattr(row, "category", None) or None),
            "status": str(getattr(row, "status", "") or ""),
        }
        if needle:
            haystack = " ".join(
                str(item[key] or "") for key in ("secret_key", "name", "remark", "category")
            ).lower()
            if needle not in haystack:
                continue
        items.append(item)

    total = len(items)
    truncated = total > wanted
    items = items[:wanted]

    note: Optional[str] = None
    if not items:
        note = (
            "没有匹配到凭据"
            if needle
            else "你名下没有可用凭据（或都没有授权给你）"
        )
    elif truncated:
        note = f"还有 {total - wanted} 条未展示，可用 keyword 收窄"

    return json.dumps(
        {
            "items": items,
            "total": total,
            "truncated": truncated,
            "note": note,
        },
        ensure_ascii=False,
    )

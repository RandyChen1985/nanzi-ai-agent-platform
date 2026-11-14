"""模型端凭据数量统计，兼容旧候选编号；不返回录入明细或凭据值。"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.ai.tools.tool_compat import tool
from app.services.secrets.center import list_visible_secrets
from app.services.secrets.policy import SCOPE_SYSTEM, SCOPE_USER, TYPE_CREDENTIAL
from app.services.secrets.structured_payload import LEGACY_FORMAT, STRUCTURED_FORMAT

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


async def _catalog_enabled(*, strict: bool = False) -> bool:
    """平台开关，默认开。

    关掉的口子留给需要最严格合规的租户：这个能力会把「某用户有哪些凭据」写进
    对话上下文与 trace，该由平台决定要不要给。
    """
    from app.services.config_service import ConfigService

    try:
        configs = await ConfigService.get_all_from_db()
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        logger.warning("[SecretCatalog] 读取平台开关失败，按默认（开启）处理: %s", exc)
        return True
    meta = configs.get("secret_policy_allow_catalog") if isinstance(configs, dict) else None
    value = meta.get("value") if isinstance(meta, dict) else meta
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _credential_counts(rows: list[Any]) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "total": 0,
        "enabled": 0,
        "single_value": {"total": 0, "enabled": 0},
        "structured": {"total": 0, "enabled": 0},
        "other": {"total": 0, "enabled": 0},
    }
    formats = {LEGACY_FORMAT: "single_value", STRUCTURED_FORMAT: "structured"}
    for row in rows:
        if row.secret_type != TYPE_CREDENTIAL:
            continue
        group = formats.get(row.credential_format, "other")
        enabled = int(row.status == "active")
        counts["total"] += 1
        counts["enabled"] += enabled
        counts[group]["total"] += 1
        counts[group]["enabled"] += enabled
    return counts


def _unavailable_catalog(code: str, note: str) -> str:
    # 未查询成功不能以 0 条回答，也不能为对话提供成功的运行状态证据。
    return json.dumps(
        {"status": "unavailable", "success": False, "code": code, "note": note},
        ensure_ascii=False,
    )


@tool
async def list_my_credentials(keyword: str = "", limit: int = _DEFAULT_LIMIT) -> str:
    """统计当前用户保存和获授权共享的凭据，包含单值与结构化凭据，不返回值或录入明细。

    - 问「我保存了多少条」使用 owned.total；shared.total 是获授权共享的数量。
    - total 是两者合计；owned/shared 内的 single_value、structured、other 分别按格式统计。
    - total 包含停用和过期的已保存记录；enabled 只表示状态为启用，不保证未过期或可调用。
    - 数量不含变量，也不含另存在 MCP 服务设置中的认证信息。
    - legacy_choices.items 保留旧 c1/c2 候选编号，仅用于旧版单值注入。
      legacy_choices.total/truncated 只描述编号候选，不是凭据统计，不能用于回答保存数量。
    - limit 仅限制返回的旧编号，所有统计始终完整。keyword 不筛选统计或检索账号。
    - status=unavailable 表示未取得统计，不能回答「0 条」。名称、账号等明细请查看凭据中心。

    Args:
        keyword: 保留的兼容参数，不筛选数量。
        limit: 最多返回的旧候选编号个数，最大 50，不影响统计。
    """
    ctx = get_current_agent_context()
    user_id = getattr(ctx, "user_id", None)
    if not user_id:
        return _unavailable_catalog("CURRENT_USER_UNAVAILABLE", "无法确定当前用户，未查询凭据清单")

    if not await _catalog_enabled():
        return _unavailable_catalog("CREDENTIAL_CATALOG_DISABLED", "平台已关闭「列出我的凭据」能力")

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
            if user is None:
                return _unavailable_catalog("CURRENT_USER_UNAVAILABLE", "当前用户已不可用，未查询凭据清单")
            dept_code = getattr(user, "dept_code", None)
            rows = await list_visible_secrets(
                session,
                user_id=str(user_id),
                dept_code=dept_code,
                only_active=False,
                legacy_only=False,
            )
            # 与注入层复用原 SQL 条件，包含数据库自身的字符串比较规则。
            legacy_rows = await list_visible_secrets(
                session, user_id=str(user_id), dept_code=dept_code, only_active=True, legacy_only=True,
            )
    except Exception as exc:  # noqa: BLE001 - 清单读不到不该炸掉整轮对话
        logger.warning("[SecretCatalog] 读取可见凭据失败: %s", exc)
        return _unavailable_catalog("CREDENTIAL_CATALOG_UNAVAILABLE", "凭据清单暂时读不到，请稍后再试")

    owned = _credential_counts([row for row in rows if row.owner_scope == SCOPE_USER])
    shared = _credential_counts([row for row in rows if row.owner_scope == SCOPE_SYSTEM])
    # 旧编号必须与 injection._resolve_choices 的 active + legacy 范围一致。
    # 不剔除其中的变量或自身已过期记录，不加入结构化凭据，避免已有 cN 移位。
    choice_total = len(legacy_rows)
    choices = {
        "items": [{"ref": ref_of(index)} for index in range(1, min(choice_total, wanted) + 1)],
        "total": choice_total,
        "truncated": choice_total > wanted,
    }
    note = (
        "我保存的凭据数量看 owned.total；获授权共享的看 shared.total；total 为两者合计。"
        "统计包含单值和结构化凭据，也包含停用或过期的记录，不含变量或 MCP 服务设置中的认证。"
        "enabled 仅表示状态启用，实际使用还要校验有效期、绑定、场景、目标和功能开关。"
        "legacy_choices 仅提供旧候选编号，可能包含变量或已过期的旧记录；编号截断不影响完整统计。"
        "本结果不提供名称、账号等明细，不能据此判断某条具名凭据是否存在。"
    )

    return json.dumps(
        {
            "status": "success",
            "total": owned["total"] + shared["total"],
            "enabled": owned["enabled"] + shared["enabled"],
            "owned": owned,
            "shared": shared,
            "truncated": False,
            "legacy_choices": choices,
            "note": note,
        },
        ensure_ascii=False,
    )

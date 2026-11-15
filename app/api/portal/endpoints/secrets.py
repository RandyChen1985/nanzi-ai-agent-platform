"""凭据中心门户端点。

**路由顺序有讲究**：``/secrets/usage``、``/secrets/picker``、``/secrets/policy``、
``/secrets/prefs``、``/secrets/bindings``、``/secrets/admin/*`` 这些**静态前缀必须
注册在 ``/secrets/{secret_id}`` 之前**。FastAPI 按注册顺序匹配，反了的话
``GET /secrets/usage`` 会被当成 ``secret_id="usage"`` 吞掉——本项目在
``/skills/gallery`` 上已经踩过一次。

**没有一条路径返回凭据明文**：
``credential`` 类型连创建者和管理员都读不到，唯一的出口 ``GET /{id}/value``
只对 ``variable``（租户号、接口地址这类）开放。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_api_key
from app.core.orm import get_db_session
from app.models.secret import SecretUserPref, UserSecret
from app.services.secrets import center
from app.services.secrets.injection import denial_text
from app.services.secrets.center import SecretPermissionError, SecretValidationError
from app.services.secrets.policy import (
    ALL_SCENES,
    CONSUMER_API_TOOL,
    CONSUMER_MCP,
    SCOPE_SYSTEM,
    SCOPE_USER,
    TYPE_CREDENTIAL,
    TYPE_VARIABLE,
    resolve_api_tool_slot,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 凭证类的统一掩码。前端不要自己拼，统一取这个字段。
MASK = "••••••••••••"


# ── 请求 / 响应模型 ────────────────────────────────────────────────────


class CreateSecretRequest(BaseModel):
    secret_key: str = Field(..., description="同一 owner 内唯一的标识，如 jira.prod.token")
    name: str = ""
    value: str = Field(..., description="凭据值；落库即加密，此后任何接口都不再返回")
    secret_type: str = TYPE_CREDENTIAL
    #: ``text`` = 单值（token / PEM 整段）；``json`` = 结构化对象序列化后的字符串。
    #: 两者都只存一列加密值，区别只在于「怎么读」，以及能不能按字段引用。
    value_format: str = Field(default="text", description="text | json")
    #: 结构化模板标识（``smtp`` / ``sftp`` / ``mysql`` …）。模板定义在前端常量，
    #: 后端只记标识用于回显与列表徽标，不校验字段结构。
    value_template: Optional[str] = Field(default=None, description="模板标识，仅 value_format=json")
    #: 模板里标记为 ``secret`` 的字段名。后端据此把它们从列表摘要里排除——
    #: **只传键名不传值**，所以摘要里的每个字都来自已加密的那份 JSON。
    value_secret_fields: List[str] = Field(default_factory=list)
    allowed_scenarios: List[str] = Field(default_factory=list)
    allowed_hosts: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    remark: Optional[str] = None
    expires_at: Optional[datetime] = None


class UpdateSecretRequest(BaseModel):
    name: Optional[str] = None
    allowed_scenarios: Optional[List[str]] = None
    allowed_hosts: Optional[List[str]] = None
    category: Optional[str] = None
    remark: Optional[str] = None
    expires_at: Optional[datetime] = None


class RotateRequest(BaseModel):
    value: str
    expires_at: Optional[datetime] = None
    clear_expires_at: bool = False
    overlap_days: int = Field(default=7, ge=0, le=30)
    #: 不传则沿用记录原来的形态；传了才允许在换代时切换单值/结构化。
    value_format: Optional[str] = None
    value_template: Optional[str] = None
    value_secret_fields: List[str] = Field(default_factory=list)


async def _audit_batch_rows(
    db: AsyncSession,
    *,
    resolved: List[Dict[str, Any]],
    consumer_type: str,
    consumer_id: str,
    actor_id: Optional[str],
    fallback_actor: Optional[str],
) -> None:
    """批量保存**提交成功之后**才补写审计。

    ``center._audit`` 用的是独立 session 和独立事务（见它的说明），所以不能在
    批量写库的中间调用——第 4 条失败整批回滚时，审计里会留下前 3 条「已绑定」，
    事后排查看到的是从没发生过的操作。漏记只是少一条线索，错记会误导判断。
    """
    actor = actor_id or fallback_actor
    for item in resolved:
        action = "unbind" if item["strategy"] == "none" else "bind"
        if item["strategy"] == "rule":
            action = "bind_rule"
        try:
            if action == "unbind":
                # 解绑的审计要带凭据快照，但那两条已经删了，只能按槽位记一笔
                await center._audit(  # noqa: SLF001 —— 同模块内的审计口径保持一处
                    enabled=True,
                    action="unbind",
                    secret=None,
                    actor_id=actor,
                    detail={
                        "consumer_type": consumer_type,
                        "consumer_id": consumer_id,
                        "slot": item["slot"],
                        "batched": True,
                    },
                )
                continue
            secret = None
            if item.get("secret_id"):
                secret = await db.get(UserSecret, item["secret_id"])
            detail = {
                "consumer_type": consumer_type,
                "consumer_id": consumer_id,
                "slot": item["slot"],
                "global": not fallback_actor,
                "value_path": item.get("value_path") or "",
                "batched": True,
            }
            if action == "bind_rule":
                # 规则正文进审计：和单条 bind_rule 同口径。只记「配过一条规则」
                # 的话，事后没人回答得出「当时按什么条件挑凭据」。
                detail["rule"] = item.get("rule") or {}
            await center._audit(  # noqa: SLF001
                enabled=True,
                action=action,
                secret=secret,
                actor_id=actor,
                detail=detail,
            )
        except Exception:  # noqa: BLE001
            # 审计失败不该让「已经保存成功」的请求变成 500
            logger.warning("[SecretAudit] 批量补写审计失败: slot=%s", item["slot"], exc_info=True)


class BindItem(BaseModel):
    """映射表里的一行：某个槽位由谁提供、取哪个字段。

    ``secret_id`` 为空表示**解除**这个槽位的绑定（映射表里被清空的那行）。
    """

    #: 直接给槽位（MCP 参数：``arg:短工具名:参数名``）
    slot: str = ""
    #: 或只给参数名，槽位由后端按工具的 URL/方法推算（API 工具）——
    #: 「GET 走 query、POST 走 body、占位符走 path」这类知识不该由前端复制一份
    name: str = ""
    #: ``fixed`` 用 ``secret_id``；``rule`` 用 ``rule``；``none`` 表示回到「模型填写」。
    #: 三种都能一次提交，多值凭据的两个来源因此共用同一个保存入口。
    strategy: str = "fixed"
    secret_id: str = ""
    rule: Dict[str, Any] | None = None
    value_path: str = ""
    allow_whole: bool = False


class BindBatchRequest(BaseModel):
    """一次保存整张映射表。

    单独一个接口而不是让前端循环调 ``/bindings``：一张表保存到一半失败，
    用户看到的界面和库里就对不上了。这里**先全量校验再写**，要么整张生效，
    要么一条都不动，并且把每条失败的原因原样返回。
    """

    consumer_type: str
    consumer_id: str
    is_global: bool = False
    items: list[BindItem]


class BindRequest(BaseModel):
    #: ``strategy=fixed`` 时必填；``strategy=rule`` 时留空（规则不指向具体凭据）
    secret_id: str = ""
    consumer_type: str
    consumer_id: str
    #: 直接指定槽位（``header:Authorization`` / ``env:VAR`` …）
    slot: str = ""
    #: 参数级绑定时只给参数名，槽位由后端按消费方推算。
    #: 前端不该关心「GET 走 query、POST 走 body、URL 占位走 path」。
    name: str = ""
    #: 全局绑定（``bound_by_user_id=''``）＝ 对所有调用者生效。
    #: 只有管理员能设，且只允许系统级凭据——用它给「全平台共用的服务」配一次即可。
    is_global: bool = False
    #: ``fixed``（把这个槽位绑到一条凭据）/ ``rule``（绑一条定位规则）
    strategy: str = "fixed"
    #: ``strategy=rule`` 时必填，形如 ``{"key_source": "arg:to", "on_miss": "model"}``
    rule: dict | None = None
    #: 只取凭据的某一部分：点分隔字段路径（``password`` / ``auth.token``）。
    #: 空串 = 整段给。结构化凭据必须给，否则注进去的是一段 JSON。
    value_path: str = ""
    #: 结构化凭据确实要整段给时显式置真。默认 False——把 SMTP 凭据整段注进
    #: ``smtp_password`` 几乎必然是配错了，不该让它在默认路径上发生。
    allow_whole: bool = False


class UnbindRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str = ""
    name: str = ""
    #: 解绑的是全局绑定还是我自己的那条。默认解自己的，避免误删管理员配置。
    is_global: bool = False
    #: 解绑哪一条：``fixed`` / ``rule``。默认 fixed —— 存量调用方都不传，行为不变
    strategy: str = "fixed"


class PrefsRequest(BaseModel):
    allow_script_env: bool


class PolicyRequest(BaseModel):
    allow_script_env: Optional[bool] = None
    allow_external_host: Optional[bool] = None
    max_inject_per_call: Optional[int] = Field(default=None, ge=1, le=20)


class StatusRequest(BaseModel):
    status: str = Field(..., description="active / disabled")


class GrantRequest(BaseModel):
    grantee_type: str = Field(..., description="user / dept / all")
    #: ``all`` 时留空——它不指向任何实体
    grantee_id: str = ""
    expires_at: Optional[datetime] = None


def _iso(value: datetime | None) -> str | None:
    """统一成带 UTC 标记的 ISO 8601。

    库里所有 datetime 都是 ``datetime.utcnow()`` 写的 **naive UTC**。原来输出
    ``"%Y-%m-%d %H:%M:%S"`` 把时区信息抹掉了，前端 ``new Date("2026-11-09T11:47:14")``
    会按**本地时区**解析——UTC+8 下每个时间都早 8 小时。用户 19:47 用过的凭据
    显示成「今天 11:47」，看着像另一件事。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def serialize_secret(row) -> Dict[str, Any]:
    """对外视图。**永不包含** ``value_encrypted`` 或解密后的值。"""
    is_variable = row.secret_type == TYPE_VARIABLE
    return {
        "id": row.id,
        "owner_scope": row.owner_scope,
        "owner_id": row.owner_id or None,
        "secret_key": row.secret_key,
        "name": row.name,
        "secret_type": row.secret_type,
        "category": row.category,
        "remark": row.remark,
        "status": row.status,
        "allowed_scenarios": row.allowed_scenarios or [],
        "allowed_hosts": row.allowed_hosts or [],
        "has_value": bool(row.value_encrypted),
        # 变量类展示前 8 位；凭证类永远是掩码。
        "value_preview": row.value_preview if is_variable else None,
        # 结构化变量没有「前 8 个字符」可看（那是 `{"host":`），退回摘要。
        "value_masked": (row.value_preview or row.value_summary or "") if is_variable else MASK,
        "readable": is_variable,
        # 值形态与模板：列表靠这两个字段区分「单值」和「结构化 · SMTP」。
        "value_format": row.value_format or "text",
        "value_template": row.value_template,
        # 摘要**只含非敏感字段**（host / 端口 / 用户名这类），凭证类也能有；
        # 它描述的是「这条凭据连的是哪儿」，不是值本身。
        "value_summary": row.value_summary,
        "expires_at": _iso(row.expires_at),
        "last_rotated_at": _iso(row.last_rotated_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        # 是否处于轮换双写窗口：前端可以提示「旧值仍在生效」
        "rotating_until": _iso(row.previous_expires_at),
    }


async def _audit_readable(
    db: AsyncSession, rows: list
) -> Dict[str, Dict[str, str]]:
    """把审计的 ``detail`` JSON 翻译成人话，并补齐「谁」和「绑到哪」。

    审计表存原始 JSON 是对的——迁移、回放、事后取证都靠它。但界面上直接摊 JSON
    等于没说：用户看到的是一个「绑定」标签和一片空白，既不知道绑到哪，也不知道
    是谁绑的。``detail`` 里其实有 consumer_id 和 slot，``actor_id`` 也有，
    只是从来没解析过。这里统一转成三件事：谁 → 对什么 → 做了什么。
    """
    parsed: List[Tuple[Any, Dict[str, Any]]] = []
    consumer_want: Dict[str, set] = {}
    user_want: set = set()

    for row in rows:
        try:
            data = json.loads(row.detail or "{}")
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        parsed.append((row, data))
        consumer_id = data.get("consumer_id")
        consumer_type = data.get("consumer_type")
        if consumer_id and consumer_type:
            consumer_want.setdefault(str(consumer_type), set()).add(str(consumer_id))
        if row.actor_id:
            user_want.add(str(row.actor_id))
        # 授权对象的真实姓名也要查，否则「授权给 12」等于没写
        if data.get("grantee_type") in ("user", "dept") and data.get("grantee_id"):
            user_want.add(str(data["grantee_id"]))

    from sqlalchemy import select

    target_names: Dict[Tuple[str, str], str] = {}
    if consumer_want:
        from app.models.mcp import McpServer
        from app.models.script_tool import SysScriptTool
        from app.models.tool import SysApiTool

        for consumer_type, model, name_column in (
            ("mcp_server", McpServer, "server_name"),
            ("api_tool", SysApiTool, "name"),
            ("script_tool", SysScriptTool, "name"),
        ):
            wanted = consumer_want.get(consumer_type) or set()
            if not wanted:
                continue
            named = (
                await db.execute(
                    select(model.id, getattr(model, name_column)).where(
                        model.id.in_(wanted)
                    )
                )
            ).all()
            for row_id, row_name in named:
                target_names[(consumer_type, str(row_id))] = str(row_name or "")

    user_labels: Dict[str, str] = {}
    if user_want:
        from app.models.user import User

        try:
            people = (
                await db.execute(
                    select(User.id, User.user_name, User.real_name).where(
                        User.id.in_(user_want)
                    )
                )
            ).all()
            for row_id, user_name, real_name in people:
                user_labels[str(row_id)] = str(real_name or user_name or row_id)
        except Exception as exc:  # noqa: BLE001 —— 查不到名字不该让整页 500
            logger.warning("[SecretAudit] 解析用户姓名失败 %s: %s", user_want, exc)

    def _target(data: Dict[str, Any]) -> str:
        key = (str(data.get("consumer_type") or ""), str(data.get("consumer_id") or ""))
        return target_names.get(key) or (data.get("consumer_id") or "")

    def _who(grantee_type: Any, grantee_id: Any) -> str:
        if grantee_type == "all":
            return "全体用户"
        if grantee_type == "dept":
            return f"部门 {user_labels.get(str(grantee_id), grantee_id)}"
        if grantee_type == "user":
            return user_labels.get(str(grantee_id)) or f"用户 {grantee_id}"
        return str(grantee_type or "")

    out: Dict[str, Dict[str, str]] = {}
    for row, data in parsed:
        actor = (
            row.actor_name
            or user_labels.get(str(row.actor_id))
            or (f"用户 {row.actor_id}" if row.actor_id else "系统")
        )
        target = _target(data)
        slot = str(data.get("slot") or "")
        where = f"{target} · {slot}" if (target and slot) else (target or slot)
        action = row.action or ""

        if action == "bind":
            text = where
            value_path = data.get("value_path")
            # 槽位末段就是参数名时不用再说一遍「取 x 字段」，那是废话
            if value_path and slot and not slot.endswith(str(value_path)):
                text += f"（取 {value_path} 字段）"
            if data.get("global"):
                text += " · 全局"
            elif data.get("batched"):
                text += " · 我绑的"
        elif action == "unbind":
            text = f"从 {where} 解绑" if where else "解绑"
        elif action == "bind_rule":
            text = f"{where} 的规则" if where else "规则变更"
        elif action == "grant":
            text = f"授权给 {_who(data.get('grantee_type'), data.get('grantee_id'))}"
            if data.get("expires_at"):
                text += f"，到期 {data['expires_at']}"
        elif action == "revoke":
            text = f"取消 {_who(data.get('grantee_type'), data.get('grantee_id'))} 的授权"
        elif action == "create":
            scenarios = "、".join(data.get("allowed_scenarios") or [])
            text = f"创建（{data.get('secret_type') or '凭据'}）"
            if scenarios:
                text += f"，场景 {scenarios}"
        elif action == "update":
            changed = [str(k) for k in data.keys() if k != "updated"]
            text = f"修改 {'、'.join(changed)}" if changed else "修改配置"
        elif action == "rotate":
            value_format = data.get("value_format")
            template = data.get("value_template")
            text = "轮换值"
            if value_format or template:
                text += f"（{'/'.join(str(item) for item in (value_format, template) if item)}）"
        elif action == "enable":
            text = "启用"
        elif action == "disable":
            text = "停用"
        elif action == "delete":
            text = "删除凭据"
        else:
            text = ""

        out[str(row.id)] = {"actor_label": str(actor), "detail_text": text}
    return out


async def _rule_shadow_map(
    db: AsyncSession, bindings: list
) -> Dict[Tuple[str, str, str], str]:
    """同槽位上存在 rule 绑定时，fixed 绑定被压制的程度。

    ``on_miss`` 决定 fixed 到底还有没有机会被选中：

    - ``fail``：规则未命中就整条失败，**不会**回退到 fixed —— 这条 fixed 是死数据
    - ``fallback_fixed``：规则未命中时才回退到它
    - ``model``：只有模型在编号字段里指定了这才用它

    不标出来的后果是「已绑定 N 处」看着像一定在用，实际一条都不会生效，
    用户只能翻流水表才发现。个人端与管理端共用一份计算，避免两边口径不一致。
    """
    from sqlalchemy import select

    from app.models.secret import SecretBinding

    fixed_slots = {
        (binding.consumer_type, str(binding.consumer_id), binding.slot)
        for binding in bindings
        if (getattr(binding, "strategy", None) or "fixed") == "fixed"
    }
    if not fixed_slots:
        return {}

    rule_rows = (
        await db.execute(
            select(
                SecretBinding.consumer_type,
                SecretBinding.consumer_id,
                SecretBinding.slot,
                SecretBinding.rule_json,
            )
            .where(SecretBinding.strategy == "rule")
            .where(SecretBinding.consumer_type.in_({item[0] for item in fixed_slots}))
            .where(SecretBinding.slot.in_({item[2] for item in fixed_slots}))
        )
    ).all()

    shadow: Dict[Tuple[str, str, str], str] = {}
    for row_type, row_consumer, row_slot, rule_json in rule_rows:
        key = (row_type, str(row_consumer), row_slot)
        if key not in fixed_slots:
            continue
        try:
            on_miss = (json.loads(rule_json or "{}") or {}).get("on_miss") or "fail"
        except (ValueError, TypeError):
            # 规则写坏时按最严格处理：宁可提示「不会生效」，也不要说成「会用上」
            on_miss = "fail"
        # 同槽位多条规则取最严格的一条：只要有一条能挡住，就不该说「会用上」
        if shadow.get(key) != "fail":
            shadow[key] = on_miss
    return shadow


async def _usage_stats(db: AsyncSession, secret_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    """按凭据聚合「绑了几个」「最近用过没有」「近 7 天成功/失败」。

    列表页一次要展示几十行，逐行查会变成 N+1；这里两张表各一次 GROUP BY。
    聚合失败直接返回空 dict，让列表照常显示——统计信息缺失不值得让整个页面报错。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import and_, case, func, select

    from app.models.secret import SecretBinding, SecretUsageLog

    if not secret_ids:
        return {}

    stats: Dict[str, Dict[str, Any]] = {
        sid: {"binding_count": 0, "last_used_at": None, "usage_7d": 0, "failed_7d": 0}
        for sid in secret_ids
    }
    try:
        binding_rows = (
            await db.execute(
                select(SecretBinding.secret_id, func.count(SecretBinding.id))
                .where(SecretBinding.secret_id.in_(secret_ids))
                .group_by(SecretBinding.secret_id)
            )
        ).all()
        for secret_id, count in binding_rows:
            stats[secret_id]["binding_count"] = int(count or 0)

        # 库里一律是 naive UTC（模型 default=datetime.utcnow），窗口也必须按 UTC
        # 算，否则「近 7 天」在 UTC+8 下整体偏移 8 小时。
        since = datetime.utcnow() - timedelta(days=7)
        in_window = SecretUsageLog.created_at >= since
        usage_rows = (
            await db.execute(
                select(
                    SecretUsageLog.secret_id,
                    # 「最近一次」是全量口径：两个月没用过也是“用过”。
                    # 只把下面两个计数扣在 7 天窗口内。
                    func.max(SecretUsageLog.created_at),
                    func.sum(case((in_window, 1), else_=0)),
                    func.sum(
                        case(
                            (and_(in_window, SecretUsageLog.result != "success"), 1),
                            else_=0,
                        )
                    ),
                )
                .where(SecretUsageLog.secret_id.in_(secret_ids))
                .group_by(SecretUsageLog.secret_id)
            )
        ).all()
        for secret_id, last_used, total, failed in usage_rows:
            stats[secret_id]["last_used_at"] = _iso(last_used)
            stats[secret_id]["usage_7d"] = int(total or 0)
            stats[secret_id]["failed_7d"] = int(failed or 0)
    except Exception:  # noqa: BLE001 - 统计失败不影响列表
        # 这里曾经是 debug 级别。结果 ``func.case`` 这个非法写法在构造语句时就抛
        # TypeError，异常被静默吞掉，统计永远停在初始值，界面上所有凭据都显示
        # 「从未使用 / 0 次」——而使用记录里明明有几十条。用 warning 打出来，
        # 下次同样的坏写法至少能看见。
        logger.warning(
            "[Secrets] 聚合使用统计失败：列表仍可显示，但「最近使用」会不准确",
            exc_info=True,
        )
    return stats


def _user_ctx(user_info: Dict[str, Any]) -> tuple[str, str]:
    return str(user_info.get("user_id") or ""), str(user_info.get("dept_code") or "")


def _is_admin(user_info: Dict[str, Any]) -> bool:
    """管理员判定。

    统一成一个函数：这条判断在本文件里出现了 5 次，散着写迟早会有一处漏掉
    字段名变化（`role` / `is_admin`），而它决定的是「能不能动系统级凭据」。
    """
    return str(user_info.get("role") or "") == "admin"


def _owner_ids(owners: Iterable[Any]) -> list[int]:
    """把「绑定者 id」收敛成整型列表，供 ``User.id.in_()`` 使用。

    User.id 是整型主键，而 bindings 里存的是字符串。MySQL 靠隐式转换能过，
    PostgreSQL（asyncpg）会直接报 operator does not exist: integer = character
    varying，接口 500。非数字值直接丢弃，不猜。
    """
    return sorted({int(item) for item in owners if str(item).isdigit()})


def _actor_id(user: Mapping[str, Any] | None) -> str | None:
    """审计操作人。

    两处都不能想当然：
    - 键名是 ``user_id`` 而不是 ``id``（``AuthService._user_model_to_data``
      产出的身份字典里就叫这个），读错就永远返回 None，审计照样缺失；
    - 值是**字符串**：``SecretAuditLog.actor_id`` 是 ``String(64)``，MySQL 会
      隐式转换混过去，PostgreSQL（asyncpg）严格类型会直接报错。

    拿不到就老实记 None，绝不编一个 id 出来。
    """
    if not isinstance(user, Mapping):
        return None
    raw = user.get("user_id")
    if raw in (None, ""):
        raw = user.get("id")  # 兼容直接传 {"id": ...} 的调用方
    if raw in (None, ""):
        return None
    return str(raw)


def _fail(exc: SecretValidationError) -> HTTPException:
    if isinstance(exc, SecretPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _rule_payload(binding: Any) -> dict | None:
    """规则行反序列化。坏 JSON 返回 None，让界面显示「规则不合法」，不抛 500。"""
    raw = getattr(binding, "rule_json", None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _rule_summary_of(binding: Any) -> str | None:
    """给界面的一句话摘要。规则坏了要说「需重新配置」，不能静默空白。"""
    from app.services.secrets.locator import RuleError, parse_rule, rule_summary

    raw = getattr(binding, "rule_json", None)
    if not raw:
        return None
    try:
        summary = rule_summary(parse_rule(raw))
    except RuleError:
        return "规则不合法（需重新配置）"
    # 摘要里带上取用字段：规则列表光看「按部门挑凭据」看不出命中后注进去的是什么
    path = (getattr(binding, "value_path", "") or "").strip()
    return f"{summary} · 取 {path}" if path else summary


def _identity_attrs(user_info: Dict[str, Any]) -> dict[str, str]:
    """试算用的身份字段：与注入层 ``_current_user_attrs`` 保持同一口径。"""
    info = user_info or {}
    attrs: dict[str, str] = {}
    for key in ("email", "user_name", "username", "real_name", "dept_code", "user_id"):
        value = info.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        attrs[key] = value if isinstance(value, str) else str(value)
    if "user_name" not in attrs and "username" in attrs:
        attrs["user_name"] = attrs["username"]
    return attrs


# ══════════════════════════════════════════════════════════════════════
# 静态前缀：必须全部排在 /{secret_id} 之前
# ══════════════════════════════════════════════════════════════════════


@router.get("/usage", summary="凭据使用记录")
async def list_usage(
    secret_id: Optional[str] = Query(default=None),
    user_keyword: Optional[str] = Query(
        default=None,
        max_length=64,
        description="按用户筛选：ID、用户名、姓名或邮箱，子串匹配",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    is_admin = _is_admin(user_info)
    rows = await center.list_usage(
        db,
        user_id=None if is_admin else user_id,
        secret_id=secret_id,
        user_keyword=user_keyword,
        limit=limit,
    )
    # 用户展示信息单独批量取：流水表只存 user_id，界面光显示「用户 7」没法排查
    # 「某用户说用不了」——对方是谁还得先去用户表反查。
    users = await center.resolve_usage_users(db, rows)
    return {
        "code": 0,
        "data": [
            {
                "id": r.id,
                "secret_id": r.secret_id,
                "secret_key": r.secret_key_snapshot,
                "owner_scope": r.owner_scope,
                "user_id": r.user_id,
                "user_name": (users.get(str(r.user_id)) or {}).get("user_name"),
                "user_real_name": (users.get(str(r.user_id)) or {}).get("real_name"),
                "user_email": (users.get(str(r.user_id)) or {}).get("email"),
                "scene": r.scene,
                "consumer_type": r.consumer_type,
                "consumer_id": r.consumer_id,
                "slot": r.slot,
                "target_host": r.target_host,
                "result": r.result,
                "trace_id": r.trace_id,
                # 排查「这条凭据是不是那次对话用的」要靠它；trace_id 只够定位单次调用
                "conversation_id": r.conversation_id,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/usage/stats", summary="使用记录统计（按凭据 / 场景 / 结果 / 天）")
async def get_usage_stats(
    secret_id: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """明细回答「刚才那次为什么失败」，统计回答「这周正常吗」。

    管理员看全局，普通用户只看自己——与 ``/usage`` 同口径。
    """
    user_id, _ = _user_ctx(user_info)
    is_admin = _is_admin(user_info)
    data = await center.usage_stats(
        db,
        user_id=None if is_admin else user_id,
        secret_id=secret_id,
        days=days,
    )
    return {"code": 0, "data": data}


@router.get("/audit", summary="凭据变更记录（谁在什么时候改了什么）")
async def list_audit(
    secret_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """与 ``/usage`` 分开：一个记「谁用了」，一个记「谁改了」。

    普通用户只看自己动过的；管理员不带 ``secret_id`` 时看全局。
    """
    user_id, _ = _user_ctx(user_info)
    is_admin = _is_admin(user_info)
    rows = await center.list_audit(
        db,
        secret_id=secret_id,
        actor_id=None if is_admin else user_id,
        limit=limit,
    )
    readable = await _audit_readable(db, rows)
    return {
        "code": 0,
        "data": [
            {
                "id": r.id,
                "secret_id": r.secret_id,
                "secret_key": r.secret_key_snapshot,
                "owner_scope": r.owner_scope,
                "actor_id": r.actor_id,
                "actor_name": r.actor_name,
                # 解析后的操作人：actor_name 常为空（历史数据），得靠 id 反查
                "actor_label": (readable.get(str(r.id)) or {}).get("actor_label"),
                "action": r.action,
                # 一句话说清「对什么做了什么」，避免前端各自解析 detail JSON
                "detail_text": (readable.get(str(r.id)) or {}).get("detail_text"),
                "detail": r.detail,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


@router.get(
    "/{secret_id}/fields",
    summary="这条凭据可以取哪几部分（结构化凭据的字段清单）",
)
async def secret_fields(
    secret_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """绑定入口的字段选择器要的数据。

    敏感字段只回名字、不回值——字段选择器不需要值，而返回了就变成「凭据可读」的
    旁路。非敏感字段给一段预览，纯粹为了让人认出「这个是端口」。

    单值凭据返回空 ``fields``，前端据此不显示字段选择器。
    """
    user_id, dept_code = _user_ctx(user_info)
    try:
        data = await center.describe_value_fields(
            db, secret_id=secret_id, user_id=user_id, dept_code=dept_code
        )
    except SecretPermissionError:
        # 与其它端点一致：未授权与不存在同样回 404，不透露「有这么一条凭据」
        raise HTTPException(status_code=404, detail="凭据不存在")
    return {"code": 0, "data": data}


@router.get("/picker", summary="凭据选择器数据（供绑定入口使用）")
async def picker(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """返回「我能选的凭据」，按来源分组，**不含任何值**。

    系统级只返回被授权的那几条——未授权的连名字都不出现。
    """
    user_id, dept_code = _user_ctx(user_info)
    # 选择器只列能用的：停用的摆进来，用户选中、绑定成功，调用时才发现被拒
    rows = await center.list_visible_secrets(
        db, user_id=user_id, dept_code=dept_code, only_active=True
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {"mine": [], "shared": []}
    for row in rows:
        is_system = row.owner_scope == SCOPE_SYSTEM
        grouped["shared" if is_system else "mine"].append(
            {
                "id": row.id,
                "secret_key": row.secret_key,
                "name": row.name,
                "secret_type": row.secret_type,
                "category": row.category,
                "status": row.status,
                # 虽然已经按 mine / shared 分好组，但组件里拿到的只有单条 item。
                # 「个人凭据绑到平台级工具上」要就地提示「只对你生效」，
                # 靠分组名传不到那个位置。
                "owner_scope": row.owner_scope,
                "allowed_scenarios": row.allowed_scenarios or [],
                # 与 serialize_secret 同口径：结构化变量没有前 8 位可看，退回摘要
                "value_masked": (row.value_preview or row.value_summary or "")
                if row.secret_type == TYPE_VARIABLE
                else MASK,
                # 下拉里要标出「单值 / 结构化·模板名」：结构化凭据绑定时必须再选字段，
                # 用户在下拉阶段就该看出来，而不是点保存才被后端拦下。
                "value_format": row.value_format or "text",
                "value_template": row.value_template or "",
            }
        )

    return {
        "code": 0,
        "data": {
            "groups": grouped,
            "counts": {"mine": len(grouped["mine"]), "shared": len(grouped["shared"])},
            "scenes": sorted(ALL_SCENES),
        },
    }


@router.get("/policy", summary="平台凭据注入策略")
async def get_policy(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.services.config_service import ConfigService

    configs = await ConfigService.get_all_from_db()

    def _raw(key: str):
        meta = configs.get(key) if isinstance(configs, dict) else None
        return meta.get("value") if isinstance(meta, dict) else meta

    def _flag(key: str, default: bool) -> bool:
        raw = _raw(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    try:
        max_inject = int(_raw("secret_policy_max_inject_per_call") or 3)
    except (TypeError, ValueError):
        max_inject = 3

    return {
        "code": 0,
        "data": {
            "allow_script_env": _flag("secret_policy_allow_script_env", False),
            "allow_external_host": _flag("secret_policy_allow_external_host", True),
            "max_inject_per_call": max_inject,
            "readonly": str(user_info.get("role") or "") != "admin",
        },
    }


@router.patch("/policy", summary="修改平台凭据注入策略（管理员）")
async def patch_policy(
    payload: PolicyRequest,
    admin: Dict[str, Any] = Depends(require_admin),
):
    from app.services.config_service import ConfigService

    updates: Dict[str, str] = {}
    if payload.allow_script_env is not None:
        updates["secret_policy_allow_script_env"] = "1" if payload.allow_script_env else "0"
    if payload.allow_external_host is not None:
        updates["secret_policy_allow_external_host"] = "1" if payload.allow_external_host else "0"
    if payload.max_inject_per_call is not None:
        updates["secret_policy_max_inject_per_call"] = str(payload.max_inject_per_call)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    # update_config_value 自己开 session 并在成功后清掉 60s 的整表缓存，
    # 所以这里不要再传 db，也不要再手动清缓存（策略变更必须立刻生效——
    # 关掉脚本注入不能等一分钟）。
    for key, value in updates.items():
        await ConfigService.update_config_value(
            key,
            value,
            changed_by=str(admin.get("user_id") or "admin"),
            change_reason="凭据注入策略调整",
        )
    logger.warning("[SecretPolicy] 管理员 %s 修改凭据策略: %s", admin.get("user_id"), updates)
    return {"code": 0, "message": "已更新", "data": updates}


@router.get("/prefs", summary="我的凭据注入偏好")
async def get_prefs(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    pref = await db.get(SecretUserPref, user_id)
    return {
        "code": 0,
        "data": {
            "allow_script_env": bool(pref and pref.allow_script_env),
            "updated_at": _iso(pref.updated_at) if pref else None,
        },
    }


@router.patch("/prefs", summary="修改我的凭据注入偏好")
async def patch_prefs(
    payload: PrefsRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    pref = await db.get(SecretUserPref, user_id)
    if pref is None:
        pref = SecretUserPref(user_id=user_id, allow_script_env=payload.allow_script_env)
        db.add(pref)
    else:
        pref.allow_script_env = payload.allow_script_env
    await db.commit()
    return {"code": 0, "message": "已保存", "data": {"allow_script_env": payload.allow_script_env}}


@router.get("/bindings", summary="绑定情况：按消费者查，或按凭据查")
async def get_bindings(
    consumer_type: str | None = Query(None),
    consumer_id: str | None = Query(None),
    secret_id: str | None = Query(None),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """两个方向共用一条路由。

    按消费者查（``consumer_type`` + ``consumer_id``）：配置页要知道「这个槽位绑了谁」。
    按凭据查（``secret_id``）：用户要知道「我这条凭据被绑到哪了」——否则只能去每个
    工具、每个 MCP 服务里翻，翻不完。

    两个方向都只返回 ``bound_by_user_id IS NULL OR = 我`` 的记录：别人的个人绑定
    与我无关，返回了反而是泄露。
    """
    from sqlalchemy import select

    from app.models.secret import SecretBinding, UserSecret

    if not secret_id and not (consumer_type and consumer_id):
        raise HTTPException(status_code=400, detail="请提供 secret_id，或 consumer_type + consumer_id")

    # 必须 outerjoin：规则绑定（strategy='rule'）的 secret_id 是空的，
    # INNER JOIN 会让这些行整行消失，界面上表现为「配了规则但列表里没有」。
    stmt = (
        select(SecretBinding, UserSecret)
        .outerjoin(UserSecret, UserSecret.id == SecretBinding.secret_id)
    )
    if secret_id:
        stmt = stmt.where(SecretBinding.secret_id == secret_id)
    else:
        stmt = stmt.where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
        )
    rows = (await db.execute(stmt)).all()
    user_id, _ = _user_ctx(user_info)

    # 槽位只有工具短名（``arg:send_mail:host``），光看它分不出是哪台 MCP 服务——
    # 同一个工具名可能出现在多台服务下，用户看到「MCP 服务 · arg:send_mail:host」
    # 根本不知道该去哪解绑。批量取名，查不到退回 id：目标可能已被删除，
    # 但至少还能定位。
    names: Dict[Tuple[str, str], str] = {}
    ids_by_type: Dict[str, set] = {}
    for binding, _secret in rows:
        ids_by_type.setdefault(binding.consumer_type, set()).add(
            str(binding.consumer_id)
        )
    if ids_by_type:
        from app.models.mcp import McpServer
        from app.models.script_tool import SysScriptTool
        from app.models.tool import SysApiTool

        for consumer_type, model, name_column in (
            ("mcp_server", McpServer, "server_name"),
            ("api_tool", SysApiTool, "name"),
            ("script_tool", SysScriptTool, "name"),
        ):
            wanted = ids_by_type.get(consumer_type) or set()
            if not wanted:
                continue
            named = (
                await db.execute(
                    select(model.id, getattr(model, name_column)).where(
                        model.id.in_(wanted)
                    )
                )
            ).all()
            for row_id, row_name in named:
                names[(consumer_type, str(row_id))] = str(row_name or "")

    rule_on_miss = await _rule_shadow_map(db, [binding for binding, _secret in rows])

    return {
        "code": 0,
        "data": [
            {
                "id": binding.id,
                "slot": binding.slot,
                # fixed / rule：界面据此决定显示「凭据名」还是「规则摘要」
                "strategy": getattr(binding, "strategy", None) or "fixed",
                "secret_id": binding.secret_id,
                "secret_key": secret.secret_key if secret else None,
                "secret_name": secret.name if secret else None,
                "owner_scope": secret.owner_scope if secret else None,
                # 绑定还在，但凭据可能已被停用/过期——界面要能标出来，
                # 否则用户看到「已绑定」却调不通，只能去翻日志
                "secret_status": secret.status if secret else None,
                # 值形态：列表里要标出「结构化 · 模板名」——结构化凭据绑定还带着
                # 「取哪个字段」，只说标识等于把最关键的一半藏起来了
                "value_format": (secret.value_format or "text") if secret else None,
                "value_template": (secret.value_template or None) if secret else None,
                "secret_expires_at": (
                    secret.expires_at.isoformat()
                    if secret and secret.expires_at
                    else None
                ),
                # 规则行原样回给前端（编辑时要回填），另给一句人话摘要
                "rule": _rule_payload(binding),
                "rule_summary": _rule_summary_of(binding),
                # 取了哪一部分。空串=整段。界面上「取用字段」列直接显示它，
                # 用户才能一眼看出这条绑定会不会把整段 JSON 注进去。
                "value_path": getattr(binding, "value_path", "") or "",
                "consumer_type": binding.consumer_type,
                "consumer_id": binding.consumer_id,
                # 绑定到哪台服务/哪个工具的名字。没有它，界面只能显示
                # 消费者类型和槽位，用户无法判断该去哪解绑
                "consumer_name": names.get(
                    (binding.consumer_type, str(binding.consumer_id))
                )
                or str(binding.consumer_id),
                # 同槽位存在 rule 绑定时的压制程度（仅对 fixed 有意义）：
                #   fail          → 规则未命中直接失败，这条 fixed 永远不会被选中
                #   fallback_fixed→ 规则未命中时才回退到它
                #   model         → 只有模型指定了编号字段才用它
                "rule_shadow": (
                    rule_on_miss.get(
                        (binding.consumer_type, str(binding.consumer_id), binding.slot)
                    )
                    if (getattr(binding, "strategy", None) or "fixed") == "fixed"
                    else None
                ),
                # 别人做的个人绑定不该被看见
                "bound_by_me": binding.bound_by_user_id == user_id,
                # 和凭据详情（``/{secret_id}``）保持同一个字段名：前端区分「全局绑定」
                # 与「我绑的」全靠它。少了这个字段时，管理端编辑平台 MCP 服务会把
                # 全局绑定整行过滤掉——界面显示「未选」，连解绑按钮都不出现。
                "is_global": not binding.bound_by_user_id,
            }
            for binding, secret in rows
            # 全局绑定迁移后是空串，迁移前是 NULL，两种都算「可见」
            if not binding.bound_by_user_id or binding.bound_by_user_id == user_id
        ],
    }


class CheckBindingRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str
    scene: str = "mcp"
    target_host: str | None = None
    rule_args: Dict[str, Any] | None = None


@router.post("/check-binding", summary="现在这个槽位能取到凭据吗（只校验，不出站）")
async def check_binding(
    payload: CheckBindingRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """把「调用时才会发现」的问题提前到配置时。

    刻意**不发任何出站请求**：只走一遍解析链路，回答「此刻这里取得到值吗、取不到
    是被哪道闸门拦的」。真实探测会把平台变成一个可以打任意内网地址的跳板，
    而 90% 的配置错误（过期、停用、场景不符、域名不在白名单、没授权）在这里就暴露了。

    返回值里**没有明文**，也没有脱敏值——只说能取到还是不能。
    """
    from app.services.secrets.resolver import secret_resolver

    user_id, dept_code = _user_ctx(user_info)
    result = await secret_resolver.resolve(
        user_id=user_id,
        scene=payload.scene,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=payload.slot,
        target_host=payload.target_host,
        dept_code=dept_code,
        user_attrs=_identity_attrs(user_info),
        rule_args=payload.rule_args,
        record_usage=False,
    )
    return {
        "code": 0,
        "data": {
            "ok": result.ok,
            "denial": result.denial,
            "reason": None if result.ok else denial_text(result.denial),
            "secret_key": result.secret_key,
        },
    }


@router.get("/platform-targets", summary="我可以给哪些平台资源绑自己的凭据")
async def list_platform_credential_targets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    kind: str = Query("all", pattern="^(all|mcp|tool)$"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """「我的 MCP → 平台服务」的完整数据源：平台 MCP 服务 + 平台 API 工具。

    普通用户看不到平台侧的任何管理界面（MCP 列表和工具列表都是管理员接口），
    所以「每个人用自己的 Token」这件事，用户没有任何入口。这条接口补上它，
    而且只给够用的字段：名称 + 我能绑的槽位 + 我已绑的状态。
    """
    from sqlalchemy import select

    from app.models.mcp import McpServer, McpToolCache
    from app.models.secret import SecretBinding, UserSecret
    from app.models.tool import SysApiTool
    from app.services.ai.tools.system_params import credential_param_names
    from app.services.ai.tools.tool_credentials import is_credential_placeholder
    from app.services.secrets.injection import is_header_value_unfilled

    user_id, _ = _user_ctx(user_info)

    # 分页：工具数量可能上百，一次全量返回既慢又白渲染。两类各自分页，
    # ``kind`` 让「我的 MCP」只取 MCP 那部分时不必白拉一遍工具。
    from sqlalchemy import func

    offset = (page - 1) * size
    servers: list[Any] = []
    server_total = 0
    tool_total = 0
    tools: list[Any] = []

    if kind in ("all", "mcp"):
        server_total = int(
            (await db.execute(
                select(func.count()).select_from(McpServer).where(McpServer.scope == "global")
            )).scalar() or 0
        )
        servers = list((await db.execute(
            select(McpServer)
            .where(McpServer.scope == "global")
            .order_by(McpServer.server_name)
            .offset(offset)
            .limit(size)
        )).scalars().all())

    if kind in ("all", "tool"):
        tool_total = int(
            (await db.execute(
                select(func.count()).select_from(SysApiTool).where(SysApiTool.is_active.is_(True))
            )).scalar() or 0
        )
        tools = list((await db.execute(
            select(SysApiTool)
            .where(SysApiTool.is_active.is_(True))
            .order_by(SysApiTool.name)
            .offset(offset)
            .limit(size)
        )).scalars().all())

    # outerjoin：规则绑定的 secret_id 是空的，INNER JOIN 会把它们整行丢掉，
    # 表现为「配了规则，个人面板里看不到」。
    rows = (
        await db.execute(
            select(SecretBinding, UserSecret).outerjoin(UserSecret, UserSecret.id == SecretBinding.secret_id)
        )
    ).all()
    # 先遍历全局绑定再遍历个人绑定，确保同一槽位同时存在全局与个人条目时由个人绑定覆盖全局绑定
    rows = sorted(rows, key=lambda pair: 0 if not pair[0].bound_by_user_id else 1)
    # 固定与规则分开两个 map：同一个 key（消费者+槽位）现在可能两条都在，
    # 塞进同一个 dict 必然互相覆盖。
    bindings: Dict[str, Dict[str, Any]] = {}
    rules: Dict[str, Dict[str, Any]] = {}
    for binding, secret in rows:
        # 别人的个人绑定与我无关；系统级绑定（bound_by_user_id 为空）要显示
        # 空串与 NULL 都表示全局绑定
        if binding.bound_by_user_id and binding.bound_by_user_id != user_id:
            continue
        key = f"{binding.consumer_type}:{binding.consumer_id}:{binding.slot}"
        if (getattr(binding, "strategy", None) or "fixed") == "rule":
            rules[key] = {
                "rule": _rule_payload(binding),
                "rule_summary": _rule_summary_of(binding),
                # 规则也要说清取哪个字段，否则编辑时回填不到、界面显示成「整段」
                "value_path": getattr(binding, "value_path", "") or "",
                "bound_by_me": binding.bound_by_user_id == user_id,
            }
            continue
        if secret is None:
            # 理论上不会发生（fixed 必填 secret_id），真发生了也别让整页 500
            continue
        bindings[key] = {
            "secret_id": secret.id,
            "secret_key": secret.secret_key,
            "secret_name": secret.name,
            "owner_scope": secret.owner_scope,
            "secret_status": secret.status,
            # 取了哪一部分。少了它，重新打开绑定弹窗时字段选择器会显示「未选」，
            # 用户以为没配，再选一次就把原来的配置覆盖掉了。
            "value_path": getattr(binding, "value_path", "") or "",
            # 值形态：卡片上要标出「结构化 · 模板名」，否则看不出取的是字段
            "value_format": secret.value_format or "text",
            "value_template": secret.value_template or None,
            "secret_expires_at": (
                secret.expires_at.isoformat() if secret.expires_at else None
            ),
            "bound_by_me": binding.bound_by_user_id == user_id,
        }

    def _binding(consumer_type: str, consumer_id: str, slot: str):
        return bindings.get(f"{consumer_type}:{consumer_id}:{slot}")

    def _rule(consumer_type: str, consumer_id: str, slot: str):
        return rules.get(f"{consumer_type}:{consumer_id}:{slot}")

    def _schema_param_names(raw: Any) -> list[str]:
        """JSON Schema 里的属性名。解析失败返回空——坏 schema 不该让整页打不开。"""
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        props = (data or {}).get("properties") if isinstance(data, dict) else None
        return [str(key) for key in props] if isinstance(props, dict) else []

    from app.services.mcp.mcp_auth_policy import resolve_mcp_auth_headers
    from app.services.secrets.injection import mcp_arg_slots

    mcp_items = []
    for server in servers:
        # 可绑槽位 = Authorization + 服务上已配置的其他认证头。
        # 只给 Authorization 的话，管理员给某个服务配了 X-API-Key 之后，用户
        # 在面板里根本看不到那个槽位，也就绑不了自己的值。
        try:
            header_names = [str(name) for name in resolve_mcp_auth_headers(server)]
        except Exception:
            # 解密失败不该让整页打不开；至少 Authorization 还能绑
            logger.warning("MCP 服务 %s 的认证头解析失败", server.id)
            header_names = []
        slots = ["header:Authorization"] + [
            f"header:{name}" for name in header_names if name.lower() != "authorization"
        ]

        # 参数级槽位：工具上声明了「值由凭据提供」的参数。漏掉它们的话用户在面板
        # 里看不到、也就绑不了，而模型侧这些参数已被剔除，工具会一直报缺参数。
        try:
            tool_rows = list((await db.execute(
                select(McpToolCache).where(McpToolCache.server_id == server.id)
            )).scalars().all())
        except Exception:
            logger.warning("MCP 服务 %s 的工具列表读取失败", server.id)
            tool_rows = []
        arg_options: Dict[str, list[str]] = {}
        for tool_row in tool_rows:
            pairs = mcp_arg_slots(tool_row)
            credential_names = {name for name, _ in pairs}
            plain = [
                name
                for name in _schema_param_names(getattr(tool_row, "parameter_schema", None))
                # 凭据参数自己不能当键：用一条凭据去决定另一条会形成依赖环
                if name not in credential_names
            ]
            for name, slot in pairs:
                if slot not in slots:
                    slots.append(slot)
                # 规则的键只能从「这次调用里模型填的其他参数」里选
                arg_options[slot] = [item for item in plain if item != name]

        mcp_items.append({
            "id": server.id,
            "server_name": server.server_name,
            "remark": getattr(server, "remark", None),
            # 字段叫 fixed_token_encrypted：只判有没有配，不解密
            "has_fixed_token": bool(getattr(server, "fixed_token_encrypted", None)),
            "bindable_slots": slots,
            "bindings": {slot: _binding("mcp_server", str(server.id), slot) for slot in slots},
            # 规则单独返回：它没有 secret_id，前端要显示的是「怎么找」而不是「哪条」
            "rules": {slot: _rule("mcp_server", str(server.id), slot) for slot in slots},
            # 配规则时可供选择的入参名（按槽位给，各工具的参数不同）
            "arg_options": arg_options,
            # 兼容旧字段：绑定面板最早只认单个 Authorization
            "binding": _binding("mcp_server", str(server.id), "header:Authorization"),
        })

    tool_items = []
    for tool in tools:
        try:
            headers = json.loads(tool.headers or "{}")
        except Exception:
            headers = {}
        if not isinstance(headers, dict):
            headers = {}
        # 只把「留空」或「还是占位符」的头列为可绑：手填了真实值的头，
        # 注入层是**只补空位**，绑了也不会生效，列出来就是骗人。
        bindable = [
            name
            for name, value in headers.items()
            if not str(value or "").strip() or is_credential_placeholder(value) or is_header_value_unfilled(value)
        ]
        # 参数位：参数定义里标了 source=credential 的，落到 path / query / body。
        # 这些槽位和 header 一样能被个人绑定，漏掉它们的话用户在面板里看不到、
        # 也就绑不了——工具会一直报「凭据未填」。
        #
        # **槽位必须由 resolve_api_tool_slot 推，不能自己按 method 拼**：
        # ``POST /refund/{applicant}`` 里的 applicant 是 path 参数，只看 method
        # 会算成 body:applicant，而运行时按 path:applicant 查——绑定写进去了、
        # 面板还显示「已绑定」，调用时永远匹配不到。
        try:
            param_names = credential_param_names(tool.parameter_schema)
        except Exception:
            # 一条工具的参数定义是坏 JSON，不该让整个「平台服务」页打不开
            logger.warning("工具 %s 的参数定义无法解析，跳过参数位", tool.id)
            param_names = []
        param_slots = [
            slot
            for slot in (
                resolve_api_tool_slot(
                    name=name, method=tool.method, url_template=tool.url_template
                )
                for name in param_names
            )
            if slot
        ]
        slots = [f"header:{name}" for name in bindable] + param_slots
        # 规则只能用「这次调用的其他参数」当键；header 位拿不到入参（注入层不传），
        # 所以只给参数位配 options，header 位留空数组由前端退化成「只用身份字段」
        normal = [
            name
            for name in _schema_param_names(tool.parameter_schema)
            if name not in set(param_names)
        ]
        arg_options = {
            slot: [item for item in normal if item != slot.split(":", 1)[1]]
            for slot in param_slots
        }
        tool_items.append({
            "id": tool.id,
            "name": tool.name,
            "description": tool.description,
            "bindable_slots": slots,
            "bindings": {
                slot: _binding("api_tool", str(tool.id), slot)
                for slot in slots
            },
            "rules": {
                slot: _rule("api_tool", str(tool.id), slot)
                for slot in slots
            },
            "arg_options": arg_options,
        })

    return {
        "code": 0,
        "data": {
            "mcp_servers": mcp_items,
            "api_tools": tool_items,
            "page": page,
            "size": size,
            "total": {"mcp_servers": server_total, "api_tools": tool_total},
            "has_more": {
                "mcp_servers": offset + len(mcp_items) < server_total,
                "api_tools": offset + len(tool_items) < tool_total,
            },
        },
    }


@router.get("/mcp-targets", summary="我可以给哪些平台 MCP 服务绑自己的凭据")
async def list_mcp_credential_targets(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只取 MCP 那部分的窄接口，``/platform-targets`` 的旧形态。

    保留它是因为「我的 MCP」页只想显示 MCP，拿一份包含 API 工具的响应再筛一遍
    没有意义；两份实现的绑定查询都收敛在 ``/platform-targets`` 里，这里只做投影。
    """
    payload = await list_platform_credential_targets(
        page=1, size=200, kind="mcp", user_info=user_info, db=db
    )
    return {"code": 0, "data": payload["data"]["mcp_servers"]}


async def _resolve_slot(
    db: AsyncSession, *, consumer_type: str, consumer_id: str, slot: str, name: str
) -> str:
    """槽位：给了就用，没给就按消费方推算。

    参数级绑定只需要参数名——前端算不准，而且不该算：`POST /refund/{applicant}`
    里的 `applicant` 是 path 参数、`GET` 的同名参数在 query、其它方法在 body，
    这三者都取决于工具自己的配置。
    """
    if slot:
        return slot
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="slot 和 name 至少要给一个")

    if consumer_type == CONSUMER_API_TOOL:
        from app.models.tool import SysApiTool

        tool = await db.get(SysApiTool, consumer_id)
        if tool is None:
            raise HTTPException(status_code=404, detail="工具不存在")
        return resolve_api_tool_slot(
            name=clean,
            method=getattr(tool, "method", None),
            url_template=getattr(tool, "url_template", None),
        )

    if consumer_type == CONSUMER_MCP:
        # MCP 的参数槽位带工具短名，光有参数名推不出来——前端本来就知道自己在
        # 哪个工具里，直接给完整槽位更省事。
        raise HTTPException(
            status_code=400,
            detail="MCP 参数请直接给槽位（arg:<短工具名>:<参数名>）",
        )

    raise HTTPException(status_code=400, detail=f"{consumer_type} 不支持按参数名绑定")


@router.post("/bindings/batch", summary="一次保存整张绑定映射表")
async def create_bindings_batch(
    payload: BindBatchRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """整张映射表的保存入口。

    「一条凭据供多个参数」在界面上是一张表，落库是多条绑定。分两步：
    先把每条当作校验跑一遍（``dry_run``），全部能过再真正写入。
    半成功是最糟的结果——界面显示保存成功，实际只有一部分参数拿到了凭据。
    """
    user_id, dept_code = _user_ctx(user_info)
    if not payload.items:
        return {"code": 0, "message": "没有需要保存的绑定", "data": {"saved": 0, "removed": 0}}

    # 不在这里对 `is_global` 一刀切：接口层看不到每条凭据的作用域。
    # 「管理员拿个人凭据绑平台级工具」是正常操作（先用自己的账号跑通），
    # 一刀切会把它当成越权拒掉。权限判断下沉到 bind_secret，按凭据作用域判：
    # 系统级凭据 + 全局 → 必须管理员；个人凭据 → 自动降级成本人绑定。
    is_admin = _is_admin(user_info)
    bound_by: str | None = None if payload.is_global else user_id

    # —— 第一轮：把槽位都解析出来，并全量校验；有一条不过就整体拒绝 ——
    errors: list[dict] = []
    resolved: list[dict] = []
    for item in payload.items:
        try:
            slot = await _resolve_slot(
                db,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=item.slot,
                name=item.name,
            )
        except HTTPException as exc:
            errors.append({"slot": item.slot or item.name, "detail": str(exc.detail)})
            continue
        raw = (item.strategy or "fixed").strip().lower()
        strategy = raw if raw in ("rule", "none") else "fixed"
        # 「回到模型填写」= 固定和规则两条都清。只清其中一条，另一条还会继续生效，
        # 用户看到的是「说好改回模型填写了，结果还在注凭据」。
        if strategy == "none" or (strategy == "fixed" and not item.secret_id):
            strategy = "none"
        resolved.append(
            {
                "slot": slot,
                "strategy": strategy,
                "secret_id": item.secret_id,
                "rule": item.rule,
                "value_path": item.value_path,
                "allow_whole": bool(item.allow_whole),
            }
        )
        if strategy == "rule":
            if not item.rule:
                errors.append({"slot": slot, "detail": "按规则匹配需要一并提交规则内容"})
                continue
            try:
                await center.bind_rule(
                    db,
                    consumer_type=payload.consumer_type,
                    consumer_id=payload.consumer_id,
                    slot=slot,
                    rule=item.rule,
                    bound_by_user_id=bound_by,
                    actor_id=_actor_id(user_info),
                    value_path=item.value_path,
                    dry_run=True,
                    # 规则不指向凭据，但「全局规则」照样影响所有人：
                    # 漏传这里，校验轮就会放行非管理员的全局规则，等到写入轮
                    # 才抛 SecretPermissionError——而写入轮没有 try/except，直接 500。
                    allow_global=is_admin,
                )
            except SecretPermissionError as exc:
                if "管理员" in str(exc):
                    errors.append({"slot": slot, "detail": str(exc)})
                else:
                    errors.append({"slot": slot, "detail": "凭据不存在或无权使用"})
            except SecretValidationError as exc:
                errors.append({"slot": slot, "detail": str(exc)})
            continue
        if not item.secret_id:
            continue  # 解除绑定只删不建
        try:
            await center.bind_secret(
                db,
                secret_id=item.secret_id,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=slot,
                bound_by_user_id=bound_by,
                dept_code=dept_code,
                actor_id=_actor_id(user_info),
                value_path=item.value_path,
                allow_whole=bool(item.allow_whole),
                dry_run=True,
                allow_global=is_admin,
            )
        except SecretPermissionError as exc:
            # 两种来源要分开说：「凭据看不见」不能透露存在性，
            # 「非管理员设全局」则是用户能自己改的操作错误。
            if "管理员" in str(exc):
                errors.append({"slot": slot, "detail": str(exc)})
            else:
                errors.append({"slot": slot, "detail": "凭据不存在或无权使用"})
        except SecretValidationError as exc:
            errors.append({"slot": slot, "detail": str(exc)})

    # 同一批里两行落到同一个槽位（`date` 和 `query:date` 都解析成 query:date）时，
    # 后一条会静默覆盖前一条——用户配的是两个参数，最后只生效一个。
    seen: dict[str, str] = {}
    for item in resolved:
        slot = item["slot"]
        label = item.get("name") or slot
        if slot in seen:
            errors.append(
                {
                    "slot": slot,
                    "detail": f"和前面的 {seen[slot]} 落到同一个槽位，得先去重",
                }
            )
        else:
            seen[slot] = label

    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"有 {len(errors)} 个参数没通过校验，未保存任何改动："
            + "；".join(f"{e['slot'] or '（空槽位）'} → {e['detail']}" for e in errors[:5]),
        )

    if not resolved:
        raise HTTPException(status_code=400, detail="没有需要保存的参数")

    # —— 第二轮：真正写入 ——
    saved = 0
    removed = 0
    for item in resolved:
        slot = item["slot"]
        if item["strategy"] == "none":
            for stale in ("fixed", "rule"):
                removed += await center.unbind_slot(
                    db,
                    consumer_type=payload.consumer_type,
                    consumer_id=payload.consumer_id,
                    slot=slot,
                    bound_by_user_id=bound_by,
                    strategy=stale,
                    commit=False,
                    # 审计延后到整批提交成功之后：_audit 用独立事务，
                    # 在这里写的话，第 4 条失败整批回滚时审计里已经留了前 3 条
                    audit=False,
                )
            continue
        if item["strategy"] == "rule":
            await center.bind_rule(
                db,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=slot,
                rule=item["rule"],
                bound_by_user_id=bound_by,
                actor_id=_actor_id(user_info),
                value_path=item["value_path"],
                dry_run=False,
                # 同样推迟到整批写完：中途失败时整批回滚
                commit=False,
                audit=False,
                allow_global=is_admin,
            )
            saved += 1
            continue
        if not item["secret_id"]:
            removed += await center.unbind_slot(
                db,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=slot,
                bound_by_user_id=bound_by,
                commit=False,
                audit=False,
            )
            continue
        await center.bind_secret(
            db,
            secret_id=item["secret_id"],
            consumer_type=payload.consumer_type,
            consumer_id=payload.consumer_id,
            slot=slot,
            bound_by_user_id=bound_by,
            dept_code=dept_code,
            actor_id=_actor_id(user_info),
            value_path=item["value_path"],
            allow_whole=item["allow_whole"],
            # 提交推迟到整批写完：中途失败时全部回滚，不留半张映射表
            commit=False,
            # 审计同理：提交成功后再写，不记没发生过的操作
            audit=False,
            allow_global=is_admin,
        )
        saved += 1

    # 整批一个事务：上面任何一步抛错，这一批整体回滚，不会留下半张映射表
    await db.commit()

    # 提交成功了才补写审计。这里逐条写而不是攒一批：批量接口的行数是个位数，
    # 而且 _audit 自己会吞异常，一条写失败不影响其余的。
    await _audit_batch_rows(
        db,
        resolved=resolved,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        actor_id=_actor_id(user_info),
        fallback_actor=bound_by,
    )

    return {
        "code": 0,
        "message": f"已保存 {saved} 条绑定" + (f"，解除 {removed} 条" if removed else ""),
        "data": {"saved": saved, "removed": removed},
    }


@router.post("/bindings", summary="把一个槽位绑到凭据上")
async def create_binding(
    payload: BindRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, dept_code = _user_ctx(user_info)
    if payload.strategy not in ("fixed", "rule"):
        raise HTTPException(status_code=400, detail="strategy 只能是 fixed 或 rule")
    if payload.strategy == "fixed" and not payload.secret_id:
        raise HTTPException(status_code=400, detail="固定绑定必须指定 secret_id")
    if payload.strategy == "rule" and payload.rule is None:
        raise HTTPException(status_code=400, detail="strategy=rule 时必须给出 rule")
    slot = await _resolve_slot(
        db,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=payload.slot,
        name=payload.name,
    )

    # 全局绑定的主体是空串（不是 NULL，唯一索引里 NULL 互不相等）。
    # 这个能力之前是**死代码**：`bind_secret` 只在 bound_by_user_id=None 时走全局
    # 分支，而唯一的调用点永远传 user_id——于是管理员在「MCP 服务注册」「系统工具」
    # 里配的公共同步成了他自己的一条私有绑定，普通用户调过去全是「未绑定」。
    # 不在这里对 `is_global` 一刀切：接口层看不到每条凭据的作用域。
    # 「管理员拿个人凭据绑平台级工具」是正常操作（先用自己的账号跑通），
    # 一刀切会把它当成越权拒掉。权限判断下沉到 bind_secret，按凭据作用域判：
    # 系统级凭据 + 全局 → 必须管理员；个人凭据 → 自动降级成本人绑定。
    is_admin = _is_admin(user_info)
    bound_by: str | None = None if payload.is_global else user_id

    # 规则绑定没有凭据可校验，走单独的函数；固定绑定保持原路径
    if payload.strategy == "rule":
        try:
            binding = await center.bind_rule(
                db,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=slot,
                rule=payload.rule,
                bound_by_user_id=bound_by,
                actor_id=_actor_id(user_info),
                value_path=payload.value_path,
                # 非管理员不能写全局规则：漏了这一条，任何普通用户
                # 传 is_global + strategy=rule 就能改掉全平台的取数规则。
                allow_global=is_admin,
            )
        except SecretPermissionError as exc:
            if "管理员" in str(exc):
                raise HTTPException(status_code=403, detail=str(exc))
            raise HTTPException(status_code=404, detail="凭据不存在")
        except SecretValidationError as exc:
            raise _fail(exc)
        return {
            "code": 0,
            "message": "已保存规则",
            "data": {
                "id": binding.id,
                "slot": binding.slot,
                "strategy": "rule",
                # 规则也带取用字段：命中结构化凭据时就按它取，不再整段注入
                "value_path": getattr(binding, "value_path", "") or "",
            },
        }

    try:
        binding = await center.bind_secret(
            db,
            secret_id=payload.secret_id,
            consumer_type=payload.consumer_type,
            consumer_id=payload.consumer_id,
            slot=slot,
            bound_by_user_id=bound_by,
            dept_code=dept_code,
            actor_id=_actor_id(user_info),
            value_path=payload.value_path,
            allow_whole=payload.allow_whole,
            allow_global=is_admin,
        )
    except SecretPermissionError as exc:
        # 两种来源：凭据不可见（当它不存在，不能透露存在性）与
        # 「非管理员设全局」（用户能自己改，必须说真话，否则 404 会让人
        # 以为是凭据被删了，去别处找问题）。
        if "管理员" in str(exc):
            raise HTTPException(status_code=403, detail=str(exc))
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {
        "code": 0,
        "message": "已绑定",
        "data": {
            "id": binding.id,
            "slot": binding.slot,
            "value_path": getattr(binding, "value_path", "") or "",
        },
    }


@router.delete("/bindings", summary="解绑一个槽位")
async def delete_binding(
    payload: UnbindRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    slot = await _resolve_slot(
        db,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=payload.slot,
        name=payload.name,
    )
    # 默认解「我自己的那条」。全局绑定必须显式指定且需管理员权限，
    # 否则普通用户一句 DELETE 就能把全平台共用的配置拆掉。
    if payload.is_global:
        if not _is_admin(user_info):
            raise HTTPException(status_code=403, detail="只有管理员可以解除全局绑定")
        bound_by: str | None = None
    else:
        bound_by = user_id

    if payload.strategy not in ("fixed", "rule"):
        raise HTTPException(status_code=400, detail="strategy 只能是 fixed 或 rule")
    removed = await center.unbind_slot(
        db,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=slot,
        bound_by_user_id=bound_by,
        strategy=payload.strategy,
        actor_id=_actor_id(user_info),
    )
    return {"code": 0, "message": "已解绑", "data": {"removed": removed}}


# ── 管理员：系统级凭据 ─────────────────────────────────────────────────


@router.post("/admin", summary="创建系统级凭据（管理员）")
async def admin_create(
    payload: CreateSecretRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await center.create_secret(
            db,
            owner_scope=SCOPE_SYSTEM,
            owner_id="",
            secret_key=payload.secret_key,
            name=payload.name,
            value=payload.value,
            secret_type=payload.secret_type,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            created_by=_actor_id(admin),
            value_format=payload.value_format,
            value_template=payload.value_template,
            value_secret_fields=payload.value_secret_fields,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已创建", "data": serialize_secret(row)}


@router.get("/admin", summary="系统级凭据列表（管理员）")
async def admin_list(
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy import select

    from app.models.secret import SecretBinding, SecretGrant, UserSecret

    rows = (
        (
            await db.execute(
                select(UserSecret)
                .where(UserSecret.owner_scope == SCOPE_SYSTEM)
                .order_by(UserSecret.secret_key)
            )
        )
        .scalars()
        .all()
    )
    grants = (await db.execute(select(SecretGrant))).scalars().all()
    by_secret: Dict[str, List[SecretGrant]] = {}
    for grant in grants:
        by_secret.setdefault(grant.secret_id, []).append(grant)

    # 管理员要能判断这条凭据「到底在跑还是摆着」：绑到哪了、用过没有。
    # 和个人列表共用同一套聚合，避免列表页逐行回查变成 N+1。
    secret_ids = [row.id for row in rows]
    stats = await _usage_stats(db, secret_ids)
    bindings = (
        (
            await db.execute(
                select(SecretBinding).where(SecretBinding.secret_id.in_(secret_ids))
            )
        )
        .scalars()
        .all()
        if secret_ids
        else []
    )
    by_binding: Dict[str, List[SecretBinding]] = {}
    for binding in bindings:
        by_binding.setdefault(binding.secret_id, []).append(binding)

    admin_rule_shadow = await _rule_shadow_map(db, bindings)

    # consumer_id 是 uuid，直接摊在界面上等于没说「绑到哪了」。
    # 三类消费者各查一次名字，绑定者同样解析成用户名——这里只有管理员能看，
    # 展示真实身份不构成越权。
    names: Dict[tuple[str, str], str] = {}
    user_names: Dict[str, str] = {}
    if bindings:
        ids_by_type: Dict[str, set[str]] = {}
        for b in bindings:
            ids_by_type.setdefault(b.consumer_type, set()).add(str(b.consumer_id))
        owners = {str(b.bound_by_user_id) for b in bindings if b.bound_by_user_id}

        from app.models.mcp import McpServer
        from app.models.script_tool import SysScriptTool
        from app.models.tool import SysApiTool
        from app.models.user import User

        lookups = (
            ("mcp_server", McpServer, "server_name"),
            ("api_tool", SysApiTool, "name"),
            ("script_tool", SysScriptTool, "name"),
        )
        for consumer_type, model, name_column in lookups:
            wanted = ids_by_type.get(consumer_type) or set()
            if not wanted:
                continue
            rows_named = (
                await db.execute(
                    select(model.id, getattr(model, name_column)).where(model.id.in_(wanted))
                )
            ).all()
            for row_id, row_name in rows_named:
                names[(consumer_type, str(row_id))] = str(row_name or "")

        if owners:
            # User.id 是整型主键，bindings 里存的是字符串。MySQL 会隐式转换混过去，
            # PostgreSQL（asyncpg）直接报 operator does not exist: integer = character
            # varying，接口 500。center.resolve_usage_users 里是同样的写法，照抄。
            owner_ids = _owner_ids(owners)
            user_rows = (
                await db.execute(
                    select(User.id, User.user_name, User.real_name).where(
                        User.id.in_(owner_ids)
                    )
                )
            ).all() if owner_ids else []
            for user_id, user_name, real_name in user_rows:
                user_names[str(user_id)] = real_name or user_name or str(user_id)

    return {
        "code": 0,
        "data": [
            {
                **serialize_secret(row),
                **stats.get(row.id, {}),
                "bindings": [
                    {
                        "id": b.id,
                        "slot": b.slot,
                        "consumer_type": b.consumer_type,
                        "consumer_id": b.consumer_id,
                        # 查不到名字时退回 id，至少还能定位（目标可能已被删除）
                        "consumer_name": names.get(
                            (b.consumer_type, str(b.consumer_id))
                        )
                        or str(b.consumer_id),
                        # 同槽位有规则时，这条固定绑定还能不能被选中（见 _rule_shadow_map）
                        "rule_shadow": (
                            admin_rule_shadow.get(
                                (b.consumer_type, str(b.consumer_id), b.slot)
                            )
                            if (getattr(b, "strategy", None) or "fixed") == "fixed"
                            else None
                        ),
                        "bound_by_user_id": b.bound_by_user_id,
                        "bound_by_name": (
                            user_names.get(str(b.bound_by_user_id))
                            if b.bound_by_user_id
                            else None
                        ),
                        "is_global": not b.bound_by_user_id,
                        # MCP 的参数槽位是 `arg:<短工具名>:<参数名>`，
                        # 服务名之外再带上工具名才够定位
                        "tool_name": (
                            b.slot.split(":")[1]
                            if b.consumer_type == "mcp_server"
                            and str(b.slot).startswith("arg:")
                            and len(str(b.slot).split(":")) > 2
                            else None
                        ),
                    }
                    for b in by_binding.get(row.id, [])
                ],
                "grants": [
                    {
                        "id": g.id,
                        "grantee_type": g.grantee_type,
                        "grantee_id": g.grantee_id,
                        "expires_at": _iso(g.expires_at),
                    }
                    for g in by_secret.get(row.id, [])
                ],
                "grant_count": len(by_secret.get(row.id, [])),
            }
            for row in rows
        ],
    }


@router.post("/admin/{secret_id}/grants", summary="授权系统级凭据（管理员）")
async def admin_grant(
    secret_id: str,
    payload: GrantRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        grant = await center.grant_secret(
            db,
            secret_id=secret_id,
            grantee_type=payload.grantee_type,
            grantee_id=payload.grantee_id,
            granted_by=_actor_id(admin),
            expires_at=payload.expires_at,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已授权", "data": {"id": grant.id}}


@router.delete("/admin/{secret_id}/grants", summary="撤销授权（管理员）")
async def admin_revoke(
    secret_id: str,
    grantee_type: str = Query(...),
    #: ``all`` 时传空串
    grantee_id: str = Query(""),
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    removed = await center.revoke_grant(
        db,
        secret_id=secret_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        actor_id=_actor_id(admin),
    )
    return {"code": 0, "message": "已撤销", "data": {"removed": removed}}


@router.post("/admin/{secret_id}/disable", summary="强制停用系统级凭据（管理员）")
async def admin_disable(
    secret_id: str,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if row.owner_scope != SCOPE_SYSTEM:
        # 管理员只能强制停用系统级凭据；个人凭据属于用户自己的资产，
        # 但「疑似泄漏」时管理员仍可通过滥用告警要求用户自行停用。
        raise HTTPException(status_code=403, detail="只能强制停用系统级凭据")
    try:
        # 审计必须记到具体管理员：凭据被误停用时，「谁停的」是第一现场信息。
        row = await center.set_secret_status(
            db,
            secret_id=secret_id,
            status_value="disabled",
            actor_id=_actor_id(admin),
            actor_is_admin=True,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已停用", "data": serialize_secret(row)}


@router.post("/admin/{secret_id}/status", summary="停用 / 恢复系统级凭据（管理员）")
async def admin_set_status(
    secret_id: str,
    payload: StatusRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """系统级凭据的状态开关。

    只有 ``disable`` 是不够的：误停用一条全体共用的凭据之后，平台里没有任何
    路径能把它恢复，只能重建——而重建意味着所有绑定都要重新做一遍。停用必须
    可逆，否则管理员不敢用它止血，最后反而不敢停。
    """
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if row.owner_scope != SCOPE_SYSTEM:
        raise HTTPException(status_code=403, detail="只能操作系统级凭据")
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status 只能是 active 或 disabled")
    try:
        row = await center.set_secret_status(
            db,
            secret_id=secret_id,
            status_value=payload.status,
            actor_id=_actor_id(admin),
            actor_is_admin=True,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


# ══════════════════════════════════════════════════════════════════════
# 列表 / 创建 / 详情：/{secret_id} 必须最后注册
# ══════════════════════════════════════════════════════════════════════


@router.get("", summary="我可见的凭据列表")
async def list_secrets(
    scope: str = Query(default="all", pattern="^(all|mine|shared)$"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, dept_code = _user_ctx(user_info)
    rows = await center.list_visible_secrets(
        db, user_id=user_id, dept_code=dept_code, include_system=scope != "mine"
    )
    if scope == "shared":
        rows = [row for row in rows if row.owner_scope == SCOPE_SYSTEM]
    elif scope == "mine":
        rows = [row for row in rows if row.owner_scope == SCOPE_USER]

    items = [serialize_secret(row) for row in rows]
    stats = await _usage_stats(db, [row.id for row in rows])
    for item in items:
        item.update(stats.get(item["id"], {}))

    return {
        "code": 0,
        "data": {
            "items": items,
            "total": len(rows),
        },
    }


@router.post("", summary="创建个人凭据")
async def create_secret(
    payload: CreateSecretRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        row = await center.create_secret(
            db,
            owner_scope=SCOPE_USER,
            owner_id=user_id,
            secret_key=payload.secret_key,
            name=payload.name,
            value=payload.value,
            secret_type=payload.secret_type,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            created_by=user_id,
            value_format=payload.value_format,
            value_template=payload.value_template,
            value_secret_fields=payload.value_secret_fields,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已创建", "data": serialize_secret(row)}


@router.get("/{secret_id}", summary="凭据详情（不含明文）")
async def get_secret(
    secret_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")

    user_id, dept_code = _user_ctx(user_info)
    visible = await center.list_visible_secrets(db, user_id=user_id, dept_code=dept_code)
    if row.id not in {item.id for item in visible}:
        # 不可见就当作不存在：不要泄露「有这么一条凭据」的事实
        raise HTTPException(status_code=404, detail="凭据不存在")

    bindings = await center.list_bindings(db, secret_id=secret_id)
    # 只回「全局的」和「我自己的」。别人的个人绑定与我无关——它同时泄露了
    # 「谁在用这条凭据」和「他绑到了哪个工具上」，而列表接口 GET /bindings
    # 早就这么过滤了，详情这里漏了。管理员例外：他要排查「这条凭据被谁用着」。
    if not _is_admin(user_info):
        bindings = [
            b for b in bindings if not b.bound_by_user_id or b.bound_by_user_id == user_id
        ]
    return {
        "code": 0,
        "data": {
            **serialize_secret(row),
            "bindings": [
                {
                    "id": b.id,
                    "consumer_type": b.consumer_type,
                    "consumer_id": b.consumer_id,
                    "slot": b.slot,
                    "is_global": not b.bound_by_user_id,
                    "bound_by_me": b.bound_by_user_id == user_id,
                }
                for b in bindings
            ],
        },
    }


@router.patch("/{secret_id}", summary="修改凭据元数据")
async def update_secret(
    secret_id: str,
    payload: UpdateSecretRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    clear_expires_at = (
        "expires_at" in payload.model_fields_set and payload.expires_at is None
    )
    try:
        row = await center.update_secret(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            name=payload.name,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            clear_expires_at=clear_expires_at,
            actor_is_admin=_is_admin(user_info),
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


@router.delete("/{secret_id}", summary="删除凭据")
async def delete_secret(
    secret_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        await center.delete_secret(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            actor_is_admin=_is_admin(user_info),
        )
    except SecretPermissionError:
        # 看不见就是不存在：越权一律 404，别把「这条存在」告诉对方
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已删除"}


@router.post("/{secret_id}/rotate", summary="轮换凭据值")
async def rotate_secret(
    secret_id: str,
    payload: RotateRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        row = await center.rotate_secret(
            db,
            secret_id=secret_id,
            new_value=payload.value,
            rotated_by=user_id,
            overlap_days=payload.overlap_days,
            expires_at=payload.expires_at,
            clear_expires_at=payload.clear_expires_at,
            actor_is_admin=_is_admin(user_info),
            value_format=payload.value_format,
            value_template=payload.value_template,
            value_secret_fields=payload.value_secret_fields,
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已轮换", "data": serialize_secret(row)}


@router.post("/{secret_id}/status", summary="停用 / 启用我自己的凭据")
async def set_status(
    secret_id: str,
    payload: StatusRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """用户自助止血。

    凭据疑似泄露时，第一反应必须是「立刻停掉」而不是「等管理员」——从发现到
    管理员响应之间的每一次调用都是真实的泄露窗口。系统级凭据不走这里：它由
    管理员统一维护，个人停用会让其他人莫名其妙地失效。
    """
    from app.models.secret import UserSecret

    user_id, _ = _user_ctx(user_info)
    row = await db.get(UserSecret, secret_id)
    if row is None or row.owner_id != user_id or row.owner_scope != SCOPE_USER:
        # 不可见就当作不存在：不要泄露「有这么一条凭据」的事实
        raise HTTPException(status_code=404, detail="凭据不存在")
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status 只能是 active 或 disabled")
    try:
        row = await center.set_secret_status(
            db, secret_id=secret_id, status_value=payload.status, actor_id=user_id
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


@router.get("/{secret_id}/value", summary="读取变量类明文")
async def get_value(
    secret_id: str,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只对 ``variable`` 开放。``credential`` 在这里被拒绝——设计上就没有这个能力。"""
    user_id, dept_code = _user_ctx(user_info)
    try:
        plaintext = await center.read_variable_value(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            dept_code=dept_code,
            trace_id=getattr(getattr(request, "state", None), "trace_id", None),
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return {"code": 0, "data": {"value": plaintext}}


class LocatePreviewRequest(BaseModel):
    #: 试算所在的位置（仅作上下文，规则本身与槽位无关）
    consumer_type: str = ""
    consumer_id: str = ""
    slot: str = ""
    #: 待试算的规则，结构与 ``POST /bindings`` 里的一致
    rule: dict
    #: 手填的入参样例——试算时不发起真实调用，模型不在场，所以入参要人给
    sample_args: dict[str, Any] = Field(default_factory=dict)


@router.post("/locate-preview", summary="试算规则会命中哪条凭据（不发请求、不解密）")
async def locate_preview(
    payload: LocatePreviewRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只回答「这条规则会命中谁」。

    三条边界：

    1. **身份固定为当前登录人**。试算用的是你自己的可见范围，不代表别人能命中
       同样的凭据；管理员配全局规则时界面必须写明这一点，否则会把试算结果当成
       「所有人都能这样」。
    2. **不解密**：只取 ``secret_key`` / ``name`` 这类元数据，值一个字节都不碰。
    3. **不写流水**：试算不是一次真实使用，写进使用流水会污染「谁在什么时候用了
       什么凭据」的审计口径。
    """
    from app.services.secrets.locator import (
        RuleError,
        build_candidates,
        parse_rule,
        pick_match,
        rule_summary,
    )

    try:
        spec = parse_rule(payload.rule)
    except RuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user_id, dept_code = _user_ctx(user_info)
    identity = _identity_attrs(user_info)
    wanted = build_candidates(spec, identity=identity, args=payload.sample_args)

    steps: list[dict[str, Any]] = [
        {
            "step": "取值",
            "detail": f"{spec.key_source} → {wanted[0] if wanted else '（空）'}",
        },
        {"step": "套模板", "detail": " → ".join(wanted) or "（无）"},
    ]

    visible = await center.list_visible_secrets(
        db, user_id=user_id, dept_code=dept_code, only_active=True
    )
    steps.append({"step": "可见凭据", "detail": f"{len(visible)} 条"})

    # 模板约束参与**候选筛选**，不是挑中之后才否掉。运行时（resolver）就是这么做的，
    # 试算不跟着做会出现「试算说命中这条，真跑却未命中」——同一份规则两种答案。
    pool = visible
    if spec.value_template:
        pool = [r for r in visible if (r.value_template or "") == spec.value_template]
        steps.append(
            {"step": "模板约束", "detail": f"{spec.value_template} 命中 {len(pool)} 条"}
        )

    hit, used = pick_match(spec, wanted, pool)
    if hit is not None:
        steps.append({"step": "匹配", "detail": f"命中 {hit.secret_key}"})
        return {
            "code": 0,
            "data": {
                "ok": True,
                "strategy": "rule",
                "secret_key": hit.secret_key,
                "secret_name": hit.name,
                "on_miss": spec.on_miss,
                "summary": rule_summary(spec),
                "candidate_count": len(pool),
                "steps": steps,
                "miss_reason": None,
            },
        }

    steps.append({"step": "匹配", "detail": "未命中"})
    return {
        "code": 0,
        "data": {
            "ok": False,
            "strategy": "rule",
            "secret_key": None,
            "secret_name": None,
            "on_miss": spec.on_miss,
            "summary": rule_summary(spec),
            "candidate_count": len(pool),
            "steps": steps,
            "miss_reason": (
                f"没有 {spec.target} = {used} 的凭据"
                if wanted
                else f"取不到 {spec.key_source} 的值（这次调用里没这个参数）"
            ),
            "miss_behaviour": {
                "fail": "这次调用会失败",
                "fallback_fixed": "会回退到同层的固定凭据",
                "model": "会让模型在可见候选里挑一条",
            }[spec.on_miss],
        },
    }

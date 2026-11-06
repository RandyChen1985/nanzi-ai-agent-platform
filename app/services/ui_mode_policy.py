"""角色级「界面模式黑名单」的策略与解析。

三件事集中在这里，避免读接口、写接口、登录接口各算一套：

1. **并集**：一个人挂多个角色时取并集——任一角色禁掉，这个人就没有该模式；
2. **双禁降级**：单个角色被禁止「两个都禁」，但两个角色分别禁不同模式时并集仍会双禁。
   此时降级为「不禁用」并告警：宁可放宽，也不能把账号锁死；
3. **管理员豁免**：系统身份 ``admin`` 或持有业务角色 ``code=admin`` 的账号一律不禁，
   否则管理员会被自己的角色锁在管理台外面。

``ai_agent_roles.blocked_ui_modes`` **刻意不在 ORM 里声明**：代码可能先于迁移上线，
映射里带上该列会让所有 ``SELECT role`` 报 Unknown column，等于用一个新功能
把角色管理、登录链路一起打挂（``ResourcePermission.agent_id`` 有过同样的教训）。
所以读写走原生 SQL，并由 :func:`supports_blocked_column` 做列探测；未迁移时
降级为「都不禁用」。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)

UI_MODES: tuple[str, ...] = ("user", "pro")
# 与迁移里的 VARCHAR(32) 对齐
MAX_BLOCKED_FIELD_LEN = 32
ADMIN_ROLE_CODE = "admin"
# 列不存在时多久重探一次：迁移可能刚跑完而进程还没重启
COLUMN_RETRY_SECONDS = 60.0

_COLUMN_STATE: bool | None = None
_COLUMN_ABSENT_AT: float = 0.0
_COLUMN_WARNED = False

_ROLES_SQL_WITH_BLOCKED = text(
    """
    SELECT r.code, r.name, r.blocked_ui_modes
    FROM ai_agent_roles r
    JOIN ai_agent_user_role_relations ur ON ur.role_id = r.id
    WHERE ur.user_id = :uid
    """
)
_ROLES_SQL_PLAIN = text(
    """
    SELECT r.code, r.name
    FROM ai_agent_roles r
    JOIN ai_agent_user_role_relations ur ON ur.role_id = r.id
    WHERE ur.user_id = :uid
    """
)
_BLOCKED_MAP_SQL = text(
    "SELECT id, blocked_ui_modes FROM ai_agent_roles WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))
_UPDATE_BLOCKED_SQL = text(
    "UPDATE ai_agent_roles SET blocked_ui_modes = :value WHERE id = :rid"
)


@dataclass(frozen=True)
class UiModePolicy:
    """一次解析出来的有效策略。

    ``blocked`` 是最终生效的黑名单；``blocked_by`` 只用于给用户看「是谁禁的」；
    ``downgraded`` 标记并集出现双禁并被降级放行，调用方据此打日志，不静默吞配置错误。
    """

    blocked: set[str] = field(default_factory=set)
    blocked_by: list[str] = field(default_factory=list)
    downgraded: bool = False


def parse_blocked(raw: Any) -> set[str]:
    """把库里的逗号串解析成合法模式集合。

    大小写归一、去空白、丢弃未知值：脏数据不能让整个账号被误限。
    """
    if raw is None:
        return set()
    value = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return {part.strip().lower() for part in value.split(",") if part.strip().lower() in UI_MODES}


def serialize_blocked(blocked: Iterable[str]) -> str:
    """稳定序列化：固定按 user,pro 顺序，便于比对与排查。"""
    values = {str(item).strip().lower() for item in (blocked or [])}
    result = ",".join(mode for mode in UI_MODES if mode in values)
    if len(result) > MAX_BLOCKED_FIELD_LEN:
        raise ValueError("blocked_ui_modes 超出长度上限")
    return result


def validate_blocked(blocked: Iterable[str], role_code: str | None) -> None:
    """写入前校验。抛 ValueError，由接口层转成 400。

    - 只认 user / pro；
    - 必须二选一：两个都禁等于把角色下的人锁死；
    - admin 角色一个都不许禁。
    """
    values = {str(item).strip().lower() for item in (blocked or [])}

    unknown = values - set(UI_MODES)
    if unknown:
        raise ValueError(f"未知的界面模式：{'、'.join(sorted(unknown))}")

    if len(values) >= len(UI_MODES):
        raise ValueError("用户模式与专业模式至少保留一种，不能同时禁用")

    if str(role_code or "").strip().lower() == ADMIN_ROLE_CODE and values:
        raise ValueError("管理员角色不允许禁用任何界面模式")


def resolve_policy(
    rows: Sequence[Sequence[Any]],
    user_role: str | None,
) -> UiModePolicy:
    """把「角色 code / 角色名 / 黑名单串」并成最终策略。

    管理员（系统身份或业务角色 admin）短路为空集；并集双禁时降级放行。
    """
    codes = {str(row[0] or "").strip().lower() for row in rows}
    if str(user_role or "").strip().lower() == ADMIN_ROLE_CODE or ADMIN_ROLE_CODE in codes:
        return UiModePolicy()

    blocked: set[str] = set()
    names: list[str] = []
    for row in rows:
        code = row[0]
        name = row[1] if len(row) > 1 else None
        raw = row[2] if len(row) > 2 else None
        parsed = parse_blocked(raw)
        if not parsed:
            continue
        blocked |= parsed
        label = str(name or code or "").strip()
        if label and label not in names:
            names.append(label)

    if len(blocked) >= len(UI_MODES):
        logger.warning(
            "[ui-mode] 角色黑名单并集把两种模式都禁用了（roles=%s），已降级为不限；请修正角色配置",
            ",".join(sorted(codes)),
        )
        return UiModePolicy(blocked=set(), blocked_by=names, downgraded=True)

    return UiModePolicy(blocked=blocked, blocked_by=names)


def clamp_mode(value: Any, blocked: Iterable[str], role: str | None) -> str:
    """把「用户偏好 / 角色默认值」夹到黑名单允许的范围里。

    顺序：偏好合法且在允许集内 → 用它；否则用剩下的唯一一种；都不可用时按角色默认。
    """
    blocked_set = {str(item).strip().lower() for item in (blocked or [])}
    allowed = [mode for mode in UI_MODES if mode not in blocked_set]
    preferred = str(value or "").strip().lower()

    if preferred in allowed:
        return preferred
    if len(allowed) == 1:
        return allowed[0]
    # 两种都允许但偏好无效（没选过 / 脏值）：按角色默认
    return "pro" if str(role or "").strip().lower() == ADMIN_ROLE_CODE else "user"


def is_admin_like(user_role: str | None, role_codes: Iterable[str | None]) -> bool:
    """管理员判定：系统身份 admin 或持有业务角色 admin（两者语义一致）。"""
    if str(user_role or "").strip().lower() == ADMIN_ROLE_CODE:
        return True
    return any(str(code or "").strip().lower() == ADMIN_ROLE_CODE for code in role_codes)


async def supports_blocked_column(db: AsyncSession) -> bool:
    """``ai_agent_roles.blocked_ui_modes`` 是否已由迁移加入。

    判据是「能不能真的引用这一列」，不查 information_schema：后者在 MySQL 里
    覆盖整台实例上该用户可见的所有 schema，同实例别的库迁移过就会被误判。

    结论缓存：真 / 假都缓存，但「假」只在 :data:`COLUMN_RETRY_SECONDS` 内有效，
    迁移刚跑完时最多等一分钟就自动启用。探测异常按「未迁移」处理——黑名单读不到
    时的安全方向是**放行**（不锁人），不是把所有账号限成单模式。
    """
    global _COLUMN_STATE, _COLUMN_ABSENT_AT, _COLUMN_WARNED

    if _COLUMN_STATE is True:
        return True
    if _COLUMN_STATE is False:
        if time.monotonic() - _COLUMN_ABSENT_AT < COLUMN_RETRY_SECONDS:
            return False

    try:
        # 放进 SAVEPOINT：失败只回滚这一小段，不污染调用方事务
        async with db.begin_nested():
            await db.execute(
                text("SELECT 1 FROM ai_agent_roles WHERE blocked_ui_modes IS NOT NULL LIMIT 1")
            )
    except Exception as exc:  # noqa: BLE001 - 探测失败必须降级而不是打挂
        _COLUMN_STATE = False
        _COLUMN_ABSENT_AT = time.monotonic()
        if not _COLUMN_WARNED:
            _COLUMN_WARNED = True
            logger.warning(
                "[ui-mode] blocked_ui_modes 列不可用，暂时按「不禁用」处理，"
                "迁移后自动启用（最多等待 %.0fs）：%s",
                COLUMN_RETRY_SECONDS,
                exc,
            )
        return False

    _COLUMN_STATE = True
    _COLUMN_ABSENT_AT = 0.0
    logger.info("[ui-mode] blocked_ui_modes 列已就绪，角色级黑名单生效")
    return True


async def effective_policy(db: AsyncSession, user_id: int) -> UiModePolicy:
    """某个账号当前生效的界面模式策略。

    先用 ``users.role`` 做管理员短路（少一次 join，也不会被任何角色限制），
    再按所属角色求并集。列未迁移时只取 code/name，黑名单视为空。
    """
    uid = int(user_id)
    user_role = (
        await db.execute(select(User.role).where(User.id == uid))
    ).scalar_one_or_none()

    if str(user_role or "").strip().lower() == ADMIN_ROLE_CODE:
        return UiModePolicy()

    if await supports_blocked_column(db):
        result = await db.execute(_ROLES_SQL_WITH_BLOCKED, {"uid": uid})
    else:
        result = await db.execute(_ROLES_SQL_PLAIN, {"uid": uid})

    rows = [tuple(row) for row in result.all()]
    return resolve_policy(rows, user_role)


async def blocked_map_for_roles(
    db: AsyncSession, role_ids: Iterable[int]
) -> dict[int, str]:
    """批量取角色黑名单（列表页用一次查询，避免每个角色查一次）。

    未迁移时返回空字典**且不查库**：列表接口不该多一次必然失败的往返。
    """
    ids = [int(role_id) for role_id in role_ids]
    if not ids:
        return {}
    if not await supports_blocked_column(db):
        return {}
    rows = (await db.execute(_BLOCKED_MAP_SQL, {"ids": ids})).all()
    return {int(role_id): (raw or "") for role_id, raw in rows}


async def set_role_blocked(
    db: AsyncSession, role_id: int, blocked: Iterable[str]
) -> str:
    """写入角色黑名单，返回落库的序列化值。

    只执行 UPDATE，**不 commit**：由调用方与其余字段一起提交。迁移未执行时抛
    ``ValueError``（接口层转 400），不能静默丢弃管理员的配置。
    """
    if not await supports_blocked_column(db):
        raise ValueError(
            "界面模式配置尚未启用：请先执行数据库迁移 "
            "（db-prod/V207 或 db-prod-pg/V107）后重试"
        )
    value = serialize_blocked(blocked)
    await db.execute(_UPDATE_BLOCKED_SQL, {"rid": int(role_id), "value": value})
    return value

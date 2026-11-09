"""凭据中心的写操作：创建、轮换、绑定、授权。

与 ``resolver.py`` 的分工：这里负责「把数据写对」，那里负责「把值给对」。
两者都刻意不含任何返回明文的路径——``credential`` 类型建完即不可读。

三个必须在写入侧守住的不变量：

* ``secret_key`` 只允许 ``[a-z0-9._-]``：它会被拼进 ``${secret:<key>}``，
  带空格或冒号会让引用语法无法解析；
* ``credential`` 不写 ``value_preview``：预览就是泄露，哪怕只有 8 个字符；
* 轮换不是覆盖，而是把旧值挪到 ``previous_*`` 并给一个过渡窗口——
  直接覆盖会让正在跑的定时任务在下一分钟突然失败，而且没法回滚。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import and_, delete, or_, select

from app.models.secret import SecretBinding, SecretGrant, UserSecret
from app.services.secrets.fingerprint import secret_fingerprint
from app.services.secrets.policy import (
    ALL_SCENES,
    SCOPE_SYSTEM,
    SCOPE_USER,
    TYPE_CREDENTIAL,
    TYPE_VARIABLE,
    normalize_host,
)
from app.services.secrets.registry import secret_registry
from app.utils.encryption import get_api_key_manager

logger = logging.getLogger(__name__)

#: ``secret_key`` 的合法字符集。它会被拼进 ``${secret:<key>}`` 引用语法。
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

#: 轮换后的旧值保留天数。够覆盖「按天跑的定时任务」至少一轮。
ROTATION_OVERLAP_DAYS = 7

VALID_SCOPES = frozenset({SCOPE_USER, SCOPE_SYSTEM})
VALID_TYPES = frozenset({TYPE_CREDENTIAL, TYPE_VARIABLE})
VALID_CONSUMER_TYPES = frozenset({"mcp_server", "api_tool", "script_tool", "agent"})


class SecretValidationError(ValueError):
    """入参不合法。消息会直接回给前端，所以要说清「哪里不对」。"""


class SecretPermissionError(SecretValidationError):
    """无权操作这条凭据。

    和「参数不合法」分开，是因为端点要对它回 **404** 而不是 400：
    403/400 等于承认「这条凭据存在，只是你碰不了」，那就把 UUID 变成了
    一个可探测的存在性预言机。「看不见就是不存在」要在写路径上也成立。
    """


def _validate_key(secret_key: str) -> str:
    key = (secret_key or "").strip().lower()
    if not key:
        raise SecretValidationError("凭据标识不能为空")
    if not _KEY_PATTERN.match(key):
        raise SecretValidationError(
            "凭据标识只能用小写字母、数字、点、下划线、连字符，且以字母或数字开头"
        )
    return key


def _validate_scenarios(scenarios: Iterable[str] | None) -> list[str]:
    if not scenarios:
        return []
    unknown = set(scenarios) - set(ALL_SCENES)
    if unknown:
        raise SecretValidationError(f"未知的注入场景: {', '.join(sorted(unknown))}")
    # 去重并保持稳定顺序，方便前端比对
    return [scene for scene in ALL_SCENES if scene in set(scenarios)]


def _validate_hosts(hosts: Iterable[str] | None) -> list[str]:
    if not hosts:
        return []
    cleaned: list[str] = []
    for host in hosts:
        normalized = normalize_host(host)
        if not normalized:
            continue
        if len(normalized) > 253:
            raise SecretValidationError(f"域名过长: {host}")
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _encrypt(value: str) -> str:
    return get_api_key_manager().encrypt_api_key(value)


async def create_secret(
    session,
    *,
    owner_scope: str = SCOPE_USER,
    owner_id: str,
    secret_key: str,
    name: str,
    value: str,
    secret_type: str = TYPE_CREDENTIAL,
    allowed_scenarios: Iterable[str] | None = None,
    allowed_hosts: Iterable[str] | None = None,
    category: str | None = None,
    remark: str | None = None,
    expires_at: datetime | None = None,
    created_by: str | None = None,
) -> UserSecret:
    """建一条凭据。值只进密文字段，任何路径都不再返回明文。"""
    if owner_scope not in VALID_SCOPES:
        raise SecretValidationError(f"未知的归属范围: {owner_scope}")
    if secret_type not in VALID_TYPES:
        raise SecretValidationError(f"未知的凭据类型: {secret_type}")
    if not value:
        raise SecretValidationError("凭据值不能为空")
    if owner_scope == SCOPE_USER and not owner_id:
        raise SecretValidationError("用户级凭据必须指定 owner_id")

    key = _validate_key(secret_key)
    # 系统级用空串占位，让 (owner_scope, owner_id, secret_key) 的唯一约束真正生效
    normalized_owner = owner_id if owner_scope == SCOPE_USER else ""

    existing = await session.execute(
        select(UserSecret.id).where(
            UserSecret.owner_scope == owner_scope,
            UserSecret.owner_id == normalized_owner,
            UserSecret.secret_key == key,
        )
    )
    if existing.first() is not None:
        raise SecretValidationError(f"凭据标识已存在: {key}")

    row = UserSecret(
        id=str(uuid.uuid4()),
        owner_scope=owner_scope,
        owner_id=normalized_owner,
        secret_key=key,
        name=(name or key).strip()[:128],
        secret_type=secret_type,
        value_encrypted=_encrypt(value),
        value_fingerprint=secret_fingerprint(value),
        # 预览就是泄露：credential 一律不存，variable 才允许展示前 8 位
        value_preview=(value[:8] if secret_type == TYPE_VARIABLE else None),
        allowed_hosts=_validate_hosts(allowed_hosts),
        allowed_scenarios=_validate_scenarios(allowed_scenarios),
        status="active",
        expires_at=expires_at,
        category=category,
        remark=remark,
        created_by=created_by or owner_id or None,
    )
    session.add(row)
    await session.commit()

    # 新值必须立刻进入落库扫描的快照，否则最长 60 秒内它仍会明文进审计。
    secret_registry.invalidate()
    return row


async def rotate_secret(
    session,
    *,
    secret_id: str,
    new_value: str,
    rotated_by: str | None = None,
    overlap_days: int = ROTATION_OVERLAP_DAYS,
    expires_at: datetime | None = None,
    actor_is_admin: bool = False,
) -> UserSecret:
    """轮换凭据值，旧值保留 ``overlap_days`` 天作为回退窗口。"""
    if not new_value:
        raise SecretValidationError("新值不能为空")

    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != rotated_by:
        # 轮换是**写值**，比改元数据更狠：知道 id 就能把别人的凭据换成自己的 token，
        # 之后对方的工具会带着攻击者的身份去调用外部服务。
        raise SecretPermissionError("不能轮换他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        # 系统级凭据是所有绑定方共用的：改一次全体同时变，只能由管理员决定。
        raise SecretPermissionError("系统级凭据由管理员维护")
    if row.secret_type not in VALID_TYPES:
        raise SecretValidationError(f"未知的凭据类型: {row.secret_type}")

    now = datetime.now()
    row.previous_value_encrypted = row.value_encrypted
    row.previous_expires_at = now + timedelta(days=overlap_days)
    row.value_encrypted = _encrypt(new_value)
    row.value_fingerprint = secret_fingerprint(new_value)
    row.value_preview = new_value[:8] if row.secret_type == TYPE_VARIABLE else None
    row.last_rotated_at = now
    row.expires_at = expires_at if expires_at is not None else row.expires_at
    await session.commit()

    secret_registry.invalidate()
    logger.info(
        "[SecretCenter] 轮换凭据 %s（旧值保留至 %s，操作人 %s）",
        row.secret_key,
        row.previous_expires_at.isoformat(timespec="seconds"),
        rotated_by or "-",
    )
    return row


async def bind_secret(
    session,
    *,
    secret_id: str,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    bound_by_user_id: str | None,
    dept_code: str = "",
) -> SecretBinding:
    """把一个槽位绑到一条凭据上。同一槽位重复绑定视为「改绑」。"""
    if consumer_type not in VALID_CONSUMER_TYPES:
        raise SecretValidationError(f"未知的消费者类型: {consumer_type}")
    if not consumer_id or not slot:
        raise SecretValidationError("消费者与槽位不能为空")
    prefix, _, slot_name = slot.partition(":")
    if not prefix or not slot_name:
        raise SecretValidationError(
            "槽位格式应为 header:名称 / query:参数名 / body:字段 / env:变量名"
        )

    secret = await session.get(UserSecret, secret_id)
    if secret is None:
        raise SecretValidationError("凭据不存在")

    # 系统级凭据只能由管理员全局绑定；普通用户绑定系统级时，绑定只对自己生效
    # （bound_by_user_id 落成自己，解析时按主体过滤）。
    if secret.owner_scope == SCOPE_SYSTEM and bound_by_user_id is None:
        pass  # 管理员全局绑定，允许
    elif secret.owner_scope == SCOPE_USER and secret.owner_id != bound_by_user_id:
        raise SecretValidationError("不能绑定他人的凭据")
    elif secret.owner_scope == SCOPE_SYSTEM:
        # 普通用户绑系统级凭据：必须先被授权。不校验的话，绑定成功之后
        # `GET /bindings?secret_id=` 会把这个系统的 secret_key / name 回给她，
        # 「未授权的连名字都不出现」就被绕开了。
        visible = await list_visible_secrets(
            session, user_id=str(bound_by_user_id or ""), dept_code=dept_code
        )
        if secret.id not in {item.id for item in visible}:
            raise SecretPermissionError("凭据不存在")

    # 同一槽位同一绑定者只保留一条：先删后插，避免唯一索引冲突
    await session.execute(
        delete(SecretBinding).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            SecretBinding.bound_by_user_id == bound_by_user_id
            if bound_by_user_id is not None
            else SecretBinding.bound_by_user_id.is_(None),
        )
    )

    binding = SecretBinding(
        id=str(uuid.uuid4()),
        secret_id=secret_id,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        slot=slot,
        bound_by_user_id=bound_by_user_id,
    )
    session.add(binding)
    await session.commit()
    return binding


async def unbind_slot(
    session,
    *,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    bound_by_user_id: str | None,
) -> int:
    """解绑。返回删除条数（0 表示本来就没绑）。"""
    result = await session.execute(
        delete(SecretBinding).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            SecretBinding.bound_by_user_id == bound_by_user_id
            if bound_by_user_id is not None
            else SecretBinding.bound_by_user_id.is_(None),
        )
    )
    await session.commit()
    return result.rowcount or 0


async def grant_secret(
    session,
    *,
    secret_id: str,
    grantee_type: str,
    grantee_id: str,
    granted_by: str | None = None,
    expires_at: datetime | None = None,
) -> SecretGrant:
    """把系统级凭据授权给一个用户或部门。"""
    if grantee_type not in {"user", "dept"}:
        raise SecretValidationError(f"未知的授权对象类型: {grantee_type}")
    if not grantee_id:
        raise SecretValidationError("授权对象不能为空")

    secret = await session.get(UserSecret, secret_id)
    if secret is None:
        raise SecretValidationError("凭据不存在")
    if secret.owner_scope != SCOPE_SYSTEM:
        raise SecretValidationError("只有系统级凭据需要授权；用户级凭据本身就是私有")

    existing = await session.execute(
        select(SecretGrant.id).where(
            SecretGrant.secret_id == secret_id,
            SecretGrant.grantee_type == grantee_type,
            SecretGrant.grantee_id == grantee_id,
        )
    )
    if existing.first() is not None:
        raise SecretValidationError("这条授权已存在")

    grant = SecretGrant(
        id=str(uuid.uuid4()),
        secret_id=secret_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        granted_by=granted_by,
        expires_at=expires_at,
    )
    session.add(grant)
    await session.commit()
    return grant


async def revoke_grant(session, *, secret_id: str, grantee_type: str, grantee_id: str) -> int:
    """撤销授权。已建立的绑定不随之删除，但下一次调用会被 ``denied_by_grant`` 拦下。"""
    result = await session.execute(
        delete(SecretGrant).where(
            SecretGrant.secret_id == secret_id,
            SecretGrant.grantee_type == grantee_type,
            SecretGrant.grantee_id == grantee_id,
        )
    )
    await session.commit()
    return result.rowcount or 0


async def list_visible_secrets(
    session,
    *,
    user_id: str,
    dept_code: str | None = None,
    include_system: bool = True,
) -> Sequence[UserSecret]:
    """列出用户能「看见」的凭据：自己的 + 被授权的系统级。

    未授权的系统级凭据**连名称都不返回**——凭据名本身也是情报。
    """
    conditions = [
        and_(UserSecret.owner_scope == SCOPE_USER, UserSecret.owner_id == user_id)
    ]
    if include_system:
        grant_conditions = [
            and_(SecretGrant.grantee_type == "user", SecretGrant.grantee_id == user_id)
        ]
        if dept_code:
            grant_conditions.append(
                and_(SecretGrant.grantee_type == "dept", SecretGrant.grantee_id == dept_code)
            )
        granted_ids = select(SecretGrant.secret_id).where(
            or_(*grant_conditions),
            or_(SecretGrant.expires_at.is_(None), SecretGrant.expires_at > datetime.now()),
        )
        conditions.append(
            and_(UserSecret.owner_scope == SCOPE_SYSTEM, UserSecret.id.in_(granted_ids))
        )

    stmt = (
        select(UserSecret)
        .where(or_(*conditions))
        .order_by(UserSecret.owner_scope.desc(), UserSecret.secret_key)
    )
    return (await session.execute(stmt)).scalars().all()


async def delete_secret(
    session, *, secret_id: str, actor_id: str, actor_is_admin: bool = False
) -> None:
    """删除凭据。绑定与授权要**显式清理**，使用流水保留。

    这几张表之间没有外键（要能单独导出再导入），所以没有 ``ON DELETE CASCADE``
    兜底。漏了这两条 delete，库里会留下指向已删凭据的授权和绑定：功能上不会
    报错（解析时是 join，孤儿记录匹配不上），但「这条凭据授权给了谁」会变成
    查不清的垃圾数据，而且下次建同名凭据时看不出哪些绑定是陈旧的。
    """
    from sqlalchemy import delete as sa_delete

    from app.models.secret import SecretBinding, SecretGrant

    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能删除他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        raise SecretPermissionError("系统级凭据由管理员维护")

    await session.execute(sa_delete(SecretGrant).where(SecretGrant.secret_id == secret_id))
    await session.execute(sa_delete(SecretBinding).where(SecretBinding.secret_id == secret_id))
    await session.delete(row)
    await session.commit()
    secret_registry.invalidate()


async def update_secret(
    session,
    *,
    secret_id: str,
    actor_id: str,
    name: str | None = None,
    allowed_scenarios: Iterable[str] | None = None,
    allowed_hosts: Iterable[str] | None = None,
    category: str | None = None,
    remark: str | None = None,
    expires_at: datetime | None = None,
    actor_is_admin: bool = False,
) -> UserSecret:
    """改元数据。**不能**改值——改值必须走 ``rotate_secret``（留轮换痕迹）。

    ``None`` 表示不动；传空列表表示清空（例如 ``allowed_scenarios=[]``
    等于把这条凭据从所有场景下线）。
    """
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能修改他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        raise SecretPermissionError("系统级凭据由管理员维护")

    if name is not None:
        row.name = name.strip()[:128] or row.secret_key
    if allowed_scenarios is not None:
        row.allowed_scenarios = _validate_scenarios(allowed_scenarios)
    if allowed_hosts is not None:
        row.allowed_hosts = _validate_hosts(allowed_hosts)
    if category is not None:
        row.category = category
    if remark is not None:
        row.remark = remark[:500]
    if expires_at is not None:
        row.expires_at = expires_at

    await session.commit()
    return row


async def set_secret_status(
    session,
    *,
    secret_id: str,
    status_value: str,
    actor_id: str | None = None,
) -> UserSecret:
    """启用 / 停用。停用是切断所有绑定的唯一手段，且不可撤销地影响全部使用者。"""
    if status_value not in {"active", "disabled"}:
        raise SecretValidationError(f"未知状态: {status_value}")
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and actor_id is not None and row.owner_id != actor_id:
        raise SecretValidationError("不能操作他人的凭据")

    row.status = status_value
    await session.commit()
    logger.warning(
        "[SecretCenter] 凭据 %s 被置为 %s（操作人 %s）", row.secret_key, status_value, actor_id or "-"
    )
    return row


async def list_bindings(session, *, secret_id: str) -> Sequence[SecretBinding]:
    stmt = (
        select(SecretBinding)
        .where(SecretBinding.secret_id == secret_id)
        .order_by(SecretBinding.created_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def list_usage(
    session,
    *,
    user_id: str | None = None,
    secret_id: str | None = None,
    limit: int = 100,
) -> Sequence:
    """使用流水。管理员传 ``user_id=None`` 看全局；普通用户只看自己。"""
    from app.models.secret import SecretUsageLog

    stmt = select(SecretUsageLog).order_by(SecretUsageLog.created_at.desc())
    if user_id:
        stmt = stmt.where(SecretUsageLog.user_id == user_id)
    if secret_id:
        stmt = stmt.where(SecretUsageLog.secret_id == secret_id)
    return (await session.execute(stmt.limit(max(1, min(limit, 500))))).scalars().all()


async def read_variable_value(
    session,
    *,
    secret_id: str,
    actor_id: str,
    dept_code: str = "",
    trace_id: str | None = None,
) -> str:
    """读变量类的明文。

    **只对 ``variable`` 开放**：租户号、接口地址这类东西必须能被复制，否则用户
    没法在别处用。``credential`` 在这里直接拒绝——不是权限不够，是设计上就没有
    这个能力，任何人都没有。

    系统级变量同样受授权约束：它不是「谁都能看的公共常量」，而是「授权给谁谁
    能看」。少了这一步，任何登录用户只要拿到 UUID 就能读出管理员配置的租户号、
    内部接口地址，而列表页还装作看不见它。

    每次成功读取都记一条流水。明文只在这一刻离开平台，没有痕迹的话，
    「这个租户号是怎么流出去的」永远查不清。
    """
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.secret_type != TYPE_VARIABLE:
        raise SecretValidationError("凭证类凭据不可读取；请在配置中引用，由平台注入")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能读取他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM:
        visible = await list_visible_secrets(session, user_id=actor_id, dept_code=dept_code)
        if row.id not in {item.id for item in visible}:
            # 未授权就当它不存在，和列表页一个口径
            raise SecretPermissionError("不能读取他人的凭据")
    if not row.value_encrypted:
        raise SecretValidationError("凭据没有值")

    plaintext = get_api_key_manager().decrypt_api_key(row.value_encrypted)

    import uuid as _uuid

    from app.models.secret import SecretUsageLog

    session.add(
        SecretUsageLog(
            id=str(_uuid.uuid4()),
            secret_id=row.id,
            secret_key_snapshot=row.secret_key,
            owner_scope=row.owner_scope,
            trace_id=trace_id,
            user_id=actor_id,
            scene="variable_read",
            consumer_type=None,
            consumer_id=None,
            slot=None,
            target_host=None,
            result="success",
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 - 流水写失败不该让用户拿不到值
        logger.warning("[SecretCenter] 读取变量流水写入失败 secret=%s", row.secret_key, exc_info=True)
        await session.rollback()
    return plaintext


async def detach_consumer(session, *, consumer_type: str, consumer_id: str) -> int:
    """删掉挂在某个消费者上的全部绑定，返回清理条数。

    消费者被删除（删工具、删 MCP 服务、删脚本）时必须调用。表间没有外键，
    所以没人会替我们清——留下的孤儿绑定不会报错（解析时按 consumer_id 查，
    消费者没了就永远查不到），但会一直占着唯一索引的位置，而且让「这条凭据
    被绑到哪了」这种反查显示出一个已经不存在的目标。

    解析失败或表不存在时安静返回 0：删工具的接口不该因为凭据表的问题而失败。
    """
    from sqlalchemy import delete as sa_delete

    from app.models.secret import SecretBinding

    if not consumer_type or not consumer_id:
        return 0
    try:
        result = await session.execute(
            sa_delete(SecretBinding).where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[SecretCenter] 清理消费者绑定失败: %s/%s", consumer_type, consumer_id, exc_info=True
        )
        await session.rollback()
        return 0

"""凭据解析：把「谁、在什么场景、给哪个槽位要值」变成一次受控的取值。

这是整个凭据中心唯一的取值路径。所有闸门都在这里收口：

1. **绑定**：``(consumer_type, consumer_id, slot)`` 必须已声明，且绑定者要么是
   管理员（``bound_by_user_id IS NULL``）要么就是本次执行主体；
2. **授权**：系统级凭据必须 grant 给该用户（直接授权或命中部门）；
3. **判定**：交给 ``policy.evaluate_secret``（状态 / 有效期 / 场景 / 域名）；
4. **解密**：只有前三步都通过才解密，结果包在 ``SensitiveValue`` 里；
5. **留痕**：无论成功还是被拒都写一条流水，删除凭据不删记录。

**fail-closed**：查表失败（迁移没执行、库抖动）一律按「没有这条凭据」处理，
调用方退回自己的默认逻辑。绝不因为「查不到」就放行——那等于把白名单变成摆设。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select

from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretBinding, SecretGrant, SecretUsageLog, SecretUserPref, UserSecret
from app.services.secrets.policy import (
    DENY_BINDING,
    DENY_GRANT,
    DENY_RULE_MISS,
    GRANTEE_ALL,
    GRANTEE_DEPT,
    GRANTEE_USER,
    SecretContext,
    SecretRecord,
    SensitiveValue,
    evaluate_secret,
    normalize_host,
)

logger = logging.getLogger(__name__)

#: 单次调用最多注入几条凭据的兜底值（配置项缺失时使用）。
DEFAULT_MAX_INJECT_PER_CALL = 3

#: 规则未命中、按规则自己声明的策略回退到固定绑定时的流水结果。
#: 单独一个值是为了事后能查「规则其实一直没命中」。
RESULT_SUCCESS_FALLBACK = "success_fallback"


@dataclass(frozen=True)
class ResolveResult:
    """一次解析的结果。``ok=False`` 时 ``denial`` 说明是哪道闸门拦下的。"""

    ok: bool
    secret_id: str | None = None
    secret_key: str | None = None
    value: SensitiveValue | None = None
    denial: str | None = None
    detail: str = ""

    @property
    def denied(self) -> bool:
        return not self.ok


@dataclass
class Located:
    """定位结果。``secret_id`` 为空表示没找到可用凭据，``denial`` 说明原因。"""

    secret_id: str | None = None
    strategy: str = "fixed"
    fallback_used: bool = False
    denial: str | None = None
    detail: str = ""


def _as_bool(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_MAX_INJECT = 3


def _as_limit(raw, default: int = DEFAULT_MAX_INJECT) -> int:
    """单次注入上限。非法值退回默认，夹在 1..50 —— 0 会让所有注入静默失效，
    没有上限又等于关掉了这道闸门，两种极端都不该由一次手滑决定。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 50))


def _to_record(row: UserSecret) -> SecretRecord:
    hosts = row.allowed_hosts or []
    scenarios = row.allowed_scenarios or []
    if not isinstance(hosts, (list, tuple)):
        hosts = []
    if not isinstance(scenarios, (list, tuple)):
        scenarios = []
    return SecretRecord(
        id=row.id,
        owner_scope=row.owner_scope or "user",
        owner_id=row.owner_id or "",
        secret_key=row.secret_key,
        secret_type=row.secret_type or "credential",
        status=row.status or "active",
        expires_at=row.expires_at,
        allowed_hosts=tuple(str(h) for h in hosts if h),
        allowed_scenarios=frozenset(str(s) for s in scenarios if s),
        previous_expires_at=row.previous_expires_at,
    )


class SecretResolver:
    """按执行主体解析单个槽位的凭据值。"""

    async def resolve(
        self,
        *,
        user_id: str,
        scene: str,
        consumer_type: str,
        consumer_id: str,
        slot: str,
        target_host: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        dept_code: str | None = None,
        user_attrs: Mapping[str, Any] | None = None,
        rule_args: Mapping[str, Any] | None = None,
        preferred_secret_id: str | None = None,
    ) -> ResolveResult:
        """解析一个槽位。返回 ``ok=False`` 时调用方应走自己的默认值。"""
        if not user_id or not consumer_type or not consumer_id or not slot:
            return ResolveResult(ok=False, denial=DENY_BINDING, detail="missing identity")

        # naive UTC，与库里所有 datetime 同一口径。用 datetime.now() 的话，
        # 在东八区机器上 now 比 saved expires_at 快 8 小时，凭据会提前 8 小时失效。
        now = datetime.utcnow()

        try:
            async with AsyncSessionLocal() as session:
                located = await self._locate(
                    session,
                    consumer_type=consumer_type,
                    consumer_id=consumer_id,
                    slot=slot,
                    user_id=user_id,
                    dept_code=dept_code,
                    identity=user_attrs,
                    args=rule_args,
                    preferred_secret_id=preferred_secret_id,
                )
                if located.denial:
                    return ResolveResult(
                        ok=False, denial=located.denial, detail=located.detail
                    )
                if not located.secret_id:
                    # 没有绑定 = 零授权。这不是错误，是常态：绝大多数工具本来就不带凭据。
                    return ResolveResult(ok=False, denial=DENY_BINDING)

                row = await session.get(UserSecret, located.secret_id)
                if row is None:
                    return ResolveResult(
                        ok=False, denial=DENY_BINDING, detail="secret row missing"
                    )

                record = _to_record(row)

                if record.is_system:
                    granted = await self._has_grant(session, record.id, user_id, dept_code, now)
                    if not granted:
                        await self._log_usage(
                            session,
                            record,
                            user_id=user_id,
                            scene=scene,
                            consumer_type=consumer_type,
                            consumer_id=consumer_id,
                            slot=slot,
                            target_host=target_host,
                            trace_id=trace_id,
                            conversation_id=conversation_id,
                            result=DENY_GRANT,
                        )
                        return ResolveResult(
                            ok=False,
                            secret_id=record.id,
                            secret_key=record.secret_key,
                            denial=DENY_GRANT,
                        )

                platform_script, platform_external, _ = await self._platform_flags()
                user_script = await self._user_allows_script_env(session, user_id)

                context = SecretContext(
                    user_id=user_id,
                    scene=scene,
                    target_host=target_host,
                    dept_code=dept_code,
                    # 两层都开才允许：平台策略 × 用户偏好，任一为假即拒绝。
                    allow_script_env=platform_script and user_script,
                    allow_external_host=platform_external,
                )

                denial = evaluate_secret(record, context, now=now)
                if denial:
                    await self._log_usage(
                        session,
                        record,
                        user_id=user_id,
                        scene=scene,
                        consumer_type=consumer_type,
                        consumer_id=consumer_id,
                        slot=slot,
                        target_host=target_host,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        result=denial,
                    )
                    return ResolveResult(
                        ok=False,
                        secret_id=record.id,
                        secret_key=record.secret_key,
                        denial=denial,
                    )

                ciphertext = self._pick_ciphertext(row, now)
                if not ciphertext:
                    return ResolveResult(
                        ok=False,
                        secret_id=record.id,
                        secret_key=record.secret_key,
                        denial=DENY_BINDING,
                        detail="no ciphertext",
                    )

                plaintext = self._decrypt(ciphertext)

                await self._log_usage(
                    session,
                    record,
                    user_id=user_id,
                    scene=scene,
                    consumer_type=consumer_type,
                    consumer_id=consumer_id,
                    slot=slot,
                    target_host=target_host,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    # 回退命中单独记一个结果：事后能查出「规则其实一直没命中」。
                    result=(
                        RESULT_SUCCESS_FALLBACK if located.fallback_used else "success"
                    ),
                )

                return ResolveResult(
                    ok=True,
                    secret_id=record.id,
                    secret_key=record.secret_key,
                    value=SensitiveValue(plaintext),
                )
        except Exception as exc:  # noqa: BLE001 - 见模块 docstring 的 fail-closed 说明
            logger.warning(
                "[SecretResolver] 解析失败，按无凭据处理 consumer=%s/%s slot=%s user=%s: %s",
                consumer_type,
                consumer_id,
                slot,
                user_id,
                exc,
            )
            return ResolveResult(ok=False, denial=DENY_BINDING, detail="resolver error")

    # ── 内部 ────────────────────────────────────────────────────────────

    async def _locate(
        self,
        session,
        *,
        consumer_type: str,
        consumer_id: str,
        slot: str,
        user_id: str,
        dept_code: str | None,
        identity: Mapping[str, Any] | None = None,
        args: Mapping[str, Any] | None = None,
        preferred_secret_id: str | None = None,
        rows: Sequence[Any] | None = None,
        visible: Sequence[Any] | None = None,
    ) -> Located:
        """选出这次要用的凭据。**层间**个人优先，**层内**规则优先。

        ``on_miss=fail`` 时绝不继续往下取固定绑定（同层、跨层都不取）——否则
        「回退」就成了默认行为，配错的规则永远没人发现。
        """
        from app.services.secrets.locator import (
            RuleError,
            build_candidates,
            parse_rule,
            pick_match,
        )

        if rows is None:
            rows = await self._bindings_for_slot(
                session, consumer_type, consumer_id, slot, user_id
            )
        if not rows:
            return Located(denial=None)  # 交给调用方按「无绑定」处理

        layers: list[list[Any]] = []
        mine = [b for b in rows if (b.bound_by_user_id or "") == user_id]
        glob = [b for b in rows if (b.bound_by_user_id or "") == ""]
        if mine:
            layers.append(mine)
        if glob:
            layers.append(glob)

        for layer in layers:
            rule_binding = next((b for b in layer if (b.strategy or "fixed") == "rule"), None)
            fixed_binding = next((b for b in layer if (b.strategy or "fixed") != "rule"), None)

            if rule_binding is None:
                if fixed_binding is not None and fixed_binding.secret_id:
                    return Located(secret_id=fixed_binding.secret_id, strategy="fixed")
                continue

            # 坏规则时 spec 为 None，wanted 保持空列表：细节文案要能安全降级
            spec = None
            wanted: list[str] = []
            try:
                spec = parse_rule(rule_binding.rule_json)
            except RuleError as exc:
                # 坏规则按未命中处理：不崩、不静默换人，交由 on_miss 语义
                logger.warning("[SecretLocator] 规则不合法，按未命中处理: %s", exc)

            if spec is not None:
                wanted = build_candidates(spec, identity=identity, args=args)
                if wanted:
                    if visible is None:
                        visible = await self._visible_records(session, user_id, dept_code)
                    hit, used = pick_match(spec, wanted, visible)
                    if hit is not None:
                        logger.info(
                            "[SecretLocator] 规则命中 consumer=%s/%s slot=%s wanted=%s hit=%s",
                            consumer_type,
                            consumer_id,
                            slot,
                            used,
                            getattr(hit, "secret_key", "?"),
                        )
                        if preferred_secret_id and str(preferred_secret_id) != str(
                            getattr(hit, "id", "") or ""
                        ):
                            # 模型也给了编号，但规则命中优先。以前这里是静默的，
                            # 结果「模型推错了」永远发现不了：编号被忽略、不报错、
                            # 也不影响这次的返回值。
                            logger.info(
                                "[SecretLocator] 规则命中，忽略模型选择 consumer=%s/%s "
                                "slot=%s rule=%s model_choice_id=%s",
                                consumer_type,
                                consumer_id,
                                slot,
                                getattr(hit, "secret_key", "?"),
                                preferred_secret_id,
                            )
                        return Located(secret_id=getattr(hit, "id", None), strategy="rule")

            # —— 未命中：只看规则自己声明的策略，绝不默认回退
            on_miss = getattr(spec, "on_miss", "fail") if spec is not None else "fail"
            if on_miss == "fallback_fixed" and fixed_binding is not None and fixed_binding.secret_id:
                logger.info("[SecretLocator] 规则未命中，回退本层固定绑定 slot=%s", slot)
                return Located(
                    secret_id=fixed_binding.secret_id, strategy="fixed", fallback_used=True
                )
            if on_miss == "model" and preferred_secret_id:
                if visible is None:
                    visible = await self._visible_records(session, user_id, dept_code)
                if any(getattr(s, "id", None) == preferred_secret_id for s in visible):
                    return Located(secret_id=preferred_secret_id, strategy="model_choice")
                logger.warning(
                    "[SecretLocator] 模型选的凭据不在可见集合内，已拒绝 slot=%s choice=%s",
                    slot,
                    preferred_secret_id,
                )

            target = wanted[0] if wanted else (spec.key_source if spec is not None else "?")
            return Located(
                denial=DENY_RULE_MISS,
                detail=f"规则未命中（要找 {target}），可见候选 {len(visible or [])} 条",
            )

        return Located(denial=None)

    @staticmethod
    async def _pick_binding(
        session,
        consumer_type: str,
        consumer_id: str,
        slot: str,
        user_id: str,
    ) -> SecretBinding | None:
        """取生效的绑定；个人绑定覆盖全局绑定。"""
        stmt = (
            select(SecretBinding)
            .where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
                SecretBinding.slot == slot,
                or_(
                    # 全局绑定：迁移后用空串，迁移前是 NULL，两种都要认。
                    # 必须分开写 or_：`IN (NULL, '')` 对 NULL 求值结果是 NULL
                    # 而不是 TRUE，匹配不到 `x IS NULL` 的行。
                    SecretBinding.bound_by_user_id.is_(None),
                    SecretBinding.bound_by_user_id == "",
                    SecretBinding.bound_by_user_id == user_id,
                ),
            )
            # 个人绑定排前面：同一槽位既被管理员全局绑定、又被本人绑定过时，
            # 应以本人的为准（否则用户改了等于没改）。
            .order_by(
                and_(
                    SecretBinding.bound_by_user_id.isnot(None),
                    SecretBinding.bound_by_user_id != "",
                ).desc()
            )
            .limit(1)
        )
        return (await session.execute(stmt)).scalars().first()

    @staticmethod
    async def _bindings_for_slot(
        session,
        consumer_type: str,
        consumer_id: str,
        slot: str,
        user_id: str,
    ) -> list[SecretBinding]:
        """取该槽位的全部候选绑定（本人的 + 全局的），个人优先、规则优先。"""
        stmt = (
            select(SecretBinding)
            .where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
                SecretBinding.slot == slot,
                or_(
                    SecretBinding.bound_by_user_id.is_(None),
                    SecretBinding.bound_by_user_id == "",
                    SecretBinding.bound_by_user_id == user_id,
                ),
            )
            .order_by(
                and_(
                    SecretBinding.bound_by_user_id.isnot(None),
                    SecretBinding.bound_by_user_id != "",
                ).desc(),
                (SecretBinding.strategy == "rule").desc(),
            )
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def _visible_records(session, user_id: str, dept_code: str | None) -> Sequence[Any]:
        """可见集合：本人的 + 被授权的系统级。规则只在这个集合里匹配。"""
        from app.services.secrets.center import list_visible_secrets

        return await list_visible_secrets(
            session, user_id=user_id, dept_code=dept_code, only_active=True
        )

    @staticmethod
    async def _has_grant(
        session,
        secret_id: str,
        user_id: str,
        dept_code: str | None,
        now: datetime,
    ) -> bool:
        """系统级凭据是否授权给了这个用户。"""
        conditions = [
            and_(SecretGrant.grantee_type == GRANTEE_USER, SecretGrant.grantee_id == user_id),
            # 全体授权不比对 grantee_id：它不指向任何实体
            SecretGrant.grantee_type == GRANTEE_ALL,
        ]
        if dept_code:
            conditions.append(
                and_(SecretGrant.grantee_type == GRANTEE_DEPT, SecretGrant.grantee_id == dept_code)
            )
        stmt = (
            select(SecretGrant.id)
            .where(
                SecretGrant.secret_id == secret_id,
                or_(*conditions) if len(conditions) > 1 else conditions[0],
                or_(SecretGrant.expires_at.is_(None), SecretGrant.expires_at > now),
            )
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None

    @staticmethod
    async def _platform_flags() -> tuple[bool, bool, int]:
        """平台策略（三层开关的第 ① 层）。读配置失败时用最保守的默认值。

        第三个返回值是「单次调用最多注入几条」。它**必须真的生效**：界面上写着
        「防止一次请求把全部凭据一起带走」，如果代码不读这个值，用户会以为有闸门
        而实际没有——那比不提供这个参数更糟。
        """
        from app.services.config_service import ConfigService

        try:
            configs = await ConfigService.get_all_from_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretResolver] 读取平台凭据策略失败，按最严格默认值处理: %s", exc)
            return False, False, DEFAULT_MAX_INJECT

        def _value(key: str):
            meta = configs.get(key) if isinstance(configs, dict) else None
            if isinstance(meta, dict):
                return meta.get("value")
            return meta

        return (
            _as_bool(_value("secret_policy_allow_script_env"), default=False),
            _as_bool(_value("secret_policy_allow_external_host"), default=True),
            _as_limit(_value("secret_policy_max_inject_per_call")),
        )

    @staticmethod
    async def _user_allows_script_env(session, user_id: str) -> bool:
        """用户偏好（第 ② 层）。没有记录 = 不允许。"""
        pref = await session.get(SecretUserPref, user_id)
        return bool(pref and pref.allow_script_env)

    @staticmethod
    def _pick_ciphertext(row: UserSecret, now: datetime) -> str | None:
        """轮换双写窗口内优先用新值，新值已过期才回退到旧值。"""
        if row.value_encrypted and (row.expires_at is None or row.expires_at > now):
            return row.value_encrypted
        if row.previous_value_encrypted and (
            row.previous_expires_at is None or row.previous_expires_at > now
        ):
            return row.previous_value_encrypted
        return row.value_encrypted

    @staticmethod
    def _decrypt(ciphertext: str) -> str:
        from app.utils.encryption import get_api_key_manager

        return get_api_key_manager().decrypt_api_key(ciphertext)

    @staticmethod
    async def _log_usage(
        session,
        record: SecretRecord,
        *,
        user_id: str,
        scene: str,
        consumer_type: str,
        consumer_id: str,
        slot: str,
        target_host: str | None,
        trace_id: str | None,
        conversation_id: str | None,
        result: str,
    ) -> None:
        """写使用流水。失败只告警——审计不能反过来阻断业务。"""
        try:
            session.add(
                SecretUsageLog(
                    id=str(uuid.uuid4()),
                    secret_id=record.id,
                    secret_key_snapshot=record.secret_key,
                    owner_scope=record.owner_scope,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    scene=scene,
                    consumer_type=consumer_type,
                    consumer_id=consumer_id,
                    slot=slot,
                    target_host=normalize_host(target_host) or None,
                    result=result,
                )
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretResolver] 写使用流水失败 %s: %s", record.secret_key, exc)


secret_resolver = SecretResolver()

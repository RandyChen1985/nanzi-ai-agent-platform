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

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import and_, or_, select

from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretBinding, SecretGrant, SecretUsageLog, SecretUserPref, UserSecret
from app.services.secrets.references import stringify_field_value
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
    #: 这条绑定只取某个字段时给路径，调用方据此从明文里挑出子字段。
    #: 空串 = 整段给。
    value_path: str = ""
    #: ``text`` / ``json``。注入层据此判断「整段给」是否合法：结构化凭据整段注入
    #: 会把一整份 JSON 塞进参数或请求头，几乎总是配置错误。
    value_format: str = ""
    #: 规则声明「命中的凭据都得是这个模板」；空串 = 不限。
    value_template: str = ""
    #: 本次写下的使用流水 id；注入失败时调用方据此把那条流水改成失败。
    usage_id: str | None = None

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
    #: 这条绑定要求「只取某个字段」；空串 = 整段。
    #: 固定绑定与规则绑定都可以带：规则带的是一个**约定**——命中的凭据都取这个字段，
    #: 缺这个字段的那条会明确失败，而不是把整份 JSON 注进去。
    value_path: str = ""
    #: 规则声明的模板约束（只有规则策略会有）；空串 = 不限。
    value_template: str = ""


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
        plain_cache: dict[str, str] | None = None,
        record_usage: bool = True,
    ) -> ResolveResult:
        """解析一个槽位。返回 ``ok=False`` 时调用方应走自己的默认值。

        ``plain_cache``：一次工具调用内多个槽位指向同一条凭据时（「一条凭据供多个参数」），
        解密只做一次。传同一个 dict 进来即可，作用域是单次调用——**不要**做成进程级缓存，
        那会让改完凭据后旧明文继续生效，也让明文在内存里留得比必要的久。
        定位、场景、域名、授权、审计每次都照跑，缓存的只有解密这一步。
        """
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
                    scene=scene,
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
                        if record_usage:
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
                    if record_usage:
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

                plaintext = None
                if plain_cache is not None:
                    plaintext = plain_cache.get(record.id)
                if plaintext is None:
                    plaintext = self._decrypt(ciphertext)
                    if plain_cache is not None:
                        plain_cache[record.id] = plaintext

                usage_id = (
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
                    if record_usage
                    else None
                )

                return ResolveResult(
                    ok=True,
                    secret_id=record.id,
                    secret_key=record.secret_key,
                    value=SensitiveValue(plaintext),
                    value_path=getattr(located, "value_path", "") or "",
                    value_format=(getattr(row, "value_format", None) or "text"),
                    value_template=(
                        getattr(located, "value_template", "") or ""
                    ),
                    usage_id=usage_id,
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

    async def mark_usage_failed(self, usage_id: str | None, reason: str) -> None:
        """把一条已经写成 success 的流水改成失败。

        解密成功、但注入这一步失败（引用解不开、字段在凭据里不存在、结构化却没选
        字段）时，流水上的 success 是**假的**：统计会把「配置错了」算成「用得好」，
        排查的人也会以为这条凭据一直正常。所以让注入层回头改这一条，
        而不是再补记一条——否则「一次调用」在报表里会变成两条。
        """
        if not usage_id:
            return
        try:
            async with AsyncSessionLocal() as session:
                row = await session.get(SecretUsageLog, usage_id)
                if row is None:
                    return
                row.result = reason or "reference_unresolved"
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - 审计失败不该反过来影响调用
            logger.warning("[SecretResolver] 回写流水失败 %s: %s", usage_id, exc)

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
        scene: str | None = None,
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
                    return Located(
                        secret_id=fixed_binding.secret_id,
                        strategy="fixed",
                        value_path=getattr(fixed_binding, "value_path", "") or "",
                    )
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
                    # 规则声明的模板约束：结构不符当作**没命中**，绝不拿另一种结构的
                    # 凭据去按字段取值——那只会在取字段时失败，或者更糟：恰好同名但
                    # 含义不同的字段被静默注进去。
                    #
                    # 约束要参与**候选筛选**而不是挑中后再否掉：按 name 匹配时可能有多条
                    # 同名不同模板的凭据，先挑中一条再判不符，会让本该命中的那条永远轮不到。
                    tpl_wanted = getattr(spec, "value_template", "") or ""
                    pool = visible
                    if tpl_wanted:
                        pool = [
                            record
                            for record in visible
                            if (getattr(record, "value_template", None) or "") == tpl_wanted
                        ]
                    field_value = (
                        field_value_resolver(spec.target_field, scene=scene)
                        if getattr(spec, "target", "") == "field"
                        else None
                    )
                    if field_value is not None and len(pool) > 200:
                        # 按字段值匹配要把候选整批解密。数量异常时先留个痕，
                        # 免得「调用变慢」查到最后是一条规则把所有人凭据都解了一遍。
                        logger.warning(
                            "[SecretLocator] 按字段值匹配的候选过多（%s 条），"
                            "consumer=%s/%s slot=%s",
                            len(pool), consumer_type, consumer_id, slot,
                        )
                    hit, used = pick_match(spec, wanted, pool, field_value=field_value)
                    if hit is None and tpl_wanted:
                        logger.info(
                            "[SecretLocator] 规则要求模板 %s，可见候选中没有结构相符的凭据 "
                            "consumer=%s/%s slot=%s 候选=%s",
                            tpl_wanted,
                            consumer_type,
                            consumer_id,
                            slot,
                            len(pool),
                        )
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
                        return Located(
                            secret_id=getattr(hit, "id", None),
                            strategy="rule",
                            # 规则也能约定「都取这个字段」，缺字段的那条会明确失败
                            value_path=getattr(rule_binding, "value_path", "") or "",
                            value_template=tpl_wanted,
                        )

            # —— 未命中：只看规则自己声明的策略，绝不默认回退
            on_miss = getattr(spec, "on_miss", "fail") if spec is not None else "fail"
            if on_miss == "fallback_fixed" and fixed_binding is not None and fixed_binding.secret_id:
                logger.info("[SecretLocator] 规则未命中，回退本层固定绑定 slot=%s", slot)
                return Located(
                    secret_id=fixed_binding.secret_id,
                    strategy="fixed",
                    fallback_used=True,
                    value_path=getattr(fixed_binding, "value_path", "") or "",
                )
            if on_miss == "model" and preferred_secret_id:
                if visible is None:
                    visible = await self._visible_records(session, user_id, dept_code)
                chosen = next(
                    (s for s in visible if getattr(s, "id", None) == preferred_secret_id),
                    None,
                )
                if chosen is not None:
                    tpl_wanted = getattr(spec, "value_template", "") or ""
                    if tpl_wanted and (
                        (getattr(chosen, "value_template", None) or "") != tpl_wanted
                    ):
                        # 模型挑的那条结构不符：按未命中处理，别让它带着 value_path
                        # 去取另一种结构的字段
                        logger.info(
                            "[SecretLocator] 模型选的凭据模板不符，已拒绝 slot=%s 期望=%s 实际=%s",
                            slot,
                            tpl_wanted,
                            getattr(chosen, "value_template", None) or "（未声明）",
                        )
                    else:
                        # 取值字段来自规则本身（规则是这条绑定配的，模型只补「哪一条」）
                        return Located(
                            secret_id=preferred_secret_id,
                            strategy="model_choice",
                            value_path=getattr(rule_binding, "value_path", "") or "",
                            value_template=tpl_wanted,
                        )
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
    ) -> str | None:
        """写使用流水，返回流水 id。失败只告警——审计不能反过来阻断业务。

        返回 id 是为了让调用方在**后续**步骤失败时（引用解析不出来、字段取不到）
        把这条流水改成失败：先记 success 再没人管，统计上会把「配置错了」算成「用得好」。
        """
        usage_id = str(uuid.uuid4())
        try:
            session.add(
                SecretUsageLog(
                    id=usage_id,
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
            return usage_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretResolver] 写使用流水失败 %s: %s", record.secret_key, exc)
            return None

# ── 按凭据字段值匹配：取值器 ─────────────────────────────────────────────
#
# 放在模块级而不是 SecretResolver 的静态方法里：试算接口
# （``POST /secrets/locate-preview``）也要用它。试算如果拿不到取值器，
# 就会一律按未命中报——真实调用却命中——把配置者彻底带偏。


def field_value_resolver(field: str, scene: str | None = None) -> Callable[[Any], str | None]:
    """按凭据内某个字段的值匹配时用的取值器：解密 → 解析 JSON → 取字段。

    只有 ``target="field"`` 才走这里。key / name / category 都是凭据表上的列，
    直接读就行；字段值必须解密，所以这条路径比普通规则重得多——一条规则会让
    整批候选都过一遍解密。

    解不开、不是 JSON、字段不存在、值是对象/数组、凭据当前不可用——一律当作
    「这条取不到」，继续看下一条。单条坏数据不该让整次解析失败，更不能因此把
    别处的凭据注进去。

    **注意这条路径会解密每一份可见候选。** 对「别人授权给我用的系统级凭据」
    来说，这相当于给了调用方一个等值探测口子：配一条规则、传一个值，就能从
    命中与否反推那条凭据的某个字段是不是这个值。前提是他本来就有该凭据的
    使用授权（规则只在可见集合里挑），而且每次试探都会走正常 resolve 路径、
    在凭据流水里留下记录，事后可追。要收紧的话，收紧点在这里。

    结果按凭据 id 记忆：一条规则最多 3 个模板，每个模板都会重放一遍候选列表，
    不记的话同一批凭据会被反复解密三遍。
    """
    key = (field or "").strip()
    cache: dict[str, str | None] = {}

    def _remember(record_id: str, value: str | None) -> str | None:
        if record_id:
            cache[record_id] = value
        return value

    def resolve(record: Any) -> str | None:
        if not key:
            return None
        record_id = str(getattr(record, "id", "") or "")
        if record_id and record_id in cache:
            return cache[record_id]

        now = datetime.utcnow()
        # 整条凭据都过期的不参与匹配。否则它可能凭字段值胜出，随后被
        # evaluate_secret 判过期拒绝，把后面真正可用的那条挡住——顺序决定谁先被看到，
        # 而用户只会看到「凭据不可用」，完全想不到是被一条过期的抢了位置。
        if not _record_is_usable(record, now, scene):
            return _remember(record_id, None)
        try:
            # naive UTC：库里所有 datetime 都是这个口径（见 resolve 里 now 的注释）。
            # 用带时区的 now 去和 expires_at 比会抛 TypeError，被下面的兜底吞掉，
            # 表现成「规则永远未命中」——排查时完全看不出来。
            ciphertext = SecretResolver._pick_ciphertext(record, now)
            if not ciphertext:
                return _remember(record_id, None)
            plain = SecretResolver._decrypt(ciphertext)
        except Exception:  # noqa: BLE001 - 单条坏数据不该让整次匹配失败
            logger.debug("[SecretLocator] 候选凭据解密失败，跳过", exc_info=True)
            return _remember(record_id, None)

        try:
            node: Any = json.loads(plain)
        except (TypeError, ValueError):
            return _remember(record_id, None)
        # 点号当路径：正则允许 ``auth.username``，只看顶层键会让嵌套凭据
        # 配得出规则却永远取不到值。数组下标与 references.pick_field_typed
        # 同语义（``items.0.id``），否则同一份路径在引用解析里能取、在规则里取不到。
        for part in key.split("."):
            if isinstance(node, dict):
                node = node.get(part)
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return _remember(record_id, None)
            if node is None:
                return _remember(record_id, None)
        if node is None or isinstance(node, (dict, list)):
            # 只有标量适合跟入参比：整段对象拿去和字符串比永远不等
            return _remember(record_id, None)
        # 与 header / 环境变量侧同一套字符串化口径：bool 给 true/false、
        # 465.0 给 465。各写一份 str() 会让同一条凭据在两边比出不同结果。
        return _remember(record_id, stringify_field_value(node))

    return resolve


def _record_is_usable(row: Any, now: datetime, scene: str | None = None) -> bool:
    """凭据现在能不能用：场景放行 + 还有可用密文（轮换过渡期内旧值仍有效）。

    场景也要在这里判。只在 evaluate_secret 里判的话，一条「不给这个场景用」的
    凭据会凭字段值先胜出、随后被判拒绝，把后面真正能用的那条挡住——用户只看到
    「凭据不可用」，完全想不到是被一条根本不该参与匹配的抢了位置。
    口径与 policy 一致：``allowed_scenarios`` 为空即不授权任何场景。
    """
    if scene:
        scopes = getattr(row, "allowed_scenarios", None) or []
        if not isinstance(scopes, (list, tuple)) or scene not in [
            str(s) for s in scopes if s
        ]:
            return False
    if getattr(row, "value_encrypted", None) and (
        getattr(row, "expires_at", None) is None or row.expires_at > now
    ):
        return True
    if getattr(row, "previous_value_encrypted", None) and (
        getattr(row, "previous_expires_at", None) is None
        or row.previous_expires_at > now
    ):
        return True
    return False


secret_resolver = SecretResolver()

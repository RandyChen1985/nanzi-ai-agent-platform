"""忘记密码（邮件找回）服务。

三条不可动摇的约束（都来自设计文档，且都由本模块保证，端点无从写错）：

1. **校验先于核销**：`verify_token` 只读，`consume_token` 才核销。任何校验（账号状态、
   密码复杂度）都排在核销之前，否则用户只是密码设得不够强，就白白失去这条链接、
   必须重走一遍邮件流程。这与 `embed_service` 里「来源校验失败却把票烧掉」的踩坑
   是同构问题，那里已经留下过注释。
2. **一次性**：核销用 `GETDEL`（原子取删），并发两个请求只有一个能拿到值。
3. **新申请作废旧链接**：每个用户只有一个键，签发即覆盖写，因此不存在「两条链接同时
   有效」的可能，也不需要读旧值（没有并发窗口、没有竞态）。

为什么把编排放在服务层而不是端点里：顺序性约束一旦暴露给调用方，迟早会被写错。

本模块还刻意把「功能是否可用」收敛成 `availability()` 一处，让端点与
`/auth/config/public` 用同一套判定，不会出现「入口显示了但接口 503」。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import USER_STATUS_ENABLED, User
from app.services.ai.tools.generated_file_service import get_download_url_prefix
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# 防枚举的对外文案。所有「不该发信」的分支都必须返回这一句，**包括被限流的分支** ——
# 否则攻击者能用「操作过于频繁」反推某个邮箱刚刚被申请过，从而确认邮箱已注册。
UNIFIED_MESSAGE = "如果该邮箱已注册，我们已发送重置邮件"
INVALID_TOKEN_MESSAGE = "链接无效或已过期，请重新申请"
RESET_SUCCESS_MESSAGE = "密码重置成功，请使用新密码登录"

RESET_PAGE_PATH = "/reset-password"


def _token_key(user_id: int) -> str:
    return f"{AuthService.PWD_RESET_PREFIX}{int(user_id)}"


class PasswordResetService:
    # ---------------- token ---------------- #

    @staticmethod
    def hash_nonce(nonce: str) -> str:
        return hashlib.sha256((nonce or "").encode("utf-8")).hexdigest()

    @staticmethod
    def parse_token(token: str) -> Optional[Tuple[int, str]]:
        """解析 `{uid}.{nonce}`；任何形态异常一律返回 None，绝不抛异常。"""
        if not token or not isinstance(token, str):
            return None
        uid_text, sep, nonce = token.partition(".")
        if not sep or not uid_text or not nonce:
            return None
        if not uid_text.isdigit():
            return None
        return int(uid_text), nonce

    @staticmethod
    async def issue_token(user_id: int, redis) -> str:
        """签发并**覆盖**该用户的旧链接。返回明文 token（只出现在邮件里）。

        覆盖写是「新申请作废旧链接」的全部实现 —— 不需要先删旧键，也就没有
        「删除成功但写入失败」这类中间态。
        """
        nonce = secrets.token_urlsafe(32)
        await redis.set(
            _token_key(user_id),
            PasswordResetService.hash_nonce(nonce),
            ex=AuthService.PWD_RESET_TTL_SECONDS,
        )
        return f"{int(user_id)}.{nonce}"

    @staticmethod
    async def _read_stored(redis, key: str) -> Optional[str]:
        """统一处理 decode_responses 开关下的 bytes / str。"""
        raw = await redis.get(key)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    @staticmethod
    async def verify_token(token: str, redis) -> Optional[int]:
        """**只读**校验，返回 user_id；不产生任何副作用。"""
        parsed = PasswordResetService.parse_token(token)
        if not parsed:
            return None
        user_id, nonce = parsed
        stored = await PasswordResetService._read_stored(redis, _token_key(user_id))
        if not stored:
            return None
        # compare_digest 而非 ==：避免按字节短路带来的时序差异
        if not hmac.compare_digest(PasswordResetService.hash_nonce(nonce), stored):
            return None
        return user_id

    @staticmethod
    async def consume_token(token: str, redis) -> Optional[int]:
        """校验并原子核销（GETDEL），返回 user_id；任何失败都不消耗 token。

        先 GET 校验、再 GETDEL 核销、最后比对「核销到的」与「刚校验的」是否一致：
        直接 GETDEL 会在校验失败时把 token 烧掉；而只比一次又会给「同一 key 被换进
        另一张票」留下窗口。范式与 embed_service 一致。
        """
        parsed = PasswordResetService.parse_token(token)
        if not parsed:
            return None
        user_id, nonce = parsed
        key = _token_key(user_id)

        stored = await PasswordResetService._read_stored(redis, key)
        if not stored:
            return None
        if not hmac.compare_digest(PasswordResetService.hash_nonce(nonce), stored):
            return None

        consumed = await redis.getdel(key)
        if consumed is None:
            # 并发下已被另一个请求核销
            return None
        consumed_text = (
            consumed.decode("utf-8") if isinstance(consumed, bytes) else str(consumed)
        )
        if consumed_text != stored:
            return None
        return user_id

    @staticmethod
    async def check_token_usable(token: str, db: AsyncSession, redis) -> bool:
        """只读预校验：token 有效且账号可用。**不核销**，校验完链接照常能用。

        供重置页在用户填密码之前调用，避免「填完一整遍才被告知链接已过期」。

        必须与 `reset_password` 的前两步**逐条对齐**（token → 账号状态）：
        预校验比实际提交宽松，就会出现「页面显示有效、提交报失效」，
        那比不做预校验更让人困惑；比提交更严格，则会让本来能用的链接被误判失效。
        密码复杂度不在此列——它取决于用户即将输入的内容，不是链接的属性。
        """
        user_id = await PasswordResetService.verify_token(token, redis)
        if user_id is None:
            return False

        row = (
            await db.execute(select(User.status).where(User.id == user_id))
        ).first()
        # 与 reset_password 同口径：账号不存在/待审核/已禁用都算链接不可用
        return row is not None and int(row.status) == USER_STATUS_ENABLED

    # ---------------- 风控 ---------------- #

    @staticmethod
    async def _safe(fn, default):
        """Redis 单条命令异常的降级包装：告警后返回 default。

        与注册/登录限流同策略：限流组件故障不能把功能整体关掉（fail-open）。
        Redis **整体**不可用是另一回事，由端点直接 503（fail-closed）。
        """
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("密码重置限流读写失败，本次跳过：%s", exc)
            return default

    @staticmethod
    async def _count(redis, key: str) -> int:
        async def _read() -> int:
            value = await redis.get(key)
            if value is None:
                return 0
            text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            return int(text)

        return await PasswordResetService._safe(_read, 0)

    @staticmethod
    async def pass_rate_limits(email: str, source: str, redis) -> bool:
        """三层风控：邮箱小时配额 → 来源小时配额 → 邮箱冷却。

        通过则占用冷却并累加两个计数，返回 True；任一维度触顶返回 False。
        冷却**在最后**才占用：被前两档拒绝的请求不应该顺带占掉冷却窗口。
        """
        email_count_key = AuthService.pwd_reset_email_count_key(email)
        source_count_key = AuthService.pwd_reset_source_count_key(source)
        cooldown_key = AuthService.pwd_reset_cooldown_key(email)

        if await PasswordResetService._count(
            redis, email_count_key
        ) >= AuthService.PWD_RESET_EMAIL_LIMIT:
            return False
        if await PasswordResetService._count(
            redis, source_count_key
        ) >= AuthService.PWD_RESET_SOURCE_LIMIT:
            return False

        async def _acquire_cooldown():
            # SET NX EX 是原子的：并发请求只有一个能拿到
            return await redis.set(
                cooldown_key,
                "1",
                ex=AuthService.PWD_RESET_COOLDOWN_SECONDS,
                nx=True,
            )

        if not await PasswordResetService._safe(_acquire_cooldown, True):
            return False

        async def _bump(key: str) -> None:
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, AuthService.PWD_RESET_WINDOW_SECONDS)
            else:
                ttl = await redis.ttl(key)
                if ttl is not None and int(ttl) < 0:
                    # 计数键丢了 TTL（例如被手工清理过）时补上，避免永久累积
                    await redis.expire(key, AuthService.PWD_RESET_WINDOW_SECONDS)

        await PasswordResetService._safe(lambda: _bump(email_count_key), None)
        await PasswordResetService._safe(lambda: _bump(source_count_key), None)
        return True

    # ---------------- 可用性与链接 ---------------- #

    @staticmethod
    async def availability() -> Optional[str]:
        """返回 None 表示功能可用；否则返回**给管理员看的**不可用原因。

        端点与 `/auth/config/public` 都只调这一个函数，保证「入口是否显示」与
        「接口是否可调」用的是同一套判定。

        「对外地址为空」也算不可用：下载链接退化成相对地址仍能用（用户就在 Web 应用
        里点），但**邮件里的相对地址点不开**，发出去就是废邮件。
        """
        from app.services.email_delivery_service import EmailDeliveryService

        if await EmailDeliveryService.load_global_smtp_settings() is None:
            return "邮件找回功能未开启，请联系管理员"
        if not (await get_download_url_prefix()).strip():
            return "平台未配置对外访问地址，请先在系统配置中填写 download_url_prefix"
        return None

    @staticmethod
    async def build_link(user_id: int, redis) -> Optional[str]:
        """签发 token 并拼出完整重置链接；前缀为空时返回 None。"""
        prefix = (await get_download_url_prefix()).strip()
        if not prefix:
            return None
        token = await PasswordResetService.issue_token(user_id, redis)
        return f"{prefix}{RESET_PAGE_PATH}?token={token}"

    @staticmethod
    def build_mail(link: str) -> Tuple[str, str]:
        subject = "AI 智能体平台 - 密码重置"
        body = (
            "我们收到了重置你账号密码的请求。\n\n"
            f"请在 {AuthService.PWD_RESET_TTL_SECONDS // 60} 分钟内点击下面的链接设置新密码"
            "（该链接只能使用一次）：\n\n"
            f"{link}\n\n"
            "如果链接已过期，请在登录页重新发起找回密码。\n\n"
            "如果这不是你本人的操作，请忽略本邮件，你的密码不会被修改。\n"
            "如果你怀疑账号已被他人使用，建议登录后到「个人中心」重置 API Key。\n"
        )
        return subject, body

    @staticmethod
    async def deliver(email: str, subject: str, body: str) -> None:
        """后台发信：任何异常只记日志。

        响应必须与「有没有真的发出去」无关 —— 反枚举要求响应不可能报告真实发送结果。
        """
        from app.services.email_delivery_service import EmailDeliveryService

        try:
            settings = await EmailDeliveryService.load_global_smtp_settings()
            if settings is None:
                logger.warning("密码重置邮件未发出：全局邮件服务不可用")
                return
            ok, message = await asyncio.to_thread(
                EmailDeliveryService.send_mail, settings, [email], subject, body
            )
            if not ok:
                logger.warning("密码重置邮件发送失败：%s", message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("密码重置邮件发送异常：%s", exc)

    # ---------------- 请求侧编排 ---------------- #

    @staticmethod
    async def request_reset(
        db: AsyncSession, email: str, source: str, redis
    ) -> Optional[Tuple[int, str]]:
        """限流 → 查库，返回 (user_id, 邮箱) 表示「应当发信」，否则 None。

        调用方**无论拿到什么**都必须返回同一句 UNIFIED_MESSAGE（含被限流的场景）。

        限流刻意排在查库**之前**且对不存在的邮箱同样计数：若先查库、只在命中时才计数，
        「计数有没有被消耗」本身就变成了邮箱是否存在的观测点。
        """
        if not await PasswordResetService.pass_rate_limits(email, source, redis):
            logger.info("password_reset_rate_limited source=%s", source)
            return None

        normalized = AuthService.normalize_email(email)
        if not normalized:
            return None

        row = (
            await db.execute(
                select(User.id, User.email).where(
                    func.lower(User.email) == normalized,
                    User.status == USER_STATUS_ENABLED,
                )
            )
        ).first()
        if row is None:
            # 邮箱不存在，或账号处于待审核/已禁用：一律静默（他们本就登录不了，
            # 给一条可用的凭证路径没有意义）
            return None
        return int(row.id), str(row.email)

    # ---------------- 重置编排 ---------------- #

    @staticmethod
    async def reset_password(
        token: str, new_password: str, db: AsyncSession, redis
    ) -> Tuple[bool, str]:
        """校验 → 核销 → 改密 → 吊销会话。返回 (是否成功, 可直接展示的文案)。

        **所有校验都在核销之前**（见模块头注释约束 1）：
          1 校验 token（只读）→ 2 查账号与状态 → 3 密码复杂度 → 4 原子核销
          → 5 改密 → 6 吊销该用户全部会话。
        """
        user_id = await PasswordResetService.verify_token(token, redis)
        if user_id is None:
            return False, INVALID_TOKEN_MESSAGE

        row = (
            await db.execute(
                select(User.id, User.user_name, User.status).where(User.id == user_id)
            )
        ).first()
        if row is None or int(row.status) != USER_STATUS_ENABLED:
            # 账号不存在/待审核/已禁用一律同一句，不把状态区分出来
            return False, INVALID_TOKEN_MESSAGE

        valid, reason = AuthService.validate_password_complexity(
            new_password, username=row.user_name
        )
        if not valid:
            # 此时**尚未核销**，链接仍然可用 —— 用户改个更强的密码就能继续用
            return False, reason

        consumed_user_id = await PasswordResetService.consume_token(token, redis)
        if consumed_user_id is None:
            # 并发下已被别人用掉，或同一 key 被换成了另一张票
            return False, INVALID_TOKEN_MESSAGE

        password = new_password
        raw = password.encode("utf-8")
        if len(raw) > 72:
            # bcrypt 上限，与 PUT /auth/password 保持一致
            password = raw[:72].decode("utf-8", errors="ignore")

        await AuthService.set_user_password(user_id, password, db=db)
        await AuthService.revoke_sessions_for_user(user_id)

        # 审计：与「个人中心自助改邮箱」同口径（结构化日志，不建审计表）；
        # 绝不记录 token、密码或密码哈希。
        logger.info(
            "password_reset_succeeded user_id=%s user_name=%s", user_id, row.user_name
        )
        return True, RESET_SUCCESS_MESSAGE

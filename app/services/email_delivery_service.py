"""统一的邮件发送服务。

三件事：
1. 从系统配置加载全局 SMTP，或从用户通知配置加载自定义 SMTP；
2. 按调用方要求的分层（global / user）解析出最终 SMTP 设置；
3. 发信，并把底层错误整理成可直接展示给管理员的文本（绝不带密码）。

为什么要有这个模块：邮件发送原先只存在于 notification_service 的用户通知路径里，
而「审核结果通知」是系统发给用户的，必须走全局配置——不能因为用户把自己的 SMTP
关掉或填错就发不出去，那恰恰是他能否登录的前提。
"""
from __future__ import annotations

import json
import logging
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.platform_timezone import format_platform_datetime

logger = logging.getLogger(__name__)

SmtpScope = Literal["user", "global"]

DEFAULT_TIMEOUT = 10


class SmtpDeliveryError(RuntimeError):
    """传输层发送失败（连接/超时/认证等），意味着「可以重试」。

    单独定义类型是为了让需要退避重试的调用方（notification_service）能靠异常区分
    瞬时故障与业务性失败——业务性失败用返回值表达，不抛异常。
    异常的 message 已做口令清洗，可直接进日志。
    """


@dataclass
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    security: str          # ssl | starttls | none
    from_address: str
    sender_name: str
    timeout: int = DEFAULT_TIMEOUT

    def is_complete(self) -> bool:
        """host 是唯一必需项。

        免认证的内部中继（内网最常见的邮件出口）合法地没有账号密码，
        所以 user/password 必须允许同时为空；但只填其中一个属于半套凭据，
        几乎总是配置失误——早报错比默默用匿名发送好。
        """
        if not (self.host or "").strip():
            return False
        return bool((self.user or "").strip()) == bool((self.password or "").strip())

    def effective_from(self) -> str:
        return (self.from_address or "").strip() or (self.user or "").strip()


class EmailDeliveryService:
    MASK = "***"

    # ---------------- 收件人 ---------------- #

    @staticmethod
    def parse_recipients(raw: Any) -> List[str]:
        """逗号/分号分隔 → 去空白 → 去重（保持原顺序）。

        全角逗号/分号必须先归一化：中文输入法下 `a@x.com，b@y.com` 极其常见，
        不归一就会被当成**一个**收件人塞进 sendmail，导致整条通知发送失败。
        （旧实现做了这步归一，重写时丢了，属回归。）
        同时保留旧实现「必须含 @」的过滤，避免把误输入的词条当收件人。
        """
        if not raw:
            return []
        text = str(raw).replace("；", ";").replace("，", ",")
        seen: List[str] = []
        for part in re.split(r"[,;]", text):
            addr = part.strip()
            if addr and "@" in addr and addr not in seen:
                seen.append(addr)
        return seen

    @classmethod
    def compose_recipients(
        cls, self_email: Optional[str], include_self: bool, extra: Any
    ) -> List[str]:
        """「默认抄送自己」的真正落地点。

        include_self 存的是布尔标志而不是邮箱快照：用户之后改了邮箱，
        收件人必须自动跟着变。写快照会导致通知一直发往已废弃的地址。
        去重是为了兼容存量用户——他们的 recipients 里可能已经写了自己。
        """
        result: List[str] = []
        if include_self and self_email:
            result.append(self_email.strip())
        for addr in cls.parse_recipients(extra):
            if addr not in result:
                result.append(addr)
        return result

    @classmethod
    async def resolve_self_email(
        cls, user_id: Optional[int], db: Optional[AsyncSession]
    ) -> Optional[str]:
        """取用户本人邮箱——「抄送自己」的唯一取数入口。

        必须每次现查库，不能用 require_api_key 注入的 user dict：那份缓存在 Redis 里
        （TTL 1 小时），用户改完邮箱后通知会一直发往旧地址。
        收敛到一处也让调用方（尤其是连通性测试）能以替换该入口的方式测试，
        不必去改共享库里真实用户的邮箱。
        """
        if user_id is None or db is None:
            return None
        from app.models.user import User

        value = (await db.execute(select(User.email).where(User.id == user_id))).scalar()
        text = str(value).strip() if value is not None else ""
        return text or None

    # ---------------- 分层 ---------------- #

    @staticmethod
    def resolve_smtp_source(config: Dict[str, Any]) -> str:
        """返回 'global' 或 'custom'。

        存量兼容零迁移：老的 config_json 里没有 smtp_source，此时按 smtp_host
        是否已填自适应——填了就当自定义（行为与升级前完全一致），没填就用全局。
        自适应、可逆、不需要改历史数据。
        """
        source = str(config.get("smtp_source") or "").strip().lower()
        if source in ("global", "custom"):
            return source
        return "custom" if str(config.get("smtp_host") or "").strip() else "global"

    # ---------------- 加密方式 ---------------- #

    ALLOWED_SECURITY = ("ssl", "starttls", "none")

    @classmethod
    def infer_security(cls, config: Dict[str, Any], port: int) -> str:
        """加密方式：显式配置优先，缺失时按端口推断。

        这里必须按端口推断，不能硬兜底成 'ssl'。历史实现是按端口自适应的
        （465 → SMTP_SSL，其余 → SMTP + starttls），而**个人中心的自定义 SMTP
        表单没有加密方式字段**，配置里就不存在 security/smtp_security 键。
        硬兜底成 'ssl' 会让所有 587（STARTTLS，企业邮箱最常见）/ 25 端口的存量
        用户从「可用」变成「必然失败」——那是把原有功能改坏，不是新增功能没做好。
        显式值（全局配置的 email_smtp_security，可填 starttls/none）始终优先。
        """
        explicit = str(
            config.get("security") or config.get("smtp_security") or ""
        ).strip().lower()
        if explicit:
            return explicit
        return "ssl" if int(port) == 465 else "starttls"

    @classmethod
    def build_custom_settings(cls, config: Dict[str, Any]) -> SmtpSettings:
        """从用户表单/配置构造自定义 SMTP —— **唯一入口**。

        之前 `load_user_smtp_settings` 与 `_test_email` 各写一份同样的构造代码，
        正是本特性要消除的「实现漂移」形态；收敛到一处后，加密方式的推断规则
        不可能再出现两处不一致。

        非法值一律抛 ValueError，由调用方转成给用户看的错误。**绝不静默降级**：
        未知的 security 值若落到「明文发送」就是安全事故（决策 12）。
        """
        raw_port = config.get("smtp_port")
        try:
            port = int(raw_port or 465)
        except (TypeError, ValueError) as exc:
            raise ValueError("SMTP 端口格式错误") from exc
        if not (1 <= port <= 65535):
            raise ValueError("SMTP 端口必须在 1-65535 之间")

        security = cls.infer_security(config, port)
        if security not in cls.ALLOWED_SECURITY:
            raise ValueError(
                f"不支持的加密方式：{security}（仅允许 {'/'.join(cls.ALLOWED_SECURITY)}）"
            )

        return SmtpSettings(
            host=str(config.get("smtp_host") or "").strip(),
            port=port,
            user=str(config.get("smtp_user") or "").strip(),
            password=str(config.get("smtp_password") or ""),
            security=security,
            from_address=str(config.get("from_address") or "").strip(),
            sender_name=str(config.get("sender_name") or "AI Agent").strip(),
        )

    @staticmethod
    def _from_global_row(row: Dict[str, Any]) -> SmtpSettings:
        def _int(v, default):
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return default

        port = _int(row.get("email_smtp_port"), 465)

        return SmtpSettings(
            host=str(row.get("email_smtp_host") or "").strip(),
            port=port,
            user=str(row.get("email_smtp_user") or "").strip(),
            password=str(row.get("email_smtp_password") or ""),
            # 与自定义路径同一套推断：显式值优先，缺失时按端口判断
            security=EmailDeliveryService.infer_security(
                {"security": row.get("email_smtp_security")}, port
            ),
            from_address=str(row.get("email_from_address") or "").strip(),
            sender_name=str(row.get("email_sender_name") or "NanZi AI Agent").strip(),
        )

    @classmethod
    async def load_global_smtp_settings(cls) -> Optional[SmtpSettings]:
        """全局 SMTP；开关关闭或配置不完整返回 None。"""
        from app.services.config_service import ConfigService

        try:
            configs = await ConfigService.get_all_from_db()
        except Exception as exc:
            logger.warning("读取邮件服务配置失败: %s", exc)
            return None

        if str(configs.get("email_service_enabled", {}).get("value", "false")).strip().lower() not in (
            "true", "1", "yes", "on"
        ):
            return None

        row = {k: v.get("value") for k, v in configs.items() if k.startswith("email_")}
        settings = cls._from_global_row(row)
        if not settings.is_complete():
            return None
        return settings

    @classmethod
    async def load_user_smtp_settings(
        cls, user_id: int, db: Optional[AsyncSession] = None
    ) -> Tuple[Optional[SmtpSettings], str]:
        """返回 (设置, 实际来源)。设置可能为 None（配置不完整或未启用）。"""
        from app.services.notification_service import NotificationService

        own_session = db is None
        session = db
        if own_session:
            from app.core.orm import AsyncSessionLocal
            session = AsyncSessionLocal()
        try:
            record = await NotificationService.get_config_by_type_raw(session, user_id, "email")
            config: Dict[str, Any] = {}
            if record and record.config_json:
                try:
                    config = json.loads(record.config_json)
                except Exception:
                    config = {}

            source = cls.resolve_smtp_source(config)
            if source == "global":
                return await cls.load_global_smtp_settings(), "global"

            try:
                settings = cls.build_custom_settings(config)
            except ValueError as exc:
                logger.warning(
                    "用户自定义 SMTP 配置非法，已按不可用处理: user_id=%s err=%s",
                    user_id, exc,
                )
                return None, "custom"
            return (settings if settings.is_complete() else None), "custom"
        finally:
            if own_session and session is not None:
                await session.close()

    @classmethod
    async def resolve_admin_recipients(cls, db: AsyncSession) -> List[str]:
        """取全部可收信的管理员邮箱（已去重）。

        为什么必须同时判 NULL 与 TRIM<>''：邮箱列可空、且可能残留历史空串。只判
        IS NOT NULL 会让空串混进收件人列表；SMTP 只要拒绝其中**一个**地址，整封邮件
        都发不出去 —— 等于把「一个管理员没填好」升级成「所有人都收不到」。
        """
        from app.models.user import User

        rows = (
            await db.execute(
                select(User.email).where(
                    User.role == "admin",
                    User.email.isnot(None),
                    func.trim(User.email) != "",
                )
            )
        ).scalars().all()
        return dedupe_recipient_emails(rows)

    @classmethod
    async def notify_admins_of_pending_registration(
        cls, user_id: int, db: Optional[AsyncSession] = None
    ) -> Tuple[bool, Optional[str]]:
        """向所有填了邮箱的管理员发一封「有待审核的账号申请」。返回 (sent, error)。

        **尽力而为**：任何异常都只记日志。调用方是注册接口的后台任务 —— 它既不该、
        也无法把失败回传给注册者，因为「是否发出通知」属于平台内部信息。

        session 生命周期：**绝不能**把请求作用域的 session 传进后台任务。响应返回后
        它已关闭，会抛「session 已关闭」；若恰好落在另一个事件循环上还会报
        `attached to a different loop`（本仓库已踩过一次同类问题）。因此沿用本模块
        既有范式（见 load_user_smtp_settings）：没给 session 就自建一个并自行关闭。

        端点传 None → 自建 session → 能读到刚提交的注册记录，因此待审核计数天然包含
        这条新申请。按 user_id 重查而不是传 ORM 对象，避免 detached instance。
        """
        from starlette.concurrency import run_in_threadpool

        from app.models.user import User
        from app.services.auth_service import AuthService

        own_session = db is None
        session = db
        if own_session:
            from app.core.orm import AsyncSessionLocal

            session = AsyncSessionLocal()
        try:
            settings = await cls.load_global_smtp_settings()
            if settings is None:
                logger.info("待审核通知未发送：全局邮件服务未启用 user_id=%s", user_id)
                return False, "平台邮件服务未启用或配置不完整"

            recipients = await cls.resolve_admin_recipients(session)
            if not recipients:
                # 邮箱是选填字段，存量管理员很可能都没填 —— 必须留下明确线索，
                # 否则本功能会静默失效而无人察觉
                logger.warning(
                    "待审核通知未发送：没有任何填了邮箱的管理员账号 user_id=%s", user_id
                )
                return False, "没有填了邮箱的管理员账号"

            row = (
                await session.execute(
                    select(
                        User.user_name,
                        User.real_name,
                        User.email,
                        User.remark,
                        User.created_at,
                    ).where(User.id == user_id)
                )
            ).first()
            if row is None:
                logger.warning("待审核通知未发送：用户不存在 user_id=%s", user_id)
                return False, "用户不存在"

            # 与 /users/pending-count 共用同一口径，避免邮件里的数和页面角标各算各的
            pending_count = await AuthService.count_pending_registrations(db=session)
            subject, body = build_admin_review_notice(
                user_name=row.user_name,
                real_name=row.real_name or "",
                email=row.email or "",
                remark=row.remark,
                registered_at=row.created_at,
                pending_count=pending_count,
                sender_name=settings.sender_name,
            )
            # send_mail 是同步阻塞的（smtplib），丢线程池，避免卡住事件循环
            ok, message = await run_in_threadpool(
                cls.send_mail, settings, recipients, subject, body
            )
            if not ok:
                logger.warning(
                    "待审核通知发送失败 user_id=%s 收件人数=%s reason=%s",
                    user_id, len(recipients), message,
                )
            return ok, (None if ok else message)
        except Exception as exc:  # noqa: BLE001
            # 后台任务的异常不会被响应吃掉，但也无人接收，冒泡只会污染日志
            logger.warning("待审核通知发送异常 user_id=%s err=%s", user_id, exc)
            return False, str(exc)
        finally:
            if own_session and session is not None:
                await session.close()

    @classmethod
    async def resolve_smtp_settings(
        cls, user_id: Optional[int], scope: SmtpScope = "user", db: Optional[AsyncSession] = None
    ) -> Optional[SmtpSettings]:
        """scope='global' 时**强制**用全局配置，忽略用户个人设置。

        审核结果通知走 global：那是系统发给用户的，不能因为用户把个人 SMTP
        关掉或填错就发不出去。
        """
        if scope == "global" or user_id is None:
            return await cls.load_global_smtp_settings()
        settings, _ = await cls.load_user_smtp_settings(user_id, db=db)
        return settings

    # ---------------- 发送 ---------------- #

    @staticmethod
    def sanitize_error_text(text: str, password: str) -> str:
        """错误文本要回传给管理员，因此必须先抹掉密码。

        SMTP 的认证失败信息里可能带上尝试使用的口令，直接透传等于在界面上泄露凭据。
        """
        cleaned = str(text or "")
        if password:
            cleaned = cleaned.replace(password, EmailDeliveryService.MASK)
        return cleaned

    @classmethod
    def send_mail(
        cls,
        settings: SmtpSettings,
        to: List[str],
        subject: str,
        body: str,
        *,
        raise_on_transport_error: bool = False,
    ) -> Tuple[bool, str]:
        """同步发送。返回 (ok, message)。

        默认**不抛异常**：配置不完整、连不上、认证失败都通过返回值表达。调用方主要
        是「一次性把结果告知用户/管理员」的场景（测试发送按钮、审核结果通知），它们
        要把失败原因展示出来，而不是让请求变成 500。

        raise_on_transport_error=True 时，**传输层**异常原样抛出，只有业务性失败
        （配置不完整 / 无收件人 / 无发件人）仍走返回值。这是给需要重试的调用方用的：
        `notification_service._send_with_retries` 的契约是「抛异常 = 瞬时故障，退避重试；
        返回 (False, err) = 业务失败，立即返回」。若把连接超时也吞成 (False, err)，
        邮件渠道的重试就静默失效了（钉钉/企微路径仍抛异常，所以只有邮件会退化）。
        """
        if not settings.is_complete():
            return False, "邮件服务配置不完整（至少需要填写 SMTP 服务器地址）"
        if not to:
            return False, "未配置收件人"

        sender = settings.effective_from()
        if not sender:
            return False, "未配置发件人地址（发件人地址与 SMTP 登录账号都为空）"

        # 未知的加密方式必须在这里拦住。下面的分支是「非 ssl 即明文连接」，
        # 若放过去，手改数据库或经 API 直传的脏值（如 'tls'）会**加密被静默跳过、
        # 口令以明文发出**——违反决策 12「绝不静默降级」。属配置错误而非瞬时故障，
        # 因此走返回值而不是抛异常。
        if settings.security not in cls.ALLOWED_SECURITY:
            return False, (
                f"不支持的加密方式：{settings.security}"
                f"（仅允许 {'/'.join(cls.ALLOWED_SECURITY)}）"
            )

        message = MIMEText(body, "plain", "utf-8")
        message["Subject"] = Header(subject, "utf-8")
        message["From"] = formataddr((str(Header(settings.sender_name, "utf-8")), sender))
        message["To"] = ", ".join(to)

        try:
            if settings.security == "ssl":
                server = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout)
            else:
                server = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)

            with server:
                server.ehlo()
                if settings.security == "starttls":
                    # 绝不静默降级：既有实现把 starttls 失败吞掉，导致 587 端口可能
                    # 明文发送凭据。这里失败就报错，让管理员看到真实原因。
                    server.starttls()
                    server.ehlo()
                if settings.user:
                    server.login(settings.user, settings.password)
                server.sendmail(sender, to, message.as_string())
            return True, "发送成功"
        except Exception as exc:
            sanitized = cls.sanitize_error_text(f"{type(exc).__name__}: {exc}", settings.password)
            if raise_on_transport_error:
                # 抛出自定义类型而不是裸抛原异常：异常文本会一路冒到日志与
                # _send_with_retries 的 last_err 里，必须先抹掉口令。
                raise SmtpDeliveryError(sanitized) from None
            return False, sanitized


# ---------------- 审核结果文案 ---------------- #

def build_review_mail(user_name: str, approved: bool, sender_name: str) -> Tuple[str, str]:
    """审核结果邮件文案。

    刻意的取舍：
    - 不写登录地址：平台没有「站点基础地址」配置项，硬编码会写死部署形态。
    - **入参不含任何凭据**。注册密码是用户自己设的，平台无法也不该声称能告知；
      把签名限制成「账号名 + 审核结果 + 发件人名称」，就从来源上保证了这封邮件
      不可能泄露口令——比在正文里回避某个词可靠得多（后者会连
      「如忘记密码请联系管理员」这种正当提示一起禁掉）。
    """
    if approved:
        subject = f"【{sender_name}】您的账号已启用"
        body = (
            f"您好，{user_name}：\n\n"
            f"您的账号申请已通过审核，现在可以登录平台了。\n\n"
            f"请使用注册时设置的账号名与密码登录。如忘记密码，请联系管理员重置。\n\n"
            f"（本邮件由系统自动发送，请勿直接回复）\n"
        )
    else:
        subject = f"【{sender_name}】您的账号申请未通过"
        body = (
            f"您好，{user_name}：\n\n"
            f"很抱歉，您的账号申请未通过审核，当前无法登录平台。\n\n"
            f"如有疑问，请联系平台管理员了解具体原因。\n\n"
            f"（本邮件由系统自动发送，请勿直接回复）\n"
        )
    return subject, body


def build_admin_review_notice(
    *,
    user_name: str,
    real_name: str,
    email: str,
    remark: Optional[str],
    registered_at: Optional[datetime],
    pending_count: int,
    sender_name: str,
) -> Tuple[str, str]:
    """待审核通知邮件文案（发给管理员）。返回 (主题, 正文)。

    刻意的取舍（与 build_review_mail 同口径）：

    - **不放链接**：待审核页无法用 URL 直达 —— 前端 Users.vue 的 activeView 是纯本地
      ref、不接受 URL 参数，/users?view=review 会落在默认列表页；放个落到列表页的
      链接反而误导。用户已明确否决「改前端加 query 支持」，故只给文字指引。
      附带好处：本函数不依赖 download_url_prefix，也就不存在「前缀没配就发不出去」。
    - **入参不含任何凭据**：签名只接受展示用字段，从来源上保证这封邮件不可能泄露
      口令或 API Key —— 比在正文里逐个回避敏感词可靠（后者会连正当提示一起禁掉）。
    """
    subject = f"【{sender_name}】有待审核的账号申请"

    lines = [
        "您好，管理员：",
        "",
        "平台有一条新的账号注册申请，需要审核：",
        "",
        f"账号名：{user_name}",
        f"用户姓名：{real_name}",
        # 邮箱是选填字段：留空行会让管理员分不清「注册者没填」和「系统取值失败」
        f"邮箱：{email.strip() or '未填写'}",
    ]

    cleaned_remark = (remark or "").strip()
    if cleaned_remark:
        # 备注同为选填：为空时整行不渲染，避免出现空的「备注：」。
        # 上限 255 与 User.remark 列宽、RegisterRequest 的约定一致。
        lines.append(f"备注：{cleaned_remark[:255]}")

    lines += [
        f"申请时间：{format_platform_datetime(registered_at)}",
        f"当前待审核：{pending_count} 条",
        "",
        "请到「用户管理 → 待审核」处理。",
        "",
        "（本邮件由系统自动发送，请勿直接回复）",
    ]
    return subject, "\n".join(lines) + "\n"


def dedupe_recipient_emails(rows: Any) -> List[str]:
    """邮箱收件人去重：大小写不敏感，丢弃 None/空串/纯空白。

    为什么不交给数据库唯一索引（实测结论，见规范 §5）：MySQL 的唯一索引落在 _ci
    collation 上确实大小写不敏感，但 **PostgreSQL 的唯一索引是大小写敏感的**，且手工
    SQL 或第三方同步可以绕过应用层的 normalize_email 归一化 —— 那种数据在 PG 上可以
    并存。这与 AuthService.is_user_email_taken 坚持显式 lower() 比对完全同构
    （auth_service.py:631：「写入时已归一化，但手工 SQL 或第三方同步可能绕过应用层，
    且两个库的 collation 行为不同」）。

    保留首次出现的原始写法：大小写不影响投递，但保留原样便于与库中数据对账。
    """
    seen = set()
    recipients: List[str] = []
    for raw in rows or ():
        addr = (raw or "").strip()
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(addr)
    return recipients

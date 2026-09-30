import json
import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user_notification_config import UserNotificationConfig
from app.services.platform_timezone import platform_now

logger = logging.getLogger(__name__)

# 发送重试：仅对网络/传输类异常重试；渠道返回的业务错误码（如 webhook 配置错误）不重试
_SEND_RETRY_DELAYS_SEC = (2.0, 5.0)
_TRUNCATED_NOTE = "\n\n…（内容超出渠道长度限制，已截断）"
# 企业微信 markdown 上限 4096 字节；钉钉 markdown 上限 20000 字节；飞书富文本/卡片建议 20000 字节，留出余量
_WECHAT_WORK_MAX_BYTES = 4000
_DINGTALK_MAX_BYTES = 18000
_FEISHU_MAX_BYTES = 20000

# 连通性测试文案：单一来源，四个渠道共用，避免各处硬编码导致措辞漂移。
TEST_MESSAGE_TITLE = "消息通知连通性测试"
TEST_MESSAGE_BODY_TEMPLATE = "您的 AI 智能体平台个人中心{channel}通知渠道已配置成功，测试消息发送正常。"
TEST_CHANNEL_LABELS = {
    "dingtalk": "钉钉",
    "wechat_work": "企业微信",
    "feishu": "飞书",
    "email": "邮件",
}


def _test_actor_display(actor: Optional[Dict[str, Any]]) -> str:
    """触发人展示名：姓名（登录名）。二者相同或缺失时退化为单一标识。"""
    if not actor:
        return ""
    real_name = str(actor.get("real_name") or "").strip()
    user_name = str(actor.get("user_name") or "").strip()
    if real_name and user_name and real_name != user_name:
        return f"{real_name}（{user_name}）"
    return real_name or user_name


def _test_message_fields(channel_type: str, actor: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """测试消息的标识字段：谁在什么时候、通过哪个渠道触发了这次测试。"""
    fields: List[Tuple[str, str]] = []
    display = _test_actor_display(actor)
    if display:
        fields.append(("触发用户", display))
    fields.append(("触发时间", platform_now().strftime("%Y-%m-%d %H:%M:%S")))
    fields.append(("通知渠道", TEST_CHANNEL_LABELS.get(channel_type, channel_type)))
    return fields


def build_test_message(channel_type: str, actor: Optional[Dict[str, Any]] = None) -> str:
    """Markdown 版测试消息（钉钉 / 企业微信 / 飞书）。"""
    lines = [f"### {TEST_MESSAGE_TITLE}", ""]
    lines.extend(f"**{label}**：{value}" for label, value in _test_message_fields(channel_type, actor))
    lines.extend(["", TEST_MESSAGE_BODY_TEMPLATE.format(channel=TEST_CHANNEL_LABELS.get(channel_type, channel_type))])
    return "\n".join(lines)


def build_test_message_plain(channel_type: str, actor: Optional[Dict[str, Any]] = None) -> str:
    """纯文本版测试消息（邮件正文，不使用 Markdown 记号）。"""
    lines = [TEST_MESSAGE_TITLE, ""]
    lines.extend(f"{label}：{value}" for label, value in _test_message_fields(channel_type, actor))
    lines.extend(["", TEST_MESSAGE_BODY_TEMPLATE.format(channel=TEST_CHANNEL_LABELS.get(channel_type, channel_type))])
    return "\n".join(lines)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断，避免超过渠道消息体上限被整条拒收。"""
    raw = str(text or "")
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw
    note_bytes = _TRUNCATED_NOTE.encode("utf-8")
    budget = max(0, max_bytes - len(note_bytes))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATED_NOTE


async def _send_with_retries(send_once, *, channel: str) -> Tuple[bool, str]:
    """send_once() 返回 (ok, err)。抛异常视为传输失败并按退避重试；业务失败立即返回。"""
    last_err = ""
    for attempt in range(len(_SEND_RETRY_DELAYS_SEC) + 1):
        try:
            return await send_once()
        except Exception as exc:
            last_err = str(exc)
            if attempt < len(_SEND_RETRY_DELAYS_SEC):
                delay = _SEND_RETRY_DELAYS_SEC[attempt]
                logger.warning(
                    "Notification send failed via %s (attempt %s), retrying in %ss: %s",
                    channel, attempt + 1, delay, last_err,
                )
                await asyncio.sleep(delay)
    return False, last_err


class NotificationService:
    DEFAULT_CONFIGS = {
        "dingtalk": {
            "is_enabled": False,
            "webhook_url": "",
            "secret": ""
        },
        "wechat_work": {
            "is_enabled": False,
            "webhook_url": ""
        },
        "feishu": {
            "is_enabled": False,
            "webhook_url": "",
            "secret": ""
        },
        "email": {
            "is_enabled": False,
            "smtp_source": "global",   # global=用平台统一配置；custom=用下面这套自定义 SMTP
            "include_self": True,      # 是否同时发送到用户自己的邮箱（存标志而非邮箱快照，
                                       # 这样用户改邮箱后收件人自动跟随）
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "sender_name": "AI Agent",
            "recipients": ""
        }
    }

    MASKED_KEYS = ["secret", "smtp_password"]

    @classmethod
    async def get_config_by_type_raw(
        cls, db: AsyncSession, user_id: int, channel_type: str
    ) -> Optional[UserNotificationConfig]:
        """Fetch raw notification config from DB by type"""
        stmt = select(UserNotificationConfig).where(
            UserNotificationConfig.user_id == user_id,
            UserNotificationConfig.channel_type == channel_type
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def get_user_configs(cls, db: AsyncSession, user_id: int) -> Dict[str, Any]:
        """Get all notification configs for a user with masking applied"""
        configs = {}
        for channel, default_val in cls.DEFAULT_CONFIGS.items():
            db_record = await cls.get_config_by_type_raw(db, user_id, channel)
            if db_record and db_record.config_json:
                try:
                    data = json.loads(db_record.config_json)
                except Exception as e:
                    logger.error(f"Failed to parse config_json for user {user_id}, channel {channel}: {e}")
                    data = {}
                
                # Merge with default structure to prevent missing keys
                merged = {**default_val, **data}

                # 存量记录的 config_json 里没有 smtp_source（该键是本次新增的）。
                # 这里必须回填**解析后的真实来源**，不能让 DEFAULT_CONFIGS 里的
                # 'global' 直接盖上去：发信路径读的是原始 JSON，并按 smtp_host 是否
                # 有值自适应（resolve_smtp_source）。若界面显示「全局」而实际按
                # 「自定义」发信，用户看到的与运行时不一致；更糟的是他下一次保存
                # 会把 'global' 落库，从此静默改用（默认未启用的）全局服务，邮件
                # 通知无声失效。回填后界面即真实来源，保存落库的值也是显式正确的。
                if "smtp_source" not in data and default_val.get("smtp_source"):
                    from app.services.email_delivery_service import EmailDeliveryService

                    merged["smtp_source"] = EmailDeliveryService.resolve_smtp_source(data)

                # Apply mask to sensitive fields
                for k in cls.MASKED_KEYS:
                    if merged.get(k):
                        merged[k] = "******"
                configs[channel] = merged
            else:
                configs[channel] = {**default_val}
        return configs

    @classmethod
    async def save_user_config(
        cls, db: AsyncSession, user_id: int, channel_type: str, config_data: Dict[str, Any]
    ) -> UserNotificationConfig:
        """Save config for a specific channel, resolving masks back to real values"""
        if channel_type not in cls.DEFAULT_CONFIGS:
            raise ValueError(f"Unsupported channel type: {channel_type}")

        # Resolve masked values
        resolved_config = await cls.resolve_masked_config(db, user_id, channel_type, config_data)
        
        # Ensure type conversions (e.g., port to int for email)
        if channel_type == "email" and "smtp_port" in resolved_config:
            try:
                resolved_config["smtp_port"] = int(resolved_config["smtp_port"])
            except:
                resolved_config["smtp_port"] = 465

        db_record = await cls.get_config_by_type_raw(db, user_id, channel_type)
        if db_record:
            db_record.config_json = json.dumps(resolved_config, ensure_ascii=False)
        else:
            db_record = UserNotificationConfig(
                user_id=user_id,
                channel_type=channel_type,
                config_json=json.dumps(resolved_config, ensure_ascii=False)
            )
            db.add(db_record)
            
        await db.commit()
        await db.refresh(db_record)
        return db_record

    @classmethod
    async def resolve_masked_config(
        cls, db: AsyncSession, user_id: int, channel_type: str, config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve masked '******' values back to their actual stored values"""
        db_record = await cls.get_config_by_type_raw(db, user_id, channel_type)
        existing_data = {}
        if db_record and db_record.config_json:
            try:
                existing_data = json.loads(db_record.config_json)
            except Exception as e:
                logger.warning(f"Failed to parse existing config json: {e}")
                
        resolved = {**config_data}
        for k in cls.MASKED_KEYS:
            if resolved.get(k) == "******":
                resolved[k] = existing_data.get(k, "")
        return resolved

    @classmethod
    async def test_connection(
        cls,
        channel_type: str,
        config_data: Dict[str, Any],
        actor: Optional[Dict[str, Any]] = None,
        *,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> Tuple[bool, str]:
        """Test notification channel connectivity using actual config data.

        actor 为触发人信息（user_name / real_name），仅用于让测试消息带上可识别的用户标识。
        user_id / db 只有邮件渠道需要：邮件要按 config_data 里的 smtp_source 决定用平台
        全局配置还是表单里的自定义 SMTP，并按用户真实邮箱推导收件人。
        """
        if channel_type == "dingtalk":
            return await cls._test_dingtalk(config_data, actor)
        elif channel_type == "wechat_work":
            return await cls._test_wechat_work(config_data, actor)
        elif channel_type == "feishu":
            return await cls._test_feishu(config_data, actor)
        elif channel_type == "email":
            return await cls._test_email(config_data, actor, user_id=user_id, db=db)
        return False, f"Unsupported channel type: {channel_type}"

    @classmethod
    async def _test_dingtalk(cls, config: Dict[str, Any], actor: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        secret = config.get("secret")
        if not webhook_url:
            return False, "Webhook 地址不能为空"
        
        try:
            target_url = webhook_url
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
                target_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": TEST_MESSAGE_TITLE,
                    "text": build_test_message("dingtalk", actor)
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(target_url, json=payload)
                resp_data = response.json()
                if resp_data.get("errcode") == 0:
                    return True, ""
                else:
                    return False, f"{resp_data.get('errmsg')} (Code: {resp_data.get('errcode')})"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def _test_wechat_work(cls, config: Dict[str, Any], actor: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            return False, "Webhook 地址不能为空"
            
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": build_test_message("wechat_work", actor)
                }
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload)
                resp_data = response.json()
                if resp_data.get("errcode") == 0:
                    return True, ""
                else:
                    return False, f"{resp_data.get('errmsg')} (Code: {resp_data.get('errcode')})"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def _test_feishu(cls, config: Dict[str, Any], actor: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        secret = config.get("secret")
        if not webhook_url:
            return False, "Webhook 地址不能为空"

        try:
            timestamp = str(int(time.time()))
            payload: Dict[str, Any] = {
                "msg_type": "interactive",
                "card": {
                    "schema": "2.0",
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": TEST_MESSAGE_TITLE
                        },
                        "template": "blue"
                    },
                    "body": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": build_test_message("feishu", actor)
                            }
                        ]
                    }
                }
            }
            if secret:
                string_to_sign = f"{timestamp}\n{secret}"
                hmac_code = hmac.new(
                    string_to_sign.encode("utf-8"),
                    digestmod=hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode("utf-8")
                payload["timestamp"] = timestamp
                payload["sign"] = sign

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload)
                resp_data = response.json()
                code = resp_data.get("code") if "code" in resp_data else resp_data.get("StatusCode")
                if code == 0:
                    return True, ""
                errmsg = resp_data.get("msg") or resp_data.get("StatusMessage") or str(resp_data)
                return False, f"{errmsg} (Code: {code})"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def _test_email(
        cls,
        config: Dict[str, Any],
        actor: Optional[Dict[str, Any]] = None,
        *,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> Tuple[bool, str]:
        """邮件渠道连通性测试。

        这里**必须**与 send_email 走同一套分层与同一个发送实现。旧实现自成一套、
        直接读表单里的 host/账号/密码，导致选「全局」（前端不渲染这三个输入框、提交
        上来是空串）时必然报「SMTP 服务地址、账号和授权码不能为空」，把「管理员没开
        全局服务」的责任推给用户去填界面根本没显示的字段；它还自带一份 starttls
        `except: pass` 静默降级，会让 587 端口可能明文发送凭据。
        """
        from app.services.email_delivery_service import EmailDeliveryService
        from app.models.user import User

        config = config or {}
        source = EmailDeliveryService.resolve_smtp_source(config)

        if source == "global":
            settings = await EmailDeliveryService.load_global_smtp_settings()
            if settings is None:
                return False, (
                    "平台邮件服务未启用或配置不完整，请联系管理员在「系统配置 › 参数配置 › "
                    "邮件服务」中配置；也可在本页改用「自定义 SMTP」"
                )
        else:
            try:
                settings = EmailDeliveryService.build_custom_settings(config)
            except ValueError as exc:
                return False, str(exc)
            if not settings.is_complete():
                # host 是唯一必填项；账号与授权码要么都填、要么都不填（内网中继免认证）
                return False, "SMTP 服务地址不能为空；账号与授权码要么都填、要么都不填"

        # 收件人：默认抄送自己的真实邮箱，其次才是配置里的其它收件人。
        # 全局模式下没有「自己的账号」可当收件人，所以这里必须查库取用户邮箱。
        to: List[str] = []
        if user_id is not None and db is not None and config.get("include_self", True):
            self_email = (
                await db.execute(select(User.email).where(User.id == user_id))
            ).scalar()
            to = EmailDeliveryService.compose_recipients(
                self_email=self_email,
                include_self=True,
                extra=config.get("recipients"),
            )
        if not to:
            to = EmailDeliveryService.parse_recipients(config.get("recipients"))
        if not to and source == "custom":
            # 与 send_email 同一规则：仅自定义 SMTP 保留「回退发给发件账号」的既有行为。
            # 全局模式下不回退——发给平台自己属于静默的错误投递，用户会看到「测试通过」
            # 却实际收不到任何通知，所以宁可明确报错让他去填邮箱。
            fallback = settings.effective_from()
            to = [fallback] if fallback else []
        if not to:
            return False, (
                "没有收件人：请先在「账号基本信息」中填写邮箱，或在此配置其他收件人"
            )

        subject = "AI 智能体平台 - 邮件通知连通性测试"
        body = build_test_message_plain("email", actor)
        # send_mail 是同步阻塞的，丢线程池，避免一次 SMTP 超时卡住事件循环
        ok, message = await asyncio.to_thread(
            EmailDeliveryService.send_mail, settings, to, subject, body
        )
        return (True, "") if ok else (False, message)

    # Core message sending APIs for user configured channels
    @classmethod
    async def send_dingtalk(cls, db: AsyncSession, user_id: int, title: str, content: str) -> Tuple[bool, str]:
        """Send message using user's configured DingTalk channel"""
        db_record = await cls.get_config_by_type_raw(db, user_id, "dingtalk")
        if not db_record or not db_record.config_json:
            return False, "用户未配置钉钉通知"
            
        data = json.loads(db_record.config_json)
        if not data.get("is_enabled"):
            return False, "用户未启用钉钉通知"
            
        return await cls._send_dingtalk_msg_real(data, title, content)

    @classmethod
    async def _send_dingtalk_msg_real(cls, config: Dict[str, Any], title: str, content: str) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        secret = config.get("secret")
        body = _truncate_utf8(f"### {title}\n\n{content}", _DINGTALK_MAX_BYTES)

        async def send_once() -> Tuple[bool, str]:
            target_url = webhook_url
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
                target_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": body
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(target_url, json=payload)
                resp_data = response.json()
                if resp_data.get("errcode") == 0:
                    return True, ""
                return False, f"{resp_data.get('errmsg')} (Code: {resp_data.get('errcode')})"

        return await _send_with_retries(send_once, channel="dingtalk")

    @classmethod
    async def send_wechat_work(cls, db: AsyncSession, user_id: int, title: str, content: str) -> Tuple[bool, str]:
        record = await cls.get_config_by_type_raw(db, user_id, "wechat_work")
        if not record or not record.config_json:
            return False, "用户未配置企业微信通知"
        config = json.loads(record.config_json)
        if not config.get("is_enabled") or not config.get("webhook_url"):
            return False, "用户未启用企业微信通知"

        body = _truncate_utf8(f"### {title}\n\n{content}", _WECHAT_WORK_MAX_BYTES)

        async def send_once() -> Tuple[bool, str]:
            payload = {"msgtype": "markdown", "markdown": {"content": body}}
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(config["webhook_url"], json=payload)
                data = response.json()
            return (True, "") if data.get("errcode") == 0 else (False, str(data.get("errmsg") or data))

        return await _send_with_retries(send_once, channel="wechat_work")

    @classmethod
    async def send_feishu(cls, db: AsyncSession, user_id: int, title: str, content: str) -> Tuple[bool, str]:
        """Send message using user's configured Feishu channel"""
        db_record = await cls.get_config_by_type_raw(db, user_id, "feishu")
        if not db_record or not db_record.config_json:
            return False, "用户未配置飞书通知"

        data = json.loads(db_record.config_json)
        if not data.get("is_enabled"):
            return False, "用户未启用飞书通知"

        return await cls._send_feishu_msg_real(data, title, content)

    @classmethod
    async def _send_feishu_msg_real(cls, config: Dict[str, Any], title: str, content: str) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        secret = config.get("secret")
        if not webhook_url:
            return False, "用户未配置飞书 Webhook 地址"

        body = _truncate_utf8(content, _FEISHU_MAX_BYTES)

        async def send_once() -> Tuple[bool, str]:
            timestamp = str(int(time.time()))
            payload: Dict[str, Any] = {
                "msg_type": "interactive",
                "card": {
                    "schema": "2.0",
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": title or "任务结果通知"
                        },
                        "template": "blue"
                    },
                    "body": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": body
                            }
                        ]
                    }
                }
            }
            if secret:
                string_to_sign = f"{timestamp}\n{secret}"
                hmac_code = hmac.new(
                    string_to_sign.encode("utf-8"),
                    digestmod=hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode("utf-8")
                payload["timestamp"] = timestamp
                payload["sign"] = sign

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload)
                resp_data = response.json()
                code = resp_data.get("code") if "code" in resp_data else resp_data.get("StatusCode")
                if code == 0:
                    return True, ""
                errmsg = resp_data.get("msg") or resp_data.get("StatusMessage") or str(resp_data)
                return False, f"{errmsg} (Code: {code})"

        return await _send_with_retries(send_once, channel="feishu")

    @staticmethod
    def parse_email_recipients(raw: Any) -> List[str]:
        """解析收件人配置：支持逗号/分号/空白分隔的多个地址。"""
        text = str(raw or "").replace("；", ";").replace("，", ",")
        parts = [p.strip() for chunk in text.split(";") for p in chunk.split(",")]
        return [p for p in parts if p and "@" in p]

    @classmethod
    async def send_email(cls, db: AsyncSession, user_id: int, title: str, content: str) -> Tuple[bool, str]:
        """按用户选择的来源（全局/自定义）发信，收件人默认包含用户自己的邮箱。"""
        from app.models.user import User
        from app.services.email_delivery_service import EmailDeliveryService

        record = await cls.get_config_by_type_raw(db, user_id, "email")
        config: Dict[str, Any] = {}
        if record and record.config_json:
            try:
                config = json.loads(record.config_json)
            except Exception:
                config = {}

        if not config.get("is_enabled"):
            return False, "用户未启用邮件通知"

        settings = await EmailDeliveryService.resolve_smtp_settings(user_id, scope="user", db=db)
        if settings is None:
            return False, "邮件服务未配置（当前来源无可用的 SMTP 设置）"

        # 用户自己的邮箱每次都现读：include_self 存的是标志而非邮箱快照，
        # 用户改邮箱后收件人必须自动跟随。
        self_email = (
            await db.execute(select(User.email).where(User.id == user_id))
        ).scalar()

        include_self = config.get("include_self", True)   # 缺失默认 True，新老用户一致
        recipients = EmailDeliveryService.compose_recipients(
            self_email=self_email, include_self=include_self, extra=config.get("recipients")
        )
        if not recipients:
            # 兼容既有行为：自定义 SMTP 下 recipients 为空时原先回退发给 smtp_user。
            # 全局模式下不回退——发给发件账号自己是静默的错误投递。
            if EmailDeliveryService.resolve_smtp_source(config) == "custom":
                fallback = settings.effective_from()
                recipients = [fallback] if fallback else []
        if not recipients:
            return False, "未配置收件人（未填写邮箱，且未设置其他收件人）"

        def send_sync() -> Tuple[bool, str]:
            # raise_on_transport_error=True：连接失败/超时这类瞬时故障要抛出去，
            # 好让 _send_with_retries 退避重试；配置不完整、无收件人这类业务失败
            # 仍走返回值立即返回，不浪费重试。
            return EmailDeliveryService.send_mail(
                settings, recipients, title, content, raise_on_transport_error=True
            )

        async def send_once() -> Tuple[bool, str]:
            return await asyncio.to_thread(send_sync)

        return await _send_with_retries(send_once, channel="email")

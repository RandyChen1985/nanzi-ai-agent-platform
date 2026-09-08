import json
import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user_notification_config import UserNotificationConfig

logger = logging.getLogger(__name__)

# 发送重试：仅对网络/传输类异常重试；渠道返回的业务错误码（如 webhook 配置错误）不重试
_SEND_RETRY_DELAYS_SEC = (2.0, 5.0)
_TRUNCATED_NOTE = "\n\n…（内容超出渠道长度限制，已截断）"
# 企业微信 markdown 上限 4096 字节；钉钉 markdown 上限 20000 字节；飞书富文本/卡片建议 20000 字节，留出余量
_WECHAT_WORK_MAX_BYTES = 4000
_DINGTALK_MAX_BYTES = 18000
_FEISHU_MAX_BYTES = 20000


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
    async def test_connection(cls, channel_type: str, config_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Test notification channel connectivity using actual config data"""
        if channel_type == "dingtalk":
            return await cls._test_dingtalk(config_data)
        elif channel_type == "wechat_work":
            return await cls._test_wechat_work(config_data)
        elif channel_type == "feishu":
            return await cls._test_feishu(config_data)
        elif channel_type == "email":
            return await cls._test_email(config_data)
        return False, f"Unsupported channel type: {channel_type}"

    @classmethod
    async def _test_dingtalk(cls, config: Dict[str, Any]) -> Tuple[bool, str]:
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
                    "title": "消息通知连通性测试",
                    "text": "### 消息通知连通性测试\n\n您的AI 智能体平台个人中心钉钉通知渠道已配置成功，测试消息发送正常。"
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
    async def _test_wechat_work(cls, config: Dict[str, Any]) -> Tuple[bool, str]:
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            return False, "Webhook 地址不能为空"
            
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": "### 消息通知连通性测试\n\n您的AI 智能体平台个人中心企业微信通知渠道已配置成功，测试消息发送正常。"
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
    async def _test_feishu(cls, config: Dict[str, Any]) -> Tuple[bool, str]:
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
                            "content": "消息通知连通性测试"
                        },
                        "template": "blue"
                    },
                    "body": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "您的 AI 智能体平台个人中心飞书通知渠道已配置成功，测试消息发送正常。"
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
    async def _test_email(cls, config: Dict[str, Any]) -> Tuple[bool, str]:
        smtp_host = config.get("smtp_host")
        smtp_port = config.get("smtp_port") or 465
        smtp_user = config.get("smtp_user")
        smtp_password = config.get("smtp_password")
        sender_name = config.get("sender_name") or "AI Agent"
        
        if not smtp_host or not smtp_user or not smtp_password:
            return False, "SMTP 服务地址、账号和授权码不能为空"
            
        try:
            smtp_port = int(smtp_port)
        except:
            return False, "SMTP 端口格式错误"

        def send_sync():
            try:
                msg = MIMEMultipart()
                msg['From'] = formataddr((sender_name, smtp_user))
                msg['To'] = smtp_user
                msg['Subject'] = "AI 智能体平台 - 邮件通知连通性测试"
                
                content = "这是一封来自AI 智能体平台的测试邮件，表明您的邮件通知通道配置已测试成功。"
                msg.attach(MIMEText(content, 'plain', 'utf-8'))

                if smtp_port == 465:
                    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10.0)
                else:
                    server = smtplib.SMTP(smtp_host, smtp_port, timeout=10.0)
                    try:
                        server.starttls()
                    except:
                        pass

                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [smtp_user], msg.as_string())
                server.quit()
                return True, ""
            except Exception as e:
                logger.error(f"Test SMTP Connection Error: {e}", exc_info=True)
                return False, str(e)

        try:
            return await asyncio.to_thread(send_sync)
        except Exception as e:
            return False, str(e)

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
        record = await cls.get_config_by_type_raw(db, user_id, "email")
        if not record or not record.config_json:
            return False, "用户未配置邮件通知"
        config = json.loads(record.config_json)
        if not config.get("is_enabled"):
            return False, "用户未启用邮件通知"

        def send_sync():
            host, port = config.get("smtp_host"), int(config.get("smtp_port") or 465)
            username, password = config.get("smtp_user"), config.get("smtp_password")
            if not host or not username or not password:
                raise ValueError("SMTP 配置不完整")
            # 收件人可配置；未配置时回退给 SMTP 账号自身
            recipients = cls.parse_email_recipients(config.get("recipients")) or [username]
            message = MIMEMultipart()
            message["From"] = formataddr((config.get("sender_name") or "AI Agent", username))
            message["To"] = ", ".join(recipients)
            message["Subject"] = title
            message.attach(MIMEText(content, "plain", "utf-8"))
            server = smtplib.SMTP_SSL(host, port, timeout=10.0) if port == 465 else smtplib.SMTP(host, port, timeout=10.0)
            if port != 465:
                server.starttls()
            server.login(username, password)
            server.sendmail(username, recipients, message.as_string())
            server.quit()

        async def send_once() -> Tuple[bool, str]:
            await asyncio.to_thread(send_sync)
            return True, ""

        return await _send_with_retries(send_once, channel="email")

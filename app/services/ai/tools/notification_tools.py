import logging
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
from typing import Any, Dict, Optional, Type
from app.services.ai.tools.tool_compat import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class DingTalkInput(BaseModel):
    title: str = Field(description="The title of the message (visible in notifications)")
    content: str = Field(description="The main body of the message in Markdown format")

class send_dingtalk_message(BaseTool):
    # Pydantic v2 requires type annotations for field overrides
    name: str = "send_dingtalk_message"
    description: str = (
        "发送钉钉群机器人 Markdown 消息。Send a Markdown message to DingTalk. "
        "本工具会自动读取当前用户在个人中心 -> 消息通知里的钉钉 Webhook/加签配置，"
        "无需用户在本轮对话中提供 webhook、access_token 或群聊目标。"
    )
    args_schema: Type[BaseModel] = DingTalkInput

    async def _arun(self, title: str, content: str) -> str:
        """Use the tool asynchronously."""
        webhook_url = None
        secret = None
        
        # Directly retrieve from user's personal notification config
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import NotificationService
        
        agent_ctx = get_current_agent_context()
        if agent_ctx and agent_ctx.user_id:
            try:
                async with AsyncSessionLocal() as db:
                    db_record = await NotificationService.get_config_by_type_raw(db, agent_ctx.user_id, "dingtalk")
                    if db_record and db_record.config_json:
                        user_cfg = json.loads(db_record.config_json)
                        if user_cfg.get("is_enabled"):
                            webhook_url = user_cfg.get("webhook_url")
                            secret = user_cfg.get("secret")
                        else:
                            return "Error: DingTalk notification is disabled. Please enable it in Personal Center -> Message Notifications."
            except Exception as e:
                logger.error(f"Failed to load user personal DingTalk config: {e}", exc_info=True)

        if not webhook_url:
            logger.warning(f"DingTalk Tool: Webhook URL missing in user personal settings.")
            return "Error: DingTalk Webhook URL not configured. Please go to Personal Center -> Message Notifications and set it."


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
                    "title": title,
                    "text": f"### {title}\n\n{content}"
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(target_url, json=payload)
                resp_data = response.json()
                
                if resp_data.get("errcode") == 0:
                    return f"Successfully sent DingTalk message: {title}"
                else:
                    return f"Failed to send DingTalk message: {resp_data.get('errmsg')} (Code: {resp_data.get('errcode')})"

        except Exception as e:
            logger.error(f"DingTalk Tool Error: {e}", exc_info=True)
            return f"Error executing DingTalk tool: {str(e)}"

    def _run(self, title: str, content: str) -> str:
        raise NotImplementedError("Use _arun instead")

class EmailInput(BaseModel):
    to_email: str = Field(
        default="",
        description=(
            "收件人邮箱，多个地址用逗号或分号分隔。"
            "**留空即发给用户在「个人中心 -> 消息通知」里配置的收件人**（默认包含用户本人邮箱）。"
            "用户没有明确给出收件人时一律留空，切勿自行猜测或编造地址。"
        ),
    )
    subject: str = Field(description="The subject line of the email")
    content: str = Field(
        description=(
            "邮件正文，**支持 Markdown**（标题、列表、表格、粗体、链接、代码块会被渲染成"
            "排版后的 HTML 邮件），直接用 Markdown 写即可；纯文本同样可以。"
            "**不要写 HTML 标签**——正文里的原始 HTML 会被转义成字面文本显示出来。"
        )
    )

class send_email(BaseTool):
    name: str = "send_email"
    description: str = (
        "发送邮件通知。Send an email via SMTP. "
        "本工具会自动读取当前用户在个人中心 -> 消息通知里的 SMTP 配置，"
        "无需用户在本轮对话中提供 SMTP 服务器或密码。"
        "收件人：用户明确说了就填 to_email；用户没说就把 to_email 留空，"
        "系统会发给用户配置好的收件人，**不要自己猜一个地址填进去**。"
        "正文支持 Markdown 排版（标题、列表、表格等会渲染成 HTML 邮件），不要写 HTML 标签。"
    )
    args_schema: Type[BaseModel] = EmailInput

    async def _arun(self, to_email: str = "", subject: str = "", content: str = "") -> str:
        """Use the tool asynchronously.

        发送统一走 EmailDeliveryService.send_mail：它显式处理 ssl/starttls/none，
        starttls 失败即报错。旧实现自己拼 SMTP 并在 starttls 失败时静默跳过，
        587 端口可能明文发送凭据——两处实现漂移正是要消除的问题。

        收件人解析与定时通知（NotificationService.send_email）**同源**：
        显式给了就用给的；没给就读用户配置（本人邮箱 + 其他收件人）。

        **任何分支都不许凭空造一个收件人**：配置为空就报错让用户去配。
        特别是**不镜像**定时任务那条「自定义 SMTP 且收件人为空时回退发给 smtp_user」
        的兼容逻辑——对定时通知那是兼容，对 agent 主动发信却等于「没说发给谁就发到
        发件账号」，属于错误投递。（那两处实现漂移也正是要避免的。）
        """
        import asyncio

        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.email_delivery_service import EmailDeliveryService
        from app.services.notification_service import NotificationService

        agent_ctx = get_current_agent_context()
        if not (agent_ctx and agent_ctx.user_id):
            return "Error: No user context available for email notification."

        try:
            async with AsyncSessionLocal() as db:
                db_record = await NotificationService.get_config_by_type_raw(
                    db, agent_ctx.user_id, "email"
                )
                user_cfg: Dict[str, Any] = {}
                if db_record and db_record.config_json:
                    try:
                        user_cfg = json.loads(db_record.config_json)
                    except Exception:
                        user_cfg = {}
                if not user_cfg.get("is_enabled"):
                    return "Error: Email notification is disabled. Please enable it in Personal Center -> Message Notifications."

                # 分层解析：用户选了 global 就用平台统一配置，选了 custom 才用个人 SMTP。
                # 现读一次，避免与 send_email 各维护一套设置装载逻辑。
                settings = await EmailDeliveryService.resolve_smtp_settings(
                    agent_ctx.user_id, scope="user", db=db
                )

                # 显式给了收件人就照给（多个地址同一套解析规则，含全角分隔符归一）；
                # 给了但解析不出任何地址（模型写错/编错）直接报错，不拿到 SMTP 层去炸。
                explicit = EmailDeliveryService.parse_recipients(to_email)
                if str(to_email or "").strip() and not explicit:
                    return (
                        "Error: Invalid recipient address. Ask the user for a valid email "
                        "address, or leave to_email empty to use their configured recipients."
                    )

                if explicit:
                    recipients = explicit
                else:
                    # 没给就读用户配置。resolve_self_email 现查库而不用 Redis 里那份
                    # 缓存的 user dict，否则用户改完邮箱仍会发往旧地址。
                    self_email = await EmailDeliveryService.resolve_self_email(
                        agent_ctx.user_id, db
                    )
                    recipients = EmailDeliveryService.compose_recipients(
                        self_email=self_email,
                        include_self=user_cfg.get("include_self", True),
                        extra=user_cfg.get("recipients"),
                    )

                if not recipients:
                    return (
                        "Error: No recipient configured. Ask the user to set an email address "
                        "in Personal Center -> Message Notifications."
                    )
        except Exception as e:
            logger.error(f"Failed to load user personal email config: {e}", exc_info=True)
            return f"Error: Failed to load email configuration: {e}"

        if settings is None:
            return "Error: SMTP configuration is missing or incomplete. Please configure it in Personal Center -> Message Notifications."

        try:
            # send_mail 是同步阻塞的，放进线程池，别卡住事件循环。
            # render_markdown=True：这里的正文由模型生成，天然带 Markdown 排版
            # （标题/列表/表格），纯文本发出去用户看到的是满屏 ** 和 |。
            ok, message = await asyncio.to_thread(
                EmailDeliveryService.send_mail,
                settings,
                recipients,
                subject,
                content,
                render_markdown=True,
            )
        except Exception as e:
            logger.error(f"SMTP Error: {e}", exc_info=True)
            return f"Error sending email: {e}"

        if ok:
            return f"Successfully sent email to {', '.join(recipients)}"
        return f"Error sending email: {message}"

    def _run(self, to_email: str, subject: str, content: str) -> str:
        raise NotImplementedError("Use _arun instead")

class WeChatWorkInput(BaseModel):
    content: str = Field(description="The main body of the message in Markdown format")

class send_wechat_work_message(BaseTool):
    name: str = "send_wechat_work_message"
    description: str = (
        "发送企业微信群机器人 Markdown 消息。Send a Markdown message to WeChat Work. "
        "本工具会自动读取当前用户在个人中心 -> 消息通知里的企微 Webhook 配置，"
        "无需用户在本轮对话中提供 webhook 或群聊目标。"
    )
    args_schema: Type[BaseModel] = WeChatWorkInput

    async def _arun(self, content: str) -> str:
        """Use the tool asynchronously."""
        webhook_url = None
        
        # Directly retrieve from user's personal notification config
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import NotificationService
        
        agent_ctx = get_current_agent_context()
        if agent_ctx and agent_ctx.user_id:
            try:
                async with AsyncSessionLocal() as db:
                    db_record = await NotificationService.get_config_by_type_raw(db, agent_ctx.user_id, "wechat_work")
                    if db_record and db_record.config_json:
                        user_cfg = json.loads(db_record.config_json)
                        if user_cfg.get("is_enabled"):
                            webhook_url = user_cfg.get("webhook_url")
                        else:
                            return "Error: WeChat Work notification is disabled. Please enable it in Personal Center -> Message Notifications."
            except Exception as e:
                logger.error(f"Failed to load user personal WeChat Work config: {e}", exc_info=True)

        if not webhook_url:
            logger.warning(f"WeChat Work Tool: Webhook URL missing in user personal settings.")
            return "Error: WeChat Work Webhook URL not configured. Please go to Personal Center -> Message Notifications and set it."

        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload)
                resp_data = response.json()
                
                if resp_data.get("errcode") == 0:
                    return f"Successfully sent WeChat Work message."
                else:
                    return f"Failed to send WeChat Work message: {resp_data.get('errmsg')} (Code: {resp_data.get('errcode')})"

        except Exception as e:
            logger.error(f"WeChat Work Tool Error: {e}", exc_info=True)
            return f"Error executing WeChat Work tool: {str(e)}"

    def _run(self, content: str) -> str:
        raise NotImplementedError("Use _arun instead")


class PortalNotificationInput(BaseModel):
    title: str = Field(description="站内消息标题（门户铃铛与消息中心可见）")
    content: str = Field(description="站内消息正文（支持纯文本或 Markdown）")
    level: str = Field(
        default="info",
        description="消息级别：info / success / warning / error，默认 info",
    )


class send_portal_notification(BaseTool):
    name: str = "send_portal_notification"
    description: str = (
        "发送站内消息到当前用户的门户消息中心（右上角铃铛 / Inbox）。"
        "Send an in-app portal notification to the current user. "
        "无需配置 Webhook；消息会出现在 PortalNotification 站内信箱。"
        "适合任务完成提醒、巡检结论摘要、需要用户回门户查看的结果通知。"
    )
    args_schema: Type[BaseModel] = PortalNotificationInput

    async def _arun(self, title: str, content: str, level: str = "info") -> str:
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.portal_notification_service import PortalNotificationService

        agent_ctx = get_current_agent_context()
        if not agent_ctx or not agent_ctx.user_id:
            return "Error: 无法确定当前用户，站内消息未发送。"

        try:
            user_id = int(agent_ctx.user_id)
        except (TypeError, ValueError):
            return "Error: 当前用户 ID 无效，站内消息未发送。"

        cleaned_title = str(title or "").strip()
        cleaned_content = str(content or "").strip()
        if not cleaned_title:
            return "Error: 标题不能为空。"
        if not cleaned_content:
            return "Error: 正文不能为空。"

        allowed_levels = {"info", "success", "warning", "error"}
        resolved_level = str(level or "info").strip().lower()
        if resolved_level not in allowed_levels:
            resolved_level = "info"

        try:
            async with AsyncSessionLocal() as db:
                row = await PortalNotificationService.create(
                    db,
                    user_id=user_id,
                    title=cleaned_title,
                    content=cleaned_content,
                    level=resolved_level,
                    category="agent",
                    resource_type="agent_message",
                    resource_id=str(getattr(agent_ctx, "conversation_id", "") or "")[:64] or None,
                    metadata={
                        "source": "send_portal_notification",
                        "agent_name": getattr(agent_ctx, "agent_name", None),
                        "conversation_id": getattr(agent_ctx, "conversation_id", None),
                    },
                )
                await db.commit()
            return (
                f"Successfully sent portal notification "
                f"(id={row.id}, level={resolved_level}): {cleaned_title}"
            )
        except Exception as e:
            logger.error("Portal notification tool error: %s", e, exc_info=True)
            return f"Error sending portal notification: {str(e)}"

    def _run(self, title: str, content: str, level: str = "info") -> str:
        raise NotImplementedError("Use _arun instead")


class FeishuInput(BaseModel):
    title: str = Field(description="The title of the message card (visible in notifications)")
    content: str = Field(description="The main body of the message in Markdown format")


class send_feishu_message(BaseTool):
    name: str = "send_feishu_message"
    description: str = (
        "发送飞书群机器人 Markdown 卡片消息。Send a Markdown message card to Feishu. "
        "本工具会自动读取当前用户在个人中心 -> 消息通知里的飞书 Webhook/加签配置，"
        "无需用户在本轮对话中提供 webhook 或群聊目标。"
    )
    args_schema: Type[BaseModel] = FeishuInput

    async def _arun(self, title: str, content: str) -> str:
        """Use the tool asynchronously."""
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import NotificationService

        agent_ctx = get_current_agent_context()
        if not agent_ctx or not agent_ctx.user_id:
            return "Error: 无法确定当前用户，飞书消息未发送。"

        try:
            user_id = int(agent_ctx.user_id)
        except (TypeError, ValueError):
            return "Error: 当前用户 ID 无效，飞书消息未发送。"

        cleaned_title = str(title or "").strip()
        cleaned_content = str(content or "").strip()
        if not cleaned_content:
            return "Error: 正文内容不能为空。"

        try:
            async with AsyncSessionLocal() as db:
                ok, err = await NotificationService.send_feishu(
                    db, user_id=user_id, title=cleaned_title or "智能体消息通知", content=cleaned_content
                )
                if ok:
                    return f"Successfully sent Feishu message: {cleaned_title or '智能体消息通知'}"
                return f"Failed to send Feishu message: {err}"
        except Exception as e:
            logger.error("Feishu Tool Error: %s", e, exc_info=True)
            return f"Error executing Feishu tool: {str(e)}"

    def _run(self, title: str, content: str) -> str:
        raise NotImplementedError("Use _arun instead")

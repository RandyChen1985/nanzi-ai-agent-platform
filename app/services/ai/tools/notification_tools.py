import logging
import json
import re
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
from typing import Any, Dict, List, Optional, Type, Union
from app.services.ai.tools.tool_compat import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _normalize_attachment_input(raw: Union[List[str], str, None]) -> List[str]:
    """把模型给的附件参数归一成路径列表。

    模型偶尔把数组写成单个字符串（甚至换行/分号分隔的一串），schema 再严格也拦不住
    它落到 `str` 分支。这里统一归一，而不是直接报参数错误让整封邮件失败。
    """
    if not raw:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in re.split(r"[\n;]", raw) if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


class DingTalkInput(BaseModel):
    title: str = Field(description="The title of the message (visible in notifications)")
    content: str = Field(description="The main body of the message in Markdown format")
    group_id: Optional[int] = Field(
        default=None,
        description=(
            "可选。要发到哪个钉钉群的群 ID（正整数）。"
            "用户明确说「发到 xx 群」时，用 list_notification_channels 查到对应群的 id 再填这里。"
            "留空则发到用户的默认钉钉群。**不要编造 id**。"
        ),
    )
    group_name: Optional[str] = Field(
        default=None,
        description=(
            "可选。要发到哪个钉钉群的名称，如「运维告警群」。"
            "已知群名但不确定 id 时可只填这个；与 group_id 同时填时以 group_id 为准。"
            "留空则发到默认群。"
        ),
    )

class send_dingtalk_message(BaseTool):
    # Pydantic v2 requires type annotations for field overrides
    name: str = "send_dingtalk_message"
    description: str = (
        "发送钉钉群机器人 Markdown 消息。Send a Markdown message to DingTalk. "
        "默认发到当前用户在个人中心 -> 消息通知里设置的**默认钉钉群**，"
        "无需用户提供 webhook 或 access_token。"
        "用户明确要求发到某个群时，用 group_id 或 group_name 指定；"
        "不确定用户有哪些群时，先调用 list_notification_channels 查一下。"
    )
    args_schema: Type[BaseModel] = DingTalkInput

    async def _arun(
        self,
        title: str,
        content: str,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
    ) -> str:
        """Use the tool asynchronously."""
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import NotificationService

        agent_ctx = get_current_agent_context()
        if not agent_ctx or not agent_ctx.user_id:
            return "Error: 无法确定当前用户，钉钉消息未发送。"

        try:
            async with AsyncSessionLocal() as db:
                ok, message, target = await NotificationService.dispatch_notification(
                    db,
                    int(agent_ctx.user_id),
                    "dingtalk",
                    title=title,
                    content=content,
                    channel_id=group_id,
                    channel_name=group_name,
                )
        except Exception as e:
            logger.error(f"DingTalk Tool Error: {e}", exc_info=True)
            return f"Error executing DingTalk tool: {str(e)}"

        if ok:
            return f"已发送到钉钉「{target.name or '未命名群'}」(id={target.id})"
        return f"钉钉消息未发送：{message}"

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
            "**不要用本参数代替附件**：需要发送文件时请用 attachments。"
        )
    )
    # schema 用 List[str]（对模型是清晰的数组），但 _arun 的入参仍接受 str：
    # 工具调用不经过 pydantic 校验，模型偶尔会把数组写成单个字符串，
    # 在 _arun 里归一比让整封邮件失败更合适。
    attachments: List[str] = Field(
        default_factory=list,
        description=(
            "附件文件路径列表（服务器绝对路径）。"
            "**只能用上下文里出现过的路径**：用户本轮上传附件时系统注入的绝对路径，"
            "或工作区里已生成的文件路径；**不要凭文件名猜路径，也不要编造路径**。"
            "用户没要求发文件就留空。"
            "上限 5 个、单个与合计均不超过 20MB；"
            "仅支持 docx/xlsx/pptx/pdf/csv/txt/md/json/png/jpg/jpeg/gif/webp/zip，"
            "其他类型会被拒绝。任一附件不合法则整封邮件不会发出。"
        ),
    )

class send_email(BaseTool):
    name: str = "send_email"
    description: str = (
        "发送邮件通知，可携带附件。Send an email via SMTP with optional attachments. "
        "本工具会自动读取当前用户在个人中心 -> 消息通知里的 SMTP 配置，"
        "无需用户在本轮对话中提供 SMTP 服务器或密码。"
        "收件人：用户明确说了就填 to_email；用户没说就把 to_email 留空，"
        "系统会发给用户配置好的收件人，**不要自己猜一个地址填进去**。"
        "附件：用户要求发文件时，把上下文里注入的附件绝对路径（或工作区文件路径）"
        "填进 attachments；路径必须真实存在且在当前会话/用户工作区允许的范围内，"
        "不要编造路径。"
        "正文支持 Markdown 排版（标题、列表、表格等会渲染成 HTML 邮件），不要写 HTML 标签。"
    )
    args_schema: Type[BaseModel] = EmailInput

    async def _arun(
        self,
        to_email: str = "",
        subject: str = "",
        content: str = "",
        attachments: Union[List[str], str] = "",
    ) -> str:
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

        # 附件：整批校验。任一不合法就整封不发——「看起来发了三个、实际只发了两个」
        # 的邮件比一句明确的报错更糟，用户根本无从察觉少了什么。
        from app.services.ai.tools.document_paths import (
            DocumentPathError,
            resolve_context_user_name,
            resolve_email_attachments,
        )

        try:
            resolved_attachments = await resolve_email_attachments(
                _normalize_attachment_input(attachments),
                allowed_attachment_paths=agent_ctx.authorized_attachment_paths,
                user_id=agent_ctx.user_id,
                conversation_id=agent_ctx.conversation_id,
                user_name=resolve_context_user_name(agent_ctx),
            )
        except DocumentPathError as exc:
            return f"Error: {exc}"

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
                attachments=resolved_attachments,
            )
        except Exception as e:
            logger.error(f"SMTP Error: {e}", exc_info=True)
            return f"Error sending email: {e}"

        if ok:
            suffix = ""
            if resolved_attachments:
                label = "attachment" if len(resolved_attachments) == 1 else "attachments"
                suffix = f" ({len(resolved_attachments)} {label})"
            return f"Successfully sent email to {', '.join(recipients)}{suffix}"
        return f"Error sending email: {message}"

    def _run(
        self,
        to_email: str,
        subject: str,
        content: str,
        attachments: Union[List[str], str] = "",
    ) -> str:
        raise NotImplementedError("Use _arun instead")

class WeChatWorkInput(BaseModel):
    content: str = Field(description="The main body of the message in Markdown format")
    group_id: Optional[int] = Field(
        default=None,
        description=(
            "可选。要发到哪个企微群的群 ID（正整数）。"
            "用户明确说「发到 xx 群」时，用 list_notification_channels 查到对应群的 id 再填这里。"
            "留空则发到默认企微群。**不要编造 id**。"
        ),
    )
    group_name: Optional[str] = Field(
        default=None,
        description=(
            "可选。要发到哪个企微群的名称。已知群名但不确定 id 时可只填这个；"
            "与 group_id 同时填时以 group_id 为准。留空则发到默认群。"
        ),
    )

class send_wechat_work_message(BaseTool):
    name: str = "send_wechat_work_message"
    description: str = (
        "发送企业微信群机器人 Markdown 消息。Send a Markdown message to WeChat Work. "
        "默认发到当前用户在个人中心 -> 消息通知里设置的**默认企微群**。"
        "用户明确要求发到某个群时，用 group_id 或 group_name 指定；"
        "不确定用户有哪些群时，先调用 list_notification_channels 查一下。"
    )
    args_schema: Type[BaseModel] = WeChatWorkInput

    async def _arun(
        self,
        content: str,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
    ) -> str:
        """Use the tool asynchronously."""
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import NotificationService

        agent_ctx = get_current_agent_context()
        if not agent_ctx or not agent_ctx.user_id:
            return "Error: 无法确定当前用户，企业微信消息未发送。"

        try:
            async with AsyncSessionLocal() as db:
                ok, message, target = await NotificationService.dispatch_notification(
                    db,
                    int(agent_ctx.user_id),
                    "wechat_work",
                    title="",
                    content=content,
                    channel_id=group_id,
                    channel_name=group_name,
                )
        except Exception as e:
            logger.error(f"WeChat Work Tool Error: {e}", exc_info=True)
            return f"Error executing WeChat Work tool: {str(e)}"

        if ok:
            return f"已发送到企业微信「{target.name or '未命名群'}」(id={target.id})"
        return f"企业微信消息未发送：{message}"

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
    group_id: Optional[int] = Field(
        default=None,
        description=(
            "可选。要发到哪个飞书群的群 ID（正整数）。"
            "用户明确说「发到 xx 群」时，用 list_notification_channels 查到对应群的 id 再填这里。"
            "留空则发到默认飞书群。**不要编造 id**。"
        ),
    )
    group_name: Optional[str] = Field(
        default=None,
        description=(
            "可选。要发到哪个飞书群的名称。已知群名但不确定 id 时可只填这个；"
            "与 group_id 同时填时以 group_id 为准。留空则发到默认群。"
        ),
    )


class send_feishu_message(BaseTool):
    name: str = "send_feishu_message"
    description: str = (
        "发送飞书群机器人 Markdown 卡片消息。Send a Markdown message card to Feishu. "
        "默认发到当前用户在个人中心 -> 消息通知里设置的**默认飞书群**。"
        "用户明确要求发到某个群时，用 group_id 或 group_name 指定；"
        "不确定用户有哪些群时，先调用 list_notification_channels 查一下。"
    )
    args_schema: Type[BaseModel] = FeishuInput

    async def _arun(
        self,
        title: str,
        content: str,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
    ) -> str:
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

        cleaned_content = str(content or "").strip()
        if not cleaned_content:
            return "Error: 正文内容不能为空。"

        try:
            async with AsyncSessionLocal() as db:
                ok, message, target = await NotificationService.dispatch_notification(
                    db,
                    user_id,
                    "feishu",
                    title=str(title or "").strip() or "智能体消息通知",
                    content=cleaned_content,
                    channel_id=group_id,
                    channel_name=group_name,
                )
        except Exception as e:
            logger.error("Feishu Tool Error: %s", e, exc_info=True)
            return f"Error executing Feishu tool: {str(e)}"

        if ok:
            return f"已发送到飞书「{target.name or '未命名群'}」(id={target.id})"
        return f"飞书消息未发送：{message}"

    def _run(self, title: str, content: str) -> str:
        raise NotImplementedError("Use _arun instead")


class ListNotificationChannelsInput(BaseModel):
    channel_type: Optional[str] = Field(
        default=None,
        description=(
            "可选。只看某个渠道：dingtalk（钉钉）/ feishu（飞书）/ wechat_work（企业微信）。"
            "留空则返回全部三个渠道。"
        ),
    )


class list_notification_channels(BaseTool):
    """列出当前用户可用的消息通道与群。

    模型在两种情况下必须先查：用户说了一个群名但不确定对应哪个 id；
    用户问「我能发到哪些群 / 有没有配钉钉」。没有这份清单就只能瞎猜 id，
    而猜错会直接导致消息被忽略（按设计不兜底默认群）。
    """

    name: str = "list_notification_channels"
    description: str = (
        "列出当前用户已配置的消息通道（钉钉 / 飞书 / 企业微信）及其下的群："
        "每个群给出 id、名称、是否启用、是否为默认群。"
        "发消息到指定群之前用它查群 ID；用户问「我有哪些群/通道」时也用它。"
        "只返回用户自己的配置，不包含任何 webhook 或密钥。"
    )
    args_schema: Type[BaseModel] = ListNotificationChannelsInput

    async def _arun(self, channel_type: Optional[str] = None) -> str:
        from app.core.context import get_current_agent_context
        from app.core.orm import AsyncSessionLocal
        from app.services.notification_service import (
            MULTI_GROUP_CHANNELS,
            NotificationService,
            TEST_CHANNEL_LABELS,
        )

        agent_ctx = get_current_agent_context()
        if not agent_ctx or not agent_ctx.user_id:
            return json.dumps(
                {"channels": [], "note": "无法确定当前用户，查不到可用的消息通道。"},
                ensure_ascii=False,
            )

        wanted = str(channel_type or "").strip()
        if wanted and wanted not in MULTI_GROUP_CHANNELS:
            return json.dumps(
                {
                    "channels": [],
                    "note": (
                        f"channel_type={channel_type!r} 不是有效渠道，"
                        f"可选：{'、'.join(MULTI_GROUP_CHANNELS)}。"
                    ),
                },
                ensure_ascii=False,
            )

        try:
            async with AsyncSessionLocal() as db:
                grouped = await NotificationService.list_channels(
                    db, int(agent_ctx.user_id), wanted or None
                )
        except Exception as exc:  # noqa: BLE001 - 查询失败要让模型知道，而不是空清单
            logger.error("list_notification_channels 查询失败: %s", exc, exc_info=True)
            return json.dumps(
                {"channels": [], "note": f"查询消息通道失败：{exc}"},
                ensure_ascii=False,
            )

        channels: List[Dict[str, Any]] = []
        default_notes: List[str] = []
        for key in MULTI_GROUP_CHANNELS:
            if wanted and key != wanted:
                continue
            items = grouped.get(key) or []
            enabled_items = [i for i in items if i.get("is_enabled")]
            # 这里的「默认」必须和 send_* 的真实兜底一致：发信走
            # get_config_by_type_raw（已启用优先 → 默认群优先 → id 升序），
            # 默认群停用时会自动落到最早启用的群。原先只认「is_default 且启用」，
            # 默认群一停用就报「没有已启用的群」，可实际上消息发得出去 ——
            # 模型据此会告诉用户去配群，而群本来就是配好的。
            default_item = next(
                (i for i in enabled_items if i.get("is_default")), None
            )
            fallback_used = False
            if default_item is None and enabled_items:
                default_item = enabled_items[0]
                fallback_used = True
            if default_item:
                label = TEST_CHANNEL_LABELS[key]
                if fallback_used:
                    default_notes.append(
                        f"{label}默认群已停用或不存，不指定群时发到最早启用的 id={default_item['id']}"
                        f"「{default_item['name']}」"
                    )
                else:
                    default_notes.append(
                        f"{label}默认群 id={default_item['id']}「{default_item['name']}」"
                    )
            channels.append(
                {
                    "type": key,
                    "label": TEST_CHANNEL_LABELS.get(key, key),
                    "configured": bool(items),
                    "default_id": default_item["id"] if default_item else None,
                    "default_is_fallback": fallback_used,
                    "items": items,
                }
            )

        if default_notes:
            note = "；".join(default_notes) + "。不给 group_id 时发到默认群；已停用的群不能作为发送目标。"
        else:
            note = "没有已启用的群。请用户先到「个人中心 → 消息通知」配置并启用至少一个群。"

        return json.dumps(
            {"channels": channels, "note": note},
            ensure_ascii=False,
        )

    def _run(self, channel_type: Optional[str] = None) -> str:
        raise NotImplementedError("Use _arun instead")

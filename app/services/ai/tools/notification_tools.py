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
    description: str = "Send a Markdown message to a DingTalk group chat via robot webhook."
    args_schema: Type[BaseModel] = DingTalkInput

    async def _arun(self, title: str, content: str) -> str:
        """Use the tool asynchronously."""
        # Retrieve configuration from runtime context
        runtime_cfg = getattr(self, "_runtime_config", None)
        
        webhook_url = None
        secret = None
        
        if runtime_cfg and hasattr(runtime_cfg, 'engine_config_override') and runtime_cfg.engine_config_override:
            webhook_url = runtime_cfg.engine_config_override.get("webhook_url")
            secret = runtime_cfg.engine_config_override.get("secret")

        if not webhook_url:
            logger.warning(f"DingTalk Tool: Webhook URL missing in runtime config.")
            return "Error: DingTalk Webhook URL not configured. Please go to Agent Tool Settings and set it."

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
    to_email: str = Field(description="The recipient email address (e.g. 'user@example.com')")
    subject: str = Field(description="The subject line of the email")
    content: str = Field(description="The body content of the email (Markdown or Text)")

class send_email(BaseTool):
    name: str = "send_email"
    description: str = "Send an email via SMTP. Requires SMTP configuration in agent settings."
    args_schema: Type[BaseModel] = EmailInput

    async def _arun(self, to_email: str, subject: str, content: str) -> str:
        """Use the tool asynchronously."""
        import smtplib
        import asyncio
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.utils import formataddr

        # 1. Retrieve Config
        runtime_cfg = getattr(self, "_runtime_config", None)
        cfg = {}
        if runtime_cfg and hasattr(runtime_cfg, 'engine_config_override') and runtime_cfg.engine_config_override:
            cfg = runtime_cfg.engine_config_override

        smtp_host = cfg.get("smtp_host")
        smtp_port = int(cfg.get("smtp_port") or 465)
        smtp_user = cfg.get("smtp_user")
        smtp_password = cfg.get("smtp_password")
        sender_name = cfg.get("sender_name", "AI Agent")
        
        if not smtp_host or not smtp_user or not smtp_password:
            return "Error: SMTP configuration (host, user, password) is missing. Please configure it in Agent Tool Settings."

        def send_sync():
            try:
                msg = MIMEMultipart()
                msg['From'] = formataddr((sender_name, smtp_user))
                msg['To'] = to_email
                msg['Subject'] = subject
                msg.attach(MIMEText(content, 'plain', 'utf-8')) # Could upgrade to HTML later

                # Auto-detect SSL/TLS based on port
                if smtp_port == 465:
                    server = smtplib.SMTP_SSL(smtp_host, smtp_port)
                else:
                    server = smtplib.SMTP(smtp_host, smtp_port)
                    # Try StartTLS if not SSL port, but catch error if server doesn't support it
                    try:
                        server.starttls()
                    except:
                        pass

                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [to_email], msg.as_string())
                server.quit()
                return f"Successfully sent email to {to_email}"
            except Exception as e:
                logger.error(f"SMTP Error: {e}", exc_info=True)
                raise e

        try:
            # Run blocking SMTP in thread pool
            return await asyncio.to_thread(send_sync)
        except Exception as e:
            return f"Error sending email: {str(e)}"

    def _run(self, to_email: str, subject: str, content: str) -> str:
        raise NotImplementedError("Use _arun instead")

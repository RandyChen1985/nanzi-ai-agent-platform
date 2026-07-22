import logging
import httpx
import json
import ipaddress
import socket
import pytz
from datetime import datetime
from urllib.parse import urlparse
from app.services.ai.tools.tool_compat import tool
from app.services.ai.tools.task_manager_tools import (
    create_recurring_task, get_my_tasks, cancel_task, 
    start_task, pause_task, run_task_manually
)
from app.services.ai.tools.notification_tools import send_dingtalk_message

logger = logging.getLogger(__name__)

def validate_url(url: str) -> bool:
    """
    Validates URL to prevent SSRF attacks by blocking internal IP ranges.
    Returns True if safe, raises ValueError if unsafe.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid URL: missing hostname")

        # Resolve hostname to IP
        try:
            ip_str = socket.gethostbyname(hostname)
        except socket.gaierror:
            # If we can't resolve it, it might be unreachable, but strict SSRF usually implies blocking it.
            # However, for a general tool, if it doesn't resolve, httpx will fail anyway.
            # But to be safe against DNS rebinding, we should resolve here.
            raise ValueError(f"Could not resolve hostname: {hostname}")

        ip = ipaddress.ip_address(ip_str)

        # Block loopback, private, link-local, multicast
        # Note: 198.18.0.0/15 is considered private by some python versions but is often used by
        # transparent proxies or benchmarking. We allow it to support tools like httpbin.org
        is_benchmark = ip in ipaddress.ip_network('198.18.0.0/15')

        if not is_benchmark and (ip.is_loopback or 
            ip.is_private or 
            ip.is_link_local or 
            ip.is_multicast):
            raise ValueError(f"Access to internal/private IP {ip_str} ({hostname}) is restricted.")
            
        return True
    except Exception as e:
        logger.warning(f"URL Validation Failed: {e}")
        raise e

@tool
async def system_http_request(method: str, url: str, headers: dict = None, body: dict = None, params: dict = None) -> str:
    """
    Executes a generic HTTP request to an external API.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        url: The full URL to request.
        headers: Optional dictionary of HTTP headers.
        body: Optional dictionary for JSON body (for POST/PUT).
        params: Optional dictionary for Query parameters.
    """
    try:
        # Security Check
        validate_url(url)
        
        method = method.upper()
        if headers is None:
            headers = {}
        # Set a default User-Agent if not present
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "NanZi-AI-Agent/1.0"

        timeout = 30.0
        
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            logger.info(f"[SystemTool] {method} {url} Params={params}")
            
            if method == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method in ["POST", "PUT", "PATCH", "DELETE"]:
                response = await client.request(method, url, json=body, params=params, headers=headers)
            else:
                return f"Error: Unsupported method {method}"

            # Try to return JSON if possible, else text
            try:
                data = response.json()
                return json.dumps(data, ensure_ascii=False)
            except:
                return response.text[:10000] # Truncate generic text responses to avoid context overflow

    except Exception as e:
        return f"Error executing request: {str(e)}"

@tool
def resolve_relative_dates(phrases: list[str], timezone: str = "Asia/Shanghai") -> str:
    """
    将中文相对日期短语解析为具体起止日期（YYYY-MM-DD），与系统【当前时间锚点】同源。

    使用规则：
    - 若 system prompt 已含【当前时间锚点】或【本轮问题时间解读】，优先直接引用，勿重复调用。
    - 仅当需要解析锚点未覆盖的短语时调用；每轮用户问题最多调用 1 次。
    - phrases: 如 ["近7天", "上周", "下周五"]，可一次传入多个。

    Args:
        phrases: 相对日期短语列表。
        timezone: IANA 时区，默认 Asia/Shanghai。
    """
    from app.services.ai.time_anchor import resolve_relative_date_phrases

    rows = resolve_relative_date_phrases(phrases, timezone=timezone or None)
    return json.dumps(rows, ensure_ascii=False)


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """
    获取当前系统时间（含星期）。

    使用规则：
    - 问候语（你好/在吗）禁止调用。
    - 若 system prompt 已含【当前时间锚点】，相对时间（今天/近N天/本周等）须直接用锚点日期，禁止为换算日期反复调用本工具。
    - 每轮用户问题最多调用 1 次；仅当锚点缺失且必须知道「此刻」时再调用。

    Args:
        timezone: IANA 时区（如 UTC、Asia/Shanghai），默认 Asia/Shanghai。
    """
    try:
        if timezone:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        else:
            now = datetime.now()
        
        # Add Chinese weekday
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday_str = weekdays[now.weekday()]
        
        return now.strftime(f"%Y-%m-%d %H:%M:%S {weekday_str} %Z%z")
    except Exception as e:
        return f"Error getting time: {str(e)}"


SYSTEM_IMPLICIT_TOOLS = [
    get_current_time,
    resolve_relative_dates,
    create_recurring_task,
    get_my_tasks,
    cancel_task,
    start_task,
    pause_task,
    run_task_manually,
]

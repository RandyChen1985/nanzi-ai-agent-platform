from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

AGENTSCOPE_PARALLEL_TOOL_EXECUTION_KEY = "agentscope_parallel_tool_execution"
AGENTSCOPE_MAX_CONCURRENT_TOOLS_KEY = "agentscope_max_concurrent_tools"

DEFAULT_PARALLEL_TOOL_EXECUTION = True
DEFAULT_MAX_CONCURRENT_TOOLS = 5
MIN_CONCURRENT_TOOLS = 1
MAX_CONCURRENT_TOOLS_LIMIT = 20

# 必须互斥串行执行、不得并发的有状态/独占工具清单
EXCLUSIVE_TOOL_NAMES = frozenset({
    "request_user_confirmation",
    "ask_user_question",
    "sub_agent_call",
    "sub_agent_batch_call",
    "web_renderer_and_snapshot",
})

_global_semaphore: asyncio.Semaphore | None = None
_current_max_concurrency: int = DEFAULT_MAX_CONCURRENT_TOOLS
_parallel_enabled_cache: bool = DEFAULT_PARALLEL_TOOL_EXECUTION


def validate_agentscope_parallel_tool_execution(value: Any) -> bool:
    """校验并转换并发开关配置。"""
    if isinstance(value, bool):
        return value
    val = str(value or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Invalid agentscope_parallel_tool_execution: '{value}'. Must be a boolean (true/false)."
    )


def validate_agentscope_max_concurrent_tools(value: Any) -> int:
    """校验并发最大连接/协程数配置。"""
    try:
        val = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid agentscope_max_concurrent_tools: '{value}'. Must be an integer."
        )
    if not (MIN_CONCURRENT_TOOLS <= val <= MAX_CONCURRENT_TOOLS_LIMIT):
        raise ValueError(
            f"agentscope_max_concurrent_tools must be between {MIN_CONCURRENT_TOOLS} and {MAX_CONCURRENT_TOOLS_LIMIT}, got {val}."
        )
    return val


def is_parallel_tool_execution_enabled() -> bool:
    """获取当前并发执行开关状态（支持内存缓存与配置兜底）。"""
    global _parallel_enabled_cache
    return _parallel_enabled_cache


def set_parallel_tool_execution_enabled(enabled: bool) -> None:
    """设置并发开关状态（运行时动态切换/单测控制）。"""
    global _parallel_enabled_cache
    _parallel_enabled_cache = bool(enabled)


def get_current_max_concurrency() -> int:
    global _current_max_concurrency
    return _current_max_concurrency


def set_max_concurrency_limit(limit: int) -> None:
    global _current_max_concurrency, _global_semaphore
    limit = validate_agentscope_max_concurrent_tools(limit)
    _current_max_concurrency = limit
    _global_semaphore = None


def get_tool_concurrency_semaphore() -> asyncio.Semaphore:
    """获取并发信号量。若并发度变更或未初始化，则重新创建。"""
    global _global_semaphore, _current_max_concurrency
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(_current_max_concurrency)
    return _global_semaphore


def is_tool_concurrency_safe(
    tool_name: str,
    *,
    permission_scope: str = "ask",
    approval_mode: str = "allow",
    source_type: str = "system",
    custom_flag: bool | None = None,
    parallel_enabled: bool | None = None,
    read_only_tool_names: frozenset[str] | set[str] | None = None,
) -> bool:
    """判定指定工具在当前配置和运行时环境下是否可以并发执行。

    安全准则：
    1. 显式禁用 (custom_flag is False)：直接串行；
    2. 全局开关降级：若系统关闭了并发工具执行，直接串行；
    3. 审批/拦截模式：若 approval_mode in ("ask", "deny")，必须串行 HITL 确认；
    4. 显式放行 (custom_flag is True)：需确认非 ask/deny 模式；
    5. 严格只读要求：permission_scope 必须为 "read"；
    6. 排除有状态/独占黑名单：浏览器交互 (browser_*)、人机提问 (ask_user_question) 等强制串行；
    7. 满足上述条件的只读工具判定为 concurrency_safe。
    """
    if custom_flag is False:
        return False

    enabled = (
        parallel_enabled
        if parallel_enabled is not None
        else is_parallel_tool_execution_enabled()
    )
    if not enabled:
        return False

    if approval_mode in {"ask", "deny"}:
        return False

    if custom_flag is True:
        return True

    if permission_scope != "read":
        return False

    # 单页面浏览器操作与有状态独占工具排除
    if tool_name.startswith("browser_") or tool_name in EXCLUSIVE_TOOL_NAMES:
        return False

    if read_only_tool_names and tool_name in read_only_tool_names:
        return True

    return True


async def execute_with_concurrency_guard(
    coro_fn: Callable[[], Coroutine[Any, Any, Any]],
) -> Any:
    """在并发信号量保护下执行工具调用协程。"""
    semaphore = get_tool_concurrency_semaphore()
    async with semaphore:
        return await coro_fn()

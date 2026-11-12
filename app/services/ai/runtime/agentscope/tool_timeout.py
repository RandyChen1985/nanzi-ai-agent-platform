"""AgentScope 工具调用的统一超时策略。"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence


AGENT_MAX_TOOLCALL_TIMEOUT_KEY = "agent_max_toolcall_timeout"
DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT = 180.0
MIN_AGENT_MAX_TOOLCALL_TIMEOUT = 1.0
MAX_AGENT_MAX_TOOLCALL_TIMEOUT = 3600.0
MAX_AGENT_VERSION_TOOLCALL_TIMEOUT = 86400.0
_INTEGER_PATTERN = re.compile(r"^[0-9]+$")


def _coerce_positive_timeout(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def parse_agent_max_toolcall_timeout(
    value: Any,
    *,
    default: float = DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
) -> float:
    """将系统配置解析为安全的整数秒；历史非法值回退默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        candidate = float(value)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = float(value)
    else:
        return default
    if not MIN_AGENT_MAX_TOOLCALL_TIMEOUT <= candidate <= MAX_AGENT_MAX_TOOLCALL_TIMEOUT:
        return default
    return candidate


def validate_agent_max_toolcall_timeout(value: Any) -> None:
    """校验管理端保存的工具调用超时配置。"""
    if isinstance(value, bool):
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = int(value)
    else:
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )
    if not 1 <= candidate <= 3600:
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )


def parse_agent_version_toolcall_timeout(value: Any) -> float | None:
    """解析版本级工具调用超时；空值或非法值表示继承全局配置。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = float(value)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = float(value)
    else:
        return None
    if not MIN_AGENT_MAX_TOOLCALL_TIMEOUT <= candidate <= MAX_AGENT_VERSION_TOOLCALL_TIMEOUT:
        return None
    return candidate


def effective_tool_timeout(
    global_timeout: Any,
    tool_timeout: Any = None,
    explicit_timeout: Any = None,
) -> float:
    """按传入配置的优先顺序选择超时秒数，不做最大值合并。"""
    for value in (global_timeout, tool_timeout, explicit_timeout):
        normalized = _coerce_positive_timeout(value)
        if normalized is not None:
            return normalized
    return DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT


def resolve_agent_toolcall_timeout(global_timeout: Any, agent_timeout: Any = None) -> float:
    """按版本级优先、全局次之选择当前智能体工具调用超时。"""
    normalized_agent = parse_agent_version_toolcall_timeout(agent_timeout)
    if normalized_agent is not None:
        return normalized_agent
    return parse_agent_max_toolcall_timeout(global_timeout)


def resolve_tool_timeout(
    *,
    tool_config_timeout: Any = None,
    spec_timeout: Any = None,
    agent_timeout: Any = None,
    global_timeout: Any = None,
) -> tuple[float, str]:
    """解析单个工具的真实生效超时与来源层级。

    三级覆盖：工具级配置 > 工具自带定义 > 智能体版本级 > 全局级。
    工具级只做「收紧」不做「放宽」：最终值不会超过上层（版本级/全局级）选定的值——
    全局是平台保护上限，单个工具配置不能把它顶开。

    Returns:
        (生效秒数, 来源)。来源取值：``tool`` / ``tool_defined`` / ``agent`` / ``global``。
    """
    ceiling = resolve_agent_toolcall_timeout(global_timeout, agent_timeout)
    ceiling_source = (
        "agent"
        if parse_agent_version_toolcall_timeout(agent_timeout) is not None
        else "global"
    )

    configured = _coerce_positive_timeout(tool_config_timeout)
    if configured is not None:
        return min(configured, ceiling), "tool"

    defined = _coerce_positive_timeout(spec_timeout)
    if defined is not None:
        return min(defined, ceiling), "tool_defined"

    return ceiling, ceiling_source


async def load_agent_max_toolcall_timeout() -> float:
    """读取当前请求构建工具时使用的全局工具调用超时。"""
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(
            AGENT_MAX_TOOLCALL_TIMEOUT_KEY,
            default=str(int(DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT)),
        )
    except Exception:
        raw = None
    return parse_agent_max_toolcall_timeout(raw)


def collect_tool_timeouts(tool_configs: Any) -> dict[str, float]:
    """从版本 ``tools`` 配置里收集「工具名 → 工具级超时」。

    兼容三种元素形态：字符串（无配置）、dict（DB JSON 直出）、
    ``ToolConfigItem``（运行时对象）。未设置/非法值一律跳过，交给上层回落。
    """
    collected: dict[str, float] = {}
    for item in tool_configs or []:
        if isinstance(item, str):
            continue
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            raw = item.get("timeout_seconds")
        else:
            name = str(getattr(item, "name", "") or "").strip()
            raw = getattr(item, "timeout_seconds", None)
        value = _coerce_positive_timeout(raw)
        if name and value is not None:
            collected[name] = value
    return collected


#: spec 上记录「平台配置名」的 metadata 键。
#: 配置按平台名（`exec_command`）存，运行时 spec 的名字却是另一个
#: （AgentScope 原生 `Bash`、MCP 的 model 化名），只按 spec.name 查表必然漏。
PLATFORM_TOOL_NAME_KEY = "_platform_tool_name"


def _lookup_tool_timeout(
    table: Mapping[str, Any],
    *,
    spec_name: str,
    platform_name: str | None,
) -> Any:
    """按「平台配置名 → spec 名 → 别名」的顺序查工具级超时。

    三条路径都要留：
    1. 平台名（首选）：配置就是按它存的；
    2. spec 名：少数工具两者本就同名；
    3. 别名反查：某些链路（如 workspace 重新绑定）会重建 spec 丢掉 metadata，
       此时仍能靠 ``exec_command → Bash`` 这张表把配置对上。
    """
    if platform_name and platform_name in table:
        return table[platform_name]
    if spec_name and spec_name in table:
        return table[spec_name]
    if spec_name and table:
        # 延迟导入：registry 依赖面很大，模块级导入会造成循环依赖。
        from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES

        for configured_name, value in table.items():
            if AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(configured_name) == spec_name:
                return value
    return None


def apply_agent_tool_timeout(
    specs: Iterable[Any],
    global_timeout: Any,
    *,
    agent_timeout: Any = None,
    tool_timeouts: Mapping[str, Any] | None = None,
) -> list[Any]:
    """给一批运行时工具应用超时快照，逐个工具解析而非整批覆盖。

    ``tool_timeouts`` 是「工具名 → 工具级超时」的映射（来自版本 tools JSON）。
    历史实现用同一个值覆盖整批 spec 的 ``timeout_seconds``，会把脚本工具注册时
    自带的超时一并抹平；这里改为逐 spec 走 :func:`resolve_tool_timeout`。
    """
    configured = dict(tool_timeouts or {})
    from dataclasses import is_dataclass

    result = []
    for spec in specs:
        tool_name = str(getattr(spec, "name", "") or "")
        metadata = getattr(spec, "metadata", None)
        platform_name: str | None = None
        if isinstance(metadata, dict):
            platform_name = str(metadata.get(PLATFORM_TOOL_NAME_KEY) or "") or None
        timeout_seconds, timeout_source = resolve_tool_timeout(
            tool_config_timeout=_lookup_tool_timeout(
                configured, spec_name=tool_name, platform_name=platform_name
            ),
            spec_timeout=getattr(spec, "timeout_seconds", None),
            agent_timeout=agent_timeout,
            global_timeout=global_timeout,
        )
        updates = {"timeout_seconds": timeout_seconds, "timeout_source": timeout_source}
        if is_dataclass(spec) and not isinstance(spec, type):
            result.append(replace(spec, **updates))
        else:
            for key, value in updates.items():
                try:
                    setattr(spec, key, value)
                except Exception:
                    pass
            result.append(spec)
    return result


async def apply_configured_agent_tool_timeout(
    specs: Sequence[Any] | Iterable[Any],
    *,
    agent_timeout: Any = None,
    tool_timeouts: Mapping[str, Any] | None = None,
) -> list[Any]:
    """读取一次系统配置并返回当前版本的工具超时快照。"""
    return apply_agent_tool_timeout(
        specs,
        await load_agent_max_toolcall_timeout(),
        agent_timeout=agent_timeout,
        tool_timeouts=tool_timeouts,
    )


#: 沙箱 gateway 单次请求超时的下限。AgentScope 在
#: ``_sandboxed_base`` 里把该值硬编码为 30s；即使平台把
#: ``agent_max_toolcall_timeout`` 调得更小，也保留 30s，避免把
#: 健康检查/短命令的传输超时压得过紧。
MIN_SANDBOX_GATEWAY_TIMEOUT = 30.0


async def apply_sandbox_gateway_timeout(workspace: Any) -> float | None:
    """把沙箱 gateway 的传输超时对齐到 ``agent_max_toolcall_timeout``。

    沙箱内的 Bash/Read/Write 工具都经 ``GatewayClient.exec_request``
    转发：每条请求在沙箱里跑一次 shim，再由 shim 调用 ``/mcps/...``
    等到工具真正执行完。AgentScope 把这次等待硬编码为 30s，所以命令
    只要超过 30s（例如 ``sleep 300``）就会以
    ``gateway shim exited with -1: timed out`` 失败，与平台侧的工具
    调用超时配置无关。这里在 workspace 初始化完成后覆盖该属性，
    让长命令按平台配置的上限执行。

    Returns:
        实际写入的超时秒数；workspace 没有 gateway（本地/SSH 策略）时
        返回 ``None``。
    """
    gateway = getattr(workspace, "_gateway", None)
    if gateway is None:
        return None
    configured = await load_agent_max_toolcall_timeout()
    timeout = max(float(configured), MIN_SANDBOX_GATEWAY_TIMEOUT)
    try:
        gateway.timeout = timeout
    except Exception:
        return None
    return timeout

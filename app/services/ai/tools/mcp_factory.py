import asyncio
import contextlib
import json
import logging
import re
import time
from typing import Dict, Any, List, Optional
from pydantic import create_model, Field
from app.services.ai.tools.model_tool_name import build_model_tool_name
from app.services.ai.tools.tool_compat import StructuredTool
from app.models.mcp import McpToolCache
from app.services.ai.tools.mcp_client import McpClientService
from app.services.ai.runtime.agentscope.tool_timeout import (
    load_agent_max_toolcall_timeout,
)
from app.services.ai.grounding.models import EvidenceType
from app.core.context import get_current_agent_context

logger = logging.getLogger(__name__)


def current_mcp_agent_identity() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """从当前后端 AgentContext 提取 MCP 断言所需身份，绝不读取工具参数。"""
    context = get_current_agent_context()
    if context is None:
        return {}, {}

    user_info = dict(context.user_dimensions or {})
    if context.user_id is not None:
        user_info["user_id"] = str(context.user_id)
    agent_info: Dict[str, Any] = {
        "agent_id": context.agent_id,
        "agent_name": context.agent_name,
    }
    if context.agent_version:
        agent_info["agent_version_id"] = context.agent_version
    return user_info, agent_info


#: 进度快照队列长度：与上游 MCP 适配器同口径，满了丢最旧的一条；
#: 进度洪峰绝不能影响最终结果的返回。
MCP_PROGRESS_QUEUE_SIZE = 1024
#: 同一进度片的最小推送间隔。远端按 token 发心跳时用它压住 SSE 帧数；
#: ``progress`` 前进 ≥1 或 ``message`` 变化时不受此限。
MCP_PROGRESS_MIN_INTERVAL_SECONDS = 0.25


async def resolve_mcp_tool_timeout() -> Optional[float]:
    """单次 MCP 工具调用的超时：优先请求期快照，其次系统配置。

    平台已有 ``agent_max_toolcall_timeout``（全局默认 180s，支持版本级覆盖），
    并把它快照进 ``AgentContext``。MCP 输出链路曾经写死 120s，长工具会在进度
    走到一半时被静默掐断——这里统一回同一个旋钮。
    """
    context = get_current_agent_context()
    snapshot = getattr(context, "agent_max_toolcall_timeout_seconds", None) if context else None
    if snapshot:
        try:
            value = float(snapshot)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    try:
        return float(await load_agent_max_toolcall_timeout())
    except Exception:  # pragma: no cover - 配置服务异常不该阻断工具调用
        logger.warning("[MCP] 读取 agent_max_toolcall_timeout 失败，回退默认超时", exc_info=True)
        return None


async def stream_remote_tool_with_progress(
    *,
    server_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout_seconds: Optional[float] = None,
    **identity_kwargs: Any,
):
    """调用远端工具，把 ``notifications/progress`` 变成 AgentScope 进度分片。

    产出顺序：0..N 个进度分片 → 1 个业务结果（最后一个 ``yield``）。
    进度是**快照语义**（队列满丢最旧、重复心跳被节流），消费者不能假设每帧都收到。
    """
    from agentscope.message import ToolResultState
    from agentscope.tool import ToolChunk

    queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=MCP_PROGRESS_QUEUE_SIZE)
    accepting = True
    last_emit = 0.0
    last_progress: Optional[float] = None
    last_message: Optional[str] = None

    async def _on_progress(
        progress: float,
        total: Optional[float],
        message: Optional[str],
    ) -> None:
        nonlocal accepting, last_emit, last_progress, last_message
        if not accepting:
            return
        if not isinstance(progress, (int, float)) or isinstance(progress, bool):
            # mcp SDK 的路由层只放行 int|float，这里再兜一层：第三方服务端的
            # 畸形通知宁可丢弃，也不能让工具因为一次进度上报而失败。
            return
        now = time.monotonic()
        advanced = last_progress is None or (progress - last_progress) >= 1
        throttled = (
            not advanced
            and message == last_message
            and (now - last_emit) < MCP_PROGRESS_MIN_INTERVAL_SECONDS
        )
        if throttled:
            return
        last_emit, last_progress, last_message = now, progress, message
        if queue.full():
            # 快照语义：丢最旧的一条，保证新进度可见，也不阻塞远端调用。
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - full/empty 竞态
                pass
        queue.put_nowait(
            ToolChunk(
                content=[],
                state=ToolResultState.RUNNING,
                is_last=False,
                metadata={
                    "mcp_progress": {
                        "progress": progress,
                        "total": total,
                        "message": message,
                    },
                },
            )
        )

    call_task = asyncio.create_task(
        McpClientService.call_remote_tool(
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            progress_callback=_on_progress,
            timeout_seconds=timeout_seconds,
            **identity_kwargs,
        )
    )
    getter: Optional[asyncio.Future] = None
    try:
        while not call_task.done():
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {call_task, getter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if getter in done:
                yield getter.result()
                getter = None
            else:
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
                getter = None

        accepting = False
        while not queue.empty():
            yield queue.get_nowait()
        yield await call_task
    finally:
        accepting = False
        if getter is not None and not getter.done():
            getter.cancel()
        if not call_task.done():
            # 上层取消（用户停止 / 平台超时）时别把远端请求晾在连接上。
            # 与上游 ``MCPTool._stream_call`` 同构：mcp SDK 取消时会先发
            # notifications/cancelled 再清理连接，用 shield 防止外层重复取消
            # 打断清理（打断会留下半关闭的 SSE 连接）。代价是取消要等 SDK
            # 清理完成——这是 SDK 的设计取舍，不是这里的等待写得不好。
            call_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.shield(call_task)
        elif not call_task.cancelled():
            # 取走异常，避免 "Task exception was never retrieved" 噪声日志。
            call_task.exception()


def _map_schema_type(param_def: dict[str, Any]) -> Any:
    """根据 JSON Schema 定义映射为 Python/Pydantic 字段类型。"""
    raw_type = param_def.get("type")

    # 兼容 type 为列表的情况，如 ["string", "null"]
    if isinstance(raw_type, list):
        non_null_types = [t for t in raw_type if t != "null"]
        type_str = non_null_types[0] if non_null_types else "string"
    elif isinstance(raw_type, str):
        type_str = raw_type
    else:
        if "items" in param_def:
            type_str = "array"
        elif "properties" in param_def:
            type_str = "object"
        elif "enum" in param_def:
            type_str = "string"
        else:
            return Any

    if type_str == "integer":
        return int
    if type_str == "number":
        return float
    if type_str == "boolean":
        return bool
    if type_str == "array":
        return list
    if type_str == "object":
        return dict
    if type_str == "string":
        return str
    return Any


def _schema_allows_null(param_def: dict[str, Any]) -> bool:
    raw_type = param_def.get("type")
    return isinstance(raw_type, list) and "null" in raw_type


class McpToolFactory:
    @staticmethod
    def create_tool(tool_record: McpToolCache) -> StructuredTool:
        """
        Creates a runtime StructuredTool-compatible wrapper from a cached MCP tool record.
        """
        
        # 1. Parse JSON Schema from MCP
        schema_def = json.loads(tool_record.parameter_schema or "{}")
        properties = schema_def.get("properties", {})
        required_fields = set(schema_def.get("required", []))

        fields = {}
        for param_name, param_def in properties.items():
            if not isinstance(param_def, dict):
                param_def = {}
            mapped_type = _map_schema_type(param_def)
            p_desc = param_def.get("description", "")
            is_required = param_name in required_fields

            if is_required:
                p_default = ...
                field_type = Optional[mapped_type] if _schema_allows_null(param_def) else mapped_type
            else:
                p_default = param_def.get("default", None)
                field_type = Optional[mapped_type]

            fields[param_name] = (field_type, Field(default=p_default, description=p_desc))
        
        # Create dynamic Pydantic model for args
        clean_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(tool_record.tool_name)).strip("_") or "Tool"
        args_schema = create_model(f"Mcp_{clean_name}Args", **fields)
        
        # 2. Define execution logic
        async def _execute(**kwargs) -> Any:
            # Extract raw tool name (remove our prefix)
            # Full name: "server_name:raw_tool_name"
            if ":" in tool_record.tool_name:
                raw_name = tool_record.tool_name.split(":", 1)[1]
            else:
                raw_name = tool_record.tool_name

            user_info, agent_info = current_mcp_agent_identity()
            context = get_current_agent_context()
            identity_kwargs: Dict[str, Any] = {}
            if user_info:
                identity_kwargs["user_info"] = user_info
            if agent_info:
                identity_kwargs["agent_info"] = agent_info
            if context and context.trace_id:
                identity_kwargs["request_id"] = context.trace_id

            # 远端进度 → AgentScope 进度分片：中间 yield ``mcp_progress`` 分片，
            # 最后 yield 业务结果本身（与协程版 return 的完全一致）。
            async for item in stream_remote_tool_with_progress(
                server_id=tool_record.server_id,
                tool_name=raw_name,
                arguments=kwargs,
                timeout_seconds=await resolve_mcp_tool_timeout(),
                require_user_context=True,
                **identity_kwargs,
            ):
                yield item
        
        _execute.__doc__ = tool_record.tool_description or f"MCP tool: {tool_record.tool_name}"
        
        # 数据库存储的冒号名称仅作平台标识；模型侧使用合法别名，执行闭包仍保留原始 MCP 名称。
        tool = StructuredTool.from_function(
            func=None,
            coroutine=_execute,
            name=build_model_tool_name(tool_record.tool_name),
            description=tool_record.tool_description or "",
            args_schema=args_schema
        )
        tool.display_name = tool_record.tool_name
        # 保留远端原始 Schema；args_schema 仅负责兼容本地参数校验，不能替代嵌套约束。
        tool.mcp_input_schema = schema_def

        declared_types = set()
        for value in schema_def.get("x-nanzi-evidence-types") or []:
            try:
                declared_types.add(EvidenceType(value))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid evidence type %r for %s", value, tool_record.tool_name)
        annotations = schema_def.get("x-nanzi-mcp-annotations") or {}

        # 只读权限与证据推断
        from app.services.ai.tools.registry import _is_read_only_mcp_tool
        annotation_read_only = (
            annotations.get("readOnlyHint") is True
            or annotations.get("read_only_hint") is True
        )
        annotation_mutating = (
            annotations.get("readOnlyHint") is False
            or annotations.get("read_only_hint") is False
        )
        inferred_read_only = _is_read_only_mcp_tool(
            name=tool_record.tool_name,
            description=str(tool_record.tool_description or ""),
        )
        if annotation_mutating:
            read_only = False
            tool.evidence_inference_disabled = True
        else:
            # readOnlyHint 是远端自报的提示，不能覆盖变更动作的保守推断。
            read_only = inferred_read_only

        tool.is_read_only = read_only
        tool.permission_scope = "read" if read_only else "ask"

        if declared_types:
            tool.evidence_types = frozenset(declared_types)
        elif annotation_read_only or read_only:
            tool.evidence_types = frozenset({EvidenceType.EXTERNAL_TOOL})

        if getattr(tool, "evidence_types", None):
            declared_policy = schema_def.get("x-nanzi-evidence-policy")
            tool.evidence_policy = (
                declared_policy
                if declared_policy in {"non_empty", "structured_success", "allow_empty_success"}
                else "allow_empty_success"
            )
        return tool

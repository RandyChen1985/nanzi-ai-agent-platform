import asyncio
import contextlib
import contextvars
import json
import keyword
import logging
import re
import time
from typing import Dict, Any, List, Optional
from pydantic import create_model, Field
from app.services.ai.tools.model_tool_name import build_model_tool_name
from app.services.ai.tools.tool_compat import StructuredTool
from app.models.mcp import McpToolCache
from app.services.ai.tools.mcp_client import McpClientService, safe_mcp_timeout_error
from app.services.ai.runtime.agentscope.tool_timeout import (
    get_active_tool_timeout_seconds,
    load_agent_max_toolcall_timeout,
    parse_agent_version_toolcall_timeout,
)
from app.services.ai.grounding.models import EvidenceType
from app.core.context import get_current_agent_context
from app.services.secrets.injection import apply_mcp_arg_bindings, denial_hint, denial_text
from app.services.secrets.redact import collect_injected_from_context
from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF, approved_arguments_digest
from app.services.secrets.mcp_capability import MCP_CREDENTIAL_INFO, credential_info, model_credential_description
from app.services.secrets.mcp_model_result import ProjectedMcpResult, restricted_model_result
from app.services.secrets.mcp_profiles import (
    CredentialInvocationError, McpCredentialInvocation, model_schema, structural_schema, unbound_sensitive_args,
)

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
    """单次 MCP 工具调用的超时：优先外层工具三级解析结果，其次请求期版本快照，再回退系统全局配置。

    平台已有 ``agent_max_toolcall_timeout``（全局默认 180s，支持版本级 1–86400s
    与工具级覆盖），此前 MCP 只读版本级快照而忽略外层工具级配置，导致慢工具
    即使单独调大了工具超时仍会在底层被提前掐断。
    """
    active_timeout = get_active_tool_timeout_seconds()
    if active_timeout is not None and active_timeout > 0:
        return float(active_timeout)
    context = get_current_agent_context()
    snapshot = getattr(context, "agent_max_toolcall_timeout_seconds", None) if context else None
    if snapshot is not None:
        parsed_version_timeout = parse_agent_version_toolcall_timeout(snapshot)
        if parsed_version_timeout is not None:
            return float(parsed_version_timeout)
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
    protect_payload: bool = False,
    **identity_kwargs: Any,
):
    """调用远端工具，把 ``notifications/progress`` 变成 AgentScope 进度分片。

    产出顺序：0..N 个进度分片 → 1 个业务结果（最后一个 ``yield``）。
    进度是**快照语义**（队列满丢最旧、重复心跳被节流），消费者不能假设每帧都收到。
    """
    if protect_payload:
        try:
            result = await McpClientService.call_remote_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                protect_payload=True,
                for_model=True,
                **identity_kwargs,
            )
        except TimeoutError as timeout_exc:
            raise safe_mcp_timeout_error(timeout_exc, protected=True) from None
        except CredentialInvocationError as exc:
            yield {"status": "error", "code": exc.code}
        except Exception:
            yield {"status": "error", "code": "CREDENTIAL_TOOL_FAILED"}
        else:
            # 只接受客户端经本地字段规则筛选后的类型；原始字典不能走这条分支。
            yield result if isinstance(result, ProjectedMcpResult) else restricted_model_result()
        return

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

    # create_task 默认复制 ContextVar，子协程内部新注册的 header/token 不会自动
    # 回流到父协程；显式传入 child_ctx 并在 finally 回收，避免父层异常脱敏漏掉明文。
    child_ctx = contextvars.copy_context()
    call_task = asyncio.create_task(
        McpClientService.call_remote_tool(
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            progress_callback=_on_progress,
            timeout_seconds=timeout_seconds,
            for_model=True,
            **identity_kwargs,
        ),
        context=child_ctx,
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
        try:
            yield await call_task
        except TimeoutError as timeout_exc:
            collect_injected_from_context(child_ctx)
            raise safe_mcp_timeout_error(timeout_exc, protected=False) from None
        except CredentialInvocationError as exc:
            yield {"status": "error", "code": exc.code}
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
        collect_injected_from_context(child_ctx)


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


def _safe_field_name(param_name: str) -> str:
    """把远端参数名映射成 Pydantic 能接受的字段名。

    ``create_model`` 对字段名有硬约束：不能以下划线开头（``_id`` 会直接抛
    ``NameError: Fields must not use names with leading underscores``）、必须是
    合法标识符、不能是 Python 关键字。远端 schema 里 ``_id`` / ``$ref`` / ``2fa``
    这类名字并不罕见，一个参数名就足以让整条工具创建失败。
    """
    candidate = re.sub(r"[^0-9a-zA-Z_]+", "_", str(param_name or "")).strip("_")
    if not candidate:
        candidate = "value"
    if candidate[0].isdigit():
        candidate = f"field_{candidate}"
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_"
    if candidate.startswith("model_"):
        candidate = f"field_{candidate}"
    return candidate


def credential_param_names(tool_record: Any) -> list[str]:
    """工具上声明「值由凭据中心提供」的参数名。

    解析失败按「没声明」处理：宁可让模型看到一个多余的参数，也不能因为存储里
    半截 JSON 就把工具定义整个搞坏——那会让这个工具彻底不可用。
    """
    raw = getattr(tool_record, "credential_params", None)
    if not raw:
        return []
    try:
        names = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        logger.warning(
            "[McpFactory] 工具 %s 的 credential_params 不是合法 JSON，忽略",
            getattr(tool_record, "tool_name", "?"),
        )
        return []
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str) and n.strip()]


# 已知的通用参数名称可以直接解释给模型。其他远端字符串使用本地别名。
_SAFE_MODEL_FIELDS = {
    "credential_account", "query", "text", "limit", "offset", "page", "page_size",
    "folder", "start", "end", "timeout", "format", "id", "count", "name", "username", "subject", "keyword",
    "command",
}


def _alias_model_properties(schema: dict) -> dict[str, str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    new_props = {}
    aliases = {}
    for index, (name, spec) in enumerate(properties.items(), start=1):
        alias = name if name in _SAFE_MODEL_FIELDS else f"arg_{index}"
        if alias != name:
            aliases[alias] = name
        # 嵌套对象只暴露类型。来自远端的子属性名可包含任意文本。
        if isinstance(spec, dict) and spec.get("type") in ("object", "array"):
            spec = {"type": spec["type"]}
        new_props[alias] = spec
    schema["properties"] = new_props
    if isinstance(schema.get("required"), list):
        by_source = {value: key for key, value in aliases.items()}
        schema["required"] = [by_source.get(name, name) for name in schema["required"]]
    return aliases


class McpToolFactory:
    @staticmethod
    def create_tool(
        tool_record: McpToolCache, *, credential_profile=None, credential_type_name: str | None = None,
        verify_profile: bool = False, parameter_binding_modes: dict[str, str] | None = None,
    ) -> StructuredTool:
        """
        Creates a runtime StructuredTool-compatible wrapper from a cached MCP tool record.
        """
        
        # 1. Parse JSON Schema from MCP
        remote_schema = json.loads(tool_record.parameter_schema or "{}")
        _credential_args = [] if credential_profile else credential_param_names(tool_record)
        unmapped = unbound_sensitive_args(remote_schema, _credential_args) if credential_profile is None else set()
        if credential_profile is not None:
            schema_def = model_schema(remote_schema, credential_profile, type_name=credential_type_name)
        elif verify_profile:
            schema_def = structural_schema({**remote_schema, "type": "object"})
        else:
            schema_def = remote_schema
        properties = schema_def.get("properties", {})
        required_fields = set(schema_def.get("required", []))
        public_credential = credential_info(
            credential_profile, credential_type_name, _credential_args, parameter_binding_modes,
        )
        description = (
            model_credential_description(tool_record.tool_name, public_credential)
            if verify_profile or credential_profile or _credential_args else (tool_record.tool_description or "")
        )

        # 声明过「值由凭据提供」的参数从模型可见的参数里剔除：留着的话模型会
        # 自己编一个值填进去，而平台随后又要用凭据覆盖它，等于骗了模型一次，
        # 日志里也看不出这个值到底是谁给的。
        #
        # 要改的是**两份** schema：
        #   - ``args_schema``：本地调用校验用；
        #   - ``schema_def``（→ ``tool.mcp_input_schema``）：这才是真正送给模型的
        #     参数表——``_schema_from_legacy_tool`` 与 ``legacy_tools_to_openai_schemas``
        #     两处都优先读它，只在 args_schema 上做改动，模型什么也看不到。
        protected_args = set(_credential_args) | unmapped
        for _name in protected_args:
            properties.pop(_name, None)
            if not isinstance(schema_def.get("properties"), dict):
                # 扁平形态：顶层就是参数表，上面的 pop 改的是新 dict，没回写
                schema_def.pop(_name, None)
            required_fields.discard(_name)
        if protected_args and isinstance(schema_def.get("required"), list):
            remaining = [n for n in schema_def["required"] if n not in protected_args]
            if remaining:
                schema_def["required"] = remaining
            else:
                # 空 required 留着只会让模型以为「必填但没定义」
                schema_def.pop("required", None)

        fields = {}
        alias_to_param = _alias_model_properties(schema_def) if verify_profile and credential_profile is None else {}
        properties = schema_def.get("properties", {})
        required_fields = set(schema_def.get("required", []))
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

            alias = _safe_field_name(param_name)
            base_alias = alias
            suffix = 2
            while alias in fields or (alias != param_name and alias in properties):
                alias = f"{base_alias}_{suffix}"
                suffix += 1
            if alias != param_name:
                alias_to_param[alias] = param_name

            fields[alias] = (field_type, Field(default=p_default, description=p_desc))
        
        # 凭据参数被剔除了，但「规则没匹配上时让模型挑一条」需要一条回传通道：
        # 固定绑定不需要编号；规则和策略未知的参数保留兼容通道。候选清单
        # 不放入可能被复用的工具定义，始终按本次用户权限查询。
        # 候选从 ``list_my_credentials`` 拿，两边用同一套编号（排序后的序号）。
        from app.services.secrets.locator import choice_field

        for index, _name in enumerate(_credential_args, start=1):
            if (parameter_binding_modes or {}).get(_name) == "fixed":
                continue
            _alias = f"credential_choice_{index}" if verify_profile else choice_field(_name)
            if _alias in fields:
                continue
            if verify_profile:
                alias_to_param[_alias] = choice_field(_name)
            _choice_desc = (
                "可选凭据编号，由 list_my_credentials 提供。只在平台规则未命中且允许选择时生效。"
            )
            fields[_alias] = (
                Optional[str],
                Field(default=None, description=_choice_desc),
            )
            # 同一个字段必须也写进 ``mcp_input_schema``。这是模型唯一能看到的
            # 参数表：只加进 args_schema 的话，``on_miss=model`` 里那句
            # 「让模型挑一条」永远等不到编号——模型不知道有这个口子，
            # 只会回一句「凭据参数我看不到、也不能猜」。
            _schema_props = schema_def.get("properties")
            if isinstance(_schema_props, dict):
                _schema_props.setdefault(
                    _alias, {"type": "string", "description": _choice_desc}
                )
            else:
                schema_def.setdefault(
                    _alias, {"type": "string", "description": _choice_desc}
                )

        # Create dynamic Pydantic model for args
        model_name = build_model_tool_name(tool_record.tool_name)
        args_schema = create_model(f"{model_name}Args", **fields)
        
        # 2. Define execution logic
        async def _execute(**kwargs) -> Any:
            if unmapped:
                yield {"status": "error", "code": "CREDENTIAL_MAPPING_REQUIRED"}
                return
            original_digest = approved_arguments_digest(kwargs) if credential_profile is not None else None
            # 字段别名 → 远端原始参数名（只有不合法名字才会出现在映射里）
            if alias_to_param:
                kwargs = {
                    alias_to_param.get(key, key): value
                    for key, value in kwargs.items()
                }

            # Extract raw tool name (remove our prefix)
            # Full name: "server_name:raw_tool_name"
            if ":" in tool_record.tool_name:
                raw_name = tool_record.tool_name.split(":", 1)[1]
            else:
                raw_name = tool_record.tool_name

            user_info, agent_info = current_mcp_agent_identity()
            context = get_current_agent_context()

            # 凭据参数在模型侧没有值，这里补上。注入点在别名映射之后——槽位存的
            # 是远端原始参数名，不是本地安全别名。
            if _credential_args and not verify_profile:
                arg_denials: Dict[str, str] = {}
                kwargs = await apply_mcp_arg_bindings(
                    tool_record, kwargs, user_info=user_info, denials=arg_denials
                )
                # 编号字段是给平台看的，绝不能发给远端：远端收到未知参数会报错。
                # 必须在上面的注入之后剥——注入层要从这里读模型的选择。
                if _credential_args:
                    from app.services.secrets.locator import choice_field

                    for _name in _credential_args:
                        kwargs.pop(choice_field(_name), None)
                if arg_denials:
                    # 声明了却取不到，就是缺必需参数。必须报错，不能带着空值发出去：
                    # 远端只会回一个 401，用户看不懂缺的是哪个绑定。
                    detail = "；".join(
                        f"{slot}（{denial_text(why)}）" for slot, why in arg_denials.items()
                    )
                    hints: list[str] = []
                    for reason in arg_denials.values():
                        hint = denial_hint(reason)
                        if hint and hint not in hints:
                            hints.append(hint)
                    message = (
                        f"MCP 工具 {tool_record.tool_name} 的凭据参数缺失：{detail}"
                    )
                    message += ("。" + "；".join(hints)) if hints else ""
                    # 只有「压根没绑」才该去绑；域名/场景不对时去绑一条也解决不了
                    if not hints:
                        message += "。请到「我的 MCP → 平台服务」绑定对应凭据"
                    raise ValueError(message + "。")

            identity_kwargs: Dict[str, Any] = {}
            if user_info:
                identity_kwargs["user_info"] = user_info
            if agent_info:
                identity_kwargs["agent_info"] = agent_info
            if context and context.trace_id:
                identity_kwargs["request_id"] = context.trace_id
            if verify_profile:
                identity_kwargs["credential_call"] = McpCredentialInvocation(
                    tool_record.id, credential_profile.revision if credential_profile else None,
                    profile_id=credential_profile.profile_id if credential_profile else None,
                    arguments_digest=original_digest,
                )
                identity_kwargs["conversation_id"] = getattr(context, "conversation_id", None)

            # 远端进度 → AgentScope 进度分片：中间 yield ``mcp_progress`` 分片，
            # 最后 yield 业务结果本身（与协程版 return 的完全一致）。
            async for item in stream_remote_tool_with_progress(
                server_id=tool_record.server_id,
                tool_name=raw_name,
                arguments=kwargs,
                timeout_seconds=await resolve_mcp_tool_timeout(),
                require_user_context=True,
                protect_payload=bool(_credential_args or credential_profile),
                **identity_kwargs,
            ):
                yield item
        
        _execute.__doc__ = description or f"MCP tool: {tool_record.tool_name}"
        
        # 数据库存储的冒号名称仅作平台标识；模型侧使用合法别名，执行闭包仍保留原始 MCP 名称。
        tool = StructuredTool.from_function(
            func=None,
            coroutine=_execute,
            name=model_name,
            description=description,
            args_schema=args_schema
        )
        tool.display_name = tool_record.tool_name
        # 保留远端原始 Schema；args_schema 仅负责兼容本地参数校验，不能替代嵌套约束。
        tool.mcp_input_schema = schema_def
        # 逐参数绑定可随时变更，依赖其策略裁剪的模型参数表必须在加载时重建。
        tool.has_mcp_parameter_bindings = bool(_credential_args)
        tool.runtime_metadata = {MCP_CREDENTIAL_INFO: public_credential}
        if credential_profile is not None:
            tool.runtime_metadata[MCP_CREDENTIAL_REF] = {
                "tool_id": tool_record.id, "profile_id": credential_profile.profile_id,
                "revision": credential_profile.revision,
                "selection_mode": credential_profile.selection_mode,
            }

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

import logging
import time
import httpx
import json
from typing import Dict, Any, Mapping, Type
from pydantic import create_model, Field
from app.services.ai.tools.tool_compat import StructuredTool
from app.services.ai.tools.user_placeholders import (
    contains_user_placeholder,
    current_user_variables,
    replace_headers_user_placeholders,
    replace_user_placeholders,
    resolve_user_variables,
)
from app.models.tool import SysApiTool
from app.services.ai.grounding.models import EvidenceType

logger = logging.getLogger(__name__)


def _parse_parameter_schema(
    schema_def: Any,
    *,
    tool_name: str = "",
) -> tuple[Dict[str, Any], Dict[str, Any], set[str]]:
    """兼容三种 schema 写法，返回 ``(顶层, 参数表, 必填集合)``。

    标准 JSON Schema（``properties`` + ``required``）、扁平 map、极简
    ``"param": "描述"`` 都由 ``create_tool`` 与运行时占位符替换共用，
    避免两处各解析一遍导致口径漂移。
    """
    if isinstance(schema_def, str):
        try:
            schema_def = json.loads(schema_def)
        except Exception as e:
            logger.warning(f"Failed to parse parameter_schema for tool {tool_name}: {e}")
            schema_def = {}
    if not isinstance(schema_def, dict):
        schema_def = {}

    properties = schema_def
    required_fields: set[str] = set()
    if "properties" in schema_def and isinstance(schema_def["properties"], dict):
        properties = schema_def["properties"]
        if "required" in schema_def and isinstance(schema_def["required"], list):
            required_fields = set(schema_def["required"])
    return schema_def, properties, required_fields


def _apply_user_placeholders(
    config: SysApiTool,
    params: Dict[str, Any],
    variables: Mapping[str, Any],
) -> Dict[str, Any]:
    """替换参数值里的 ``{user.xxx}``，并补上含占位符的默认值。

    模型没传某个参数时，标准 JSON Schema 的 ``default`` 不会自动落进
    ``kwargs``（``StructuredTool`` 不做 pydantic 校验）；含占位符的默认值
    在这里补，运行时才能真正按当前用户取值。
    """
    _schema_def, properties, _required_fields = _parse_parameter_schema(
        config.parameter_schema,
        tool_name=config.name,
    )
    prepared = dict(params)

    for name, definition in properties.items():
        param_type = definition.get("type") if isinstance(definition, dict) else None
        default = definition.get("default") if isinstance(definition, dict) else None
        has_placeholder_default = contains_user_placeholder(default)

        missing = name not in prepared or prepared[name] is None
        if (
            not missing
            and has_placeholder_default
            and isinstance(prepared[name], str)
            and not prepared[name].strip()
        ):
            # 模型偶尔会回一个空串而不是省略参数；有占位符默认值时按「没填」处理，
            # 否则会把空条件发给外部接口。
            missing = True

        if missing:
            if has_placeholder_default:
                prepared[name] = replace_user_placeholders(
                    default,
                    variables,
                    expected_type=param_type,
                )
            continue
        prepared[name] = replace_user_placeholders(
            prepared[name],
            variables,
            expected_type=param_type,
        )

    for name in list(prepared):
        if name in properties:
            continue
        prepared[name] = replace_user_placeholders(prepared[name], variables)
    return prepared


class UnsupportedMethodError(Exception):
    """运行时遇到不支持的方法时抛出。

    单独成一个类型（而不是在请求分支里直接 return 字符串）有两个原因：
    1. ``_perform_request`` 的返回类型得以保持「一定是 response」；
    2. ``_execute_request`` 能逐字节复现历史文案 ``[Error] Unsupported method: XXX``，
       试跑端点也能把它归成 ``unsupported_method`` 而不是笼统的连接失败。
    """

    def __init__(self, method: str):
        self.method = method
        super().__init__(f"Unsupported method: {method}")


class GenericApiToolFactory:
    @staticmethod
    def create_tool(tool_config: SysApiTool) -> StructuredTool:
        """
        Creates a runtime StructuredTool-compatible wrapper from a SysApiTool configuration.
        """
        
        # 1. Define the args schema dynamically
        fields = {}
        
        # Parse schema from JSON string if necessary
        schema_def, properties, required_fields = _parse_parameter_schema(
            tool_config.parameter_schema,
            tool_name=tool_config.name,
        )

        for param_name, param_def in properties.items():
            # Skip keys that are clearly not parameters if we were passed a raw schema but misses checks
            if param_name in ["type", "required", "$schema"] and properties is schema_def:
                 continue

            p_type = str
            p_desc = ""
            p_default = ... # Required by default
            
            if isinstance(param_def, dict):
                type_str = param_def.get("type", "string")
                if type_str == "integer":
                    p_type = int
                elif type_str == "boolean":
                    p_type = bool
                elif type_str == "number":
                    p_type = float
                
                p_desc = param_def.get("description", "")
                
                # Handling 'required'. Default is True unless specified otherwise.
                # 1. Check top-level 'required' list if it exists
                # 2. Check inline 'required' property (less standard but sometimes used)
                is_required = True
                
                if required_fields:
                    # If we have a required list, only those in it are required
                    if param_name not in required_fields:
                        is_required = False
                else:
                    # Fallback: check inline 'required' (default True)
                    # Note: In standard JSON schema, fields are optional by default if 'required' list is missing,
                    # but for tool definitions we usually want strict inputs. keeping default True for safety.
                    if not param_def.get("required", True):
                        is_required = False
                
                default_value = param_def.get("default", None)
                if contains_user_placeholder(default_value):
                    # 不把 {user.xxx} 原样当作默认值写进工具 schema 给模型看：
                    # 模型很可能照抄这段文本，且它本来就不是可用的字面量。
                    # 真正的替换在 _perform_request 里按当前用户完成。
                    p_desc = f"{p_desc}（未提供时按当前用户信息自动填充）".strip()
                    if not is_required:
                        p_default = None
                elif not is_required:
                    p_default = default_value
            else:
                # Simple case: "param_name": "description"
                p_desc = str(param_def)
                # Simple case assumes required
            
            fields[param_name] = (p_type, Field(default=p_default, description=p_desc))
        
        # Create Pydantic model
        args_schema = create_model(f"{tool_config.name}Args", **fields)
        
        # 2. Define the execution function
        async def _execute(**kwargs) -> Any:
            return await GenericApiToolFactory._execute_request(tool_config, kwargs)
        
        _execute.__doc__ = tool_config.description or f"Execute {tool_config.name}"
        
        tool = StructuredTool.from_function(
            func=None,
            coroutine=_execute,
            name=tool_config.name,
            description=tool_config.description or "",
            args_schema=args_schema
        )
        # Generic API 配置表中的接口统一视为只读查询，包括使用 POST
        # 携带复杂查询条件的接口；写操作不应通过该表注册。
        tool.is_read_only = True
        tool.permission_scope = "read"
        declared_types = set()
        for value in schema_def.get("x-nanzi-evidence-types") or []:
            try:
                declared_types.add(EvidenceType(value))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid evidence type %r for %s", value, tool_config.name)
        if declared_types:
            tool.evidence_types = frozenset(declared_types)
        elif tool.is_read_only:
            tool.evidence_types = frozenset({EvidenceType.EXTERNAL_TOOL})
        if getattr(tool, "evidence_types", None):
            declared_policy = schema_def.get("x-nanzi-evidence-policy")
            tool.evidence_policy = (
                declared_policy
                if declared_policy in {"non_empty", "structured_success", "allow_empty_success"}
                else "allow_empty_success"
            )
        return tool

    @staticmethod
    async def _perform_request(
        config: SysApiTool,
        params: Dict[str, Any],
        *,
        raise_on_http_error: bool = True,
        user_info: Mapping[str, Any] | None = None,
    ) -> tuple[Any, int, str]:
        """发一次请求的公共内核：用户变量替换 + 路径替换 + 参数落位 + headers + 超时。

        抽出来的唯一目的，是让「保存前试跑」与智能体运行时**走同一段代码**，
        避免出现「试跑通过、运行时却失败」的口径分裂。

        ``user_info`` 只在试跑等 HTTP 入口显式传入（当前登录用户）；运行时
        不传，从 ``AgentContext`` / ``current_user_info`` 读取。

        返回 ``(response, elapsed_ms, final_url)``。
        ``raise_on_http_error=True``（运行时默认）时，非 2xx 抛 ``RuntimeError``，
        文案与历史行为逐字节一致；试跑传 ``False``，以取得原始 status 与 body
        用于分类展示。连接层异常一律原样抛出，由调用方决定怎么呈现。
        """
        # 0. 用户变量：必须先于路径替换，否则默认值里的 {user.id} 会被原样
        #    拼进 URL。变量取不到时抛 UserPlaceholderError，由调用方呈现。
        variables = (
            resolve_user_variables(user_info)
            if user_info is not None
            else current_user_variables()
        )
        params = _apply_user_placeholders(config, params, variables)

        url = config.url_template

        # 1. Path Substitution
        path_params = {}
        for key, value in params.items():
            placeholder = f"{{{key}}}"
            if placeholder in url:
                url = url.replace(placeholder, str(value))
                path_params[key] = value

        # Filter out path params from remaining params
        remaining_params = {k: v for k, v in params.items() if k not in path_params}

        # 2. Headers
        headers = {}
        if config.headers:
            h_config = config.headers
            if isinstance(h_config, str):
                try:
                    h_config = json.loads(h_config)
                except Exception:
                    h_config = {}
            if isinstance(h_config, dict):
                headers.update(h_config)
        if headers:
            headers = replace_headers_user_placeholders(headers, variables)

        # 3. Request
        method = config.method.upper()

        logger.info(f"[GenericTool] Executing {config.name} ({method} {url}) with params: {remaining_params}")

        started = time.monotonic()
        # We disable trust_env=True to avoid SOCKS proxy issues if dependency is missing
        # If explicit proxy is needed, it should be configured in settings or handled via specific client config.
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            if method == "GET":
                response = await client.get(url, params=remaining_params, headers=headers)
            elif method in ["POST", "PUT", "PATCH", "DELETE"]:
                # Default to JSON body for non-GET
                response = await client.request(method, url, json=remaining_params, headers=headers)
            else:
                raise UnsupportedMethodError(method)

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if raise_on_http_error and not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:500]}"
            )

        return response, elapsed_ms, url

    @staticmethod
    async def _execute_request(config: SysApiTool, params: Dict[str, Any]) -> Any:
        """
        Executes the HTTP request.
        """
        try:
            response, _elapsed_ms, _url = await GenericApiToolFactory._perform_request(
                config, params
            )

            if response.status_code == 204 or not response.content:
                return {"success": True, "content": ""}

            # Try to format JSON result
            try:
                resp_json = response.json()
                return json.dumps(resp_json, ensure_ascii=False)
            except Exception:
                return response.text

        except UnsupportedMethodError as e:
            # 历史文案：``[Error] Unsupported method: XXX``
            return f"[Error] {e}"
        except Exception as e:
            logger.error(f"[GenericTool] Execution failed: {e}")
            return f"[Execution Error] {str(e)}"

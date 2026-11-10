import logging
import time
import httpx
import json
from typing import Dict, Any, Mapping, Optional, Type
from pydantic import create_model, Field
from app.services.ai.tools.request_signing import (
    parse_signing_config,
    sign_request,
    validate_no_reserved_signing_headers,
)
from app.services.ai.tools.system_params import apply_system_params, credential_param_names
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
from app.services.ai.tools.parameter_schema import (
    apply_user_placeholders as _apply_user_placeholders_impl,
    parse_parameter_schema as _parse_parameter_schema,
)
from app.services.ai.tools.tool_credentials import unresolved_credential_headers
from app.services.secrets.injection import apply_api_tool_bindings, apply_api_tool_param_bindings
from app.services.secrets.redact import redact_value

logger = logging.getLogger(__name__)


def _apply_user_placeholders(
    config: SysApiTool,
    params: Dict[str, Any],
    variables: Mapping[str, Any],
) -> Dict[str, Any]:
    """按当前用户替换参数里的 ``{user.xxx}``（实现见 ``parameter_schema`` 模块）。

    保留这个同签名包装：契约测试以 ``_apply_user_placeholders(config, params, variables)``
    这一行为锚点，确保占位符处理发生在路径替换之前；脚本工具用的是同一个实现。
    """
    return _apply_user_placeholders_impl(
        config.parameter_schema,
        params,
        variables,
        tool_name=config.name,
    )


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


class UnresolvedCredentialError(Exception):
    """工具还带着导入时的 ``${CREDENTIAL:<name>}`` 占位符，或凭据参数没注入成功。

    导入的工具默认没有凭据（导出侧永不带走密钥），若使用者启用前没补，这里必须
    明确拦住。把占位符原样发给对方服务只会得到一个莫名其妙的 401，让人以为是
    「接口变了」。
    """

    #: ``denials`` 的 key 是**槽位**（``header:Authorization`` / ``query:token`` /
    #: ``body:password`` / ``path:applicant``），调用方手里只有名字。
    #: 不按前缀挨个试，参数级的拒绝原因就永远查不到——表现是「明明是参数缺凭据，
    #: 却让用户去改 header」，真正的原因（规则未命中 / 过期 / 停用）全被吞掉。
    SLOT_PREFIXES = ("header:", "query:", "body:", "path:", "")

    def __init__(self, tool_name: str, names: list[str], denials: dict[str, str] | None = None):
        self.tool_name = tool_name
        self.names = list(names)
        #: 兼容旧名：它原本只表示 header 名，现在也可能是凭据参数名
        self.headers = self.names
        # 有拒绝原因时要说清是「过期了」还是「被停用了」——这两种情况用户要做的事
        # 完全不同（轮换 vs 找管理员恢复），都笼统说成「还没填」等于没说。
        denials = denials or {}
        reasons: dict[str, str] = {}
        for name in self.names:
            for prefix in self.SLOT_PREFIXES:
                text = denials.get(f"{prefix}{name}") if prefix else denials.get(name)
                if text:
                    reasons[name] = text
                    break
        if reasons:
            from app.services.secrets.injection import denial_hint, denial_text

            detail = "；".join(
                f"{name}（{denial_text(reason)}）" for name, reason in reasons.items()
            )
            self.reasons = reasons
            # 按原因给具体的修复动作：域名白名单不对时凭据状态是正常的，
            # 一句「检查状态」会把人引到错误的页面。
            hints: list[str] = []
            for reason in reasons.values():
                hint = denial_hint(reason)
                if hint and hint not in hints:
                    hints.append(hint)
            message = f"工具 {tool_name} 的请求凭据没有注入：{detail}"
            if hints:
                message += "。" + "；".join(hints)
            super().__init__(message + "。也可以在工具里改用固定值。")
        else:
            self.reasons = {}
            names = "、".join(self.names)
            # 别点名「header」：这里也可能是参数级凭据，指错位置比不指更费时间
            super().__init__(
                f"工具 {tool_name} 的请求凭据还没填：{names}。"
                "请在「工具管理」里把这几处改成真实值，或给它绑定一条凭据后再调用"
            )


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

        # 凭据参数在模型侧没有值可填（值由平台注入），但「规则没匹配上时让模型
        # 挑一条」需要一条回传通道：给每个凭据参数配一个可选编号字段。
        # 编号来自 ``list_my_credentials`` 的返回，两边用同一套排序（secret_key → id），
        # 与 MCP 工具参数那条链路逐位一致。
        # **不在这里注入候选清单**：工具定义可能被缓存复用，而候选按用户可见范围算。
        try:
            _credential_names = credential_param_names(
                getattr(tool_config, "parameter_schema", None)
            )
        except Exception as exc:  # noqa: BLE001 - 坏配置不该让整个工具定义构建失败
            logger.warning(
                "[GenericApi] 工具 %s 的系统参数配置不合法，跳过凭据编号字段: %s",
                getattr(tool_config, "name", "?"),
                exc,
            )
            _credential_names = []

        if _credential_names:
            from app.services.secrets.locator import choice_field

            for _name in _credential_names:
                _alias = choice_field(_name)
                if _alias in fields:
                    continue
                fields[_alias] = (
                    Optional[str],
                    Field(
                        default=None,
                        description=(
                            f"可选。{_name} 用哪条凭据的编号（例如 c3），编号来自 "
                            "list_my_credentials 工具的返回。只在平台规则没能自动匹配到"
                            "凭据时才生效；拿不准就留空，平台会按规则自己去匹配。"
                        ),
                    ),
                )
        
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
        capture: dict[str, Any] | None = None,
    ) -> tuple[Any, int, str]:
        """发一次请求的公共内核：用户变量替换 + 路径替换 + 参数落位 + headers + 超时。

        抽出来的唯一目的，是让「保存前试跑」与智能体运行时**走同一段代码**，
        避免出现「试跑通过、运行时却失败」的口径分裂。

        ``user_info`` 只在试跑等 HTTP 入口显式传入（当前登录用户）；运行时
        不传，从 ``AgentContext`` / ``current_user_info`` 读取。

        ``capture`` 仅试跑传入：在真正发出请求前写入最终 method / URL / query /
        body / headers，供界面展示「实际请求」。它不影响运行时路径，也不代替
        正常日志；调用方负责对 headers 等敏感值脱敏后再返回给前端。

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
        params = apply_system_params(config.parameter_schema, params, variables)

        method = config.method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise UnsupportedMethodError(method)

        # 凭据参数必须**先于**路径替换注入：`apply_system_params` 会把声明为
        # 「来自凭据」的参数摘掉（它不能塞空串），URL 里 `{applicant}` 就再也
        # 拿不到值，字面量会被原样发出去。
        credential_params = credential_param_names(getattr(config, "parameter_schema", None))
        if credential_params:
            param_denials: dict[str, str] = {}
            params = await apply_api_tool_param_bindings(
                config, params, method=method, user_info=user_info, denials=param_denials
            )
            unresolved_params = [name for name in credential_params if name not in params]
            if unresolved_params:
                # 和 header 一样的口径：宁可在这里说清楚，也不要发出去换一个 401
                raise UnresolvedCredentialError(config.name, unresolved_params, param_denials)
            # 编号字段是给平台看的，绝不能进 query/body：远端收到未知参数会报错，
            # 模型在挑哪条凭据也没有理由告诉对方。必须在上面的注入之后剥——
            # 注入层正是从这里读模型的选择。
            from app.services.secrets.locator import choice_field

            for _name in credential_params:
                params.pop(choice_field(_name), None)

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
            # 凭据中心：只补「本来就没填」的 header，手填的真实值一律不动。
            # 必须在 unresolved_credential_headers 之前——否则占位符会先被判为
            # 「未填」而抛错，绑定过的凭据根本没机会生效。
            auth_denials: dict[str, str] = {}
            headers = await apply_api_tool_bindings(
                config, headers, user_info=user_info, denials=auth_denials
            )
            pending = unresolved_credential_headers(headers)
            if pending:
                # 导入工具默认没有凭据；发出去只会换来对方的 401，不如在这里说清楚
                raise UnresolvedCredentialError(config.name, pending, auth_denials)

        # 3. Request
        signing = parse_signing_config(config)
        if signing.enabled:
            validate_no_reserved_signing_headers(headers)

        if capture is not None and not signing.enabled:
            if method == "GET":
                query = remaining_params
                body = None
                final_url = str(httpx.URL(url, params=remaining_params))
            else:
                query = {}
                body = remaining_params
                final_url = url
            capture.update({
                "method": method,
                "url": final_url,
                "headers": dict(headers),
                "query": query,
                "body": body,
            })

        logger.info(f"[GenericTool] Executing {config.name} ({method} {url}) with params: {remaining_params}")

        started = time.monotonic()
        # We disable trust_env=True to avoid SOCKS proxy issues if dependency is missing
        # If explicit proxy is needed, it should be configured in settings or handled via specific client config.
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            if signing.enabled:
                # 先让 httpx 构造最终请求，再对**实际会发出去的** path/query/body
                # 签名；不能拿 json= 序列化前的 dict 另算一份。
                request = client.build_request(
                    method,
                    url,
                    params=remaining_params if method == "GET" else None,
                    json=remaining_params if method != "GET" else None,
                    headers=headers,
                )
                raw_path = request.url.raw_path.split(b"?", 1)[0].decode("ascii")
                raw_query = request.url.query.decode("ascii")
                signature_headers = sign_request(
                    method=method,
                    raw_path=raw_path,
                    raw_query=raw_query,
                    body=request.content,
                    secret=signing.secret or "",
                    key_id=signing.key_id or "",
                )
                request.headers.update(signature_headers)
                headers = dict(request.headers)
                if capture is not None:
                    capture.update({
                        "method": method,
                        "url": str(request.url),
                        "headers": dict(headers),
                        "query": dict(remaining_params) if method == "GET" else {},
                        "body": remaining_params if method != "GET" else None,
                    })
                if method == "GET":
                    response = await client.get(str(request.url), headers=headers)
                else:
                    response = await client.request(
                        method,
                        str(request.url),
                        content=request.content,
                        headers=headers,
                    )
            elif method == "GET":
                response = await client.get(url, params=remaining_params, headers=headers)
            elif method in ["POST", "PUT", "PATCH", "DELETE"]:
                # Default to JSON body for non-GET
                response = await client.request(method, url, json=remaining_params, headers=headers)
            else:
                raise UnsupportedMethodError(method)

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if capture is not None:
            # 请求成功后用 httpx 真正构造出的 URL/Headers 覆盖预捕获值：
            # 这样连 httpx 自动补的 Content-Type、query 编码也能在试跑详情里看到。
            try:
                actual_request = response.request
            except Exception:
                actual_request = None
            if actual_request is not None:
                capture["url"] = str(actual_request.url)
                capture["headers"] = dict(actual_request.headers)

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

            # 回灌之前掩码：远端把请求参数原样回显是常见操作，而模型不该看到注入的值。
            where = f"API 工具 {config.name}"
            if response.status_code == 204 or not response.content:
                return {"success": True, "content": ""}

            # Try to format JSON result
            try:
                resp_json = response.json()
                return redact_value(
                    json.dumps(resp_json, ensure_ascii=False), where=where
                )
            except Exception:
                return redact_value(response.text, where=where)

        except UnsupportedMethodError as e:
            # 历史文案：``[Error] Unsupported method: XXX``
            return f"[Error] {e}"
        except Exception as e:
            logger.error(f"[GenericTool] Execution failed: {e}")
            return f"[Execution Error] {str(e)}"

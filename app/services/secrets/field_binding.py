"""固定字段绑定的目标指纹与只写值读取。"""

from dataclasses import dataclass
import json
import re

from app.models.credential_type import StructuredCredential
from app.services.secrets import structured_payload, type_catalog
from app.services.secrets.credential_schema import schema_digest, validate_payload
from app.services.secrets.payload_config import load_payload_config
from app.services.secrets.policy import CONSUMER_API_TOOL, CONSUMER_MCP, CONSUMER_SCRIPT, resolve_api_tool_slot

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,123}\Z")
_RESERVED_ENV = frozenset({"PATH", "HOME", "PWD", "OLDPWD", "SHELL", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "IFS"})

def valid_script_env_name(name: str) -> bool:
    return bool(_ENV_NAME.fullmatch(name)) and name.upper() not in _RESERVED_ENV and not name.upper().startswith(("LD_", "DYLD_", "PYTHON"))

_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_\x60|~0-9A-Za-z]{1,100}\Z")


@dataclass(repr=False, frozen=True)
class BoundFieldValue:
    _value: str | int | float | bool
    value_revision: int | None = None

    def reveal(self):
        return self._value


def target_digest(consumer_type: str, consumer, slot: str) -> str:
    if consumer_type == CONSUMER_SCRIPT:
        from app.services.ai.tools.script_tool_factory import requirements_of
        raw_schema = getattr(consumer, "parameter_schema", None)
        try:
            schema = json.loads(raw_schema) if isinstance(raw_schema, str) else raw_schema
        except ValueError:
            schema = raw_schema
        return schema_digest([consumer_type, getattr(consumer, "id", None), slot,
                              getattr(consumer, "code", None), list(requirements_of(getattr(consumer, "requirements", None))),
                              schema])
    if consumer_type == CONSUMER_MCP:
        return schema_digest([
            consumer_type, getattr(consumer, "id", None), getattr(consumer, "sse_url", None), slot,
            bool(getattr(consumer, "user_assertion_enabled", False)), getattr(consumer, "user_assertion_header", None),
        ])
    return schema_digest([
        consumer_type, getattr(consumer, "id", None), str(getattr(consumer, "method", "")).upper(),
        getattr(consumer, "url_template", None), slot,
        _as_mapping(getattr(consumer, "headers", None)), _as_mapping(getattr(consumer, "parameter_schema", None)),
    ])


def _as_mapping(raw) -> dict:
    try:
        result = json.loads(raw or "{}") if isinstance(raw, str) else raw
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


async def validate_binding(session, secret, consumer_type: str, consumer_id: str, slot: str,
                           field_key: str | None, actor_id: str | None) -> str:
    from app.models.mcp import McpServer
    from app.models.tool import SysApiTool
    from app.services.ai.tools.system_params import parse_system_params
    from app.services.ai.tools.tool_credentials import is_credential_placeholder

    payload = await session.get(StructuredCredential, secret.id)
    if payload is None or not field_key:
        raise ValueError("请选择类型中可绑定的字段")
    version = await type_catalog.get_version(session, payload.type_key, payload.type_version)
    source = version.schema_json["properties"].get(field_key)
    if source is None:
        raise ValueError("所选字段不属于已发布的类型版本")
    prefix, _, name = slot.partition(":")
    if prefix == "header" and not _HEADER_NAME.fullmatch(name):
        raise ValueError("请求头名称无效")

    if consumer_type == CONSUMER_MCP and prefix == "header":
        server = await session.get(McpServer, consumer_id)
        if server is None or (server.scope == "personal" and str(server.user_id) != actor_id):
            raise ValueError("MCP 服务不可用")
        if payload.type_key != "mcp_token" or field_key != "token":
            raise ValueError("MCP 连接认证使用 MCP 服务令牌类型的 token 字段")
        if server.user_assertion_enabled and name.lower() == (server.user_assertion_header or "X-Nanzi-User-Assertion").lower():
            raise ValueError("认证头与平台用户断言冲突")
        return target_digest(consumer_type, server, slot)

    if consumer_type == CONSUMER_SCRIPT:
        from app.models.script_tool import SysScriptTool
        from app.services.ai.tools.script_tool_factory import config_from_row
        from app.services.ai.tools.script_tool_builtins import get_builtin_script_tool
        if prefix != "env" or not valid_script_env_name(name):
            raise ValueError("环境变量名称无效")
        if source.get("type") not in {"string", "integer", "number", "boolean"}:
            raise ValueError("凭据字段类型不支持环境变量")
        tool = await session.get(SysScriptTool, consumer_id)
        if tool is None or not tool.is_active:
            raise ValueError("脚本工具不可用")
        builtin = get_builtin_script_tool(tool.name) if tool.source == "builtin" else None
        return target_digest(consumer_type, config_from_row(tool, builtin=builtin), slot)

    if consumer_type != CONSUMER_API_TOOL:
        raise ValueError("结构化字段仅支持 API 工具槽位及 MCP 连接认证")
    if prefix not in {"header", "body"}:
        raise ValueError("结构化字段仅支持请求头和请求体")
    tool = await session.get(SysApiTool, consumer_id)
    if tool is None or not tool.is_active:
        raise ValueError("API 工具不可用")
    if prefix == "header":
        headers = _as_mapping(tool.headers)
        if name not in headers or (headers[name] and not is_credential_placeholder(headers[name])):
            raise ValueError("请先将工具的请求头声明为凭据占位")
        expected = "string"
    else:
        declared = {spec.name: spec for spec in parse_system_params(tool.parameter_schema)}
        spec = declared.get(name)
        if spec is None or spec.source != "credential" or slot != resolve_api_tool_slot(
            name=name, method=tool.method, url_template=tool.url_template,
        ):
            raise ValueError("请先将工具参数声明为固定凭据来源")
        expected = spec.type
    if source["type"] != expected and (source["type"], expected) != ("integer", "number"):
        raise ValueError("凭据字段与工具槽位的类型不匹配")
    return target_digest(consumer_type, tool, slot)


async def resolve_field(session, parent, binding, *, user_id: str, expected_target: str | None) -> BoundFieldValue:
    if (not expected_target or binding.target_digest != expected_target or not binding.value_field
            or parent.owner_scope not in {"user", "system"}
            or not binding.slot.startswith(("header:", "body:", "env:"))
            or (parent.owner_scope == "user" and parent.owner_id != user_id)
            or (binding.bound_by_user_id and binding.bound_by_user_id != user_id)):
        raise ValueError("字段绑定的身份或目标已变化")
    row = await session.get(StructuredCredential, parent.id)
    if row is None or (row.owner_scope, row.owner_id) != (parent.owner_scope, parent.owner_id):
        raise ValueError("凭据不可用")
    version = await type_catalog.get_version(session, row.type_key, row.type_version)
    cipher = (await load_payload_config(session)).cipher()
    fields = validate_payload(version.schema_json, structured_payload.decrypt_fields(row, cipher), row.extra_schema).values
    if binding.value_field not in fields:
        raise ValueError("凭据字段未设置")
    value = fields[binding.value_field]
    if binding.slot.startswith("header:") and (not isinstance(value, str) or not value or "\n" in value or "\r" in value):
        raise ValueError("认证请求头字段无效")
    return BoundFieldValue(value, value_revision=row.value_revision)

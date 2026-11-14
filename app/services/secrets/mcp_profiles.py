"""MCP 结构化凭据配置、模型投影和受保护的出站准备。"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import load_only

from app.models.credential_type import McpCredentialProfile, McpCredentialUseGrant, StructuredCredential
from app.models.mcp import McpServer, McpToolCache
from app.models.secret import SecretBinding, SecretGrant, UserSecret
from app.models.user import User
from app.services.secrets import structured_payload, type_catalog
from app.services.secrets.credential_approval import claim_approval, matching_approval
from app.services.secrets.credential_schema import (
    CredentialSchemaError, FIELD_TYPES, SCALAR_FIELD_TYPES, account_key, schema_digest, validate_payload,
)
from app.services.secrets.injection import apply_mcp_arg_bindings, host_of, mcp_arg_slots
from app.services.secrets.mcp_model_result import compile_result_fields
from app.services.secrets.payload_config import load_payload_config
from app.services.secrets.payload_crypto import PayloadCryptoError
from app.services.secrets.policy import CONSUMER_MCP, SCENE_MCP, SecretContext, evaluate_secret
from app.services.secrets.resolver import SecretResolver, _to_record

ACCOUNT_PARAM = "credential_account"
SELECTION_MODES = {"fixed", "infer", "ask"}
_CANDIDATE_QUERY_BATCH_SIZE = 100
_CONNECTION_FIELDS = {
    "username", "host", "hostname", "port", "server", "endpoint", "url", "base_url", "token_url",
}
_AUTH_TOKEN_FIELDS = {
    "token", "access_token", "refresh_token", "auth_token", "authentication_token", "authorization_token",
    "bearer_token", "oauth_token", "oauth2_token", "id_token", "service_token", "session_token",
}
_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"}
_SCHEMA_CHILDREN = {
    "items", "prefixItems", "additionalProperties", "anyOf", "oneOf", "allOf",
    "if", "then", "else", "not", "contains", "unevaluatedProperties", "unevaluatedItems",
    "propertyNames", "additionalItems",
}
_SCHEMA_DEFINITIONS = {"$defs", "definitions"}
_REF_ANNOTATIONS = {"$comment", "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly"}
_UNSUPPORTED_BUSINESS_STRUCTURES = {
    "$ref", "$dynamicRef", "$recursiveRef", "$id", "id", "$anchor", "$dynamicAnchor", "$recursiveAnchor",
    "allOf", "if", "not", "contains", "prefixItems", "patternProperties", "propertyNames", "dependentSchemas",
    "dependencies", "unevaluatedProperties", "unevaluatedItems",
}
_MAX_MCP_SCHEMA_DEPTH = 64
_MAX_MCP_SCHEMA_NODES = 16_384
_BUSINESS_BOUNDS = {
    "minimum": operator.ge,
    "maximum": operator.le,
    "exclusiveMinimum": operator.gt,
    "exclusiveMaximum": operator.lt,
    "minLength": operator.ge,
    "maxLength": operator.le,
    "minItems": operator.ge,
    "maxItems": operator.le,
}
_BUSINESS_SCHEMA_KEYS = {"description", "enum", "const", "format"} | _BUSINESS_BOUNDS.keys()


class CredentialInvocationError(ValueError):
    def __init__(self, code: str, message: str = "凭据校验未通过", status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(repr=False, frozen=True)
class McpCredentialInvocation:
    tool_id: str
    profile_revision: int | None
    user_query: str = ""
    selected_secret_id: str | None = None
    explicit_account: str | None = None
    profile_id: str | None = None
    arguments_digest: str | None = None
    parameter_choices: tuple[tuple[str, str], ...] | None = None


@dataclass(repr=False, frozen=True)
class PreparedMcpArguments:
    arguments: dict[str, Any]
    protect_payload: bool
    model_result_fields: tuple[str, ...] = ()
    protected_values: tuple[Any, ...] = ()
    result_policy_stale: bool = False
    result_policy_error: str | None = None


def _fail(code: str, message: str = "凭据校验未通过") -> CredentialInvocationError:
    return CredentialInvocationError(code, message)


def parse_schema(tool) -> dict:
    try:
        raw = tool.parameter_schema
        schema = json.loads(raw or "{}") if not isinstance(raw, dict) else raw
        if not isinstance(schema, dict):
            raise ValueError
        return schema
    except (TypeError, ValueError):
        raise _fail("CREDENTIAL_SCHEMA_INVALID", "请同步工具后重新检查参数定义") from None


def endpoint_digest(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class _McpBusinessSchema(dict):
    """保持公开 JSON Schema 与仅供出站使用的禁用名称分离。"""

    def __init__(self, schema: dict, hidden_names: set[str]):
        super().__init__(schema)
        self.hidden_names = frozenset(hidden_names)


class _SchemaExpansionLimit(Exception):
    pass


def _local_definition_path(ref: Any) -> tuple[str, str] | None:
    if not isinstance(ref, str) or not ref.startswith("#/") or re.search(r"%(?![0-9A-Fa-f]{2})", ref):
        return None
    try:
        pointer = unquote(ref[2:], errors="strict")
    except UnicodeDecodeError:
        return None
    parts = pointer.split("/")
    if len(parts) != 2 or parts[0] not in _SCHEMA_DEFINITIONS or re.search(r"~(?![01])", parts[1]):
        return None
    return parts[0], parts[1].replace("~1", "/").replace("~0", "~")


def resolve_local_mcp_schema(tool_schema: dict) -> dict:
    """在参数子 schema 内展开 MCP 常用的本地定义，不读取外部资源或改动原定义。"""
    remaining = _MAX_MCP_SCHEMA_NODES

    def expand(spec: Any, active: frozenset[tuple[str, str]], depth: int, local: bool) -> tuple[Any, bool]:
        nonlocal remaining
        remaining -= 1
        if depth > _MAX_MCP_SCHEMA_DEPTH or remaining < 0:
            raise _SchemaExpansionLimit
        if isinstance(spec, list):
            children = [expand(item, active, depth + 1, local) for item in spec]
            return [child for child, _ in children], any(invalid for _, invalid in children)
        if not isinstance(spec, dict):
            return spec, False

        # 嵌套资源会重设引用基址。只识别指向当前输入文档的引用。
        local = local and (depth == 0 or "$id" not in spec and "id" not in spec)
        if "$ref" in spec:
            path = _local_definition_path(spec["$ref"]) if local else None
            if path is None or path in active:
                return _invalid_ref(spec), True
            collection = tool_schema.get(path[0])
            target = collection.get(path[1]) if isinstance(collection, dict) else None
            if not isinstance(target, dict) and type(target) is not bool:
                return _invalid_ref(spec), True
            resolved, invalid = expand(target, active | {path}, depth + 1, local)
            if invalid or spec.keys() - {"$ref"} - _REF_ANNOTATIONS:
                return _invalid_ref(spec, resolved), True
            if not isinstance(resolved, dict):
                return resolved, False
            annotations = {name: spec[name] for name in _REF_ANNOTATIONS & spec.keys()}
            if resolved.get("writeOnly") is True:
                annotations["writeOnly"] = True
            return {**resolved, **annotations}, False

        result = {}
        invalid_reference = False
        invalid_alternative = False
        for key, value in spec.items():
            if key in _SCHEMA_DEFINITIONS:
                continue
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                children = {name: expand(child, active, depth + 1, local) for name, child in value.items()}
                result[key] = {name: child for name, (child, _) in children.items()}
                invalid_reference |= any(invalid for _, invalid in children.values())
            elif key in _SCHEMA_CHILDREN:
                result[key], invalid = expand(value, active, depth + 1, local)
                invalid_reference |= invalid
                invalid_alternative |= invalid and key in {"anyOf", "oneOf"}
            else:
                result[key] = value
        # 未能解析的分支无法判断是否包含认证约束，不能交给宽松分支兜底。
        return (_invalid_ref(result), True) if invalid_alternative else (result, invalid_reference)

    def _invalid_ref(spec: dict, resolved: Any = None) -> dict:
        result: dict[str, Any] = {"not": {}}
        if _has_nested_binding_fields(spec) or _has_nested_binding_fields(resolved):
            result["writeOnly"] = True
        return result

    try:
        resolved, _ = expand(tool_schema, frozenset(), 0, True)
        return resolved if isinstance(resolved, dict) else {"not": {}}
    except _SchemaExpansionLimit:
        return {"not": {}}


def _normalize_arg_name(name: str) -> str:
    # 同时处理 accessToken、APIToken、api-key 这类远端命名。
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_").lower()


def _is_sensitive_arg(name: str, spec: Any) -> bool:
    if isinstance(spec, dict) and (spec.get("writeOnly") is True or spec.get("format") == "password"):
        return True
    normalized = _normalize_arg_name(name)
    if normalized.startswith("secret_choice_"):
        # 平台历史选择字段通过独立通道解释。远端同名字段可以是普通业务参数。
        return False
    return (
        normalized in _AUTH_TOKEN_FIELDS
        or normalized in {"authorization", "passwd", "password", "secret", "apikey", "api_key",
                          "private_key", "access_key", "client_secret", "clientsecret"}
        or normalized.endswith(("_password", "_passwd", "_secret", "_api_key", "_private_key", "_access_key"))
    )


def mcp_parameter_properties(tool_schema: dict) -> dict:
    properties = tool_schema.get("properties")
    if isinstance(properties, dict):
        return properties
    # 部分历史记录将参数定义直接存于顶层。
    if "type" not in tool_schema and "properties" not in tool_schema:
        return {
            name: spec for name, spec in tool_schema.items()
            if isinstance(spec, dict) and not name.startswith(("x-nanzi-", "$"))
        }
    return {}


def unbound_sensitive_args(tool_schema: dict, declared: list[str]) -> set[str]:
    return _unbound_sensitive_args(resolve_local_mcp_schema(tool_schema), declared)


def _unbound_sensitive_args(tool_schema: dict, declared: list[str]) -> set[str]:
    sensitive = {
        name for name, spec in mcp_parameter_properties(tool_schema).items()
        if name not in declared and group_binding_required(name, spec)
    }
    for key in ("anyOf", "oneOf"):
        branches = tool_schema.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    sensitive.update(_unbound_sensitive_args(branch, declared))
    return sensitive


def _root_schema_variants(schema: dict):
    yield schema
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    yield from _root_schema_variants(branch)


def _require_mappable_dynamic_arguments(schema: dict) -> None:
    if any(_has_nested_binding_fields(variant.get("additionalProperties")) for variant in _root_schema_variants(schema)):
        raise _fail("CREDENTIAL_MAPPING_REQUIRED", "动态认证参数需要在工具定义中声明为可绑定的字段")


def group_binding_required(name: str, spec: Any, *, authentication_connection: bool = False) -> bool:
    return (
        _is_sensitive_arg(name, spec)
        or (authentication_connection and _normalize_arg_name(name) in _CONNECTION_FIELDS)
        or _has_nested_binding_fields(spec)
    )


def _has_nested_binding_fields(spec: Any) -> bool:
    if isinstance(spec, list):
        return any(_has_nested_binding_fields(value) for value in spec)
    if not isinstance(spec, dict):
        return False
    if _is_sensitive_arg("", spec):
        return True
    for key in _SCHEMA_MAPS:
        children = spec.get(key)
        if not isinstance(children, dict):
            continue
        if key == "properties":
            if any(group_binding_required(name, value) for name, value in children.items()):
                return True
        elif any(_has_nested_binding_fields(value) for value in children.values()):
            return True
    return any(_has_nested_binding_fields(spec.get(key)) for key in _SCHEMA_CHILDREN)


def _mapping_types(schema: dict) -> set[str] | None:
    if {"$ref", "$dynamicRef", "allOf", "not", "if"} & schema.keys():
        return None
    kind = schema.get("type")
    types = kind if isinstance(kind, list) else [kind]
    if "type" in schema:
        if not types or any(not isinstance(item, str) or item not in FIELD_TYPES | {"null"} for item in types):
            return None
        declared = set(types)
    else:
        declared = None
    composites = [key for key in ("anyOf", "oneOf") if key in schema]
    if not composites:
        return declared
    if len(composites) != 1:
        return None
    keyword = composites[0]
    branches = schema[keyword]
    if not isinstance(branches, list) or not branches:
        return None
    merged: set[str] = set()
    for branch in branches:
        if not isinstance(branch, dict):
            return None
        branch_types = _mapping_types(branch)
        if branch_types is None or (keyword == "oneOf" and merged & branch_types):
            return None
        merged.update(branch_types)
    if declared is not None and not merged <= declared:
        return None
    return merged


def mapping_target_type(target_schema: dict) -> str | None:
    """归一化映射目标类型，仅接受单一非空类型及其 nullable 形式。"""
    types = (_mapping_types(target_schema) or set()) - {"null"}
    return next(iter(types)) if len(types) == 1 else None


def mapping_type_compatible(source_kind: str, target_schema: dict) -> bool:
    target_kind = mapping_target_type(target_schema)
    return source_kind == target_kind or (source_kind == "integer" and target_kind == "number")


def compile_mappings(tool_schema: dict, type_schema: dict, mappings: dict[str, str]) -> dict[str, str]:
    tool_schema = resolve_local_mcp_schema(tool_schema)
    _require_mappable_dynamic_arguments(tool_schema)
    props = tool_schema.get("properties")
    if not isinstance(props, dict) or ACCOUNT_PARAM in props:
        raise _fail("CREDENTIAL_SCHEMA_INVALID", "工具参数定义无效或含平台保留字段")
    type_fields = type_schema["properties"]
    if not isinstance(mappings, dict) or not mappings or len(mappings) > 32:
        raise _fail("CREDENTIAL_MAPPING_INVALID", "至少映射一个凭据字段，最多 32 个")
    for target, source in mappings.items():
        dest = props.get(target)
        if not isinstance(dest, dict) or not isinstance(source, str) or source not in type_fields:
            raise _fail("CREDENTIAL_MAPPING_INVALID", "映射必须引用已声明的工具参数和类型字段")
        source_kind = type_fields[source]["type"]
        # 按声明类型整体注入顶层字段，无字符串强转。
        if not mapping_type_compatible(source_kind, dest):
            raise _fail("CREDENTIAL_MAPPING_INVALID", "凭据字段和工具参数类型不匹配")
    sensitive = _unbound_sensitive_args(tool_schema, [])
    authentication_connection = bool(sensitive)
    if sensitive - mappings.keys() or any(
        group_binding_required(name, spec, authentication_connection=authentication_connection) and name not in mappings
        for name, spec in props.items()
    ):
        raise _fail("CREDENTIAL_MAPPING_REQUIRED", "敏感参数和关联连接参数需要绑定服务端凭据字段")
    return dict(mappings)


def _business_schema_metadata(spec: dict, hidden: re.Pattern | None) -> dict:
    result = {}

    def references_hidden(value: Any) -> bool:
        return hidden is not None and isinstance(value, str) and bool(hidden.search(value))

    for key in _BUSINESS_SCHEMA_KEYS:
        value = spec.get(key)
        if key in {"description", "format"}:
            if isinstance(value, str) and not references_hidden(value):
                result[key] = value
        elif key in {"enum", "const"}:
            if key not in spec or (key == "enum" and not isinstance(value, list)):
                continue
            values = value if key == "enum" else [value]
            if all(
                (item is None or type(item) in (str, int, bool) or type(item) is float and math.isfinite(item))
                and not references_hidden(item)
                for item in values
            ):
                result[key] = value
        elif type(value) is int or type(value) is float and math.isfinite(value):
            result[key] = value
    return result


def structural_schema(
    spec: Any, *, hidden: re.Pattern | None = None, excluded: frozenset[str] = frozenset(),
) -> dict:
    """保留可安全公开和校验的 MCP 业务结构。"""
    if spec is True:
        return {}
    if not isinstance(spec, dict) or _UNSUPPORTED_BUSINESS_STRUCTURES & spec.keys():
        return {"not": {}}
    if _has_nested_binding_fields(spec.get("additionalProperties")):
        return {"not": {}}
    kind = spec.get("type")
    metadata = _business_schema_metadata(spec, hidden)
    composites = [key for key in ("anyOf", "oneOf") if key in spec]
    if composites:
        if len(composites) != 1:
            return {"not": {}}
        key = composites[0]
        branches = spec[key]
        if not isinstance(branches, list) or not branches or any(not isinstance(branch, dict) for branch in branches):
            return {"not": {}}
        common = {name: value for name, value in spec.items() if name != key}
        return {
            **structural_schema(common, hidden=hidden, excluded=excluded),
            key: [structural_schema(branch, hidden=hidden, excluded=excluded) for branch in branches],
        }
    if isinstance(kind, list):
        if not kind or any(not isinstance(item, str) for item in kind):
            return {"not": {}}
        kinds = list(dict.fromkeys(kind))
        non_null = [item for item in kinds if item != "null"]
        if len(kinds) == 2 and len(non_null) == 1:
            projected = structural_schema({**spec, "type": non_null[0]}, hidden=hidden, excluded=excluded)
            return {**projected, "type": kinds}
        return {"anyOf": [
            structural_schema({**spec, "type": item}, hidden=hidden, excluded=excluded) for item in kinds
        ], **metadata}
    if kind is None:
        if {"properties", "additionalProperties", "required"} & spec.keys():
            kind = "object"
        elif "items" in spec:
            kind = "array"
        else:
            return metadata
    if not isinstance(kind, str):
        return {"not": {}}
    if kind in SCALAR_FIELD_TYPES | {"null"}:
        return {"type": kind, **metadata}
    if kind == "array":
        return {"type": "array", "items": structural_schema(spec.get("items", True), hidden=hidden), **metadata}
    if kind != "object":
        return {"not": {}}

    props = spec.get("properties", {})
    if not isinstance(props, dict):
        return {"not": {}}
    result = {
        "type": "object",
        "properties": {
            name: structural_schema(value, hidden=hidden)
            for name, value in props.items()
            if name != ACCOUNT_PARAM and name not in excluded and not group_binding_required(name, value)
        },
        **metadata,
    }
    # JSON Schema 缺省允许扩展字段。凭据字段通过独立的出站检查排除。
    additional = spec.get("additionalProperties", True)
    result["additionalProperties"] = (
        additional if type(additional) is bool else structural_schema(additional, hidden=hidden)
    )
    required = spec.get("required")
    if isinstance(required, list):
        safe_required = [
            name for name in required
            if isinstance(name, str) and name not in excluded and (
                name in result["properties"]
                or additional is not False and name not in props and name != ACCOUNT_PARAM
                and not _is_sensitive_arg(name, {})
            )
        ]
        if safe_required:
            result["required"] = safe_required
    return result


def business_mcp_schema(tool_schema: dict, protected: set[str]) -> dict:
    """排除服务端参数，给模型和出站校验共用同一份业务定义。"""
    return _business_mcp_schema(resolve_local_mcp_schema(tool_schema), protected)


def _business_mcp_schema(tool_schema: dict, protected: set[str]) -> dict:
    hidden = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(name) for name in sorted(protected)) + r")(?!\w)",
        re.IGNORECASE,
    ) if protected else None
    properties = mcp_parameter_properties(tool_schema)
    root = {
        **tool_schema, "type": "object",
        "properties": {name: spec for name, spec in properties.items() if name not in protected},
        "required": [
            name for name in tool_schema.get("required") or []
            if isinstance(name, str) and name not in protected
        ],
    }
    if protected:
        root.pop("description", None)
    result = structural_schema(root, hidden=hidden, excluded=frozenset(protected))
    result.setdefault("properties", {})
    return _McpBusinessSchema(result, protected)


def model_schema(tool_schema: dict, profile: McpCredentialProfile, *, type_name: str | None = None) -> dict:
    tool_schema = resolve_local_mcp_schema(tool_schema)
    sensitive = _unbound_sensitive_args(tool_schema, [])
    authentication_connection = bool(sensitive)
    protected = set(profile.mappings) | sensitive | {
        name for name, spec in mcp_parameter_properties(tool_schema).items()
        if group_binding_required(name, spec, authentication_connection=authentication_connection)
    }
    # 组级账号选择字段仅用于平台，根级描述也可能包含真实凭据字段。
    public = _business_mcp_schema({**tool_schema, "description": None}, protected)
    if profile.selection_mode == "infer":
        label = f"{type_name}（{profile.type_key}）" if type_name else profile.type_key
        public["properties"][ACCOUNT_PARAM] = {
            "type": "string",
            "description": f"凭据中心的账号标识，绑定类型为 {label} v{profile.type_version}。"
                           "可依据用户消息、历史、邮件、网页或工具结果选择完整账号。"
                           "不确定时使用 list_mcp_credential_candidates 查询本工具的候选，"
                           "仍无法确定则留空。平台会校验权限并在服务端注入凭据。",
        }
    return public


def _matches_business_type(value: Any, kind: str) -> bool:
    return (
        (kind == "null" and value is None)
        or (kind == "boolean" and type(value) is bool)
        or (kind == "integer" and type(value) is int)
        or (kind == "number" and type(value) in (int, float))
        or (kind == "string" and isinstance(value, str))
        or (kind == "object" and isinstance(value, dict))
        or (kind == "array" and isinstance(value, list))
    )


def _public_mcp_arguments(value: Any, schema: dict) -> Any:
    """按公开业务结构构造独立出站对象。"""
    hidden = schema.hidden_names if isinstance(schema, _McpBusinessSchema) else frozenset()
    return _project_mcp_value(value, schema, hidden)


def _business_field_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_business_field_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_business_field_count(item) for item in value)
    return 0


def _check_business_bounds(value: Any, schema: dict) -> None:
    if type(value) in (int, float):
        actual = value
        keys = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
    elif isinstance(value, str):
        actual = len(value)
        keys = ("minLength", "maxLength")
    elif isinstance(value, list):
        actual = len(value)
        keys = ("minItems", "maxItems")
    else:
        return
    if any(not _BUSINESS_BOUNDS[key](actual, schema[key]) for key in keys if key in schema):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数超出已声明的数值或长度范围")


def _project_mcp_value(value: Any, schema: dict, hidden: frozenset[str]) -> Any:
    if schema.get("not") == {}:
        raise _fail("MCP_ARGUMENTS_INVALID", "不支持工具声明的业务结构")
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if _matches_business_type(value, item)), None)
        if kind is None:
            raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
    keyword = "oneOf" if "oneOf" in schema else "anyOf"
    branches = schema.get(keyword)
    if branches:
        common = {name: part for name, part in schema.items() if name != keyword}
        common_value = _project_mcp_value(value, common, hidden)
        projected = []
        for branch in branches:
            try:
                candidate = _project_mcp_value(common_value, branch, hidden)
                projected.append(_project_mcp_value(candidate, common, hidden))
            except CredentialInvocationError:
                continue
        if not projected:
            raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
        # 优先保留合法业务字段，比较时计入嵌套对象中实际保留的键。
        candidates = sorted(
            ((_business_field_count(item), item) for item in projected), key=lambda pair: pair[0], reverse=True,
        )
        if keyword == "oneOf" and len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise _fail("MCP_ARGUMENTS_INVALID", "工具参数匹配到多种业务结构")
        return candidates[0][1]
    if kind is not None and not _matches_business_type(value, kind):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
    if ("const" in schema and value != schema["const"]) or ("enum" in schema and value not in schema["enum"]):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数不在允许的业务范围内")
    _check_business_bounds(value, schema)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        if any(name not in value for name in schema.get("required", ()) if name != ACCOUNT_PARAM):
            raise _fail("MCP_ARGUMENTS_INVALID", "缺少已声明的业务参数")
        outgoing = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
            if name == ACCOUNT_PARAM:
                continue
            child = properties.get(name)
            if child is None and (name in hidden or _is_sensitive_arg(name, {})):
                continue
            if child is None:
                if additional is False:
                    continue
                child = additional if isinstance(additional, dict) else {}
            outgoing[name] = _project_mcp_value(item, child, hidden)
        return outgoing
    if isinstance(value, list):
        return [_project_mcp_value(child, schema.get("items", {}), hidden) for child in value]
    if not (
        value is None or type(value) in (str, int, bool) or type(value) is float and math.isfinite(value)
    ):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
    return value


def validate_model_result_fields(value: Any) -> tuple[list[str], str | None]:
    if value is None:
        return [], None
    try:
        compile_result_fields(value)
    except ValueError as exc:
        return [], str(exc)
    return list(dict.fromkeys(value)), None


def tool_result_target(tool, server) -> str:
    return schema_digest({
        "server_id": server.id,
        "tool_name": tool.tool_name,
        "endpoint": endpoint_digest(server.sse_url),
        "schema": parse_schema(tool),
        "credential_params": getattr(tool, "credential_params", None),
        "credential_mode": getattr(server, "credential_mode", None),
        "user_assertion_enabled": bool(getattr(server, "user_assertion_enabled", False)),
    })


def _result_policy_snapshot(tool, server) -> tuple[int, list[str], bool, str | None]:
    policy = getattr(tool, "model_result_policy", None)
    if not isinstance(policy, dict):
        return 0, [], False, None
    fields, error = validate_model_result_fields(policy.get("fields"))
    revision = policy.get("revision")
    revision = revision if type(revision) is int and revision > 0 else 0
    stale = bool(fields and policy.get("target") != tool_result_target(tool, server))
    invalid = bool(revision and (error or policy.get("reason") == "fields_invalid"))
    return revision, [] if not revision or stale or invalid else fields, stale, "fields_invalid" if invalid else None


def tool_result_policy(tool, server) -> tuple[int, list[str], bool]:
    revision, fields, stale, _ = _result_policy_snapshot(tool, server)
    return revision, fields, stale


async def save_tool_result_policy(
    session, tool, server, *, paths: Any, expected_revision: int, expected_target: str, actor_id: str,
    credential_params: list[str] | None = None,
    commit: bool = True,
) -> tuple[int, list[str], str | None]:
    revision, _, _ = tool_result_policy(tool, server)
    target = tool_result_target(tool, server)
    if revision != expected_revision or target != expected_target:
        raise _fail("MCP_RESULT_FIELDS_CHANGED", "结果字段已更新，请刷新后重试")
    # MySQL 默认可重复读；获工具锁后仍需当前读，不能沿用拿锁前的 profile 快照。
    if await session.get(McpCredentialProfile, tool.id, with_for_update=True) is not None:
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "请在同一条凭据配置中保存结果字段")
    fields, error = validate_model_result_fields(paths)
    if credential_params is not None:
        # 先以用户打开编辑器时的快照做并发校验，再一次批准最终的参数来源和结果范围。
        # 不能先提交参数再单独保存结果，否则刚批准的目标指纹会立即失效。
        tool.credential_params = json.dumps(credential_params, ensure_ascii=False) if credential_params else None
        target = tool_result_target(tool, server)
    tool.model_result_policy = {
        "revision": revision + 1, "fields": fields, "target": target,
    }
    if error:
        tool.model_result_policy["reason"] = "fields_invalid"
    type_catalog.audit_change(session, "mcp_result_fields_save", actor_id, {
        "tool_id": tool.id, "field_count": len(fields), "revision": revision + 1,
    })
    if commit:
        await session.commit()
    return revision + 1, fields, error


def clear_tool_result_policy(tool, server) -> None:
    revision, _, _ = tool_result_policy(tool, server)
    tool.model_result_policy = {
        "revision": revision + 1, "fields": [], "target": tool_result_target(tool, server),
    }


async def clear_profile_access(session, tool_id: str, server_id: str) -> None:
    await session.execute(delete(SecretBinding).where(
        SecretBinding.consumer_type == CONSUMER_MCP, SecretBinding.consumer_id == server_id,
        SecretBinding.slot == f"group:{tool_id}",
    ))
    await session.execute(delete(McpCredentialUseGrant).where(McpCredentialUseGrant.tool_id == tool_id))


async def save_profile(
    session, tool, server, *, type_key: str, type_version: int, selection_mode: str,
    mappings: dict, expected_revision: int, actor_id: str, expected_profile_id: str | None = None,
    model_result_fields: Any = None, expected_result_target: str | None = None,
    warnings: list[dict[str, str]] | None = None, commit: bool = True,
) -> McpCredentialProfile:
    if type_key in type_catalog.LEGACY_TYPES or selection_mode not in SELECTION_MODES:
        raise _fail("CREDENTIAL_PROFILE_INVALID", "请选择结构化凭据类型和有效的选择方式")
    version = await type_catalog.get_version(session, type_key, type_version)
    remote_schema = parse_schema(tool)
    compiled = compile_mappings(remote_schema, version.schema_json, mappings)
    profile = await session.get(McpCredentialProfile, tool.id, with_for_update=True)
    revision = profile.revision if profile else 0
    if revision != expected_revision or (profile is not None and profile.profile_id != expected_profile_id):
        raise _fail("CREDENTIAL_REVISION_CONFLICT", "配置已更新，请刷新后重试")
    is_current = profile is not None and profile_is_current(profile, tool, server)
    model_result_fields, error = validate_model_result_fields(model_result_fields)
    if model_result_fields and (
        (expected_result_target is not None and expected_result_target != tool_result_target(tool, server))
        or (expected_result_target is None and profile is not None and not is_current)
    ):
        raise _fail("MCP_RESULT_FIELDS_CHANGED", "结果字段目标已变化，请刷新后重试")
    if error and warnings is not None:
        warnings.append({"code": "MCP_RESULT_FIELDS_INVALID", "message": error})
    if profile is None:
        profile = McpCredentialProfile(
            tool_id=tool.id, profile_id=str(uuid.uuid4()), server_id=server.id, tool_name=tool.tool_name,
        )
        session.add(profile)
        # 进入组凭据后废弃旧的独立规则，切回逐参数模式时须重新确认。
        result_revision, _, _ = tool_result_policy(tool, server)
        if result_revision:
            clear_tool_result_policy(tool, server)
    else:
        if (
            not is_current or profile.mappings != compiled
            or (profile.type_key, profile.type_version, profile.selection_mode) != (type_key, type_version, selection_mode)
        ):
            await clear_profile_access(session, tool.id, server.id)
        profile.server_id = server.id
        profile.tool_name = tool.tool_name
    profile.revision = revision + 1
    profile.type_key, profile.type_version = type_key, type_version
    profile.selection_mode, profile.mappings = selection_mode, compiled
    profile.model_result_fields = model_result_fields
    profile.schema_digest = schema_digest(remote_schema)
    profile.endpoint_digest = endpoint_digest(server.sse_url)
    type_catalog.audit_change(session, "mcp_profile_save", actor_id, {
        "tool_id": tool.id, "type_key": type_key, "type_version": type_version,
        "selection_mode": selection_mode, "revision": profile.revision, "fields": sorted(compiled),
        "model_result_field_count": len(profile.model_result_fields),
    })
    if commit:
        await session.commit()
    return profile


def profile_is_current(profile, tool, server) -> bool:
    return (
        profile.server_id == server.id and profile.tool_name == tool.tool_name
        and profile.schema_digest == schema_digest(parse_schema(tool))
        and profile.endpoint_digest == endpoint_digest(server.sse_url)
    )


def allows_mcp_target(parent, server, *, user_id: str, dept_code: str | None,
                      allow_external: bool, now: datetime) -> bool:
    host = host_of(server.sse_url)
    return bool(host) and evaluate_secret(_to_record(parent), SecretContext(
        user_id=user_id, dept_code=dept_code, scene=SCENE_MCP,
        target_host=host, allow_external_host=allow_external,
    ), now=now) is None


async def list_candidates(session, profile, server, *, user_id: str, dept_code: str | None,
                          secret_id: str | None = None, digest: str | None = None,
                          keyword: str = "", limit: int | None = None, only_usable: bool = True,
                          allow_external: bool | None = None):
    """只查询工具可见的元数据。调用方选择唯一凭据后才读取对应密文。"""
    payload = StructuredCredential
    now = datetime.utcnow()
    shared = SecretGrant
    shared_grantees = [
        and_(shared.grantee_type == "user", shared.grantee_id == user_id), shared.grantee_type == "all",
    ]
    if dept_code:
        shared_grantees.append(and_(shared.grantee_type == "dept", shared.grantee_id == dept_code))
    shared_access = select(shared.id).where(
        shared.secret_id == UserSecret.id, or_(*shared_grantees),
        or_(shared.expires_at.is_(None), shared.expires_at > now),
    ).exists()
    statement = select(
        UserSecret, payload.account, payload.account_digest, payload.value_revision,
    ).options(load_only(
        UserSecret.id, UserSecret.name, UserSecret.owner_scope, UserSecret.owner_id,
        UserSecret.secret_key, UserSecret.secret_type, UserSecret.status, UserSecret.expires_at,
        UserSecret.previous_expires_at, UserSecret.allowed_hosts, UserSecret.allowed_scenarios, raiseload=True,
    )).join(payload, and_(
        payload.secret_id == UserSecret.id,
        payload.owner_id == UserSecret.owner_id,
        payload.owner_scope == UserSecret.owner_scope,
    )).where(
        payload.type_key == profile.type_key,
        payload.type_version == profile.type_version,
        UserSecret.credential_format == structured_payload.STRUCTURED_FORMAT,
        or_(
            and_(UserSecret.owner_scope == "user", UserSecret.owner_id == user_id),
            # 凭据中心共享授权是系统凭据的唯一使用授权来源。
            and_(UserSecret.owner_scope == "system", shared_access),
        ),
    ).order_by(payload.account, UserSecret.id)
    if only_usable:
        statement = statement.where(UserSecret.status == "active")
    if secret_id:
        statement = statement.where(UserSecret.id == secret_id)
    if digest:
        statement = statement.where(payload.account_digest == digest)
    if keyword:
        statement = statement.where(or_(
            payload.account.icontains(keyword, autoescape=True),
            UserSecret.name.icontains(keyword, autoescape=True),
        ))
    if only_usable and allow_external is None:
        _, allow_external, _ = await SecretResolver._platform_flags()
    candidates = []
    batch_size = min(max(limit, 1), _CANDIDATE_QUERY_BATCH_SIZE) if limit is not None else None
    cursor = None
    while True:
        query = statement
        if batch_size is not None:
            query = query.limit(batch_size)
            if cursor is not None:
                account, secret_id_cursor = cursor
                query = query.where(or_(
                    payload.account > account,
                    and_(payload.account == account, UserSecret.id > secret_id_cursor),
                ))
        rows = list(await session.execute(query))
        for row in rows:
            if only_usable and not allows_mcp_target(
                row[0], server, user_id=user_id, dept_code=dept_code, allow_external=bool(allow_external), now=now,
            ):
                continue
            candidates.append(row)
            if limit is not None and len(candidates) >= limit:
                return candidates
        if batch_size is None or len(rows) < batch_size:
            return candidates
        # 只有通过策略校验的记录才计入 limit；游标按已扫描的最后一条推进，
        # 同账号用唯一 id 续读，避免无效候选占满一页或跳过共享的同名账号。
        cursor = (rows[-1].account, rows[-1][0].id)
        batch_size = min(batch_size * 2, _CANDIDATE_QUERY_BATCH_SIZE)


async def has_visible_candidate(session, profile, server, *, user_id: str, dept_code: str | None, digest: str) -> bool:
    return bool(await list_candidates(
        session, profile, server, user_id=user_id, dept_code=dept_code, digest=digest, only_usable=False, limit=1,
    ))


async def active_invocation_user(session, user_id: str):
    if not (user_id.isascii() and user_id.isdecimal() and len(user_id) <= 10):
        return None
    return (await session.execute(select(User.id, User.dept_code).where(
        User.id == int(user_id), User.status == 1,
    ))).one_or_none()


async def invocation_principal(session, server, user_id: str):
    user = await active_invocation_user(session, user_id)
    if user is None or (server.scope == "personal" and server.user_id != user.id):
        raise _fail("CREDENTIAL_ACCESS_DENIED")
    return user


async def pending_target(session, ref: dict, user_id: str, *, lock: bool = False):
    config = await load_payload_config(session)
    try:
        config.cipher()
    except PayloadCryptoError:
        raise CredentialInvocationError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "结构化凭据密钥环不可用", 503) from None
    tool = await session.get(McpToolCache, ref["tool_id"], with_for_update=True) if lock else await session.get(
        McpToolCache, ref["tool_id"],
    )
    profile = await session.get(McpCredentialProfile, ref["tool_id"])
    server = await session.get(McpServer, tool.server_id) if tool else None
    if (
        profile is None or profile.revision != ref["revision"] or profile.profile_id != ref.get("profile_id")
        or server is None or server.enabled_status != 1
        or not tool.is_available or not tool.is_published or not profile_is_current(profile, tool, server)
    ):
        raise _fail("CREDENTIAL_PROFILE_CHANGED")
    user = await invocation_principal(session, server, user_id)
    version = await type_catalog.get_version(session, profile.type_key, profile.type_version)
    compile_mappings(parse_schema(tool), version.schema_json, profile.mappings)
    return profile, server, user


async def pending_candidates(session, ref: dict, user_id: str, *, secret_id: str | None = None,
                             account_digest: str | None = None, allow_external: bool | None = None):
    profile, server, user = await pending_target(session, ref, user_id)
    return await list_candidates(
        session, profile, server, user_id=user_id, dept_code=user.dept_code,
        secret_id=secret_id, digest=account_digest, allow_external=allow_external,
    )


def pending_account_hint(pending, profile) -> str | None:
    if profile.selection_mode != "infer":
        return None
    try:
        raw = pending.snapshot.tool_call.get("input") or "{}"
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        account, _ = account_key(arguments.get(ACCOUNT_PARAM))
    except (AttributeError, ValueError, TypeError):
        return None
    return account


def _task_secret_id(profile, user_id: str) -> str | None:
    # 定时任务仅通过服务端上下文传递引用，模型参数不能提供 secret_id。
    from app.services.secrets.task_credential_wait import task_credential_secret_id

    return task_credential_secret_id({
        "tool_id": profile.tool_id, "profile_id": profile.profile_id,
        "revision": profile.revision, "selection_mode": profile.selection_mode,
    }, user_id)


async def _selection_candidates(
    session, profile, server, *, user_id: str, dept_code: str | None, arguments: dict,
    selected_id: str | None = None, explicit_account: str | None = None,
    allow_task_reselection: bool = False,
):
    """同一候选逻辑服务预检与实际出站。返回候选及受控选择原因。"""
    account = digest = None
    if selected_id is None and profile.selection_mode == "infer":
        raw = explicit_account if explicit_account is not None else arguments.get(ACCOUNT_PARAM)
        if raw is not None and (not isinstance(raw, str) or raw.strip()):
            try:
                account, digest = account_key(raw)
            except CredentialSchemaError:
                return None, "credential_selection_invalid"

    # 任务引用是缺少明确账号时的默认值。显式账号仍通过同一套 ACL 精确选择。
    if selected_id is None and digest is None:
        try:
            selected_id = _task_secret_id(profile, user_id)
        except CredentialInvocationError as exc:
            if allow_task_reselection and exc.code == "CREDENTIAL_PROFILE_CHANGED":
                return None, "credential_selection_invalid"
            raise
    if selected_id is None and profile.selection_mode == "fixed":
        # 已存在但失效的本人绑定仍优先。不得静默回退到全局绑定。
        binding = await SecretResolver._pick_binding(
            session, CONSUMER_MCP, server.id, f"group:{profile.tool_id}", user_id,
        )
        selected_id = binding.secret_id if binding else None
    if selected_id is None and digest is None:
        return None, "credential_selection_required"

    candidates = await list_candidates(
        session, profile, server, user_id=user_id, dept_code=dept_code, secret_id=selected_id, digest=digest,
    )
    if len(candidates) > 1:
        return None, "credential_ambiguous"
    if not candidates or (account is not None and candidates[0].account != account):
        if selected_id or (digest and await has_visible_candidate(
            session, profile, server, user_id=user_id, dept_code=dept_code, digest=digest,
        )):
            return None, "credential_selection_invalid"
        return None, "credential_missing"
    return candidates[0], None


async def credential_selection_reason(ref: dict, arguments: dict, user_id: str) -> str | None:
    from app.core.orm import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            profile, server, user = await pending_target(session, ref, user_id)
            _, reason = await _selection_candidates(
                session, profile, server, user_id=user_id, dept_code=user.dept_code,
                arguments=arguments, allow_task_reselection=True,
            )
            return reason
    except CredentialInvocationError:
        raise
    except type_catalog.CredentialTypeError as exc:
        raise CredentialInvocationError(exc.code, str(exc), exc.status_code) from None
    except Exception:
        raise CredentialInvocationError("CREDENTIAL_QUERY_UNAVAILABLE", "凭据查询暂不可用", 503) from None


async def requires_interactive_selection(
    ref: dict, arguments: dict, user_id: str, user_query: str = "",
) -> bool:
    """兼容旧签名。模型可从已有上下文推导账号，执行授权由候选查询确定。"""
    return await credential_selection_reason(ref, arguments, user_id) is not None


async def _prepare_legacy_arguments(
    tool, server, arguments: dict, user_info: dict, *, parameter_choices: tuple[tuple[str, str], ...] | None = None,
) -> tuple[dict, bool]:
    from app.services.secrets.injection import DENIAL_REASONS, denial_hint
    from app.services.secrets.locator import choice_field

    fields = [name for name, _ in mcp_arg_slots(tool)]
    schema = resolve_local_mcp_schema(parse_schema(tool))
    _require_mappable_dynamic_arguments(schema)
    if _unbound_sensitive_args(schema, fields):
        raise _fail("CREDENTIAL_MAPPING_REQUIRED", "请先为敏感参数配置凭据绑定")
    legacy_keys = {}
    if parameter_choices is None and fields:
        declared = {name for variant in _root_schema_variants(schema) for name in mcp_parameter_properties(variant)}
        legacy_keys = {field: choice_field(field) for field in fields if choice_field(field) not in declared}
    values = _public_mcp_arguments(
        {key: value for key, value in arguments.items() if key not in legacy_keys.values()},
        _business_mcp_schema(schema, set(fields)),
    )
    if not fields:
        return values, False
    target = host_of(server.sse_url)
    if not target or not user_info.get("user_id"):
        raise _fail("CREDENTIAL_ACCESS_DENIED")
    choices = dict(parameter_choices) if parameter_choices is not None else {
        field: arguments[key] for field, key in legacy_keys.items() if isinstance(arguments.get(key), str)
    }
    denials: dict[str, str] = {}
    values = await apply_mcp_arg_bindings(
        tool, values, user_info=user_info, denials=denials, target_host=target, model_choices=choices,
    )
    if denials or any(field not in values for field in fields):
        reasons = [DENIAL_REASONS.get(reason, "凭据不可用") for reason in denials.values()]
        hints = [denial_hint(reason) for reason in denials.values()]
        messages = list(dict.fromkeys(text for text in [*reasons, *hints] if text))
        detail = "。".join(messages) or "请在我的 MCP 中绑定参数凭据"
        raise CredentialInvocationError("CREDENTIAL_SELECTION_REQUIRED", f"工具缺少可用的参数凭据。{detail}。", 400)
    return values, True


async def prepare_mcp_arguments(
    session, server, tool_name: str, arguments: dict, call: McpCredentialInvocation,
    *, user_info: dict, trace_id: str | None, conversation_id: str | None,
) -> PreparedMcpArguments:
    profile = await session.get(McpCredentialProfile, call.tool_id)
    current_revision = profile.revision if profile else None
    if call.profile_revision != current_revision or (profile is not None and profile.profile_id != call.profile_id):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具凭据配置已变化，请重新调用")
    tool = await session.get(McpToolCache, call.tool_id)
    if (tool is None or tool.server_id != server.id or tool.tool_name.split(":", 1)[-1] != tool_name
            or not tool.is_available or not tool.is_published):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具配置已变化，请重新调用")
    if profile is None:
        result_revision, result_fields, stale, result_error = _result_policy_snapshot(tool, server)
        if result_revision and server.scope == "personal":
            await invocation_principal(session, server, str(user_info.get("user_id") or ""))
        outgoing, protected = await _prepare_legacy_arguments(
            tool, server, arguments, user_info, parameter_choices=call.parameter_choices,
        )
        return PreparedMcpArguments(
            outgoing, protected or bool(result_revision), tuple(result_fields),
            result_policy_stale=stale, result_policy_error=result_error,
        )

    config = await load_payload_config(session)
    try:
        cipher = config.cipher()
    except PayloadCryptoError:
        raise CredentialInvocationError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "结构化凭据密钥环不可用", 503) from None
    if not profile_is_current(profile, tool, server):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具参数定义或目标已变化，请重新配置")

    user_id = str(user_info.get("user_id") or "")
    user = await invocation_principal(session, server, user_id)
    version = await type_catalog.get_version(session, profile.type_key, profile.type_version)
    schema = parse_schema(tool)
    mappings = compile_mappings(schema, version.schema_json, profile.mappings)
    outgoing = _public_mcp_arguments(arguments, model_schema(schema, profile))
    dept_code = user.dept_code
    approval = matching_approval(
        profile, user_id=user_id, conversation_id=conversation_id,
        arguments=arguments, original_digest=call.arguments_digest,
    )
    if profile.selection_mode == "fixed" and call.selected_secret_id is not None and approval is None:
        raise _fail("CREDENTIAL_SELECTION_INVALID")
    selected_id = approval.secret_id if approval else call.selected_secret_id
    try:
        candidate, reason = await _selection_candidates(
            session, profile, server, user_id=user_id, dept_code=dept_code, arguments=arguments,
            selected_id=selected_id, explicit_account=call.explicit_account,
        )
    except CredentialInvocationError:
        raise
    except Exception:
        raise CredentialInvocationError("CREDENTIAL_QUERY_UNAVAILABLE", "凭据查询暂不可用", 503) from None
    if reason:
        message = {
            "credential_missing": "未找到与该账号匹配的可用凭据，请选择或补录",
            "credential_ambiguous": "有多条符合条件的凭据，请在界面选择",
            "credential_selection_invalid": "所选凭据不可用，请在界面重新选择",
            "credential_selection_required": "请在界面选择凭据后继续",
        }[reason]
        raise _fail(reason.upper(), message)
    parent, _, _, value_revision = candidate
    payload = await session.get(StructuredCredential, parent.id)
    if (payload is None or payload.value_revision != value_revision
            or (payload.type_key, payload.type_version, payload.owner_scope, payload.owner_id)
            != (profile.type_key, profile.type_version, parent.owner_scope, parent.owner_id)):
        raise _fail("CREDENTIAL_PROFILE_CHANGED")
    try:
        prepared = validate_payload(
            version.schema_json, structured_payload.decrypt_fields(payload, cipher), payload.extra_schema,
        )
        outgoing.update({target: prepared.values[source] for target, source in mappings.items()})
    except (CredentialSchemaError, PayloadCryptoError, KeyError):
        raise _fail("CREDENTIAL_PAYLOAD_UNAVAILABLE", "凭据字段缺失或无法解密") from None
    if approval is not None and not await claim_approval(session, approval):
        raise _fail("CREDENTIAL_APPROVAL_CONSUMED", "本次授权已使用或过期")
    await SecretResolver._log_usage(
        session, _to_record(parent), user_id=user_id, scene=SCENE_MCP, consumer_type=CONSUMER_MCP,
        consumer_id=server.id, slot=f"group:{profile.tool_id}", target_host=host_of(server.sse_url),
        trace_id=trace_id, conversation_id=conversation_id, result="success",
    )
    raw_fields = profile.model_result_fields
    result_fields = tuple(raw_fields) if isinstance(raw_fields, list) else ()
    return PreparedMcpArguments(outgoing, True, result_fields)

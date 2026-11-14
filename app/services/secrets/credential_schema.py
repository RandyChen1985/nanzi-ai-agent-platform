"""供凭证类型与实例共用的有限 JSON Schema 子集。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

MAX_SCHEMA_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_FIELDS = 32
MAX_OBJECT_DEPTH = 4
FIELD_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
SCALAR_FIELD_TYPES = {"string", "integer", "number", "boolean"}
FIELD_TYPES = SCALAR_FIELD_TYPES | {"array", "object"}
OBJECT_KEYS = {"properties", "required", "additionalProperties"}
ROOT_KEYS = OBJECT_KEYS | {"type", "title", "description", "$schema", "x-icon"}
FIELD_KEYS = OBJECT_KEYS | {
    "type", "title", "description", "writeOnly", "format", "default",
    "minimum", "maximum", "minLength", "maxLength", "enum", "items",
}


class CredentialSchemaError(ValueError):
    """只携带受控字段路径和错误类型。"""


def _error(path: str, message: str) -> CredentialSchemaError:
    return CredentialSchemaError(f"{path}: {message}")


def _bytes(data: Any) -> bytes:
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("$", "JSON 格式无效") from None


def schema_digest(schema: dict) -> str:
    return hashlib.sha256(_bytes(schema)).hexdigest()


def account_key(account: str) -> tuple[str, str]:
    if not isinstance(account, str):
        raise _error("account", "账号必须是字符串")
    normalized = unicodedata.normalize("NFC", account.strip())
    if not 1 <= len(normalized) <= 128 or any(unicodedata.category(ch).startswith("C") for ch in normalized):
        raise _error("account", "账号长度为 1–128 且不能包含控制字符")
    return normalized, hashlib.sha256(normalized.encode()).hexdigest()


def sensitive_field(name: str, spec: dict) -> bool:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_").lower()
    if spec.get("writeOnly") is True or spec.get("format") == "password":
        return True
    if name in {"authorization", "passwd", "password", "secret", "token", "apikey"}:
        return True
    return name.endswith(("_password", "_passwd", "_secret", "_token", "_api_key", "_private_key", "_access_key")) or name in {
        "api_key", "private_key", "access_key",
    }


def _field_key(name: Any) -> bool:
    return isinstance(name, str) and FIELD_KEY.fullmatch(name) is not None and name not in {
        "credential_account", "constructor", "prototype",
    }


def _validate_value(path: str, spec: dict, value: Any) -> Any:
    kind = spec["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise _error(path, "需要字符串")
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", MAX_PAYLOAD_BYTES):
            raise _error(path, "字符串长度超出限制")
        if "enum" in spec and value not in spec["enum"]:
            raise _error(path, "请选择定义中的枚举值")
        return value
    if kind == "boolean":
        if type(value) is not bool:
            raise _error(path, "需要布尔值")
        return value
    if kind == "array":
        if not isinstance(value, list):
            raise _error(path, "需要文本数组")
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise _error(f"{path}[{index}]", "需要字符串")
        return value
    if kind == "object":
        return _prepare_values(value, spec["properties"], spec["required"], path)
    if kind == "integer" and type(value) is not int:
        raise _error(path, "需要整数")
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise _error(path, "需要有限数值")
    if ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]):
        raise _error(path, "数值超出限制")
    return value


def _prepare_values(fields: Any, properties: dict, required: list, parent: str = "") -> dict:
    if not isinstance(fields, dict):
        raise _error(parent or "fields", "需要对象")
    if set(fields) - set(properties):
        raise _error(parent or "fields", "存在未定义字段")
    values = {}
    for name, spec in properties.items():
        path = f"{parent}.{name}" if parent else name
        if name in fields:
            value = fields[name]
        elif "default" in spec:
            value = spec["default"]
        elif name in required:
            raise _error(path, "缺少必填字段")
        else:
            continue
        value = _validate_value(path, spec, value)
        if name in required and isinstance(value, str) and not value.strip():
            raise _error(path, "必填字段不能为空")
        values[name] = value
    return values


def _normalize_properties(raw: dict, parent: str = "", depth: int = 0, private: bool = False) -> dict:
    props = raw.get("properties")
    if not isinstance(props, dict) or not 1 <= len(props) <= MAX_FIELDS:
        raise _error(parent or "properties", f"字段数必须为 1–{MAX_FIELDS}")
    required = raw.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) or key not in props for key in required):
        raise _error(parent or "required", "必填字段未定义")
    additional = raw.get("additionalProperties", False)
    if type(additional) is not bool:
        raise _error(parent or "additionalProperties", "additionalProperties 必须是布尔值")
    if depth and additional:
        raise _error(parent, "嵌套对象只能录入已定义的字段")
    return {
        "properties": {name: _normalize_field(name, spec, parent, depth, private) for name, spec in props.items()},
        "required": list(dict.fromkeys(required)),
        "additionalProperties": additional,
    }


def _normalize_field(name: str, raw: Any, parent: str = "", depth: int = 0, private: bool = False) -> dict:
    if not _field_key(name):
        raise _error(parent or "properties", "字段标识无效或保留")
    path = f"{parent}.{name}" if parent else name
    if not isinstance(raw, dict) or set(raw) - FIELD_KEYS:
        raise _error(path, "存在不支持的字段属性")
    field_type = raw.get("type")
    if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
        raise _error(path, "字段类型不支持")
    field = dict(raw)
    for label in ("title", "description"):
        if label in field and (not isinstance(field[label], str) or len(field[label]) > 512):
            raise _error(path, "字段说明超长或格式无效")
    if "writeOnly" in field and type(field["writeOnly"]) is not bool:
        raise _error(path, "writeOnly 必须是布尔值")
    if "format" in field and (field["format"] != "password" or field_type != "string"):
        raise _error(path, "字段格式不支持")
    private = private or sensitive_field(name, field)
    if private:
        if "default" in field or "enum" in field:
            raise _error(path, "敏感字段不能有默认值或枚举")
        field["writeOnly"] = True

    if "enum" in field:
        choices = field["enum"]
        if (field_type != "string" or not isinstance(choices, list) or not choices
                or any(not isinstance(item, str) or not item.strip() or "\n" in item or "\r" in item for item in choices)
                or len(set(choices)) != len(choices)):
            raise _error(path, "枚举需要一组互不重复的非空单行文本")
    if field_type == "array":
        if field.get("items") != {"type": "string"}:
            raise _error(path, "数组仅支持文本元素")
    elif "items" in field:
        raise _error(path, "items 仅适用于数组")
    if field_type == "object":
        if depth >= MAX_OBJECT_DEPTH:
            raise _error(path, f"对象最多嵌套 {MAX_OBJECT_DEPTH} 层")
        if "default" in field:
            raise _error(path, "对象字段须在录入时填写")
        field.update(_normalize_properties(field, path, depth + 1, private))
    elif OBJECT_KEYS & field.keys():
        raise _error(path, "只有对象可以定义子字段")

    for bound in ("minLength", "maxLength"):
        if bound in field and (field_type != "string" or type(field[bound]) is not int or field[bound] < 0):
            raise _error(path, "长度约束无效")
    for bound in ("minimum", "maximum"):
        if bound in field:
            n = field[bound]
            if field_type not in {"integer", "number"} or type(n) not in (int, float) or (
                type(n) is float and not math.isfinite(n)
            ):
                raise _error(path, "数值约束无效")
    if field.get("minLength", 0) > field.get("maxLength", MAX_PAYLOAD_BYTES):
        raise _error(path, "长度范围无效")
    if "minimum" in field and "maximum" in field and field["minimum"] > field["maximum"]:
        raise _error(path, "数值范围无效")
    if "default" in field:
        _validate_value(path, field, field["default"])
    return field


def _field_count(properties: dict) -> int:
    return sum(1 + _field_count(spec.get("properties", {})) for spec in properties.values())


def _contains_sensitive_field(name: str, spec: dict) -> bool:
    return sensitive_field(name, spec) or any(
        _contains_sensitive_field(child, child_spec) for child, child_spec in spec.get("properties", {}).items()
    )


def validate_schema(schema: Any) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object" or set(schema) - ROOT_KEYS:
        raise _error("$", "只支持顶层对象及已列出的属性")
    if len(_bytes(schema)) > MAX_SCHEMA_BYTES:
        raise _error("$", "类型定义过大")
    if "$schema" in schema and schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise _error("$schema", "仅支持 2020-12 标识")
    normalized = {"type": "object", **_normalize_properties(schema)}
    if _field_count(normalized["properties"]) > MAX_FIELDS:
        raise _error("properties", f"字段总数不能超过 {MAX_FIELDS}")
    for key in ("title", "description"):
        if key in schema:
            if not isinstance(schema[key], str) or len(schema[key]) > 512:
                raise _error(key, "类型说明超长或格式无效")
            normalized[key] = schema[key]
    if "x-icon" in schema:
        icon = schema["x-icon"]
        if not isinstance(icon, str) or len(icon) > 16 or "\r" in icon or "\n" in icon:
            raise _error("x-icon", "图标须为至多 16 个字符的单行文本")
        if icon.strip():
            normalized["x-icon"] = icon.strip()
    if len(_bytes(normalized)) > MAX_SCHEMA_BYTES:
        raise _error("$", "类型定义过大")
    return normalized


@dataclass(repr=False, frozen=True)
class PreparedPayload:
    values: dict[str, Any]
    public: dict[str, Any]
    secret_fields: list[str]
    extra_schema: dict[str, dict]


def validate_payload(schema: dict, fields: dict, extra_schema: dict | None = None) -> PreparedPayload:
    schema = validate_schema(schema)
    if not isinstance(fields, dict) or len(_bytes(fields)) > MAX_PAYLOAD_BYTES:
        raise _error("fields", "字段必须是大小受限的 JSON 对象")
    if extra_schema is not None and not isinstance(extra_schema, dict):
        raise _error("extra_fields", "扩展字段定义无效")
    extras = extra_schema or {}
    properties = dict(schema["properties"])
    if extras and (not schema["additionalProperties"] or set(properties) & set(extras)):
        raise _error("extra_fields", "不允许追加字段或字段重复")
    if len(_bytes(extras)) > MAX_SCHEMA_BYTES:
        raise _error("extra_fields", "扩展字段过多")
    normalized_extras = {}
    for name, raw in extras.items():
        if not isinstance(raw, dict):
            raise _error("extra_fields", "扩展字段定义无效")
        normalized_extras[name] = _normalize_field(name, {**raw, "writeOnly": True})
    properties.update(normalized_extras)
    if _field_count(properties) > MAX_FIELDS:
        raise _error("extra_fields", "扩展字段过多")
    values = _prepare_values(fields, properties, schema["required"])
    secrets = sorted(name for name in values if _contains_sensitive_field(name, properties[name]))
    public = {name: value for name, value in values.items() if name not in secrets}
    if len(_bytes(values)) > MAX_PAYLOAD_BYTES:
        raise _error("fields", "字段超出大小限制")
    return PreparedPayload(values, public, secrets, normalized_extras)


def _builtin(title: str, fields: dict, required: list[str]) -> tuple[str, dict]:
    return title, validate_schema({"type": "object", "properties": fields, "required": required})


def _text(title: str, **attributes) -> dict:
    return {"type": "string", "title": title, **attributes}


def _port(default: int) -> dict:
    return {"type": "integer", "title": "端口", "minimum": 1, "maximum": 65535, "default": default}


BUILTIN_SCHEMAS = {
    "scalar": _builtin("单值凭据", {"value": _text("凭据值", writeOnly=True)}, ["value"]),
    "variable": _builtin("变量", {"value": _text("变量值")}, ["value"]),
    "imap_password": _builtin("IMAP 邮箱", {
        "host": _text("服务器"), "port": _port(993), "username": _text("用户名"),
        "password": _text("应用专用密码", writeOnly=True),
    }, ["host", "username", "password"]),
    "oauth2": _builtin("OAuth2 刷新令牌", {
        "token_url": _text("Token 地址"), "client_id": _text("Client ID"),
        "client_secret": _text("Client Secret", writeOnly=True),
        "refresh_token": _text("Refresh Token", writeOnly=True),
    }, ["token_url", "client_id", "client_secret", "refresh_token"]),
    "api_key": _builtin("API Key", {
        "base_url": _text("Base URL"), "header": _text("Header 名", default="Authorization"),
        "api_key": _text("API Key", writeOnly=True),
    }, ["api_key"]),
    "gitlab_token": _builtin("GitLab Token", {
        "base_url": _text("GitLab 地址"), "token": _text("访问令牌", writeOnly=True),
    }, ["base_url", "token"]),
    "database": _builtin("数据库", {
        "engine": _text("类型"), "host": _text("主机"), "port": _port(5432),
        "database": _text("库名"), "username": _text("用户"), "password": _text("密码", writeOnly=True),
    }, ["host", "database", "username", "password"]),
    "webhook": _builtin("Webhook", {
        "url": _text("回调地址"), "secret": _text("签名密钥", writeOnly=True),
    }, ["url"]),
    "mcp_token": _builtin("MCP 服务令牌", {"token": _text("服务令牌", writeOnly=True)}, ["token"]),
}

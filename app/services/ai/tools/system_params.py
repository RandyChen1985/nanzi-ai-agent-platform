"""API 工具的系统级参数。

系统级参数不进入模型可见的 ``properties``：它们由平台在发请求前按当前用户
强制写入，模型既看不到也改不了。配置存放在 ``parameter_schema`` 顶层扩展
``x-nanzi-system-params``，因此不需要数据库迁移。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.ai.tools.user_placeholders import replace_user_placeholders

SYSTEM_PARAMS_KEY = "x-nanzi-system-params"
SYSTEM_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SYSTEM_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean"})
_NON_PARAM_KEYS = frozenset({"type", "required", "$schema", "properties", "description"})


class SystemParamConfigError(ValueError):
    """系统级参数配置不合法。"""


@dataclass(frozen=True)
class SystemParamSpec:
    name: str
    type: str
    value: str
    description: str = ""


def _schema_mapping(parameter_schema: Any) -> dict[str, Any]:
    if parameter_schema is None:
        return {}
    if isinstance(parameter_schema, str):
        try:
            parsed = json.loads(parameter_schema or "{}")
        except (TypeError, ValueError) as exc:
            raise SystemParamConfigError("参数定义不是合法 JSON") from exc
    elif isinstance(parameter_schema, Mapping):
        parsed = dict(parameter_schema)
    else:
        raise SystemParamConfigError("参数定义必须是 JSON 对象")
    if not isinstance(parsed, dict):
        raise SystemParamConfigError("参数定义必须是 JSON 对象")
    return parsed


def _normal_properties(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return properties
    return {
        str(key): value
        for key, value in schema.items()
        if not str(key).startswith("x-") and str(key) not in _NON_PARAM_KEYS
    }


def parse_system_params(parameter_schema: Any) -> list[SystemParamSpec]:
    schema = _schema_mapping(parameter_schema)
    raw = schema.get(SYSTEM_PARAMS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SystemParamConfigError(f"{SYSTEM_PARAMS_KEY} 必须是数组")

    specs: list[SystemParamSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, Mapping):
            raise SystemParamConfigError(f"第 {index} 个系统级参数必须是对象")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise SystemParamConfigError(f"第 {index} 个系统级参数缺少名称")
        if not SYSTEM_PARAM_NAME_RE.fullmatch(name):
            raise SystemParamConfigError(f"系统级参数名不合法：{name}")
        if name in seen:
            raise SystemParamConfigError(f"系统级参数名重复：{name}")
        type_name = str(entry.get("type") or "string").strip()
        if type_name not in SYSTEM_PARAM_TYPES:
            raise SystemParamConfigError(f"系统级参数 {name} 的类型不支持：{type_name}")
        value = entry.get("value")
        if not isinstance(value, str) or not value.strip():
            raise SystemParamConfigError(f"系统级参数 {name} 缺少固定值")
        specs.append(SystemParamSpec(
            name=name,
            type=type_name,
            value=value,
            description=str(entry.get("description") or ""),
        ))
        seen.add(name)
    return specs


def validate_system_params(parameter_schema: Any) -> list[SystemParamSpec]:
    schema = _schema_mapping(parameter_schema)
    specs = parse_system_params(schema)
    properties = _normal_properties(schema)
    for spec in specs:
        if spec.name in properties:
            raise SystemParamConfigError(
                f"系统级参数 {spec.name} 与普通参数重名，请改名或删除普通参数"
            )
    return specs


def _coerce_literal(value: Any, type_name: str, name: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        if type_name == "integer":
            return int(value)
        if type_name == "number":
            return float(value)
        if type_name == "boolean":
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError
        return value
    except (TypeError, ValueError) as exc:
        raise SystemParamConfigError(
            f"系统级参数 {name} 的值 {value!r} 不能转换为 {type_name}"
        ) from exc


def apply_system_params(
    parameter_schema: Any,
    params: Mapping[str, Any],
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    specs = validate_system_params(parameter_schema)
    prepared = dict(params)
    for spec in specs:
        raw = replace_user_placeholders(spec.value, variables, expected_type=spec.type)
        prepared[spec.name] = _coerce_literal(raw, spec.type, spec.name)
    return prepared

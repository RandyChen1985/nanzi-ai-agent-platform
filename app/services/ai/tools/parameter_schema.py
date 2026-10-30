"""参数 schema 归一与 ``{user.xxx}`` 占位符替换。

原来这两个函数是 ``generic_api.py`` 的私有实现。脚本工具需要**完全相同**的
schema 兼容规则（标准 JSON Schema / 扁平 map / ``"param": "描述"`` 三种写法，
以及 ``{user.xxx}`` 占位符），所以提取到独立模块共用——两处各解析一遍必然漂移。

``generic_api.py`` 保留一个同签名的薄包装 ``_apply_user_placeholders``，因为
契约测试把它当作「占位符必须在路径替换之前处理」的锚点。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping

from app.services.ai.tools.user_placeholders import (
    contains_user_placeholder,
    replace_user_placeholders,
)

logger = logging.getLogger(__name__)

# 扁平形态下这些键是 schema 元信息，不是参数本身
_SCHEMA_META_KEYS = {"type", "required", "$schema", "description"}


def parse_parameter_schema(
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
    else:
        # 扁平形态下顶层就是参数表；x-* 是平台扩展（如 x-nanzi-system-params），
        # 不能被当成普通参数交给模型。
        properties = {
            str(key): value
            for key, value in schema_def.items()
            if not str(key).startswith("x-")
            and str(key) not in _SCHEMA_META_KEYS
        }
    return schema_def, properties, required_fields


def apply_user_placeholders(
    parameter_schema: Any,
    params: Dict[str, Any],
    variables: Mapping[str, Any],
    *,
    tool_name: str = "",
) -> Dict[str, Any]:
    """替换参数值里的 ``{user.xxx}``，并补上含占位符的默认值。

    模型没传某个参数时，标准 JSON Schema 的 ``default`` 不会自动落进
    ``kwargs``（``StructuredTool`` 不做 pydantic 校验）；含占位符的默认值
    在这里补，运行时才能真正按当前用户取值。
    """
    _schema_def, properties, _required_fields = parse_parameter_schema(
        parameter_schema,
        tool_name=tool_name,
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

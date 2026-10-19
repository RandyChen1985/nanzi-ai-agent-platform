"""API 工具参数里的用户变量占位符。

工具参数（尤其是「默认值」）经常需要按调用者取值：把 ``order_id`` 默认成当前
用户 ID、把申请人写成当前用户姓名。元数据行级权限已经支持 ``{user.user_name}``
这类占位符，这里复用同一套变量口径，避免两处各定义一套字段名。

变量来源优先级：

1. ``AgentContext.user_dimensions``——已由上下文管理器把 ``extra_data`` 展平，
   委派子智能体共享同一份维度；
2. ``current_user_info``——普通会话请求开始时写入的原始 user_info。

只暴露白名单内的安全字段；``extra_data`` 中的敏感键（password / token /
api_key 等）一律不参与替换，避免有人用 ``{user.password}`` 把凭据发给外部接口。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

USER_PLACEHOLDER_RE = re.compile(r"\{user\.([A-Za-z_][A-Za-z0-9_]*)\}")

#: 与元数据管理、SQL 行级权限保持一致的内置用户属性（``id`` 另以 ``user_id`` 别名提供）。
CORE_USER_VARIABLE_KEYS = (
    "id",
    "user_id",
    "user_name",
    "real_name",
    "dept_code",
    "org_path",
    "role",
)


class UserPlaceholderError(ValueError):
    """占位符存在，但当前请求取不到对应的用户变量。"""

    def __init__(self, placeholders: list[str]):
        self.placeholders = placeholders
        names = "、".join(f"{{user.{name}}}" for name in placeholders)
        super().__init__(
            f"用户变量 {names} 没有取到值：请确认当前用户已配置该字段，"
            "或修正工具参数里的占位符"
        )


def contains_user_placeholder(value: Any) -> bool:
    """值里是否出现 ``{user.xxx}``（对象/数组默认值也会递归检查）。"""
    if isinstance(value, str):
        return bool(USER_PLACEHOLDER_RE.search(value))
    if isinstance(value, Mapping):
        return any(contains_user_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_user_placeholder(item) for item in value)
    return False


def _coerce_user_id(raw: Any) -> Any:
    """用户主键统一成整数，方便 integer 类型参数直接落位。"""
    if isinstance(raw, bool):
        return raw
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return raw


def _load_extra_data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _variables_from_extra_data(
    raw: Any,
    variables: dict[str, Any],
) -> None:
    from app.services.ai.user_extra_data import is_sensitive_key

    for key, value in _load_extra_data(raw).items():
        key_text = str(key)
        if key_text in CORE_USER_VARIABLE_KEYS or key_text in variables:
            continue
        if is_sensitive_key(key_text) or value is None:
            continue
        variables[key_text] = value


def resolve_user_variables(user_info: Mapping[str, Any] | None) -> dict[str, Any]:
    """把原始 user_info 归一成 ``{变量名: 值}``。"""
    info = dict(user_info or {})
    raw_uid = info.get("user_id")
    if raw_uid is None:
        raw_uid = info.get("id")

    variables: dict[str, Any] = {}
    if raw_uid is not None:
        variables["id"] = _coerce_user_id(raw_uid)
        variables["user_id"] = variables["id"]
    for key in ("user_name", "real_name", "dept_code", "org_path", "role"):
        value = info.get(key)
        if value is not None and value != "":
            variables[key] = value
    _variables_from_extra_data(info.get("extra_data"), variables)
    return variables


def _variables_from_dimensions(dimensions: Mapping[str, Any]) -> dict[str, Any]:
    """``AgentContext.user_dimensions`` 已展平 extra_data，这里只做白名单过滤。"""
    from app.services.ai.user_extra_data import is_sensitive_key

    variables: dict[str, Any] = {}
    for key in CORE_USER_VARIABLE_KEYS:
        value = dimensions.get(key)
        if key == "user_id" and value is None:
            value = dimensions.get("id")
        if value is None or value == "":
            continue
        variables[key] = _coerce_user_id(value) if key in {"id", "user_id"} else value
    for key, value in dimensions.items():
        key_text = str(key)
        if key_text in CORE_USER_VARIABLE_KEYS or key_text == "extra_data":
            continue
        if is_sensitive_key(key_text) or value is None:
            continue
        variables[key_text] = value
    return variables


def current_user_variables() -> dict[str, Any]:
    """读取当前请求的用户变量。"""
    try:
        from app.core.context import get_current_agent_context

        agent_context = get_current_agent_context()
        dimensions = getattr(agent_context, "user_dimensions", None) if agent_context else None
        if dimensions:
            return _variables_from_dimensions(dimensions)
    except Exception:
        logger.debug("读取 AgentContext 用户维度失败", exc_info=True)

    try:
        from app.utils.context import current_user_info

        return resolve_user_variables(current_user_info.get())
    except Exception:
        logger.debug("读取 current_user_info 失败", exc_info=True)
        return {}


def _placeholder_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _coerce_placeholder_value(value: Any, expected_type: str | None) -> Any:
    """整串就是一个占位符时保留变量本身的类型，避免 integer 被发成字符串。"""
    if expected_type == "string":
        return _placeholder_text(value)
    if expected_type in {"integer", "number"}:
        try:
            return int(str(value).strip()) if expected_type == "integer" else float(value)
        except (TypeError, ValueError):
            return value
    if expected_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
        return value
    return value


def replace_user_placeholders(
    value: Any,
    variables: Mapping[str, Any],
    *,
    expected_type: str | None = None,
) -> Any:
    """递归替换值里的 ``{user.xxx}``。

    - 整个值就是一个占位符时，按 ``expected_type`` 保留原始类型；
    - 占位符嵌在文本里时按字符串插值（如 ``Bearer {user.user_name}``）；
    - 任一变量取不到就抛 :class:`UserPlaceholderError`——宁可让本次调用
      明确失败，也不要把空字符串发给外部接口（空过滤条件可能反而放宽范围）。
    """
    if isinstance(value, str):
        matches = list(USER_PLACEHOLDER_RE.finditer(value))
        if not matches:
            return value

        missing = [
            match.group(1)
            for match in matches
            if match.group(1) not in variables
        ]
        if missing:
            raise UserPlaceholderError(list(dict.fromkeys(missing)))

        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            return _coerce_placeholder_value(
                variables[matches[0].group(1)],
                expected_type,
            )

        return USER_PLACEHOLDER_RE.sub(
            lambda match: _placeholder_text(variables[match.group(1)]),
            value,
        )

    if isinstance(value, Mapping):
        return {
            key: replace_user_placeholders(item, variables)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [replace_user_placeholders(item, variables) for item in value]

    return value


def replace_headers_user_placeholders(
    headers: Mapping[str, Any],
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    """Headers 里的用户变量也替换（常见于 ``X-User: {user.user_name}``）。"""
    return {
        str(key): replace_user_placeholders(value, variables)
        for key, value in headers.items()
    }

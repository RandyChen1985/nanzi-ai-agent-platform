"""Platform MCP 方法共用的安全边界和响应序列化工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.config import settings
from app.services.mcp.user_context_assertion import sanitize_user_extra_data


def build_platform_user_info(user: Any) -> dict[str, Any]:
    """从已加载的 NanZi User 记录构造运行时用户上下文。

    这里刻意只复制身份和扩展字段，不复制 ``api_key``、密码摘要或其他凭证字段。
    ``extra_data`` 在进入运行时前就按 User Assertion 的敏感字段规则清洗。
    """
    if isinstance(user, Mapping):
        getter = user.get
    else:
        def getter(key: str, default: Any = None) -> Any:
            return getattr(user, key, default)

    return {
        "user_id": str(getter("id", getter("user_id", "")) or ""),
        "user_name": getter("user_name"),
        "real_name": getter("real_name"),
        "role": getter("role", "user") or "user",
        "dept_code": getter("dept_code"),
        "org_path": getter("org_path"),
        "extra_data": sanitize_user_extra_data(getter("extra_data")),
    }


def _clean_resource_ids(values: Iterable[Any] | None) -> set[str] | None:
    if values is None:
        return None
    return {str(value).strip() for value in values if str(value).strip()}


def intersect_resource_ids(
    user_allowed: Iterable[Any] | None,
    client_allowed: Iterable[Any] | None,
    requested: Iterable[Any] | None = None,
) -> list[str]:
    """计算用户、Client 白名单和请求范围的交集。

    ``None`` 表示这一层没有额外限制，空集合表示这一层明确没有权限。
    """
    user_set, client_set, requested_set = (
        _clean_resource_ids(values)
        for values in (user_allowed, client_allowed, requested)
    )
    result = user_set
    if client_set is not None:
        result = client_set if result is None else result & client_set
    if requested_set is not None:
        result = requested_set if result is None else result & requested_set
    return sorted(result or set())


def _cursor_secret() -> bytes:
    configured = str(getattr(settings, "ENCRYPTION_KEY", "") or "").strip()
    return (configured or "nanzi-platform-mcp-cursor-secret").encode("utf-8")


def encode_platform_cursor(method_name: str, offset: int) -> str:
    """生成绑定方法名且带签名的分页游标。"""
    if not method_name or offset < 0:
        raise ValueError("invalid platform cursor")
    body = json.dumps(
        {"method": method_name, "offset": int(offset)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_body = base64.urlsafe_b64encode(body).rstrip(b"=")
    signature = hmac.new(_cursor_secret(), encoded_body, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{encoded_body.decode('ascii')}.{encoded_signature.decode('ascii')}"


def decode_platform_cursor(method_name: str, cursor: str | None) -> int | None:
    """校验并解析 NanZi 生成的游标；任何篡改或跨方法使用都返回 None。"""
    if not cursor or "." not in cursor:
        return None
    encoded_body, encoded_signature = cursor.split(".", 1)
    if not encoded_body or not encoded_signature:
        return None
    try:
        expected = hmac.new(
            _cursor_secret(), encoded_body.encode("ascii"), hashlib.sha256
        ).digest()
        supplied = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            return None
        body = base64.urlsafe_b64decode(encoded_body + "=" * (-len(encoded_body) % 4))
        payload = json.loads(body.decode("utf-8"))
        if payload.get("method") != method_name:
            return None
        offset = int(payload.get("offset"))
        return offset if offset >= 0 else None
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        return None


def _active_tables(dataset: Any) -> list[Any]:
    return [table for table in (getattr(dataset, "tables", None) or []) if getattr(table, "status", 1) == 1]


def serialize_metadata_dataset(dataset: Any) -> dict[str, Any]:
    """输出对外数据集摘要，明确排除连接和权限策略字段。"""
    tables = _active_tables(dataset)
    metrics = list(getattr(dataset, "metrics", None) or [])
    return {
        "dataset_id": str(dataset.id),
        "name": getattr(dataset, "display_name", None) or getattr(dataset, "name", ""),
        "dataset_name": getattr(dataset, "name", ""),
        "description": getattr(dataset, "description", None) or "",
        "data_source_type": getattr(dataset, "data_source", None) or "unknown",
        "table_count": len(tables),
        "metric_count": len(metrics),
        "tags": list(getattr(dataset, "tags", None) or []),
    }


def serialize_metadata_schema(dataset: Any, *, table_names: set[str] | None = None) -> dict[str, Any]:
    """输出授权数据集的表字段结构，不携带样例值、枚举或内部策略。"""
    tables = _active_tables(dataset)
    if table_names is not None:
        tables = [table for table in tables if str(getattr(table, "physical_name", "")) in table_names]

    serialized_tables: list[dict[str, Any]] = []
    for table in tables:
        columns = []
        for column in getattr(table, "columns", None) or []:
            columns.append(
                {
                    "name": getattr(column, "physical_name", ""),
                    "display_name": getattr(column, "term", None) or getattr(column, "physical_name", ""),
                    "type": getattr(column, "type", None) or "unknown",
                    "nullable": False,
                    "description": getattr(column, "description", None) or "",
                    "sensitive": False,
                }
            )
        serialized_tables.append(
            {
                "name": getattr(table, "physical_name", ""),
                "display_name": getattr(table, "term", None) or getattr(table, "physical_name", ""),
                "description": getattr(table, "description", None) or "",
                "columns": columns,
            }
        )

    return {"dataset_id": str(dataset.id), "tables": serialized_tables}


def serialize_metadata_metric(metric: Any) -> dict[str, Any]:
    return {
        "metric_id": str(metric.id),
        "name": getattr(metric, "display_name", None) or getattr(metric, "name", ""),
        "metric_name": getattr(metric, "name", ""),
        "description": getattr(metric, "description", None) or "",
        "dataset_id": str(metric.dataset_id),
        "definition": getattr(metric, "calculation_logic", None) or "",
        "unit": getattr(metric, "unit", None) or "",
        "filters": [],
        "tags": list(getattr(metric, "tags", None) or []),
    }


__all__ = [
    "build_platform_user_info",
    "decode_platform_cursor",
    "encode_platform_cursor",
    "intersect_resource_ids",
    "serialize_metadata_dataset",
    "serialize_metadata_metric",
    "serialize_metadata_schema",
]

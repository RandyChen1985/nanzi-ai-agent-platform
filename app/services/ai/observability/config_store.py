"""``langfuse_config`` 单行配置表的读写与字段校验。

与 ``ConfigService``/``system_configs`` 完全解耦：Langfuse 的配置字段是强类型的
（布尔、小数、超时秒数），放在独立表里可以逐字段校验与掩码，也不必挤进通用键值分组的
排序与白名单。

约定：
- ``secret_key`` 的三态语义 —— ``None`` = 本次未提交（保持原值）；``""`` = 显式清空；
  非空字符串 = 覆盖写入（加密后落库）。
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy import Table, Column, Integer, String, Text, Boolean, Numeric, DateTime, MetaData

from app.core.config import settings
from app.core.orm import AsyncSessionLocal
from app.services.ai.observability import credentials

logger = logging.getLogger(__name__)

ROW_ID = 1

# 单行配置的字段默认值（缺行 / 字段为空时使用）。
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "host": None,
    "public_key": None,
    "secret_key": None,
    "sample_rate": 1.0,
    "capture_content": True,
    "environment": None,
    "release": None,
    "timeout_seconds": 5,
    "trace_url_template": None,
}

_BOOL_FIELDS = ("enabled", "capture_content")
_TRUE_TOKENS = {"true", "1", "yes", "on"}
_FALSE_TOKENS = {"false", "0", "no", "off"}
_STR_MAX_LENGTH = {
    "host": 512,
    "public_key": 255,
    "environment": 64,
    "release": 64,
    "trace_url_template": 512,
}
_STR_FIELDS = tuple(_STR_MAX_LENGTH.keys())

_metadata = MetaData()

LANGFUSE_CONFIG_TABLE = Table(
    "langfuse_config",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Boolean),
    Column("host", String(512)),
    Column("public_key", String(255)),
    Column("secret_key", Text),
    Column("sample_rate", Numeric(4, 3)),
    Column("capture_content", Boolean),
    Column("environment", String(64)),
    Column("release", String(64)),
    Column("timeout_seconds", Integer),
    Column("trace_url_template", String(512)),
    Column("updated_by", String(64)),
)

_SELECT_COLUMNS = (
    LANGFUSE_CONFIG_TABLE.c.enabled,
    LANGFUSE_CONFIG_TABLE.c.host,
    LANGFUSE_CONFIG_TABLE.c.public_key,
    LANGFUSE_CONFIG_TABLE.c.secret_key,
    LANGFUSE_CONFIG_TABLE.c.sample_rate,
    LANGFUSE_CONFIG_TABLE.c.capture_content,
    LANGFUSE_CONFIG_TABLE.c.environment,
    LANGFUSE_CONFIG_TABLE.c.release,
    LANGFUSE_CONFIG_TABLE.c.timeout_seconds,
    LANGFUSE_CONFIG_TABLE.c.trace_url_template,
    LANGFUSE_CONFIG_TABLE.c.updated_by,
)
_SELECT_NAMES = (
    "enabled",
    "host",
    "public_key",
    "secret_key",
    "sample_rate",
    "capture_content",
    "environment",
    "release",
    "timeout_seconds",
    "trace_url_template",
    "updated_by",
)


class LangfuseConfigError(ValueError):
    """配置字段非法或无法解密时抛出，调用方应转成 400 / ``degraded`` 状态。"""


def _normalize_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise LangfuseConfigError(f"{field} 必须是 true/false")


def _normalize_optional_str(field: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    limit = _STR_MAX_LENGTH[field]
    if len(text_value) > limit:
        raise LangfuseConfigError(f"{field} 长度不能超过 {limit}")
    return text_value


def _normalize_sample_rate(value: Any) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError) as exc:
        raise LangfuseConfigError("sample_rate 必须是 0~1 之间的小数") from exc
    if rate != rate or rate in (float("inf"), float("-inf")):
        raise LangfuseConfigError("sample_rate 必须是 0~1 之间的小数")
    if rate < 0.0 or rate > 1.0:
        raise LangfuseConfigError("sample_rate 取值范围为 0~1")
    return rate


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise LangfuseConfigError("timeout_seconds 必须是正整数") from exc
    if timeout < 1:
        raise LangfuseConfigError("timeout_seconds 必须大于 0")
    return timeout


def normalize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把外部提交的字段规整成可落库的字典；非法值抛 ``LangfuseConfigError``。

    未提交的字段回落到 ``DEFAULTS``；``secret_key`` 保留三态语义不做默认覆盖。
    """
    raw = dict(payload or {})
    normalized: Dict[str, Any] = {}

    for field in _BOOL_FIELDS:
        if field in raw and raw[field] is not None:
            normalized[field] = _normalize_bool(field, raw[field])
        else:
            normalized[field] = DEFAULTS[field]

    if "sample_rate" in raw and raw["sample_rate"] is not None:
        normalized["sample_rate"] = _normalize_sample_rate(raw["sample_rate"])
    else:
        normalized["sample_rate"] = DEFAULTS["sample_rate"]

    if "timeout_seconds" in raw and raw["timeout_seconds"] is not None:
        normalized["timeout_seconds"] = _normalize_timeout(raw["timeout_seconds"])
    else:
        normalized["timeout_seconds"] = DEFAULTS["timeout_seconds"]

    for field in _STR_FIELDS:
        if field in raw:
            normalized[field] = _normalize_optional_str(field, raw[field])
        else:
            normalized[field] = DEFAULTS[field]

    host = normalized.get("host")
    if host and not (host.startswith("http://") or host.startswith("https://")):
        raise LangfuseConfigError("host 必须以 http:// 或 https:// 开头")

    if "secret_key" in raw:
        secret = raw["secret_key"]
        normalized["secret_key"] = "" if secret is None else str(secret).strip()
    else:
        normalized["secret_key"] = None

    return normalized


def to_public_view(config: Dict[str, Any]) -> Dict[str, Any]:
    """转成可返回给前端的视图：不含密钥明文，只报告是否已配置。"""
    view = {key: config.get(key, DEFAULTS.get(key)) for key in DEFAULTS}
    view.pop("secret_key", None)
    view["has_secret_key"] = bool((config.get("secret_key") or "").strip())
    return view


def _coerce_row_value(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _BOOL_FIELDS:
        return bool(value)
    if name == "sample_rate":
        return float(value)
    if name == "timeout_seconds":
        return int(value)
    return str(value)


async def load_raw_row() -> Optional[Dict[str, Any]]:
    """读取原始行（``secret_key`` 为落库密文），缺行返回 ``None``。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(*_SELECT_COLUMNS).where(LANGFUSE_CONFIG_TABLE.c.id == ROW_ID)
        )
        row = result.fetchone()
    if row is None:
        return None
    return {
        name: _coerce_row_value(name, row[index])
        for index, name in enumerate(_SELECT_NAMES)
    }


async def load_config() -> Dict[str, Any]:
    """读取有效配置：缺行用默认值，``secret_key`` 解密为明文。

    解密失败抛 ``LangfuseCredentialError``，由 ``settings`` 捕获并置为 ``degraded``。
    """
    raw_row = await load_raw_row()
    if raw_row is None:
        return normalize_payload({})

    config: Dict[str, Any] = {}
    for field, default in DEFAULTS.items():
        value = raw_row.get(field)
        config[field] = default if value is None else value

    stored_secret = raw_row.get("secret_key")
    config["secret_key"] = credentials.decrypt_langfuse_secret(stored_secret)
    config["updated_by"] = raw_row.get("updated_by")
    return config


def _dialect_name() -> str:
    try:
        return settings.normalized_database_type
    except Exception:  # pragma: no cover - 兜底：配置异常时按 mysql 处理
        return "mysql"


def _build_upsert(values: Dict[str, Any], dialect: str):
    if dialect == "postgresql":
        statement = postgresql_insert(LANGFUSE_CONFIG_TABLE).values(**values)
        return statement.on_conflict_do_update(
            index_elements=[LANGFUSE_CONFIG_TABLE.c.id],
            set_={key: getattr(statement.excluded, key) for key in values if key != "id"},
        )
    if dialect == "mysql":
        statement = mysql_insert(LANGFUSE_CONFIG_TABLE).values(**values)
        return statement.on_duplicate_key_update(
            **{key: getattr(statement.inserted, key) for key in values if key != "id"}
        )
    raise LangfuseConfigError(f"不支持的数据库方言: {dialect}")


async def save_config(payload: Dict[str, Any], changed_by: Optional[str] = None) -> Dict[str, Any]:
    """校验后写入单行配置，返回写入后的有效配置（含解密后的密钥）。"""
    normalized = normalize_payload(payload)

    secret_marker = normalized.pop("secret_key", None)
    if secret_marker is None:
        # 未提交密钥：保持原值，避免把掩码占位或空值误写成真密钥。
        row_values: Dict[str, Any] = dict(normalized)
    elif secret_marker == "":
        row_values = {**normalized, "secret_key": None}
    else:
        row_values = {**normalized, "secret_key": credentials.encrypt_langfuse_secret(secret_marker)}

    values = {"id": ROW_ID, **row_values, "updated_by": changed_by}

    async with AsyncSessionLocal() as session:
        statement = _build_upsert(values, _dialect_name())
        await session.execute(statement)
        await session.commit()

    logger.info(
        "langfuse_config 已更新 by=%s enabled=%s host=%s",
        changed_by,
        row_values.get("enabled"),
        row_values.get("host"),
    )
    return await load_config()


async def delete_config_for_tests() -> None:
    """仅测试使用：删除单行配置，保证用例之间互不影响。"""
    from sqlalchemy import delete

    async with AsyncSessionLocal() as session:
        await session.execute(delete(LANGFUSE_CONFIG_TABLE).where(LANGFUSE_CONFIG_TABLE.c.id == ROW_ID))
        await session.commit()


async def snapshot_row_for_tests() -> Optional[Dict[str, Any]]:
    """仅测试使用：读出当前原始行（含**加密后**的密钥）。

    存在的意义是让测试夹具**可逆**。教训来源：夹具原先只做 DELETE，
    会把开发库/联调库里已保存的真实配置（含密钥）永久删掉，而这张表没有变更审计、
    删了无法恢复。现在测试前先备份、测试后原样写回。
    """
    raw_row = await load_raw_row()
    return None if raw_row is None else dict(raw_row)


async def restore_row_for_tests(row: Dict[str, Any]) -> None:
    """仅测试使用：把 :func:`snapshot_row_for_tests` 的结果原样写回。

    只覆盖业务列；``updated_at`` 由数据库的 ON UPDATE 自动刷新（时间戳变化可接受，
    数据不丢才是重点）。
    """
    values: Dict[str, Any] = {"id": ROW_ID}
    for name in _SELECT_NAMES:
        values[name] = row.get(name)

    async with AsyncSessionLocal() as session:
        await session.execute(_build_upsert(values, _dialect_name()))
        await session.commit()

"""ChatBI schema-stale correction driven by database query errors (方案 A).

背景：底层表 DDL 变更后，get_dataset_schema 返回的 Schema（缓存/RAG 索引）可能已过时，
SQL 执行后数据库会报 ``unknown column xxx`` / ``column xxx does not exist`` 等错误。
数据库报错是物理真相。本模块据此把「报错字段」与「当前 Schema 已声明的列」做对照：

* 报错字段**确实在当前 Schema 中** → 判断为「真实过时列」：把它从当轮可见 Schema 中剔除，
  构造纠正后的 Schema，让模型绕开失效列重写 SQL（database wins）。
* 报错字段**不在当前 Schema 中** → 判断为「模型编造/别名误用」：**绝不剔除**（Schema 里本就没有
  可删的东西），走既有 schema 重查 / invalid-identifier 纠错路径，防止诱导编造与反复删除。

本模块只做「内存态当轮视图」的纠正，不持久化、不污染源 Schema / RAG（那是长期根治方案的事）。
刻意保持纯函数，便于单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.ai.runners.chatbi.sql_gates import (
    extract_invalid_sql_identifiers,
    is_schema_reference_sql_error,
    normalize_sql_identifier,
)

logger = logging.getLogger(__name__)

# 表内全部失效列都被剔除后，视为该表已无可用查询列。
_TABLE_EXHAUSTED_MARKER = "# [stale: DDL 已变更，该表已无剩余可用列]"


@dataclass
class StaleColumn:
    """一个被数据库报错点名的字段是否应作为「过时列」剔除。"""

    field_name: str
    table_key: str | None
    safe_to_drop: bool
    reason: str = ""


@dataclass
class StaleCorrection:
    """当轮 Schema 纠正结果。"""

    stale_columns: list[StaleColumn] = field(default_factory=list)
    corrected_schema_output: str = ""
    # 剔除后各表剩余可用列（normalized table_key -> [column_name]）
    remaining_table_columns: dict[str, list[str]] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return any(c.safe_to_drop for c in self.stale_columns)


def _candidate_identifiers(message: str) -> list[str]:
    """从错误文本提取候选字段名（复用现有解析）。"""
    return list(dict.fromkeys(extract_invalid_sql_identifiers(message)))


def classify_stale(
    candidate: str,
    *,
    table_keys: set[str] | list[str],
    schema_columns: dict[str, list[str]],
) -> StaleColumn:
    """判断单个候选字段是否应作为「过时列」剔除。

    剔除的唯一合法条件：该列名在当前 Schema 的某张表**已声明**（normalized 命中）。
    这样能严格区分「真实过时列」（可删）与「模型编造/别名」（绝不删）。

    支持带表名限定（如 ``table.col`` 或 ``schema.table.col``）的优先精准匹配，
    若无前缀或前缀未匹配，则按确定性排序在各表中查找匹配。
    """
    norm = normalize_sql_identifier(candidate)
    prefix = ""
    if "." in norm:
        parts = norm.split(".")
        bare_name = parts[-1]
        prefix = parts[-2]
    else:
        bare_name = norm

    if not bare_name:
        return StaleColumn(candidate, None, False, "无有效列名")

    sorted_keys = sorted(table_keys)
    matched_table_key: str | None = None

    # 1. 若候选字段带有表名限定前缀，优先尝试精准匹配对应表
    if prefix:
        for table_key in sorted_keys:
            norm_key = normalize_sql_identifier(table_key)
            table_bare = norm_key.split(".")[-1] if "." in norm_key else norm_key
            if prefix in (norm_key, table_bare):
                cols = schema_columns.get(table_key, [])
                if any(normalize_sql_identifier(c) == bare_name for c in cols):
                    matched_table_key = table_key
                    break

    # 2. 若未带表限定或前缀未精准命中，按确定性顺序查找
    if matched_table_key is None:
        for table_key in sorted_keys:
            cols = schema_columns.get(table_key, [])
            if any(normalize_sql_identifier(c) == bare_name for c in cols):
                matched_table_key = table_key
                break

    if matched_table_key is None:
        # 当前 Schema 里没有该列 → 模型编造/未知别名，绝不删。
        return StaleColumn(candidate, None, False, "schema 未声明，判定为编造/未知列，不剔除")

    # 命中已声明列 → 数据库却报不存在 → 真实过时列，允许剔除。
    return StaleColumn(candidate, matched_table_key, True, "schema 已声明但数据源报不存在，判定过时")


def build_stale_correction(
    message: str,
    *,
    table_bindings: dict[str, Any] | None = None,
    schema_table_columns: dict[str, list[str]] | None = None,
) -> StaleCorrection:
    """从 SQL 执行错误构造当轮 Schema 纠正结果。

    参数与 ``tool_result_handlers`` 中 state 的字段一一对应：
    - ``table_bindings``      -> ``state.table_bindings``
    - ``schema_table_columns``-> ``state.schema_table_columns``
    任意一个为空时退化为无纠正（不误伤）。
    """
    schema_columns = dict(schema_table_columns or {})
    table_bindings = dict(table_bindings or {})
    if not schema_columns or not table_bindings:
        return StaleCorrection()

    table_keys = set(schema_columns.keys())
    candidates = _candidate_identifiers(str(message or ""))

    stale_list: list[StaleColumn] = []
    for cand in candidates:
        stale_list.append(
            classify_stale(cand, table_keys=table_keys, schema_columns=schema_columns)
        )
    droppable = [s for s in stale_list if s.safe_to_drop]
    if not droppable:
        return StaleCorrection(stale_columns=stale_list)

    # 统计每张表需要剔除的失效列集合（支持单表多失效列）
    dropped_by_table: dict[str, set[str]] = {}
    for s in droppable:
        if s.table_key:
            dropped_by_table.setdefault(s.table_key, set()).add(
                normalize_sql_identifier(s.field_name)
            )

    # 构造纠正后的当轮可见 Schema 文本：剔除全部失效列。
    corrected = _drop_columns_from_schema_output(
        dropped_by_table,
        schema_table_columns=schema_columns,
        table_bindings=table_bindings,
    )
    remaining = _remaining_table_columns(dropped_by_table, schema_columns)
    return StaleCorrection(
        stale_columns=stale_list,
        corrected_schema_output=corrected,
        remaining_table_columns=remaining,
    )


def _drop_columns_from_schema_output(
    dropped_by_table: dict[str, set[str]],
    *,
    schema_table_columns: dict[str, list[str]],
    table_bindings: dict[str, Any],
) -> str:
    """基于现有 table_bindings / schema_table_columns 重建一份剔除失效列的 Schema YAML。

    支持每张表剔除多个失效列（传入 dropped_by_table 映射表到规范化字段名集合）。
    """
    lines: list[str] = []
    for table_key, binding in table_bindings.items():
        cols = schema_table_columns.get(table_key, [])
        if not cols:
            continue
        drop_set = dropped_by_table.get(table_key, set())
        remain = [
            c
            for c in cols
            if normalize_sql_identifier(c) not in drop_set
        ]
        dataset_name = str(getattr(binding, "dataset_name", "") or "")
        physical = str(getattr(binding, "physical_name", "") or table_key)
        if dataset_name:
            lines.append(f"dataset: {dataset_name}")
        lines.append(f"table_name: {physical}")
        if not remain:
            lines.append(_TABLE_EXHAUSTED_MARKER)
        else:
            lines.append("columns: " + ", ".join(remain))
        lines.append("")
    return "\n".join(lines).strip()


def _remaining_table_columns(
    dropped_by_table: dict[str, set[str]],
    schema_columns: dict[str, list[str]],
) -> dict[str, list[str]]:
    """剔除失效列后，各表剩余可用列（供 prompt / final guard 用）。"""
    remaining: dict[str, list[str]] = {}
    for table_key, cols in schema_columns.items():
        dropped = dropped_by_table.get(table_key, set())
        keep = [c for c in cols if normalize_sql_identifier(c) not in dropped]
        if keep:
            remaining[table_key] = keep
    return remaining


def build_stale_repair_hint(correction: StaleCorrection) -> str:
    """生成给模型的强约束修复提示：已剔除的失效列 + 各表剩余可用列。"""
    if not correction.usable:
        return ""
    dropped = [
        f"{s.field_name}（表 {s.table_key}）" for s in correction.stale_columns if s.safe_to_drop
    ]
    remaining_lines: list[str] = []
    for table_key, cols in correction.remaining_table_columns.items():
        remaining_lines.append(f"- {table_key}: {', '.join(cols)}")

    text = (
        "【以数据库为准，剔除过时字段】数据库实际执行后明确指出以下列已不存在，"
        f"平台已从当前 Schema 中剔除：{', '.join(dropped)}。\n"
        "请在重写 SQL 时彻底移除这些字段（SELECT/JOIN/WHERE/GROUP BY/ORDER BY/别名均不得再引用），"
        "只使用下列数据库确认仍然存在的物理列：\n"
        + ("\n".join(remaining_lines) if remaining_lines else "（相关表已无可查询列，请更换查询对象）")
        + "\n禁止凭空编造新列名；若确无可用列，应如实说明无法查询。"
    )
    return text


def is_stale_driver_error(message: str) -> bool:
    """是否属于「字段/列不存在」类错误（方案 A 的驱动条件）。"""
    return is_schema_reference_sql_error(message)
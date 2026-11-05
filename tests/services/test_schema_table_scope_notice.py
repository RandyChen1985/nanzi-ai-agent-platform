"""schema 检索被表级白名单挡掉时，要告诉模型「有表但没权限」。

静默剔除的副作用：模型只看到「没有这张表」，于是回答「数据集里没有用户表」，
或者凭记忆编一个表名再试一轮。这里锁住三点：被挡的表会被计数、提示会拼到
schema 文本末尾、提示里不出现具体表名（表名也可能是敏感信息）。
"""
from __future__ import annotations

import pathlib

from app.services.chatbi_dataset_schema_service import (
    _append_table_scope_notice,
    _filter_hits_by_table_scope,
)

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SERVICE = (_REPO / "app/services/chatbi_dataset_schema_service.py").read_text(encoding="utf-8")


class _Dataset:
    def __init__(self, dataset_id: int):
        self.id = dataset_id
        self.name = f"ds{dataset_id}"
        self.display_name = f"数据集{dataset_id}"
        self.rag_dataset_id = ""


def _hit(content: str, dataset_id: int) -> dict:
    return {
        "content": content,
        "doc_name": "schema",
        "similarity": 0.9,
        "metadata_dataset_id": str(dataset_id),
    }


def test_dropped_tables_are_counted() -> None:
    """白名单外的表块被剔除，并且计数传给调用方。"""
    dropped: list[str] = []
    items = [
        _hit("dataset_id: 1\ntable_name: ai_agent_access_logs\ncolumns: []", 1),
        _hit("dataset_id: 1\ntable_name: ai_agent_users\ncolumns: []", 1),
    ]
    kept = _filter_hits_by_table_scope(
        items,
        authorized_datasets=[_Dataset(1)],
        allowed_table_names_by_dataset={1: {"ai_agent_access_logs"}},
        dropped_out=dropped,
    )
    assert len(kept) == 1
    assert dropped == ["ai_agent_users"]


def test_dropped_out_is_optional() -> None:
    """不传 dropped_out 时行为不变（老调用点无需改）。"""
    items = [_hit("dataset_id: 1\ntable_name: ai_agent_users\ncolumns: []", 1)]
    kept = _filter_hits_by_table_scope(
        items,
        authorized_datasets=[_Dataset(1)],
        allowed_table_names_by_dataset={1: {"ai_agent_access_logs"}},
    )
    assert kept == []


def test_notice_mentions_count_but_never_table_names() -> None:
    """提示只说数量，不泄露表名，并要求模型照实说明权限而不是编表名。"""
    text = _append_table_scope_notice("SCHEMA_BODY", 3)
    assert text.startswith("SCHEMA_BODY")
    assert "3 张" in text
    assert "授权范围" in text
    assert "不要猜测表名" in text
    # 提示文本里不允许出现任何表名占位
    assert "ai_agent_users" not in text


def test_notice_is_skipped_when_nothing_dropped() -> None:
    assert _append_table_scope_notice("SCHEMA_BODY", 0) == "SCHEMA_BODY"


def test_notice_is_skipped_for_empty_schema() -> None:
    """检索彻底没结果时不要拼一段「另有 N 张表」——那会误导模型以为有东西可查。"""
    assert _append_table_scope_notice("", 2) == ""
    assert _append_table_scope_notice("   ", 2) == "   "


def test_both_retrieval_paths_wire_the_notice() -> None:
    """RAGFlow 与 local 向量两条检索路径都要把计数接到输出上。"""
    assert _SERVICE.count("dropped_out=dropped_table_hits") == 2
    assert _SERVICE.count("_append_table_scope_notice(schema_text, len(dropped_table_hits))") == 2


def test_fallback_path_counts_blocked_tables() -> None:
    """MySQL 关键词兜底路径也要算出「被挡掉几张表」，否则提示只在向量检索时出现。"""
    assert "_total = await session.scalar(" in _SERVICE
    assert 'dropped_table_hits.extend(["_"] * max(0, int(_total or 0) - len(_allowed)))' in _SERVICE


# ---------------------------------------------------------------------------
# 指标块：sql 字段是完整 SQL，同样按表级白名单过滤
# ---------------------------------------------------------------------------

_METRICS_CHUNK = """# 说明：本文件仅为指标计算口径参考。
dataset: ai_agent_users_ds
metrics_scope: 用户主数据
metrics:
- name: total_users
  display: 总用户数
  sql: SELECT count(*) AS total_users FROM ai_agent_users
- name: secret_total
  display: 机密统计
  sql: SELECT count(*) FROM secret_payroll
- name: broken_metric
  display: 解析不了的
  sql: countIf(status = 1)
"""


def test_metrics_chunk_drops_metrics_referencing_blocked_tables() -> None:
    """引用白名单外表的指标要逐条剔除，而不是把整块 SQL 交给模型。"""
    from app.services.chatbi_dataset_schema_service import _filter_metrics_chunk_by_table_scope

    kept = _filter_metrics_chunk_by_table_scope(_METRICS_CHUNK, {"ai_agent_users"})
    assert kept is not None
    assert "total_users" in kept
    # 无权表的指标连同它的 SQL 一起消失
    assert "secret_payroll" not in kept
    assert "secret_total" not in kept
    # 解析不出表名的指标保守保留，交给执行层兜底
    assert "broken_metric" in kept
    # 重组后仍然是指标块（否则会退化成表块被别的分支误判）
    assert "metrics_scope:" in kept


def test_metrics_chunk_returns_none_when_everything_blocked() -> None:
    """所有指标都引用了无权表 → 整块丢弃。

    注意 `sql` 解析不出表名的指标（如纯表达式 `countIf(status = 1)`）会被保守保留：
    它本身不含 FROM，不泄露表名，交给执行层兜底即可。
    """
    from app.services.chatbi_dataset_schema_service import _filter_metrics_chunk_by_table_scope

    chunk = """dataset: ads
metrics_scope: 广告
metrics:
- name: a
  sql: SELECT count(*) FROM secret_a
- name: b
  sql: SELECT sum(x) FROM secret_b
"""
    assert _filter_metrics_chunk_by_table_scope(chunk, {"nothing_allowed"}) is None


def test_metrics_chunk_untouched_when_all_allowed() -> None:
    """全部允许时原样返回，避免无谓的 YAML 重排改动检索文本。"""
    from app.services.chatbi_dataset_schema_service import _filter_metrics_chunk_by_table_scope

    allowed = {"ai_agent_users", "secret_payroll"}
    assert _filter_metrics_chunk_by_table_scope(_METRICS_CHUNK, allowed) == _METRICS_CHUNK


def test_metrics_filter_wired_into_both_paths() -> None:
    """向量检索与 MySQL 兜底两条路径都要过滤指标块。"""
    assert _SERVICE.count("_filter_metrics_chunk_by_table_scope(") >= 3
    assert '"metrics_scope:" in str(content)' in _SERVICE


def test_unknown_dataset_falls_back_to_allowlist_union() -> None:
    """归属识别不出来时用白名单并集兜底，不能整块放行。

    向量索引若缺 `metadata_dataset_id`，`_metadata_dataset_id_from_hit` 会返回 None；
    早期实现把它当成「不限表」直接保留，等于索引一缺字段，表级收窄就失效。
    """
    from app.services.chatbi_dataset_schema_service import _filter_hits_by_table_scope

    class _Ds:
        id = 1
        name = "ds1"
        display_name = "数据集1"
        rag_dataset_id = ""

    hit = {
        "content": "dataset_id: 1\ntable_name: secret_payroll\ncolumns: []",
        "doc_name": "x",
        "metadata_dataset_id": "",   # 归属缺失
        "rag_dataset_id": "",
    }
    dropped: list[str] = []
    kept = _filter_hits_by_table_scope(
        [hit],
        authorized_datasets=[_Ds()],
        allowed_table_names_by_dataset={1: {"ai_agent_access_logs"}},
        dropped_out=dropped,
    )
    assert kept == []
    assert dropped == ["secret_payroll"]


def test_unknown_dataset_still_passes_when_scope_unrestricted() -> None:
    """并集为空说明各数据集都不限表，此时保持放行。"""
    from app.services.chatbi_dataset_schema_service import _filter_hits_by_table_scope

    class _Ds:
        id = 1
        name = "ds1"
        display_name = "数据集1"
        rag_dataset_id = ""

    hit = {"content": "dataset_id: 1\ntable_name: any_table\ncolumns: []", "doc_name": "x"}
    kept = _filter_hits_by_table_scope(
        [hit],
        authorized_datasets=[_Ds()],
        allowed_table_names_by_dataset={1: None},   # MODE_ALL
    )
    assert len(kept) == 1

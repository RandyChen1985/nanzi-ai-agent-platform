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

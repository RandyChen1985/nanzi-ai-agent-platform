"""ChatBI 查数底线（GLOBAL_GUARDRAILS）与 Schema chunk 的分层契约。

背景：同一个议题原先存在 **3 处口径**且互相打架——

1. `DataQueryPrompts.GLOBAL_GUARDRAILS` 的规则 10.1 / 16（代码，始终注入）
2. `MetadataRagService.render_table_schema_yaml` 的 chunk note（**进入向量索引正文**）
3. 规则 12「必须本轮直接给出结论、禁止停轮」

典型冲突：
- 用户没给时间范围时，chunk note 说「**先收窄时间/数量**」（授权静默收窄），
  规则 10.1 说「**先追问**」，规则 12 又说「**必须出结论、别停轮」——
  模型最省事的解法就是静默收窄后照常回答，用户会拿到被悄悄缩小口径的答案。
- 「未标注维度的字段能否 GROUP BY」：chunk note 说「**优先**用」（柔和），
  规则 16 说「**不得**作为分组维度」（绝对禁止）。而 `dimension_role` 默认为 `none`
  且为 `none` 时根本不输出该键，于是「无该标记」是**默认状态**——
  字面执行规则 16 等于「没有任何字段可以分组」，会把普通的「按X分组」查询打坏。

因此确立分层：
- **字段语义（是什么）** → 随 Schema 走（chunk），描述性、无命令动词
- **行为规则（怎么用）** → 只留 `GLOBAL_GUARDRAILS`（代码），单一权威

副产物：调整查询策略不再需要重建向量索引。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.metadata_rag_service import MetadataRagService

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]


def _render_chunk() -> str:
    """渲染一份真实的 Schema chunk（即进入向量索引正文的文本）。"""
    dataset = SimpleNamespace(
        name="orders_ds",
        display_name="订单数据集",
        data_source="mysql_primary",
    )
    table = SimpleNamespace(
        id=1,
        physical_name="orders",
        term="订单表",
        description="订单明细",
        synonyms=[],
        partition_fields=["event_date"],
        index_fields=["tenant_id"],
        columns=[],
    )
    return MetadataRagService.render_table_schema_yaml(dataset, table)


@pytest.fixture(scope="module")
def guardrails() -> str:
    return DataQueryPrompts.GLOBAL_GUARDRAILS


@pytest.fixture(scope="module")
def chunk() -> str:
    return _render_chunk()


# --- 规则 16：不得再绝对禁止未标注字段分组 ---


def test_guardrails_no_longer_forbid_grouping_by_unmarked_fields(guardrails: str) -> None:
    """`dimension_role` 默认 none 且 none 时不输出该键，绝对禁止会打坏普通分组查询。"""
    assert "不得作为分组维度" not in guardrails


def test_guardrails_state_unmarked_fields_remain_usable(guardrails: str) -> None:
    assert "未标注的字段不是禁用项" in guardrails
    # 必须明确「用户点名了未标注字段时照常用」，否则模型仍可能反问或拒绝
    assert "照常用它做 GROUP BY" in guardrails


def test_guardrails_still_forbid_inventing_dimensions(guardrails: str) -> None:
    """放宽的是「未标注」，不是「不存在」——禁止臆造维度必须保留。"""
    assert "禁止 GROUP BY Schema 中不存在的字段" in guardrails


def test_guardrails_keep_hierarchy_drilldown_semantics(guardrails: str) -> None:
    assert "hierarchy_group" in guardrails
    assert "hierarchy_order" in guardrails


# --- 规则 10.1：不得造条件，也不得静默收窄范围 ---


def test_guardrails_forbid_fabricating_filter_values(guardrails: str) -> None:
    """防止模型为了「用上索引」而硬加用户没提出的条件（如 tenant_id='default'）。"""
    assert "不得臆造过滤取值" in guardrails
    assert "添加用户没有提出的过滤条件" in guardrails


def test_guardrails_scope_partition_use_to_user_provided_conditions(guardrails: str) -> None:
    """分区裁剪只能落实用户已提出的范围，不能凭空生成过滤条件。"""
    assert "当问题本身带有时间/范围条件时" in guardrails


def test_guardrails_require_disclosing_the_range_actually_used(guardrails: str) -> None:
    """与规则 12「必须本轮出结论」的冲突，用「必须披露」而不是「静默收窄」来化解。"""
    assert "显著说明实际使用的范围与口径" in guardrails


def test_guardrails_no_longer_authorize_silent_narrowing(guardrails: str) -> None:
    assert "先收窄" not in guardrails


def test_guardrails_keep_the_unbounded_scan_ban(guardrails: str) -> None:
    """行为规则搬家，但底线本身一条都不能丢。"""
    assert "禁止 SELECT *" in guardrails
    assert "无界大结果集" in guardrails


# --- chunk note：只描述语义，不承载行为规则 ---

BEHAVIORAL_PHRASES = [
    "先收窄",
    "必须优先",
    "禁止无界全表扫描",
    "不得作为分组维度",
    "查询必须尽量命中分区和索引",
]


@pytest.mark.parametrize("phrase", BEHAVIORAL_PHRASES)
def test_chunk_note_carries_no_behavioral_rules(chunk: str, phrase: str) -> None:
    """chunk note 会进入向量索引正文；行为规则放这里会导致改策略必须重建索引。"""
    assert phrase not in chunk


def test_chunk_note_still_describes_the_field_semantics(chunk: str) -> None:
    """去掉行为规则不等于去掉字段语义——模型仍须知道这些键是什么意思。"""
    for field in ("partition_fields", "index_fields", "dimension_role", "hierarchy_group", "hierarchy_order"):
        assert field in chunk, f"chunk 丢失了对 {field} 的语义说明"
    assert "- event_date" in chunk
    assert "- tenant_id" in chunk


def test_chunk_note_keeps_the_table_name_discipline() -> None:
    """与本次无关但同样重要的既有约束不得被误删。"""
    chunk = _render_chunk()
    assert "禁止写成 FROM dataset.table_name" in chunk
    assert "不可当表名" in chunk


# --- 分层本身：行为规则只能有一处权威 ---


def test_behavioral_rules_have_a_single_source_of_truth(guardrails: str, chunk: str) -> None:
    """同一议题不得在两处给出不同强度：chunk 里不得再出现任何行为命令。"""
    contradictions = [p for p in BEHAVIORAL_PHRASES if p in chunk]
    assert contradictions == [], (
        f"这些行为规则同时出现在索引正文与代码常量中，口径会漂移：{contradictions}"
    )
    # 而权威侧必须确实给出这些规则
    assert "禁止 SELECT *" in guardrails

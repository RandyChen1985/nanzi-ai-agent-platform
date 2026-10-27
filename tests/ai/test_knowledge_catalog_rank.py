"""rank_knowledge_catalog 的排序、截断与容错行为。

``match_knowledge_catalog`` 仍被外层路由使用，这里同时锁住它的既有语义，
避免为新增排序能力而改动它。
"""
from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    KnowledgeBaseCatalogItem,
    match_knowledge_catalog,
    rank_knowledge_catalog,
)


def _catalog(items):
    return AuthorizedKnowledgeCatalog(status="available", items=tuple(items))


def test_rank_orders_by_score_and_limits():
    catalog = _catalog(
        [
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id="ds_fin",
                name="财务制度库",
                description="报销 发票 差旅",
            ),
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id="ds_travel",
                name="差旅管理",
                description="报销 差旅 标准",
            ),
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id="ds_ops",
                name="运维手册",
                description="部署 回滚",
            ),
        ]
    )

    ranked = rank_knowledge_catalog("差旅报销标准", catalog, limit=2)

    assert len(ranked) == 2
    assert {item[0] for item in ranked} == {"ds_fin", "ds_travel"}
    assert ranked[0][1] >= ranked[1][1]


def test_rank_returns_empty_without_overlap():
    catalog = _catalog(
        [
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id="ds_ops",
                name="运维手册",
                description="部署 回滚",
            ),
        ]
    )

    assert rank_knowledge_catalog("差旅报销标准", catalog, limit=3) == []


def test_rank_returns_empty_for_unavailable_catalog():
    assert rank_knowledge_catalog("报销", None, limit=3) == []
    assert rank_knowledge_catalog("报销", AuthorizedKnowledgeCatalog(status="empty"), limit=3) == []


def test_rank_respects_limit_of_one():
    catalog = _catalog(
        [
            KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_fin", name="财务制度库", description="报销 差旅"),
            KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_travel", name="差旅管理", description="报销 差旅"),
        ]
    )

    assert len(rank_knowledge_catalog("差旅报销", catalog, limit=1)) == 1


def test_match_knowledge_catalog_behavior_unchanged():
    catalog = _catalog(
        [
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id="ds_fin",
                name="财务制度库",
                description="报销 发票 差旅",
            ),
        ]
    )

    matched = match_knowledge_catalog("差旅报销标准", catalog)

    assert matched.matched_ids == ("ds_fin",)
    assert matched.confidence in ("weak", "strong")

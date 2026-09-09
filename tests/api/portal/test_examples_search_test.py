"""案例集检索测试端点 (POST /api/portal/examples/search-test) 的契约测试。"""

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from app.api.portal.endpoints.examples import ExampleSearchTestRequest, search_test_examples


pytestmark = pytest.mark.no_infrastructure


def _make_request(**overrides):
    defaults = {
        "query": "查询上海机房本月 PUE",
        "metadata_provider": "default",
        "top_k": None,
        "similarity_threshold": None,
        "vector_weight": None,
    }
    defaults.update(overrides)
    return ExampleSearchTestRequest(**defaults)


HIT_ITEMS = [
    {
        "id": 12,
        "question": "查询上海机房本月 PUE",
        "sql": "SELECT * FROM dc_pue WHERE site='上海'",
        "dataset_name": "机房能耗数据集",
        "similarity": 0.91,
    }
]


async def test_search_test_empty_query_rejected():
    with pytest.raises(HTTPException) as exc:
        await search_test_examples(_make_request(query="   "), _=object())
    assert exc.value.status_code == 400


async def test_search_test_hit_returns_items_and_hit_log():
    async def fake_search(query, dataset_id=None, top_k=None, history=None, stats_dump=None,
                          provider_override=None, threshold_override=None, vector_weight_override=None):
        stats_dump.update({
            "mode": "local",
            "query": query,
            "rewritten_query": query,
            "top_k": top_k or 5,
            "similarity_threshold": 0.4,
            "vector_recalled": 3,
            "valid_sql_after_filter": 1,
            "mysql_fallback_hits": 0,
            "mysql_keywords": ["上海", "PUE"],
        })
        return HIT_ITEMS

    with patch(
        "app.api.portal.endpoints.examples.ExampleService.search_examples",
        new=fake_search,
    ):
        res = await search_test_examples(
            _make_request(metadata_provider="local", top_k=5),
            _=object(),
        )

    assert res["code"] == 200
    data = res["data"]
    assert data["found"] is True
    assert data["provider"] == "local"
    assert data["count"] == 1
    assert data["items"] == HIT_ITEMS
    assert data["elapsed_ms"] >= 0
    joined_logs = "\n".join(data["logs"])
    assert "[HIT] 命中 1 条相似案例" in joined_logs
    assert "local · Redis 向量检索" in joined_logs
    assert "检索词: 「查询上海机房本月 PUE」" in joined_logs
    assert "向量/原始召回 3 条" in joined_logs
    assert "阈值与可用 SQL 过滤后有效 1 条" in joined_logs
    assert "关键词兜底 0 条〔上海、PUE〕" in joined_logs
    assert "[TIME]" in joined_logs
    # 手动覆盖了 local，日志应体现覆盖来源
    assert "（来自手动覆盖）" in joined_logs


async def test_search_test_miss_returns_empty_with_miss_log():
    async def fake_search(query, dataset_id=None, top_k=None, history=None, stats_dump=None,
                          provider_override=None, threshold_override=None, vector_weight_override=None):
        stats_dump.update({
            "mode": "ragflow",
            "query": query,
            "rewritten_query": query,
            "top_k": 5,
            "similarity_threshold": 0.4,
            "vector_recalled": 0,
            "valid_sql_after_filter": 0,
        })
        return []

    with patch(
        "app.api.portal.endpoints.examples.ExampleService.search_examples",
        new=fake_search,
    ):
        res = await search_test_examples(_make_request(), _=object())

    assert res["code"] == 200
    data = res["data"]
    assert data["found"] is False
    assert data["provider"] == "ragflow"
    assert data["count"] == 0
    assert data["items"] == []
    joined_logs = "\n".join(data["logs"])
    assert "[MISS] 未找到足够相似的优质 SQL 案例" in joined_logs
    # 跟随系统配置：日志不应出现"来自手动覆盖"
    assert "（来自手动覆盖）" not in joined_logs


async def test_search_test_passes_override_params_to_service():
    mock_search = AsyncMock(return_value=[])
    with patch(
        "app.api.portal.endpoints.examples.ExampleService.search_examples",
        mock_search,
    ):
        await search_test_examples(
            _make_request(
                query="本月销售额",
                metadata_provider="ragflow",
                top_k=8,
                similarity_threshold=0.6,
                vector_weight=0.7,
            ),
            _=object(),
        )

    _, kwargs = mock_search.call_args
    assert kwargs["provider_override"] == "ragflow"
    assert kwargs["threshold_override"] == 0.6
    assert kwargs["vector_weight_override"] == 0.7
    assert kwargs["top_k"] == 8
    assert kwargs["history"] is None
    assert kwargs["dataset_id"] is None


async def test_search_test_default_provider_does_not_force_override():
    mock_search = AsyncMock(return_value=[])
    with patch(
        "app.api.portal.endpoints.examples.ExampleService.search_examples",
        mock_search,
    ):
        await search_test_examples(_make_request(), _=object())

    _, kwargs = mock_search.call_args
    assert kwargs["provider_override"] is None
    assert kwargs["threshold_override"] is None
    assert kwargs["vector_weight_override"] is None


async def test_search_test_service_exception_returns_miss_with_error_log():
    async def failing_search(*_args, **_kwargs):
        raise RuntimeError("redis down")

    with patch(
        "app.api.portal.endpoints.examples.ExampleService.search_examples",
        new=failing_search,
    ):
        res = await search_test_examples(_make_request(), _=object())

    assert res["code"] == 200
    data = res["data"]
    assert data["found"] is False
    assert data["items"] == []
    assert any("[ERROR] 检索过程发生异常" in log for log in data["logs"])
    assert any("redis down" in log for log in data["logs"])


# ---- service 层：override 参数行为 ----

@pytest.mark.asyncio
async def test_search_examples_provider_override_forces_local():
    """即使系统配置为 ragflow，provider_override='local' 也应走本地链路。"""
    from app.services.chatbi_example_service import ExampleService

    async def mock_config_get(key, default=None):
        if key == "metadata_provider":
            return "ragflow"  # 系统默认 ragflow
        if key == "chatbi_sample_top_k":
            return "5"
        if key == "chatbi_sample_similarity_threshold":
            return "0.4"
        return default

    with patch("app.services.chatbi_example_service.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", return_value=[0.1]), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", return_value=[]) as mock_knn, \
         patch("app.services.chatbi_example_service.ExampleService._search_mysql_fallback", return_value=[]) as mock_fallback:
        result = await ExampleService.search_examples(
            query="上海 PUE",
            top_k=None,
            provider_override="local",
            stats_dump={},
        )

    assert result == []
    mock_knn.assert_awaited_once()  # local 链路被真正调用
    mock_fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_examples_threshold_override_filters_differently():
    """threshold_override 应替换系统阈值参与过滤。"""
    from app.services.chatbi_example_service import ExampleService

    async def mock_config_get(key, default=None):
        if key == "metadata_provider":
            return "local"
        if key == "chatbi_sample_top_k":
            return "5"
        if key == "chatbi_sample_similarity_threshold":
            return "0.4"
        return default

    knn_hits = [
        {"id": 1, "question": "q1", "sql": "SELECT 1", "similarity": 0.5},
        {"id": 2, "question": "q2", "sql": "SELECT 2", "similarity": 0.7},
    ]

    with patch("app.services.chatbi_example_service.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", return_value=[0.1]), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", return_value=knn_hits) as mock_knn, \
         patch("app.services.chatbi_example_service.ExampleService._search_mysql_fallback", return_value=[]):
        # 阈值 0.6：只应保留 similarity>=0.6 的一条，直接命中不再走兜底
        result = await ExampleService.search_examples(
            query="q",
            top_k=5,
            threshold_override=0.6,
        )

    mock_knn.assert_awaited_once()
    assert len(result) == 1
    assert result[0]["id"] == 2

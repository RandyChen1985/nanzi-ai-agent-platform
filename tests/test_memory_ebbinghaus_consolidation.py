"""Test suite for Ebbinghaus memory reranking and consolidation pipeline."""
import math
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.ai.memory_index_service import MemoryIndexService
from app.services.memory_config_service import MemoryConfigService

pytestmark = pytest.mark.no_infrastructure


def test_ebbinghaus_decay_calculation_and_sorting():
    # 构造测试数据
    now = time.time()
    items = [
        {
            "conversation_id": "conv_stale_high_match",
            "score": 0.90,
            "last_active": now - (30 * 86400),  # 30天前
            "reference_count": 0,
        },
        {
            "conversation_id": "conv_fresh_low_match",
            "score": 0.80,
            "last_active": now - (1 * 86400),   # 1天前
            "reference_count": 3,               # 被多次引用
        },
        {
            "conversation_id": "conv_recent_no_ref",
            "score": 0.70,
            "last_active": now - (0.5 * 86400), # 12小时前
            "reference_count": 0,
        }
    ]

    # 执行重排计算，基础半衰期设为 7.0 天
    reranked = MemoryIndexService._apply_ebbinghaus_decay(items, base_half_life=7.0)

    # 1. 验证排序结果
    # conv_fresh_low_match 应该因为新鲜且有多次引用对数增强排在第1位
    # conv_recent_no_ref 排第2位
    # conv_stale_high_match (30天前且0引用) 应该衰减严重，排在最后
    assert reranked[0]["conversation_id"] == "conv_fresh_low_match"
    assert reranked[1]["conversation_id"] == "conv_recent_no_ref"
    assert reranked[2]["conversation_id"] == "conv_stale_high_match"

    # 2. 验证得分计算准确性
    # 对于 items[0] (stale):
    # t = 30
    # S = 7.0 * (1 + ln(1 + 0)) = 7.0
    # R = exp(-30 / 7.0) = exp(-4.28) = 0.0138
    # final_score = 0.90 * 0.0138 = 0.0124
    stale_item = next(it for it in reranked if it["conversation_id"] == "conv_stale_high_match")
    expected_R = math.exp(-30.0 / 7.0)
    assert math.isclose(stale_item["final_score"], 0.90 * expected_R, rel_tol=1e-5)


@pytest.mark.asyncio
async def test_search_summaries_uses_ebbinghaus_decay_in_knn():
    # Mock redis and FT.SEARCH output
    redis = AsyncMock()
    redis.execute_command = AsyncMock(return_value=[
        2,  # Return count
        b"memory:summary:user_123:conv_stale",
        [
            b"user_id", b"user_123",
            b"conversation_id", b"conv_stale",
            b"title", b"Title A",
            b"summary", b"Summary A",
            b"last_active", str(int(time.time() - 30 * 86400)).encode("utf-8"),
            b"turn_count", b"1",
            b"score", b"0.10", # Cosine distance = 0.10 -> score = 0.90
            b"reference_count", b"0"
        ],
        b"memory:summary:user_123:conv_fresh",
        [
            b"user_id", b"user_123",
            b"conversation_id", b"conv_fresh",
            b"title", b"Title B",
            b"summary", b"Summary B",
            b"last_active", str(int(time.time() - 1 * 86400)).encode("utf-8"),
            b"turn_count", b"1",
            b"score", b"0.20", # Cosine distance = 0.20 -> score = 0.80
            b"reference_count", b"5"
        ]
    ])

    with patch(
        "app.services.ai.memory_index_service.get_redis_binary",
        new_callable=AsyncMock,
        return_value=redis
    ), patch(
        "app.services.memory_config_service.MemoryConfigService.get_float",
        new_callable=AsyncMock,
        return_value=7.0
    ):
        results = await MemoryIndexService._search_summaries_knn(
            user_id="user_123",
            query_embedding=[0.1] * 1536,
            limit=5
        )

        assert len(results) == 2
        # 重排后，最新的应该排在前面
        assert results[0]["conversation_id"] == "conv_fresh"
        assert results[1]["conversation_id"] == "conv_stale"


@pytest.mark.asyncio
async def test_consolidate_user_memories_clustering_and_merging():
    # 构造 3 条高度相似（余弦距离相似度大于 0.82）的记忆，和 1 条不相似的记忆
    now = time.time()
    mock_memories = [
        {
            "conversation_id": "conv_a",
            "summary": "用户喜欢 ClickHouse 数据库",
            "has_embedding": True,
            "_embedding_vec": [0.1, 0.2, 0.3],
            "summary_type": "session",
            "reference_count": 2,
            "last_active": now
        },
        {
            "conversation_id": "conv_b",
            "summary": "ClickHouse 数据库很符合用户胃口",
            "has_embedding": True,
            "_embedding_vec": [0.11, 0.2, 0.3], # 极度相似
            "summary_type": "session",
            "reference_count": 1,
            "last_active": now
        },
        {
            "conversation_id": "conv_c",
            "summary": "用户倾向使用 ClickHouse 分析 PUE",
            "has_embedding": True,
            "_embedding_vec": [0.1, 0.21, 0.3], # 与 A 连通，与 B 也相似
            "summary_type": "session",
            "reference_count": 0,
            "last_active": now
        },
        {
            "conversation_id": "conv_d",
            "summary": "今天天气非常不错",
            "has_embedding": True,
            "_embedding_vec": [0.9, 0.8, 0.7], # 完全不相似
            "summary_type": "session",
            "reference_count": 0,
            "last_active": now
        }
    ]

    # Mock cosine 距离计算，以确保 a-b, b-c 大于阈值 0.82，d 均小于
    def mock_cosine(v1, v2):
        if v1 == [0.9, 0.8, 0.7] or v2 == [0.9, 0.8, 0.7]:
            return 0.1
        return 0.95 # 其他的都大于 0.82 阈值

    redis = AsyncMock()
    redis.hset = AsyncMock()

    # Mock LLM calls and generate_text
    chat_client = AsyncMock()
    chat_client.generate_text = AsyncMock(return_value="用户对使用 ClickHouse 数据库有强烈偏好。")

    with patch(
        "app.services.ai.memory_index_service.MemoryIndexService.list_summaries",
        new_callable=AsyncMock,
        return_value=mock_memories
    ), patch(
        "app.services.ai.memory_index_service._cosine",
        side_effect=mock_cosine
    ), patch(
        "app.services.memory_config_service.MemoryConfigService.get_float",
        new_callable=AsyncMock,
        return_value=0.82
    ), patch(
        "app.services.ai.memory_index_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis
    ), patch(
        # memory_index_service 在函数内 import 这两个依赖（重构后模块级不再持有），
        # 所以必须 patch 它们的定义处，patch 服务模块上的同名属性会直接 AttributeError。
        "app.core.llm.client.get_llm_async",
        new_callable=AsyncMock,
        return_value=AsyncMock()
    ), patch(
        "app.services.ai.runtime.agentscope.chat.chat_client_from_handle",
        return_value=chat_client
    ), patch(
        # 嵌入接口在重构后统一为 embed_text(text, use_global=...)，旧的 get_embedding 已移除。
        "app.services.ai.embedding_client.EmbeddingClient.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1] * 1536
    ), patch(
        "app.services.ai.memory_index_service.MemoryIndexService.upsert_summary",
        new_callable=AsyncMock
    ) as mock_upsert, patch(
        "app.services.ai.memory_index_service.MemoryIndexService.delete_summary",
        new_callable=AsyncMock
    ) as mock_delete:
        await MemoryIndexService.consolidate_user_memories(user_id="user_123")

        # 1. 验证是否调用了大模型进行压缩合并
        chat_client.generate_text.assert_called_once()
        
        # 2. 验证合并结果是否被正确写入
        # reference_count 应继承累加值 2 + 1 + 0 = 3
        mock_upsert.assert_called_once()
        args, kwargs = mock_upsert.call_args
        assert kwargs["summary"] == "用户对使用 ClickHouse 数据库有强烈偏好。"

        # 3. 验证被合并的 3 个旧记忆碎片是否被物理清理删除
        assert mock_delete.call_count == 3
        deleted_conv_ids = [call[0][1] for call in mock_delete.call_args_list]
        assert "conv_a" in deleted_conv_ids
        assert "conv_b" in deleted_conv_ids
        assert "conv_c" in deleted_conv_ids
        assert "conv_d" not in deleted_conv_ids

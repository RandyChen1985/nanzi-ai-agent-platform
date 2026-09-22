"""向量索引维度守卫：embed_dimensions 变更后「检查/创建索引」必须真正重建。

回归背景：三个 ensure_index 原先只判「索引是否存在」，不校验 DIM。维度变更后
FT.INFO 成功即 return True，索引维度停在旧值，写入的新维度向量被 RediSearch
拒绝索引（hash_indexing_failures 上涨），向量检索静默失效。
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai import redis_index_utils
from app.services.ai.example_index_service import ExampleIndexService
from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.metadata_index_service import MetadataIndexService
from app.services.ai.redis_index_utils import index_vector_dim

pytestmark = pytest.mark.no_infrastructure

MEMORY_IDX = "nanzi:idx:memory:session_summary"


def _ft_info(dim: int, index_name: str = MEMORY_IDX) -> list:
    """RESP2 形态的 FT.INFO 回复（redis-py decode_responses=True）。"""
    return [
        "index_name",
        index_name,
        "num_docs",
        3,
        "attributes",
        [
            ["identifier", "user_id", "attribute", "user_id", "type", "TAG"],
            [
                "identifier",
                "embedding",
                "attribute",
                "embedding",
                "type",
                "VECTOR",
                "algorithm",
                "HNSW",
                "data_type",
                "FLOAT32",
                "dim",
                dim,
                "distance_metric",
                "COSINE",
            ],
        ],
    ]


def test_index_vector_dim_parses_resp2_and_resp3():
    assert index_vector_dim(_ft_info(1024)) == 1024
    # RESP3 可能直接返回 map
    assert index_vector_dim({"attributes": {"type": "VECTOR", "dim": 1536}}) == 1536
    # 非向量字段 / 空回复 / 异常结构不抛错
    assert index_vector_dim(["attributes", [["type", "TEXT"]]]) is None
    assert index_vector_dim(None) is None
    assert index_vector_dim("OK") is None


@pytest.mark.asyncio
async def test_memory_ensure_index_recreates_on_dim_mismatch():
    redis_index_utils.reset_ensure_results()
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(side_effect=[_ft_info(1024), "OK", "OK"])

    with patch(
        "app.services.ai.memory_index_service.get_redis", return_value=mock_redis
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.get_dimensions",
        return_value=1536,
    ):
        ok = await MemoryIndexService.ensure_index(force=True)

    assert ok is True
    # 关键：必须删掉旧索引，且不能带 DD —— 否则会连用户记忆一起删除
    mock_redis.execute_command.assert_any_call("FT.DROPINDEX", MEMORY_IDX)
    drop_calls = [
        c for c in mock_redis.execute_command.call_args_list
        if c.args and c.args[0] == "FT.DROPINDEX"
    ]
    assert len(drop_calls) == 1
    assert drop_calls[0].args == ("FT.DROPINDEX", MEMORY_IDX)

    create_calls = [
        c for c in mock_redis.execute_command.call_args_list
        if c.args and c.args[0] == "FT.CREATE"
    ]
    assert len(create_calls) == 1
    create_args = create_calls[0].args
    assert create_args[1] == MEMORY_IDX
    assert create_args[create_args.index("DIM") + 1] == "1536"

    detail = redis_index_utils.last_ensure_result(MEMORY_IDX)
    assert detail["action"] == "recreated"
    assert detail["index_dim"] == 1024
    assert detail["configured_dim"] == 1536


@pytest.mark.asyncio
async def test_memory_ensure_index_keeps_matching_index():
    redis_index_utils.reset_ensure_results()
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(side_effect=[_ft_info(1536)])

    with patch(
        "app.services.ai.memory_index_service.get_redis", return_value=mock_redis
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.get_dimensions",
        return_value=1536,
    ):
        ok = await MemoryIndexService.ensure_index(force=True)

    assert ok is True
    assert mock_redis.execute_command.await_count == 1
    assert redis_index_utils.last_ensure_result(MEMORY_IDX)["action"] == "ok"


@pytest.mark.asyncio
async def test_memory_ensure_index_creates_when_missing():
    redis_index_utils.reset_ensure_results()
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(
        side_effect=[Exception("Unknown index name"), "OK"]
    )

    with patch(
        "app.services.ai.memory_index_service.get_redis", return_value=mock_redis
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.get_dimensions",
        return_value=1024,
    ):
        ok = await MemoryIndexService.ensure_index(force=True)

    assert ok is True
    assert redis_index_utils.last_ensure_result(MEMORY_IDX)["action"] == "created"


@pytest.mark.asyncio
async def test_metadata_and_example_recreate_on_dim_mismatch():
    for service, idx, log_tag in (
        (MetadataIndexService, "nanzi:idx:metadata:dataset", "MetadataIndex"),
        (ExampleIndexService, "nanzi:idx:example:local", "ExampleIndex"),
    ):
        module = service.__module__
        mock_redis = AsyncMock()
        mock_redis.execute_command = AsyncMock(
            side_effect=[_ft_info(1024, idx), "OK", "OK"]
        )
        with patch(f"{module}.get_redis", return_value=mock_redis), patch(
            f"{module}.EmbeddingClient.get_dimensions", return_value=1536
        ):
            ok = await service.ensure_index()
        assert ok is True, log_tag
        mock_redis.execute_command.assert_any_call("FT.DROPINDEX", idx)
        create_args = [
            c.args for c in mock_redis.execute_command.call_args_list
            if c.args and c.args[0] == "FT.CREATE"
        ][0]
        assert create_args[create_args.index("DIM") + 1] == "1536", log_tag


@pytest.mark.asyncio
async def test_rebuild_index_reports_real_action():
    with patch(
        "app.services.ai.memory_index_service.MemoryIndexService.ensure_index",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.ai.memory_index_service.last_ensure_result",
        return_value={
            "ok": True,
            "action": "recreated",
            "index_name": MEMORY_IDX,
            "index_dim": 1024,
            "configured_dim": 1536,
        },
    ):
        res = await MemoryIndexService.rebuild_index()

    assert res["ok"] is True
    assert res["action"] == "recreated"
    assert "1024 → 1536" in res["message"]


@pytest.mark.asyncio
async def test_rebuild_index_reports_failure():
    with patch(
        "app.services.ai.memory_index_service.MemoryIndexService.ensure_index",
        AsyncMock(return_value=False),
    ), patch(
        "app.services.ai.memory_index_service.last_ensure_result",
        return_value={
            "ok": False,
            "action": "failed",
            "index_name": MEMORY_IDX,
            "configured_dim": 1536,
            "message": "创建索引失败：boom",
        },
    ):
        res = await MemoryIndexService.rebuild_index()

    assert res["ok"] is False
    assert res["status"] == "error"
    assert "boom" in res["message"]


@pytest.mark.asyncio
async def test_index_status_flags_dim_mismatch():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=_ft_info(1024))

    with patch(
        "app.services.ai.memory_index_service.get_redis", return_value=mock_redis
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.get_dimensions",
        return_value=1536,
    ):
        status = await MemoryIndexService.index_status()

    assert status["available"] is True
    assert status["index_dim"] == 1024
    assert status["configured_dim"] == 1536
    assert status["dim_mismatch"] is True
    assert "重建" in status["message"]


@pytest.mark.asyncio
async def test_index_status_ok_when_dims_match():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=_ft_info(1536))

    with patch(
        "app.services.ai.memory_index_service.get_redis", return_value=mock_redis
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.get_dimensions",
        return_value=1536,
    ):
        status = await MemoryIndexService.index_status()

    assert status["dim_mismatch"] is False
    assert "message" not in status

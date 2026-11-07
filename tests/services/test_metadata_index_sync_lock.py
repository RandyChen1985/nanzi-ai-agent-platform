"""Unit tests for MetadataIndexService vector sync mutex, debouncing, and incremental hash skipping."""
import asyncio
import hashlib
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.ai.metadata_index_service import MetadataIndexService

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
async def cleanup_coordinator_state():
    """Ensure coordinator state is clean before and after each test."""
    await MetadataIndexService.reset_sync_coordinator_state()
    yield
    await MetadataIndexService.reset_sync_coordinator_state()


@pytest.mark.asyncio
async def test_incremental_hash_skips_unchanged_embedding():
    """测试当文档内容 Hash 匹配时跳过 Embedding 计算并正确上报 skipped。"""
    dataset_id = 101
    table_content = "table: user\ncolumns: [id, name]"
    content_hash = hashlib.sha256(table_content.encode("utf-8")).hexdigest()

    mock_dataset = MagicMock()
    mock_dataset.id = dataset_id
    mock_dataset.name = "test_dataset"
    mock_dataset.display_name = "测试数据集"
    mock_table = MagicMock()
    mock_table.status = 1
    mock_table.physical_name = "user"
    mock_dataset.tables = [mock_table]
    mock_dataset.relationships = []
    mock_dataset.metrics = []

    mock_db = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_redis = AsyncMock()
    # scan 查到当前已有 key
    existing_key = f"metadata:dataset:{dataset_id}:table:user"
    mock_redis.scan = AsyncMock(side_effect=[(0, [existing_key])])

    # 管道批量读取已有 doc_name 与 content_hash
    mock_pipe = AsyncMock()
    mock_pipe.hmget = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[[b"user.txt", content_hash.encode("utf-8")]])
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)

    notifications: List[Dict[str, Any]] = []

    async def _progress_cb(payload: Dict[str, Any]) -> None:
        notifications.append(payload)

    with patch("app.core.orm.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_id", return_value=mock_dataset), \
         patch("app.services.metadata_rag_service.MetadataRagService.generate_table_content", return_value=table_content), \
         patch("app.services.ai.metadata_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.upsert_vector", new_callable=AsyncMock) as mock_upsert:

        ok = await MetadataIndexService.sync_local_redis_vector(
            dataset_id=dataset_id,
            progress_cb=_progress_cb,
            wait=True,
        )

        assert ok is True
        # 由于 hash 相同，未调用 embedding 计算与 upsert
        mock_embed.assert_not_called()
        mock_upsert.assert_not_called()

        # 校验进度通知
        doc_events = [p for p in notifications if p.get("phase") == "doc"]
        assert len(doc_events) == 1
        assert doc_events[0]["skipped"] is True

        done_events = [p for p in notifications if p.get("phase") == "dataset_done"]
        assert len(done_events) == 1
        assert done_events[0]["skipped"] == 1
        assert done_events[0]["failed"] == 0


@pytest.mark.asyncio
async def test_incremental_hash_updates_changed_doc():
    """测试当文档内容 Hash 发生变动时正常触发 Embedding 计算并写入。"""
    dataset_id = 102
    table_content = "table: order\ncolumns: [id, price, amount]"
    old_hash = hashlib.sha256(b"old content").hexdigest()

    mock_dataset = MagicMock()
    mock_dataset.id = dataset_id
    mock_dataset.name = "orders_dataset"
    mock_dataset.display_name = "订单数据集"
    mock_table = MagicMock()
    mock_table.status = 1
    mock_table.physical_name = "order"
    mock_dataset.tables = [mock_table]
    mock_dataset.relationships = []
    mock_dataset.metrics = []

    mock_db = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_redis = AsyncMock()
    existing_key = f"metadata:dataset:{dataset_id}:table:order"
    mock_redis.scan = AsyncMock(side_effect=[(0, [existing_key])])

    mock_pipe = AsyncMock()
    mock_pipe.hmget = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[[b"order.txt", old_hash.encode("utf-8")]])
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)

    notifications: List[Dict[str, Any]] = []

    async def _progress_cb(payload: Dict[str, Any]) -> None:
        notifications.append(payload)

    with patch("app.core.orm.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_id", return_value=mock_dataset), \
         patch("app.services.metadata_rag_service.MetadataRagService.generate_table_content", return_value=table_content), \
         patch("app.services.ai.metadata_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1536) as mock_embed, \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.upsert_vector", new_callable=AsyncMock) as mock_upsert:

        ok = await MetadataIndexService.sync_local_redis_vector(
            dataset_id=dataset_id,
            progress_cb=_progress_cb,
            wait=True,
        )

        assert ok is True
        mock_embed.assert_called_once()
        mock_upsert.assert_called_once()

        doc_events = [p for p in notifications if p.get("phase") == "doc"]
        assert len(doc_events) == 1
        assert doc_events[0]["skipped"] is False

        done_events = [p for p in notifications if p.get("phase") == "dataset_done"]
        assert len(done_events) == 1
        assert done_events[0]["skipped"] == 0


@pytest.mark.asyncio
async def test_debounce_coalesces_rapid_calls():
    """测试连续多次调用 wait=False 在防抖窗口内合并为单次执行。"""
    dataset_id = 201
    call_count = 0

    async def _fake_do_sync(d_id: int, progress_cb=None) -> bool:
        nonlocal call_count
        call_count += 1
        return True

    with patch.object(MetadataIndexService, "_do_sync_dataset", side_effect=_fake_do_sync):
        # 连续快速触发 3 次（防抖窗口 0.05 秒）
        res1 = await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=0.05)
        res2 = await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=0.05)
        res3 = await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=0.05)

        assert res1 is True
        assert res2 is True
        assert res3 is True

        # 等待防抖触发及执行完毕
        await asyncio.sleep(0.12)

        # 3 次连续触发被合并为 1 次底层同步
        assert call_count == 1


@pytest.mark.asyncio
async def test_dirty_flag_causes_followup_run_during_sync():
    """测试在同步执行期间收到新请求时，自动标记 dirty 并在当期完成后补跑最新轮次。"""
    dataset_id = 301
    call_count = 0
    sync_started_event = asyncio.Event()

    async def _slow_sync(d_id: int, progress_cb=None) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            sync_started_event.set()
            # 模拟第一轮耗时操作
            await asyncio.sleep(0.06)
        return True

    with patch.object(MetadataIndexService, "_do_sync_dataset", side_effect=_slow_sync):
        # 启动第一轮同步
        await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=0.01)

        # 等待第一轮开始执行（已获取锁）
        await asyncio.wait_for(sync_started_event.wait(), timeout=0.5)

        # 在执行中发出新的同步请求
        await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=0.01)

        # 给予足够时间让两轮执行完毕
        await asyncio.sleep(0.15)

        # 验证因为 dirty 追赶，共执行了 2 轮同步
        assert call_count == 2


@pytest.mark.asyncio
async def test_wait_true_cancels_pending_debounce():
    """测试 wait=True 会取消尚未触发的防抖任务并独占阻塞完成。"""
    dataset_id = 401
    call_count = 0

    async def _fake_sync(d_id: int, progress_cb=None) -> bool:
        nonlocal call_count
        call_count += 1
        return True

    with patch.object(MetadataIndexService, "_do_sync_dataset", side_effect=_fake_sync):
        # 触发一个具有较长防抖窗口的任务
        await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=1.0)
        assert dataset_id in MetadataIndexService._dataset_debounce_handles

        # 立即以 wait=True 执行
        ok = await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=True)
        assert ok is True
        assert call_count == 1

        # 等待超过之前的 1.0 秒防抖时间
        await asyncio.sleep(1.05)

        # 防抖已被取消，调用计数仍为 1
        assert call_count == 1


@pytest.mark.asyncio
async def test_delete_dataset_vectors_cleans_coordinator_state():
    """测试删除数据集向量时同步取消挂起的防抖并清理协调器资源。"""
    dataset_id = 501

    # scan 必须返回 (cursor, keys)：默认的 AsyncMock 返回值没法解包
    mock_redis = AsyncMock()
    mock_redis.scan = AsyncMock(return_value=(0, []))

    with patch(
        "app.services.ai.metadata_index_service.get_redis",
        new=AsyncMock(return_value=mock_redis),
    ):
        # 挂起一个防抖任务
        await MetadataIndexService.sync_local_redis_vector(dataset_id, wait=False, debounce_seconds=1.0)
        assert dataset_id in MetadataIndexService._dataset_debounce_handles

        # 执行删除
        await MetadataIndexService.delete_dataset_vectors(dataset_id)

        # 验证防抖句柄被移除
        assert dataset_id not in MetadataIndexService._dataset_debounce_handles
        assert dataset_id not in MetadataIndexService._dataset_dirty

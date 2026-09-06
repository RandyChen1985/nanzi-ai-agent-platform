import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.conversation_identity import MissingUserIdentityError
from app.services.ai.memory_service import MemoryService

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override infrastructure initialization."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock):
        yield

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    # Mock Pipeline: redis.pipeline() 返回一个异步上下文管理器
    mock_pipe = AsyncMock()
    mock_pipe.rpush = AsyncMock(return_value=1)
    mock_pipe.ltrim = AsyncMock(return_value=True)
    mock_pipe.expire = AsyncMock(return_value=True)
    mock_pipe.execute = AsyncMock(return_value=[1, True, True])
    # 让 pipeline() 作为异步上下文管理器使用
    mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_pipe.__aexit__ = AsyncMock(return_value=False)
    redis.pipeline = MagicMock(return_value=mock_pipe)
    # 保留其他方法 Mock（供其他测试用）
    redis.lrange = AsyncMock(return_value=[])
    redis.delete = AsyncMock(return_value=1)
    # 单调 seq 游标：add_message 在入队前直接 redis.incr(seq_key) 取 seq，
    # 必须返回整数（否则 AsyncMock 被写入 message["seq"] → json.dumps 序列化失败）
    seq_counter = {"n": 0}
    async def _incr(_key):
        seq_counter["n"] += 1
        return seq_counter["n"]
    redis.incr = AsyncMock(side_effect=_incr)
    redis._mock_pipe = mock_pipe  # 暴露 pipe 引用供测试断言
    return redis

# --- Tests ---

@pytest.mark.asyncio
async def test_memory_service_get_key():
    """测试 Redis Key 生成逻辑"""
    service = MemoryService()
    key = service._get_key("user123", "conv456")
    assert key == "conversation:user123:conv456:history"
    
    with pytest.raises(MissingUserIdentityError):
        service._get_key(None, "conv789")


@pytest.mark.asyncio
async def test_memory_service_scopes_active_conversation_by_instance():
    service = MemoryService()

    assert service._get_active_conversation_key("user123") == "conversation:user123:active"
    assert (
        service._get_active_conversation_key("user123", "ops-assistant")
        == "conversation:user123:active:ops-assistant"
    )


@pytest.mark.asyncio
async def test_memory_service_reads_and_writes_instance_active_conversation(mock_redis):
    service = MemoryService()
    mock_redis.get.return_value = "conv-1"

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis

        current = await service.get_active_conversation("u1", "ops-assistant")
        await service.set_active_conversation("u1", "conv-2", "ops-assistant")

    assert current == "conv-1"
    mock_redis.get.assert_awaited_once_with("conversation:u1:active:ops-assistant")
    mock_redis.set.assert_awaited_once_with("conversation:u1:active:ops-assistant", "conv-2")

@pytest.mark.asyncio
async def test_memory_service_add_message(mock_redis):
    """测试添加消息（使用 Pipeline）"""
    service = MemoryService()
    
    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        await service.add_message("u1", "c1", "user", "Hello Redis")
        await service.add_message(
            "u1",
            "c1",
            "assistant",
            "回答",
            reasoning_content="模型推理",
            process_timeline=[{"kind": "log", "title": "调用工具: search", "status": "success"}],
            reusable_result_id="rr-1",
            reusable_result_status="reused",
            tool_run_text="search: {} -> 权限申请流程",
            status="cancelled",
        )
        
        pipe = mock_redis._mock_pipe
        
        # 验证 Pipeline 内 RPUSH 被调用且参数正确
        assert pipe.rpush.called
        args, _ = pipe.rpush.call_args_list[0]
        key, val = args
        assert key == "conversation:u1:c1:history"
        msg_data = json.loads(val)
        assert msg_data["role"] == "user"
        assert msg_data["content"] == "Hello Redis"

        _, assistant_val = pipe.rpush.call_args_list[1].args
        assistant_data = json.loads(assistant_val)
        assert assistant_data["reasoning_content"] == "模型推理"
        assert assistant_data["process_timeline"][0]["title"] == "调用工具: search"
        assert assistant_data["reusable_result_id"] == "rr-1"
        assert assistant_data["reusable_result_status"] == "reused"
        assert assistant_data["tool_run_text_version"] == "final_tool_result_v2"
        assert assistant_data["status"] == "cancelled"
        
        # 验证 LTRIM 和 EXPIRE 也在 Pipeline 中被调用
        assert pipe.ltrim.call_count == 2
        # seq 机制：每次 add_message 同时 expire history key 与 seq_counter key，
        # 因此 2 条消息共 4 次 expire
        assert pipe.expire.call_count == 4
        # 验证 execute 被调用（提交 Pipeline）
        assert pipe.execute.call_count == 2

@pytest.mark.asyncio
async def test_memory_service_get_history(mock_redis):
    """测试获取历史记录及其限额过滤"""
    service = MemoryService(max_history_turns=2) # Max 4 messages
    
    # Mock data in Redis (5 items), in list-index order (oldest first)
    mock_data = [
        json.dumps({"role": "user", "content": f"msg {i}"})
        for i in range(5)
    ]

    def _lrange_side_effect(key, start, end):
        # 模拟 Redis LRange 返回 [start, end]（含端点）区间，并支持负索引。
        if start < 0:
            start = max(len(mock_data) + start, 0)
        if end < 0:
            end = len(mock_data) + end
        else:
            end = min(end, len(mock_data) - 1)
        if start > end:
            return []
        return mock_data[start : end + 1]

    mock_redis.llen.return_value = len(mock_data)
    mock_redis.lrange.side_effect = _lrange_side_effect

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        # 1. Fetch with service default limit (4): Redis 端取窗口 [1,4]
        history = await service.get_history("u1", "c1")
        assert len(history) == 4
        assert history[-1]["content"] == "msg 4"
        assert history[0]["content"] == "msg 1"
        
        # 2. Fetch with custom limit (2): Redis 端取窗口 [3,4]
        history_limited = await service.get_history("u1", "c1", limit=2)
        assert len(history_limited) == 2
    assert history_limited[0]["content"] == "msg 3"


@pytest.mark.asyncio
async def test_memory_service_get_history_uses_one_tail_range_without_llen(mock_redis):
    service = MemoryService(max_history_turns=2)
    mock_redis.llen.return_value = 0
    mock_redis.lrange.return_value = [
        json.dumps({"role": "user", "content": "msg 2"}),
        json.dumps({"role": "assistant", "content": "msg 3"}),
    ]

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis

        history = await service.get_history("u1", "c1", limit=2, offset=1)

    assert [item["content"] for item in history] == ["msg 2", "msg 3"]
    mock_redis.lrange.assert_awaited_once_with(
        "conversation:u1:c1:history",
        -3,
        -2,
    )
    mock_redis.llen.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_service_clear_history(mock_redis):
    """测试清理历史记录"""
    service = MemoryService()
    
    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        await service.clear_history("u1", "c1")
        mock_redis.delete.assert_any_call("conversation:u1:c1:history")
        mock_redis.delete.assert_any_call("conversation:u1:c1:last_data_result")
        mock_redis.delete.assert_any_call("conversation:u1:c1:digest")
        mock_redis.delete.assert_any_call("memory:debounce:u1:c1")
        deleted_keys = {call.args[0] for call in mock_redis.delete.await_args_list}
        assert {
            "conversation:u1:c1:data_result_stack_v1",
            "conversation:u1:c1:session_tool_artifact_v1",
            "conversation:u1:c1:reusable_result_v1:current",
            "conversation:u1:c1:reusable_result_v1:stack",
        }.issubset(deleted_keys)


@pytest.mark.asyncio
async def test_memory_service_update_last_user_message_content(mock_redis):
    service = MemoryService()
    mock_redis.lrange.return_value = [
        json.dumps({"role": "user", "content": "old", "files": [{"url": "a.png"}]}),
        json.dumps({"role": "assistant", "content": "ok"}),
        json.dumps({"role": "user", "content": "latest image", "files": [{"url": "b.png"}]}),
    ]
    mock_redis.lset = AsyncMock(return_value=True)

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        updated = await service.update_last_user_message_content(
            "u1",
            "c1",
            "latest image\n\n<vision_sidecar>caption</vision_sidecar>",
        )

    assert updated is True
    mock_redis.lset.assert_awaited_once()
    index, payload = mock_redis.lset.await_args.args[1:]
    assert index == 2
    stored = json.loads(payload)
    assert stored["role"] == "user"
    assert "<vision_sidecar>" in stored["content"]
    assert stored["files"] == [{"url": "b.png"}]


@pytest.mark.asyncio
async def test_memory_service_truncate_history(mock_redis):
    service = MemoryService()
    mock_redis.ltrim = AsyncMock(return_value=True)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis

        # 截断到保留 2 条
        success = await service.truncate_history("u1", "c1", 2)
        assert success is True
        mock_redis.ltrim.assert_awaited_once_with("conversation:u1:c1:history", 0, 1)

        # 截断到 <= 0 时直接删除
        success_del = await service.truncate_history("u1", "c1", 0)
        assert success_del is True
        mock_redis.delete.assert_any_await("conversation:u1:c1:history")


@pytest.mark.asyncio
async def test_memory_service_truncate_history_resets_context_state(mock_redis):
    service = MemoryService()
    reset_state = AsyncMock()

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis, \
         patch.object(service, "reset_context_state", reset_state):
        mock_get_redis.return_value = mock_redis

        success = await service.truncate_history("u1", "c1", 2)

    assert success is True
    reset_state.assert_awaited_once_with("u1", "c1")


@pytest.mark.asyncio
async def test_memory_service_reset_context_state_clears_summary_layers_but_keeps_seq(mock_redis):
    service = MemoryService()

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis, \
         patch(
             "app.services.ai.memory_index_service.MemoryIndexService.delete_summary",
             new_callable=AsyncMock,
         ) as delete_summary:
        mock_get_redis.return_value = mock_redis

        await service.reset_context_state("u1", "c1")

    mock_redis.delete.assert_any_await("conversation:u1:c1:digest")
    mock_redis.delete.assert_any_await("memory:debounce:u1:c1")
    delete_summary.assert_awaited_once_with("u1", "c1")
    deleted_keys = {call.args[0] for call in mock_redis.delete.await_args_list}
    assert "conversation:u1:c1:seq_counter" not in deleted_keys
    mock_redis.incr.assert_awaited_once_with("conversation:u1:c1:context_revision")


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_writes_when_seq_is_current():
    service = MemoryService()

    class _Pipeline:
        def __init__(self):
            self.commands = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def watch(self, key):
            self.commands.append(("watch", key))

        async def get(self, key):
            self.commands.append(("get", key))
            return "3" if key.endswith(":seq_counter") else None

        def multi(self):
            self.commands.append(("multi",))

        def set(self, key, value, ex):
            self.commands.append(("set", key, value, ex))

        async def execute(self):
            self.commands.append(("execute",))
            return [True]

    class _Redis:
        def __init__(self):
            self.pipe = _Pipeline()

        async def get(self, key):
            return "3" if key.endswith(":seq_counter") else None

        def pipeline(self):
            return self.pipe

    redis = _Redis()
    with patch(
        "app.services.ai.memory_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        written = await service.set_digest_if_current(
            "u1", "c1", "semantic", source_seq=3
        )

    assert written is True
    assert ("set", "conversation:u1:c1:digest", "semantic", service.ttl) in redis.pipe.commands


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_skips_reset_branch():
    service = MemoryService()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=["3", "4"])

    with patch(
        "app.services.ai.memory_service.get_redis",
        new_callable=AsyncMock,
        return_value=mock_redis,
    ):
        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "stale semantic",
            source_seq=3,
            source_revision=3,
        )

    assert written is False
    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_skips_stale_source(mock_redis):
    service = MemoryService()
    mock_redis.get = AsyncMock(return_value="12")

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis

        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "[早前对话摘录]\nnew",
            source_seq=11,
        )

    assert written is False
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_allows_newer_seq_for_same_branch(
    mock_redis,
):
    service = MemoryService()
    async def get_value(key):
        if key.endswith(":seq_counter"):
            return "12"
        if key.endswith(":context_revision"):
            return "2"
        return None

    mock_redis.get = AsyncMock(side_effect=get_value)
    pipe = mock_redis.pipeline.return_value
    pipe.__aenter__.return_value = pipe
    pipe.watch = AsyncMock()
    pipe.get = AsyncMock(side_effect=get_value)
    pipe.multi = lambda: None
    pipe.set = lambda *args, **kwargs: None
    pipe.execute = AsyncMock(return_value=[True])
    pipe.reset = AsyncMock()

    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "prefix semantic",
            source_seq=11,
            source_revision=2,
            allow_newer_seq=True,
        )

    assert written is True
    pipe.execute.assert_awaited_once()


def _make_digest_fake_redis(*, stored_seq, stored_quality):
    """构造模拟 Redis，模拟已存 digest 的 seq/quality（事务路径可写）。"""

    class _Pipeline:
        def __init__(self):
            self.commands = []
            self.set_cmds = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def watch(self, key):
            self.commands.append(("watch", key))

        async def get(self, key):
            self.commands.append(("get", key))
            if key.endswith(":digest_seq"):
                return str(stored_seq)
            if key.endswith(":digest_quality"):
                return str(stored_quality)
            if key.endswith(":seq_counter"):
                return str(max(stored_seq, 0))
            return None

        def multi(self):
            self.commands.append(("multi",))

        def set(self, key, value, ex=None):
            self.set_cmds.append(("set", key, value, ex))

        def reset(self):
            self.commands.append(("reset",))

        async def execute(self):
            self.commands.append(("execute",))
            return [True]

    class _Redis:
        def __init__(self):
            self.pipe = _Pipeline()

        async def get(self, key):
            if key.endswith(":seq_counter"):
                return str(max(stored_seq, 0))
            if key.endswith(":digest_seq"):
                return str(stored_seq)
            if key.endswith(":digest_quality"):
                return str(stored_quality)
            if key.endswith(":digest"):
                return "OLD-DIGEST"
            return None

        def pipeline(self):
            return self.pipe

    redis = _Redis()
    return redis


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_override_same_seq_same_quality():
    """路由二次重压：同 seq、同 quality=0 的确定性摘要应能覆盖（override=True）。"""
    service = MemoryService()
    redis = _make_digest_fake_redis(stored_seq=3, stored_quality=0)

    with patch(
        "app.services.ai.memory_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "REBUILT-DIGEST",
            source_seq=3,
            quality=0,
            allow_newer_seq=True,
            override_same_seq_same_quality=True,
        )

    assert written is True
    assert ("set", "conversation:u1:c1:digest", "REBUILT-DIGEST", service.ttl) in redis.pipe.set_cmds


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_keeps_rejecting_without_override():
    """同 seq、同 quality=0、override=False：保持原语义，仍拒绝覆盖。"""
    service = MemoryService()
    redis = _make_digest_fake_redis(stored_seq=3, stored_quality=0)

    with patch(
        "app.services.ai.memory_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "SHOULD-NOT-WRITE",
            source_seq=3,
            quality=0,
            allow_newer_seq=True,
        )

    assert written is False
    assert not redis.pipe.set_cmds


@pytest.mark.asyncio
async def test_memory_service_set_digest_if_current_override_never_downgrades_llm():
    """override=True 也绝不能把 quality=1 的 LLM 摘要降级覆盖成 quality=0。"""
    service = MemoryService()
    redis = _make_digest_fake_redis(stored_seq=3, stored_quality=1)

    with patch(
        "app.services.ai.memory_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        written = await service.set_digest_if_current(
            "u1",
            "c1",
            "MUST-NOT-OVERRIDE-LLM",
            source_seq=3,
            quality=0,
            allow_newer_seq=True,
            override_same_seq_same_quality=True,
        )

    assert written is False
    assert not redis.pipe.set_cmds

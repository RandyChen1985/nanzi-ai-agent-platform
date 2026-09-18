"""Regression tests for the monotonic `seq` summary cursor and the runtime
model-info fallback.

Covers the two P1 fixes:

1. Session-summary cursor no longer stalls once the Redis retention window
   (`max_history_len`, default 100) is full. `MemoryService.add_message`
   assigns a monotonic `seq`; `merge_session_summary` advances on `synced_seq`
   instead of a positional `len(history)` cursor, so new messages keep entering
   the summary even when the list length stays capped.

2. `_resolve_runtime_model_info_safe` swallows `ModelRegistryError` (and any
   other exception) from `resolve_runtime_model_info` and yields a usable
   `RuntimeModelInfo` instead of blocking the chat turn; downstream, the
   post-route history budget falls back to `agent_context_max_tokens`.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.agent_service import AgentService
from app.services.ai.config import RuntimeModelInfo
from app.services.ai.session_summary_service import SessionSummaryService


@pytest.mark.no_infrastructure
class TestSeqCursor:
    def test_new_message_after_window_full_still_reaches_summary(self):
        """Full history (capped length) + a fresh monotonic seq > synced_seq.

        simulate: window already at cap (seq 1..100), prior merge consumed up to
        seq 100 (synced_seq=100 and synced_len=100). A new message is appended
        with seq 101 while the list length stays at 100 (oldest dropped). The
        seq-based cursor must still produce an incremental window and update the
        summary instead of stalling on the recency/`changed=False` path.
        """
        # Window at cap: 100 messages, seq 1..100 (plus one more arriving next).
        history_full = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"pre-trim tail message {i}",
             "seq": i + 1}
            for i in range(100)
        ]
        # A new message (seq 101) arrives, ltrim drops the single oldest (seq 1)
        # so the list length stays capped at 100.
        history_now = history_full[1:] + [
            {"role": "user", "content": "brand new message after trim", "seq": 101}
        ]
        assert len(history_now) == 100
        assert max(m["seq"] for m in history_now) == 101

        prev = {"title": "t", "summary": "old", "key_facts": [], "decisions": [],
                "open_items": [], "entities": []}

        # Seed debounce state with synced_seq=100, synced_len=100 via a prior run.
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {"last_run": 0, "pending_turns": 0,
                 "synced_len": 100, "synced_seq": 100}
            )
        )
        redis.set = AsyncMock()

        with patch(
            "app.services.ai.session_summary_service.get_redis",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "app.services.ai.session_summary_service.memory_service.get_history",
            new_callable=AsyncMock,
            return_value=history_now,
        ), patch.object(
            SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "should_run", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "_prev_summary", new_callable=AsyncMock,
            return_value=prev,
        ), patch(
            "app.services.ai.session_summary_service.ConversationSummarizer.summarize",
            new_callable=AsyncMock,
            return_value={
                "title": "t2", "summary": "updated", "key_facts": [],
                "decisions": [], "open_items": [], "entities": [],
                "memory_type": "general",
            },
        ) as mock_summarize, patch(
            "app.services.ai.session_summary_service.EmbeddingClient.embed_text",
            new_callable=AsyncMock,
            return_value=[0.1],
        ), patch(
            "app.services.ai.session_summary_service.MemoryIndexService.upsert_summary",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "app.services.ai.session_summary_service.MemoryIndexService.list_summaries",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.ai.session_summary_service.MemoryConfigService.get_int",
            new_callable=AsyncMock,
            return_value=50,
        ), patch(
            "app.services.ai.session_summary_service.DailySummaryService.refresh_for_date",
            new_callable=AsyncMock,
        ):
            import asyncio
            asyncio.run(
                SessionSummaryService.merge_session_summary(
                    "7", "conv-1", "assistant reply", force=True
                )
            )

        # The incremental path must run (real LLM summarize call), not the
        # recency-refresh (changed=False) shortcut.
        mock_summarize.assert_awaited_once()
        assert mock_upsert.await_args.kwargs["summary"] == "updated"

    def test_edit_resend_recomputes_on_new_branch(self):
        """After truncate (keep prefix) + resend append, new message carries a
        larger seq and is captured as the incremental delta."""
        # Pre-truncate the list kept the prefix seq 1..96, then resend appended
        # a new current-branch user msg -> bigger seq (e.g. 150).
        history = (
            [{"role": "user", "content": f"kept {i}", "seq": i + 1}
             for i in range(96)]
            + [{"role": "assistant", "content": "old branch tail", "seq": 97}]
            + [{"role": "user", "content": "RESENT up-to-date question", "seq": 150}]
        )
        prev = {"title": "t", "summary": "branch-before", "key_facts": [],
                "decisions": [], "open_items": [], "entities": []}

        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {"last_run": 0, "pending_turns": 0,
                 "synced_len": 97, "synced_seq": 97}
            )
        )
        redis.set = AsyncMock()

        with patch(
            "app.services.ai.session_summary_service.get_redis",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "app.services.ai.session_summary_service.memory_service.get_history",
            new_callable=AsyncMock,
            return_value=history,
        ), patch.object(
            SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "should_run", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "_prev_summary", new_callable=AsyncMock,
            return_value=prev,
        ), patch(
            "app.services.ai.session_summary_service.ConversationSummarizer.summarize",
            new_callable=AsyncMock,
            return_value={
                "title": "t2", "summary": "recomputed-on-current-branch",
                "key_facts": [], "decisions": [], "open_items": [], "entities": [],
                "memory_type": "general",
            },
        ) as mock_summarize, patch(
            "app.services.ai.session_summary_service.EmbeddingClient.embed_text",
            new_callable=AsyncMock,
            return_value=[0.1],
        ), patch(
            "app.services.ai.session_summary_service.MemoryIndexService.upsert_summary",
            new_callable=AsyncMock,
        ), patch(
            "app.services.ai.session_summary_service.MemoryIndexService.list_summaries",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.ai.session_summary_service.MemoryConfigService.get_int",
            new_callable=AsyncMock,
            return_value=50,
        ), patch(
            "app.services.ai.session_summary_service.DailySummaryService.refresh_for_date",
            new_callable=AsyncMock,
        ):
            import asyncio
            asyncio.run(
                SessionSummaryService.merge_session_summary(
                    "7", "conv-1", "assistant reply", force=True
                )
            )

        # must recompute on the new branch (incremental above seq 97 = seq 150).
        mock_summarize.assert_awaited_once()
        summarized_messages = mock_summarize.await_args.args[0]
        assert all(
            "old branch tail" not in str(message.get("content"))
            for message in summarized_messages
        )
        # Verify write-back advanced synced_seq to 150.
        written = None
        for call in redis.set.call_args_list:
            if call.args[0] == "memory:debounce:7:conv-1":
                written = json.loads(call.args[1])
        assert written is not None
        assert written["synced_seq"] == 150

    def test_legacy_no_seq_messages_fall_back_to_positional(self):
        """Pre-upgrade session (no seq) still works via positional fallback when
        synced_seq has no data yet (synced_seq==0 and synced_len>0)."""
        history = [
            {"role": "user", "content": "a1"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "a3"},
        ]
        prev = {"title": "t", "summary": "old", "key_facts": [], "decisions": [],
                "open_items": [], "entities": []}

        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {"last_run": 0, "pending_turns": 0,
                 "synced_len": 2, "synced_seq": 0}
            )
        )
        redis.set = AsyncMock()

        with patch(
            "app.services.ai.session_summary_service.get_redis",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "app.services.ai.session_summary_service.memory_service.get_history",
            new_callable=AsyncMock,
            return_value=history,
        ), patch.object(
            SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "should_run", new_callable=AsyncMock, return_value=True
        ), patch.object(
            SessionSummaryService, "_prev_summary", new_callable=AsyncMock,
            return_value=prev,
        ), patch(
            "app.services.ai.session_summary_service.ConversationSummarizer.summarize",
            new_callable=AsyncMock,
            return_value={
                "title": "t2", "summary": "pos-delta", "key_facts": [],
                "decisions": [], "open_items": [], "entities": [],
                "memory_type": "general",
            },
        ) as mock_summarize, patch(
            "app.services.ai.session_summary_service.EmbeddingClient.embed_text",
            new_callable=AsyncMock,
            return_value=[0.1],
        ), patch(
            "app.services.ai.session_summary_service.MemoryIndexService.upsert_summary",
            new_callable=AsyncMock,
        ), patch(
            "app.services.ai.session_summary_service.MemoryIndexService.list_summaries",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.ai.session_summary_service.MemoryConfigService.get_int",
            new_callable=AsyncMock,
            return_value=50,
        ), patch(
            "app.services.ai.session_summary_service.DailySummaryService.refresh_for_date",
            new_callable=AsyncMock,
        ):
            import asyncio
            asyncio.run(
                SessionSummaryService.merge_session_summary(
                    "7", "conv-1", "assistant reply", force=True
                )
            )

        # positional fallback runs (incremental = history[2:] i.e. the editor),
        # proving a legacy session still merges.
        mock_summarize.assert_awaited_once()


@pytest.mark.no_infrastructure
class TestPostRouteModelWindowAdoption:
    """路由后是否采纳「模型自身 context_size」作为历史水位线。

    这是上下文预算契约里最容易被误解的一环，因此单独锁定两个方向：显式指定的模型
    才采纳其窗口，系统默认回落一律不采纳。

    背景：生产上真正生效的是 ``_resolve_pre_route_context_budget``（阶段①，路由前，
    只用 ``agent_context_max_tokens``）+ ``_history_budget_for_runtime_model_info``
    （阶段②，路由后，按最终模型抬高水位线）。曾有一个 ``_resolve_runtime_context_budget``
    把两者合在一起并写了完整的「优先级 1/2/3」说明，但**生产代码从未调用过它**
    （只被本文件的旧测试直接调用），极易让排查者误以为它才是主路径。该冗余方法已删除，
    现将测试改锚到真正生效的方法上。
    """

    async def _history_budget(self, source: str, context_size: int, *, cfg: str = "65536"):
        service = AgentService()
        runtime_info = RuntimeModelInfo(
            configured_model="some-model",
            effective_model_id="some-model",
            source=source,
            context_size=context_size,
        )

        async def config_get(key, default=None):
            return {
                "agent_context_max_tokens": cfg,
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ), patch(
            # overhead 已是模块常量，这里置 0 让期望值就是 window - output。
            "app.services.ai.agent_service.CONTEXT_OVERHEAD_RESERVATION_TOKENS",
            0,
        ):
            return await service._history_budget_for_runtime_model_info(runtime_info)

    def test_explicit_agent_config_model_window_is_adopted(self):
        """显式配置的模型（agent_config）：采纳其更大的 context_size 抬高水位线。

        水位线不抬高会导致「模型明明能装 200k，平台却在 64k 就压缩」——即提前 compact。
        """
        import asyncio

        result = asyncio.run(
            self._history_budget("agent_config", 200000, cfg="65536")
        )

        assert result == 200000, "agent_config 指定的模型窗口应被采纳"

    def test_system_default_model_window_is_not_adopted(self):
        """system_default 回落：**即使注册表给出大窗口也不采纳**，仍用配置水位线。

        系统默认模型是平台兜底选择而非智能体有意配置，按它的窗口放大水位线会让本该
        压缩的会话继续增长，最终在另一侧超窗。
        """
        import asyncio

        result = asyncio.run(
            self._history_budget("system_default", 200000, cfg="65536")
        )

        assert result == 65536, "system_default 不得采纳模型窗口，应回落 agent_context_max_tokens"

    def test_zero_context_size_falls_back_to_config(self):
        """context_size 缺失/为 0 时回落配置值，不得把水位线压成 0。"""
        import asyncio

        result = asyncio.run(self._history_budget("agent_config", 0, cfg="43210"))

        assert result == 43210

    def test_debug_override_model_window_is_adopted(self):
        """用户在输入框手动切换的模型（debug_override）同样应采纳其窗口。"""
        import asyncio

        result = asyncio.run(
            self._history_budget("debug_override", 128000, cfg="65536")
        )

        assert result == 128000


@pytest.mark.no_infrastructure
class TestFinalRuntimeModelFallback:
    async def test_final_runtime_model_registry_error_returns_safe_info(self):
        service = AgentService()
        config = SimpleNamespace(model_name="agent-model")

        class _RegistryError(Exception):
            pass

        with patch(
            "app.services.ai.agent_service.resolve_runtime_model_info",
            new_callable=AsyncMock,
            side_effect=_RegistryError("registry unavailable"),
        ):
            result = await service._resolve_runtime_model_info_safe(
                config=config,
                debug_options=None,
            )

        assert isinstance(result, RuntimeModelInfo)
        assert result.configured_model == "agent-model"
        assert result.effective_model_id == "agent-model"
        assert result.resolution_status == "registry_unresolved"


@pytest.mark.no_infrastructure
class TestPostRouteContextBudget:
    async def test_history_budget_reserves_configured_completion_tokens(self):
        service = AgentService()

        async def config_get(key, default=None):
            return {
                "agent_context_overhead_headroom_tokens": "8192",
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ):
            result = await service._resolve_history_context_budget(
                65536,
                max_output_tokens=32768,
            )

        assert result == 24576

    async def test_history_budget_keeps_legacy_fallback_when_output_is_unset(self):
        service = AgentService()

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(return_value="8192"),
        ):
            result = await service._resolve_history_context_budget(65536)

        assert result == 57344

    async def test_runtime_context_metadata_exposes_completion_reserve(self):
        service = AgentService()
        runtime_info = RuntimeModelInfo(
            configured_model="deepseek-v3.2",
            effective_model_id="deepseek-v3.2",
            source="agent_config",
            context_size=65536,
            max_output_tokens=32768,
        )

        async def config_get(key, default=None):
            return {
                "agent_context_max_tokens": "65536",
                "agent_context_overhead_headroom_tokens": "8192",
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ):
            metadata = await service._runtime_context_metadata(runtime_info)

        assert metadata["physical_window"] == 65536
        assert metadata["history_budget"] == 24576
        assert metadata["completion_reserve_tokens"] == 32768
        assert metadata["request_input_budget"] == 32768
        assert metadata["prompt_overhead_reservation_tokens"] == 8192

    async def test_runtime_context_metadata_uses_common_primary_and_synthesis_budget(self):
        service = AgentService()
        primary = RuntimeModelInfo(
            configured_model="primary",
            effective_model_id="primary",
            source="agent_config",
            context_size=65536,
            max_output_tokens=32768,
        )
        synthesis = RuntimeModelInfo(
            configured_model="synthesis",
            effective_model_id="synthesis",
            source="runtime_override",
            context_size=16384,
            max_output_tokens=4096,
        )

        async def config_get(key, default=None):
            return {
                "agent_context_max_tokens": "65536",
                "agent_context_overhead_headroom_tokens": "8192",
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ):
            metadata = await service._runtime_context_metadata(
                primary,
                synthesis_runtime_model_info=synthesis,
            )

        assert metadata["physical_window"] == 16384
        assert metadata["history_budget"] == 4096
        assert metadata["completion_reserve_tokens"] == 32768
        assert metadata["request_input_budget"] == 12288

    async def test_pre_route_budget_uses_config_fallback_only(self):
        service = AgentService()

        with patch(
            "app.services.config_service.ConfigService.get",
            new_callable=AsyncMock,
            return_value="49152",
        ), patch(
            "app.services.ai.agent_manager.AgentManagerService.get_active_agent_config",
            new_callable=AsyncMock,
            side_effect=AssertionError("pre-route budget must not resolve an agent"),
        ):
            result = await service._resolve_pre_route_context_budget()

        assert result == 49152

    async def test_post_route_context_rebuild_uses_target_model_window(self):
        service = AgentService()
        history = [
            {"role": "user", "content": "旧消息", "seq": 1},
            {"role": "assistant", "content": "旧回答", "seq": 2},
        ]
        user_message = {"role": "user", "content": "当前问题"}
        shared_state = {
            "context_source_history": history,
            "context_user_message": user_message,
            "context_history_budget": 1,
        }
        runtime_info = RuntimeModelInfo(
            configured_model="target-model",
            effective_model_id="target-model",
            source="agent_config",
            context_size=32768,
        )
        compacted = [{"role": "system", "content": "target digest"}] + history
        compact_mock = AsyncMock(return_value=compacted)

        async def config_get(key, default=None):
            return {
                "agent_context_max_tokens": "65536",
                "agent_context_overhead_headroom_tokens": "8192",
                "agent_max_context_messages": "60",
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ), patch.object(
            service, "_maybe_compact_overflow", compact_mock
        ):
            result = await service._rebuild_context_for_resolved_model(
                messages=[user_message],
                runtime_model_info=runtime_info,
                conversation_id="c1",
                user_info={"user_id": "u1"},
                agent_id="target-agent",
                agent_name="target-agent",
                version_id=None,
                shared_state=shared_state,
            )

        assert result == compacted + [user_message]
        assert compact_mock.await_args.kwargs["token_budget"] == 24576
        assert compact_mock.await_args.kwargs["agent_id"] == "target-agent"
        assert compact_mock.await_args.kwargs["conversation_id"] == "c1"

    async def test_post_route_budget_is_safe_for_primary_and_synthesis_models(self):
        service = AgentService()
        history = [
            {"role": "user", "content": "旧消息", "seq": 1},
            {"role": "assistant", "content": "旧回答", "seq": 2},
        ]
        user_message = {"role": "user", "content": "当前问题"}
        shared_state = {
            "context_source_history": history,
            "context_user_message": user_message,
            "context_history_budget": 1,
        }
        primary = RuntimeModelInfo(
            configured_model="primary",
            effective_model_id="primary",
            source="agent_config",
            context_size=65536,
        )
        synthesis = RuntimeModelInfo(
            configured_model="synthesis",
            effective_model_id="synthesis",
            source="runtime_override",
            context_size=16384,
        )
        compact_mock = AsyncMock(return_value=history)

        async def config_get(key, default=None):
            return {
                "agent_context_max_tokens": "65536",
                "agent_context_overhead_headroom_tokens": "8192",
                "agent_max_context_messages": "60",
            }.get(key, default)

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=config_get),
        ), patch.object(service, "_maybe_compact_overflow", compact_mock):
            await service._rebuild_context_for_resolved_model(
                messages=[user_message],
                runtime_model_info=primary,
                synthesis_runtime_model_info=synthesis,
                conversation_id="c1",
                user_info={"user_id": "u1"},
                agent_id="a1",
                agent_name="a1",
                version_id=None,
                shared_state=shared_state,
            )

        assert shared_state["context_history_budget"] == 8192
        assert compact_mock.await_args.kwargs["token_budget"] == 8192

    async def test_final_compaction_event_is_saved_for_sse_after_rebuild(self):
        service = AgentService()
        history = [
            {"role": "user", "content": "旧消息", "seq": 1},
            {"role": "assistant", "content": "旧回答", "seq": 2},
        ]
        shared_state = {
            "context_source_history": history,
            "context_user_message": {"role": "user", "content": "当前问题"},
            "context_history_budget": 1,
        }
        runtime_info = RuntimeModelInfo(
            configured_model="target",
            effective_model_id="target",
            source="agent_config",
            context_size=32768,
        )

        async def compact(*args, **kwargs):
            kwargs["out"].update(
                {
                    "title": "对话上下文已压缩（平台摘录）",
                    "preview": "旧消息",
                    "token_budget": kwargs["token_budget"],
                }
            )
            return history

        with patch(
            "app.services.config_service.ConfigService.get",
            new=AsyncMock(side_effect=lambda key, default=None: {
                "agent_context_max_tokens": "65536",
                "agent_context_overhead_headroom_tokens": "8192",
                "agent_max_context_messages": "60",
            }.get(key, default)),
        ), patch.object(service, "_maybe_compact_overflow", side_effect=compact):
            await service._rebuild_context_for_resolved_model(
                messages=[shared_state["context_user_message"]],
                runtime_model_info=runtime_info,
                conversation_id="c1",
                user_info={"user_id": "u1"},
                agent_id="a1",
                agent_name="a1",
                version_id=None,
                shared_state=shared_state,
            )

        assert shared_state["context_final_compaction_event"]["type"] == "context_summarized"


@pytest.mark.no_infrastructure
class TestAssistantPersistenceOrdering:
    async def test_assistant_is_persisted_before_summary_merge(self):
        from app.services.ai.agent_service import _persist_assistant_message_and_summary

        events = []

        async def persist(*args, **kwargs):
            events.append("message")

        async def merge(*args, **kwargs):
            events.append("summary")

        with patch(
            "app.services.ai.agent_service.memory_service.add_message",
            new=AsyncMock(side_effect=persist),
        ), patch(
            "app.services.ai.session_summary_service.SessionSummaryService.merge_session_summary",
            new=AsyncMock(side_effect=merge),
        ):
            await _persist_assistant_message_and_summary(
                user_id="u1",
                conversation_id="c1",
                content="assistant reply",
                merge_summary=True,
            )

        assert events == ["message", "summary"]

    async def test_cancelled_assistant_persistence_includes_cancelled_status(self):
        from app.services.ai.agent_service import _persist_assistant_message_and_summary

        add_message = AsyncMock()
        with patch(
            "app.services.ai.agent_service.memory_service.add_message",
            new=add_message,
        ):
            await _persist_assistant_message_and_summary(
                user_id="u1",
                conversation_id="c1",
                content="半截回答",
                status="cancelled",
            )

        assert add_message.await_args.kwargs["status"] == "cancelled"

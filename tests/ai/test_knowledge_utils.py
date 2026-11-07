import json
import pytest

from app.core.context import AgentContext, set_agent_context
from app.services.ai.knowledge_utils import (
    NO_KNOWLEDGE_DATASET_MESSAGE,
    collect_citation_ids_from_payload,
    collect_current_turn_knowledge_dataset_ids_from_messages,
    collect_knowledge_dataset_ids_from_messages,
    extract_dataset_ids_from_message,
    filter_invalid_citation_markers,
    format_dataset_ids_for_tool,
    format_knowledge_tool_log_display,
    build_rag_retrieval_debug_meta,
    has_knowledge_context_in_messages,
    knowledge_prefetch_had_citations,
    merge_request_knowledge_dataset_ids,
    resolve_bound_dataset_ids,
    resolve_knowledge_dataset_ids,
    resolve_rag_retrieval_params,
)


def test_extract_dataset_ids_from_message_embed_hint():
    rid = "4525d66cec7111f0a3d00242ac120006"
    text = (
        f"用户本轮已选择知识库，dataset_id：{rid}。"
        f"dataset_ids 请传 ['{rid}']"
    )
    assert extract_dataset_ids_from_message(text) == [rid]


def test_format_dataset_ids_for_tool():
    rid_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rid_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert format_dataset_ids_for_tool([rid_a]) == rid_a
    assert rid_a in format_dataset_ids_for_tool([rid_a, rid_b])


def test_resolve_bound_dataset_ids_uses_merged_effective_context():
    agent_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    user_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    set_agent_context(
        AgentContext(
            agent_id="kb",
            agent_name="knowledge-base",
            dataset_ids=[agent_id, user_id],
            agent_dataset_ids=[agent_id],
        )
    )
    try:
        assert resolve_bound_dataset_ids() == [agent_id, user_id]
    finally:
        set_agent_context(
            AgentContext(
                agent_id="test-reset",
                agent_name="test-reset",
            )
        )


def test_format_knowledge_tool_log_display_before_truncation():
    payload = {
        "content": "summary for llm",
        "citations": [
            {
                "id": "1",
                "doc_name": "EC6 用户手册.pdf",
                "similarity": 0.82,
                "content": "换电流程说明",
            }
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    formatted = format_knowledge_tool_log_display(raw, max_len=500)
    assert "【引用片段】" in formatted
    assert "[ID:1] EC6 用户手册.pdf" in formatted
    assert "换电流程说明" in formatted
    assert not formatted.startswith("{")


def test_knowledge_prefetch_had_citations_and_filter_markers():
    payload = {
        "content": "doc",
        "citations": [{"id": "1", "doc_name": "a.pdf", "content": "x"}],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    assert knowledge_prefetch_had_citations(raw) is True
    assert collect_citation_ids_from_payload(raw) == {"1"}

    text = "依据制度说明 [ID:1]，另一句 [ID:9] 编造。"
    filtered = filter_invalid_citation_markers(text, {"1"})
    assert "[ID:1]" in filtered
    assert "[ID:9]" not in filtered


def test_has_knowledge_citation_markers_supports_id_and_legacy_formats():
    from app.services.ai.knowledge_utils import (
        has_knowledge_citation_markers,
        text_has_valid_citation_markers,
    )

    assert has_knowledge_citation_markers("制度要求保存配置。[ID:1]") is True
    assert has_knowledge_citation_markers("制度要求保存配置。[1]") is True
    assert has_knowledge_citation_markers("制度要求保存配置。") is False
    assert text_has_valid_citation_markers("说明见文档 [ID:1] 与 [ID:9]", {"1"}) is True
    assert text_has_valid_citation_markers("说明见文档 [ID:9]", {"1"}) is False


def test_resolve_rag_retrieval_params_from_engine_config():
    set_agent_context(
        AgentContext(
            agent_id="kb",
            agent_name="knowledge-base",
            engine_config={
                "ragflow_similarity_threshold": 0.65,
                "ragflow_vector_weight": 0.7,
                "top_k": 8,
            },
        )
    )
    threshold, weight, top_k = resolve_rag_retrieval_params(
        system_threshold=0.2,
        system_weight=0.3,
        system_top_k=5,
    )
    assert threshold == 0.65
    assert weight == 0.7
    assert top_k == 8


def test_collect_knowledge_dataset_ids_from_messages():
    rid = "4525d66cec7111f0a3d00242ac120006"
    messages = [
        {"role": "user", "content": "hello", "files": [{"type": "knowledge_base", "url": rid}]},
    ]
    assert collect_knowledge_dataset_ids_from_messages(messages) == [rid]
    assert merge_request_knowledge_dataset_ids([rid], messages) == [rid]


def test_collect_knowledge_dataset_ids_inherits_from_earlier_turn():
    rid = "4525d66cec7111f0a3d00242ac120006"
    messages = [
        {
            "role": "user",
            "content": "换电流程",
            "files": [{"type": "knowledge_base", "url": rid}],
        },
        {"role": "assistant", "content": "换电预计 3-5 分钟"},
        {"role": "user", "content": "换电过程中可以开门吗？"},
    ]
    assert collect_knowledge_dataset_ids_from_messages(messages) == [rid]
    assert merge_request_knowledge_dataset_ids(None, messages) == [rid]


def test_explicit_request_knowledge_dataset_ids_do_not_merge_history():
    current_rid = "11111111111111111111111111111111"
    history_rid = "22222222222222222222222222222222"
    messages = [
        {"role": "user", "content": "旧问题", "files": [{"type": "knowledge_base", "url": history_rid}]},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "本次问题"},
    ]

    assert merge_request_knowledge_dataset_ids([current_rid], messages) == [current_rid]


def test_current_turn_knowledge_ids_do_not_treat_earlier_attachment_as_explicit():
    rid = "4525d66cec7111f0a3d00242ac120006"
    messages = [
        {"role": "user", "content": "换电流程", "files": [{"type": "knowledge_base", "url": rid}]},
        {"role": "assistant", "content": "换电流程说明 [ID:1]"},
        {"role": "user", "content": "今天几号"},
    ]

    assert collect_knowledge_dataset_ids_from_messages(messages) == [rid]
    assert collect_current_turn_knowledge_dataset_ids_from_messages(messages) == []
    assert has_knowledge_context_in_messages(messages) is True


@pytest.mark.asyncio
async def test_resolve_knowledge_dataset_ids_blocks_without_explicit_dataset():
    from unittest.mock import AsyncMock, patch

    set_agent_context(
        AgentContext(
            agent_id="assistant",
            agent_name="assistant",
            require_explicit_dataset=True,
        )
    )
    try:
        with patch(
            "app.services.config_service.ConfigService.get",
            new_callable=AsyncMock,
            return_value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ) as mock_get:
            ids, err = await resolve_knowledge_dataset_ids(query="换电流程")
        assert ids == []
        assert err == NO_KNOWLEDGE_DATASET_MESSAGE
        mock_get.assert_not_called()
    finally:
        set_agent_context(
            AgentContext(
                agent_id="test-reset",
                agent_name="test-reset",
                require_explicit_dataset=False,
            )
        )


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_load_alive_dataset_ids_covers_datasets_beyond_first_500(monkeypatch):
    """存活集合必须翻完所有页。

    旧实现取 list_datasets(page=1, page_size=500)，RAGFlow 单页上限 100，实际只拿
    前 500 个（含自动生成的 meta-* 库）；库超过 500 个后，排在后面的正常库会被判成
    「在 RAGFlow 侧已失联」，问答被直接阻断。
    """
    from app.services.ai import knowledge_utils

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def list_all_datasets(self):
            return [{"id": f"{i:032x}"} for i in range(600)]

    async def _no_redis():
        return None

    monkeypatch.setattr(
        "app.services.ai.ragflow_client.RagFlowClient",
        _FakeClient,
    )
    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)

    alive = await knowledge_utils._load_ragflow_alive_dataset_ids()

    assert alive is not None
    assert len(alive) == 600
    assert f"{599:032x}" in alive


@pytest.mark.asyncio
async def test_filter_alive_knowledge_dataset_ids_drops_missing():
    from unittest.mock import AsyncMock, patch

    from app.services.ai.knowledge_utils import filter_alive_knowledge_dataset_ids

    alive = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    missing = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    with patch(
        "app.services.ai.knowledge_utils._load_ragflow_alive_dataset_ids",
        new_callable=AsyncMock,
        return_value={alive},
    ):
        filtered = await filter_alive_knowledge_dataset_ids([alive, missing, alive])
    assert filtered == [alive]


@pytest.mark.asyncio
async def test_filter_alive_knowledge_dataset_ids_keeps_all_when_live_set_unknown():
    """RAGFlow 列表不可用时不误伤：保留原 ID，避免检索整条链路被堵死。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.knowledge_utils import filter_alive_knowledge_dataset_ids

    ids = [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    with patch(
        "app.services.ai.knowledge_utils._load_ragflow_alive_dataset_ids",
        new_callable=AsyncMock,
        return_value=None,
    ):
        filtered = await filter_alive_knowledge_dataset_ids(ids)
    assert filtered == ids


@pytest.mark.asyncio
async def test_build_rag_retrieval_debug_meta_reads_config_in_parallel():
    import asyncio
    from unittest.mock import AsyncMock, patch

    set_agent_context(
        AgentContext(
            agent_id="kb",
            agent_name="knowledge-base",
            dataset_ids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        )
    )
    active = 0
    max_active = 0
    values = {
        "knowledge_ragflow_similarity_threshold": "0.65",
        "knowledge_ragflow_vector_weight": "0.7",
        "knowledge_ragflow_metadata_top_k": "8",
    }

    async def get_config(key, default=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0)
            return values.get(key, default)
        finally:
            active -= 1

    try:
        with patch(
            "app.services.config_service.ConfigService.get",
            new_callable=AsyncMock,
            side_effect=get_config,
        ) as mock_get:
            meta = await build_rag_retrieval_debug_meta()
    finally:
        set_agent_context(
            AgentContext(
                agent_id="test-reset",
                agent_name="test-reset",
            )
        )

    assert max_active == 3
    assert mock_get.await_count == 3
    assert meta["similarity_threshold"] == 0.65
    assert meta["vector_similarity_weight"] == 0.7
    assert meta["top_k"] == 8


@pytest.mark.asyncio
async def test_resolve_knowledge_dataset_ids_excludes_missing_from_ragflow():
    from unittest.mock import AsyncMock, patch

    alive = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    missing = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    set_agent_context(
        AgentContext(
            agent_id="kb",
            agent_name="knowledge-base",
            dataset_ids=[alive, missing],
            require_explicit_dataset=False,
        )
    )
    try:
        with patch(
            "app.services.ai.knowledge_utils.filter_alive_knowledge_dataset_ids",
            new_callable=AsyncMock,
            side_effect=lambda ids: [i for i in ids if i == alive],
        ):
            ids, err = await resolve_knowledge_dataset_ids(query="换电")
        assert ids == [alive]
        assert err is None
    finally:
        set_agent_context(
            AgentContext(
                agent_id="test-reset",
                agent_name="test-reset",
                require_explicit_dataset=False,
            )
        )

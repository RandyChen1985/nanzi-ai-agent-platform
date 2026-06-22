import json
import pytest

from app.core.context import AgentContext, set_agent_context
from app.services.ai.knowledge_utils import (
    NO_KNOWLEDGE_DATASET_MESSAGE,
    collect_citation_ids_from_payload,
    collect_knowledge_dataset_ids_from_messages,
    extract_dataset_ids_from_message,
    filter_invalid_citation_markers,
    format_dataset_ids_for_tool,
    format_knowledge_tool_log_display,
    knowledge_prefetch_had_citations,
    merge_request_knowledge_dataset_ids,
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

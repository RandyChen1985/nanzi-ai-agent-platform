import pytest
import json
from unittest.mock import AsyncMock, patch
from app.services.ai.tools.knowledge_tool import search_knowledge_base, normalize_dataset_ids
from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool


def _config_side_effect(key, default=None):
    values = {
        "knowledge_base_enabled": "true",
        "knowledge_ragflow_similarity_threshold": None,
        "knowledge_ragflow_vector_weight": None,
        "knowledge_ragflow_metadata_top_k": None,
    }
    return values.get(key, default)


def _patch_search(*, retrieve_return=None, retrieve_side_effect=None, config_side_effect=None):
    """检索测试公共 mock：启用知识库 + 失联过滤透传。"""
    mock_retrieve = AsyncMock(return_value=retrieve_return if retrieve_return is not None else [])
    if retrieve_side_effect is not None:
        mock_retrieve.side_effect = retrieve_side_effect
    return (
        patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new=mock_retrieve),
        patch(
            "app.services.config_service.ConfigService.get",
            new_callable=AsyncMock,
            side_effect=config_side_effect or _config_side_effect,
        ),
        patch(
            "app.services.ai.knowledge_utils.filter_alive_knowledge_dataset_ids",
            new_callable=AsyncMock,
            side_effect=lambda ids: list(ids),
        ),
        mock_retrieve,
    )


def test_normalize_dataset_ids_plain_and_comma():
    rid_a = "4525d66cec7111f0a3d00242ac120006"
    rid_b = "abcd1234abcd1234abcd1234abcd1234"
    assert normalize_dataset_ids(rid_a) == [rid_a]
    assert normalize_dataset_ids(f"{rid_a},{rid_b}") == [rid_a, rid_b]
    assert normalize_dataset_ids("id1,id2") == []


def test_normalize_dataset_ids_json_and_python_list_strings():
    rid = "4525d66cec7111f0a3d00242ac120006"
    assert normalize_dataset_ids(f'["{rid}"]') == [rid]
    assert normalize_dataset_ids(f"['{rid}']") == [rid]
    assert normalize_dataset_ids([rid]) == [rid]


def test_search_knowledge_base_runtime_schema_accepts_dataset_id_arrays():
    spec = runtime_tool_spec_from_legacy_tool(search_knowledge_base, source_type="system")
    dataset_schema = spec.parameters_schema["properties"]["dataset_ids"]

    assert {item.get("type") for item in dataset_schema["anyOf"]} == {"string", "null", "array"}
    array_schema = next(item for item in dataset_schema["anyOf"] if item.get("type") == "array")
    assert array_schema["items"] == {"type": "string"}


@pytest.mark.asyncio
async def test_search_knowledge_base_accepts_dataset_id_arrays_from_runtime():
    rid = "4525d66cec7111f0a3d00242ac120006"
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, p_alive:
        await search_knowledge_base.ainvoke({"query": "换电", "dataset_ids": [rid]})

    args, _ = mock_retrieve.call_args
    assert args[1] == [rid]


@pytest.mark.asyncio
async def test_search_knowledge_base_strips_json_array_argument():
    """LLM 传入 JSON 数组字符串时应解析为合法 ID"""
    rid = "4525d66cec7111f0a3d00242ac120006"
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, p_alive:
        await search_knowledge_base.ainvoke({"query": "换电", "dataset_ids": f'["{rid}"]'})

        args, _ = mock_retrieve.call_args
        assert args[1] == [rid]


@pytest.mark.asyncio
async def test_search_knowledge_base_explicit_ids():
    """测试传递显式数据集 ID"""
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(
        retrieve_return=[{"doc_name": "test.pdf", "content": "text"}]
    )
    with p_retrieve, p_config, p_alive:
        rid_a = "11111111111111111111111111111111"
        rid_b = "22222222222222222222222222222222"
        result = await search_knowledge_base.ainvoke(
            {"query": "hello", "dataset_ids": f"{rid_a},{rid_b}"}
        )

        args, kwargs = mock_retrieve.call_args
        assert args[0] == "hello"
        assert args[1] == [rid_a, rid_b]
        assert "test.pdf" in result


@pytest.mark.asyncio
async def test_search_knowledge_base_context_ids():
    """测试从 Agent 上下文获取数据集 ID"""
    from app.core.context import AgentContext, set_agent_context

    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, p_alive:
        set_agent_context(
            AgentContext(
                agent_id="kb",
                agent_name="knowledge-base",
                dataset_ids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            )
        )

        await search_knowledge_base.ainvoke({"query": "hi"})

        args, kwargs = mock_retrieve.call_args
        assert args[1] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]


@pytest.mark.asyncio
async def test_search_knowledge_base_keeps_agent_granted_dataset_without_user_permission():
    """智能体绑定的数据集对智能体用户公开，不应被普通用户权限再次删掉。"""
    from app.core.context import AgentContext, set_agent_context

    agent_dataset = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, p_alive, patch(
        "app.services.permission_service.PermissionService.get_knowledge_base_access",
        new_callable=AsyncMock,
        return_value={"is_admin": False, "accessible_ids": set()},
    ):
        set_agent_context(
            AgentContext(
                agent_id="kb",
                agent_name="knowledge-base",
                dataset_ids=[agent_dataset],
                agent_dataset_ids=[agent_dataset],
                user_id=100,
                is_admin=False,
            )
        )

        await search_knowledge_base.ainvoke({"query": "hi"})

    args, _ = mock_retrieve.call_args
    assert args[1] == [agent_dataset]


@pytest.mark.asyncio
async def test_search_knowledge_base_parameter_priority():
    """测试参数（阈值、权重、top_k）的优先级逻辑"""
    from app.core.context import AgentContext, set_agent_context

    def config_side_effect(k, default=None):
        return {
            "knowledge_base_enabled": "true",
            "ragflow_similarity_threshold": "0.5",
            "ragflow_vector_weight": "0.4",
            "ragflow_metadata_top_k": "5",
            "knowledge_ragflow_similarity_threshold": "0.5",
            "knowledge_ragflow_vector_weight": "0.4",
            "knowledge_ragflow_metadata_top_k": "5",
        }.get(k, default)

    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(
        retrieve_return=[],
        config_side_effect=config_side_effect,
    )
    with p_retrieve, p_config, p_alive:
        default_ds = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        set_agent_context(
            AgentContext(
                agent_id="kb",
                agent_name="knowledge-base",
                dataset_ids=[default_ds],
                require_explicit_dataset=False,
                engine_config={
                    "ragflow_similarity_threshold": 0.8,
                    "ragflow_vector_weight": 0.6,
                    "top_k": 9,
                },
            )
        )

        await search_knowledge_base.ainvoke({"query": "param test"})

        args, kwargs = mock_retrieve.call_args
        assert kwargs["similarity_threshold"] == 0.8
        assert kwargs["vector_similarity_weight"] == 0.6
        assert kwargs["top_k"] == 9


@pytest.mark.asyncio
async def test_search_knowledge_base_empty_result_is_structured_json():
    rid = "4525d66cec7111f0a3d00242ac120006"
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, p_alive:
        result = await search_knowledge_base.ainvoke(
            {"query": "不存在的内容", "dataset_ids": rid}
        )

    payload = json.loads(result)
    assert payload["status"] == "empty"
    assert payload["citations"] == []
    assert "未找到" in payload["content"]


@pytest.mark.asyncio
async def test_search_knowledge_base_reports_service_unavailable_on_502():
    """RAGFlow 502 时应返回明确的「知识库服务不可用」提示，而非泛化 Tool Error。"""
    rid = "4525d66cec7111f0a3d00242ac120006"
    p_retrieve, p_config, p_alive, mock_retrieve = _patch_search(
        retrieve_side_effect=Exception("RAGFlow Retrieve failed: HTTP 502 Bad Gateway")
    )
    with p_retrieve, p_config, p_alive:
        result = await search_knowledge_base.ainvoke(
            {"query": "换电流程", "dataset_ids": rid}
        )

    assert "知识库服务不可用" in result
    assert "Failed to search knowledge base" not in result
    assert mock_retrieve.call_count == 1


@pytest.mark.asyncio
async def test_search_knowledge_base_drops_missing_dataset_ids_before_retrieve():
    """失联知识库 ID 不应再发给 RAGFlow retrieve。"""
    alive = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    missing = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    p_retrieve, p_config, _, mock_retrieve = _patch_search(retrieve_return=[])
    with p_retrieve, p_config, patch(
        "app.services.ai.knowledge_utils.filter_alive_knowledge_dataset_ids",
        new_callable=AsyncMock,
        side_effect=lambda ids: [i for i in ids if i == alive],
    ):
        await search_knowledge_base.ainvoke(
            {"query": "换电", "dataset_ids": f"{alive},{missing}"}
        )

    args, _ = mock_retrieve.call_args
    assert args[1] == [alive]


@pytest.mark.asyncio
async def test_search_qa_examples_is_read_only_no_runtime_confirm():
    """search_qa_examples 是只读查询工具，运行时不得进入「工具确认」弹框。"""
    from app.services.ai.runtime.agentscope.tools import (
        READ_ONLY_TOOL_NAMES,
        infer_runtime_permission_scope,
    )
    from app.services.ai.tools.registry import ToolRegistry

    assert "search_qa_examples" in READ_ONLY_TOOL_NAMES
    assert infer_runtime_permission_scope("search_qa_examples", "system") == "read"
    assert infer_runtime_permission_scope("search_qa_examples", "static") == "read"

    spec = await ToolRegistry.get_runtime_tool("search_qa_examples")
    assert spec is not None
    assert spec.permission_scope == "read"


@pytest.mark.asyncio
async def test_search_knowledge_base_disabled_when_feature_off():
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        mock_config.side_effect = lambda k, default=None: {
            "knowledge_base_enabled": "false",
        }.get(k, default)

        result = await search_knowledge_base.ainvoke({"query": "test", "dataset_ids": "a" * 32})

    assert "知识库功能未开启" in result
    mock_retrieve.assert_not_called()

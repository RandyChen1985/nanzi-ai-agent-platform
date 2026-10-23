from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import json

from app.schemas.agent import ChatConfig
from app.services.ai.grounding.service import GroundingService
from app.services.ai.hallucination_evaluator import HallucinationEvaluator
from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.executors.prompts import KnowledgeChatPrompts

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def kb_config():
    return ChatConfig(
        agent_id="sys-agent-kb",
        agent_name="knowledge-base",
        agent_version=None,
        model_name="qwen",
        temperature=0.1,
        system_prompt="You are a knowledge assistant.",
        tools=["search_knowledge_base"],
    )


def _kb_runner(kb_config, **kwargs):
    return KnowledgeAgentRunner(
        config=kb_config,
        trace_id="test-kb-guard",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        **kwargs,
    )


def _search_knowledge_tool(output: str = "{}") -> RuntimeToolSpec:
    async def fake_search(**kwargs):
        return output

    return RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search knowledge base",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=fake_search,
        permission_scope="read",
    )


def test_knowledge_state_prompt_describes_prefetch_and_citation_boundary():
    prompt = KnowledgeChatPrompts.knowledge_state_block(
        search_status="PREFETCHED",
        result_status="SUFFICIENT",
        citation_count=2,
        supplement_allowed=False,
    )

    assert "[KNOWLEDGE_STATE]" in prompt
    assert "search_status: PREFETCHED" in prompt
    assert "result_status: SUFFICIENT" in prompt
    assert "citation_count: 2" in prompt
    assert "supplement_search_allowed: false" in prompt


def test_successful_knowledge_retrieval_removes_false_unbound_tool_notice():
    runner = _kb_runner(kb_config)
    response = (
        "⚠️ 当前会话未绑定知识库检索工具（search_knowledge_base），"
        "我暂时无法直接检索相关文档内容。\n\n"
        "根据文档，人事档案报表需要先打开对应模块。[ID:1]"
    )

    sanitized = runner._sanitize_knowledge_tool_binding_notice(
        response,
        retrieval_succeeded=True,
    )

    assert "未绑定知识库检索工具" not in sanitized
    assert "根据文档，人事档案报表需要先打开对应模块。[ID:1]" in sanitized


def test_unsuccessful_knowledge_retrieval_keeps_unbound_tool_notice():
    runner = _kb_runner(kb_config)
    response = "当前会话未绑定知识库检索工具（search_knowledge_base）。"

    assert runner._sanitize_knowledge_tool_binding_notice(
        response,
        retrieval_succeeded=False,
    ) == response


@pytest.mark.asyncio
async def test_hallucination_evaluator_success():
    """验证 HallucinationEvaluator 是否能正确请求大模型并解析幻觉判定 JSON。"""
    mock_llm = MagicMock()
    mock_client = AsyncMock()
    mock_client.generate_text.return_value = '{"is_hallucinated": true, "reason": "回答包含了事实文献未提及的新参数 B"}'

    with patch("app.services.ai.hallucination_evaluator.get_llm_async", AsyncMock(return_value=mock_llm)), \
         patch("app.services.ai.hallucination_evaluator.chat_client_from_handle", return_value=mock_client):
         
         res = await HallucinationEvaluator.evaluate(
             query="什么是系统 A？",
             context="文献提到：系统 A 包含参数 C。",
             response="系统 A 包含参数 C 和参数 B。"
         )
         
         assert res["is_hallucinated"] is True
         assert "新参数 B" in res["reason"]


@pytest.mark.asyncio
async def test_hybrid_search_trigger_on_empty_recall(kb_config):
    """验证当知识库空召回时自适应唤起百度检索，并融合网页引用。"""
    runner = _kb_runner(kb_config)

    # 1. 模拟知识库空召回返回值
    kb_empty_output = json.dumps({
        "status": "empty",
        "content": "未找到匹配片段。",
        "citations": []
    })

    # 2. 模拟百度搜索返回值
    mock_web_results = [
        {
            "title": "百度百科: 混合检索",
            "link": "http://baidu.com/123",
            "abstract": "混合检索结合了关键字与向量召回...",
            "extracted_content": "混合检索结合了关键字与向量召回。正文内容..."
        }
    ]

    kb_spec = _search_knowledge_tool(kb_empty_output)

    with patch("app.services.ai.runners.knowledge_agent_runner.collect_citation_ids_from_payload", return_value=[]), \
         patch("app.services.ai.tools.advanced_auxiliary_tools.web_search_baidu_raw", AsyncMock(return_value=mock_web_results)):

        chunks = []
        async for chunk in runner._auto_invoke_search_knowledge_base(
            query="什么是混合检索？",
            tools=[kb_spec],
            dataset_ids="default"
        ):
            chunks.append(chunk)

        # 3. 检查是否有 log 阶段事件
        assert any(c.get("title") == "触发联网辅助检索" for c in chunks)
        assert any(c.get("title") == "联网辅助检索完成" for c in chunks)

        # 4. 检查最终 output 里是否融入了网页 context 和 citation 节点
        final_info = next(c for c in chunks if "__knowledge_output__" in c)
        final_output_str = final_info["__knowledge_output__"]
        citation_event = next(c for c in chunks if c.get("type") == "citation")

        assert "【互联网参考事实文献】" in final_output_str
        assert len(citation_event["data"]) == 1
        assert citation_event["data"][0]["source_type"] == "web"
        assert "网页: 百度百科: 混合检索" in citation_event["data"][0]["doc_name"]


@pytest.mark.asyncio
async def test_hybrid_search_does_not_trigger_on_tool_error(kb_config):
    """知识库工具错误不能被当成低相似度召回去触发联网补检索。"""
    runner = _kb_runner(kb_config)
    kb_spec = _search_knowledge_tool("[TOOL_ERROR] 自动检索知识库失败: timeout")

    with patch("app.services.ai.tools.advanced_auxiliary_tools.web_search_baidu_raw", AsyncMock()) as web_search:
        chunks = []
        async for chunk in runner._auto_invoke_search_knowledge_base(
            query="系统 A 支持什么？",
            tools=[kb_spec],
            dataset_ids="default",
        ):
            chunks.append(chunk)

    web_search.assert_not_awaited()
    assert not any(c.get("title") == "触发联网辅助检索" for c in chunks)
    assert any(c.get("__knowledge_service_unavailable__") is True for c in chunks)


@pytest.mark.asyncio
async def test_hallucination_reflection_loop_corrects(kb_config):
    """验证当检测到幻觉时触发自反思重写循环，若第二次通过则输出正确答案。"""
    runner = _kb_runner(
        kb_config,
        execution_options={"grounding_enabled": True, "hallucination_check": True},
    )
    runner._rag_empty = False
    runner._valid_citation_ids = ["chunk_1"]

    # Mock 第一次生成包含幻觉，第二次生成没有幻觉
    fake_generator_first = [{"content": "系统 A 支持 B。"}]
    fake_generator_second = [{"content": "根据文献，系统 A 支持 C[ID:chunk_1]。"}]

    call_count = 0
    async def mock_execute_agentscope(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        source = fake_generator_first if call_count == 1 else fake_generator_second
        for c in source:
            yield c

    # Mock 第一次判定有幻觉，第二次判定无幻觉
    eval_call_count = 0
    async def mock_evaluate(query, context, response, enabled=True):
        nonlocal eval_call_count
        eval_call_count += 1
        if eval_call_count == 1:
            return {"is_hallucinated": True, "reason": "文献里没写支持 B"}
        return {"is_hallucinated": False, "reason": ""}

    async def mock_auto_prefetch(*args, **kwargs):
        yield {
            "__knowledge_output__": json.dumps({
                "content": "文献提到：系统 A 支持 C。",
                "citations": [
                    {
                        "chunk_id": "chunk_1",
                        "doc_id": "doc-1",
                        "dataset_id": "ds-1",
                        "doc_name": "系统说明.pdf",
                    }
                ],
            })
        }

    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_knowledge_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", mock_evaluate), \
         patch.object(runner, "_is_hallucinated_with_rag_reply", return_value=False), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.core.redis.get_redis", AsyncMock(return_value=None)):

        events = []
        async for chunk in runner._execute_raw([{"role": "user", "content": "系统 A 支持什么？"}]):
            events.append(chunk)

        # 验证是否触发了反思日志事件
        assert any("检测到无依据表述" in str(e.get("title")) for e in events)
        assert call_count == 2
        # 验证最后返回的是第二次通过校验的正确内容
        assert any("根据文献，系统 A 支持 C[ID:chunk_1]" in str(e.get("content")) for e in events)


@pytest.mark.asyncio
async def test_hallucination_max_retries_retains_answer_with_soft_warning(kb_config):
    """反思次数耗尽后保留最后回答，并追加一次消息内风险提示。"""
    runner = _kb_runner(
        kb_config,
        execution_options={"grounding_enabled": True, "hallucination_check": True},
    )
    runner._rag_empty = False

    async def mock_execute_agentscope(*args, **kwargs):
        yield {"content": "系统 A 支持 B。"}

    # 持续判定有幻觉
    async def mock_evaluate(query, context, response, enabled=True):
        return {"is_hallucinated": True, "reason": "持续包含臆造事实"}

    async def mock_auto_prefetch(*args, **kwargs):
        yield {
            "__knowledge_output__": json.dumps({
                "content": "文献提到：系统 A 支持 C。",
                "citations": [
                    {
                        "chunk_id": "chunk_1",
                        "doc_id": "doc-1",
                        "dataset_id": "ds-1",
                        "doc_name": "系统说明.pdf",
                    }
                ],
            })
        }

    with patch.object(
        GroundingService,
        "warning_chunk",
        wraps=GroundingService.warning_chunk,
    ) as warning_mock, \
         patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_knowledge_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", mock_evaluate), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        events = []
        async for chunk in runner._execute_raw([{"role": "user", "content": "系统 A 支持什么？"}]):
            events.append(chunk)

        content = "".join(str(e.get("content") or "") for e in events)
        assert "系统 A 支持 B。" in content
        assert content.count("风险提示") == 1
        assert warning_mock.call_count == 1
        assert not any(e.get("title") == "安全网关最终拦截" for e in events)
        assert not any(e.get("type") == "grounding_blocked" for e in events)


@pytest.mark.asyncio
async def test_hallucination_guard_is_disabled_by_default(kb_config):
    runner = _kb_runner(kb_config)
    runner._rag_empty = False

    async def mock_execute_agentscope(*args, **kwargs):
        yield {"content": "系统 A 支持 B。"}

    async def mock_auto_prefetch(*args, **kwargs):
        yield {
            "__knowledge_output__": json.dumps({
                "content": "文献提到：系统 A 支持 C。",
                "citations": [],
            })
        }

    evaluate = AsyncMock(return_value={"is_hallucinated": True, "reason": "不应执行"})
    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_knowledge_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", evaluate), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = [
            event
            async for event in runner._execute_raw(
                [{"role": "user", "content": "系统 A 支持什么？"}]
            )
        ]

    evaluate.assert_not_awaited()
    assert "系统 A 支持 B。" == "".join(str(event.get("content") or "") for event in events)
    assert not any(event.get("category") == "grounding" for event in events)


@pytest.mark.asyncio
async def test_stream_error_bypasses_knowledge_grounding_reflection(kb_config):
    runner = _kb_runner(
        kb_config,
        execution_options={"grounding_enabled": True, "hallucination_check": True},
    )
    runner._rag_empty = False

    async def mock_execute_agentscope(*args, **kwargs):
        yield {
            "type": "error",
            "status": "error",
            "content": "⚠️ [流式安全拦截] 检测到重复输出。",
        }

    async def mock_auto_prefetch(*args, **kwargs):
        yield {
            "__knowledge_output__": json.dumps(
                {"content": "文献提到：系统 A 支持 C。", "citations": []}
            )
        }

    evaluate = AsyncMock(
        return_value={"is_hallucinated": True, "reason": "不应对流式错误做事实评估"}
    )
    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_knowledge_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", evaluate), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=([], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = [
            event
            async for event in runner._execute_raw(
                [{"role": "user", "content": "系统 A 支持什么？"}]
            )
        ]

    evaluate.assert_not_awaited()
    assert any(event.get("type") == "error" for event in events)
    assert not any(event.get("category") == "grounding" for event in events)

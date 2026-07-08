from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import json

from app.schemas.agent import ChatConfig
from app.services.ai.hallucination_evaluator import HallucinationEvaluator
from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
from app.services.ai.runtime.agentscope.compat import AIMessage

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


@pytest.mark.asyncio
async def test_hallucination_evaluator_success():
    """验证 HallucinationEvaluator 是否能正确请求大模型并解析幻觉判定 JSON。"""
    mock_llm = MagicMock()
    mock_client = AsyncMock()
    
    mock_response = MagicMock()
    mock_response.content = '{"is_hallucinated": true, "reason": "回答包含了事实文献未提及的新参数 B"}'
    mock_client.generate_async.return_value = mock_response

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

    kb_spec = MagicMock()
    kb_spec.callable = AsyncMock(return_value=kb_empty_output)

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
        final_output = json.loads(final_output_str)

        assert "【互联网参考事实文献】" in final_output["content"]
        assert len(final_output["citations"]) == 1
        assert final_output["citations"][0]["source_type"] == "web"
        assert "网页: 百度百科: 混合检索" in final_output["citations"][0]["doc_name"]


@pytest.mark.asyncio
async def test_hallucination_reflection_loop_corrects(kb_config):
    """验证当检测到幻觉时触发自反思重写循环，若第二次通过则输出正确答案。"""
    runner = _kb_runner(kb_config)
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
    async def mock_evaluate(query, context, response):
        nonlocal eval_call_count
        eval_call_count += 1
        if eval_call_count == 1:
            return {"is_hallucinated": True, "reason": "文献里没写支持 B"}
        return {"is_hallucinated": False, "reason": ""}

    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", mock_evaluate), \
         patch.object(runner, "_is_hallucinated_with_rag_reply", return_value=False):

        events = []
        async for chunk in runner._execute_raw([{"role": "user", "content": "系统 A 支持什么？"}]):
            events.append(chunk)

        # 验证是否触发了反思日志事件
        assert any("检测到无依据表述" in str(e.get("title")) for e in events)
        assert call_count == 2
        # 验证最后返回的是第二次通过校验的正确内容
        assert any("根据文献，系统 A 支持 C[ID:chunk_1]" in str(e.get("content")) for e in events)


@pytest.mark.asyncio
async def test_hallucination_max_retries_triggers_fatal_fallback(kb_config):
    """验证当反思次数耗尽仍有幻觉时，触发安全网关熔断拦截并降级提示。"""
    runner = _kb_runner(kb_config)
    runner._rag_empty = False

    async def mock_execute_agentscope(*args, **kwargs):
        yield {"content": "系统 A 支持 B。"}

    # 持续判定有幻觉
    async def mock_evaluate(query, context, response):
        return {"is_hallucinated": True, "reason": "持续包含臆造事实"}

    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", mock_evaluate):

        events = []
        async for chunk in runner._execute_raw([{"role": "user", "content": "系统 A 支持什么？"}]):
            events.append(chunk)

        # 检查是否上报了最终拦截 log
        assert any(e.get("title") == "安全网关最终拦截" for e in events)
        # 检查最终返回的降级文案
        assert any("抱歉，在系统知识库中未检索到相关内容" in str(e.get("content")) for e in events)

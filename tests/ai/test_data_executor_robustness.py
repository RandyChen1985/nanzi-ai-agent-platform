import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.prompts import DataQueryPrompts, GeneralChatPrompts
from app.services.ai.intent_service import IntentType
from app.services.ai.turn_classifier import TurnClassification, TurnType, attach_turn_classification
from app.services.ai.data_query_turn_classifier import DataQueryTurnClassification, DataQueryTurnType
from langchain_core.messages import AIMessage, SystemMessage

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override global infrastructure initialization to keep this executor unit test offline."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
        patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.fixture(scope="function", autouse=True)
def default_data_query_turn_classification():
    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="测试默认：新数据查询",
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=False,
        intent=IntentType.DATA_QUERY,
    )
    with patch(
        "app.services.ai.executors.data_executor.resolve_data_query_turn_classification",
        AsyncMock(return_value=(classification, None, 0.0)),
    ) as mock_resolve:
        yield mock_resolve


@pytest.mark.asyncio
async def test_data_executor_react_nudge():
    """
    Verify that DataQueryExecutor nudges the model if it's "chatty" but doesn't call tools in Turn 1.
    """
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.capabilities = ["data_query"]

    trace_id = "test-trace"
    trace_buffer = []
    
    executor = DataQueryExecutor(mock_config, trace_id, trace_buffer)
    
    # 1. Prepare Mock Responses (Must be > 50 chars to trigger nudge)
    procrastinating_response = MagicMock(spec=AIMessage)
    procrastinating_response.content = "好的，我理解您的需求是查询监控数据。为了能够给出准确的分析结果，我首先需要调用相关工具来获取数据库中 monitor 表的具体结构定义。请您稍等片刻。"
    procrastinating_response.tool_calls = []
    
    acting_response = MagicMock(spec=AIMessage)
    acting_response.content = ""
    acting_response.tool_calls = [{"name": "get_dataset_schema", "args": {"query": "monitor"}, "id": "call_1"}]
    
    stopping_response = MagicMock(spec=AIMessage) # Turn 3: Break the loop
    stopping_response.content = "I have the results now."
    stopping_response.tool_calls = []

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "This is the final Answer"

    # 2. Mock model-with-tools astream
    model_with_tools = MagicMock()
    
    async def mock_astream_main(*args, **kwargs):
        # We need to simulate multiple turns. 
        # But DataQueryExecutor.execute recreates the model/stream? No, it uses the same one in the loop.
        # Wait, langchain models are usually stateless. 
        # In the loop:
        # async for chunk in model_with_tools.astream(langchain_messages):
        # We can use side_effect on astream to return different generators
        pass

    async def gen_1(*args, **kwargs): yield procrastinating_response
    async def gen_2(*args, **kwargs): yield acting_response
    async def gen_3(*args, **kwargs): yield stopping_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2(), gen_3()]
    
    # model_with_tools is the result of bind_tools
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools
    
    # 3. Mock synthesis LLM astream
    llm_syn = MagicMock()
    async def mock_astream_syn(*args, **kwargs):
        yield final_answer
    llm_syn.astream.side_effect = mock_astream_syn

    # 4. Patch context
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        
        # Mock internal tool execution
        executor._dispatch_tool_safe = AsyncMock(return_value=({'name': 'get_dataset_schema', 'id': 'call_1', 'args': {'query': 'monitor'}}, "Schema YAML", 100.0))
        
        # Actually execute
        messages = [{"role": "user", "content": "帮我查一下监控数据"}]
        events = []
        async for chunk in executor.execute(messages):
            events.append(chunk)

        # Verification: 
        # astream should have been called three times
        assert model_with_tools.astream.call_count == 3
        
        # Check if the nudge message was added to history in the second call
        history_in_second_call = model_with_tools.astream.call_args_list[1][0][0]
        assert any("检测到你在描述计划但未调用工具" in str(m.content) for m in history_in_second_call)        
        assert not any(
            chunk.get("content") == procrastinating_response.content
            for chunk in events
        )
        print("\n✅ DataQueryExecutor Nudge Logic Verified with astream.")


def test_synthesis_prompts_require_valid_markdown_tables():
    """最终合成提示应明确要求标准 Markdown 表格，避免模型把整张表压成一行。"""
    data_message = DataQueryPrompts.synthesis_user_message("查询用户", "执行 SQL 返回 2 行")
    followup_message = DataQueryPrompts.followup_synthesis_user_message("分析一下", '{"rows": []}')
    chat_message = GeneralChatPrompts.synthesis_user_message("整理结果", "工具返回表格数据")

    for message in (data_message, followup_message, chat_message):
        assert "Markdown 输出规范" in message
        assert "表头、分隔行、每一条数据行必须各占独立一行" in message
        assert "禁止把整张表压成一行" in message
        assert "禁止使用 `||`" in message


@pytest.mark.asyncio
async def test_data_executor_auto_fetches_schema_when_model_returns_no_tool_call():
    """新查数轮首轮模型不调用工具时，执行器应兜底发起 get_dataset_schema，避免空转熔断。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-auto-schema", [], {"dry_run": False})

    empty_response = MagicMock(spec=AIMessage)
    empty_response.content = "我先看看有哪些数据可以用。"
    empty_response.tool_calls = []

    sql_response = MagicMock(spec=AIMessage)
    sql_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    sql_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {"sql": "select 1", "data_source": "ds", "dataset_name": "users"},
        "id": "call_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield empty_response

    async def gen_2(*args, **kwargs):
        yield sql_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    schema_tool = MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])
    schema_tool.name = "get_dataset_schema"
    sql_tool = MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])
    sql_tool.name = "execute_sql_query"

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: users")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[schema_tool, sql_tool])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return tool_call, "schema yaml", 3.0
            return tool_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查询用户列表"}]):
            events.append(chunk)

    first_tool_call = executor._dispatch_tool_safe.await_args_list[0].args[0]
    assert first_tool_call["name"] == "get_dataset_schema"
    assert first_tool_call["args"]["keywords"] == "查询用户列表"
    thought_logs = [e for e in events if e.get("title") == "模型决策完成: 第 1 轮"]
    assert thought_logs
    assert "当前阶段: NEED_SCHEMA" in thought_logs[0].get("details", "")
    second_thought_logs = [e for e in events if e.get("title") == "模型决策完成: 第 2 轮"]
    assert second_thought_logs
    assert "当前阶段: NEED_SQL" in second_thought_logs[0].get("details", "")
    assert any(e.get("title") == "兜底检索数据集定义" for e in events if e.get("type") == "log")
    assert not any(e.get("title") == "🧭 触发空转熔断保护" for e in events if e.get("type") == "log")


@pytest.mark.asyncio
async def test_data_executor_rewrites_contextual_new_data_query_for_schema_fallback():
    """多轮新数据查询兜底查 Schema 时，应使用上下文化后的完整问题。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-standalone-schema", [], {"dry_run": False})
    executor._resolve_standalone_query_for_new_data_query = AsyncMock(return_value="查询上海机房本月 PUE 趋势")

    empty_response = MagicMock(spec=AIMessage)
    empty_response.content = "我先看看有哪些数据可以用。"
    empty_response.tool_calls = []

    sql_response = MagicMock(spec=AIMessage)
    sql_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    sql_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {"sql": "select 1", "data_source": "ds", "dataset_name": "pue"},
        "id": "call_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield empty_response

    async def gen_2(*args, **kwargs):
        yield sql_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    schema_tool = MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])
    schema_tool.name = "get_dataset_schema"
    sql_tool = MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])
    sql_tool.name = "execute_sql_query"

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: pue")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[schema_tool, sql_tool])), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])) as mock_search_examples, \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return tool_call, "schema yaml", 3.0
            return tool_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        async for _ in executor.execute([
            {"role": "user", "content": "查询上海机房上周 PUE 趋势"},
            {"role": "assistant", "content": "上海机房上周 PUE 趋势如下。"},
            {"role": "user", "content": "那本月呢"},
        ]):
            pass

    first_tool_call = executor._dispatch_tool_safe.await_args_list[0].args[0]
    assert first_tool_call["name"] == "get_dataset_schema"
    assert first_tool_call["args"]["keywords"] == "查询上海机房本月 PUE 趋势"
    assert mock_search_examples.await_args.args[0] == "查询上海机房本月 PUE 趋势"
    executor._resolve_standalone_query_for_new_data_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_executor_retries_schema_miss_once_then_aborts_without_sql():
    """Schema 未命中时只换关键词重试一次；仍未命中则终止，不进入空转或 SQL 阶段。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-schema-miss-abort", [], {"dry_run": False})

    first_schema_call = {
        "name": "get_dataset_schema",
        "args": {"keywords": "机房 临港 数据中心 机柜 机架 机位"},
        "id": "call_schema_1",
    }
    first_response = MagicMock(spec=AIMessage)
    first_response.content = ""
    first_response.tool_calls = [first_schema_call]

    retry_response = MagicMock(spec=AIMessage)
    retry_response.content = "我会换一组关键词重新检索元数据。"
    retry_response.tool_calls = []

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield first_response

    async def gen_2(*args, **kwargs):
        yield retry_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    schema_miss = "No relevant schema info found for '机房 临港 数据中心 机柜 机架 机位'.\nDebug Logs:"
    schema_tool = MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])
    schema_tool.name = "get_dataset_schema"
    sql_tool = MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])
    sql_tool.name = "execute_sql_query"

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[schema_tool, sql_tool])), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        async def dispatch_side_effect(tool_call, tools):
            return tool_call, schema_miss, 4.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查询临港数据中心机房机柜机位"}]):
            events.append(chunk)

    assert executor._dispatch_tool_safe.await_count == 2
    assert all(call.args[0]["name"] != "execute_sql_query" for call in executor._dispatch_tool_safe.await_args_list)
    assert any("元数据未命中" in chunk.get("title", "") for chunk in events if chunk.get("type") == "log")
    assert any("未找到与本次问题相关的数据集定义" in chunk.get("content", "") for chunk in events)


@pytest.mark.asyncio
async def test_data_executor_resolves_standalone_query_with_recent_history():
    """上下文依赖的新数据查询短句应改写成可独立检索的完整查数问题。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    executor = DataQueryExecutor(mock_config, "trace-standalone-direct", [])

    langchain_messages = executor._convert_history([
        {"role": "user", "content": "查询上海机房上周 PUE 趋势"},
        {"role": "assistant", "content": "上海机房上周 PUE 趋势如下。"},
        {"role": "user", "content": "那本月呢"},
    ])
    rewrite_response = MagicMock()
    rewrite_response.content = "查询上海机房本月 PUE 趋势"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=rewrite_response)

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=llm)):
        result = await executor._resolve_standalone_query_for_new_data_query("那本月呢", langchain_messages)

    assert result == "查询上海机房本月 PUE 趋势"
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_executor_keeps_complete_new_data_query_without_rewrite_llm():
    """当前问题已经完整时，不应额外调用 LLM 改写。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    executor = DataQueryExecutor(mock_config, "trace-standalone-complete", [])
    langchain_messages = executor._convert_history([
        {"role": "user", "content": "查询上海机房上周 PUE 趋势"},
        {"role": "assistant", "content": "上海机房上周 PUE 趋势如下。"},
        {"role": "user", "content": "查询北京机房本月能耗趋势"},
    ])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock()) as mock_get_llm:
        result = await executor._resolve_standalone_query_for_new_data_query("查询北京机房本月能耗趋势", langchain_messages)

    assert result == "查询北京机房本月能耗趋势"
    mock_get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_data_executor_adds_schema_tool_when_agent_config_omits_it():
    """DataExecutor 即使配置漏选 get_dataset_schema，也应补齐核心查数工具并触发 schema 兜底。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-missing-schema-tool", [], {"dry_run": False})

    empty_response = MagicMock(spec=AIMessage)
    empty_response.content = "我先分析一下用户需求。"
    empty_response.tool_calls = []

    sql_response = MagicMock(spec=AIMessage)
    sql_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    sql_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {"sql": "select 1", "data_source": "ds", "dataset_name": "users"},
        "id": "call_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield empty_response

    async def gen_2(*args, **kwargs):
        yield sql_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    schema_tool = MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])
    schema_tool.name = "get_dataset_schema"
    sql_tool = MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])
    sql_tool.name = "execute_sql_query"

    async def get_tools_side_effect(names):
        assert "get_dataset_schema" in names
        assert "execute_sql_query" in names
        return [schema_tool, sql_tool]

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: users")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(side_effect=get_tools_side_effect)), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return tool_call, "schema yaml", 3.0
            return tool_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查一下用户列表数据呢"}]):
            events.append(chunk)

    first_tool_call = executor._dispatch_tool_safe.await_args_list[0].args[0]
    assert first_tool_call["name"] == "get_dataset_schema"
    assert any(e.get("title") == "兜底检索数据集定义" for e in events if e.get("type") == "log")
    assert not any(e.get("title") == "🧭 触发空转熔断保护" for e in events if e.get("type") == "log")


@pytest.mark.asyncio
async def test_data_executor_skill_context_does_not_block_schema_fallback():
    """技能摘要是辅助上下文；ChatBI 新查数轮空转时仍应优先兜底查 Schema。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = (
        "[Active Skills Loaded]\n"
        "=== 已匹配技能: 用户列表查询 (ID: user-list-query) ===\n"
        "- 完整指令: 未预载；执行前必须调用 read_skill_instruction\n\n"
        "You are a Data Analysis Assistant. {dataset_menu}"
    )
    mock_config.tools = ["get_dataset_schema", "execute_sql_query", "read_skill_instruction"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-skill-auxiliary", [], {"dry_run": False})

    empty_response = MagicMock(spec=AIMessage)
    empty_response.content = "我先看一下应该怎么查。"
    empty_response.tool_calls = []

    sql_response = MagicMock(spec=AIMessage)
    sql_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    sql_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {"sql": "select 1", "data_source": "ds", "dataset_name": "users"},
        "id": "call_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield empty_response

    async def gen_2(*args, **kwargs):
        yield sql_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    schema_tool = MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])
    schema_tool.name = "get_dataset_schema"
    sql_tool = MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])
    sql_tool.name = "execute_sql_query"
    read_skill_tool = MagicMock(name="read_skill_instruction", spec=["name", "ainvoke"])
    read_skill_tool.name = "read_skill_instruction"

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: users")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[schema_tool, sql_tool, read_skill_tool])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return tool_call, "schema yaml", 3.0
            return tool_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查一下用户列表数据呢"}]):
            events.append(chunk)

    first_tool_call = executor._dispatch_tool_safe.await_args_list[0].args[0]
    assert first_tool_call["name"] == "get_dataset_schema"
    assert any(e.get("title") == "兜底检索数据集定义" for e in events if e.get("type") == "log")
    assert not any(e.get("title") == "🧭 触发空转熔断保护" for e in events if e.get("type") == "log")


@pytest.mark.asyncio
async def test_data_executor_injects_global_guardrails_before_db_prompt():
    """DataExecutor 应注入自己的流程底线，但保留 DB 智能体 prompt 作为业务主干。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "DB ChatBI prompt. Use {dataset_menu}."
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-guardrails", [], {"dry_run": False})

    schema_response = MagicMock(spec=AIMessage)
    schema_response.content = ""
    schema_response.tool_calls = [{"name": "get_dataset_schema", "args": {"keywords": "用户"}, "id": "call_schema"}]

    model_with_tools = MagicMock()
    captured = {}

    async def gen_1(messages):
        captured["messages"] = messages
        yield schema_response

    model_with_tools.astream.side_effect = gen_1
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock()), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: users")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="1")):

        executor._dispatch_tool_safe = AsyncMock(return_value=(schema_response.tool_calls[0], "schema yaml", 3.0))
        async for _ in executor.execute([{"role": "user", "content": "查询用户"}]):
            pass

    system_content = captured["messages"][0].content
    assert "[DataExecutor Global Guardrails]" in system_content
    assert system_content.index("[DataExecutor Global Guardrails]") < system_content.index("DB ChatBI prompt")
    assert "必须先调用 get_dataset_schema" in system_content
    assert "长期记忆中的业务别名" in system_content
    assert "DB ChatBI prompt" in system_content


@pytest.mark.asyncio
async def test_data_executor_followup_visualization_reuses_last_result_without_querying():
    """基于上一轮结果的可视化追问应直接分析历史结果，不应重新检索 schema 或执行 SQL。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(
        mock_config,
        "trace-followup",
        [],
        user_info={"user_id": 1001},
        conversation_id="conv-1",
    )

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "基于上一轮用户列表，已生成状态分布图。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn
    last_result = {
        "sql": "select status, count(*) as total_count from users group by status",
        "dataset_name": "users",
        "data_source": "mysql_oa",
        "rows": [{"status": "启用", "total_count": 8}, {"status": "停用", "total_count": 2}],
    }
    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：复用上一轮结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=False,
        intent=IntentType.DATA_QUERY,
    )

    with patch("app.services.ai.executors.data_executor.resolve_data_query_turn_classification", AsyncMock(return_value=(reuse_turn, None, 0.0))), \
         patch("app.services.ai.memory_service.memory_service.get_last_data_result", AsyncMock(return_value=last_result)) as mock_get_last, \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock()) as mock_get_tools, \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock()) as mock_get_llm:

        events = []
        async for chunk in executor.execute([
            {"role": "assistant", "content": "上一轮返回了用户列表。"},
            {"role": "user", "content": "可视化分析一下"},
        ]):
            events.append(chunk)

    assert mock_get_last.await_count == 1
    mock_get_tools.assert_not_called()
    mock_get_llm.assert_not_called()
    assert any(chunk.get("content") == final_answer.content for chunk in events)


@pytest.mark.asyncio
async def test_data_executor_internal_turn_classifier_overrides_external_shared_turn():
    """DataExecutor 内部分类应是最终执行依据，而不是外部 shared_turn。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(
        mock_config,
        "trace-internal-turn",
        [],
        user_info={"user_id": 1001},
        conversation_id="conv-1",
    )
    attach_turn_classification(
        executor,
        TurnClassification(
            turn_type=TurnType.DATA_QUERY_REQUEST,
            reasoning="外部只知道是数据查询请求",
            intent=IntentType.DATA_QUERY,
        ),
    )

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "已基于上一轮结果完成分析。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn
    last_result = {
        "sql": "select status, count(*) as total_count from users group by status",
        "dataset_name": "users",
        "data_source": "mysql_oa",
        "rows": [{"status": "启用", "total_count": 8}],
    }
    internal_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="内部识别为复用上一轮结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    with patch("app.services.ai.executors.data_executor.resolve_data_query_turn_classification", AsyncMock(return_value=(internal_turn, None, 0.0))) as mock_resolve_turn, \
         patch("app.services.ai.memory_service.memory_service.get_last_data_result", AsyncMock(return_value=last_result)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock()) as mock_get_tools, \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock()) as mock_get_llm:

        events = []
        async for chunk in executor.execute([
            {"role": "user", "content": "查询用户状态分布"},
            {"role": "assistant", "content": "上一轮返回了用户状态分布。"},
            {"role": "user", "content": "分析一下"},
        ]):
            events.append(chunk)

    mock_resolve_turn.assert_awaited_once()
    mock_get_tools.assert_not_called()
    mock_get_llm.assert_not_called()
    assert executor.turn_classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert executor._requires_fresh_data is False
    turn_logs = [chunk for chunk in events if chunk.get("type") == "log" and chunk.get("title") == "ChatBI 请求类别分析结果"]
    assert turn_logs
    assert "复用上一轮结果" in turn_logs[0]["details"]
    assert "内部识别为复用上一轮结果" in turn_logs[0]["details"]
    assert any(chunk.get("content") == final_answer.content for chunk in events)


@pytest.mark.asyncio
async def test_data_executor_stores_successful_sql_result_for_followups():
    """SQL 查询成功后应保存结构化结果，供下一轮可视化/分析追问复用。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(
        mock_config,
        "trace-store",
        [],
        user_info={"user_id": 1001},
        conversation_id="conv-1",
    )

    acting_response = MagicMock(spec=AIMessage)
    acting_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    acting_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {
            "sql": "select status, count(*) as total_count from users group by status",
            "data_source": "mysql_oa",
            "dataset_name": "users",
        },
        "id": "call_sql",
    }]
    stopping_response = MagicMock(spec=AIMessage)
    stopping_response.content = "I have the results now."
    stopping_response.tool_calls = []

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield acting_response

    async def gen_2(*args, **kwargs):
        yield stopping_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "用户状态分布已汇总。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn
    sql_rows = [{"status": "启用", "total_count": 8}, {"status": "停用", "total_count": 2}]

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.ai.memory_service.memory_service.set_last_data_result", AsyncMock()) as mock_set_last:

        executor._dispatch_tool_safe = AsyncMock(return_value=(acting_response.tool_calls[0], sql_rows, 12.0))
        async for _ in executor.execute([{"role": "user", "content": "查询用户状态分布"}]):
            pass

    saved_payload = mock_set_last.await_args.args[2]
    assert mock_set_last.await_args.args[:2] == (1001, "conv-1")
    assert saved_payload["sql"] == "select status, count(*) as total_count from users group by status"
    assert saved_payload["dataset_name"] == "users"
    assert saved_payload["rows"] == sql_rows


@pytest.mark.asyncio
async def test_data_executor_stops_react_loop_after_successful_sql():
    """SQL 成功且无异常复核需求时，应直接进入汇总，不再额外跑一轮模型决策。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-fast-path", [], {"dry_run": False})

    acting_response = MagicMock(spec=AIMessage)
    acting_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    acting_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {
            "sql": "select status, count(*) as total_count from users group by status",
            "data_source": "mysql_oa",
            "dataset_name": "users",
        },
        "id": "call_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield acting_response

    async def gen_unexpected(*args, **kwargs):
        raise AssertionError("SQL 成功后不应该再进入下一轮模型决策")
        yield

    model_with_tools.astream.side_effect = [gen_1(), gen_unexpected()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "用户状态分布已汇总。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        executor._dispatch_tool_safe = AsyncMock(return_value=(acting_response.tool_calls[0], [{"status": "启用", "total_count": 8}], 12.0))
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查询用户状态分布"}]):
            events.append(chunk)

    assert model_with_tools.astream.call_count == 1
    assert any(chunk.get("content") == final_answer.content for chunk in events)


@pytest.mark.asyncio
async def test_data_executor_rechecks_empty_sql_result_before_fast_path():
    """SQL 执行成功但结果为空时，应继续一轮复核而不是直接进入 fast path 汇总。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-empty-result-recheck", [], {"dry_run": False})

    sql_call = {
        "name": "execute_sql_query",
        "args": {
            "sql": "select room_name from rooms where shipName like '%书院%'",
            "data_source": "mysql_oa",
            "dataset_name": "rooms",
        },
        "id": "call_sql",
    }
    acting_response = MagicMock(spec=AIMessage)
    acting_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    acting_response.tool_calls = [sql_call]

    recheck_response = MagicMock(spec=AIMessage)
    recheck_response.content = "<thought><sql_plan>{}</sql_plan>我会先复核过滤条件和各子查询是否有数据。</thought>"
    recheck_response.tool_calls = [{
        "name": "execute_sql_query",
        "args": {
            "sql": "select shipName, count(*) as cnt from rooms group by shipName limit 50",
            "data_source": "mysql_oa",
            "dataset_name": "rooms",
        },
        "id": "call_recheck_sql",
    }]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield acting_response

    async def gen_2(*args, **kwargs):
        yield recheck_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "已复核空结果。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn
    empty_result = {
        "columns": [{"name": "room_name", "type": "253"}],
        "items": [],
    }

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        executor._dispatch_tool_safe = AsyncMock(side_effect=[
            (sql_call, empty_result, 12.0),
            (recheck_response.tool_calls[0], [], 8.0),
        ])
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查询书院机房负载率"}]):
            events.append(chunk)

    assert model_with_tools.astream.call_count == 2
    assert executor._dispatch_tool_safe.await_count == 2
    assert any("空结果" in chunk.get("title", "") for chunk in events if chunk.get("type") == "log")


@pytest.mark.asyncio
async def test_data_executor_requires_diagnostic_sql_when_empty_recheck_model_is_chatty():
    """空结果复核阶段模型若不调 SQL，不应汇总，应继续强制直到发生诊断 SQL。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-empty-chatty-recheck", [], {"dry_run": False})

    first_sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select * from rooms where shipName like '%书院%'", "data_source": "mysql_oa", "dataset_name": "rooms"},
        "id": "call_sql",
    }
    diagnostic_sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select shipName, count(*) cnt from rooms group by shipName limit 50", "data_source": "mysql_oa", "dataset_name": "rooms"},
        "id": "call_diag_sql",
    }

    first_response = MagicMock(spec=AIMessage)
    first_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    first_response.tool_calls = [first_sql_call]
    chatty_response = MagicMock(spec=AIMessage)
    chatty_response.content = "我会先分析一下为什么为空。"
    chatty_response.tool_calls = []
    diagnostic_response = MagicMock(spec=AIMessage)
    diagnostic_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    diagnostic_response.tool_calls = [diagnostic_sql_call]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield first_response

    async def gen_2(*args, **kwargs):
        yield chatty_response

    async def gen_3(*args, **kwargs):
        yield diagnostic_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2(), gen_3()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "已完成诊断。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):

        executor._dispatch_tool_safe = AsyncMock(side_effect=[
            (first_sql_call, {"columns": [{"name": "room_name"}], "items": []}, 7.0),
            (diagnostic_sql_call, [], 5.0),
        ])
        async for _ in executor.execute([{"role": "user", "content": "查询书院机房"}]):
            pass

    assert model_with_tools.astream.call_count == 3
    assert executor._dispatch_tool_safe.await_count == 2


@pytest.mark.asyncio
async def test_data_executor_requires_final_sql_after_non_empty_empty_result_diagnostic():
    """空结果诊断 SQL 若返回候选证据，应再执行修正后的最终 SQL 后才汇总。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-empty-final-required", [], {"dry_run": False})

    first_sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select * from rooms where shipName like '%书院%'", "data_source": "mysql_oa", "dataset_name": "rooms"},
        "id": "call_sql",
    }
    diagnostic_sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select shipName, count(*) cnt from rooms group by shipName limit 50", "data_source": "mysql_oa", "dataset_name": "rooms"},
        "id": "call_diag_sql",
    }
    final_sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select * from rooms where shipName like '%书苑%'", "data_source": "mysql_oa", "dataset_name": "rooms"},
        "id": "call_final_sql",
    }

    first_response = MagicMock(spec=AIMessage)
    first_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    first_response.tool_calls = [first_sql_call]
    diagnostic_response = MagicMock(spec=AIMessage)
    diagnostic_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    diagnostic_response.tool_calls = [diagnostic_sql_call]
    final_sql_response = MagicMock(spec=AIMessage)
    final_sql_response.content = "<thought><sql_plan>{}</sql_plan></thought>"
    final_sql_response.tool_calls = [final_sql_call]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield first_response

    async def gen_2(*args, **kwargs):
        yield diagnostic_response

    async def gen_3(*args, **kwargs):
        yield final_sql_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2(), gen_3()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "已按修正后的条件查到数据。"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="6")):

        executor._dispatch_tool_safe = AsyncMock(side_effect=[
            (first_sql_call, {"columns": [{"name": "room_name"}], "items": []}, 7.0),
            (diagnostic_sql_call, [{"shipName": "上海书苑", "cnt": 12}], 5.0),
            (final_sql_call, [{"room_name": "A机房"}], 9.0),
        ])
        async for _ in executor.execute([{"role": "user", "content": "查询书院机房"}]):
            pass

    assert model_with_tools.astream.call_count == 3
    assert executor._dispatch_tool_safe.await_count == 3


def test_data_executor_detects_empty_items_payload():
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = ""
    mock_config.tools = ["execute_sql_query"]

    executor = DataQueryExecutor(mock_config, "trace-empty-detect", [])

    assert executor._detect_empty_result({"columns": [{"name": "x"}], "items": []})
    assert executor._detect_empty_result({"rows": []})
    assert executor._detect_empty_result([])
    assert executor._detect_empty_result({"total": 0, "list": []})
    assert executor._detect_empty_result({"data": {"rows": []}})
    assert executor._detect_empty_result({"result": []})
    assert executor._detect_empty_result({"items": [{"x": 1}]}) is None


@pytest.mark.asyncio
async def test_data_executor_executes_sql_after_single_plan_block():
    """高风险查询首次缺 sql_plan 仅阻断一次；再次带 execute_sql_query 时应真正执行工具。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-plan-once", [], {"dry_run": False})

    schema_call = {"name": "get_dataset_schema", "args": {"keywords": "负载"}, "id": "call_schema"}
    sql_call = {
        "name": "execute_sql_query",
        "args": {
            "sql": "select 1",
            "data_source": "mysql_oa",
            "dataset_name": "users",
        },
        "id": "call_sql",
    }
    schema_response = MagicMock(spec=AIMessage)
    schema_response.content = ""
    schema_response.tool_calls = [schema_call]

    blocked_response = MagicMock(spec=AIMessage)
    blocked_response.content = ""
    blocked_response.tool_calls = [sql_call]

    retry_response = MagicMock(spec=AIMessage)
    retry_response.content = ""
    retry_response.tool_calls = [sql_call]

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield schema_response

    async def gen_2(*args, **kwargs):
        yield blocked_response

    async def gen_3(*args, **kwargs):
        yield retry_response

    model_with_tools.astream.side_effect = [gen_1(), gen_2(), gen_3()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="10")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return schema_call, "schema yaml", 3.0
            return sql_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "按机房分组统计负载率"}]):
            events.append(chunk)

    assert model_with_tools.astream.call_count == 3
    assert executor._dispatch_tool_safe.await_count == 2


@pytest.mark.asyncio
async def test_data_executor_runs_pending_sql_after_empty_round():
    """Schema 已就绪且上一轮 SQL 被拦截后，空转轮次应代为执行待处理 SQL。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-pending-sql", [], {"dry_run": False})

    schema_call = {"name": "get_dataset_schema", "args": {"keywords": "延迟"}, "id": "call_schema"}
    sql_call = {
        "name": "execute_sql_query",
        "args": {"sql": "select 1", "data_source": "ds", "dataset_name": "t"},
        "id": "call_sql",
    }
    schema_response = MagicMock(spec=AIMessage)
    schema_response.content = ""
    schema_response.tool_calls = [schema_call]

    blocked_response = MagicMock(spec=AIMessage)
    blocked_response.content = ""
    blocked_response.tool_calls = [sql_call]

    empty_round = MagicMock(spec=AIMessage)
    empty_round.content = "让我想想"
    empty_round.tool_calls = []

    model_with_tools = MagicMock()

    async def gen_1(*args, **kwargs):
        yield schema_response

    async def gen_2(*args, **kwargs):
        yield blocked_response

    async def gen_3(*args, **kwargs):
        yield empty_round

    model_with_tools.astream.side_effect = [gen_1(), gen_2(), gen_3()]
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    final_answer = MagicMock(spec=AIMessage)
    final_answer.content = "完成"
    llm_syn = MagicMock()

    async def mock_astream_syn(*args, **kwargs):
        yield final_answer

    llm_syn.astream.side_effect = mock_astream_syn

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=llm_syn)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="execute_sql_query", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="10")):

        async def dispatch_side_effect(tool_call, tools):
            if tool_call["name"] == "get_dataset_schema":
                return schema_call, "schema yaml", 3.0
            return sql_call, [{"ok": 1}], 5.0

        executor._dispatch_tool_safe = AsyncMock(side_effect=dispatch_side_effect)

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "按机房分组统计负载率"}]):
            events.append(chunk)

    assert model_with_tools.astream.call_count == 3
    assert executor._dispatch_tool_safe.await_count == 2
    assert any(e.get("title") == "执行待处理 SQL" for e in events if e.get("type") == "log")


@pytest.mark.asyncio
async def test_data_executor_followup_without_last_result_does_not_query_schema():
    """可视化追问缺少上一轮结构化结果时，应提示用户先查询数据，而不是把追问词拿去检索 schema。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(
        mock_config,
        "trace-no-last-result",
        [],
        user_info={"user_id": 1001},
        conversation_id="conv-1",
    )
    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：结果追问但缺少可复用结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=False,
        intent=IntentType.DATA_QUERY,
    )

    with patch("app.services.ai.executors.data_executor.resolve_data_query_turn_classification", AsyncMock(return_value=(reuse_turn, None, 0.0))), \
         patch("app.services.ai.memory_service.memory_service.get_last_data_result", AsyncMock(return_value=None)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock()) as mock_get_tools, \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock()) as mock_get_llm:

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "可视化分析一下"}]):
            events.append(chunk)

    mock_get_tools.assert_not_called()
    mock_get_llm.assert_not_called()
    assert any("没有可复用的上一轮查询结果" in chunk.get("content", "") for chunk in events)


@pytest.mark.asyncio
async def test_data_executor_retries_on_model_stream_error():
    """模型决策流式 transient 失败时应自动重试，而不是立即终止迭代。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.synthesis_model_name = None
    mock_config.synthesis_temperature = None
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-stream-retry", [], {"dry_run": False})

    schema_response = MagicMock(spec=AIMessage)
    schema_response.content = ""
    schema_response.tool_calls = [{"name": "get_dataset_schema", "args": {"query": "users"}, "id": "call_1"}]

    model_with_tools = MagicMock()
    stream_calls = {"count": 0}

    async def failing_then_success(*args, **kwargs):
        stream_calls["count"] += 1
        if stream_calls["count"] == 1:
            raise IndexError("list index out of range")
        yield schema_response

    model_with_tools.astream.side_effect = failing_then_success
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[MagicMock(name="get_dataset_schema", spec=["name", "ainvoke"])])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.chatbi_example_service.ExampleService.search_examples", AsyncMock(return_value=[])), \
         patch("asyncio.sleep", AsyncMock()):

        executor._dispatch_tool_safe = AsyncMock(return_value=(
            {"name": "get_dataset_schema", "id": "call_1", "args": {"query": "users"}},
            "Schema YAML",
            100.0,
        ))

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查一下用户数量"}]):
            events.append(chunk)

    assert stream_calls["count"] >= 2
    assert any(
        chunk.get("type") == "log" and "正在重试" in chunk.get("title", "")
        for chunk in events
    )
    assert not any(
        chunk.get("type") == "log"
        and chunk.get("title") == "⚠️ 模型响应异常"
        and chunk.get("status") == "error"
        for chunk in events
    )
    assert any(
        chunk.get("type") == "log" and chunk.get("title") == "模型决策完成: 第 1 轮"
        for chunk in events
    )


@pytest.mark.asyncio
async def test_data_executor_stream_error_exhausts_retries():
    """流式失败且重试耗尽后，才终止迭代并上报最终错误。"""
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.system_prompt = "You are a Data Analysis Assistant. {dataset_menu}"
    mock_config.tools = ["get_dataset_schema", "execute_sql_query"]
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    mock_config.capabilities = ["data_query"]

    executor = DataQueryExecutor(mock_config, "trace-stream-fail", [], user_info={"user_id": 1001})

    model_with_tools = MagicMock()

    async def always_fail(*args, **kwargs):
        raise IndexError("list index out of range")
        yield  # pragma: no cover

    model_with_tools.astream.side_effect = always_fail
    bound_model = MagicMock()
    bound_model.bind_tools.return_value = model_with_tools

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=bound_model)), \
         patch("app.services.ai.config.AgentConfigProvider.get_dataset_menu", AsyncMock(return_value="Dataset: Test")), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(return_value=[])), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("asyncio.sleep", AsyncMock()):

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查一下用户数量"}]):
            events.append(chunk)

    assert model_with_tools.astream.call_count == 2
    assert any(
        chunk.get("type") == "log"
        and chunk.get("title") == "⚠️ 模型响应异常"
        and chunk.get("status") == "error"
        for chunk in events
    )
    assert not any(chunk.get("content") for chunk in events if chunk.get("type") != "log")


if __name__ == "__main__":
    asyncio.run(test_data_executor_react_nudge())

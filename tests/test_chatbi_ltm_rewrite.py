import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.services.ai.runtime.agentscope.compat import SystemMessage, AIMessage
from app.services.ai.runners.data_agent_runner import DataAgentRunner

@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_chatbi_ltm_rewrite_success():
    """
    验证 DataAgentRunner 能够正确地从 system_prompt 中提取 [Memory Profile] 块，
    并把偏好信息传递给改写大模型，从而将别名 '临港' 改写为 '书院'。
    """
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    # 在 system_prompt 中模拟注入 [Memory Profile] 块
    mock_config.system_prompt = (
        "You are ChatBI.\n\n"
        "[Memory Profile]\n"
        "用户偏好与业务常识映射关系：\n"
        "- 临港: 书院\n"
        "- 浦东: 外高桥\n\n"
        "[Agent Rules]\n"
        "一些无关的规则"
    )

    runner = DataAgentRunner(mock_config, "trace-ltm-rewrite", [])

    # Mock 改写 LLM 的返回值
    rewrite_response = MagicMock(spec=AIMessage)
    rewrite_response.content = "查询书院机房数据"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=rewrite_response)

    # 用 mock_get_llm 替换掉 get_configured_llm，拦截 LLM 的实例化
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=llm)):
        result = await runner._resolve_standalone_query_for_new_data_query("查询临港机房数据", [])

    # 1. 验证改写后的结果是否成功转换
    assert result == "查询书院机房数据"
    llm.ainvoke.assert_awaited_once()

    # 2. 检查传入 LLM 的 prompt 是否包含 [Memory Profile] 部分，并且不包含多余内容
    call_args = llm.ainvoke.await_args
    sent_message = call_args.args[0][0]
    assert isinstance(sent_message, SystemMessage)
    
    # 确认 [Memory Profile] 被正确截取并加入到了 prompt 里
    assert "【用户个性化偏好与记忆】" in sent_message.content
    assert "临港: 书院" in sent_message.content
    assert "浦东: 外高桥" in sent_message.content
    # 确保没有引入 [Agent Rules] 等无关大段废话
    assert "[Agent Rules]" not in sent_message.content


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_chatbi_ltm_rewrite_no_ltm_no_rewrite():
    """
    验证在 system_prompt 中没有 [Memory Profile]（或者只有空白标题）且没有历史追问上下文时，
    改写器不会调用改写 LLM，直接原样返回问题。
    """
    mock_config = MagicMock()
    mock_config.agent_name = "ChatBI"
    mock_config.model_name = "test-model"
    mock_config.temperature = 0.0
    # 无 LTM 块或 LTM 块为空
    mock_config.system_prompt = (
        "You are ChatBI.\n\n"
        "[Memory Profile]\n\n"
        "[Agent Rules]\n"
        "一些无关的规则"
    )

    runner = DataAgentRunner(mock_config, "trace-no-ltm", [])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock()) as mock_get_llm:
        result = await runner._resolve_standalone_query_for_new_data_query("查询临港机房数据", [])

    # 应该直接原样返回，不调用 LLM
    assert result == "查询临港机房数据"
    mock_get_llm.assert_not_called()

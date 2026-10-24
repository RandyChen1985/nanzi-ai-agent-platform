import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.prompt_ops.prompt_service import PromptService
from app.schemas.prompt import PromptSource

pytestmark = pytest.mark.no_infrastructure

# --- Tests ---


def _mock_chat_client(content: str):
    chat_client = AsyncMock()
    chat_client.generate_text.return_value = content
    return chat_client

def test_extract_variables():
    """测试提示词变量提取"""
    text = "Hello {name}, your task is {task}. Don't forget {name}."
    vars = PromptService.extract_variables(text)
    assert set(vars) == {"name", "task"}
    assert len(vars) == 2

@pytest.mark.asyncio
async def test_get_prompt_detail_config():
    """测试获取系统配置提示词详情"""
    mock_val = "System Prompt {var}"
    mock_history = [
        {"old_value": "Old Prompt", "created_at": "2026-01-01", "description": "init"}
    ]
    
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_get, \
         patch("app.services.config_service.ConfigService.get_config_history", new_callable=AsyncMock) as mock_hist:
        
        mock_get.return_value = mock_val
        mock_hist.return_value = mock_history
        
        # 获取最新版
        detail = await PromptService.get_prompt_detail(PromptSource.SYSTEM_CONFIG, "test_key")
        assert detail.content == mock_val
        assert detail.variables == ["var"]
        assert detail.version_number == 2 # 1 history + 1 current
        
        # 获取历史版
        detail_old = await PromptService.get_prompt_detail(PromptSource.SYSTEM_CONFIG, "test_key", version=1)
        assert detail_old.content == "Old Prompt"

@pytest.mark.asyncio
async def test_test_prompt_logic():
    """测试提示词 Playground 模拟逻辑"""
    content = "You are {role}. Input: {input}"
    variables = {"role": "Assistant", "input": "Hello"}
    
    mock_llm = object()
    mock_chat = _mock_chat_client("Mock Response")
    
    with patch("app.services.ai.prompt_ops.prompt_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.prompt_ops.prompt_service.chat_client_from_handle", return_value=mock_chat):
        mock_get_llm.return_value = mock_llm
        
        resp = await PromptService.test_prompt(content, variables, user_input="User Msg")
        
        # 验证插值
        assert resp.interpolated_prompt == "You are Assistant. Input: Hello"
        assert resp.raw_output == "Mock Response"
        
        # 验证 LLM 调用参数
        args, kwargs = mock_chat.generate_text.call_args
        messages = args[0]
        assert messages[0].content[0].text == "You are Assistant. Input: Hello"
        assert messages[1].content[0].text == "User Msg"

@pytest.mark.asyncio
async def test_optimize_prompt_parsing():
    """测试提示词优化建议的解析逻辑"""
    content = "Original Prompt"
    mock_json_resp = {
        "suggestions": [
            {"title": "Optimized", "content": "Better Prompt", "reason": "Because"}
        ]
    }
    
    mock_llm = object()
    # 模拟包含 Markdown 代码块的返回
    mock_chat = _mock_chat_client(f"```json\n{json.dumps(mock_json_resp)}\n```")
    
    with patch("app.services.ai.prompt_ops.prompt_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.prompt_ops.prompt_service.chat_client_from_handle", return_value=mock_chat):
        mock_get_llm.return_value = mock_llm
        
        data = await PromptService.optimize_prompt(content)
        assert data["suggestions"][0]["title"] == "Optimized"
        assert data["suggestions"][0]["content"] == "Better Prompt"
        args, _kwargs = mock_chat.generate_text.call_args
        system_text = args[0][0].content[0].text
        assert "恰好 8 个" in system_text
        assert "ReAct / 工具调用规范" in system_text
        assert "反幻觉 / 取证门禁" in system_text
        assert "输出契约 (Schema)" in system_text
        assert "CDATA" in system_text


def test_parse_optimize_prompt_response_xml_cdata():
    from app.services.ai.prompt_ops.prompt_service import parse_optimize_prompt_response

    raw = """
    <suggestions>
      <item>
        <title>结构化增强版</title>
        <reason>更清晰</reason>
        <content><![CDATA[
你是助手。若用户说 "hello"，回复世界。
]]></content>
      </item>
      <item>
        <title>角色设定版</title>
        <reason>更专业</reason>
        <content><![CDATA[Role: expert]]></content>
      </item>
    </suggestions>
    """
    data = parse_optimize_prompt_response(raw)
    assert len(data["suggestions"]) == 2
    assert '说 "hello"' in data["suggestions"][0]["content"]
    assert data["suggestions"][1]["title"] == "角色设定版"


def test_parse_optimize_prompt_response_json_with_raw_newline():
    from app.services.ai.prompt_ops.prompt_service import parse_optimize_prompt_response

    # content 字符串内含未转义换行（模型常见瑕疵）
    raw = '{\n  "suggestions": [\n    {"title": "A", "content": "line1\nline2", "reason": "ok"}\n  ]\n}'
    data = parse_optimize_prompt_response(raw)
    assert data["suggestions"][0]["content"] == "line1\nline2"


def test_parse_optimize_prompt_response_json_trailing_comma():
    from app.services.ai.prompt_ops.prompt_service import parse_optimize_prompt_response

    raw = '{"suggestions":[{"title":"A","content":"B","reason":"C"},]}'
    data = parse_optimize_prompt_response(raw)
    assert data["suggestions"][0]["content"] == "B"


@pytest.mark.asyncio
async def test_optimize_prompt_retries_on_broken_json():
    content = "Original Prompt"
    broken = '{"suggestions":[{"title":"A","content":"say "hi" now","reason":"x"}]}'
    fixed = """
    <suggestions>
      <item>
        <title>A</title>
        <reason>x</reason>
        <content><![CDATA[say "hi" now]]></content>
      </item>
    </suggestions>
    """
    mock_llm = object()
    mock_chat = AsyncMock()
    mock_chat.generate_text.side_effect = [broken, fixed]

    with patch("app.services.ai.prompt_ops.prompt_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.prompt_ops.prompt_service.chat_client_from_handle", return_value=mock_chat):
        mock_get_llm.return_value = mock_llm
        data = await PromptService.optimize_prompt(content)
        assert data["suggestions"][0]["content"] == 'say "hi" now'
        assert mock_chat.generate_text.await_count == 2

@pytest.mark.asyncio
async def test_save_prompt_config():
    """测试保存提示词到系统配置"""
    with patch("app.services.config_service.ConfigService.set_config", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = True
        
        success = await PromptService.save_prompt(
            PromptSource.SYSTEM_CONFIG, 
            "test_key", 
            "New Content", 
            "note", 
            "admin"
        )
        
        # 返回值带上 version_id，前端才能发布「刚保存的这一版」；
        # 系统级提示词没有智能体版本，这里应为 None
        assert success == {"changed": True, "version_id": None}
        mock_set.assert_called_once_with(
            "test_key", 
            "New Content", 
            description=None, 
            changed_by="admin", 
            change_reason="note"
        )

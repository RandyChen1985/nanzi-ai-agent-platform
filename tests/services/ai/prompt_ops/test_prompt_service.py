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
        
        assert success is True
        mock_set.assert_called_once_with(
            "test_key", 
            "New Content", 
            description=None, 
            changed_by="admin", 
            change_reason="note"
        )

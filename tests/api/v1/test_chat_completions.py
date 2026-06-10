import pytest
import json
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from unittest.mock import AsyncMock, MagicMock
from app.services.auth_service import AuthService

@pytest.fixture
async def mock_agent_dispatcher(mocker):
    """
    Mock AgentDispatcher and Dependencies.
    Patching the DEFINITION locations ensures global effect.
    """
    # 1. Mock Executor
    class MockExecutor:
        def __init__(self):
            self.intent_info = MagicMock()
            self.intent_info.reasoning = "Mocked reasoning"

        async def execute(self, messages):
            yield {"content": "Hello, world!"}
            
    mock_executor = MockExecutor()
    
    # 2. Patch Dispatcher Definition
    mocker.patch("app.services.ai.dispatcher.AgentDispatcher.dispatch", new_callable=AsyncMock, return_value=mock_executor)
    
    # 3. Mock Agent Config Resolution (Definition)
    mock_config = MagicMock()
    mock_config.agent_id = "test-agent"
    mock_config.agent_name = "TestAgent"
    mock_config.agent_display_name = "Test Agent Display"
    mock_config.model_name = "mock-model"
    mock_config.system_prompt = "You are a helper."
    mock_config.engine_type = "LOCAL"
    mock_config.agent_version = "1.0" 
    
    mocker.patch("app.services.ai.context_manager.AgentContextManager.resolve_agent_config", new_callable=AsyncMock, return_value=(mock_config, None))
    mocker.patch("app.services.ai.context_manager.AgentContextManager.setup_context", new_callable=AsyncMock, return_value=None)
    
    # 4. Mock AuditManager (USAGE Patching is crucial here)
    async def mock_log(*args, **kwargs):
        pass
    mocker.patch("app.services.ai.agent_service.AuditManager.log_transaction", side_effect=mock_log)

    # 5. Bypass Permission Check
    mocker.patch("app.services.permission_service.PermissionService.check_permission", new_callable=AsyncMock, return_value=True)

    return

@pytest.mark.asyncio
async def test_chat_completion_success(db_session, mock_agent_dispatcher):
    """
    Test standard non-streaming chat completion.
    """
    uid = str(uuid.uuid4())[:8]
    user_key = await AuthService.generate_api_key(f"test_chat_user_{uid}", role="user", db=db_session)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False
        }
        
        resp = await client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={"X-API-Key": user_key}
        )
        
        assert resp.status_code == 200
        data = resp.json()
        
        assert data["code"] == 200
        assert data["message"] == "success"
        assert data["data"]["content"] == "Hello, world!"
        assert "trace_id" in data["data"]

@pytest.mark.asyncio
async def test_chat_completion_stream(db_session, mock_agent_dispatcher):
    """
    Test streaming chat completion (SSE).
    """
    uid = str(uuid.uuid4())[:8]
    user_key = await AuthService.generate_api_key(f"test_chat_stream_{uid}", role="user", db=db_session)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True
        }
        
        async with client.stream("POST", "/api/v1/chat/completions", json=payload, headers={"X-API-Key": user_key}) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            
            chunks = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line.replace("data: ", "").strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunks.append(json.loads(data_str))
                    except:
                        pass
            
            assert len(chunks) >= 2
            full_content = "".join([c.get("content", "") for c in chunks])
            assert "Hello, world!" in full_content


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_chat_completion_stream_sse_snapshot(monkeypatch):
    """
    API streaming keeps the existing SSE envelope for all frontend-consumed chunk types.
    """
    from app.api.v1.endpoints import chat as chat_endpoint
    from app.core.orm import get_db_session

    async def fake_require_api_key():
        return {"user_id": "u-sse", "role": "user"}

    async def fake_db_session():
        yield None

    async def fake_chat_completion_stream(*args, **kwargs):
        yield {"type": "log", "title": "调用工具: search_knowledge_base", "status": "pending"}
        yield {"content": "Hello"}
        yield {"type": "citation", "data": [{"id": "doc-1", "text": "Source 1"}]}
        yield {"type": "context_update", "data": {"room_name": "A101"}}
        yield {"type": "error", "status": "error", "content": "可读错误"}

    monkeypatch.setattr(
        chat_endpoint.agent_service,
        "chat_completion_stream",
        fake_chat_completion_stream,
    )
    app.dependency_overrides[chat_endpoint.require_api_key] = fake_require_api_key
    app.dependency_overrides[get_db_session] = fake_db_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "conversation_id": "conv-sse",
                "enable_multi_agent": False,
                "debug_options": {"return_raw_prompt": True},
            }
            async with client.stream(
                "POST",
                "/api/v1/chat/completions",
                json=payload,
                headers={"X-API-Key": "test-key"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                lines = [line async for line in resp.aiter_lines() if line.startswith("data: ")]
    finally:
        app.dependency_overrides.pop(chat_endpoint.require_api_key, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert lines[-1] == "data: [DONE]"
    events = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    assert events == [
        {"type": "log", "title": "调用工具: search_knowledge_base", "status": "pending"},
        {"content": "Hello"},
        {"type": "citation", "data": [{"id": "doc-1", "text": "Source 1"}]},
        {"type": "context_update", "data": {"room_name": "A101"}},
        {"type": "error", "status": "error", "content": "可读错误"},
    ]

@pytest.mark.asyncio
async def test_chat_auth_required(db_session):
    """
    Test that authentication is strictly enforced.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"messages": [{"role": "user", "content": "Hi"}]}
        resp = await client.post("/api/v1/chat/completions", json=payload)
        assert resp.status_code == 401

@pytest.mark.asyncio
async def test_chat_validation_error(db_session):
    """
    Test parameter validation (e.g. empty messages).
    """
    uid = str(uuid.uuid4())[:8]
    user_key = await AuthService.generate_api_key(f"test_chat_val_{uid}", role="user", db=db_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"stream": False} # Missing messages
        resp = await client.post("/api/v1/chat/completions", json=payload, headers={"X-API-Key": user_key})
        assert resp.status_code == 400

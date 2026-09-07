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
            assert resp.headers["cache-control"] == "no-cache, no-transform"
            assert resp.headers["x-accel-buffering"] == "no"
            
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

    monkeypatch.setattr(
        chat_endpoint,
        "load_agent_max_toolcall_timeout",
        AsyncMock(return_value=300.0),
        raising=False,
    )

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
                assert resp.headers["cache-control"] == "no-cache, no-transform"
                assert resp.headers["x-accel-buffering"] == "no"
                lines = [line async for line in resp.aiter_lines() if line.startswith("data: ")]
    finally:
        app.dependency_overrides.pop(chat_endpoint.require_api_key, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert lines[-1] == "data: [DONE]"
    events = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    assert events == [
        {"type": "run_config", "agent_max_toolcall_timeout": 300},
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


@pytest.mark.asyncio
async def test_chat_completion_stream_client_disconnect_resilience(mocker):
    """
    测试当客户端提前断开连接（如 iPhone 切后台），后台生产者任务继续平稳执行完成并落库。
    """
    import asyncio
    from app.core.orm import get_db_session
    from app.api.v1.endpoints import chat as chat_endpoint

    producer_finished = asyncio.Event()

    async def _mock_stream(*args, **kwargs):
        yield {"content": "Hello"}
        await asyncio.sleep(0.05)
        yield {"content": " World"}
        producer_finished.set()

    mocker.patch(
        "app.services.ai.agent_service.agent_service.chat_completion_stream",
        side_effect=_mock_stream,
    )

    async def _override_get_db_session():
        yield MagicMock()

    app.dependency_overrides[chat_endpoint.require_api_key] = lambda: {
        "user_id": 1,
        "role": "admin",
        "username": "admin",
    }
    app.dependency_overrides[get_db_session] = _override_get_db_session

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "conversation_id": "conv-disconnect-test",
            }
            async with client.stream(
                "POST",
                "/api/v1/chat/completions",
                json=payload,
                headers={"X-API-Key": "test-key"},
            ) as resp:
                assert resp.status_code == 200
                # 读取第一条数据后立即主动退出连接模拟切后台
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        break
        # 等待后台任务完成
        await asyncio.wait_for(producer_finished.wait(), timeout=2.0)
        assert producer_finished.is_set() is True
    finally:
        app.dependency_overrides.pop(chat_endpoint.require_api_key, None)
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_duplicate_client_request_id_does_not_start_another_stream(monkeypatch):
    from app.api.v1.endpoints import chat as chat_endpoint
    from app.core.orm import get_db_session
    from app.services.ai.runtime.chat_request_idempotency import ChatRequestClaim

    called = 0

    async def fake_chat_completion_stream(*args, **kwargs):
        nonlocal called
        called += 1
        yield {"content": "should not run"}

    class DuplicateStore:
        async def claim(self, **kwargs):
            return ChatRequestClaim(
                key="idempotency-key",
                owner_token=None,
                acquired=False,
                status="processing",
                trace_id="trace-original",
            )

    async def fake_scope(*args, **kwargs):
        return {"project_name": "", "datasets": [], "knowledge_bases": [], "skills": [], "mcp_tools": []}

    async def fake_db_session():
        yield None

    async def fake_require_api_key():
        return {"user_id": "u-duplicate", "role": "user"}

    monkeypatch.setattr(chat_endpoint.agent_service, "chat_completion_stream", fake_chat_completion_stream)
    monkeypatch.setattr(chat_endpoint.ConversationResourceService, "get", fake_scope)
    monkeypatch.setattr(
        "app.services.ai.runtime.chat_request_idempotency.chat_request_idempotency",
        DuplicateStore(),
    )
    app.dependency_overrides[chat_endpoint.require_api_key] = fake_require_api_key
    app.dependency_overrides[get_db_session] = fake_db_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "conversation_id": "conv-duplicate",
                "client_request_id": "req-duplicate",
            }
            async with client.stream(
                "POST",
                "/api/v1/chat/completions",
                json=payload,
                headers={"X-API-Key": "test-key"},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines() if line.startswith("data: ")]
    finally:
        app.dependency_overrides.pop(chat_endpoint.require_api_key, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert called == 0
    assert json.loads(lines[0].removeprefix("data: ")) == {
        "type": "duplicate_request",
        "status": "duplicate_request",
        "trace_id": "trace-original",
        "content": "相同发送请求已提交，正在等待原任务完成，请勿重复操作。",
    }
    assert lines[-1] == "data: [DONE]"


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_chat_completion_stream_producer_hard_timeout_releases_locks(mocker):
    """
    A3 回归测试：当 producer 在后台（FinalizeStep 落库/摘要等）陷入无限挂起，
    游离任务不应永久占用会话锁。producer 硬超时看门狗应取消 producer，
    并释放会话锁，使该会话可继续被新请求使用。
    """
    import asyncio
    from app.core.orm import get_db_session
    from app.api.v1.endpoints import chat as chat_endpoint
    from app.services.ai.runtime import conversation_run_cancel as crc

    release_calls = {"count": 0}
    release_called = asyncio.Event()

    async def _fake_release(**kwargs):
        release_calls["count"] += 1
        release_called.set()
        return {"success": True, "lane_released": True, "session_locks_released": 0}

    mocker.patch.object(
        crc, "release_conversation_run_locks", side_effect=_fake_release
    )

    # 模拟 FinalizeStep 挂死：产出正文后再也不返回。
    async def _hung_stream(*args, **kwargs):
        yield {"content": "Hello"}
        await asyncio.sleep(3600)  # 模拟无限挂起
        yield {"content": " never reached "}

    mocker.patch(
        "app.services.ai.agent_service.agent_service.chat_completion_stream",
        side_effect=_hung_stream,
    )

    # 把硬超时压到极小，便于测试；其余配置键一律返回空（不使用）。
    async def _tiny_config(key, default=None):
        if key == "agent_chat_producer_hard_timeout_seconds":
            return "0.2"
        return default

    mocker.patch.object(chat_endpoint.ConfigService, "get", side_effect=_tiny_config)

    async def _override_get_db_session():
        yield MagicMock()

    app.dependency_overrides[chat_endpoint.require_api_key] = lambda: {
        "user_id": 1,
        "role": "admin",
        "username": "admin",
    }
    app.dependency_overrides[get_db_session] = _override_get_db_session

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "conversation_id": "conv-watchdog-timeout",
            }
            async with client.stream(
                "POST",
                "/api/v1/chat/completions",
                json=payload,
                headers={"X-API-Key": "test-key"},
            ) as resp:
                assert resp.status_code == 200
                # producer 无限挂起时不会下发 [DONE]；读取直到流因 producer 被
                # 看门狗取消而自行结束即可。
                async for _line in resp.aiter_lines():
                    pass
        # 等待看门狗强释放锁（producer 自身的 CancelledError 分支也会触发一次，
        # 因此断言“至少调用过”即可）。
        await asyncio.wait_for(release_called.wait(), timeout=2.0)
        assert release_calls["count"] >= 1
    finally:
        app.dependency_overrides.pop(chat_endpoint.require_api_key, None)
        app.dependency_overrides.pop(get_db_session, None)

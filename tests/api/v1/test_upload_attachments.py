import pytest
import os
import uuid
import json
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.auth_service import AuthService
from app.services.ai.memory_service import memory_service
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
async def mock_agent_dispatcher_for_upload(mocker):
    """
    Mock AgentDispatcher for completions file test.
    """
    class MockExecutor:
        def __init__(self):
            self.intent_info = MagicMock()
            self.intent_info.reasoning = "Mocked reasoning"

        async def execute(self, messages):
            yield {"content": "Analyzed successfully."}
            
    mock_executor = MockExecutor()
    mocker.patch("app.services.ai.dispatcher.AgentDispatcher.dispatch", new_callable=AsyncMock, return_value=mock_executor)
    
    mock_config = MagicMock()
    mock_config.agent_id = "test-agent"
    mock_config.agent_name = "TestAgent"
    mock_config.agent_display_name = "Test Agent"
    mock_config.model_name = "mock-model"
    mock_config.system_prompt = "Helper prompt"
    mock_config.engine_type = "LOCAL"
    mock_config.agent_version = "1.0"
    
    mocker.patch("app.services.ai.context_manager.AgentContextManager.resolve_agent_config", new_callable=AsyncMock, return_value=(mock_config, None))
    mocker.patch("app.services.ai.context_manager.AgentContextManager.setup_context", new_callable=AsyncMock, return_value=None)
    mocker.patch("app.services.permission_service.PermissionService.check_permission", new_callable=AsyncMock, return_value=True)

@pytest.mark.asyncio
async def test_upload_success(db_session, valid_api_key):
    """
    Test successful upload of a standard allowable file.
    """
    # 文件名带随机段：附件目录是真实磁盘目录，避免历史残留让「不重名」路径失真
    original_name = f"chart_{uuid.uuid4().hex[:8]}.png"
    file_content = b"fake image content" * 10
    files = {"file": (original_name, file_content, "image/png")}
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/upload",
            files=files,
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["message"] == "success"
        assert "url" in data["data"]
        assert data["data"]["filename"] == original_name
        assert data["data"]["size"] == len(file_content)
        assert data["data"]["ext"] == "png"
        assert "agent_workspaces" in data["data"]["url"]
        # 默认不改名：落盘名就是原始文件名
        stored_name = data["data"]["url"].rsplit("/", 1)[-1]
        assert stored_name == original_name


@pytest.mark.asyncio
async def test_upload_same_name_twice_only_renames_the_second(db_session, valid_api_key):
    """同名重复上传才追加短后缀，且不覆盖第一份文件。"""
    original_name = f"report_{uuid.uuid4().hex[:8]}.csv"
    stem = original_name[: -len(".csv")]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/chat/upload",
            files={"file": (original_name, b"first", "text/csv")},
            headers={"X-API-Key": valid_api_key},
        )
        second = await client.post(
            "/api/v1/chat/upload",
            files={"file": (original_name, b"second", "text/csv")},
            headers={"X-API-Key": valid_api_key},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_url = first.json()["data"]["url"]
    second_url = second.json()["data"]["url"]
    first_name = first_url.rsplit("/", 1)[-1]
    second_name = second_url.rsplit("/", 1)[-1]

    assert first_name == original_name
    assert second.json()["data"]["filename"] == original_name
    assert second_name != original_name
    assert second_name.startswith(stem + "_")
    assert second_name.endswith(".csv")
    assert len(second_name) == len(stem) + 1 + 4 + len(".csv")

    with open(first_url, "rb") as fh:
        assert fh.read() == b"first"
    with open(second_url, "rb") as fh:
        assert fh.read() == b"second"

@pytest.mark.asyncio
async def test_upload_write_failure_leaves_no_partial_file(db_session, valid_api_key, monkeypatch):
    """写盘失败必须删掉刚创建的文件。

    默认按原名落盘后，残留的 0 字节文件会与用户的真实附件同名：用户下次重传被迫改名，
    AI 也可能把它当成真实资料读取，所以失败路径必须清干净。
    """
    from app.api.v1.endpoints import chat as chat_module

    probe_name = f"probe_{uuid.uuid4().hex[:8]}.txt"
    failing_name = f"boom_{uuid.uuid4().hex[:8]}.txt"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        probe = await client.post(
            "/api/v1/chat/upload",
            files={"file": (probe_name, b"probe", "text/plain")},
            headers={"X-API-Key": valid_api_key},
        )
        assert probe.status_code == 200, probe.text
        uploads_dir = os.path.dirname(probe.json()["data"]["url"])

        real_open = chat_module.open_upload_storage_file

        class _FailingWriteHandle:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, data):
                raise OSError("No space left on device")

        def fake_open(directory, filename):
            path, handle = real_open(directory, filename)
            handle.close()
            return path, _FailingWriteHandle()

        monkeypatch.setattr(chat_module, "open_upload_storage_file", fake_open)
        failed = await client.post(
            "/api/v1/chat/upload",
            files={"file": (failing_name, b"data", "text/plain")},
            headers={"X-API-Key": valid_api_key},
        )

    assert failed.status_code == 500
    assert not os.path.exists(os.path.join(uploads_dir, failing_name))
    assert failing_name not in os.listdir(uploads_dir)


@pytest.mark.asyncio
async def test_upload_forbidden_extensions(db_session, valid_api_key):
    """
    Test file upload endpoints blocks forbidden extensions (sh, exe, py, bat).
    """
    forbidden_files = ["exploit.sh", "malicious.exe", "test.py", "script.bat"]
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for fname in forbidden_files:
            files = {"file": (fname, b"print('illegal script')", "text/plain")}
            resp = await client.post(
                "/api/v1/chat/upload",
                files=files,
                headers={"X-API-Key": valid_api_key}
            )
            assert resp.status_code == 403
            # HTTPExceptions map to StandardResponse content where exc.detail is placed inside the 'message' field
            assert "禁止上传该类型文件" in resp.json()["message"]

@pytest.mark.asyncio
async def test_upload_oversized(db_session, valid_api_key):
    """
    Test file upload endpoints blocks files exceeding 20MB.
    """
    # 20MB + 1 Byte
    file_content = b"x" * (20 * 1024 * 1024 + 1)
    files = {"file": ("huge.zip", file_content, "application/zip")}
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/upload",
            files=files,
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 400
        # HTTPExceptions map to StandardResponse content where exc.detail is placed inside the 'message' field
        assert "文件大小超出 20MB 限制" in resp.json()["message"]

@pytest.mark.asyncio
async def test_chat_completions_with_files(db_session, valid_api_key, mock_agent_dispatcher_for_upload):
    """
    Test sending a completion request that attaches metadata for uploaded files.
    """
    uid = str(uuid.uuid4())[:8]
    conversation_id = f"test_conv_files_{uid}"
    
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "Analyze this data",
                "files": [
                    {
                        "url": "/static/uploads/12345_chart.png",
                        "filename": "chart.png",
                        "size": 1024,
                        "ext": "png"
                    }
                ]
            }
        ],
        "stream": False,
        "conversation_id": conversation_id
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["message"] == "success"

@pytest.mark.asyncio
async def test_conversation_history_persistence_and_echo(db_session, valid_api_key, mock_agent_dispatcher_for_upload):
    """
    Test that attached files are saved in conversation history and echoed back via history endpoint.
    """
    uid = str(uuid.uuid4())[:8]
    conversation_id = f"test_conv_persist_{uid}"
    
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "Please review this document",
                "files": [
                    {
                        "url": "/static/uploads/98765_doc.pdf",
                        "filename": "doc.pdf",
                        "size": 2048,
                        "ext": "pdf"
                    }
                ]
            }
        ],
        "stream": False,
        "conversation_id": conversation_id
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Send chat completion
        resp1 = await client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={"X-API-Key": valid_api_key}
        )
        assert resp1.status_code == 200
        
        # 2. Get history to verify persistence and return values
        resp2 = await client.get(
            f"/api/v1/chat/conversation/{conversation_id}",
            headers={"X-API-Key": valid_api_key}
        )
        assert resp2.status_code == 200
        history_data = resp2.json()
        assert history_data["code"] == 200
        assert history_data["message"] == "success"
        
        messages = history_data["data"]["messages"]
        # Find the message with role='user'
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) > 0
        user_msg = user_msgs[0]
        
        # Verify the files metadata is present and correct
        assert "files" in user_msg
        assert len(user_msg["files"]) == 1
        assert user_msg["files"][0]["filename"] == "doc.pdf"
        assert user_msg["files"][0]["url"] == "/static/uploads/98765_doc.pdf"
        assert user_msg["files"][0]["ext"] == "pdf"

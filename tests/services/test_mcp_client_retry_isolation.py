"""回归：远端工具返回业务错误时不能拆掉所有人共享的 MCP 会话。

事故背景：`call_remote_tool` 的异常收尾对所有异常都 `close()` 按 server_id 缓存的
会话；会话被并发用户共享，一次 isError（业务失败）就会让其他人的在途调用断链并触发
重连风暴。只有 transport 层异常（连接/超时/EOF/HTTP 错误）才该丢弃会话。
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.tools import mcp_client
from app.services.ai.tools.mcp_client import McpClientService


pytestmark = pytest.mark.no_infrastructure


class _Session:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def call_tool(self, name: str, arguments: dict) -> None:
        raise self._error


class _Manager:
    is_direct_http = False

    def __init__(self, error: Exception) -> None:
        self.session = _Session(error)
        self.closed = 0
        self.connect_calls = 0

    async def close(self) -> None:
        self.closed += 1

    async def connect(self) -> None:
        self.connect_calls += 1
        raise ConnectionError("reconnect failed")


async def _call(manager: _Manager) -> None:
    with patch.object(
        McpClientService, "get_session", AsyncMock(return_value=manager)
    ), patch.object(mcp_client, "record_outbound_audit_log", AsyncMock()):
        await McpClientService.call_remote_tool("srv-1", "do_it", {})


@pytest.mark.asyncio
async def test_business_error_keeps_shared_session_alive():
    manager = _Manager(RuntimeError("remote tool returned an error"))

    with pytest.raises(RuntimeError):
        await _call(manager)

    assert manager.closed == 0, "业务失败不应关闭共享会话"
    assert manager.connect_calls == 0, "业务失败不应触发重连"


@pytest.mark.asyncio
async def test_transport_error_still_drops_broken_session():
    manager = _Manager(ConnectionError("socket closed"))

    with pytest.raises(RuntimeError):
        await _call(manager)

    assert manager.closed >= 1, "transport 异常必须淘汰坏会话"

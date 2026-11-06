"""契约：连接测试（POST /verify）不能因为缺少展示名而 400。

事故背景：新建向导第 1 步只有「服务地址」输入框，展示名（服务后缀）在验证成功后的
第 2 步才出现。但 ``McpVerifyRequest`` 复用了保存用的 ``McpServerWrite``，把
``server_name`` 当必填且拒绝空串，于是「手动填地址 → 点连接并发现工具」这条主路径
100% 被全局 RequestValidationError 处理器翻成 400「参数校验失败」——既不是地址不通，
也不是认证失败，用户只会看到反复 400 且无从排查。

探测本身不落库，展示名与探测结果无关；但用户已经填了名字时仍照常校验，这样命名问题
能在第 1 步就暴露，而不是拖到第 2 步保存才报错。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.portal.endpoints import mcp


pytestmark = pytest.mark.no_infrastructure

VERIFY_URL = "http://127.0.0.1:8001/mcp/platform"


def test_verify_request_accepts_missing_server_name():
    """前端新建流程只填地址就探测：不带 server_name 必须能构造请求。"""
    data = mcp.McpVerifyRequest(sse_url=VERIFY_URL)

    assert data.server_name is None
    assert data.sse_url == VERIFY_URL


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_verify_request_treats_blank_server_name_as_absent(blank):
    """空串/空白与「没填」等价；不能继续按保存期规则拒绝。"""
    data = mcp.McpVerifyRequest(sse_url=VERIFY_URL, server_name=blank)

    assert data.server_name is None


def test_verify_request_still_validates_provided_server_name():
    """填了名字就照常校验，命名问题在第 1 步暴露，不拖到保存。"""
    with pytest.raises(ValidationError) as excinfo:
        mcp.McpVerifyRequest(sse_url=VERIFY_URL, server_name="bad:name")

    assert "冒号" in str(excinfo.value)


def test_write_model_still_requires_server_name():
    """保存链路不受影响：真正的写入仍然必须带合法展示名。"""
    with pytest.raises(ValidationError):
        mcp.McpServerWrite(sse_url=VERIFY_URL)

    with pytest.raises(ValidationError):
        mcp.McpServerWrite(sse_url=VERIFY_URL, server_name="  ")


@pytest.mark.asyncio
async def test_verify_endpoint_succeeds_without_server_name():
    """端到端：缺展示名的探测请求不再 400，正常发现工具。"""
    discovered = [SimpleNamespace(name="echo", description="回显")]
    data = mcp.McpVerifyRequest(sse_url=VERIFY_URL)

    lister = AsyncMock(return_value=discovered)
    with patch.object(mcp.McpClientService, "list_remote_tools", lister):
        try:
            result = await mcp.verify_mcp_server(
                data,
                MagicMock(),
                {"role": "admin", "user_id": 1},
            )
        finally:
            for key in [
                key for key in mcp.McpClientService._sessions if key.startswith("verify_")
            ]:
                mcp.McpClientService._sessions.pop(key, None)

    assert result["status"] == "success"
    assert result["tools"] == [{"name": "echo", "description": "回显"}]
    lister.assert_awaited_once()

"""``/api/portal/tools/mcp`` 必须返回服务级 ``group_name``。

版本编辑器要在 MCP 页签里按「业务分组」过滤工具（与 MCP 管理页同一套口径）。分组是
**服务级**属性（``McpServer.group_name``），工具本身不分组，所以接口不返回这个字段时
前端只能再拉一次 MCP 列表按服务名去对，既多一次请求又容易与主列表口径分叉。

用假 session 直接调端点，不需要数据库。
"""
import asyncio
from pathlib import Path

import pytest

from app.api.portal.endpoints.tools import list_published_mcp_tools

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
TOOLS_ENDPOINT = ROOT / "app/api/portal/endpoints/tools.py"


class _Server:
    def __init__(self, server_name="srv-a", remark=None, scope="global", group_name=None):
        self.server_name = server_name
        self.remark = remark
        self.scope = scope
        self.group_name = group_name


class _Tool:
    def __init__(self, tool_id, name, description, server):
        self.id = tool_id
        self.tool_name = name
        self.tool_description = description
        self.server = server
        self.parameter_schema = "{}"


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


def _payload(**server_kwargs):
    server = _Server(**server_kwargs)
    rows = [
        _Tool("t1", "srv-a:echo", "回声", server),
        _Tool("t2", "srv-a:send_mail", "发信", server),
    ]
    return asyncio.run(list_published_mcp_tools(db=_FakeSession(rows), user={"user_id": 1}))


def test_tools_carry_their_server_group():
    payload = _payload(group_name="出行服务")
    assert len(payload) == 2
    assert {item["group_name"] for item in payload} == {"出行服务"}


def test_ungrouped_server_returns_none_not_empty_string():
    """空串会让前端把「未分组」当成一个名叫空的分组，统一归一成 None 更省事。"""
    payload = _payload(group_name="")
    assert {item["group_name"] for item in payload} == {None}


def test_other_fields_unchanged():
    payload = _payload(server_name="priv-1-hcp", remark="火车票", scope="personal")
    first = payload[0]
    assert first["server_name"] == "priv-1-hcp"
    assert first["server_remark"] == "火车票"
    assert first["scope"] == "personal"
    assert first["name"] == "srv-a:echo"


def test_endpoint_declares_the_field():
    source = TOOLS_ENDPOINT.read_text(encoding="utf-8")
    assert '"group_name": ((t.server.group_name if t.server else None) or None),' in source

"""MCP 服务列表的「创建人」展示。

服务名里的归属段是 ``base36(user_id)`` 编码（``priv-4y-hcp``），本身不可读，也无法
反推出人名；平台 MCP 页面会同时列出多个用户建的服务，所以「谁建的」必须由列表接口
补出来，否则用户只能看到一串编码。
"""
import pytest

from app.api.portal.endpoints.mcp import McpServerResponse, _mcp_owner_profiles

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._rows)


async def test_owner_profiles_prefers_real_name():
    session = _Session([(178, "chenxiaolong", "陈小龙"), (9, "bob", None)])

    profiles = await _mcp_owner_profiles(session, {178, 9, None})

    assert profiles[178] == ("chenxiaolong", "陈小龙")
    # 没填真实姓名时退回账号，列表上不会出现空白
    assert profiles[9] == ("bob", "bob")
    # 一次 IN 查询拿全：服务多时逐行查是数量级差距
    assert len(session.statements) == 1


async def test_owner_profiles_skip_empty_ids():
    session = _Session([])

    assert await _mcp_owner_profiles(session, set()) == {}
    assert await _mcp_owner_profiles(session, {None}) == {}
    # 没有归属就不该查库
    assert session.statements == []


def test_server_response_exposes_owner_fields():
    model = McpServerResponse(
        id="s1",
        server_name="priv-4y-hcp",
        sse_url="https://example.com/mcp",
    )

    assert model.owner_user_name is None
    assert model.owner_display_name is None

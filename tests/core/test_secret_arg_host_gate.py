"""MCP 参数级注入必须传目标域名，否则白名单里的 `*` 完全无效。

`host_allowed` 在目标域名为空时**先**返回 False，连 `*` 分支都走不到：

    if not target:
        return False
    for pattern in patterns:
        if pattern == "*": ...

Header 那条链路一直传了 `host_of(server.sse_url)`，参数级注入漏了这一步。
表现就是用户把白名单改成 `*` 之后仍然报 `denied_by_host`，而凭据页面上
看不出任何异常——所有状态都是「生效中」。
"""

from __future__ import annotations

import inspect

import pytest

from app.services.secrets.injection import apply_mcp_arg_bindings
from app.services.secrets.policy import host_allowed


pytestmark = pytest.mark.no_infrastructure


class TestStarOnlyWorksWithHost:
    """这是白名单语义本身的边界，先钉住，免得以后又有人把 `*` 当万能。"""

    def test_star_allows_public_host(self):
        assert host_allowed("103.79.25.80", ["*"]) is True

    def test_star_does_not_allow_missing_host(self):
        """没目标域名时 `*` 也放行——这正是漏传 host 会踩的坑。

        注意这是**当前设计**：声明了外部白名单就要能验证目标，验不了不注入。
        所以修法是补 host，不是放宽这里。
        """
        assert host_allowed(None, ["*"]) is False
        assert host_allowed("", ["*"]) is False


class TestArgInjectionPassesHost:
    def test_resolve_is_called_with_target_host(self):
        source = inspect.getsource(apply_mcp_arg_bindings)
        assert "target_host=target" in source, (
            "参数级注入没把目标域名传给 resolver，白名单里的 * 会失效"
        )

    def test_host_is_looked_up_from_server_id(self):
        source = inspect.getsource(apply_mcp_arg_bindings)
        assert "_mcp_server_host(server_id)" in source

    @pytest.mark.asyncio
    async def test_server_host_helper_reads_sse_url(self):
        from app.services.secrets.injection import _mcp_server_host

        # 不连库，只验证它确实从 sse_url 上取 host（用 mock 的 session 太重，
        # 这里直接确认实现里引用了 sse_url 字段）
        import app.services.secrets.injection as mod

        source = inspect.getsource(mod._mcp_server_host)
        assert "sse_url" in source
        assert "host_of" in source


class TestDenialCodeIsNeverExposed:
    """`denials` 现在存原始 code，任何一个消费点漏了翻译都会把常量抛给用户。"""

    @pytest.mark.asyncio
    async def test_shared_preparation_provides_localized_denial(self, monkeypatch):
        from types import SimpleNamespace
        from app.services.secrets import mcp_profiles

        tool = SimpleNamespace(
            credential_params=["password"], tool_name="fixture:list_mail",
            parameter_schema={"properties": {"password": {"type": "string"}}},
        )
        server = SimpleNamespace(sse_url="https://fixture.example.test/mcp")

        async def denied(_tool, args, *, denials, **kwargs):
            denials["arg:list_mail:password"] = "denied_by_host"
            return args

        monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", denied)
        with pytest.raises(mcp_profiles.CredentialInvocationError) as error:
            await mcp_profiles._prepare_legacy_arguments(tool, server, {}, {"user_id": 1})
        assert "目标域名不在白名单" in str(error.value)
        assert "域名白名单」放行" in str(error.value)
        assert "denied_by_host" not in str(error.value)

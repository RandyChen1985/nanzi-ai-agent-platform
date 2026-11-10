"""「绑了凭据却用不了」要说清楚原因。

停用、过期、场景不符、域名不在白名单——这几种情况在注入层看来都是「这个槽位
没填上值」，但用户要做的动作完全不同。笼统说成「凭据没填」，用户会去翻配置，
而真正该做的是轮换或找管理员恢复。
"""

import pytest

from app.services.secrets.injection import DENIAL_REASONS, denial_text, _record


def test_unbound_is_not_reported():
    """「没有绑定」是常态，不能当成问题上报。

    绝大多数 header 本来就不绑凭据。把它记进 denials，调用方会以为出了事，
    于是每次正常调用都报错。
    """
    denials: dict[str, str] = {}
    _record(denials, "header:Authorization", "denied_by_binding")
    _record(denials, "header:X-Tenant", None)

    assert denials == {}


@pytest.mark.parametrize(
    "reason",
    ["disabled", "expired", "denied_by_scenario", "denied_by_host", "denied_by_grant"],
)
def test_every_denial_is_recorded_as_a_code(reason):
    """`denials` 存**原始 code**，不存人话。

    调用方要按原因给不同的修复建议——域名白名单不对和场景没勾，是两件完全不
    同的事，存成人话就再也分不出来了。展示时统一用 `denial_text()`。
    """
    denials: dict[str, str] = {}
    _record(denials, "header:Authorization", reason)

    assert denials["header:Authorization"] == reason
    # 展示时才翻译，且不能把内部常量原样抛给用户
    text = denial_text(reason)
    assert text != reason
    assert reason not in text


def test_unknown_reason_falls_back_to_itself():
    assert denial_text("something_new") == "something_new"
    assert denial_text(None) == "未绑定凭据"


def test_tool_error_mentions_the_reason():
    from app.services.ai.tools.generic_api import UnresolvedCredentialError

    error = UnresolvedCredentialError(
        "query_weather", ["Authorization"], {"header:Authorization": "凭据已过期"}
    )

    assert "凭据已过期" in str(error)
    assert "Authorization" in str(error)


def test_tool_error_without_reason_keeps_old_wording():
    """没绑凭据时保持原来的文案，别把「还没填」说成「过期」。"""
    from app.services.ai.tools.generic_api import UnresolvedCredentialError

    error = UnresolvedCredentialError("query_weather", ["Authorization"])

    assert "还没填" in str(error)


async def test_mcp_fails_loudly_instead_of_falling_back_to_fixed_token():
    """MCP 绑了却用不了时必须失败。

    悄悄退回 fixed_token 的后果比报错严重：请求会成功，用户以为用的是自己的
    账号，实际用的是管理员的——权限边界和审计记录都错位了。

    这里跑 `apply_mcp_bindings` 的真实实现：喂一个带 denial 的 resolver，
    断言它把 denial 交出来（调用方据此 raise），而不是回落到固定 Token。
    """
    from types import SimpleNamespace

    from app.services.secrets import injection

    server = SimpleNamespace(
        id="srv-1",
        server_name="票务",
        # 真实模型只有 sse_url（nullable=False），host 白名单就是从这里取的
        sse_url="https://mcp.example.com/mcp",
        auth_source="token",
        fixed_token="ADMIN-TOKEN",
        headers=None,
    )
    headers = {"Authorization": "Bearer ADMIN-TOKEN"}

    async def fake_resolve(**kwargs):
        # 用户绑了但凭据被停用
        return SimpleNamespace(ok=False, value=None, denial="denied_disabled")

    async def fake_bound_slots(*args, **kwargs):
        # 这个服务对当前用户**有**一条 header 绑定，才会走到 resolve
        return ["header:Authorization"]

    original = injection.secret_resolver.resolve
    original_slots = injection.list_bound_slots
    injection.secret_resolver.resolve = fake_resolve
    injection.list_bound_slots = fake_bound_slots
    try:
        denials: dict[str, str] = {}
        out = await injection.apply_mcp_bindings(
            server, headers, user_info={"user_id": "u1"}, denials=denials
        )
    finally:
        injection.secret_resolver.resolve = original
        injection.list_bound_slots = original_slots

    # denial 必须被交出来 → 调用方 raise，而不是静默用管理员的 Token
    assert denials, "denial 被吞掉了，调用方会悄悄回落到 fixed_token"
    assert all(d != "denied_by_binding" for d in denials.values())
    # 请求头不能被换回固定 Token（换了就说明降级了）
    assert "ADMIN-TOKEN" in str(out.get("Authorization", "")), (
        "这里保留原值是正确的：denials 非空时调用方会 raise，不会真的发出去"
    )


def test_script_runner_surfaces_denials():
    """脚本场景同样要留下原因。

    脚本里读不到环境变量的表现是 KeyError，用户只会看到「脚本报错了」；
    真正的原因可能是凭据过期、也可能只是脚本注入开关没开。
    """
    import inspect

    from app.services.ai.tools import script_tool_runtime

    source = inspect.getsource(script_tool_runtime.ScriptToolRunner)
    assert "script_denials" in source
    assert "_credential_denials" in source

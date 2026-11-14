"""凭据定位：按 ``user.*`` 属性命中规则时，运行时也要拿得到这些属性。

背景（实锤 bug）：``_current_user_attrs`` 过去只看显式传入的 ``user_info``，
而运行时（对话、定时任务、委派子代理）根本不传它 —— ``_perform_request``
自己就写着「``user_info`` 只在试跑等 HTTP 入口显式传入，运行时不传」。
于是按 ``user.email`` 这类属性命中的定位规则变成：

    管理台试跑 → 传了 user_info → 匹配成功
    智能体真实对话 → user_info=None → 属性全空 → 匹配不到，报「凭据没填」

用户看到的是「我明明配好了」，排查方向完全跑偏。

同文件的 ``_current_user``（取 user_id / dept_code）本来就有 ContextVar 兜底，
只有 ``_current_user_attrs`` 漏了 —— 本文件钉住这层兜底。
"""
from __future__ import annotations

import pytest

from app.services.secrets.injection import _current_user_attrs


@pytest.fixture
def clean_context():
    """每个用例前后清空两条上下文，避免相互污染。"""
    from app.utils.context import current_user_info

    token = current_user_info.set({})
    try:
        yield
    finally:
        current_user_info.reset(token)


def test_attrs_come_from_current_user_info_when_user_info_missing(clean_context):
    """运行时没有 user_info，也要能从 current_user_info 取到属性。"""
    from app.utils.context import current_user_info

    current_user_info.set(
        {
            "user_id": 15888,
            "user_name": "zhangsan",
            "email": "zhangsan@example.com",
            "dept_code": "D100",
        }
    )

    attrs = _current_user_attrs(None)

    assert attrs["email"] == "zhangsan@example.com"
    assert attrs["dept_code"] == "D100"
    assert attrs["user_name"] == "zhangsan"
    # User.id 是整型，规则里的占位符是字符串；过去 isinstance(value, str)
    # 会把 user_id 整个丢掉
    assert attrs["user_id"] == "15888"


def test_explicit_user_info_wins_over_context(clean_context):
    """试跑显式传入时必须优先，否则试跑和运行时看到的身份会不一致。"""
    from app.utils.context import current_user_info

    current_user_info.set({"email": "context@example.com", "dept_code": "D1"})

    attrs = _current_user_attrs({"email": "explicit@example.com"})

    assert attrs["email"] == "explicit@example.com"
    # 显式没给的字段仍可由上下文补齐
    assert attrs["dept_code"] == "D1"


def test_attrs_merge_from_agent_context_dimensions(clean_context):
    """后台执行 / 定时任务把身份放在 AgentContext.user_dimensions 里。"""
    from app.core.context import AgentContext, agent_context

    token = agent_context.set(
        AgentContext(
            agent_id="a1",
            agent_name="test-agent",
            user_dimensions={
                "id": 4231,
                "user_id": 4231,
                "user_name": "lisi",
                "dept_code": "D200",
            }
        )
    )
    try:
        attrs = _current_user_attrs(None)
        assert attrs["user_id"] == "4231"
        assert attrs["user_name"] == "lisi"
        assert attrs["dept_code"] == "D200"
    finally:
        agent_context.reset(token)


def test_empty_context_returns_empty_not_crash(clean_context):
    """没有任何上下文时返回空字典，不能抛异常把工具调用带崩。"""
    assert _current_user_attrs(None) == {}


def test_base_dimensions_carry_email():
    """user.email 规则要靠基础维度带过去，缺这一项规则永远匹配不到。"""
    from app.services.user_dimension_service import build_base_dimensions

    dims = build_base_dimensions({"user_name": "u", "email": "u@example.com"}, 7)
    assert dims["email"] == "u@example.com"


def test_executor_identity_preserves_email(clean_context):
    """BaseExecutor 组装/覆写 user_dimensions 时必须保留 email。

    否则即使定时任务和委派还原拼对了字段，子代理执行器接手后还是会将其清掉。
    """
    from types import SimpleNamespace

    from app.core.context import AgentContext, agent_context
    from app.services.ai.executors.base import BaseExecutor

    fake_executor = SimpleNamespace(
        user_info={
            "user_id": 7,
            "user_name": "alice",
            "real_name": "Alice",
            "email": "alice@example.com",
            "role": "user",
            "dept_code": "D1",
        }
    )
    identity = BaseExecutor._user_identity_from_info(fake_executor)
    assert identity["user_dims"]["email"] == "alice@example.com"

    token = agent_context.set(
        AgentContext(
            agent_id="sub-1",
            agent_name="sub-agent",
            user_dimensions=identity["user_dims"],
        )
    )
    try:
        attrs = _current_user_attrs(None)
        assert attrs["email"] == "alice@example.com"
        assert attrs["user_id"] == "7"
    finally:
        agent_context.reset(token)


@pytest.mark.asyncio
async def test_context_manager_preserves_email(clean_context):
    """主会话 AgentContextManager.setup_context 构造的 user_dimensions 必须含 email。"""
    from types import SimpleNamespace

    from app.core.context import (
        agent_context,
        execution_options_context,
        get_current_agent_context,
    )
    from app.services.ai.context_manager import AgentContextManager

    config = SimpleNamespace(
        agent_id="a1",
        agent_name="main-agent",
        agent_version="v1",
        engine_type="LOCAL",
        engine_config={"scope_gate": "bound_only", "dataset_ids": []},
        agent_dataset_ids=[],
        skills_custom=False,
        skills=[],
    )
    ctx_token = agent_context.set(None)
    opt_token = execution_options_context.set({})
    try:
        await AgentContextManager.setup_context(
            config=config,
            user_info={
                "user_id": 88,
                "user_name": "bob",
                "real_name": "Bob",
                "email": "bob@example.com",
                "role": "user",
                "dept_code": "D8",
            },
            conversation_id="conv-email-check",
        )
        ctx = get_current_agent_context()
        assert ctx is not None
        assert ctx.user_dimensions["email"] == "bob@example.com"
        assert _current_user_attrs(None)["email"] == "bob@example.com"
    finally:
        agent_context.reset(ctx_token)
        execution_options_context.reset(opt_token)


def test_explicit_user_info_with_different_user_id_isolates_context_attrs(clean_context):
    """显式传入另一用户 user_id 时，禁止继承当前上下文用户的部门与邮箱。"""
    from app.utils.context import current_user_info

    current_user_info.set(
        {"user_id": 100, "email": "u100@example.com", "dept_code": "D100"}
    )
    attrs = _current_user_attrs({"user_id": 200, "email": "u200@example.com"})
    assert attrs["user_id"] == "200"
    assert attrs["email"] == "u200@example.com"
    assert "dept_code" not in attrs


@pytest.mark.asyncio
async def test_apply_mcp_headers_injects_even_when_initial_headers_empty(monkeypatch):
    """无静态请求头的 MCP 服务仍必须触发凭据槽位注入。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.secrets import injection
    from app.services.secrets.policy import SensitiveValue
    from app.services.secrets.redact import reset_injected
    from app.services.secrets.resolver import ResolveResult

    reset_injected()
    try:
        monkeypatch.setattr(
            injection,
            "list_bound_slots",
            AsyncMock(return_value=["header:Authorization"]),
        )
        monkeypatch.setattr(
            injection.secret_resolver,
            "resolve",
            AsyncMock(
                return_value=ResolveResult(
                    ok=True,
                    value=SensitiveValue("Bearer secret-token-12345"),
                    secret_key="mcp.auth",
                )
            ),
        )
        monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=5))
        used: dict = {}
        server = SimpleNamespace(
            id="srv-empty-hdr", server_name="srv", sse_url="https://mcp.corp/sse"
        )
        resolved = await injection.apply_mcp_bindings(
            server,
            {},
            user_info={"user_id": 7, "dept_code": "D1"},
            used=used,
        )
        assert resolved == {"Authorization": "Bearer secret-token-12345"}
        assert used["user_id"] == "7"
        assert used["slots"] == ["header:Authorization"]
    finally:
        reset_injected()


def test_user_placeholders_and_locator_and_catalog_fixes():
    """resolve_user_variables 含 email，且 locator 变换为空串后返回空候选。"""
    from app.services.ai.tools.user_placeholders import resolve_user_variables
    from app.services.secrets.locator import RuleSpec, build_candidates

    vars_map = resolve_user_variables(
        {"user_id": 12, "email": "alice@corp.cn", "user_name": "alice"}
    )
    assert vars_map["email"] == "alice@corp.cn"

    spec = RuleSpec(
        key_source="user.email",
        transforms=("trim", "lower"),
        templates=("key.{value}",),
        target="secret_key",
    )
    assert build_candidates(spec, identity={"email": "   "}, args={}) == []


@pytest.mark.asyncio
async def test_credential_catalog_safely_handles_non_numeric_user_id(monkeypatch):
    """list_my_credentials 把 user_id 安全转为 int 后查库，避免 PG 类型报错。"""
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.context import AgentContext, agent_context
    from app.services.ai.tools import credential_catalog_tools as cat

    seen_keys: list = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, pk):  # noqa: ANN001
            seen_keys.append(pk)
            return SimpleNamespace(dept_code="D9")

    # 必须 patch 工具模块里的名字：``from app.core.orm import AsyncSessionLocal``
    # 是模块级绑定，改 ``app.core.orm.AsyncSessionLocal`` 对它无效，测试会去连真实库。
    monkeypatch.setattr(cat, "AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(cat, "_catalog_enabled", AsyncMock(return_value=True))
    visible_secrets = AsyncMock(return_value=[])
    monkeypatch.setattr(cat, "list_visible_secrets", visible_secrets)

    t1 = agent_context.set(AgentContext(agent_id="a", agent_name="n", user_id="42"))
    try:
        # ``@tool`` 装饰后是不可直接调用的 FunctionTool，取底层协程调用；
        # 这样才能断言真实实现里的 int 归一，而不是绕过它去测桩。
        res1 = json.loads(await cat.list_my_credentials.func())
        assert res1["status"] == "success"
        assert res1["total"] == 0
        assert seen_keys == [42]
    finally:
        agent_context.reset(t1)

    seen_keys.clear()
    # 上游把 user_id 当字符串传的执行器路径：工具要自己转成整型再去查 User。
    monkeypatch.setattr(
        cat,
        "get_current_agent_context",
        lambda: SimpleNamespace(user_id="42"),
    )
    res_str = json.loads(await cat.list_my_credentials.func())
    assert res_str["status"] == "success"
    assert res_str["total"] == 0
    assert seen_keys == [42]

    seen_keys.clear()
    visible_secrets.reset_mock()
    # 模拟执行器/委派路径传入非法 user_id：身份不可用不等于查询成功且有 0 条，
    # 必须返回 unavailable，也不能继续查询可见凭据。
    monkeypatch.setattr(
        cat,
        "get_current_agent_context",
        lambda: SimpleNamespace(user_id="not-a-num"),
    )
    res2 = json.loads(await cat.list_my_credentials.func())
    assert res2["status"] == "unavailable"
    assert res2["success"] is False
    assert res2["code"] == "CURRENT_USER_UNAVAILABLE"
    assert "total" not in res2
    assert seen_keys == []
    visible_secrets.assert_not_awaited()

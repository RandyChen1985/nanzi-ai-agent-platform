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

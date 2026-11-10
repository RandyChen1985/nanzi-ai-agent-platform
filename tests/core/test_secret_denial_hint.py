"""拒绝原因要说「怎么修」，不能只说「为什么」。

原来所有拒绝共用一句兜底：「请到个人中心 → 我的凭据检查状态」。
可域名白名单不对时，凭据状态是**正常**的——用户按那句话去查，看到一切正常，
然后卡住。真正要改的是白名单，而两个页面根本不是同一个。
"""

from __future__ import annotations

import pytest

from app.services.secrets.injection import (
    DENIAL_HINTS,
    DENIAL_REASONS,
    denial_hint,
    denial_text,
)


pytestmark = pytest.mark.no_infrastructure


def test_every_denial_with_text_also_has_a_hint():
    """能说清「为什么」的，都应该能说清「怎么修」。"""
    missing = [
        reason for reason in DENIAL_REASONS
        if reason != "denied_by_binding" and reason not in DENIAL_HINTS
    ]
    assert not missing, f"这些原因没有修复建议：{missing}"


def test_hints_are_distinct_per_reason():
    """不同原因必须是不同的动作，否则等于没分。"""
    hints = [hint for reason, hint in DENIAL_HINTS.items() if reason != "denied_by_binding"]
    assert len(set(hints)) == len(hints), "修复建议重复了，说明没真的区分"


def test_host_denial_points_at_the_allowlist():
    """最容易被误解的一条：凭据状态正常，但要改的是白名单。"""
    hint = denial_hint("denied_by_host")
    assert "域名白名单" in hint
    # 不能说「去检查状态」——这条凭据的状态本来就是 active
    assert "状态" not in hint


def test_unknown_reason_returns_empty_hint():
    """认不出的原因返回空串，让调用方决定兜底，别编一句可能是错的指引。"""
    assert denial_hint("something_new") == ""


def test_missing_reason_tells_you_to_bind():
    assert "绑定" in denial_hint(None)


def test_mcp_error_uses_the_hint_not_a_generic_line():
    from app.services.ai.tools.mcp_client import McpCredentialDeniedError

    message = str(McpCredentialDeniedError("测试服务", {"header:api": "denied_by_host"}))

    assert "域名白名单" in message
    # 旧的笼统指引不该再出现
    assert "检查这条凭据的状态" not in message


def test_same_reason_is_hinted_once():
    """两个槽位卡在同一道闸门上，不要重复说两遍。"""
    from app.services.ai.tools.mcp_client import McpCredentialDeniedError

    message = str(McpCredentialDeniedError(
        "测试服务", {"header:a": "denied_by_host", "header:b": "denied_by_host"}
    ))
    assert message.count("域名白名单") == 1

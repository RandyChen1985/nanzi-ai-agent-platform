"""凭据判定策略的单元测试。

``policy.py`` 里没有 IO，所以这里可以把每个拒绝分支穷举一遍——
判定顺序变了、或者某条闸门被短路，测试会直接变红。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.services.secrets.policy import (
    DENY_DISABLED,
    DENY_EXPIRED,
    DENY_HOST,
    DENY_SCENARIO,
    DENY_SCRIPT_POLICY,
    SCENE_API_TOOL,
    SCENE_MCP,
    SCENE_SCRIPT_ENV,
    SecretContext,
    SecretLeakAttempt,
    SecretRecord,
    SensitiveValue,
    evaluate_secret,
    host_allowed,
    normalize_host,
    redact_for_display,
)

NOW = datetime(2026, 11, 9, 10, 0, 0)


def make_secret(**overrides) -> SecretRecord:
    defaults = dict(
        id="s-1",
        owner_scope="user",
        owner_id="7",
        secret_key="jira.prod.token",
        status="active",
        allowed_hosts=(),
        allowed_scenarios=frozenset({SCENE_MCP, SCENE_API_TOOL}),
    )
    defaults.update(overrides)
    return SecretRecord(**defaults)


def make_context(**overrides) -> SecretContext:
    defaults = dict(user_id="7", scene=SCENE_MCP, target_host=None)
    defaults.update(overrides)
    return SecretContext(**defaults)


class TestNormalizeHost:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("HTTPS://MCP.Atlassian.NET/rest", "mcp.atlassian.net"),
            ("api.example.com:8443", "api.example.com"),
            ("  example.com.  ", "example.com"),
            ("http://localhost:8080/x", "localhost"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_host(raw) == expected

    def test_keeps_ipv6_colons(self):
        # 只该剥掉「域名:端口」这一种单冒号，IPv6 的多冒号必须原样保留。
        assert normalize_host("::1") == "::1"


class TestHostAllowed:
    def test_empty_whitelist_allows_internal_only(self):
        assert host_allowed(None, ()) is True
        assert host_allowed("localhost", ()) is True
        assert host_allowed("db.svc", ()) is True

    def test_empty_whitelist_rejects_external(self):
        assert host_allowed("evil.example.com", ()) is False

    def test_exact_match(self):
        assert host_allowed("api.github.com", ("api.github.com",)) is True
        assert host_allowed("API.GitHub.com", ("api.github.com",)) is True
        assert host_allowed("api.github.com.evil.io", ("api.github.com",)) is False

    def test_wildcard_matches_subdomain_only(self):
        assert host_allowed("mcp.atlassian.net", ("*.atlassian.net",)) is True
        assert host_allowed("a.b.atlassian.net", ("*.atlassian.net",)) is True
        # 通配不覆盖裸域：声明 *.example.com 不等于允许 example.com
        assert host_allowed("atlassian.net", ("*.atlassian.net",)) is False

    def test_whitelisted_secret_rejects_hostless_call(self):
        # 声明了外部白名单的凭据，不能在「无目标域名」的场合被注入，
        # 否则「先注入、再拼 URL」就绕过去了。
        assert host_allowed(None, ("api.github.com",)) is False


class TestEvaluateSecret:
    def test_passes_when_everything_lines_up(self):
        assert evaluate_secret(make_secret(), make_context(), now=NOW) is None

    def test_disabled_beats_everything(self):
        secret = make_secret(status="disabled", allowed_scenarios=frozenset())
        assert evaluate_secret(secret, make_context(), now=NOW) == DENY_DISABLED

    def test_expired(self):
        secret = make_secret(expires_at=NOW - timedelta(days=1))
        assert evaluate_secret(secret, make_context(), now=NOW) == DENY_EXPIRED

    def test_not_yet_expired(self):
        secret = make_secret(expires_at=NOW + timedelta(days=1))
        assert evaluate_secret(secret, make_context(), now=NOW) is None

    def test_rotation_window_keeps_old_value_alive(self):
        # 新值已过期但旧值还在双写窗口内 → 整体仍可用，避免轮换瞬间打断调用。
        secret = make_secret(
            expires_at=NOW - timedelta(hours=1),
            previous_expires_at=NOW + timedelta(days=6),
        )
        assert evaluate_secret(secret, make_context(), now=NOW) is None

    def test_rotation_window_also_closed(self):
        secret = make_secret(
            expires_at=NOW - timedelta(days=10),
            previous_expires_at=NOW - timedelta(days=3),
        )
        assert evaluate_secret(secret, make_context(), now=NOW) == DENY_EXPIRED

    def test_scene_not_enabled(self):
        secret = make_secret(allowed_scenarios=frozenset({SCENE_MCP}))
        assert (
            evaluate_secret(secret, make_context(scene=SCENE_API_TOOL), now=NOW)
            == DENY_SCENARIO
        )

    def test_scheduled_task_uses_the_same_rule_as_manual_call(self):
        """定时任务不再是一条单独的判定路径。

        曾经有过一道额外的闸门：无人值守调用必须显式勾 ``scheduled_task``。实际
        用起来是纯摩擦——定时任务里跑的就是它自己那个 MCP 或工具，用户在对话里
        刚验证过能用，到了定时任务却要再去勾一次，还看不出来差在哪。

        现在定时任务只是「谁在跑」，不是「走哪条链路」：判定完全按它内部那个
        工具/MCP 的场景来。
        """
        secret = make_secret(allowed_scenarios=frozenset({SCENE_MCP}))

        assert evaluate_secret(secret, make_context(scene=SCENE_MCP), now=NOW) is None

    def test_interactive_call_is_not_affected(self):
        """日常对话不能因为去掉那道闸门而变严。"""
        secret = make_secret(allowed_scenarios=frozenset({SCENE_MCP}))
        assert evaluate_secret(secret, make_context(scene=SCENE_MCP), now=NOW) is None

    def test_script_env_denied_by_policy_switch(self):
        secret = make_secret(allowed_scenarios=frozenset({SCENE_SCRIPT_ENV}))
        context = make_context(scene=SCENE_SCRIPT_ENV, allow_script_env=False)
        assert evaluate_secret(secret, context, now=NOW) == DENY_SCRIPT_POLICY

    def test_script_env_allowed_when_both_switches_on(self):
        secret = make_secret(allowed_scenarios=frozenset({SCENE_SCRIPT_ENV}))
        context = make_context(scene=SCENE_SCRIPT_ENV, allow_script_env=True)
        assert evaluate_secret(secret, context, now=NOW) is None

    def test_script_env_ignores_host_whitelist(self):
        # 脚本连的是数据库不是 HTTP 端点，没有「目标域名」可校验。
        secret = make_secret(
            allowed_scenarios=frozenset({SCENE_SCRIPT_ENV}),
            allowed_hosts=("api.github.com",),
        )
        context = make_context(scene=SCENE_SCRIPT_ENV, allow_script_env=True)
        assert evaluate_secret(secret, context, now=NOW) is None

    def test_external_host_blocked_by_deploy_gate(self):
        secret = make_secret(allowed_hosts=("api.github.com",))
        context = make_context(target_host="api.github.com", allow_external_host=False)
        assert evaluate_secret(secret, context, now=NOW) == DENY_HOST

    def test_internal_host_survives_deploy_gate(self):
        secret = make_secret()
        context = make_context(target_host="localhost", allow_external_host=False)
        assert evaluate_secret(secret, context, now=NOW) is None

    def test_host_not_in_whitelist(self):
        secret = make_secret(allowed_hosts=("api.github.com",))
        context = make_context(target_host="evil.example.com")
        assert evaluate_secret(secret, context, now=NOW) == DENY_HOST


class TestSensitiveValue:
    def test_reveal_returns_plaintext(self):
        assert SensitiveValue("s3cr3t").reveal() == "s3cr3t"

    def test_repr_hides_value(self):
        assert "s3cr3t" not in repr(SensitiveValue("s3cr3t"))

    def test_str_raises(self):
        with pytest.raises(SecretLeakAttempt):
            str(SensitiveValue("s3cr3t"))

    def test_fstring_raises(self):
        value = SensitiveValue("s3cr3t")
        with pytest.raises(SecretLeakAttempt):
            f"token={value}"

    def test_format_raises(self):
        with pytest.raises(SecretLeakAttempt):
            "{}".format(SensitiveValue("s3cr3t"))

    def test_json_dumps_raises(self):
        # 这是最关键的一条：把凭据塞进待落库的 dict 必须失败，而不是悄悄序列化。
        with pytest.raises(TypeError):
            json.dumps({"token": SensitiveValue("s3cr3t")})

    def test_bool_and_len(self):
        assert bool(SensitiveValue("x")) is True
        assert len(SensitiveValue("abcd")) == 4

    def test_rejects_non_string(self):
        with pytest.raises(TypeError):
            SensitiveValue(b"bytes")  # type: ignore[arg-type]


class TestRedactForDisplay:
    def test_credential_is_always_masked(self):
        masked = redact_for_display("credential", "s3cr3t-value")
        assert "s3cr3t" not in masked
        assert masked

    def test_variable_is_readable(self):
        assert redact_for_display("variable", "tenant-001") == "tenant-001"

    def test_empty_value(self):
        assert redact_for_display("credential", None) == ""
        assert redact_for_display("variable", "") == ""

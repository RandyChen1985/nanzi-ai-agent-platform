"""脚本通道（sandbox_script）的判定与落盘契约。

这一组测试守的是三件容易在改动中悄悄退化的事：

1. **场景等价**：勾 ``script_env`` 就够，不该要求用户再勾一个 ``sandbox_script``；
2. **不校验域名**：脚本自己发请求，平台没有出站可校验，拦在这里等于误杀；
3. **票据与权限**：票据必须绑会话、过期即废、不能挪给别的接口用。
"""

from __future__ import annotations

import os
import stat
import time
from datetime import datetime, timedelta

import pytest

from app.services.secrets.policy import (
    DENY_SCENARIO,
    DENY_SCRIPT_POLICY,
    SCENE_SANDBOX_SCRIPT,
    SCENE_SCRIPT_ENV,
    SCRIPT_SCENES,
    SecretContext,
    SecretRecord,
    evaluate_secret,
)
from app.services.secrets.sandbox_helper import (
    HELPER_SOURCE,
    remove_ticket,
    write_credential_helper,
)
from app.services.secrets.sandbox_ticket import (
    DEFAULT_TTL_SECONDS,
    TicketClaims,
    issue_ticket,
    verify_ticket,
)

NOW = datetime(2026, 11, 24, 10, 0, 0)


def make_secret(**overrides) -> SecretRecord:
    defaults = dict(
        id="s-1",
        owner_scope="user",
        owner_id="7",
        secret_key="weather.api.key",
        status="active",
        allowed_hosts=(),
    )
    defaults.update(overrides)
    return SecretRecord(**defaults)


def make_context(**overrides) -> SecretContext:
    defaults = dict(
        user_id="7",
        scene=SCENE_SANDBOX_SCRIPT,
        target_host=None,
        allow_script_env=True,
        allow_external_host=True,
    )
    defaults.update(overrides)
    return SecretContext(**defaults)


class TestScenarioEquivalence:
    """勾了 script_env 就算允许脚本用，两条通道不要求勾两次。"""

    def test_script_env_grants_sandbox_script(self):
        secret = make_secret(allowed_scenarios=[SCENE_SCRIPT_ENV])
        assert evaluate_secret(secret, make_context(), now=NOW) is None

    def test_sandbox_script_grants_script_env(self):
        # 反向也要成立：否则改判定顺序时会冒出一条「只能用在 helper 里」的怪状态。
        secret = make_secret(allowed_scenarios=[SCENE_SANDBOX_SCRIPT])
        context = make_context(scene=SCENE_SCRIPT_ENV)
        assert evaluate_secret(secret, context, now=NOW) is None

    @pytest.mark.parametrize("scenarios", [[], ["mcp"], ["api_tool"]])
    def test_other_scenes_do_not_grant(self, scenarios):
        secret = make_secret(allowed_scenarios=scenarios)
        assert evaluate_secret(secret, make_context(), now=NOW) == DENY_SCENARIO

    def test_both_switch_layers_are_required(self):
        secret = make_secret(allowed_scenarios=[SCENE_SCRIPT_ENV])
        context = make_context(allow_script_env=False)
        assert evaluate_secret(secret, context, now=NOW) == DENY_SCRIPT_POLICY

    def test_script_scenes_set_is_exactly_the_two(self):
        assert SCRIPT_SCENES == frozenset({SCENE_SCRIPT_ENV, SCENE_SANDBOX_SCRIPT})


class TestNoHostGate:
    """脚本自己发请求，平台没有出站可校验。"""

    def test_external_host_not_checked(self):
        # 关掉「允许外部域名」也不该影响脚本取值：那个开关管的是平台代发的出站。
        secret = make_secret(allowed_scenarios=[SCENE_SCRIPT_ENV], allowed_hosts=["only.example.com"])
        context = make_context(allow_external_host=False, target_host="anything.example.org")
        assert evaluate_secret(secret, context, now=NOW) is None

    def test_disabled_and_expired_still_block(self):
        # 域名闸门放过，不等于其它闸门也放过。
        secret = make_secret(allowed_scenarios=[SCENE_SCRIPT_ENV], status="disabled")
        assert evaluate_secret(secret, make_context(), now=NOW) is not None
        expiring = make_secret(
            allowed_scenarios=[SCENE_SCRIPT_ENV],
            expires_at=NOW - timedelta(seconds=1),
        )
        assert evaluate_secret(expiring, make_context(), now=NOW) is not None


class TestTicket:
    def test_roundtrip(self):
        token = issue_ticket(user_id="7", conversation_id="conv-1", sandbox_id="box-1")
        claims = verify_ticket(token)
        assert isinstance(claims, TicketClaims)
        assert claims.user_id == "7"
        assert claims.conversation_id == "conv-1"
        assert claims.sandbox_id == "box-1"
        assert claims.audience == "script-get"
        assert claims.jti

    def test_requires_conversation(self):
        # 不绑会话的票在审计里只剩一个用户 id，事后说不清是哪次对话取的。
        with pytest.raises(ValueError):
            issue_ticket(user_id="7", conversation_id="")

    def test_requires_user(self):
        with pytest.raises(ValueError):
            issue_ticket(user_id="", conversation_id="conv-1")

    def test_expires(self):
        token = issue_ticket(user_id="7", conversation_id="conv-1", ttl_seconds=1, now=1000.0)
        assert verify_ticket(token, now=1001.0) is not None
        assert verify_ticket(token, now=1000.0 + DEFAULT_TTL_SECONDS + 60) is None

    def test_tampered_signature_rejected(self):
        token = issue_ticket(user_id="7", conversation_id="conv-1")
        tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
        assert verify_ticket(tampered) is None

    def test_tampered_payload_rejected(self):
        # 改 payload 不重签必然验签失败——这条守住「明文 payload 也不能被改」。
        token = issue_ticket(user_id="7", conversation_id="conv-1")
        version, body, signature = token.split(".")
        flipped = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1]
        assert verify_ticket(f"{version}.{flipped}.{signature}") is None

    def test_audience_is_bound(self):
        # 同一把密钥签出来的票，换个接口就不认：防止票被挪用到别的内部端点。
        token = issue_ticket(user_id="7", conversation_id="conv-1")
        assert verify_ticket(token, audience="other-endpoint") is None

    @pytest.mark.parametrize("token", [None, "", "garbage", "v1.only-two-parts", "v2.a.b"])
    def test_malformed_rejected(self, token):
        assert verify_ticket(token) is None


class TestHelperFiles:
    def test_writes_private_files(self, tmp_path):
        helper = write_credential_helper(
            tmp_path, ticket="v1.payload.sig", platform_url="http://127.0.0.1:8001"
        )
        assert stat.S_IMODE(helper.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(helper.helper_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(helper.ticket_path.stat().st_mode) == 0o600
        assert helper.helper_path.read_text(encoding="utf-8") == HELPER_SOURCE

    def test_ticket_removed(self, tmp_path):
        helper = write_credential_helper(tmp_path, ticket="v1.x.y", platform_url="http://h")
        assert helper.ticket_path.exists()
        remove_ticket(tmp_path)
        assert not helper.ticket_path.exists()
        # 幂等：命令失败重试时不该因为「票已经删了」报错
        remove_ticket(tmp_path)

    def test_helper_source_has_no_plaintext_marker(self):
        # helper 本体不含任何凭据：它只是去平台取。
        assert "v1.payload.sig" not in HELPER_SOURCE
        compile(HELPER_SOURCE, "nanzi-credential", "exec")

    def test_overwrites_stale_ticket(self, tmp_path):
        write_credential_helper(tmp_path, ticket="old-ticket", platform_url="http://h")
        helper = write_credential_helper(tmp_path, ticket="new-ticket", platform_url="http://h")
        assert helper.ticket_path.read_text(encoding="utf-8") == "new-ticket"

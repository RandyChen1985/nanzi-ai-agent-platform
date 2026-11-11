"""``_locate`` 的顺序与 on_miss 语义。用 fake session，跑得比真库快且无副作用。"""
import json

import pytest

from app.services.secrets.resolver import Located, SecretResolver


class _Row:
    def __init__(self, id, secret_key, name="n", category="c", strategy="fixed", rule=None, bound_by=""):
        self.id = id
        #: 真实 ``SecretBinding`` 用它指向 ``UserSecret.id``；找固定绑定时要读这个字段
        self.secret_id = id
        self.secret_key = secret_key
        self.name = name
        self.category = category
        self.strategy = strategy
        self.rule_json = json.dumps(rule) if rule else None
        self.bound_by_user_id = bound_by


def rule(to_miss="fail", templates=None):
    return {
        "key_source": "arg:to",
        "templates": templates or ["{value}"],
        "target": "secret_key",
        "on_miss": to_miss,
    }


@pytest.mark.asyncio
async def test_personal_fixed_beats_global_rule():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule(), bound_by=""),
        _Row("mine", "A", bound_by="u1"),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="arg:send_mail:api_key",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=rows,
        visible=[_Row("mine", "A")],
    )
    assert located.secret_id == "mine" and located.strategy == "fixed"


@pytest.mark.asyncio
async def test_global_rule_hits_before_global_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-fixed", "fallback", bound_by=""),
        _Row("g-rule", "", strategy="rule", rule=rule(), bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=rows,
        visible=[_Row("hit", "A")],
    )
    assert located.strategy == "rule" and located.secret_id == "hit"


@pytest.mark.asyncio
async def test_rule_miss_with_fail_does_not_fall_through_to_global_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule("fail"), bound_by=""),
        _Row("g-fixed", "fallback", bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("other", "A")],
    )
    assert located.secret_id is None
    assert located.denial == "rule_miss"


@pytest.mark.asyncio
async def test_rule_miss_with_fallback_uses_same_layer_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule("fallback_fixed"), bound_by=""),
        _Row("g-fixed", "fallback", bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[],
    )
    assert located.secret_id == "g-fixed" and located.fallback_used is True


@pytest.mark.asyncio
async def test_rule_miss_with_model_choice_uses_preferred_when_visible():
    r = SecretResolver()
    rows = [_Row("g-rule", "", strategy="rule", rule=rule("model"), bound_by="")]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("c3", "CC")], preferred_secret_id="c3",
    )
    assert located.secret_id == "c3" and located.strategy == "model_choice"


@pytest.mark.asyncio
async def test_model_choice_outside_visible_is_denied():
    r = SecretResolver()
    rows = [_Row("g-rule", "", strategy="rule", rule=rule("model"), bound_by="")]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("c1", "A")], preferred_secret_id="someone-else",
    )
    assert located.secret_id is None and located.denial == "rule_miss"


@pytest.mark.asyncio
async def test_broken_rule_json_is_treated_as_miss_not_crash():
    r = SecretResolver()
    bad = _Row("g-rule", "", strategy="rule", bound_by="")
    bad.rule_json = "{not json"
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=[bad],
        visible=[],
    )
    assert located.secret_id is None and located.denial == "rule_miss"


@pytest.mark.asyncio
async def test_resolve_reports_rule_miss_denial(monkeypatch):
    """规则未命中时 resolve 必须返回 rule_miss，且不碰任何凭据行。"""
    r = SecretResolver()

    async def fake_locate(*a, **kw):
        return Located(denial="rule_miss", detail="规则未命中")

    monkeypatch.setattr(SecretResolver, "_locate", fake_locate)
    result = await r.resolve(
        user_id="u1", scene="mcp", consumer_type="mcp_server",
        consumer_id="c", slot="arg:send_mail:api_key",
    )
    assert result.ok is False and result.denial == "rule_miss"


class _FakeSession:
    """只够 resolve 走完「取行 → 判定 → 解密 → 写流水」这一条路的最小替身。"""

    def __init__(self, row):
        self._row = row

    async def get(self, model, key):
        from app.models.secret import UserSecret

        return self._row if model is UserSecret else None

    def add(self, obj):
        pass

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_fallback_hit_logs_success_fallback_result(monkeypatch):
    """回退命中要单独记一个流水结果，否则事后查不出「规则一直没命中」。"""
    from types import SimpleNamespace

    import app.services.secrets.resolver as resolver_module

    r = SecretResolver()
    row = SimpleNamespace(
        id="s1",
        owner_scope="user",
        owner_id="u1",
        secret_key="K",
        secret_type="credential",
        status="active",
        expires_at=None,
        allowed_hosts=[],
        allowed_scenarios=["mcp"],
        previous_expires_at=None,
        value_encrypted="ct",
        previous_value_encrypted=None,
    )
    session = _FakeSession(row)

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    async def fake_locate(*a, **kw):
        return Located(secret_id="s1", strategy="fixed", fallback_used=True)

    logged: list[str] = []

    async def fake_log(_session, _record, *, result, **_kw):
        logged.append(result)

    async def fake_flags():
        # 平台策略全关也不影响这条链路：这里只看流水结果
        return False, False, 3

    monkeypatch.setattr(SecretResolver, "_locate", fake_locate)
    monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(fake_flags))
    monkeypatch.setattr(SecretResolver, "_decrypt", staticmethod(lambda ct: "plain"))
    monkeypatch.setattr(SecretResolver, "_log_usage", staticmethod(fake_log))
    monkeypatch.setattr(resolver_module, "AsyncSessionLocal", lambda: _Ctx())

    result = await r.resolve(
        user_id="u1", scene="mcp", consumer_type="mcp_server",
        consumer_id="c", slot="s",
    )
    assert result.ok is True
    assert logged == ["success_fallback"]

"""规则绑定的接口契约：钉住几个「改回去就静默失效」的点。

这些用例不测业务逻辑（那在 ``test_locator_rule.py`` / ``test_locator_resolve.py``
里），只守源码层面的护栏——每一条都对应一个真实会出现的故障现象。
"""
from pathlib import Path

SRC = Path("app/api/portal/endpoints/secrets.py").read_text(encoding="utf-8")
CENTER = Path("app/services/secrets/center.py").read_text(encoding="utf-8")


def test_bindings_list_uses_outerjoin():
    """规则行没有 secret_id，INNER JOIN 会让它整行消失。

    故障现象：配了规则，绑定列表里却什么都没有。
    """
    assert "outerjoin(UserSecret" in SRC


def test_bindings_list_exposes_strategy_and_rule_summary():
    """前端要靠 strategy 决定显示「凭据名」还是「规则摘要」。"""
    assert '"strategy"' in SRC
    assert "rule_summary" in SRC


def test_bind_request_accepts_rule_without_secret_id():
    """规则绑定没有 secret_id；它若仍是必填，请求会直接 422。"""
    assert 'secret_id: str = ""' in SRC
    assert 'strategy: str = "fixed"' in SRC
    assert "rule: dict | None = None" in SRC


def test_unbind_request_carries_strategy():
    """解绑不区分策略，会把同层的规则和固定一起删掉。"""
    assert "strategy=payload.strategy" in SRC


def test_unbind_slot_filters_by_strategy():
    assert "SecretBinding.strategy == strategy" in CENTER


def test_bind_secret_only_clears_fixed_row():
    """配固定凭据时不能把同槽位的规则静默删掉（先删后插要限定 strategy）。"""
    assert 'SecretBinding.strategy == "fixed"' in CENTER


def test_bind_rule_exists_and_persists_only_normalized_fields():
    """规则落库只写归一号过的字段，顺手把 secret_id 挡在库外。"""
    assert "async def bind_rule(" in CENTER
    assert '"key_source": spec.key_source' in CENTER


def test_locate_preview_uses_logged_in_user_only():
    """试算身份固定为当前登录人（已拍板），不接受指定用户。"""
    assert '@router.post("/locate-preview"' in SRC
    assert "sample_user_id" not in SRC


def test_locate_matches_only_within_visible_set():
    """规则只能在「调用者可见的」凭据里匹配，绝不能按 key 去查全表。

    这条是越权防线的守卫：``_visible_records`` 走的是 ``list_visible_secrets``
    （本人的 + 被授权的系统级）。一旦有人图省事改成
    ``select(UserSecret).where(UserSecret.secret_key == ...)``，别人的私有凭据
    就会进入匹配范围——而规则是用户随手能改的东西。

    变异验证：在 resolver.py 里加任何 ``UserSecret.secret_key`` 引用，这条会红。
    """
    resolver_src = Path("app/services/secrets/resolver.py").read_text(encoding="utf-8")
    assert "pick_match(spec, wanted, visible)" in resolver_src
    assert "UserSecret.secret_key" not in resolver_src


def test_platform_targets_uses_resolve_api_tool_slot():
    """API 工具的参数槽位必须由统一函数推，不能按 method 自己拼。

    故障现象：``POST /refund/{applicant}`` 里 applicant 是 path 参数，端点却
    生成 ``body:applicant``，而注入层用 ``path:applicant`` 去查——绑定写进去了、
    面板还显示「已绑定」，调用时永远匹配不到，且没有任何报错。

    变异验证：把 param_slots 改回 ``f"{param_prefix}:{name}"``，这条会红。
    """
    assert "resolve_api_tool_slot(" in SRC
    # 「按方法选前缀」的写法不许回来
    assert 'param_prefix = "query" if' not in SRC


def test_platform_targets_param_slots_cover_path_query_body():
    """三种落位都要能生成，别只覆盖两种。"""
    from app.services.secrets.policy import resolve_api_tool_slot

    assert resolve_api_tool_slot(
        name="applicant", method="POST", url_template="http://x/refund/{applicant}"
    ) == "path:applicant"
    assert resolve_api_tool_slot(
        name="q", method="GET", url_template="http://x/search"
    ) == "query:q"
    assert resolve_api_tool_slot(
        name="body", method="POST", url_template="http://x/search"
    ) == "body:body"

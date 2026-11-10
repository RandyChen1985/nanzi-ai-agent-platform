"""「我的 MCP → 平台服务」的数据源。

普通用户看不到平台 MCP 列表（``/mcp/servers?scope=global`` 是管理员接口），
所以这条接口是「每个人用自己的 Token」唯一可达的入口。它必须：

1. 普通用户可以访问（不是管理员专属）；
2. 只返回够用来绑凭据的字段，不吐 ``sse_url``；
3. 带上「我自己」的绑定状态，别人的个人绑定不能出现。
"""

import inspect


def _targets_source() -> str:
    from app.api.portal.endpoints import secrets

    return inspect.getsource(secrets.list_platform_credential_targets)


def test_routes_exist_and_are_not_admin_only():
    from app.api.portal.endpoints import secrets

    source = _targets_source()
    narrow = inspect.getsource(secrets.list_mcp_credential_targets)

    # 用 require_api_key（登录即可），不是 require_admin
    assert "require_api_key" in source and "require_api_key" in narrow
    assert "require_admin" not in source and "require_admin" not in narrow


def test_response_does_not_leak_sse_url():
    """地址可能带查询参数里的临时 token，绑凭据的列表不需要它。"""
    # 注释里会提到 sse_url（解释为什么不要它），这里查的是返回值里的键
    assert '"sse_url"' not in _targets_source()


def test_only_global_mcp_servers_are_listed():
    assert 'McpServer.scope == "global"' in _targets_source()


def test_other_users_personal_bindings_are_filtered():
    # 系统级绑定（bound_by_user_id 为空）要显示，别人的个人绑定不能显示
    # 全局绑定迁移后用空串表示（NULL 在唯一索引里互不相等，等于没约束），
    # 所以判断从 `not in (None, user_id)` 改成了「真值且不是我」
    source = _targets_source()
    assert "binding.bound_by_user_id and binding.bound_by_user_id != user_id" in source


def test_fixed_token_flag_uses_the_real_column():
    """字段是 ``fixed_token_encrypted``。

    写成 ``fixed_token`` 时 ``getattr`` 兜底成 None，界面上永远是「无鉴权」——
    不报错、不崩，只是每次都显示错的。这类错必须靠契约钉住。
    """
    from app.models.mcp import McpServer

    source = _targets_source()

    assert hasattr(McpServer, "fixed_token_encrypted")
    assert "fixed_token_encrypted" in source


def test_api_tool_lists_only_bindable_slots():
    """API 工具只列「留空」或「还是占位符」的头。

    注入层对 API 工具是**只补空位**：手填了真实值的 header，绑了凭据也不会生效。
    把它列成可绑，就是在承诺一件不会发生的事。
    """
    source = _targets_source()

    assert "is_credential_placeholder" in source
    assert "bindable_slots" in source


def test_credential_params_are_offered_as_bindable_slots():
    """「来自凭据」的参数位也要出现在可绑列表里。

    漏掉它们的话，用户在面板里看不到这些槽位，也就绑不了；工具调用时会一直
    报「凭据未填」，而错误信息里没有任何线索指向「这里能绑」。
    """
    source = _targets_source()

    assert "credential_param_names" in source
    assert 'param_prefix = "query" if' in source


def test_mcp_lists_every_configured_auth_header():
    """MCP 的可绑槽位不能只有 Authorization。

    管理员给某个服务配了 X-API-Key 之后，用户若在面板里看不到那个槽位，
    就永远绑不了自己的值——而运行时那个头恰恰是需要凭据的地方。
    """
    source = _targets_source()

    assert "resolve_mcp_auth_headers" in source
    assert '"header:Authorization"' in source
    assert 'f"header:{name}"' in source


def test_broken_schema_does_not_break_the_list():
    """一条工具的参数定义是坏 JSON，不该让整个「平台服务」页打不开。"""
    source = _targets_source()

    assert "跳过参数位" in source



def test_targets_are_paginated():
    """工具上百个时不该一次全量返回。

    ``kind`` 让「我的 MCP」只取 MCP 那部分，不必白拉一遍工具。
    """
    from app.api.portal.endpoints import secrets

    params = inspect.signature(secrets.list_platform_credential_targets).parameters

    assert "page" in params and "size" in params and "kind" in params


def test_check_binding_sends_no_outbound_request():
    """校验接口只走解析链路，不发真实请求。

    真发请求的话，平台就变成了一个可以打任意内网地址的跳板；而绝大多数配置
    错误（过期、停用、场景不符、域名不在白名单、没授权）在这里就能暴露。
    """
    from app.api.portal.endpoints import secrets

    source = inspect.getsource(secrets.check_binding)

    for forbidden in ("httpx", "aiohttp", "requests", "urlopen"):
        assert forbidden not in source


def test_check_binding_never_returns_the_value():
    """只回答「能不能取到」，不返回明文也不返回脱敏值。"""
    from app.api.portal.endpoints import secrets

    source = inspect.getsource(secrets.check_binding)

    assert ".reveal()" not in source
    assert "value" not in source.split("return")[-1]

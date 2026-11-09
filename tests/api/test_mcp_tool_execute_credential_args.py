"""测试台的调用路径也要注入凭据参数。

`execute_mcp_tool` 有两条分支：开了用户身份签名的服务走 `call_remote_tool`
（绕过 McpToolFactory），没开的走工厂。注入如果只做在工厂里，签名模式下
声明成凭据的参数永远是空的——测试台会看到 `{"arguments": {}}`。
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_execute_injects_before_branching():
    from app.api.portal.endpoints import mcp as mcp_endpoints

    source = inspect.getsource(mcp_endpoints.execute_mcp_tool)
    inject_at = source.find("apply_mcp_arg_bindings")
    branch_at = source.find("signed_user_mode = bool(")

    assert inject_at != -1, "execute 里没有凭据参数注入"
    assert branch_at != -1
    assert inject_at < branch_at, "注入必须在分支之前，否则签名模式那条路会漏掉"


def test_both_branches_use_the_injected_arguments():
    from app.api.portal.endpoints import mcp as mcp_endpoints

    source = inspect.getsource(mcp_endpoints.execute_mcp_tool)
    # 再也不能把 req.arguments 直接传给远端
    assert "arguments=req.arguments" not in source
    assert "lc_tool.ainvoke(req.arguments)" not in source
    assert source.count("arguments=arguments") + source.count("ainvoke(arguments)") >= 2


def test_missing_credential_is_a_4xx_not_502():
    """凭据没绑是我们给的结论，不该被兜底 except 改成「执行失败」。"""
    from app.api.portal.endpoints import mcp as mcp_endpoints

    source = inspect.getsource(mcp_endpoints.execute_mcp_tool)
    assert "except HTTPException:" in source
    # 必须排在兜底 except 之前
    assert source.find("except HTTPException:") < source.find("except Exception as e:")

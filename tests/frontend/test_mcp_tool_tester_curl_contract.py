"""工具测试台「curl 请求」分页的契约入口。

模板断言（分页存在、可复制、按当前参数重新生成）在 node 里解析 AST 完成；
这里只负责把它接进 pytest，并守住「片段不许泄露真实凭据」这条硬约束。
"""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNIPPET_MODULE = ROOT / "app/services/mcp/curl_snippet.py"

pytestmark = pytest.mark.no_infrastructure


def test_curl_tab_contract_via_node():
    result = subprocess.run(
        ["node", "tests/frontend/mcp_tool_tester_curl.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK: 工具测试台 curl 分页" in result.stdout


def test_curl_snippet_never_reads_secret_material():
    """片段生成只允许读端点与非敏感配置，不能碰令牌/私钥字段。"""
    source = SNIPPET_MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "fixed_token_encrypted",
        "user_assertion_private_key_encrypted",
        "auth_headers）",
    ):
        assert forbidden not in source, forbidden
    # auth_headers 只用于判断「有没有静态鉴权」，不能把值拼进命令
    assert "auth_headers" in source
    assert "Bearer {PLACEHOLDER_TOKEN}" in source


def test_curl_route_requires_ownership_check():
    """只读接口也要做归属校验，否则能读到别人的 MCP 端点地址。"""
    source = (ROOT / "app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")
    body = source.split('@router.get("/tools/{tool_id}/curl")', 1)[1].split("@router.", 1)[0]

    assert "_ensure_server_control_access(server, user)" in body
    assert "build_mcp_curl_snippet(" in body
    assert "require_api_key" in body

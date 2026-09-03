from pathlib import Path


COMPONENT = Path("frontend/src/components/system/McpServerRegistry.vue")


def test_mcp_registry_exposes_signed_user_context_configuration():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "开启用户身份传递" in source
    assert "固定 MCP Token" not in source
    assert "user_assertion_enabled" in source
    assert "user_assertion_audience" in source
    assert "user_assertion_key_id" in source
    assert "user_assertion_issuer" in source
    assert "系统自动生成" in source
    assert "只读" in source
    assert "复制 Audience" in source
    assert "复制 Issuer" in source
    assert "默认透传字段" in source
    assert "公钥获取地址（JWKS）" in source
    assert "复制 JWKS 地址" in source
    assert "业务方如何使用" in source
    assert "一键生成调用模拟代码" in source
    assert "业务方 MCP 调用模拟代码" in source
    assert "复制全部代码" in source
    assert "generatedMcpCode" in source
    assert "mcpCodeLanguage" in source
    assert "NANZI_MCP_AUDIENCE" in source
    assert "NANZI_MCP_ISSUER" in source
    assert "NANZI_MCP_JWKS_URL" in source
    assert 'claims.get("sub")' in source
    assert 'claims.get("agent_id")' in source
    assert 'claims.get("request_id")' in source
    assert "redis_client.set" in source
    assert "replayStore.claim" in source
    assert "字段位置" in source
    assert "是否必有" in source
    assert "业务方使用方式" in source
    assert "custom_attributes" in source
    assert "jti" in source
    assert "过滤规则" in source
    assert ".well-known/nanzi/mcp/" in source
    assert "copyToClipboard" in source
    assert "v-model=\"newServer.user_assertion_audience\"" not in source
    assert "v-model=\"newServer.user_assertion_key_id\"" not in source
    assert "v-model=\"newServer.user_assertion_issuer\"" not in source
    assert "user_context_fields" not in source
    assert "公钥" in source
    assert "私钥" in source
    assert "问号" not in source
    assert "flex flex-1 flex-col" in source
    assert "class=\"order-2\"" in source
    assert "class=\"order-3 rounded-lg" in source


def test_mcp_registry_preserves_auth_policy_when_toggling_server_status():
    source = COMPONENT.read_text(encoding="utf-8")

    payload_builder = source[source.index("const buildServerPayload"):source.index("watch(", source.index("const buildServerPayload"))]
    assert "credential_mode" in payload_builder
    assert "user_assertion_enabled" in payload_builder
    assert "user_assertion_audience" in payload_builder
    assert "...buildServerPayload(server)" in source[source.index("const toggleServerStatus"):source.index("const fetchServerUsage")]
    assert "auth_headers: server.auth_headers || '{}'" in source


def test_mcp_tool_tester_exposes_sanitized_user_assertion_status():
    source = Path("frontend/src/components/system/McpToolTester.vue").read_text(encoding="utf-8")

    assert "本次调用认证信息" in source
    assert "X-Nanzi-User-Assertion" in source
    assert "********" in source
    assert "完整签名值不会展示" in source
    assert "mcp_auth" in source
    assert "user_assertion_sent" in source

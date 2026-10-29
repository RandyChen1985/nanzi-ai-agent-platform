from pathlib import Path


COMPONENT = Path("frontend/src/components/system/McpServerRegistry.vue")
CODE_SAMPLES = Path("frontend/src/components/system/mcpCodeSamples.ts")


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
    assert "mcpCodeSamples" in source
    # 弹窗要直接回答「代码嵌在哪一层」，而不是只丢一段验签函数。
    assert "嵌入位置" in source
    assert "① 认证中间件" in source
    assert "② MCP 端点" in source
    assert "③ 业务工具" in source
    assert "代码放哪里" in source
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
    assert "auth_headers: server.auth_headers || '{}'" not in source
    assert "已有认证信息会直接回显" not in source
    assert "已配置 Token 不会回显" in source
    assert "认证信息不会回显；已配置项显示为" not in source
    assert "authorizationEnabled" in source
    assert "authorizationEditing" in source
    assert "Bearer" in source
    assert "masked_auth_headers" in source
    assert "auth_headers_patch" in source
    assert "startAuthorizationEdit" in source


def test_mcp_registry_does_not_render_server_authentication_values():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "auth_headers: server.auth_headers" not in source
    assert "已配置 Token 不会回显" in source
    assert "认证信息不会回显；已配置项显示为" not in source
    assert "{{ server.auth_headers }}" not in source


def test_mcp_tool_tester_exposes_sanitized_user_assertion_status():
    source = Path("frontend/src/components/system/McpToolTester.vue").read_text(encoding="utf-8")

    assert "本次调用认证信息" in source
    assert "X-Nanzi-User-Assertion" in source
    assert "********" in source
    assert "完整签名值不会展示" in source
    assert "mcp_auth" in source
    assert "user_assertion_sent" in source


def test_mcp_code_samples_embed_verification_in_a_runnable_echo_mcp():
    """示例代码必须是完整的 Echo MCP，业务方照着就能定位验签嵌在哪一层。"""
    samples = CODE_SAMPLES.read_text(encoding="utf-8")

    # 三个只读配置仍然自动带入。
    assert "NANZI_MCP_AUDIENCE" in samples
    assert "NANZI_MCP_ISSUER" in samples
    assert "NANZI_MCP_JWKS_URL" in samples

    # 断言校验与防重放口径不变。
    assert 'claims.get("sub")' in samples
    assert 'claims.get("agent_id")' in samples
    assert 'claims.get("request_id")' in samples
    assert "redis_client.set" in samples
    assert "setIfAbsent" in samples

    # Python：完整 Echo MCP，验签在 ASGI 中间件层，工具里读已验签身份。
    assert "NanziUserAuthMiddleware" in samples
    assert "streamable_http_app" in samples
    assert "@mcp.tool(name=\"echo\"" in samples
    assert "request.state" in samples
    assert "consume_assertion_once" in samples

    # Java：Filter 验签 + @Tool 读取身份，与 Python 三段编号对齐。
    assert "OncePerRequestFilter" in samples
    assert "@Tool(name = \"echo\"" in samples
    assert "NanziUserAuthFilter" in samples
    assert "[①]" in samples


def test_mcp_code_samples_fail_closed_and_include_public_host():
    samples = CODE_SAMPLES.read_text(encoding="utf-8")

    # Java 静态 Token 缺失时必须启动失败，不能把期望值退化成 "Bearer null"。
    assert "FIXED_TOKEN == null" in samples
    assert "必须设置环境变量 MCP_FIXED_TOKEN" in samples

    # PyJWKClientError 不是 InvalidTokenError 的子类，JWKS 拉取失败也要 401。
    assert "jwt.PyJWKClientError" in samples

    # 跨主机部署时 Origin 校验要跟着实际访问地址走，并同时覆盖 http/https。
    assert "MCP_PUBLIC_HOST=<" in samples
    assert "https://{MCP_PUBLIC_HOST}" in samples

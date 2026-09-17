"""嵌入场景「真实 Key 换短期会话令牌」契约测试（源码级）。

约定：`?token=` 兼容入口保留，但校验通过后应改用后端换发的可过期会话令牌，
避免长期 API Key 在客户端内存中长期驻留。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def test_frontend_prefers_issued_session_token():
    """校验成功后必须优先采用后端换发的会话令牌，而非传入的长期 API Key。"""
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert 'issuedSessionToken = String(response.data.data?.session_token || "")' in source
    assert "syncValidatedCredentials(issuedSessionToken || key)" in source
    # strict_token 模式同样要换发，避免调试台沿用长期 Key
    assert "syncValidatedCredentials(issuedSessionToken || token)" in source


def test_backend_issues_session_token_for_long_lived_key():
    """`user_apikey` 端点在收到长期 Key 时签发会话令牌，已是会话令牌则不重复签发。"""
    source = (ROOT / "app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    assert "EmbedService.issue_session_from_user(user)" in source
    assert 'data["session_token"] = issued["session_token"]' in source
    assert 'credential.startswith(("sess_", "emb_ses_"))' in source


def test_embed_service_writes_into_existing_auth_keyspace():
    """令牌必须写入既有 auth 键空间，才能让 verify_api_key 保持零前缀分支。"""
    source = (ROOT / "app/services/embed_service.py").read_text(encoding="utf-8")

    assert 'cache_key = f"auth:api_key:{hashed_token}"' in source
    assert '"session_type": "embed"' in source


def test_issued_session_does_not_carry_the_real_credential():
    """签发逻辑不得把调用方的真实凭据搬运进会话缓存。"""
    source = (ROOT / "app/services/embed_service.py").read_text(encoding="utf-8")
    issue_section = source.split("async def issue_session_from_user", 1)[1]

    assert '"api_key": user.get("api_key")' not in issue_section


def test_init_config_without_token_falls_back_to_cookie_auth():
    """无凭据的 INIT_CONFIG 必须回落到同源 Cookie 校验，而不是直接 return。

    父页（Chat.vue）不再向 iframe 下发凭据——会话凭据在 HttpOnly Cookie 里，JS 读不到。
    若此处直接 return，子页不会执行 initChat，会永远停在骨架屏。
    """
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    handler = source.split("const handleInitConfig", 1)[1].split("const handlePostMessage", 1)[0]
    missing_branch = handler.split("if (!incomingToken) {", 1)[1].split(
        "config.token = incomingToken;", 1
    )[0]

    # 非 strict 分支必须继续初始化（走 Cookie 校验）
    assert "await initChat();" in missing_branch
    # strict 调试模式仍必须显式失败，不得静默回落到 Cookie
    assert "if (strict) {" in missing_branch
    assert 'reason: "missing_token"' in missing_branch


def test_parent_page_does_not_forward_credentials_to_iframe():
    """父页不得再经 postMessage 传递凭据——它已无法读到 HttpOnly Cookie。"""
    source = (ROOT / "frontend/src/views/Chat.vue").read_text(encoding="utf-8")
    init_config = source.split("const sendInitConfig", 1)[1].split("const retryInit", 1)[0]

    assert "localStorage.getItem('api_key')" not in init_config
    assert "token: apiKey" not in init_config


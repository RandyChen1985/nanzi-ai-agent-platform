"""凭据脱敏：审计日志不得落盘明文会话令牌。

## 背景

平台有四种可辨识前缀的凭据：`nzi_` 长期 API Key、`sess_` 门户会话、
`emb_ses_` 嵌入会话、`emt_` 嵌入票据。审计中间件会捕获**响应体**并落库
（`middleware.py` -> `AuditService.log_request_data` -> `mask_sensitive_data`），
而签发接口恰好把会话令牌放在响应体里：

- `GET /api/portal/auth/user_apikey` -> `data.session_token`
- `POST /api/v1/embed/tickets/exchange` -> `data.session_token`

原先的脱敏名单只有精确匹配的 `token` 与 `api_key` 等子串，`session_token`
两边都不命中，于是这些令牌以明文写进了 `ai_agent_access_logs`，审计详情接口还会
原样回显。拿到令牌即可冒用该用户，配合会话的滑动续期还能长期续命。

这里锁定两层保护：**键名**（已知字段名）与**值前缀**（字段名未收录时的兜底）。
"""
import pytest

from app.utils.masking import mask_sensitive_data

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    "key",
    [
        "session_token",
        "sessionToken",
        "portal_session",
        "portalSession",
        "embed_session",
        "ticket",
        "api_key",
        # 旧名：历史日志里已存在该键，重新脱敏时仍需覆盖
        "admin_token",
        "set_cookie",
    ],
)
def test_credential_keys_are_masked(key):
    """已知的凭据字段名一律脱敏（含 camelCase 变体）。"""
    masked = mask_sensitive_data({"data": {key: "emb_ses_SuperSecretValue123"}})

    assert masked["data"][key] == "******"


def test_ticket_id_is_not_mistaken_for_a_credential():
    """`ticket` 只做精确匹配——`ticket_id` 是业务字段，不该被误伤。"""
    masked = mask_sensitive_data({"ticket_id": "INC-2024-0001"})

    assert masked["ticket_id"] == "INC-2024-0001"


@pytest.mark.parametrize(
    "credential",
    [
        "emb_ses_abcdefghijklmnop",
        "sess_abcdefghijklmnopqrst",
        "emt_abcdefghijklmnop",
        "nzi_abcdefghijklmnopqrst",
    ],
)
def test_credential_values_are_masked_by_prefix(credential):
    """字段名未被收录时，凭据值也要按前缀抹掉（含自由文本场景）。"""
    assert mask_sensitive_data(credential) == "******"
    assert mask_sensitive_data(f"请使用 {credential} 访问") == "请使用 ****** 访问"


def test_unknown_field_name_still_masked_by_value_shape():
    """自定义字段名（如 sessionToken 以外的写法）也要靠值前缀兜底。"""
    masked = mask_sensitive_data({"my_custom_field": "emb_ses_abcdefghijklmnop"})

    assert masked["my_custom_field"] == "******"


def test_query_string_credentials_are_masked():
    """query 形态（非 JSON）同样要覆盖——原先 `?session_token=` 会原样保留。"""
    masked = mask_sensitive_data(
        "session_token=emb_ses_abcdefghijklmnop&api_key=nzi_abcdefghijklmnop"
    )

    assert "emb_ses_abcdefghijklmnop" not in masked
    assert "nzi_abcdefghijklmnop" not in masked


def test_nested_response_shape_is_masked():
    """贴合真实响应体形状：{"status":..., "data": {"session_token": ...}}。"""
    masked = mask_sensitive_data(
        {
            "status": "success",
            "data": {"valid": True, "session_token": "emb_ses_abcdefghijklmnop"},
        }
    )

    assert masked["data"]["session_token"] == "******"
    assert masked["status"] == "success"


def test_non_credential_values_are_left_alone():
    """不能把普通文本/业务值也抹掉——脱敏过度会毁掉审计可用性。"""
    payload = {
        "user_name": "test_admin",
        "message": "登录成功，欢迎回来",
        "count": 3,
    }

    assert mask_sensitive_data(payload) == payload


# --------------------------------------------------------------------------- #
# HTTP 头风格键名：名单按下划线书写，而头习惯用连字符
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key",
    ["Authorization", "X-Api-Key", "x-api-key", "Api-Key", "X-Auth-Token", "X-Access-Token"],
)
def test_http_header_style_credential_keys_are_masked(key):
    """工具试跑会把用户填的 headers 作为请求体提交，请求体再进审计。

    这些键名原先只有 ``Authorization`` 恰好被精确命中；``X-Api-Key`` 这种更常见的
    写法因为「连字符 vs 下划线」的差异整条漏过，密钥**明文落进审计库**。
    """
    masked = mask_sensitive_data({"headers": {key: "secret-value-123"}})

    assert masked["headers"][key] == "******"


@pytest.mark.parametrize(
    "key",
    ["Content-Type", "Accept", "X-Request-Id", "User-Agent", "cache_key", "sort_key", "ticket_id"],
)
def test_header_key_normalization_does_not_over_mask(key):
    """归一化只放大既有名单的覆盖面，不能把普通头也抹掉。"""
    masked = mask_sensitive_data({"headers": {key: "plain-value-123"}})

    assert masked["headers"][key] == "plain-value-123"


def test_header_style_keys_masked_inside_a_full_request_body():
    """贴合试跑请求的真实形状：headers 嵌在 JSON 请求体里。"""
    masked = mask_sensitive_data(
        {
            "method": "GET",
            "url_template": "https://api.test/v1/items",
            "headers": {"X-Api-Key": "secret-value-123", "Content-Type": "application/json"},
            "params": {"id": "7"},
        }
    )

    assert masked["headers"]["X-Api-Key"] == "******"
    assert masked["headers"]["Content-Type"] == "application/json"
    assert masked["params"] == {"id": "7"}



@pytest.mark.parametrize("key", ["signing_secret", "signingSecret"])
def test_signing_secret_is_masked_in_audit_payload(key):
    """工具创建/试跑请求体里的签名密钥不能以明文进审计库。"""
    secret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    masked = mask_sensitive_data({"data": {key: secret}})

    assert masked["data"][key] == "******"


def test_set_cookie_response_header_is_masked_with_http_spelling():
    """试跑响应头会展示 Set-Cookie；连字符写法也必须按凭据抹掉。"""
    masked = mask_sensitive_data({"Set-Cookie": "session=abc123"})

    assert masked["Set-Cookie"] == "******"

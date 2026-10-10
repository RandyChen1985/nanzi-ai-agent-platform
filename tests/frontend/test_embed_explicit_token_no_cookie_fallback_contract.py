"""显式 token 校验失败后不得回落到同源 Cookie 会话（源码级契约）。

## 背景

嵌入页支持两种建立会话的方式：

1. **显式凭据**：URL `?token=<API Key>`，或宿主经 `INIT_CONFIG` 下发
   `token` / `api_key` / `apikey`。此时 `config.token` 非空（`strict_token` 同理）。
2. **同源 Cookie 回落**：平台内 iframe（`Chat.vue`）不再向子页下发凭据，
   会话位于 HttpOnly `portal_session` Cookie，由 `validateToken` 的 Cookie 分支认证。
   此时 `config.token` 为空。

问题在于 `validateToken` 的 Cookie 回落分支原先**无条件执行**：宿主显式传了无效
token 时，候选凭据虽全部被拒，却仍会回落到浏览器里残留的 portal 会话并「成功」——
token 形同虚设。共享设备上 A 用户登录后未登出，任何人打开
`/embed/chat?token=乱填` 都以 A 的身份进入。

## 约定

- **显式**提供了凭据（`config.token` 非空）时，凭据无效即失败，**不得**回落 Cookie；
- 只有「本 tab sessionStorage 里持久化的嵌入会话令牌」时不同：它是上一位使用者留下的
  （换人登录后 Cookie 才是权威身份），因此：
  - 该令牌被服务端拒绝后允许回落 Cookie（否则换了人只会看到空白骨架屏）；
  - 该令牌有效但身份与同源 Cookie 不一致时，**以 Cookie 身份为准**并丢弃存储令牌
    （`switchToCookieIdentityIfChanged`），避免界面显示新用户、数据却落到旧用户名下；
- 未提供任何凭据（平台内访问）时，Cookie 回落照常工作，否则子页会停在骨架屏。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

COOKIE_FALLBACK = 'credentials: "include"'
# 守卫只看「本次是否显式下发了凭据」：存储令牌被拒时要允许回落 Cookie。
CLEAR_TOKEN_GUARD = "if (!config.token) {"
STORED_CREDENTIAL_MARKER = "const storedSessionCredential = readEmbedSession();"


def _validate_token_body() -> str:
    """截取 validateToken 函数体（到下一个顶层函数 initChat 为止）。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    start = source.index("const validateToken = async")
    end = source.index("const initChat = async", start)
    return source[start:end]


def test_cookie_fallback_still_present_for_platform_internal_access():
    """Cookie 回落分支必须保留——平台内 iframe 不带 token，仅靠它认证。"""
    assert COOKIE_FALLBACK in _validate_token_body()


def test_cookie_fallback_is_guarded_by_explicit_token_only():
    """Cookie 回落必须受「本次是否显式带凭据」守卫，否则显式无效凭据会被静默放行。"""
    body = _validate_token_body()

    assert CLEAR_TOKEN_GUARD in body, "Cookie 回落分支缺少 config.token 判空守卫"
    # 取最后一处 Cookie 请求：函数里前置的「Cookie 身份核对」也在同函数内，
    # 真正的兜底回落是最后那一处。
    fallback_at = body.rindex(COOKIE_FALLBACK)
    guard_at = body.index(CLEAR_TOKEN_GUARD)
    assert guard_at < fallback_at, "判空守卫必须位于兜底 Cookie 回落之前"
    guard_region = body[guard_at:fallback_at]
    assert "storedSessionCredential" not in guard_region, (
        "守卫不得把本 tab 存储的令牌也算作显式凭据："
        "否则换人登录后（存储令牌已失效）会永远回不到 Cookie 身份"
    )


def test_stored_credential_yields_to_cookie_identity_when_it_differs():
    """存储令牌有效但身份与同源 Cookie 不一致时，必须让位给 Cookie 身份。"""
    body = _validate_token_body()

    assert "const switchToCookieIdentityIfChanged = async (): Promise<boolean> =>" in body
    assert "const usingStoredCredential = key === storedSessionCredential && key !== explicitToken;" in body
    assert "await switchToCookieIdentityIfChanged()" in body, (
        "命中存储令牌后必须做一次 Cookie 身份一致性核对，否则同机换人登录仍会用旧身份"
    )
    # 让位判定必须发生在写入凭据（syncValidatedCredentials）之前，否则旧令牌已经生效。
    assert body.index("await switchToCookieIdentityIfChanged()") < body.index(
        "syncValidatedCredentials(issuedSessionToken || key)"
    )
    assert STORED_CREDENTIAL_MARKER in body


def test_guard_does_not_change_strict_mode_behavior():
    """strict 模式仍应只认本次传入的 token，不因新增守卫而获得 Cookie 兜底。"""
    body = _validate_token_body()

    strict_branch = body.index("if (strict) {")
    guard_at = body.index(CLEAR_TOKEN_GUARD)
    # strict 分支应在守卫之前就 return，二者互不影响
    assert strict_branch < guard_at

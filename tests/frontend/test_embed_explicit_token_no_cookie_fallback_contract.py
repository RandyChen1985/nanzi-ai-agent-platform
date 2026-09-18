"""显式 token 校验失败后不得回落到同源 Cookie 会话（源码级契约）。

## 背景

嵌入页支持两种建立会话的方式：

1. **显式凭据**：URL `?token=<API Key>`，或宿主经 `INIT_CONFIG` 下发
   `token` / `api_key` / `apikey`。此时 `config.token` 非空。
2. **同源 Cookie 回落**：平台内 iframe（`Chat.vue`）不再向子页下发凭据，
   会话位于 HttpOnly `admin_token` Cookie，由 `validateToken` 的 Cookie 分支认证。
   此时 `config.token` 为空。

问题在于 `validateToken` 的 Cookie 回落分支原先**无条件执行**：宿主显式传了无效
token 时，候选凭据虽全部被拒，却仍会回落到浏览器里残留的 portal 会话并「成功」——
token 形同虚设。共享设备上 A 用户登录后未登出，任何人打开
`/embed/chat?token=乱填` 都以 A 的身份进入。

## 约定

- 显式提供了凭据（`config.token` 非空）时，凭据无效即失败，**不得**回落 Cookie；
- 未提供凭据（平台内访问）时，Cookie 回落照常工作，否则子页会停在骨架屏。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

COOKIE_FALLBACK = 'credentials: "include"'
# 守卫同时考虑「本 tab 持久化的嵌入会话」：它是刷新后的显式凭据，失效即失败，
# 不能因为 config.token 为空就回落到浏览器里可能残留的 portal 会话。
CLEAR_TOKEN_GUARD = "if (!config.token"


def _validate_token_body() -> str:
    """截取 validateToken 函数体（到下一个顶层函数 initChat 为止）。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    start = source.index("const validateToken = async")
    end = source.index("const initChat = async", start)
    return source[start:end]


def test_cookie_fallback_still_present_for_platform_internal_access():
    """Cookie 回落分支必须保留——平台内 iframe 不带 token，仅靠它认证。"""
    assert COOKIE_FALLBACK in _validate_token_body()


def test_cookie_fallback_is_guarded_by_empty_token():
    """Cookie 回落必须受「本次是否带嵌入凭据」守卫，否则显式无效凭据会被静默放行。"""
    body = _validate_token_body()

    assert CLEAR_TOKEN_GUARD in body, "Cookie 回落分支缺少 config.token 判空守卫"
    assert "storedSessionCredential" in body, (
        "守卫未考虑本 tab 持久化的嵌入会话：其失效时会回落到残留 portal 会话，造成串号"
    )
    assert body.index(CLEAR_TOKEN_GUARD) < body.index(COOKIE_FALLBACK), (
        "判空守卫必须位于 Cookie 回落之前"
    )


def test_guard_does_not_change_strict_mode_behavior():
    """strict 模式仍应只认本次传入的 token，不因新增守卫而获得 Cookie 兜底。"""
    body = _validate_token_body()

    strict_branch = body.index("if (strict) {")
    guard_at = body.index(CLEAR_TOKEN_GUARD)
    # strict 分支应在守卫之前就 return，二者互不影响
    assert strict_branch < guard_at

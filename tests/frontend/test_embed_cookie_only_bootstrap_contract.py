"""清除 URL 凭据后，刷新必须仍能靠 Cookie 触发一次认证。

## 背景（真实故障）

引入 `embed_session` Cookie 后，`?token=` 页面会在认证成功后清除地址栏里的 token。
但清完之后按 F5 会出现「无权限 / 登录状态已失效」。根因**不在 Cookie**——后端实测
「只带 Cookie、不带任何 header」认证是成功的；问题在前端**没有触发认证**：

    const scheduleUrlTokenInitialization = () => {
      if (!config.token || initConfigReceived) return;   // ← 要求 token 非空
      ...
    };

`initChat()`（内部调用 `validateToken()`，即真正的 Cookie 回落分支）此前只由
`INIT_CONFIG` 消息或 `scheduleUrlTokenInitialization()` 触发，而后者**要求
`config.token` 非空**。URL 被清干净后 `config.token` 为空、又没有 `INIT_CONFIG`，
于是**没有任何代码发起认证**，页面直接停在失败态。

旧行为之所以能刷新，正是因为 token 一直留在 URL 里撑起了这个触发条件；清除 URL
等于抽掉了它。

## 要求

1. 存在「无 token 时」的引导路径，且它会调用 `initChat()`（从而走 Cookie 回落）。
2. 该路径必须受 `initConfigReceived` 保护并保留握手窗口，避免抢在平台内嵌的
   `INIT_CONFIG` 之前用 Cookie 认证，导致身份或配置被覆盖。
3. 不得改变「有 token 时」的既有优先级（显式凭据优先于 Cookie）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_cookie_only_bootstrap_path_exists():
    """必须存在无 token 时的 Cookie 引导路径（否则清 URL 后刷新无人发起认证）。"""
    source = _source()
    assert "scheduleCookieOnlyInitialization" in _source(), (
        "缺少无 token 的引导路径：清除 URL 后刷新将没有任何代码发起认证"
    )

    fn_start = source.index("const scheduleCookieOnlyInitialization")
    fn_body = source[fn_start : fn_start + 900]

    assert "initChat(" in fn_body, "Cookie 引导路径必须调用 initChat（内部走 Cookie 回落分支）"
    assert "initConfigReceived" in fn_body, (
        "Cookie 引导必须受 initConfigReceived 保护，避免抢在平台内嵌 INIT_CONFIG 之前认证"
    )


def test_cookie_only_bootstrap_is_invoked_when_no_token():
    """页面引导逻辑必须在「无 token 且无 ticket」时调用该路径。"""
    source = _source()
    call_pos = source.index("scheduleCookieOnlyInitialization()")
    window = source[max(0, call_pos - 1500) : call_pos]

    # 调用点应位于「有 token」分支的 else 侧，即由 config.token 的判定区分开
    assert "config.token" in window, (
        "调用点应处于对 config.token 的判断分支内"
    )
    assert "else if (!ticketFromUrl)" in window, (
        "调用点必须位于「无 ticket」的 else 分支内，避免与 ticket 路径重复初始化"
    )


def test_explicit_token_still_takes_precedence():
    """有 token 时仍走原路径：显式凭据优先，不能被 Cookie 引导抢占。"""
    source = _source()
    # 原函数必须保留 token 前置条件
    fn_start = source.index("const scheduleUrlTokenInitialization")
    fn_body = source[fn_start : fn_start + 300]
    assert "if (!config.token" in fn_body, (
        "scheduleUrlTokenInitialization 必须保留 token 前置条件，显式凭据优先于 Cookie"
    )

"""URL 里的凭据只能在「刷新有凭据可依」之后才可清除（token 与 ticket 同理）。

## 背景

早先曾尝试「读到 URL token 后立即清除地址栏」，结果**刷新即失效**：`?token=` 模式
当时全程无会话，URL 里的 Key 是唯一凭据，清掉它等于刷新时两处都没凭据。

随后改为「后端下发 `embed_session` Cookie 就清 URL」，仍然不够——`session_cookie_issued`
只说明**服务端写了 Set-Cookie**，并不保证浏览器会存、更不保证后续请求会带上。该 Cookie
是 `SameSite=lax`，**在跨站第三方 iframe 里不会随请求发送**，而 `/embed/` 恰恰以
`frame-ancestors *` 明确支持跨站嵌入。于是跨站 `?token=` 场景在清理 URL 后刷新必失败，
比清理之前更糟（此前 URL 里还有 Key 可撑刷新）。

## 现在的要求

刷新要能成立，必须有一个**不属于 URL** 的凭据来源，判定顺序：

1. 本 tab 已把会话令牌写进 `sessionStorage`（`readEmbedSession()` 非空）——跨站 iframe
   同样有效，是主要依据；
2. 或后端确实下发了 `embed_session` Cookie（`lastSessionCookieIssued`）——同站补充。

统一入口是 `maybeStripUrlAfterSessionReady()`，禁止绕过它直接清除。

## 覆盖点

- 清除动作必须由上述「凭据落地」判定保护；
- 只删 `token` 与 `ticket`，其余参数（`agent_id` / `theme` / `instance_id`）原样保留；
- 用 `replaceState` 改写当前历史条目，不得跳转或重载页面；
- ticket 路径同样经统一入口清除。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_stripping_is_guarded_by_persisted_session_or_cookie_flag():
    """清除必须由「会话令牌已落地（本 tab）或后端已下发 Cookie」保护。"""
    source = _source()
    fn_start = source.index("const maybeStripUrlAfterSessionReady")
    fn_body = source[fn_start : fn_start + 600]

    assert "readEmbedSession()" in fn_body, (
        "未检查本 tab 持久化的会话令牌——跨站 iframe 下 Cookie 不发送，清 URL 会导致刷新失效"
    )
    assert "lastSessionCookieIssued" in fn_body, (
        "未检查后端是否下发 embed_session Cookie（同站场景的补充依据）"
    )
    assert "stripUrlCredentials()" in fn_body, "判定通过后未真正清除 URL 凭据"


def test_no_call_site_bypasses_the_guard():
    """任何地方都不得绕过判定直接清 URL（曾因「读到即清」踩过刷新失效）。"""
    source = _source()
    assert source.count("stripUrlCredentials()") == 1, (
        "stripUrlCredentials() 应在 maybeStripUrlAfterSessionReady 内部被调用一次；"
        "其他位置必须改调 maybeStripUrlAfterSessionReady()，否则会绕过凭据落地判定"
    )


def test_stripping_deletes_only_credentials():
    """只删 token 与 ticket，其余非敏感参数必须保留。"""
    source = _source()
    fn_start = source.index("const stripUrlCredentials")
    fn_body = source[fn_start : fn_start + 1100]

    assert 'searchParams.delete("token")' in fn_body, "未删除 token"
    assert 'searchParams.delete("ticket")' in fn_body, "未删除 ticket"
    for keep in ("agent_id", "theme", "instance_id"):
        assert f'delete("{keep}")' not in fn_body, (
            f"不得删除 {keep}：它不属于本次清理范围，删了会破坏智能体锁定/主题/多实例隔离"
        )


def test_stripping_uses_replace_state_without_reload():
    """用 replaceState 改写历史条目，不得跳转或重载（且不能留下带凭据的历史记录）。"""
    source = _source()
    fn_start = source.index("const stripUrlCredentials")
    fn_body = source[fn_start : fn_start + 1100]

    assert "history.replaceState" in fn_body, "须用 replaceState 避免留下带凭据的历史条目"
    assert "location.reload" not in fn_body, "不得重载页面"
    assert "location.href =" not in fn_body, "不得跳转"


def test_ticket_success_also_strips_url_credential():
    """ticket 兑换成功后也要经统一入口清除 URL（否则一次性票据长期留在地址栏）。"""
    source = _source()
    idx = source.index("const ok = await exchangeTicketOnce(")
    branch = source[idx : idx + 900]

    assert "maybeStripUrlAfterSessionReady()" in branch, (
        "ticket 兑换成功后未清除 URL：一次性票据会长期残留在地址栏与历史中"
    )


def test_init_config_ticket_branch_also_strips():
    """INIT_CONFIG 的 ticket 分支与 URL ticket 分支行为必须一致，不能一个清一个不清。"""
    source = _source()
    idx = source.index("if (data.ticket) {")
    branch = source[idx : idx + 900]

    assert "maybeStripUrlAfterSessionReady()" in branch, (
        "INIT_CONFIG 的 ticket 分支未清除 URL 凭据，与 onMounted 分支不一致"
    )

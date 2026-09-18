"""嵌入会话令牌必须落到**本 tab**（sessionStorage），刷新才不再依赖跨站不发送的 Cookie。

## 背景（两个真实缺陷）

1. **跨站 iframe 刷新失效**：`embed_session` 是 `SameSite=lax`，在跨站第三方 iframe 里
   不会随请求发送（`/embed/` 以 `frame-ancestors *` 支持跨站嵌入）。而前端只要看到
   `session_cookie_issued` 就清除 URL 里的凭据，于是跨站 `?token=` 场景刷新后既无 URL
   凭据、Cookie 又不发送 —— 比不清除时更差（此前 URL 里的 Key 还能撑住刷新）。
2. **身份串号**：Cookie 是「整浏览器一个槽」，`require_api_key` 又让 `portal_session` 优先于
   `embed_session`。多个嵌入实例（不同 target user）会互相覆盖，或刷新后静默切换成门户
   登录用户的身份 —— 会话历史按认证 user_id 过滤，即读到他人的会话与消息。

把会话令牌放进 sessionStorage 可同时解决：按 tab 隔离（iframe 各自独立）、刷新后仍在、
关闭标签即清除，且不必依赖 Cookie 是否会被发送。

## 权衡说明

sessionStorage 是 JS 可读的，因此这里**只放短期、可吊销的嵌入会话令牌**，绝不放长期
API Key；真实 Key 依旧用后即弃、不落任何 localStorage。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_session_storage_helpers_exist():
    """必须存在本 tab 的持久化/读取/清除三个操作。"""
    source = _source()

    assert "sessionStorage" in source, "未使用 sessionStorage 持久化嵌入会话"
    assert "const persistEmbedSession" in source, "缺少持久化入口"
    assert "const readEmbedSession" in source, "缺少读取入口"
    assert "const clearEmbedSession" in source, "缺少清除入口"


def test_validated_credentials_are_persisted():
    """校验通过后的统一入口必须把令牌写进本 tab，否则刷新仍然无据可依。"""
    source = _source()
    fn_start = source.index("const syncValidatedCredentials")
    fn_body = source[fn_start : fn_start + 800]

    assert "persistEmbedSession(" in fn_body, (
        "校验通过后未持久化会话令牌：刷新时只能依赖可能不发送的 Cookie"
    )
    # 长期 Key 不得落 localStorage（sessionStorage 只放换发来的会话令牌）
    assert "localStorage.setItem" not in fn_body, (
        "syncValidatedCredentials 不得写入 localStorage"
    )


def test_validate_candidates_include_persisted_session():
    """刷新后 config.token 为空，候选必须补上本 tab 持久化的会话令牌。"""
    source = _source()
    start = source.index("const validateToken = async")
    end = source.index("const initChat = async", start)
    body = source[start:end]

    assert "readEmbedSession()" in body, (
        "校验候选未包含 sessionStorage 中的会话令牌，刷新后无法重建请求头"
    )


def test_rejected_session_is_cleared():
    """服务端明确拒绝（401/403）时必须清掉本 tab 的令牌，避免每次刷新白试一次。"""
    source = _source()
    start = source.index("const validateToken = async")
    end = source.index("const initChat = async", start)
    body = source[start:end]

    assert "clearEmbedSession()" in body, (
        "会话被拒后未清除本地令牌：会一直带着失效凭据重试"
    )


def test_ticket_exchange_is_deduplicated():
    """同一张 ticket 的重复兑换必须去重。

    ticket 一次性（后端 GETDEL 原子核销），URL `?ticket=` 与宿主 postMessage 下发的
    ticket 可能同时到达，并发兑换必有一方失败并误报「凭证已失效」。
    """
    source = _source()

    assert "const exchangeTicketOnce" in source, "缺少 ticket 兑换去重入口"
    fn_start = source.index("const exchangeTicketOnce")
    fn_body = source[fn_start : fn_start + 900]
    assert "inflightTicketExchanges" in fn_body, "未共享进行中的兑换 Promise"
    assert "consumedTicketValues" in fn_body, "未记录已成功兑换的 ticket"
    # 所有 ticket 兑换都必须经去重入口
    assert source.count("exchangeTicketAndApply(") == 1, (
        "exchangeTicketAndApply 只应在 exchangeTicketOnce 内部被调用一次，"
        "其他位置必须走 exchangeTicketOnce"
    )


def test_ticket_exchange_persists_session_too():
    """ticket 路径同样要把会话令牌落到本 tab（ticket 一次性，撑不住刷新）。"""
    source = _source()
    fn_start = source.index("const exchangeTicketAndApply")
    fn_body = source[fn_start : fn_start + 1200]

    assert "syncValidatedCredentials(" in fn_body, (
        "ticket 兑换未走统一入口：会话令牌不会持久化，刷新即丢（跨站场景必失败）"
    )

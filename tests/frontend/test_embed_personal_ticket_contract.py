"""Contract: /embed/personal 的自带凭据入口（`?ticket=`）与跨站可用性。

## 背景

`/embed/personal` 原先只认 Cookie，而 `portal_session` / `embed_session` 都是
`SameSite=Lax`——SameSite 的判定基准是**顶层站点的站**，因此宿主与平台不同主域时，
跨站 iframe 里的请求既不会带上、也不接受这两个 Cookie，该页在跨站场景下必然落到
「未授权访问」。修法与 `/embed/chat` 同口径：由宿主后端签发一次性 ticket，页面自行兑换。

## 两条最容易做错、也最容易漏测的边界

1. **令牌必须注入 axios 默认请求头**，而不只是用于门禁核验。`PersonalCenter` 九个 Tab
   的所有请求都靠 Cookie 认证；跨站下它们同样收不到 Cookie。只改门禁的后果是
   「门禁通过、各 Tab 全是空数据/报错」，比直接拒绝渲染更难排查。
2. **令牌必须落到本 tab（sessionStorage）**，ticket 是一次性的、撑不住刷新；
   键名还要按 `instance_id` 分桶，因为 sessionStorage 的隔离粒度是 tab + origin，
   不是 iframe——同一 tab 里多个同源 iframe 共享它，不分桶就会互相覆盖导致身份串号。

## 刻意不做的事

不接受 `?token=` / `INIT_CONFIG.token` 这类**长期 API Key**。嵌入个人中心没有存量宿主
需要兼容，长期密钥一旦进浏览器地址栏、历史记录与访问日志就再也收不回来。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
VIEW_PATH = "frontend/src/views/EmbedPersonal.vue"


def _source() -> str:
    return (ROOT / VIEW_PATH).read_text(encoding="utf-8")


def _fn_body(source: str, marker: str, length: int = 1400) -> str:
    """截取某个顶层 const 之后的一段源码作为函数体近似。"""
    start = source.index(marker)
    return source[start : start + length]


def test_accepts_ticket_from_url_and_exchanges_it():
    """`?ticket=` 必须被解析并兑换，否则跨站场景根本没有凭据入口。"""
    source = _source()

    assert 'query.get("ticket")' in source, "未解析 URL 里的 ticket"
    assert "/api/v1/embed/tickets/exchange" in source, "未调用 ticket 兑换接口"
    assert "const exchangeTicketOnce" in source, "缺少兑换去重入口"


def test_ticket_exchange_is_deduplicated():
    """同一张 ticket 只能兑换一次。

    URL `?ticket=` 与宿主经 postMessage 下发的 ticket 可能同时到达，而 ticket 是一次性的
    （后端 GETDEL 原子核销），并发兑换必有一方失败并误报「凭证已失效」。
    """
    source = _source()
    body = _fn_body(source, "const exchangeTicketOnce")

    assert "inflightTicketExchanges" in body, "未共享进行中的兑换 Promise"
    assert "consumedTicketValues" in body, "未记录已成功兑换的 ticket"
    assert source.count("exchangeTicketAndApply(") == 1, (
        "exchangeTicketAndApply 只应在 exchangeTicketOnce 内部被调用一次，"
        "其他位置必须走去重入口"
    )


def test_session_token_is_injected_into_axios_default_headers():
    """兑换来的会话令牌必须写进 axios 默认头——这是各 Tab 在跨站下唯一能用的凭据。"""
    source = _source()
    body = _fn_body(source, "const applySessionToken")

    assert 'axios.defaults.headers.common["X-API-Key"]' in body, (
        "未把会话令牌写进 axios 默认请求头：跨站下 PersonalCenter 的请求仍会 401"
    )
    assert 'axios.defaults.headers.common["Authorization"]' in body, (
        "未同时写 Authorization 头，与 EmbedChat 的凭据口径不一致"
    )
    assert "persistEmbedSession(" in body, "令牌未落到本 tab，刷新即失效"


def test_session_token_is_persisted_per_instance():
    """令牌持久化必须按 instance_id 分桶：sessionStorage 的边界是 tab + origin。"""
    source = _source()
    body = _fn_body(source, "const embedSessionStorageKey", 400)

    assert "instanceId.value" in body, (
        "存储键未按 instance_id 分桶：同页多实例会共享同一个键并互相覆盖"
    )
    assert "${EMBED_SESSION_STORAGE_PREFIX}:" in body, (
        "分桶键未以固定前缀派生，会与其它实例或历史键冲突"
    )

    # 三个读写入口都必须走分桶键，不得硬编码字面量
    for fn, what in (
        ("const persistEmbedSession", "写入"),
        ("const readEmbedSession", "读取"),
        ("const clearEmbedSession", "清除"),
    ):
        fn_body = _fn_body(source, fn, 700)
        assert "embedSessionStorageKey()" in fn_body, f"{what}入口未使用分桶键"
        assert 'sessionStorage.setItem("nzi_embed_personal_session_token"' not in fn_body, (
            f"{what}入口硬编码了无实例维度的键名"
        )


def test_persisted_session_bootstraps_refresh():
    """刷新（URL 只剩本页、没有 ticket）时必须用本 tab 的令牌重建请求头。"""
    source = _source()
    body = _fn_body(source, "const bootstrapEmbedSession", 1800)

    assert "readEmbedSession()" in body, "启动引导未读取本 tab 持久化的令牌"
    assert "applySessionToken(persistedToken)" in body, (
        "读回的令牌未注入请求头，刷新后仍然无法访问个人中心"
    )


def test_url_ticket_is_stripped_only_after_session_ready():
    """ticket 一次性且已核销，确认刷新有据可依后才能从地址栏移除，否则刷新即失效。"""
    source = _source()
    body = _fn_body(source, "const maybeStripUrlAfterSessionReady", 260)

    assert "readEmbedSession()" in body, "清除 URL 凭据前未确认本 tab 已持有令牌"
    assert "lastSessionCookieIssued" in body, "缺少同站场景的 Cookie 兜底判定"
    assert "history.replaceState" in _fn_body(source, "const stripUrlTicket", 700), (
        "未从地址栏移除已核销的 ticket（会残留在历史记录与分享链接里）"
    )


def test_ticket_failure_reasons_are_distinguishable():
    """403（来源不被允许）与 400（票已失效）必须给出不同原因，否则排查方向被带偏。"""
    source = _source()
    body = _fn_body(source, "const exchangeTicketAndApply", 1400)

    assert 'status === 403 ? "origin_not_allowed" : "ticket_invalid"' in body, (
        "未按 HTTP 状态码区分兑换失败原因"
    )
    assert "resolveTicketFailureReason" in source, "未把失败原因透传给宿主的 INIT_FAILURE"
    assert 'authFailureReason.value = resolveTicketFailureReason()' in source, (
        "ticket 失败后未使用具体原因上报"
    )


def test_host_can_push_ticket_via_postmessage():
    """宿主无法把凭据放在 URL 时应能经 postMessage 下发 ticket 或短期会话令牌。"""
    source = _source()
    body = _fn_body(source, "const handleHostMessage", 1500)

    assert 'case "INIT_CONFIG"' in body, "未处理 INIT_CONFIG"
    assert 'case "RESET_SESSION"' in body, "未处理 RESET_SESSION（会话过期后的静默续签）"
    assert "applyTicketFromHost(ticket)" in body, "宿主下发的 ticket 未被兑换"
    assert "applySessionToken(sessionToken)" in body, "宿主下发的短期会话令牌未被注入"


def test_reports_lifecycle_events_to_host():
    """上行事件与 /embed/chat 同一信封，宿主可用同一套监听逻辑。"""
    source = _source()

    assert 'source: EMBED_MESSAGE_SOURCE' in source or 'source: "nanzi-agent-embed"' in source, (
        "消息信封缺少 source 标识，宿主无法过滤"
    )
    assert 'type: "NANZI_WIDGET_READY"' in source, "未宣告组件就绪"
    assert 'type: "INIT_SUCCESS"' in source, "核验通过后未通知宿主"
    assert 'type: "INIT_FAILURE"' in source, "失败后未通知宿主"
    # 非认证类失败（断网 / 5xx）不得上报 INIT_FAILURE，否则会诱使宿主重签一张没问题的票
    error_branch = source[source.index("const verifyCredential") :]
    error_branch = error_branch[: error_branch.index("\n};")]
    assert 'gateState.value = isAuthFailure(error) ? "unauthorized" : "error"' in error_branch
    assert 'reason: authFailureReason.value });' in error_branch


def test_rejects_long_lived_api_key():
    """嵌入个人中心不接受长期 API Key：没有存量宿主需要兼容，别把 Key 请进浏览器。"""
    source = _source()

    assert 'query.get("token")' not in source, "不应接受 URL 里的长期 API Key"
    # 只看真实读取点，不能因为注释里解释了「为什么不接受」而误报
    assert "query.get(\"api_key\")" not in source, "不应接受 api_key 查询参数"
    assert "(data as any).api_key" not in source, "不应接受 INIT_CONFIG.api_key 这类长期凭据"
    assert "(data as any).token" not in source, "不应接受 INIT_CONFIG.token 这类长期凭据"
    assert "localStorage.setItem" not in source, (
        "长期存储不得写入 localStorage；短期会话令牌只放本 tab 的 sessionStorage"
    )

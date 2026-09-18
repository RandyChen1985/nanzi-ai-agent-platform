"""embed_session Cookie：让嵌入页首次认证后不再依赖 URL 里的长期 Key。

## 背景

`?token=<长期 API Key>` 兼容模式下，`/api/portal/auth/user_apikey` 只校验凭据并
返回 `emb_ses_` 给 JS，而 `EmbedChat.vue` 的 `syncValidatedCredentials` 仅把它写进
内存与 axios header（刻意不落 localStorage，以防 XSS 窃取）。后果：

- 刷新时内存已空，只能回头再读 URL 里的长期 Key；
- 于是长期 Key **一直停留在地址栏 / 历史 / Referer / 访问日志**中；
- 且每次刷新都重新签发一个 24 小时会话令牌（本机实测三次调用得到三个不同
  `emb_ses_`），Redis 中旧令牌不被吊销、持续累积。

参照「密码登录 → 下发 `portal_session`」的对称做法，校验通过后应下发**独立**的
`embed_session` Cookie：长期 Key 仅首次出现，之后由该会话支撑刷新。

## 本测试锁定的安全约束

1. **必须独立于 `portal_session`**：共用同名 Cookie 会顶掉用户的门户登录态
   （浏览器同名 Cookie 相互覆盖，path 均为 `/`）。
2. **`portal_session` 优先级必须高于 `embed_session`**：门户登录态优先于嵌入会话，
   否则平台内嵌 iframe 场景会被降级或串号。
3. **仅当凭据经 header 显式传入时下发**：凭据来自 Cookie 说明浏览器已有会话，
   此时再下发会在门户登录态之外凭空多挂一个身份。
4. **HttpOnly**：不得让 JS 读到会话令牌。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = ROOT / "app/core/dependencies.py"
AUTH_PY = ROOT / "app/api/portal/endpoints/auth.py"
EMBED_PY = ROOT / "app/api/v1/endpoints/embed.py"

EMBED_COOKIE = "embed_session"
PORTAL_COOKIE = "portal_session"


def test_require_api_key_reads_embed_session_after_portal_session():
    """portal_session 必须优先于 embed_session（门户登录态不得被嵌入会话覆盖）。"""
    source = DEPENDENCIES.read_text(encoding="utf-8")
    body = source[source.index("async def require_api_key") :]
    body = body[: body.index("async def ", 10)] if "async def " in body[10:] else body

    assert f'cookies.get("{EMBED_COOKIE}")' in body, "require_api_key 未支持 embed_session"
    assert f'cookies.get("{PORTAL_COOKIE}")' in body, "require_api_key 不应移除 portal_session"

    admin_pos = body.index(f'cookies.get("{PORTAL_COOKIE}")')
    embed_pos = body.index(f'cookies.get("{EMBED_COOKIE}")')
    assert admin_pos < embed_pos, (
        "读取顺序错误：portal_session 必须先于 embed_session，否则门户登录态会被嵌入会话抢占"
    )


def test_embed_session_cookie_is_distinct_named_and_httponly():
    """embed_session 必须是与 portal_session 不同的 Cookie 名，且 HttpOnly。"""
    source = AUTH_PY.read_text(encoding="utf-8")

    # 断言常量赋值本身。不能靠 index("embed_session") 定位后截取片段——解释性注释里
    # 必然同时提到 embed_session 与 portal_session，会被自己的文档绊倒（已踩过一次）。
    const_pos = source.index("EMBED_SESSION_COOKIE_NAME = ")
    const_line = source[const_pos : source.index("\n", const_pos)]
    assert '"embed_session"' in const_line, "常量名与 Cookie 名不一致"
    assert PORTAL_COOKIE not in const_line, (
        "embed_session 不得复用 portal_session 名称（会顶掉门户登录态）"
    )

    # 定位实际下发调用：set_cookie( 与 key= 分处相邻两行，不能按同一行查找
    lines = source.splitlines()
    key_line = next(
        (i for i, ln in enumerate(lines) if "key=EMBED_SESSION_COOKIE_NAME" in ln),
        None,
    )
    assert key_line is not None, "embed_session 未通过 set_cookie 下发"
    call_block = "\n".join(lines[max(0, key_line - 3) : key_line + 8])
    assert "set_cookie" in call_block, "embed_session 未通过 set_cookie 下发"
    assert "httponly=True" in call_block, (
        "embed_session 必须 HttpOnly（否则 XSS 可读取会话令牌）"
    )


def test_cookie_only_issued_for_explicit_header_credentials():
    """仅当凭据经 header 显式传入时才下发 embed_session。

    判据是**凭据来源**（`require_api_key` 写在 `request.state.credential_source` 上），
    而不是比较凭据值——值相等不代表来源相同（门户 Cookie 里恰好就是同一个真实 Key 时，
    值比较会把 header 传入误判成 Cookie 来源，从而漏发会话）。
    """
    source = AUTH_PY.read_text(encoding="utf-8")
    fn_start = source.index("async def validate_user_apikey")
    fn_body = source[fn_start : fn_start + 3600]

    # 必须具备「凭据来源」判断，避免对 Cookie 认证的请求重复下发
    assert "credential_source" in fn_body, (
        "user_apikey 未判断凭据来源，会对 Cookie 认证的请求也下发 embed_session"
    )
    assert '"header"' in fn_body, "来源判断必须限定为 header 显式传入"
    assert "_set_embed_session_cookie(" in fn_body, "user_apikey 未下发 embed_session"

    # 下发必须受来源判断保护（下发行出现在判断之后）
    source_check_pos = fn_body.index("credential_source")
    issue_positions = [
        p
        for p in (
            fn_body.find("response.set_cookie"),
            fn_body.find("_set_embed_session_cookie("),
        )
        if p != -1
    ]
    assert issue_positions, "未找到下发点"
    assert all(p > source_check_pos for p in issue_positions), (
        "下发 embed_session 的代码必须位于「凭据来源判断」之后并被其保护"
    )


def test_require_api_key_exposes_credential_source():
    """require_api_key 必须把凭据来源放进 request.state，供上层按来源决策。"""
    source = DEPENDENCIES.read_text(encoding="utf-8")
    body = source[source.index("async def require_api_key") :]
    body = body[: body.index("async def ", 10)] if "async def " in body[10:] else body

    assert "state.credential_source" in body, (
        "require_api_key 未暴露凭据来源，调用方只能退回去做不可靠的值比较"
    )
    for marker in ('"header"', '"cookie:portal_session"', '"cookie:embed_session"'):
        assert marker in body, f"凭据来源未区分 {marker}"


def test_ticket_exchange_also_issues_embed_session_cookie():
    """ticket 兑换成功后同样下发 embed_session。

    ticket 是**一次性**凭据（`redis.getdel()` 原子核销，默认 5 分钟），刷新时它必然
    已失效；若兑换接口不下发会话 Cookie，`emb_ses_` 就只存在于 JS 内存里，刷新即丢，
    iframe 内按 F5 必然失败。因此兑换接口要与 `user_apikey` 对称地下发 Cookie。

    注意：这里放进 Cookie 的是**兑换来的会话**，而不是 ticket 本身——ticket 放哪都
    撑不过一次刷新（一次性 + 5 分钟时效）。
    """
    source = EMBED_PY.read_text(encoding="utf-8")
    fn_start = source.index("async def exchange_embed_ticket")
    fn_body = source[fn_start : fn_start + 1200]

    assert "response: Response" in fn_body, (
        "exchange_embed_ticket 未接收 Response 参数，无法下发 Cookie"
    )
    assert "_set_embed_session_cookie(" in fn_body, (
        "ticket 兑换未下发 embed_session：ticket 一次性且已核销，刷新将无凭据可用"
    )


def test_session_cookie_is_renewed_on_cookie_authenticated_requests():
    """经 Cookie 认证的请求必须重新下发 Cookie，令 max_age 顺延。

    服务端会话会随活跃调用滑动续期（`session_type in ("embed", "portal")` 都会
    `expire` 回 24 小时），但 Cookie 的 `max_age` 自**下发那一刻**固定计算，二者
    必然脱节：连续使用满 24 小时后，服务端会话仍有效，浏览器却已丢弃 Cookie，
    刷新会无故要求重新登录。因此在每个经 Cookie 认证的请求上重新下发即可对齐。

    注意必须**仅对 Cookie 来源**的凭据续期：header 传来的凭据不应被写进 Cookie。
    """
    source = DEPENDENCIES.read_text(encoding="utf-8")
    fn_start = source.index("async def require_api_key")
    fn_body = source[fn_start : fn_start + 3000]

    assert "response: Response" in fn_body, (
        "require_api_key 未接收 Response，无法重新下发 Cookie"
    )
    assert "cookie_name" in fn_body, (
        "缺少凭据来源标记：无法区分「来自 Cookie」与「来自 header」，"
        "会导致 header 凭据也被写进 Cookie"
    )
    assert "response.set_cookie" in fn_body or "_renew_session_cookie(" in fn_body, (
        "require_api_key 未重新下发 Cookie：Cookie max_age 不会随会话滑动续期顺延"
    )


def test_logout_revokes_and_clears_embed_session_cookie():
    """登出必须同时处理 embed_session。

    嵌入页刷新后只带 `embed_session`（没有 `portal_session`）。若登出只认 `portal_session`
    且只清它：服务端嵌入会话不会被吊销、Cookie 也不会被删；而 `require_api_key` 又会
    回落到这个 Cookie，于是门户登出后请求仍以嵌入用户身份通过 —— 身份串号。
    """
    source = AUTH_PY.read_text(encoding="utf-8")
    fn_start = source.index("async def logout")
    fn_body = source[fn_start : fn_start + 1600]

    assert "cookies.get(EMBED_SESSION_COOKIE_NAME)" in fn_body, (
        "logout 未读取 embed_session，登出时无法吊销它"
    )
    assert "delete_cookie(key=EMBED_SESSION_COOKIE_NAME)" in fn_body, (
        "logout 未删除 embed_session Cookie，它会一直残留并可在门户请求中生效"
    )
    assert "AuthService.EMBED_SESSION_PREFIX" in fn_body, (
        "logout 未把 emb_ses_ 当作会话令牌吊销（会被误当真实 Key 处理）"
    )

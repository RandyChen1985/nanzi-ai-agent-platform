"""忘记密码（邮件找回）行为测试。

守卫重点（每一条都对应设计文档里一处「写错了也不会被发现」的地方）：
- 防枚举：不存在 / 待审核 / 已禁用 / 被限流的响应必须与成功路径逐字节相同；
- token 一次性：`GETDEL` 原子核销，并发只有一个能成功；
- 新申请作废旧链接：签发即覆盖写；
- **校验先于核销**：密码不合规不能把链接烧掉，否则用户得重走一遍邮件流程。

测试造数复用 `tests/test_user_email_and_mail_service.py` 里已验证过的写法：
`AuthService.generate_api_key` 自身落库并只返回明文 API Key，`_drop_user` 负责清理。
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.password_reset_service import (
    INVALID_TOKEN_MESSAGE,
    UNIFIED_MESSAGE,
    PasswordResetService,
)

USER_STATUS_ENABLED = 1
USER_STATUS_DISABLED = 0
USER_STATUS_PENDING = 2


class FakeRedis:
    """最小 Redis 替身：只需要 get/set/getdel/del/incr/expire/ttl。

    刻意不引入 fakeredis：本文件要断言的正是「调了哪个命令、TTL 是多少、
    键名里有没有明文邮箱」这类细节，自己实现反而更直白。
    """

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.getdel_calls = 0
        # 记录所有出现过的键，供「键名不得含明文邮箱」这类断言使用
        self.seen_keys: list[str] = []

    def _remember(self, key):
        self.seen_keys.append(key)
        return key

    async def set(self, key, value, ex=None, nx=False):
        self._remember(key)
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def get(self, key):
        self._remember(key)
        return self.data.get(key)

    async def getdel(self, key):
        self._remember(key)
        self.getdel_calls += 1
        return self.data.pop(key, None)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            self._remember(key)
            if self.data.pop(key, None) is not None:
                removed += 1
        return removed

    async def incr(self, key):
        self._remember(key)
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    async def expire(self, key, seconds):
        self._remember(key)
        self.ttls[key] = int(seconds)
        return True

    async def ttl(self, key):
        self._remember(key)
        return self.ttls.get(key, -1)


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _seed_with_status(user_name: str, email: str, status: int):
    """建一个指定状态的用户，返回 (uid, email)。

    注意 `generate_api_key` 的返回语义：它内部生成 key/encrypted/hash 并自行落库，
    只把**明文 key** 返回给调用方；这里不需要 key，所以忽略返回值。
    """
    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="找回密码测试", status=status, email=email, db=s
        )
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()
        return uid, email


async def _drop_user(user_name: str) -> None:
    async with AsyncSessionLocal() as s:
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar()
        if uid is not None:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


@pytest.fixture
async def enabled_account():
    """启用状态、带邮箱的临时账号。"""
    name = _name("zreset_en")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_ENABLED
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)


@pytest.fixture
async def disabled_account():
    name = _name("zreset_dis")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_DISABLED
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)


@pytest.fixture
async def pending_account():
    name = _name("zreset_pend")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_PENDING
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)


# --------------------------------------------------------------------------- #
# Task 1：限流常量与键函数
# --------------------------------------------------------------------------- #

def test_password_reset_limit_constants_and_key_shapes():
    assert AuthService.PWD_RESET_TTL_SECONDS == 900
    assert AuthService.PWD_RESET_COOLDOWN_SECONDS == 60
    assert AuthService.PWD_RESET_EMAIL_LIMIT == 3
    assert AuthService.PWD_RESET_SOURCE_LIMIT == 5

    key = AuthService.pwd_reset_email_count_key("Victim@Example.com")
    assert key.startswith("auth:pwdreset:cnt:email:")
    # 键名不得出现明文邮箱：Redis 键名会出现在监控、慢日志与 KEYS 输出里
    assert "victim" not in key.lower()
    assert "example.com" not in key.lower()
    # 同一邮箱不同大小写必须落到同一个桶，否则大小写就成了绕过限流的口子
    assert key == AuthService.pwd_reset_email_count_key("victim@example.com")


# --------------------------------------------------------------------------- #
# Task 2：token 签发 / 校验 / 原子核销
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
async def test_issue_token_stores_hash_not_plaintext_and_sets_ttl():
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    uid, _, nonce = token.partition(".")
    assert uid == "7" and len(nonce) > 20

    stored = redis.data["auth:pwdreset:7"]
    assert stored != nonce, "Redis 里必须是哈希，不能是明文 nonce"
    assert stored == PasswordResetService.hash_nonce(nonce)
    assert redis.ttls["auth:pwdreset:7"] == 900, "TTL 必须是 15 分钟"


@pytest.mark.no_infrastructure
async def test_key_name_never_contains_plaintext_email():
    """签发/限流用到的键名里都不该出现明文邮箱。"""
    redis = FakeRedis()
    await PasswordResetService.issue_token(7, redis)
    await PasswordResetService.pass_rate_limits("Victim@Example.com", "1.2.3.4", redis)

    for key in redis.seen_keys:
        assert "victim" not in key.lower()
        assert "example.com" not in key.lower()


@pytest.mark.no_infrastructure
async def test_new_issue_overwrites_previous_token():
    """「再次申请会作废旧链接」必须由覆盖写天然实现，不需要读旧值。"""
    redis = FakeRedis()
    first = await PasswordResetService.issue_token(7, redis)
    second = await PasswordResetService.issue_token(7, redis)

    assert first != second
    assert await PasswordResetService.consume_token(first, redis) is None, "旧链接必须失效"
    assert await PasswordResetService.consume_token(second, redis) == 7


@pytest.mark.no_infrastructure
async def test_consume_token_is_single_use():
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.consume_token(token, redis) == 7
    assert await PasswordResetService.consume_token(token, redis) is None, "第二次必须失败"


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: "8." + t.partition(".")[2],           # 改 uid
        lambda t: t.partition(".")[0] + ".deadbeef",    # 改 nonce
        lambda t: t.replace(".", "", 1),                # 去掉分隔符
        lambda t: "",                                   # 空串
        lambda t: "abc.def",                            # uid 非数字
        lambda t: None,                                 # 非字符串
    ],
)
async def test_consume_token_rejects_tampered_tokens(mutate):
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.consume_token(mutate(token), redis) is None


@pytest.mark.no_infrastructure
async def test_verify_token_does_not_burn_it():
    """只读校验不得核销 —— 这是「密码不合规不该吃掉链接」的前提。"""
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.verify_token(token, redis) == 7
    assert redis.getdel_calls == 0, "只读校验不允许调 GETDEL"
    assert await PasswordResetService.consume_token(token, redis) == 7


# --------------------------------------------------------------------------- #
# Task 3：三层风控
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
async def test_rate_limit_cooldown_blocks_second_request():
    redis = FakeRedis()
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is True
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False
    assert redis.ttls[AuthService.pwd_reset_cooldown_key("a@x.com")] == 60


@pytest.mark.no_infrastructure
async def test_rate_limit_email_hourly_quota():
    redis = FakeRedis()
    redis.data[AuthService.pwd_reset_email_count_key("a@x.com")] = "3"
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False
    # 配额触顶时不该顺带占掉冷却窗口
    assert AuthService.pwd_reset_cooldown_key("a@x.com") not in redis.data


@pytest.mark.no_infrastructure
async def test_rate_limit_source_hourly_quota():
    redis = FakeRedis()
    redis.data[AuthService.pwd_reset_source_count_key("1.2.3.4")] = "5"
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False


@pytest.mark.no_infrastructure
async def test_rate_limit_counts_both_dimensions():
    redis = FakeRedis()
    for _ in range(2):
        # 人为清掉冷却以模拟「过了 60 秒后再次申请」
        redis.data.pop(AuthService.pwd_reset_cooldown_key("a@x.com"), None)
        assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is True

    assert redis.data[AuthService.pwd_reset_email_count_key("a@x.com")] == "2"
    assert redis.data[AuthService.pwd_reset_source_count_key("1.2.3.4")] == "2"
    assert redis.ttls[AuthService.pwd_reset_email_count_key("a@x.com")] == 3600


@pytest.mark.no_infrastructure
async def test_rate_limit_fails_open_on_redis_error():
    """Redis 单条命令异常必须 fail-open，与注册/登录限流一致。"""

    class Broken(FakeRedis):
        async def get(self, key):
            raise RuntimeError("redis boom")

    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", Broken()) is True


# --------------------------------------------------------------------------- #
# Task 4：可用性、链接与邮件正文
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
async def test_availability_reports_mail_service_off(monkeypatch):
    from app.services import email_delivery_service as edmod

    async def _none():
        return None

    monkeypatch.setattr(edmod.EmailDeliveryService, "load_global_smtp_settings", _none)
    assert await PasswordResetService.availability() == "邮件找回功能未开启，请联系管理员"


@pytest.mark.no_infrastructure
async def test_availability_reports_missing_prefix(monkeypatch):
    from app.services import email_delivery_service as edmod
    from app.services import password_reset_service as prmod

    async def _settings():
        return edmod.SmtpSettings(
            host="h", port=465, user="", password="",
            security="ssl", from_address="f@a.cn", sender_name="X",
        )

    async def _empty():
        return ""

    monkeypatch.setattr(edmod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(prmod, "get_download_url_prefix", _empty)

    reason = await PasswordResetService.availability()
    assert reason is not None and "download_url_prefix" in reason


@pytest.mark.no_infrastructure
async def test_build_link_uses_download_url_prefix(monkeypatch):
    from app.services import password_reset_service as prmod

    async def _prefix():
        return "https://portal.example.com"

    monkeypatch.setattr(prmod, "get_download_url_prefix", _prefix)

    link = await PasswordResetService.build_link(7, FakeRedis())
    assert link.startswith("https://portal.example.com/reset-password?token=7.")


@pytest.mark.no_infrastructure
async def test_build_link_returns_none_when_prefix_empty(monkeypatch):
    """空前缀会拼出邮件里点不开的相对地址，因此宁可返回 None（端点据此 503）。"""
    from app.services import password_reset_service as prmod

    async def _empty():
        return ""

    monkeypatch.setattr(prmod, "get_download_url_prefix", _empty)
    assert await PasswordResetService.build_link(7, FakeRedis()) is None


@pytest.mark.no_infrastructure
def test_reset_mail_body_mentions_ttl_and_one_time():
    subject, body = PasswordResetService.build_mail(
        "https://x/reset-password?token=7.abc"
    )
    assert "密码重置" in subject
    assert "https://x/reset-password?token=7.abc" in body
    # 分钟数从常量推导，不再各写一份字面量
    assert f"{AuthService.PWD_RESET_TTL_SECONDS // 60} 分钟" in body
    assert "一次" in body
    assert "忽略" in body


@pytest.mark.no_infrastructure
def test_ttl_minutes_agree_across_constant_mail_and_reset_page():
    """有效期分钟数在「常量 / 重置邮件 / 重置页静态说明」三处必须一致。

    这个数字本来是散在三处的字面量，改 TTL 时漏掉任何一处，用户看到的承诺就和
    实际有效期对不上——页面承诺的时间比实际更久，用户只会认为平台有 bug。
    邮件正文已改为从常量推导，重置页仍是硬编码，所以这里做一次跨层核对：
    改 TTL 而忘改前端时，这个用例会先把漏改指出来。
    """
    minutes = AuthService.PWD_RESET_TTL_SECONDS // 60
    _, body = PasswordResetService.build_mail("https://x/reset-password?token=7.abc")
    assert f"{minutes} 分钟" in body

    page = (
        Path(__file__).resolve().parents[1] / "frontend/src/views/ResetPassword.vue"
    ).read_text(encoding="utf-8")
    assert f"重置链接有效期 {minutes} 分钟，且只能使用一次。" in page, (
        "重置页静态说明里的有效期与后端 TTL 不一致"
    )


# --------------------------------------------------------------------------- #
# Task 5 / 6：请求侧与重置侧编排
# --------------------------------------------------------------------------- #

async def test_request_reset_only_for_enabled(
    db_session, enabled_account, disabled_account, pending_account
):
    """只有启用账号才会真发信；待审核与已禁用一律 None（静默）。

    每个邮箱用不同来源地址，避免被同一来源 5 次/小时的配额挡住而误判。
    """
    redis = FakeRedis()
    _, enabled_email = enabled_account
    _, disabled_email = disabled_account
    _, pending_email = pending_account

    hit = await PasswordResetService.request_reset(
        db_session, enabled_email, "1.2.3.4", redis
    )
    assert hit is not None and hit[1] == enabled_email

    assert await PasswordResetService.request_reset(
        db_session, disabled_email, "2.2.2.2", redis
    ) is None
    assert await PasswordResetService.request_reset(
        db_session, pending_email, "3.3.3.3", redis
    ) is None
    assert await PasswordResetService.request_reset(
        db_session, "nobody@nowhere.example.com", "4.4.4.4", redis
    ) is None


async def test_request_reset_is_case_insensitive(db_session, enabled_account):
    redis = FakeRedis()
    _, email = enabled_account
    hit = await PasswordResetService.request_reset(
        db_session, email.upper(), "1.2.3.4", redis
    )
    assert hit is not None, "邮箱唯一索引口径是大小写不敏感，找回也必须一致"


async def test_request_reset_returns_none_when_rate_limited(db_session, enabled_account):
    redis = FakeRedis()
    _, email = enabled_account
    first = await PasswordResetService.request_reset(db_session, email, "1.2.3.4", redis)
    assert first is not None
    second = await PasswordResetService.request_reset(db_session, email, "1.2.3.4", redis)
    assert second is None, "冷却窗口内不得再次发信"


async def test_request_reset_consumes_limits_even_for_unknown_email(db_session):
    """限流必须在查库之前生效，且对不存在的邮箱同样计数。

    若先查库、只在命中时才计数，「计数有没有被消耗」本身就变成了邮箱是否存在的观测点。
    """
    redis = FakeRedis()
    assert await PasswordResetService.request_reset(
        db_session, "nobody@nowhere.example.com", "9.9.9.9", redis
    ) is None
    assert redis.data.get(AuthService.pwd_reset_cooldown_key("nobody@nowhere.example.com"))


async def test_reset_password_rejects_weak_password_without_burning_token(
    db_session, enabled_account
):
    """密码不合规 → 给出具体原因，且 token 仍然可用（守住「校验先于核销」）。"""
    redis = FakeRedis()
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    ok, message = await PasswordResetService.reset_password(
        token, "short", db_session, redis
    )
    assert ok is False
    assert message != INVALID_TOKEN_MESSAGE, "密码问题是可修复错误，应给出具体原因"
    assert await PasswordResetService.verify_token(token, redis) == uid, "链接不能被烧掉"

    ok, _ = await PasswordResetService.reset_password(
        token, "Str0ng#Passw0rd", db_session, redis
    )
    assert ok is True


async def test_reset_password_invalid_token_message_is_unified(db_session):
    """过期 / 已用 / 被覆盖 / 账号不可用 —— 必须共用同一句文案。"""
    redis = FakeRedis()
    for bad in ("", "abc", "999.deadbeef", "1.not-the-nonce", None):
        ok, message = await PasswordResetService.reset_password(
            bad, "Str0ng#Passw0rd", db_session, redis
        )
        assert ok is False
        assert message == INVALID_TOKEN_MESSAGE


async def test_reset_password_refuses_non_enabled_account_and_burns_nothing(
    db_session, disabled_account
):
    """账号被禁用时链接不可用，但也不该在核销前就被消耗 —— 这里断言「失败 + 未核销」。"""
    redis = FakeRedis()
    uid, _ = disabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    ok, message = await PasswordResetService.reset_password(
        token, "Str0ng#Passw0rd", db_session, redis
    )
    assert ok is False
    assert message == INVALID_TOKEN_MESSAGE
    assert redis.getdel_calls == 0, "状态校验失败发生在核销之前"


async def test_reset_password_consumes_token_and_revokes_sessions(
    db_session, enabled_account, monkeypatch
):
    redis = FakeRedis()
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    revoked = []

    async def _revoke(user_id, redis=None):
        revoked.append(int(user_id))
        return 1

    monkeypatch.setattr(AuthService, "revoke_sessions_for_user", staticmethod(_revoke))

    ok, message = await PasswordResetService.reset_password(
        token, "Str0ng#Passw0rd", db_session, redis
    )
    assert ok is True
    assert "重置成功" in message
    assert revoked == [uid], "重置成功后必须吊销该用户全部会话"
    # 一次性：同一条链接不能再用
    assert await PasswordResetService.consume_token(token, redis) is None


async def test_reset_password_audit_log_has_no_secrets(
    db_session, enabled_account, monkeypatch, caplog
):
    redis = FakeRedis()
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    async def _noop(user_id, redis=None):
        return 0

    monkeypatch.setattr(AuthService, "revoke_sessions_for_user", staticmethod(_noop))

    password = "Str0ng#Passw0rd"
    with caplog.at_level("INFO"):
        await PasswordResetService.reset_password(token, password, db_session, redis)

    text = caplog.text
    assert "password_reset_succeeded" in text
    assert password not in text, "审计日志绝不能出现明文密码"
    assert token not in text, "审计日志绝不能出现 token"




# --------------------------------------------------------------------------- #
# Task 7 / 8：HTTP 层
# --------------------------------------------------------------------------- #

RESET_REQUEST_URL = "/api/portal/auth/password-reset/request"
RESET_CONFIRM_URL = "/api/portal/auth/password-reset/confirm"
RESET_VERIFY_URL = "/api/portal/auth/password-reset/verify"
PUBLIC_CONFIG_URL = "/api/portal/auth/config/public"
PORTAL_PREFIX = "https://portal.example.com"


def _stub_mail(monkeypatch, *, available=True, prefix=PORTAL_PREFIX):
    """把「邮件服务可用性 + 对外地址 + 实际发信」变成显式可控，返回发信捕获列表。

    patch 目标只有一处：`password_reset_service` 模块名下的 `get_download_url_prefix`
    （availability 与 build_link 都经它导入）。**不要**分别去 patch 端点里的局部导入，
    否则会出现「patch 了不生效、用例永远通过」的假绿。
    """
    from app.services import email_delivery_service as edmod
    from app.services import password_reset_service as prmod

    captured: list = []

    async def _settings():
        if not available:
            return None
        return edmod.SmtpSettings(
            host="g.smtp.cn", port=465, user="g@a.cn", password="pw",
            security="ssl", from_address="g@a.cn", sender_name="平台",
        )

    def _send(smtp, to, subject, body, **kwargs):
        captured.append({"to": list(to), "subject": subject, "body": body})
        return True, ""

    async def _prefix():
        return prefix

    monkeypatch.setattr(edmod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))
    monkeypatch.setattr(prmod, "get_download_url_prefix", _prefix)
    return captured


def _stub_redis(monkeypatch, redis):
    """替换端点模块里的 get_redis；传 None 即模拟 Redis 不可用。"""

    async def _get():
        return redis

    monkeypatch.setattr("app.api.portal.endpoints.auth.get_redis", _get)


@pytest.mark.no_infrastructure
def test_request_endpoint_schedules_mail_as_background_task():
    """发信必须在响应之后进行。

    TestClient 会等 BackgroundTasks 执行完才把响应交给测试，所以「响应快慢」在这里
    观测不到；改为对机制本身做契约断言：端点必须把发信**登记**为 background task，
    而不是直接 await。否则 SMTP（同步阻塞、10 秒超时）的耗时就变成了「邮箱是否存在」
    的旁路信道。
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app/api/portal/endpoints/auth.py"
    text = src.read_text(encoding="utf-8")
    body = text[text.index("def request_password_reset") : text.index("def confirm_password_reset")]
    assert "background_tasks.add_task(PasswordResetService.deliver" in body
    assert "await PasswordResetService.deliver" not in body


async def test_request_endpoint_is_uniform_for_every_email(
    client, enabled_account, disabled_account, pending_account, monkeypatch
):
    """防枚举核心：不存在 / 待审核 / 已禁用 / 启用命中的响应必须逐字节相同。

    四个邮箱各自 60 秒冷却（按邮箱分桶），同一来源共 4 次未触顶 5 次/小时，
    因此这一串请求不会被限流干扰。
    """
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())

    _, enabled_email = enabled_account
    _, disabled_email = disabled_account
    _, pending_email = pending_account

    bodies = []
    for email in ("nobody@nowhere.example.com", pending_email, disabled_email, enabled_email):
        resp = await client.post(RESET_REQUEST_URL, json={"email": email})
        assert resp.status_code == 200, resp.text
        bodies.append(resp.text)
    assert len(set(bodies)) == 1, "不同邮箱的响应体必须完全一致"


async def test_request_endpoint_rate_limited_response_is_identical(
    client, enabled_account, monkeypatch
):
    """被限流的响应也必须一模一样。

    这是最容易写错的一条：如果限流时回「操作过于频繁」，攻击者就得到了
    「这个邮箱刚刚被申请过」的信号，等于确认邮箱已注册。
    """
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())
    _, email = enabled_account

    first = await client.post(RESET_REQUEST_URL, json={"email": email})
    second = await client.post(RESET_REQUEST_URL, json={"email": email})

    assert first.status_code == 200
    assert second.status_code == 200, f"被限流不能返回错误码：{second.text}"
    assert first.text == second.text, "被限流的响应体必须与正常路径逐字节相同"


async def test_request_endpoint_503_when_mail_disabled(client, monkeypatch):
    """与邮箱无关的平台级原因 → 可以明确报错，不构成枚举泄露。"""
    _stub_mail(monkeypatch, available=False)
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "邮件找回功能未开启" in resp.json()["detail"]


async def test_request_endpoint_503_when_prefix_empty(client, monkeypatch):
    _stub_mail(monkeypatch, prefix="")
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "download_url_prefix" in resp.json()["detail"]


async def test_request_endpoint_503_when_redis_down(client, monkeypatch):
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, None)

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "暂时不可用" in resp.json()["detail"]


async def test_request_endpoint_rejects_malformed_email(client, monkeypatch):
    """格式非法必须是 400，不能静默 200 —— 格式只取决于输入，不泄露注册信息。"""
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "not-an-email"})
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]


async def test_request_endpoint_sends_mail_with_prefix_link(
    client, enabled_account, monkeypatch
):
    """发信发生在响应之后，且链接由 download_url_prefix 拼出。"""
    sent = _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())
    _, email = enabled_account

    resp = await client.post(RESET_REQUEST_URL, json={"email": email})
    assert resp.status_code == 200
    assert sent, "BackgroundTasks 在响应后执行；TestClient 下此刻应已完成"
    assert sent[0]["to"] == [email]
    assert f"{PORTAL_PREFIX}/reset-password?token=" in sent[0]["body"]


async def test_request_endpoint_does_not_leak_send_result(
    client, enabled_account, monkeypatch
):
    """发信失败也不能体现在响应里（否则可用「故意让收件方被拒」来探测邮箱）。"""
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())
    _, email = enabled_account

    from app.services import email_delivery_service as edmod

    def _boom(smtp, to, subject, body, **kwargs):
        raise RuntimeError("smtp refused")

    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_boom))

    resp = await client.post(RESET_REQUEST_URL, json={"email": email})
    assert resp.status_code == 200, "发信异常不得冒泡成 500"
    assert resp.json()["message"] == UNIFIED_MESSAGE


async def test_public_config_exposes_password_reset_flag(client, monkeypatch):
    _stub_mail(monkeypatch)
    resp = await client.get(PUBLIC_CONFIG_URL)
    assert resp.status_code == 200
    assert resp.json()["data"]["password_reset_available"] is True


async def test_public_config_hides_entry_when_prefix_missing(client, monkeypatch):
    """入口可见性与接口可用性必须同源，否则会出现「入口显示了但一点就 503」。"""
    _stub_mail(monkeypatch, prefix="")
    resp = await client.get(PUBLIC_CONFIG_URL)
    assert resp.json()["data"]["password_reset_available"] is False

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503


async def test_confirm_endpoint_rejects_bad_token_and_accepts_good_one(
    client, enabled_account, monkeypatch
):
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account

    bad = await client.post(
        RESET_CONFIRM_URL, json={"token": "999.deadbeef", "password": "Str0ng#Passw0rd"}
    )
    assert bad.status_code == 400
    assert INVALID_TOKEN_MESSAGE in bad.json()["detail"]

    token = await PasswordResetService.issue_token(uid, redis)
    ok = await client.post(
        RESET_CONFIRM_URL, json={"token": token, "password": "Str0ng#Passw0rd"}
    )
    assert ok.status_code == 200, ok.text
    assert "重置成功" in ok.json()["message"]


async def test_confirm_endpoint_reports_password_rule_specifically(
    client, enabled_account, monkeypatch
):
    """密码不合规要给出具体原因（可修复错误），且链接不能因此被吃掉。"""
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    weak = await client.post(
        RESET_CONFIRM_URL, json={"token": token, "password": "aaaaaaaa"}
    )
    assert weak.status_code == 400
    assert INVALID_TOKEN_MESSAGE not in weak.json()["detail"]

    ok = await client.post(
        RESET_CONFIRM_URL, json={"token": token, "password": "Str0ng#Passw0rd"}
    )
    assert ok.status_code == 200, "密码不合规不该消耗链接"


async def test_confirm_endpoint_503_when_redis_down(client, monkeypatch):
    _stub_redis(monkeypatch, None)
    resp = await client.post(
        RESET_CONFIRM_URL, json={"token": "1.abc", "password": "Str0ng#Passw0rd"}
    )
    assert resp.status_code == 503


async def test_change_password_revokes_all_sessions(
    client, valid_api_key, db_session, monkeypatch
):
    """改密必须让所有设备重新登录：否则旧密码泄露后改密也踢不掉对方。"""
    revoked = []

    async def _revoke(user_id, redis=None):
        revoked.append(int(user_id))
        return 1

    monkeypatch.setattr(AuthService, "revoke_sessions_for_user", staticmethod(_revoke))

    resp = await client.put(
        "/api/portal/auth/password",
        json={"password": "Str0ng#Passw0rd"},
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.text

    async with AsyncSessionLocal() as s:
        expected = (
            await s.execute(select(User.id).where(User.user_name == "test_user"))
        ).scalar_one()
    assert revoked == [expected], "改密后必须吊销该用户的全部会话"


# --------------------------------------------------------------------------- #
# Task 7：进页面预校验（只读，不核销）
#
# 这个端点的全部意义是让重置页在用户填密码之前就知道链接还能不能用。因此最关键的
# 不是某个分支的对错，而是它与 confirm 的判定**必须一致**：只要有一边宽松，
# 用户就会遇到「页面显示正常、点提交却说失效」，那比不做预校验更让人困惑。
# --------------------------------------------------------------------------- #

async def test_verify_endpoint_reports_valid_and_does_not_consume(
    client, enabled_account, monkeypatch
):
    """预校验必须是只读的：校验完用户还得用这条链接改密码。"""
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    resp = await client.get(RESET_VERIFY_URL, params={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"valid": True}

    # 关键：预校验不能把 token 烧掉，否则预览一下就再也改不了密码
    assert await PasswordResetService.verify_token(token, redis) == uid


async def test_verify_endpoint_says_invalid_without_saying_why(
    client, enabled_account, monkeypatch
):
    """失效时只回 valid=false，不区分原因——与 confirm 同一套防探测口径。"""
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    # 已核销（等价于已被使用）：键消失
    await redis.getdel(f"auth:pwdreset:{uid}")

    used = await client.get(RESET_VERIFY_URL, params={"token": token})
    assert used.status_code == 200
    assert used.json() == {"valid": False}

    for bad in ("999.deadbeef", "not-a-token", ""):
        resp = await client.get(RESET_VERIFY_URL, params={"token": bad})
        assert resp.status_code == 200, bad
        assert resp.json() == {"valid": False}, bad


async def test_verify_endpoint_matches_confirm_for_unusable_accounts(
    client, disabled_account, pending_account, monkeypatch
):
    """禁用／待审核账号的链接一律不可用，判定必须与 reset_password 逐条一致。"""
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)

    for account in (disabled_account, pending_account):
        uid, _ = account
        token = await PasswordResetService.issue_token(uid, redis)
        resp = await client.get(RESET_VERIFY_URL, params={"token": token})
        assert resp.status_code == 200
        assert resp.json() == {"valid": False}, f"账号状态 {uid} 应判为不可用"


async def test_precheck_agrees_with_confirm_outcome(
    client, enabled_account, monkeypatch
):
    """预校验说有效 → 提交就必须能成功；用完之后预校验立刻转为无效。"""
    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    pre = await client.get(RESET_VERIFY_URL, params={"token": token})
    assert pre.json() == {"valid": True}

    ok = await client.post(
        RESET_CONFIRM_URL, json={"token": token, "password": "Str0ng#Passw0rd"}
    )
    assert ok.status_code == 200, ok.text

    after = await client.get(RESET_VERIFY_URL, params={"token": token})
    assert after.json() == {"valid": False}, "链接用过之后必须立刻显示失效"


async def test_verify_endpoint_503_when_redis_down(client, monkeypatch):
    """Redis 不可用要回 503：前端才能显示「稍后重试」而不是「链接已失效」。"""
    _stub_redis(monkeypatch, None)
    resp = await client.get(RESET_VERIFY_URL, params={"token": "1.abc"})
    assert resp.status_code == 503

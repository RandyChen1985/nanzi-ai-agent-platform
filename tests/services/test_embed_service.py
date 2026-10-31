"""Embed 凭据链路单测：签发 / 兑换 / 来源白名单 / 绝对寿命。

这条链路此前**没有任何单元测试**——一次性核销、来源白名单、代他人权限全靠人工 review。
这里用内存 FakeRedis + 假 DB，覆盖那些「一旦写错就是安全漏洞」的分支。

覆盖点：
1. origin 归一化（Origin / Referer / 带斜杠配置 / 无 scheme）；
2. 白名单的放行与拒绝，尤其是「配置了白名单但请求没有 Origin」必须拒绝；
3. 兑换的一次性（GETDEL）与「来源不匹配不消耗票据」；
4. 兑换出的会话带 `issued_at`（绝对寿命的锚点）与回传 `allowed_origins`；
5. 签发时的 TTL 截断与代他人权限。
"""

from __future__ import annotations

import json

import pytest

from app.services import embed_service
from app.services.embed_service import (
    EmbedService,
    TICKET_TTL_SECONDS,
    normalize_origin,
    origin_allowed,
)

pytestmark = pytest.mark.no_infrastructure


# --------------------------------------------------------------------------- #
# 最小 FakeRedis：只实现 EmbedService 用到的命令
# --------------------------------------------------------------------------- #
class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key, value, ex=None):
        self.strings[key] = value if isinstance(value, str) else value.decode()
        if ex is not None:
            self.expires[key] = int(ex)
        return True

    async def get(self, key):
        return self.strings.get(key)

    async def getdel(self, key):
        return self.strings.pop(key, None)

    async def hset(self, key, mapping=None, **kwargs):
        bucket = self.hashes.setdefault(key, {})
        for field, value in (mapping or {}).items():
            bucket[field] = value if isinstance(value, str) else str(value)
        return len(mapping or {})

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, seconds):
        self.expires[key] = int(seconds)
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.strings, self.hashes, self.sets):
                if key in store:
                    store.pop(key, None)
                    removed += 1
        return removed

    async def sadd(self, key, *values):
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def srem(self, key, *values):
        bucket = self.sets.get(key, set())
        removed = len(bucket & set(values))
        bucket.difference_update(values)
        return removed


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, user=None) -> None:
        self.user = user
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        return FakeResult(self.user)


class FakeUser:
    def __init__(self, **kwargs) -> None:
        self.id = kwargs.get("id", 7)
        self.user_name = kwargs.get("user_name", "alice")
        self.real_name = kwargs.get("real_name", "Alice")
        self.role = kwargs.get("role", "user")
        self.status = kwargs.get("status", 1)
        self.dept_code = kwargs.get("dept_code", "D1")
        self.org_path = kwargs.get("org_path", "/D1")
        self.extra_data = kwargs.get("extra_data", "")


@pytest.fixture()
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(embed_service, "get_redis", _returner(redis))
    # 部门名查询要连库，测试里固定降级为空（签发的容错分支本来也允许）
    from app.services.department_service import DepartmentService

    async def _label(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(DepartmentService, "department_label_for_user", staticmethod(_label))
    return redis


def _returner(value):
    async def _inner():
        return value

    return _inner


def _seed_ticket(redis: FakeRedis, **overrides) -> str:
    ticket = overrides.pop("ticket", "emt_test-ticket")
    payload = {
        "ticket": ticket,
        "user_id": "7",
        "user_name": "alice",
        "real_name": "Alice",
        "role": "user",
        "dept_code": "D1",
        "org_path": "/D1",
        "dept_name": "研发部",
        "extra_data": "",
        "agent_id": "",
        "allowed_origins": "[]",
        "created_by_user_id": "7",
    }
    payload.update(overrides)
    redis.strings[f"embed:ticket:{ticket}"] = json.dumps(payload)
    return ticket


# --------------------------------------------------------------------------- #
# 1. 归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://crm.example.com", "https://crm.example.com"),
        ("https://crm.example.com/", "https://crm.example.com"),
        ("https://CRM.Example.com", "https://crm.example.com"),
        ("https://crm.example.com/page/1?x=1", "https://crm.example.com"),
        ("crm.example.com", "https://crm.example.com"),
        ("http://localhost:5173/", "http://localhost:5173"),
        ("*", "*"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_origin(raw, expected):
    assert normalize_origin(raw) == expected


def test_origin_allowed_matrix():
    # 未配置白名单：保持历史行为，放行
    assert origin_allowed("https://any.example.com", "[]") is True
    assert origin_allowed(None, None) is True
    # 配了白名单：命中放行（含 Referer 带路径的形态）
    allowed = json.dumps(["https://crm.example.com"])
    assert origin_allowed("https://crm.example.com", allowed) is True
    assert origin_allowed("https://crm.example.com/deal/42", allowed) is True
    # 未命中拒绝
    assert origin_allowed("https://evil.example.com", allowed) is False
    # 配置了白名单但请求没有来源：必须拒绝（旧实现这里是「跳过校验」）
    assert origin_allowed(None, allowed) is False
    assert origin_allowed("", allowed) is False
    # 通配
    assert origin_allowed("https://whatever.example.com", json.dumps(["*"])) is True
    # 配置项带斜杠也要能命中
    assert origin_allowed("https://crm.example.com", json.dumps(["https://crm.example.com/"])) is True


# --------------------------------------------------------------------------- #
# 2. 签发
# --------------------------------------------------------------------------- #
async def _create(fake_redis, operator=None, user=None, **kwargs):
    operator = operator or {"user_id": 7, "user_name": "alice", "role": "user"}
    db = FakeDB(user or FakeUser())
    return await EmbedService.create_ticket(operator_user=operator, db=db, **kwargs)


async def test_create_ticket_self_does_not_need_delegation_permission(fake_redis, monkeypatch):
    from app.services.permission_service import PermissionService

    async def _deny(_self, *_args, **_kwargs):
        raise AssertionError("自签不应查询代他人权限")

    monkeypatch.setattr(PermissionService, "check_permission", _deny)

    data = await _create(fake_redis)
    assert data["ticket"].startswith("emt_")
    assert data["expires_in"] == TICKET_TTL_SECONDS
    stored = json.loads(fake_redis.strings[f"embed:ticket:{data['ticket']}"])
    assert stored["user_id"] == "7"
    assert stored["allowed_origins"] == "[]"


async def test_create_ticket_rejects_delegation_without_permission(fake_redis, monkeypatch):
    from app.services.permission_service import PermissionService

    async def _deny(_self, *_args, **_kwargs):
        return False

    monkeypatch.setattr(PermissionService, "check_permission", _deny)

    with pytest.raises(PermissionError):
        await _create(fake_redis, target_username="bob")


async def test_create_ticket_allows_admin_delegation(fake_redis):
    operator = {"user_id": 1, "user_name": "root", "role": "admin"}
    data = await _create(fake_redis, operator=operator, target_username="alice")
    assert data["target_user"]["user_name"] == "alice"


async def test_create_ticket_rejects_disabled_target(fake_redis):
    with pytest.raises(PermissionError):
        await _create(fake_redis, user=FakeUser(status=0))


async def test_create_ticket_clamps_ttl_and_normalizes_origins(fake_redis):
    data = await _create(
        fake_redis,
        expires_in=999999,
        allowed_origins=["https://crm.example.com/", "CRM.example.com", ""],
    )
    assert data["expires_in"] == 1800  # 上限 30 分钟
    stored = json.loads(fake_redis.strings[f"embed:ticket:{data['ticket']}"])
    assert json.loads(stored["allowed_origins"]) == ["https://crm.example.com"]


# --------------------------------------------------------------------------- #
# 3. 兑换
# --------------------------------------------------------------------------- #
async def test_exchange_issues_session_with_issued_at_and_allowed_origins(fake_redis):
    ticket = _seed_ticket(fake_redis, allowed_origins=json.dumps(["https://crm.example.com"]))

    data = await EmbedService.exchange_ticket(ticket=ticket, origin="https://crm.example.com")

    assert data["session_token"].startswith("emb_ses_")
    assert data["user_info"]["user_name"] == "alice"
    assert data["allowed_origins"] == ["https://crm.example.com"]

    cache_key = f"auth:api_key:{embed_service.get_api_key_manager().hash_api_key(data['session_token'])}"
    cached = fake_redis.hashes[cache_key]
    assert cached["session_type"] == "embed"
    assert cached["issued_at"].isdigit()
    assert int(fake_redis.expires[cache_key]) == embed_service.SESSION_TOKEN_TTL_SECONDS
    # 会话被登记到用户索引，供批量吊销
    assert cache_key in fake_redis.sets["auth:user_sessions:7"]


async def test_exchange_is_one_time(fake_redis):
    ticket = _seed_ticket(fake_redis)
    await EmbedService.exchange_ticket(ticket=ticket, origin=None)
    with pytest.raises(ValueError):
        await EmbedService.exchange_ticket(ticket=ticket, origin=None)


async def test_exchange_rejects_origin_but_keeps_ticket_usable(fake_redis):
    """来源不匹配是「可修复」错误：不能把票白吃掉，换正确来源重试必须还能兑。"""
    ticket = _seed_ticket(fake_redis, allowed_origins=json.dumps(["https://crm.example.com"]))

    with pytest.raises(PermissionError):
        await EmbedService.exchange_ticket(ticket=ticket, origin="https://evil.example.com")

    # 票仍在
    assert f"embed:ticket:{ticket}" in fake_redis.strings

    data = await EmbedService.exchange_ticket(ticket=ticket, origin="https://crm.example.com")
    assert data["session_token"]


async def test_exchange_requires_origin_when_whitelist_configured(fake_redis):
    ticket = _seed_ticket(fake_redis, allowed_origins=json.dumps(["https://crm.example.com"]))
    with pytest.raises(PermissionError):
        await EmbedService.exchange_ticket(ticket=ticket, origin=None)


async def test_exchange_accepts_referer_with_path(fake_redis):
    """`Referer` 是带路径的完整 URL，白名单比较必须归一化后再比。"""
    ticket = _seed_ticket(fake_redis, allowed_origins=json.dumps(["https://crm.example.com"]))
    data = await EmbedService.exchange_ticket(
        ticket=ticket, origin="https://crm.example.com/deal/42?tab=chat"
    )
    assert data["session_token"]


async def test_exchange_rejects_bad_prefix(fake_redis):
    with pytest.raises(ValueError):
        await EmbedService.exchange_ticket(ticket="not-a-ticket", origin=None)


async def test_persisted_session_is_indexed_for_revocation(fake_redis):
    ticket = _seed_ticket(fake_redis)
    await EmbedService.exchange_ticket(ticket=ticket, origin=None)
    assert fake_redis.sets["auth:user_sessions:7"], "会话必须可被按用户批量吊销"

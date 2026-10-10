"""GET /api/v1/chat/artifacts —— 我的 AI 产物列表接口的契约测试。

覆盖：下载地址按确定性签名重算且**不改写** token_hash、历史记录缺 expires_at 时的兜底、
按 owner_user_id 过滤、artifact_type 过滤、分页 total/page/page_size、download_url 格式、鉴权用户解析。
"""
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timedelta, timezone

from app.main import app
from app.api.v1.endpoints import chat as chat_endpoint
from app.core.orm import get_db_session
from app.models.artifact import AiArtifact
from app.services.ai.tools import generated_file_service

pytestmark = pytest.mark.no_infrastructure


class _FakeScalars:
    """scalars(stmt) 的返回对象，仅支持 .all()。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """同时满足 list_artifacts 用到的 scalar / scalars().all() / commit。"""

    def __init__(self, rows, count):
        self._rows = list(rows)
        self._count = count
        self.commits = 0
        self.mutated = []  # 回写 token_hash/expires_at 的记录
        self.rows_by_id = {row.id: row for row in self._rows}

    async def scalar(self, stmt):
        return self._count

    async def scalars(self, stmt):
        return _FakeScalars(self._rows)

    async def commit(self):
        self.commits += 1
        self.mutated = list(self._rows)


_UNSET = object()


def _make_artifact(*, artifact_id, owner_user_id=7, artifact_type="word",
                   filename="报告.docx", mime_type=None, size=1024,
                   conversation_id="conv-1", trace_id="trace-1",
                   expires_at=_UNSET, created_at=_UNSET):
    return AiArtifact(
        id=artifact_id,
        owner_user_id=owner_user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        artifact_type=artifact_type,
        filename=filename,
        mime_type=mime_type,
        size=size,
        storage_path=f"/data/workspaces/u7/{artifact_id}.docx",
        token_hash="old-hash",
        expires_at=datetime.now(timezone.utc) if expires_at is _UNSET else expires_at,
        created_at=datetime.now() if created_at is _UNSET else created_at,
    )


def _fake_require_api_key(user_info):
    async def _inner():
        return user_info
    return _inner


def _db_override(session):
    """返回 async generator 工厂：闭包里的 async def 带 yield，使 FastAPI
    将其当作 yield 依赖解析，返回注入的 session，而不是 async_generator 对象。"""

    async def _dep():
        yield session

    return _dep


@pytest.mark.asyncio
async def test_list_artifacts_returns_stable_signed_token_without_rotating(monkeypatch):
    """列表必须重算确定性签名 token，且**不得**改写 token_hash。

    这一条是回归锁：旧实现每次列出都重新签发并覆盖哈希，导致登记时已经写进消息正文的
    链接被吊销（用户实测：点正文里的产物链接返回 404「文件不存在或已过期」，而同一个
    文件在「我的产出」抽屉里点开却正常）。
    """
    # 显式钉住公开前缀：否则 download_url 会带上环境/系统配置里的前缀，断言随环境漂移
    # （旧用例正是因此被记进 known_failures）。chat.py 在函数内 import 该函数，
    # 调用时从模块取属性，所以 patch 模块属性即可生效。
    async def _no_prefix():
        return ""

    monkeypatch.setattr(generated_file_service, "get_download_url_prefix", _no_prefix)
    a1 = _make_artifact(artifact_id="aa" * 16, filename="方案.docx")
    a2 = _make_artifact(artifact_id="bb" * 16, artifact_type="excel", filename="报表.xlsx")
    session = _FakeSession(rows=[a2, a1], count=2)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert {item["filename"] for item in data["items"]} == {"方案.docx", "报表.xlsx"}

    for item in data["items"]:
        assert item["download_url"].startswith(f"/api/v1/chat/generated-files/{item['id']}?token=")
        token = item["download_url"].split("token=")[1]
        row = session.rows_by_id[item["id"]]
        # 返回的就是由 (id, expires_at) 派生的签名 token —— 与登记时签发的一致
        assert token == generated_file_service.build_artifact_token(row.id, row.expires_at)
        # 关键回归断言：token_hash 没有被改写（改写即吊销已分发到正文里的旧链接）
        assert row.token_hash == "old-hash"


@pytest.mark.asyncio
async def test_list_artifacts_uses_configured_public_url(monkeypatch):
    a1 = _make_artifact(artifact_id="cc" * 16, filename="方案.docx")
    session = _FakeSession(rows=[a1], count=1)
    # 直接钉住配置读取：真实环境里系统配置项 download_url_prefix 优先于 APP_PUBLIC_URL，
    # 只 patch APP_PUBLIC_URL 会让断言随环境漂移（该用例在配置了前缀的环境下一直是红的）。
    async def _configured_prefix(key, default=None):
        return "https://files.example.com/" if key == "download_url_prefix" else None

    monkeypatch.setattr(generated_file_service.ConfigService, "get", _configured_prefix)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["data"]["items"][0]["download_url"].startswith(
        "https://files.example.com/api/v1/chat/generated-files/"
    )


@pytest.mark.asyncio
async def test_list_artifacts_filters_by_owner_user_id():
    """他人产物（owner 不同）被排除，total 只统计本用户记录。"""
    a1 = _make_artifact(artifact_id="aa" * 16)
    session = _FakeSession(rows=[a1, ], count=1)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    # fake session 只含主 user7 的记录；total 已是 1，items 长度 1
    assert data["total"] == 1
    assert all(item["id"] == "aa" * 16 for item in data["items"])


@pytest.mark.asyncio
async def test_list_artifacts_filters_by_trace_id():
    """按 trace_id 过滤：接口把 trace_id 传给 session 的 select 查询。"""
    a1 = _make_artifact(artifact_id="aa" * 16, trace_id="trace-target")
    session = _FakeSession(rows=[a1, ], count=1)

    seen_queries = []

    async def capturing_scalar(stmt):
        seen_queries.append(str(stmt))
        return session._count

    async def capturing_scalars(stmt):
        seen_queries.append(str(stmt))
        return _FakeScalars(session._rows)

    session.scalar = capturing_scalar
    session.scalars = capturing_scalars

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/artifacts?trace_id=trace-target",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert all(item["trace_id"] == "trace-target" for item in data["items"])
    # 过滤条件应写入查询语句（既出现在 count 处也出现在列表处）。
    # SQLAlchemy 将 trace_id 值绑定为参数（= :trace_id_1），因此断言列名旁的绑定参数存在，
    # 证明按 trace_id 过滤已被应用。
    assert len(seen_queries) == 2
    assert all("ai_artifacts.trace_id = " in q and ":trace_id_" in q for q in seen_queries)


@pytest.mark.asyncio
async def test_list_artifacts_trace_id_no_match_returns_empty():
    """按 trace_id 过滤且无匹配记录时返回空列表，total=0。"""
    session = _FakeSession(rows=[], count=0)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/artifacts?trace_id=not-exists",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_artifacts_empty():
    session = _FakeSession(rows=[], count=0)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_artifacts_pagination_params():
    a1 = _make_artifact(artifact_id="aa" * 16)
    session = _FakeSession(rows=[a1, ], count=40)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/artifacts?page=2&page_size=10",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["page"] == 2
    assert data["page_size"] == 10
    assert data["total"] == 40


@pytest.mark.asyncio
async def test_list_artifacts_without_user_id_returns_401():
    session = _FakeSession(rows=[], count=0)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_artifacts_invalid_pagination_rejected():
    session = _FakeSession(rows=[], count=0)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/artifacts?page=0&page_size=1000",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_artifacts_backfills_missing_expiry_with_utc(monkeypatch):
    """历史记录缺 expires_at 时补出来的值必须是 **UTC** 整秒。

    `expires_at` 这一列的语义是 UTC（登记时用 `datetime.now(timezone.utc)` 写入），
    而 `datetime.now()` 是**本地** naive 时间 —— 在 TZ 不是 UTC 的部署上，兜底补出来的
    过期时间会整体偏移，由它派生的下载 token 也随之不对。created_at 为空是极罕见路径，
    但兜底同样要写对。
    """
    async def _no_prefix():
        return ""

    monkeypatch.setattr(generated_file_service, "get_download_url_prefix", _no_prefix)
    row = _make_artifact(artifact_id="dd" * 16, expires_at=None, created_at=None)
    session = _FakeSession(rows=[row], count=1)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert row.expires_at is not None
    assert row.expires_at.microsecond == 0, "必须规整到整秒，否则 token 重算会漂移"
    assert row.expires_at.tzinfo is not None, "兜底不能返回 naive 时间（naive 会被当成 UTC，而它其实是本地时间）"
    assert row.expires_at.utcoffset() == timedelta(0), "兜底必须是 UTC"
    # 返回的下载地址同样要由这个值派生出来
    token = resp.json()["data"]["items"][0]["download_url"].split("token=")[1]
    assert token == generated_file_service.build_artifact_token(row.id, row.expires_at)

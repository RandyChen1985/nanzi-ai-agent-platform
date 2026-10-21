"""个人 MCP 分享：快照、命名冲突、接受复制与状态流转。"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models.mcp import McpServer, McpToolCache
from app.services import mcp_share_service as svc
from app.services.mcp import mcp_auth_policy
from app.services.portal_notification_service import PortalNotificationService


class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalars(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *results):
        self._results = list(results)
        self.added = []
        self.flushed = 0

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0) if self._results else _FakeResult([])

    async def flush(self):
        self.flushed += 1

    def add(self, obj):
        self.added.append(obj)


def _notification_mock():
    return patch.object(PortalNotificationService, "create", new=AsyncMock())


def _server(**overrides) -> McpServer:
    server = McpServer(
        id="srv-1",
        server_name="mcp-private-admin-trends-hub",
        remark="趋势查询",
        sse_url="https://example.com/mcp",
        auth_headers=mcp_auth_policy.encrypt_mcp_auth_headers(
            {"Authorization": "Bearer secret-token", "X-Tenant": "tenant-a"}
        ),
        credential_mode="static",
        fixed_token_encrypted="enc-fixed-token",
        user_assertion_enabled=True,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience="mcp:srv-1",
        user_assertion_key_id="key-1",
        user_assertion_issuer="nanzi-platform",
        user_assertion_private_key_encrypted="enc-private-key",
        enabled_status=1,
        scope="personal",
        user_id=7,
    )
    for key, value in overrides.items():
        setattr(server, key, value)
    return server


def _tool(name: str = "trend_query", **overrides) -> McpToolCache:
    tool = McpToolCache(
        id=f"tool-{name}",
        server_id="srv-1",
        tool_name=name,
        tool_description="查询趋势",
        parameter_schema='{"type":"object"}',
        is_published=True,
        is_available=True,
    )
    for key, value in overrides.items():
        setattr(tool, key, value)
    return tool


def _share_row(**overrides):
    row = svc.McpServerShare(
        id="share-1",
        source_user_id=7,
        source_server_id="srv-1",
        server_name="mcp-private-admin-trends-hub",
        remark="趋势查询",
        sse_url="https://example.com/mcp",
        payload='{"version":1,"config":{"server_name":"mcp-private-admin-trends-hub","source_user_name":"admin","sse_url":"https://example.com/mcp","auth_headers":"enc:v1:abc"},"tools":[{"tool_name":"trend_query","tool_description":"查询趋势","parameter_schema":"{}","is_published":true,"is_available":true}]}',
        include_credentials=True,
        target_user_id=9,
        status="PENDING",
        message="试试",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------

def test_share_config_strips_non_authorization_credentials():
    """很多网关用 X-Api-Key 认证：只剥 Authorization 等于「以为没带、其实带了」。"""
    server = _server()
    server.auth_headers = mcp_auth_policy.encrypt_mcp_auth_headers(
        {
            "Authorization": "Bearer secret-token",
            "X-Api-Key": "api-key-123",
            "X-Auth-Token": "auth-token-456",
            "Accept": "application/json",
            "X-Tenant-Id": "tenant-a",
        }
    )

    config = svc.build_share_config(server, include_credentials=False)
    headers = mcp_auth_policy._parse_auth_headers(
        mcp_auth_policy._stored_auth_headers(config["auth_headers"])
    )

    assert set(headers) == {"Accept", "X-Tenant-Id"}, "疑似凭据头必须全部剔除，无害头保留"


def test_looks_like_credential_header_matches_common_auth_headers():
    for name in ("Authorization", "x-api-key", "X_API_KEY", "X-Auth-Token", "apiKey", "X-Signature"):
        assert mcp_auth_policy.looks_like_credential_header(name), name
    for name in ("Accept", "Content-Type", "x-tenant-id", "X-Nanzi-User-Assertion"):
        assert not mcp_auth_policy.looks_like_credential_header(name), name


def test_share_config_keeps_credentials_by_default():
    server = _server()
    config = svc.build_share_config(server, include_credentials=True)

    assert config["auth_headers"] == server.auth_headers
    assert config["fixed_token_encrypted"] == "enc-fixed-token"
    assert config["user_assertion_enabled"] is True
    assert config["user_assertion_private_key_encrypted"] == "enc-private-key"
    assert config["enabled_status"] == 1


def test_share_config_can_strip_credentials():
    config = svc.build_share_config(_server(), include_credentials=False)

    stored = mcp_auth_policy._parse_auth_headers(
        mcp_auth_policy._stored_auth_headers(config["auth_headers"])
    )
    assert "Authorization" not in stored
    assert stored == {"X-Tenant": "tenant-a"}
    assert config["fixed_token_encrypted"] is None
    assert config["user_assertion_enabled"] is False
    assert config["user_assertion_private_key_encrypted"] is None
    assert config["user_assertion_audience"] is None


def test_share_payload_carries_tool_snapshot():
    payload = svc.build_share_payload(_server(), [_tool()], include_credentials=True)

    assert payload["version"] == svc.PAYLOAD_VERSION
    assert payload["tools"] == [
        {
            "tool_name": "trend_query",
            "tool_description": "查询趋势",
            "parameter_schema": '{"type":"object"}',
            "is_published": True,
            "is_available": True,
        }
    ]


# ---------------------------------------------------------------------------
# 命名冲突
# ---------------------------------------------------------------------------

def test_shared_name_suffix_strips_prefix_by_source_account():
    """分享人账号含 '-' 时，正则只能切到第一个 '-'，知道账号就能精确切。"""
    assert svc.shared_name_suffix("mcp-private-ad-min-hcp", "ad-min") == "hcp"
    assert svc.shared_name_suffix("mcp-private-admin-hcp", "admin") == "hcp"
    # 不知道分享人时退化为正则（只切第一段）
    assert svc.shared_name_suffix("mcp-private-admin-hcp") == "hcp"
    # 手动改成裸名时原样返回
    assert svc.shared_name_suffix("hcp") == "hcp"


def test_suggested_accepted_name_uses_receiver_account():
    assert (
        svc.suggested_accepted_name("mcp-private-admin-hcp", "chenxiaolong", "admin")
        == "mcp-private-chenxiaolong-hcp"
    )
    # 没前缀的裸名也能装上接收人前缀
    assert (
        svc.suggested_accepted_name("hcp", "chenxiaolong")
        == "mcp-private-chenxiaolong-hcp"
    )
    # 接收人账号缺失时退到 user，仍保持 mcp-private- 约定
    assert svc.suggested_accepted_name("hcp", None) == "mcp-private-user-hcp"


def test_suggested_accepted_name_fits_column_width():
    long_suffix = "x" * 200
    name = svc.suggested_accepted_name(f"mcp-private-admin-{long_suffix}", "bob", "admin")
    assert len(name) <= svc.MAX_SERVER_NAME_LENGTH
    assert name.startswith("mcp-private-bob-")


async def test_accepted_server_name_appends_suffix_on_conflict():
    session = _FakeSession(_FakeResult([("srv-existing",)]), _FakeResult([]))

    name = await svc.resolve_accepted_server_name(session, "mcp-private-admin-trends-hub")

    assert name == "mcp-private-admin-trends-hub-2"


async def test_accepted_server_name_keeps_name_when_free():
    session = _FakeSession(_FakeResult([]))

    name = await svc.resolve_accepted_server_name(session, "mcp-private-admin-trends-hub")

    assert name == "mcp-private-admin-trends-hub"


# ---------------------------------------------------------------------------
# 接受 / 拒绝 / 撤回
# ---------------------------------------------------------------------------

async def test_accept_share_installs_independent_copy():
    row = _share_row()
    session = _FakeSession(_FakeResult([row]), _FakeResult([]))
    with _notification_mock():
        result = await svc.accept_share(
            session, user={"user_id": 9, "user_name": "bob"}, share_id="share-1"
        )

    server = next(obj for obj in session.added if isinstance(obj, McpServer))
    tools = [obj for obj in session.added if isinstance(obj, McpToolCache)]

    assert server.scope == "personal"
    assert server.user_id == 9
    # 名字必须换成接收人的前缀，否则看起来像分享人（admin）的资产
    assert server.server_name == "mcp-private-bob-trends-hub"
    assert server.sse_url == "https://example.com/mcp"
    assert len(tools) == 1
    assert tools[0].server_id == server.id
    # 工具标识必须换成副本自己的服务名前缀，否则同步后旧前缀的行会变孤立记录
    assert tools[0].tool_name == "mcp-private-bob-trends-hub:trend_query"

    assert row.status == "ACCEPTED"
    assert row.accepted_server_id == server.id
    assert row.payload is None
    assert result["accepted_server_id"] == server.id


def test_suggested_name_from_payload_uses_receiver_account():
    assert (
        svc._suggested_name_from_payload(_share_row(), "bob")
        == "mcp-private-bob-trends-hub"
    )
    # 终态 / 坏 payload 不猜名字
    assert svc._suggested_name_from_payload(_share_row(payload=None), "bob") is None
    assert svc._suggested_name_from_payload(_share_row(payload="{bad json"), "bob") is None


async def test_accept_share_applies_custom_name():
    """安装向导里手填的名字要原样生效（不自动加 -2）。"""
    row = _share_row()
    session = _FakeSession(_FakeResult([row]), _FakeResult([]))

    with _notification_mock():
        await svc.accept_share(
            session,
            user={"user_id": 9, "user_name": "bob"},
            share_id="share-1",
            server_name="my-own-hcp",
        )

    server = next(obj for obj in session.added if isinstance(obj, McpServer))
    assert server.server_name == "my-own-hcp"
    tools = [obj for obj in session.added if isinstance(obj, McpToolCache)]
    assert tools[0].tool_name == "my-own-hcp:trend_query"


async def test_accept_share_rejects_conflicting_custom_name():
    """用户显式输的名字撞了要报错让他改，不能偷偷加后缀。"""
    row = _share_row()
    session = _FakeSession(_FakeResult([row]), _FakeResult([("srv-existing",)]))

    with _notification_mock(), pytest.raises(svc.McpShareConflictError, match="已存在"):
        await svc.accept_share(
            session,
            user={"user_id": 9, "user_name": "bob"},
            share_id="share-1",
            server_name="taken-name",
        )
    assert [obj for obj in session.added if isinstance(obj, McpServer)] == []


async def test_accept_share_applies_remark_and_url_overrides():
    row = _share_row()
    session = _FakeSession(_FakeResult([row]), _FakeResult([]))

    with _notification_mock():
        await svc.accept_share(
            session,
            user={"user_id": 9, "user_name": "bob"},
            share_id="share-1",
            remark="我改过的备注",
            sse_url="https://new.example.com/mcp",
        )

    server = next(obj for obj in session.added if isinstance(obj, McpServer))
    assert server.remark == "我改过的备注"
    assert server.sse_url == "https://new.example.com/mcp"


async def test_accept_share_allows_clearing_remark_and_validates_url():
    row = _share_row()
    session = _FakeSession(_FakeResult([row]), _FakeResult([]))
    with _notification_mock():
        await svc.accept_share(
            session,
            user={"user_id": 9, "user_name": "bob"},
            share_id="share-1",
            remark="",
        )
    server = next(obj for obj in session.added if isinstance(obj, McpServer))
    assert server.remark is None

    bad_row = _share_row()
    bad_session = _FakeSession(_FakeResult([bad_row]), _FakeResult([]))
    with _notification_mock(), pytest.raises(svc.McpShareConflictError, match="http"):
        await svc.accept_share(
            bad_session,
            user={"user_id": 9, "user_name": "bob"},
            share_id="share-1",
            sse_url="ftp://example.com/mcp",
        )


async def test_accept_share_rejects_when_already_handled():
    row = _share_row(status="ACCEPTED")
    session = _FakeSession(_FakeResult([row]))

    with pytest.raises(svc.McpShareConflictError):
        await svc.accept_share(session, user={"user_id": 9}, share_id="share-1")


async def test_accept_share_rejects_when_payload_missing():
    row = _share_row(payload=None)
    session = _FakeSession(_FakeResult([row]))

    with pytest.raises(svc.McpShareConflictError):
        await svc.accept_share(session, user={"user_id": 9}, share_id="share-1")


async def test_reject_and_withdraw_only_allow_pending():
    rejected = _share_row()
    session = _FakeSession(_FakeResult([rejected]))
    await svc.reject_share(session, user={"user_id": 9}, share_id="share-1")
    assert rejected.status == "REJECTED"
    assert rejected.payload is None

    withdrawn = _share_row()
    session = _FakeSession(_FakeResult([withdrawn]))
    await svc.withdraw_share(session, user={"user_id": 7}, share_id="share-1")
    assert withdrawn.status == "WITHDRAWN"
    assert withdrawn.payload is None


async def test_withdraw_requires_source_user():
    row = _share_row()
    session = _FakeSession(_FakeResult([row]))

    with pytest.raises(svc.McpSharePermissionError):
        await svc.withdraw_share(session, user={"user_id": 9}, share_id="share-1")


# ---------------------------------------------------------------------------
# 发送校验
# ---------------------------------------------------------------------------

async def test_share_server_validates_targets_before_hitting_database():
    session = _FakeSession()

    with pytest.raises(svc.McpShareConflictError, match="请选择接收人"):
        await svc.share_server(
            session, user={"user_id": 7}, server_id="srv-1", target_user_ids=[]
        )
    with pytest.raises(svc.McpShareConflictError, match="不能分享给自己"):
        await svc.share_server(
            session, user={"user_id": 7}, server_id="srv-1", target_user_ids=[7]
        )
    with pytest.raises(svc.McpShareConflictError, match="附言"):
        await svc.share_server(
            session,
            user={"user_id": 7},
            server_id="srv-1",
            target_user_ids=[9],
            message="x" * (svc.MAX_MESSAGE_LENGTH + 1),
        )


async def test_share_server_rejects_unknown_or_disabled_target():
    """接收人不存在或已停用必须当场拒绝：没有物理外键，脏 ID 会静默写进分享表。"""
    session = _FakeSession(
        _FakeResult([_server()]),
        _FakeResult([svc.User(id=9, user_name="b", status=0)]),
    )

    with pytest.raises(svc.McpShareConflictError, match="不存在或已停用"):
        await svc.share_server(
            session, user={"user_id": 7}, server_id="srv-1", target_user_ids=[9, 99]
        )
    assert session.added == []


async def test_share_server_queries_existing_shares_in_one_batch():
    """分享给 N 个人不能产生 N 次分享表查询（50 人 = 50 次往返）。"""
    session = _FakeSession(
        _FakeResult([_server()]),
        _FakeResult([
            svc.User(id=9, user_name="b", status=1),
            svc.User(id=10, user_name="c", status=1),
        ]),
        _FakeResult([]),
    )
    calls = []
    original = session.execute

    async def capture(stmt, *args, **kwargs):
        calls.append(stmt)
        return await original(stmt, *args, **kwargs)

    session.execute = capture

    with _notification_mock():
        result = await svc.share_server(
            session,
            user={"user_id": 7, "user_name": "a"},
            server_id="srv-1",
            target_user_ids=[9, 10],
        )

    assert len(result["created"]) == 2
    # 固定 4 次：源服务、工具清单、接收人、已有分享——与接收人数无关
    assert len(calls) == 4, f"期望 4 次查询，实际 {len(calls)}"
    assert "mcp_server_shares" in str(calls[3])


async def test_share_server_refuses_platform_server():
    platform = _server(scope="global", user_id=None)
    session = _FakeSession(_FakeResult([platform]))

    with pytest.raises(svc.McpSharePermissionError):
        await svc.share_server(
            session, user={"user_id": 7}, server_id="srv-1", target_user_ids=[9]
        )


async def test_share_server_refuses_echo_test_server():
    """Echo 的 audience 与 Token 绑定原实例，复制一份给他人必然验签失败。"""
    echo = _server(sse_url="https://nanzi.example.com/mcp/echo/srv-echo/mcp")
    session = _FakeSession(_FakeResult([echo]))

    with pytest.raises(svc.McpShareConflictError, match="Echo 测试 MCP 不支持分享"):
        await svc.share_server(
            session, user={"user_id": 7}, server_id="srv-1", target_user_ids=[9]
        )
    assert session.added == []


# ---------------------------------------------------------------------------
# 列表分页上限
# ---------------------------------------------------------------------------

async def test_list_received_shares_applies_limit():
    session = _FakeSession(_FakeResult([]), _FakeResult([2]))
    statements = []
    original = session.execute

    async def capture(stmt, *args, **kwargs):
        statements.append(stmt)
        return await original(stmt, *args, **kwargs)

    session.execute = capture

    result = await svc.list_received_shares(session, user={"user_id": 9}, status="all")

    assert result["pending_count"] == 2
    assert getattr(statements[0], "_limit_clause", None) is not None

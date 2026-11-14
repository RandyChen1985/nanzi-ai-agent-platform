import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from redis.exceptions import WatchError

import pytest
from fastapi import HTTPException

from app.services.conversation_resource_service import (
    TTL_SECONDS,
    ConversationResourceService,
    MountSyncUnavailable,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_replace_writes_the_shared_thirty_day_ttl():
    """TTL 必须是共享常量，且真的传给了 Redis（此前重命名链路漏了 ex）。"""
    redis = AsyncMock()
    with patch("app.services.conversation_resource_service.get_redis", new_callable=AsyncMock, return_value=redis):
        await ConversationResourceService.replace(7, "conv-1", {"skills": [{"id": "s"}]})

    assert TTL_SECONDS == 2592000
    assert ConversationResourceService.TTL_SECONDS == TTL_SECONDS
    assert redis.set.await_args.kwargs["ex"] == TTL_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_failure", ["missing", "read_failed", "write_failed", "write_rejected"])
async def test_chat_cannot_confirm_mcp_mount_when_scope_was_not_saved(monkeypatch, storage_failure):
    from app.api.v1.endpoints import chat

    tool = {"id": "mcp-fixture", "name": "fixture-mail:list_mail"}
    normalizer = AsyncMock(return_value={"mcp_tools": [tool]})
    monkeypatch.setattr(chat, "_normalize_conversation_resource_scope", normalizer)
    redis = _WatchRedis({"knowledge_scope": {"mode": "all"}})
    redis.failure = storage_failure
    get_redis = AsyncMock(return_value=None if storage_failure == "missing" else redis)
    with patch("app.services.conversation_resource_service.get_redis", get_redis):
        with pytest.raises(HTTPException) as error:
            await chat.update_conversation_resource_scope(
                "fixture-conversation",
                chat.ConversationResourceScopeRequest(mcp_tools=[tool]),
                user_info={"user_id": 7},
                db=AsyncMock(),
            )

    assert error.value.status_code == 503
    assert redis.version == 0


@pytest.mark.asyncio
async def test_chat_saves_only_active_owned_mcp_tools(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.v1.endpoints import chat
    from app.models.knowledge import KnowledgeBaseMetadata
    from app.models.mcp import McpServer, McpToolCache
    from app.services.metadata_service import MetadataService
    from app.services.permission_service import PermissionService
    from tests.temporary_database import temporary_database

    monkeypatch.setattr(MetadataService, "list_accessible_dataset_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(PermissionService, "get_knowledge_base_access", AsyncMock(return_value={"accessible_ids": set()}))

    redis = _WatchRedis({"knowledge_scope": {"mode": "all"}})
    monkeypatch.setattr("app.services.conversation_resource_service.get_redis", AsyncMock(return_value=redis))

    async with temporary_database(
        KnowledgeBaseMetadata.__table__, McpServer.__table__, McpToolCache.__table__,
    ) as engine, async_sessionmaker(engine)() as db:
        server_states = {
            "active": {"user_id": 7, "enabled_status": 1},
            "disabled": {"user_id": 7, "enabled_status": 0},
            "foreign": {"user_id": 8, "enabled_status": 1},
        }
        tools = []
        for key, state in server_states.items():
            server = McpServer(id=key, server_name=key, scope="personal", sse_url="https://mcp.example", **state)
            tools.append(McpToolCache(
                id=f"tool-{key}", tool_name=f"{key}:list_mail", server=server, is_published=True, is_available=True,
            ))
        db.add_all(tools)
        await db.flush()

        requests = [{"id": tool.id} for tool in tools] + [{"id": "old-id", "name": "active:list_mail"}]
        body = chat.ConversationResourceScopeRequest(mcp_tools=requests[-1:])
        stale = await chat.update_conversation_resource_scope(
            "fixture-conversation", body, user_info={"user_id": 7}, db=db,
        )
        assert stale.data["mcp_tools"] == []

        body = chat.ConversationResourceScopeRequest(mcp_tools=requests)
        result = await chat.update_conversation_resource_scope(
            "fixture-conversation", body, user_info={"user_id": 7}, db=db,
        )

    assert [item["id"] for item in result.data["mcp_tools"]] == ["tool-active"]
    assert result.data["knowledge_scope"] == {"mode": "all"}
    assert result.data == json.loads(redis.value)


class _WatchRedis:
    def __init__(self, scope):
        self.value = json.dumps(scope)
        self.version = 0
        self.failure = None

    def pipeline(self):
        return _WatchPipeline(self)


class _WatchPipeline:
    def __init__(self, redis):
        self.redis = redis

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_error):
        pass

    async def watch(self, _key):
        self.version = self.redis.version

    async def get(self, _key):
        if self.redis.failure == "read_failed":
            raise ConnectionError("fixture unavailable")
        value = self.redis.value
        await asyncio.sleep(0)
        return value

    def multi(self):
        pass

    def set(self, _key, value, *, ex):
        assert ex == TTL_SECONDS
        self.value = value

    async def execute(self):
        if self.redis.failure == "write_failed":
            raise ConnectionError("fixture unavailable")
        if self.redis.failure == "write_rejected":
            return [False]
        if self.redis.version != self.version:
            raise WatchError()
        self.redis.value = self.value
        self.redis.version += 1
        return [True]


@pytest.mark.asyncio
async def test_parallel_mcp_mounts_keep_both_tools_and_existing_resources(monkeypatch):
    from app.api.v1.endpoints import chat

    from app.services.metadata_service import MetadataService

    monkeypatch.setattr(
        MetadataService, "list_accessible_dataset_options", AsyncMock(return_value=[
            SimpleNamespace(id="dataset-one", name="dataset-one"),
        ]),
    )
    redis = _WatchRedis({
        "skills": [{"id": "old-skill"}],
        "knowledge_scope": {"mode": "all"},
        "mcp_tools": [{"id": "old-tool", "name": "fixture:existing"}],
    })
    monkeypatch.setattr("app.services.conversation_resource_service.get_redis", AsyncMock(return_value=redis))

    async def authorized_mounts(_db, owner, items):
        assert owner == "7"
        return [item for item in items if item["id"] != "unavailable"]

    monkeypatch.setattr(chat, "normalize_personal_mcp_mounts", authorized_mounts)

    async def mount(tool_id, name):
        return await chat.mount_conversation_mcp_tools(
            "fixture-conversation",
            chat.ConversationMcpMountRequest(
                mcp_tools=[{"id": tool_id, "name": name}, {"id": "unavailable", "name": "fixture:off"}],
            ),
            user_info={"user_id": 7},
            db=AsyncMock(),
        )

    await asyncio.gather(
        mount("first", "fixture:first"),
        mount("second", "fixture:second"),
        ConversationResourceService.patch_scope(
            7, "fixture-conversation", pending_scope_gate={"gate_id": "fixture-gate"},
        ),
    )
    await chat.update_conversation_dataset_mount(
        "fixture-conversation",
        chat.ConversationDatasetMountRequest(dataset_id="dataset-one", mounted=True),
        user_info={"user_id": 7},
        db=AsyncMock(),
    )
    latest = await mount("first", "fixture:first")
    stored = json.loads(redis.value)

    assert latest.data["scope"] == stored
    assert [tool["id"] for tool in latest.data["mounted_mcp_tools"]] == ["first"]
    assert {tool["id"] for tool in stored["mcp_tools"]} == {"old-tool", "first", "second"}
    assert len(stored["mcp_tools"]) == 3
    assert stored["skills"] == [{"id": "old-skill"}]
    assert [dataset["id"] for dataset in stored["datasets"]] == ["dataset-one"]
    assert stored["knowledge_scope"] == {"mode": "all"}
    assert stored["pending_scope_gate"] == {"gate_id": "fixture-gate"}


@pytest.mark.asyncio
async def test_rewrite_skill_mounts_raises_when_redis_unavailable_in_strict_mode():
    """删除/下架链路需要知道「这一处没同步」，不能被混进「改了 0 个会话」。"""
    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        with pytest.raises(MountSyncUnavailable):
            await ConversationResourceService.rewrite_skill_mounts(
                skill_id="gone", strict_unavailable=True
            )
        # 重命名链路保持历史语义：静默降级为 0
        assert (
            await ConversationResourceService.rewrite_skill_mounts(
                skill_id="gone", new_skill_id="new"
            )
            == 0
        )


@pytest.mark.asyncio
async def test_replace_keeps_only_explicit_project_resources():
    redis = AsyncMock()
    with patch("app.services.conversation_resource_service.get_redis", new_callable=AsyncMock, return_value=redis):
        scope = await ConversationResourceService.replace(
            7,
            "conv-1",
            {
                "project_name": "销售分析",
                "datasets": [{"id": "sales_ds", "name": "销售数据"}, {"name": "无 ID"}],
                "knowledge_bases": [{"id": "kb-1"}],
                "skills": [{"id": "skill-1"}],
                "mcp_tools": [{"id": "mcp-1", "name": "mcp_demo"}],
            },
        )

    assert scope["project_name"] == "销售分析"
    assert [item["id"] for item in scope["datasets"]] == ["sales_ds"]
    assert [item["id"] for item in scope["mcp_tools"]] == ["mcp-1"]
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_many_uses_one_redis_mget_for_history_scopes():
    redis = AsyncMock()
    redis.mget.return_value = [
        '{"project_name":"p1","datasets":[]}',
        None,
    ]
    with patch("app.services.conversation_resource_service.get_redis", new_callable=AsyncMock, return_value=redis):
        scopes = await ConversationResourceService.get_many(7, ["conv-1", "conv-2"])

    redis.mget.assert_awaited_once_with([
        "conversation:7:conv-1:resource_scope_v1",
        "conversation:7:conv-2:resource_scope_v1",
    ])
    assert scopes["conv-1"]["project_name"] == "p1"
    assert scopes["conv-2"]["datasets"] == []


class _FakeScanRedis:
    """极小的 Redis 替身：只实现 rewrite_skill_mounts 用到的 scan/get/set。"""

    def __init__(self, store):
        self.store = dict(store)
        self.scanned_patterns = []
        self.sets = []

    async def scan(self, cursor=0, match=None, count=200):
        self.scanned_patterns.append(match)
        # 按 Redis 语义过滤：模式里带 owner 就必须真的少扫到别人的 key
        from fnmatch import fnmatch

        return 0, [key for key in self.store if match is None or fnmatch(key, match)]

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.sets.append((key, value, ex))
        self.store[key] = value


@pytest.mark.asyncio
async def test_rewrite_skill_mounts_owner_scope_never_touches_other_users():
    """★ 个人技能的重命名/删除绝不能改到别人的会话挂载。

    技能 ID 就是目录名：用户 A 完全可以建一个 id 恰好叫 ``pdf`` 的个人技能（平台允许与
    公共技能同名），而历史挂载项没有 ``scope`` 字段时会被判为「匹配」。扫描原先不带 owner
    过滤，于是 A 的删除会顺手动到 **B 会话**里的同名挂载（跨用户写会话资源）。
    """
    import json

    mine = "conversation:7:conv-a:resource_scope_v1"
    other = "conversation:8:conv-b:resource_scope_v1"
    redis = _FakeScanRedis(
        {
            mine: json.dumps({"skills": [{"id": "pdf", "scope": "personal"}]}),
            other: json.dumps({"skills": [{"id": "pdf"}]}),  # B 的老挂载：没有 scope 字段
        }
    )
    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        updated = await ConversationResourceService.rewrite_skill_mounts(
            skill_id="pdf", new_skill_id=None, scope="personal", owner_id=7
        )

    assert updated == 1
    assert redis.scanned_patterns == ["conversation:7:*:resource_scope_v1"]
    assert [key for key, _value, _ex in redis.sets] == [mine]
    assert json.loads(redis.store[mine])["skills"] == []
    assert json.loads(redis.store[other])["skills"] == [{"id": "pdf"}], "别人的挂载不能被改"
    # 写回必须带 TTL（历史 bug：重命名链路把 30 天写成了永久键）
    assert redis.sets[0][2] == TTL_SECONDS


@pytest.mark.asyncio
async def test_rewrite_skill_mounts_without_owner_still_scans_everyone():
    """平台技能（global）的重命名/下架必须扫全库：所有用户的挂载都要跟着改。"""
    import json

    mine = "conversation:7:conv-a:resource_scope_v1"
    other = "conversation:8:conv-b:resource_scope_v1"
    redis = _FakeScanRedis(
        {
            mine: json.dumps({"skills": [{"id": "pdf"}]}),
            other: json.dumps({"skills": [{"id": "pdf"}]}),
        }
    )
    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        updated = await ConversationResourceService.rewrite_skill_mounts(
            skill_id="pdf", new_skill_id="pdf2", scope="global"
        )

    assert updated == 2
    assert redis.scanned_patterns == ["conversation:*:*:resource_scope_v1"]
    assert json.loads(redis.store[mine])["skills"] == [{"id": "pdf2"}]
    assert json.loads(redis.store[other])["skills"] == [{"id": "pdf2"}]


@pytest.mark.asyncio
async def test_rewrite_skill_mounts_owner_scope_ignores_other_users_same_id():
    """该用户自己没有这条挂载时返回 0，且绝不顺手去改别人的同名挂载。"""
    import json

    other = "conversation:8:conv-b:resource_scope_v1"
    redis = _FakeScanRedis({other: json.dumps({"skills": [{"id": "pdf"}]})})
    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        updated = await ConversationResourceService.rewrite_skill_mounts(
            skill_id="pdf", new_skill_id=None, scope="personal", owner_id=7
        )
    assert updated == 0
    assert redis.sets == []
    assert json.loads(redis.store[other])["skills"] == [{"id": "pdf"}]


@pytest.mark.asyncio
async def test_rewrite_skill_mounts_rejects_bogus_owner_identity():
    """非法/空身份 fail closed：不扫描、不改任何 key。"""
    redis = _FakeScanRedis({})
    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        updated = await ConversationResourceService.rewrite_skill_mounts(
            skill_id="pdf", new_skill_id=None, scope="personal", owner_id="anonymous"
        )
    assert updated == 0
    assert redis.scanned_patterns == []


@pytest.mark.asyncio
async def test_rewrite_mcp_tool_name_matches_tool_id_not_same_name():
    """同名平台工具不能被连带改名：只能按工具缓存 id 精确命中。"""
    import json

    mine = "conversation:7:conv-a:resource_scope_v1"
    other = "conversation:8:conv-b:resource_scope_v1"
    redis = _FakeScanRedis(
        {
            mine: json.dumps(
                {
                    "mcp_tools": [
                        {"id": "tool-personal", "name": "NanZi Echo 测试 MCP:echo"},
                        {"id": "tool-platform", "name": "NanZi Echo 测试 MCP:echo"},
                    ]
                },
                ensure_ascii=False,
            ),
            other: json.dumps(
                {"mcp_tools": [{"id": "tool-personal", "name": "NanZi Echo 测试 MCP:echo"}]},
                ensure_ascii=False,
            ),
        }
    )

    with patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        updated = await ConversationResourceService.rewrite_mcp_tool_name(
            owner_id=7,
            tool_id="tool-personal",
            new_name="mcp-private-admin-echo:echo",
        )

    assert updated == 1
    assert redis.scanned_patterns == ["conversation:7:*:resource_scope_v1"]

    rewritten = json.loads(redis.store[mine])["mcp_tools"]
    assert rewritten[0]["name"] == "mcp-private-admin-echo:echo"
    assert rewritten[1]["name"] == "NanZi Echo 测试 MCP:echo"
    assert json.loads(redis.store[other])["mcp_tools"][0]["name"] == "NanZi Echo 测试 MCP:echo"
    assert redis.sets[0][2] == TTL_SECONDS

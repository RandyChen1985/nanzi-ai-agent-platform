from unittest.mock import AsyncMock, patch

import pytest

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

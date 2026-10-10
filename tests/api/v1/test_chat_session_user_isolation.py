"""聊天面会话数据的跨用户隔离：任何角色（含 admin）都不得读写他人会话。

覆盖的越权面（修复前全部存在 admin 旁路）：
  - GET  /api/v1/chat/history                 读他人会话轮次
  - GET  /api/v1/chat/logs/{trace_id}         读他人执行轨迹
  - DELETE /api/v1/chat/history/{trace_id}    删他人记录
  - POST /api/v1/chat/history/batch-delete    删他人会话（含对方 Redis 记忆）
  - GET  /api/v1/chat/export/data/{trace_id}  导出他人数据
  - POST /api/v1/chat/active                  把他人会话登记成自己的活跃会话
  - POST /api/v1/chat/completions             把提问写进他人会话
以及本次新增的归属查询 GET /api/v1/chat/conversation/{id}/ownership，供前端在采用会话 ID 前校验。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from unittest.mock import AsyncMock, patch

from app.core.orm import AsyncSessionLocal
from app.main import app
from app.models.audit import AgentExecutionHistory
from app.models.user import User
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


class _IsolationFixture:
    """一次用例内的用户、会话与清理登记。"""

    def __init__(self) -> None:
        self.users: list[tuple[str, str]] = []
        self.trace_ids: list[str] = []

    async def create_user(self, db, prefix: str, role: str) -> dict:
        uniq = uuid.uuid4().hex[:10]
        user_name = f"{prefix}_{uniq}"
        api_key = await AuthService.generate_api_key(user_name, role=role, db=db)
        result = await db.execute(select(User).where(User.user_name == user_name))
        user = result.scalar_one()
        self.users.append((user_name, role))
        return {
            "headers": {"X-API-Key": api_key},
            "user_id": str(user.id),
            "user_name": user_name,
        }

    async def seed_turn(self, db, *, user: dict, conversation_id: str, query: str, summary: str) -> str:
        trace_id = f"trace-{uuid.uuid4().hex[:16]}"
        db.add(
            AgentExecutionHistory(
                agent_id="agent-iso",
                trace_id=trace_id,
                conversation_id=conversation_id,
                user_id=user["user_id"],
                username=user["user_name"],
                query=query,
                summary=summary,
                status="success",
            )
        )
        await db.commit()
        self.trace_ids.append(trace_id)
        return trace_id

    async def cleanup(self) -> None:
        async with AsyncSessionLocal() as session:
            if self.trace_ids:
                await session.execute(
                    delete(AgentExecutionHistory).where(
                        AgentExecutionHistory.trace_id.in_(self.trace_ids)
                    )
                )
            for user_name, _role in self.users:
                await session.execute(delete(User).where(User.user_name == user_name))
            await session.commit()


@pytest.fixture
async def isolation():
    fixture = _IsolationFixture()
    yield fixture
    await fixture.cleanup()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _items(res) -> list:
    assert res.status_code == 200, res.text
    return res.json()["data"]["items"]


async def test_chat_history_hides_other_users_conversation_from_admin(db_session, isolation):
    """管理员不能再借 conversation_id 读到他人会话轮次。"""
    admin = await isolation.create_user(db_session, "iso_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="别人的提问", summary="别人的回答"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert await _items(res) == []


async def test_chat_history_is_self_scoped_for_regular_user(db_session, isolation):
    """普通用户同样只能看到自己的会话。"""
    viewer = await isolation.create_user(db_session, "iso_viewer", "user")
    owner = await isolation.create_user(db_session, "iso_owner2", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="别人的提问", summary="别人的回答"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=viewer["headers"],
        )
        assert await _items(res) == []


async def test_chat_history_still_returns_own_turns(db_session, isolation):
    """收紧不能把本人历史一起挡掉。"""
    owner = await isolation.create_user(db_session, "iso_self", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="我的提问", summary="我的回答"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=owner["headers"],
        )
        items = await _items(res)
        assert [item["query"] for item in items] == ["我的提问"]


async def test_trace_logs_hide_other_users_trace_from_admin(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_trace_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_trace_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/chat/logs/{trace_id}", headers=admin["headers"])
        # 不泄露存在性：他人轨迹按「不存在」处理
        assert res.status_code == 404


async def test_trace_logs_still_return_own_trace(db_session, isolation):
    owner = await isolation.create_user(db_session, "iso_trace_self", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/chat/logs/{trace_id}", headers=owner["headers"])
        assert res.status_code == 200


async def test_delete_history_ignores_other_users_record_for_admin(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_del_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_del_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.delete(f"/api/v1/chat/history/{trace_id}", headers=admin["headers"])
        assert res.status_code == 404

    result = await db_session.execute(
        select(AgentExecutionHistory.id).where(AgentExecutionHistory.trace_id == trace_id)
    )
    assert result.scalar_one_or_none() is not None, "他人记录不得被删除"


async def test_batch_delete_history_ignores_other_users_conversation_for_admin(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_batch_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_batch_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.post(
            "/api/v1/chat/history/batch-delete",
            json={"conversation_ids": [conversation_id]},
            headers=admin["headers"],
        )
        assert res.status_code == 200

    result = await db_session.execute(
        select(AgentExecutionHistory.id).where(AgentExecutionHistory.trace_id == trace_id)
    )
    assert result.scalar_one_or_none() is not None, "他人会话不得被批量删除"


async def test_export_trace_data_hides_other_users_trace(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_export_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_export_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/chat/export/data/{trace_id}", headers=admin["headers"])
        assert res.status_code == 404


async def test_conversation_ownership_endpoint_reports_foreign_conversation(db_session, isolation):
    viewer = await isolation.create_user(db_session, "iso_own_viewer", "admin")
    owner = await isolation.create_user(db_session, "iso_own_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/conversation/{conversation_id}/ownership",
            headers=viewer["headers"],
        )
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["owned"] is False
        assert data["foreign"] is True


async def test_conversation_ownership_endpoint_distinguishes_own_and_brand_new(db_session, isolation):
    user = await isolation.create_user(db_session, "iso_own_self", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=user, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        own = await client.get(
            f"/api/v1/chat/conversation/{conversation_id}/ownership",
            headers=user["headers"],
        )
        assert own.json()["data"] == {"owned": True, "foreign": False}

        fresh = await client.get(
            f"/api/v1/chat/conversation/conv-{uuid.uuid4().hex[:12]}/ownership",
            headers=user["headers"],
        )
        # 全新会话既非「我的」也非「他人的」→ 前端可安全采用
        assert fresh.json()["data"] == {"owned": False, "foreign": False}


async def test_conversation_belongs_to_user_uses_the_requested_instance_bucket(db_session, isolation):
    """「归属」判定必须把调用方传来的 instance_id 带进活跃会话查询。

    多实例嵌入下「活跃会话」按实例分桶存储；丢掉 instance_id 会退回默认桶，把空会话
    误判为不归属（可复用结果列表会静默变空）。
    """
    user = await isolation.create_user(db_session, "iso_own_instance", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    seen: list[tuple[str, object]] = []

    async def fake_get_active(user_id, instance_id=None):
        seen.append((str(user_id), instance_id))
        return conversation_id

    from app.api.v1.endpoints.chat import _conversation_belongs_to_user

    with patch(
        "app.api.v1.endpoints.chat.memory_service.get_active_conversation", fake_get_active
    ), patch(
        "app.api.v1.endpoints.chat.memory_service.history_exists",
        AsyncMock(return_value=False),
    ):
        belongs = await _conversation_belongs_to_user(
            db_session, user["user_id"], conversation_id, instance_id="embed-inst-a"
        )

    assert belongs is True
    assert seen == [(user["user_id"], "embed-inst-a")], "instance_id 必须透传到活跃会话查询"


async def test_conversation_ownership_does_not_treat_a_polluted_active_pointer_as_owned(
    db_session, isolation
):
    """仅「该会话正是我的活跃会话」不足以证明归属。

    会话 ID 只是字符串：打补丁前，换用户后本地残留的 cid 会被写进新身份的活跃指针。
    若 owned 认这条指针，别人的会话就会被判成「我的」，写侧守卫随之失效。
    """
    mine = await isolation.create_user(db_session, "iso_ptr_mine", "user")
    other = await isolation.create_user(db_session, "iso_ptr_other", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=other, conversation_id=conversation_id, query="别人的轮次", summary="x"
    )

    with patch(
        "app.api.v1.endpoints.chat.memory_service.get_active_conversation",
        AsyncMock(return_value=conversation_id),
    ), patch(
        "app.api.v1.endpoints.chat.memory_service.history_exists",
        AsyncMock(return_value=False),
    ):
        async with _client() as client:
            res = await client.get(
                f"/api/v1/chat/conversation/{conversation_id}/ownership",
                headers=mine["headers"],
            )

    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"owned": False, "foreign": True}


async def test_set_active_conversation_rejects_foreign_conversation(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_active_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_active_owner", "user")
    foreign_conversation = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=foreign_conversation, query="q", summary="a"
    )

    async with _client() as client:
        rejected = await client.post(
            "/api/v1/chat/active",
            json={"conversation_id": foreign_conversation},
            headers=admin["headers"],
        )
        assert rejected.status_code == 403

        fresh_conversation = f"conv-{uuid.uuid4().hex[:12]}"
        accepted = await client.post(
            "/api/v1/chat/active",
            json={"conversation_id": fresh_conversation},
            headers=admin["headers"],
        )
        assert accepted.status_code == 200


async def test_chat_completions_rejects_foreign_conversation(db_session, isolation):
    admin = await isolation.create_user(db_session, "iso_comp_admin", "admin")
    owner = await isolation.create_user(db_session, "iso_comp_owner", "user")
    foreign_conversation = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=owner, conversation_id=foreign_conversation, query="q", summary="a"
    )

    completion_mock = AsyncMock(return_value={"trace_id": "trace-should-not-run"})
    with patch(
        "app.api.v1.endpoints.chat.agent_service.chat_completion", completion_mock
    ):
        async with _client() as client:
            res = await client.post(
                "/api/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "写进别人会话"}],
                    "conversation_id": foreign_conversation,
                    "stream": False,
                },
                headers=admin["headers"],
            )
        assert res.status_code == 403, res.text
        completion_mock.assert_not_awaited()


async def test_reusable_results_uses_the_requested_instance_bucket(db_session, isolation):
    """可复用结果列表的归属判定同样要把 instance_id 带进活跃会话查询。

    活跃会话按实例分桶存储；丢掉 instance_id 会退回默认桶，把「该实例下的活跃会话」
    误判为不存在而 404（多实例嵌入下列表静默变空）。
    """
    user = await isolation.create_user(db_session, "iso_reusable_instance", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    seen: list[object] = []

    async def fake_get_active(user_id, instance_id=None):
        seen.append(instance_id)
        return conversation_id

    with patch(
        "app.api.v1.endpoints.chat.memory_service.get_active_conversation", fake_get_active
    ), patch(
        "app.api.v1.endpoints.chat.memory_service.history_exists",
        AsyncMock(return_value=False),
    ):
        async with _client() as client:
            res = await client.get(
                "/api/v1/chat/reusable-results",
                params={"conversation_id": conversation_id, "instance_id": "embed-inst-b"},
                headers=user["headers"],
            )

    assert res.status_code == 200, res.text
    assert seen == ["embed-inst-b"], "instance_id 必须透传到活跃会话查询"


# ---------------------------------------------------------------------------
# 混杂会话（同一 cid 下既有我的轮次、也有他人的轮次）
#
# 这不是假设：打补丁前「会话 ID 只按实例分桶、与登录身份无关」，同一浏览器换人登录后
# 会沿用同一个 cid；宿主下发的 resume id 也可能被同一租户的多人共用。于是历史数据里
# 存在大量「一个 cid、多个归属人」的会话。
#
# 对这类会话不能一律按「别人的会话」处理：
#   - 读侧本来就按 user_id 过滤（`/chat/history` 只回我自己的轮次、Redis 记忆键也带 user_id），
#     拦下来并不会多挡住任何跨用户内容，只会让用户**打不开自己参与过的会话**；
#   - 前端一旦判成 foreign 就会清掉本地续接指针，表现为「刷新后内容没了、像开了新会话」。
# 正确的判定是「**只有**在别人的轮次存在、且没有我一轮时才拦」。
# ---------------------------------------------------------------------------


async def test_conversation_ownership_reports_my_own_share_of_a_mixed_conversation(db_session, isolation):
    mine = await isolation.create_user(db_session, "iso_mixed_mine", "user")
    other = await isolation.create_user(db_session, "iso_mixed_other", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=other, conversation_id=conversation_id, query="别人的轮次", summary="x"
    )
    await isolation.seed_turn(
        db_session, user=mine, conversation_id=conversation_id, query="我的轮次", summary="y"
    )

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/conversation/{conversation_id}/ownership",
            headers=mine["headers"],
        )

    assert res.status_code == 200, res.text
    # owned 与 foreign 必须相互独立：既「有我的一份」，也「混着别人的记录」。
    assert res.json()["data"] == {"owned": True, "foreign": True}


async def test_blocking_rule_only_rejects_conversations_without_any_of_my_turns(db_session, isolation):
    mine = await isolation.create_user(db_session, "iso_rule_mine", "user")
    other = await isolation.create_user(db_session, "iso_rule_other", "user")
    mixed = f"conv-{uuid.uuid4().hex[:12]}"
    foreign_only = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(db_session, user=other, conversation_id=mixed, query="o", summary="x")
    await isolation.seed_turn(db_session, user=mine, conversation_id=mixed, query="m", summary="y")
    await isolation.seed_turn(
        db_session, user=other, conversation_id=foreign_only, query="o", summary="x"
    )

    from app.api.v1.endpoints.chat import _conversation_blocked_for_user

    assert (
        await _conversation_blocked_for_user(db_session, mine["user_id"], foreign_only) is True
    ), "只有别人的轮次 → 必须拦住（换用户后沿用他人会话 ID 的越界写入）"
    assert (
        await _conversation_blocked_for_user(db_session, mine["user_id"], mixed) is False
    ), "有我自己的轮次 → 不是越界写入，必须放行"
    assert (
        await _conversation_blocked_for_user(db_session, mine["user_id"], f"conv-{uuid.uuid4().hex[:12]}") is False
    ), "全新会话既非我的也非他人的 → 放行"


async def test_set_active_conversation_allows_a_mixed_conversation_i_participate_in(db_session, isolation):
    mine = await isolation.create_user(db_session, "iso_mixed_active_mine", "user")
    other = await isolation.create_user(db_session, "iso_mixed_active_other", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=other, conversation_id=conversation_id, query="别人的轮次", summary="x"
    )
    await isolation.seed_turn(
        db_session, user=mine, conversation_id=conversation_id, query="我的轮次", summary="y"
    )

    async with _client() as client:
        res = await client.post(
            "/api/v1/chat/active",
            json={"conversation_id": conversation_id},
            headers=mine["headers"],
        )

    assert res.status_code == 200, res.text


async def test_chat_completions_allows_continuing_a_mixed_conversation_i_participate_in(
    db_session, isolation
):
    mine = await isolation.create_user(db_session, "iso_mixed_comp_mine", "user")
    other = await isolation.create_user(db_session, "iso_mixed_comp_other", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await isolation.seed_turn(
        db_session, user=other, conversation_id=conversation_id, query="别人的轮次", summary="x"
    )
    await isolation.seed_turn(
        db_session, user=mine, conversation_id=conversation_id, query="我的轮次", summary="y"
    )

    completion_mock = AsyncMock(
        return_value={"trace_id": "trace-mixed-continue", "content": "ok", "intent": "chat"}
    )
    with patch(
        "app.api.v1.endpoints.chat.agent_service.chat_completion", completion_mock
    ):
        async with _client() as client:
            res = await client.post(
                "/api/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "继续我自己的会话"}],
                    "conversation_id": conversation_id,
                    "stream": False,
                },
                headers=mine["headers"],
            )

    assert res.status_code == 200, res.text
    completion_mock.assert_awaited(), "参与过的混杂会话必须能继续对话"


async def test_conversation_ownership_envelope_is_the_standard_response(db_session, isolation):
    """归属查询走 StandardResponse 信封（`code` / `message` / `data`），**没有** `status`。

    跨层约定：聊天面前端必须按 `code === 200` 判成功（见
    `tests/frontend/test_api_envelope_behavior.py` 的真实信封行为测试）。前端曾按门户遗留信封
    读 `status`，在聊天面上永远为假——归属校验静默退化成「判不出来」，历史会话打不开、
    刷新后像开了新会话。这条断言把信封形状钉在后端侧。
    """
    user = await isolation.create_user(db_session, "iso_envelope_user", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/conversation/{conversation_id}/ownership",
            headers=user["headers"],
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 200
    assert body["message"] == "success"
    assert "status" not in body, "StandardResponse 没有 status 字段，前端不得按它判成功"

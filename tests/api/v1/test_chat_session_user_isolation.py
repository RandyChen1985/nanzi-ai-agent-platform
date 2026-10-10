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


async def test_conversation_ownership_uses_the_requested_instance_bucket(db_session, isolation):
    """归属判定必须把前端传来的 instance_id 带进活跃会话查询。

    多实例嵌入下「活跃会话」是按实例分桶存储的；丢掉 instance_id 会让判定退回默认桶，
    `owned` 语义失真（前端目前只依赖 `foreign`，但接口一旦被别处复用就会误判）。
    """
    user = await isolation.create_user(db_session, "iso_own_instance", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    seen: list[tuple[str, object]] = []

    async def fake_get_active(user_id, instance_id=None):
        seen.append((str(user_id), instance_id))
        return conversation_id

    with patch(
        "app.api.v1.endpoints.chat.memory_service.get_active_conversation", fake_get_active
    ), patch(
        "app.api.v1.endpoints.chat.memory_service.history_exists",
        AsyncMock(return_value=False),
    ):
        async with _client() as client:
            res = await client.get(
                f"/api/v1/chat/conversation/{conversation_id}/ownership",
                params={"instance_id": "embed-inst-a"},
                headers=user["headers"],
            )

    assert res.status_code == 200, res.text
    assert seen == [(user["user_id"], "embed-inst-a")], "instance_id 必须透传到活跃会话查询"
    assert res.json()["data"] == {"owned": True, "foreign": False}


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

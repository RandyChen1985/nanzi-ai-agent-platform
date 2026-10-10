"""聊天日志审计页的 admin 跨用户读取能力。

用户口径：**聊天面（EmbedChat）任何角色都只能访问自己的会话**，但**聊天日志审计页保留
admin 查阅其他用户聊天日志的能力**。两者必须泾渭分明：

  - 常规端点 `/api/v1/chat/history`、`/api/v1/chat/logs/{trace_id}`：始终自隔离，admin 同样；
  - 审计端点 `/api/v1/chat/admin/*`：`Depends(require_admin)`，非 admin 一律 403，
    供聊天日志审计页跨用户查看会话列表与执行轨迹。

因此本文件既验证「审计端点能跨用户」，也验证「常规端点不会因为审计需求被重新打开跨用户」。
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
from app.services.conversation_resource_service import ConversationResourceService

pytestmark = pytest.mark.asyncio


class _AuditFixture:
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
                agent_id="agent-audit",
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

    async def seed_legacy_turn(
        self, db, *, username: str, conversation_id: str, query: str, summary: str
    ) -> str:
        """早期历史的形状：只有 `username`、没有 `user_id`（归属人需由用户名反查补出）。"""
        trace_id = f"trace-{uuid.uuid4().hex[:16]}"
        db.add(
            AgentExecutionHistory(
                agent_id="agent-audit",
                trace_id=trace_id,
                conversation_id=conversation_id,
                user_id=None,
                username=username,
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
async def audit():
    fixture = _AuditFixture()
    yield fixture
    await fixture.cleanup()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_admin_history_endpoint_returns_other_users_turn(db_session, audit):
    """审计端点：admin 能按会话查看他人轮次（含归属用户名，供审计页展示）。"""
    admin = await audit.create_user(db_session, "audit_admin", "admin")
    owner = await audit.create_user(db_session, "audit_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="别人的提问", summary="别人的回答"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/admin/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]
        assert [item["query"] for item in items] == ["别人的提问"]
        assert items[0]["username"] == owner["user_name"]


async def test_admin_history_endpoint_keeps_username_filter(db_session, audit):
    """审计端点：管理员按用户名筛选仍应生效（这正是审计页的用法）。"""
    admin = await audit.create_user(db_session, "audit_filter_admin", "admin")
    owner = await audit.create_user(db_session, "audit_filter_owner", "user")
    other = await audit.create_user(db_session, "audit_filter_other", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="筛选目标", summary="回答"
    )
    other_conversation = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=other, conversation_id=other_conversation, query="不该出现", summary="回答"
    )

    async with _client() as client:
        hit = await client.get(
            "/api/v1/chat/admin/history",
            params={"username": owner["user_name"]},
            headers=admin["headers"],
        )
        assert [item["query"] for item in hit.json()["data"]["items"]] == ["筛选目标"]

        miss = await client.get(
            "/api/v1/chat/admin/history",
            params={"username": f"nobody_{uuid.uuid4().hex[:8]}"},
            headers=admin["headers"],
        )
        assert miss.json()["data"]["items"] == []


async def test_admin_history_endpoint_rejects_non_admin(db_session, audit):
    viewer = await audit.create_user(db_session, "audit_plain", "user")

    async with _client() as client:
        res = await client.get("/api/v1/chat/admin/history", headers=viewer["headers"])
        assert res.status_code == 403


async def test_admin_history_endpoint_requires_authentication():
    async with _client() as client:
        res = await client.get("/api/v1/chat/admin/history")
        assert res.status_code == 401


async def test_admin_trace_logs_endpoint_returns_other_users_trace(db_session, audit):
    admin = await audit.create_user(db_session, "audit_trace_admin", "admin")
    owner = await audit.create_user(db_session, "audit_trace_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/admin/logs/{trace_id}", headers=admin["headers"]
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["history"]["trace_id"] == trace_id


async def test_admin_trace_logs_endpoint_rejects_non_admin(db_session, audit):
    viewer = await audit.create_user(db_session, "audit_trace_plain", "user")
    owner = await audit.create_user(db_session, "audit_trace_owner2", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/admin/logs/{trace_id}", headers=viewer["headers"]
        )
        assert res.status_code == 403


async def test_admin_trace_logs_endpoint_unknown_trace_is_404(db_session, audit):
    admin = await audit.create_user(db_session, "audit_trace_admin2", "admin")

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/admin/logs/trace-{uuid.uuid4().hex[:16]}", headers=admin["headers"]
        )
        assert res.status_code == 404


async def test_regular_history_stays_self_scoped_for_admin(db_session, audit):
    """回归护栏：审计需求不得让常规端点重新变成跨用户。"""
    admin = await audit.create_user(db_session, "audit_reg_admin", "admin")
    owner = await audit.create_user(db_session, "audit_reg_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="别人的提问", summary="别人的回答"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert res.status_code == 200
        assert res.json()["data"]["items"] == []


async def test_regular_trace_logs_stays_self_scoped_for_admin(db_session, audit):
    admin = await audit.create_user(db_session, "audit_reg_admin2", "admin")
    owner = await audit.create_user(db_session, "audit_reg_owner2", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    trace_id = await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/chat/logs/{trace_id}", headers=admin["headers"])
        assert res.status_code == 404


async def test_admin_context_compactions_endpoint_reads_the_owner_scope(db_session, audit):
    """压缩时间线也按「会话归属人」读取，否则管理员看到的是自己名下的空记录。"""
    admin = await audit.create_user(db_session, "audit_comp_admin", "admin")
    owner = await audit.create_user(db_session, "audit_comp_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )

    list_records = AsyncMock(return_value=[])
    with patch(
        "app.api.v1.endpoints.chat.context_compaction_log_service.list_records", list_records
    ):
        async with _client() as client:
            res = await client.get(
                f"/api/v1/chat/admin/conversation/{conversation_id}/context_compactions",
                headers=admin["headers"],
            )
    assert res.status_code == 200, res.text
    list_records.assert_awaited_once()
    assert list_records.await_args.args[0] == owner["user_id"]
    assert list_records.await_args.args[1] == conversation_id


async def test_admin_context_compactions_endpoint_rejects_non_admin(db_session, audit):
    viewer = await audit.create_user(db_session, "audit_comp_plain", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/admin/conversation/{conversation_id}/context_compactions",
            headers=viewer["headers"],
        )
        assert res.status_code == 403


async def test_admin_context_compactions_endpoint_requires_authentication():
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"

    async with _client() as client:
        res = await client.get(
            f"/api/v1/chat/admin/conversation/{conversation_id}/context_compactions"
        )
        assert res.status_code == 401


async def test_admin_history_reads_resource_scope_from_the_conversation_owner(db_session, audit):
    """审计列表的项目范围必须取「会话归属人」名下的记录。

    跨用户视图里若按管理员自己的身份去读资源范围，审计页的「项目」列会整列变空；
    更糟的是管理员自己若有同名会话，会显示成管理员的项目——所以这里特意在两个身份下
    放了不同的项目名，断言拿到的是归属人的。
    """
    admin = await audit.create_user(db_session, "audit_scope_admin", "admin")
    owner = await audit.create_user(db_session, "audit_scope_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="q", summary="a"
    )
    await ConversationResourceService.replace(
        owner["user_id"], conversation_id, {"project_name": "归属人的项目"}
    )
    await ConversationResourceService.replace(
        admin["user_id"], conversation_id, {"project_name": "管理员自己的项目"}
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/admin/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]
        assert [item["project_name"] for item in items] == ["归属人的项目"]


async def test_admin_history_groups_other_users_turns_by_conversation(db_session, audit):
    """审计端点同样支持按会话聚合（审计列表切到「按会话」视图时用到）。"""
    admin = await audit.create_user(db_session, "audit_group_admin", "admin")
    owner = await audit.create_user(db_session, "audit_group_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="第一轮", summary="回答一"
    )
    await audit.seed_turn(
        db_session, user=owner, conversation_id=conversation_id, query="第二轮", summary="回答二"
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/admin/history",
            params={"conversation_id": conversation_id, "group_by_conversation": True},
            headers=admin["headers"],
        )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["conversation_id"] == conversation_id
        assert items[0]["turn_count"] == 2
        assert items[0]["username"] == owner["user_name"]


async def test_admin_history_keeps_project_scope_per_owner_for_the_same_conversation(
    db_session, audit
):
    """同一个 conversation_id 出现在两个归属人名下时，项目名必须各按各的归属人取。

    这种「一 cid 混多 owner」正是历史遗留的串号会话形态；若把资源范围折叠成
    `cid -> scope` 的映射，后写会覆盖先写，审计页会把一个人的项目名显示成另一个人的。
    """
    admin = await audit.create_user(db_session, "audit_dupe_admin", "admin")
    owner_a = await audit.create_user(db_session, "audit_dupe_a", "user")
    owner_b = await audit.create_user(db_session, "audit_dupe_b", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_turn(
        db_session, user=owner_a, conversation_id=conversation_id, query="A 的提问", summary="A 的回答"
    )
    await audit.seed_turn(
        db_session, user=owner_b, conversation_id=conversation_id, query="B 的提问", summary="B 的回答"
    )
    await ConversationResourceService.replace(
        owner_a["user_id"], conversation_id, {"project_name": "A 的项目"}
    )
    await ConversationResourceService.replace(
        owner_b["user_id"], conversation_id, {"project_name": "B 的项目"}
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/admin/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]

    project_by_user = {item["username"]: item["project_name"] for item in items}
    assert project_by_user == {
        owner_a["user_name"]: "A 的项目",
        owner_b["user_name"]: "B 的项目",
    }


async def test_admin_history_backfills_owner_from_username_for_legacy_rows(db_session, audit):
    """早期历史行只有 username、user_id 为空：项目范围要按用户名反查出的归属人读取。"""
    admin = await audit.create_user(db_session, "audit_legacy_admin", "admin")
    owner = await audit.create_user(db_session, "audit_legacy_owner", "user")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await audit.seed_legacy_turn(
        db_session,
        username=owner["user_name"],
        conversation_id=conversation_id,
        query="老数据的提问",
        summary="老数据的回答",
    )
    await ConversationResourceService.replace(
        owner["user_id"], conversation_id, {"project_name": "老数据的项目"}
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/admin/history",
            params={"conversation_id": conversation_id},
            headers=admin["headers"],
        )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]

    assert [item["project_name"] for item in items] == ["老数据的项目"]

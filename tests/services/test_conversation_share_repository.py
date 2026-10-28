"""快照装配与计数：分享必须冻结正文，且读内容才 +1。

本文件连真实数据库（``ai_conversation_shares`` / ``ai_agent_execution_history``），
因此**不加** ``no_infrastructure`` 标记；每个 fixture 自己清理造的数据，不污染共享库。
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-test-0001"
USER_ID = "share-test-user"
#: 智能体名冻结用例专用：独立的会话 ID 与不存在的智能体 ID
AGENT_CONV_ID = "conv-share-test-agent-name"
AGENT_ID = "agent-share-test-0001"


async def _purge_history(conversation_id: str) -> None:
    """删掉本文件造的历史轮次（共享库不做全表清理）。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AgentExecutionHistory).where(
                AgentExecutionHistory.conversation_id == conversation_id
            )
        )
        await session.commit()


async def _purge_shares(conversation_id: str) -> None:
    """删掉本文件造的分享记录。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(svc.ConversationShare).where(
                svc.ConversationShare.conversation_id == conversation_id
            )
        )
        await session.commit()


@pytest.fixture
async def seeded_turns():
    """造两轮问答：第一轮助手正文里带内部推理，用来验证清洗生效。"""
    await _purge_history(CONV_ID)
    async with AsyncSessionLocal() as session:
        for index, (query, summary) in enumerate(
            [
                ("第一问", "第一答 <thought>内部</thought>"),
                ("第二问", "第二答：463.9"),
            ]
        ):
            session.add(
                AgentExecutionHistory(
                    agent_id="agent-test",
                    trace_id=f"trace-share-{index}",
                    conversation_id=CONV_ID,
                    user_id=USER_ID,
                    username="share_tester",
                    query=query,
                    summary=summary,
                    status="success",
                    # 第一轮更早：正序快照的首条必须是「第一问」
                    created_at=datetime.now() - timedelta(minutes=10 - index),
                )
            )
        await session.commit()
    yield
    await _purge_history(CONV_ID)
    await _purge_shares(CONV_ID)


@pytest.fixture
async def seeded_agent_turn():
    """造一个真实存在的智能体 + 一轮问答，验证分享冻结的是 ``display_name``。"""
    await _purge_history(AGENT_CONV_ID)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AIAgent).where(AIAgent.id == AGENT_ID))
        session.add(
            AIAgent(id=AGENT_ID, name="share_test_agent", display_name="分享测试机器人")
        )
        session.add(
            AgentExecutionHistory(
                agent_id=AGENT_ID,
                trace_id="trace-share-agent-name",
                conversation_id=AGENT_CONV_ID,
                user_id=USER_ID,
                username="share_tester",
                query="智能体名问",
                summary="智能体名答",
                status="success",
            )
        )
        await session.commit()
    yield
    await _purge_history(AGENT_CONV_ID)
    await _purge_shares(AGENT_CONV_ID)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AIAgent).where(AIAgent.id == AGENT_ID))
        await session.commit()


async def test_snapshot_freezes_sanitized_content(seeded_turns):
    async with AsyncSessionLocal() as session:
        snapshot = await svc.assemble_snapshot(
            session, user_id=USER_ID, conversation_id=CONV_ID
        )
    assert snapshot is not None
    assert snapshot.turn_count == 2
    # 按时间正序：分享页要从第一问读到最后一问
    assert [m["role"] for m in snapshot.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert snapshot.messages[0]["content"] == "第一问"
    assert "内部" not in snapshot.messages[1]["content"]
    assert snapshot.messages[3]["content"] == "第二答：463.9"
    assert snapshot.title == "第一问"
    # 库里没有这个智能体：冻结名回退成 ID，不能是空串
    assert snapshot.agent_name == "agent-test"


async def test_snapshot_freezes_agent_display_name(seeded_agent_turn):
    async with AsyncSessionLocal() as session:
        snapshot = await svc.assemble_snapshot(
            session, user_id=USER_ID, conversation_id=AGENT_CONV_ID
        )
    assert snapshot is not None
    assert snapshot.agent_id == AGENT_ID
    assert snapshot.agent_name == "分享测试机器人"


async def test_snapshot_returns_none_for_foreign_conversation(seeded_turns):
    """别人的会话一律当作不存在：端点据此转 404，不泄露「有这个会话」。"""
    async with AsyncSessionLocal() as session:
        snapshot = await svc.assemble_snapshot(
            session, user_id="someone-else", conversation_id=CONV_ID
        )
    assert snapshot is None


async def test_create_list_revoke_and_count(seeded_turns):
    async with AsyncSessionLocal() as session:
        row = await svc.create_share(
            session,
            owner_user_id=USER_ID,
            owner_username="share_tester",
            conversation_id=CONV_ID,
            access_mode="secret",
            secret="abcd1234",
            expires_at=datetime.now() + timedelta(days=7),
        )
    assert row.share_id and len(row.share_id) >= 20
    assert row.view_count == 0
    assert row.access_mode == "secret"
    # 密码只存密文，且能解回原值
    assert row.secret_cipher and row.secret_cipher != "abcd1234"
    assert svc.decrypt_secret(row.secret_cipher) == "abcd1234"

    async with AsyncSessionLocal() as session:
        items, total = await svc.list_shares(
            session, user_id=USER_ID, page=1, page_size=20
        )
    assert total >= 1
    assert any(item.share_id == row.share_id for item in items)

    async with AsyncSessionLocal() as session:
        content = await svc.read_share_content(
            session,
            share_id=row.share_id,
            token=None,
            viewer_user_id=USER_ID,
            client_ip="127.0.0.1",
        )
    assert len(content["messages"]) == 4

    async with AsyncSessionLocal() as session:
        refreshed = await svc.get_share(session, row.share_id)
    assert refreshed.view_count == 1
    assert refreshed.last_viewed_at is not None

    async with AsyncSessionLocal() as session:
        assert await svc.revoke_share(session, user_id=USER_ID, share_id=row.share_id) is True
        # 重复作废不报错
        assert await svc.revoke_share(session, user_id=USER_ID, share_id=row.share_id) is True
        # 非本人不能作废
        assert await svc.revoke_share(session, user_id="other", share_id=row.share_id) is False

    async with AsyncSessionLocal() as session:
        with pytest.raises(svc.ShareNotFoundError):
            await svc.read_share_content(
                session,
                share_id=row.share_id,
                token=None,
                viewer_user_id=USER_ID,
                client_ip="127.0.0.1",
            )


async def test_view_count_only_increments_on_content_read(seeded_turns):
    """只有真正读出正文才 +1：读元信息、解锁失败都不算「被打开」。"""
    async with AsyncSessionLocal() as session:
        row = await svc.create_share(
            session,
            owner_user_id=USER_ID,
            owner_username="share_tester",
            conversation_id=CONV_ID,
            access_mode="secret",
            secret="abcd1234",
            expires_at=datetime.now() + timedelta(days=7),
        )

    async with AsyncSessionLocal() as session:
        meta = await svc.publish_public_meta(session, row.share_id)
    assert meta["access_mode"] == "secret"
    assert meta["remaining_seconds"] is not None and meta["remaining_seconds"] > 0
    assert meta["message_count"] == 4

    async with AsyncSessionLocal() as session:
        with pytest.raises(svc.ShareAccessDeniedError):
            await svc.unlock_share(
                session, share_id=row.share_id, secret="wrong-9999", client_ip="127.0.0.1"
            )

    async with AsyncSessionLocal() as session:
        refreshed = await svc.get_share(session, row.share_id)
    assert refreshed.view_count == 0, "读元信息与解锁失败都不应计数"
    assert refreshed.last_viewed_at is None

    async with AsyncSessionLocal() as session:
        await svc.read_share_content(
            session,
            share_id=row.share_id,
            token=None,
            viewer_user_id=USER_ID,
            client_ip="127.0.0.1",
        )
    async with AsyncSessionLocal() as session:
        refreshed = await svc.get_share(session, row.share_id)
    assert refreshed.view_count == 1
    assert refreshed.last_viewed_at is not None


async def test_secret_share_requires_valid_token(seeded_turns):
    async with AsyncSessionLocal() as session:
        row = await svc.create_share(
            session,
            owner_user_id=USER_ID,
            owner_username="share_tester",
            conversation_id=CONV_ID,
            access_mode="secret",
            secret="abcd1234",
            expires_at=None,
        )
    async with AsyncSessionLocal() as session:
        with pytest.raises(svc.ShareAccessDeniedError):
            await svc.read_share_content(
                session,
                share_id=row.share_id,
                token=None,
                viewer_user_id="visitor",
                client_ip="127.0.0.1",
            )
    # 解锁后拿到的 token 可以读
    async with AsyncSessionLocal() as session:
        token, _ttl = await svc.unlock_share(
            session, share_id=row.share_id, secret="abcd1234", client_ip="127.0.0.1"
        )
        content = await svc.read_share_content(
            session,
            share_id=row.share_id,
            token=token,
            viewer_user_id=None,
            client_ip="127.0.0.1",
        )
    assert content["title"]
    assert len(content["messages"]) == 4
    # 分享人自己带登录态直接读，不需要 token
    async with AsyncSessionLocal() as session:
        owner_view = await svc.read_share_content(
            session,
            share_id=row.share_id,
            token=None,
            viewer_user_id=USER_ID,
            client_ip="127.0.0.1",
        )
    assert owner_view["messages"]

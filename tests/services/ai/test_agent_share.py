"""智能体分享：副本邀请的完整生命周期。

分享的语义容易和「授权」搞混，这里用测试把边界钉住：

* 分享是**发一份副本**，不是把使用权授出去——接受后副本归接收方，源不受影响；
* 快照在分享那一刻生成，所以源后来被改、被删、被提升为系统级，已发出的分享仍然可用
  （唯一例外是升级为系统级时主动作废，见 ``test_becoming_system_revokes_pending_shares``）；
* 系统级智能体**不能**分享：它的可用范围由管理员统一授权治理；
* 接收方拿到的副本停在向导第一步、版本是 DRAFT，需要自己命名确认后才发布。
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.orm import Base
from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_share import (
    STATUS_ACCEPTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_REVOKED,
    STATUS_WITHDRAWN,
    AgentShareRequest,
)
from app.services.ai.agent_share_service import AgentShareService
from app.services.ai.agent_system_publication_service import AgentSystemPublicationService


def _ensure_share_table(sync_conn) -> None:
    """确保测试库里的分享表与模型定义一致。

    只做 ``create(checkfirst=True)`` 不够：表一旦存在就不会再改，
    模型加了列之后测试会报 ``Unknown column``。这里比对列名，不一致就重建。
    """
    from sqlalchemy import inspect

    table = Base.metadata.tables["ai_agent_share_requests"]
    inspector = inspect(sync_conn)
    if table.name in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        expected = {col.name for col in table.columns}
        if not expected.issubset(existing):
            table.drop(sync_conn, checkfirst=True)
    table.create(sync_conn, checkfirst=True)


@pytest.fixture
async def share_table(db_session):
    """V186 的表由测试自己建：本地库可能还没执行迁移。

    DDL 走 ``Base.metadata``，与迁移 SQL 保持同一份定义来源。
    """
    conn = await db_session.connection()
    await conn.run_sync(_ensure_share_table)
    AgentShareService.reset_probe_state_for_tests(None)
    yield
    await db_session.execute(delete(AgentShareRequest))
    await db_session.commit()
    AgentShareService.reset_probe_state_for_tests(None)


async def _make_user(session, *, name: str, status: int = 1):
    from app.models.user import User

    user = User(
        user_name=name,
        real_name=name,
        password_hash="x",
        # api_key_hash 是 NOT NULL 列，测试用户必须给值
        api_key_hash=uuid.uuid4().hex,
        status=status,
        email=f"{name}@example.com",
    )
    session.add(user)
    await session.flush()
    return user


async def _make_agent(session, *, owner: str, is_system: bool = False, tools=None):
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"share-{uuid.uuid4().hex[:8]}",
        display_name="分享测试智能体",
        created_by=owner,
        is_enabled=True,
        is_system=is_system,
        engine_type="LOCAL",
        onboarding_step="COMPLETE",
    )
    session.add(agent)
    await session.flush()
    session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=1,
            system_prompt="原始提示词",
            model_name="deepseek-chat",
            temperature=0.7,
            tools=tools if tools is not None else [],
            status="PUBLISHED",
        )
    )
    await session.flush()
    return agent


async def _cleanup(session, agent_ids, user_ids):
    if agent_ids:
        await session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids)))
        )
        await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
    if user_ids:
        from app.models.user import User

        await session.execute(delete(User).where(User.id.in_(list(user_ids))))
    await session.commit()


@pytest.mark.asyncio
async def test_share_creates_pending_record_with_snapshot(db_session, share_table):
    """分享 = 一条待接受记录 + 一份配置快照。"""
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()

    try:
        records = await AgentShareService.create_shares(
            db_session,
            agent.id,
            [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
            message="这个给你用",
        )
        await db_session.commit()

        assert len(records) == 1
        record = records[0]
        assert record.status == STATUS_PENDING
        assert record.recipient_user_name == peer_name
        assert record.expires_at is not None, "默认应当有过期时间"

        snapshot = json.loads(record.snapshot)
        assert snapshot["agent"]["name"] == agent.name
        assert snapshot["version"]["system_prompt"] == "原始提示词"
        assert snapshot["version"]["temperature"] == 0.7
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_system_agent_cannot_be_shared(db_session, share_table):
    """系统级智能体不提供分享——否则可以绕过管理员的授权治理往外发副本。"""
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name, is_system=True)
    await db_session.commit()

    try:
        with pytest.raises(HTTPException) as exc:
            await AgentShareService.create_shares(
                db_session,
                agent.id,
                [peer_name],
                {"role": "user", "user_name": owner_name, "user_id": owner.id},
            )
        assert exc.value.status_code == 409
        assert "系统级" in str(exc.value.detail)
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_only_owner_can_share(db_session, share_table):
    """不是自己的智能体不能分享。"""
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    other_name = f"other-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    other = await _make_user(db_session, name=other_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()

    try:
        with pytest.raises(HTTPException) as exc:
            await AgentShareService.create_shares(
                db_session,
                agent.id,
                [peer_name],
                {"role": "user", "user_name": other_name, "user_id": other.id},
            )
        assert exc.value.status_code == 403
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, other.id, peer.id])


@pytest.mark.asyncio
async def test_cannot_share_to_self_or_disabled_user(db_session, share_table):
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    off_name = f"off-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    off = await _make_user(db_session, name=off_name, status=0)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    ctx = {"role": "user", "user_name": owner_name, "user_id": owner.id}

    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as self_exc:
            await AgentShareService.create_shares(db_session, agent.id, [owner_name], ctx)
        assert self_exc.value.status_code == 400

        with pytest.raises(HTTPException) as off_exc:
            await AgentShareService.create_shares(db_session, agent.id, [off_name], ctx)
        assert off_exc.value.status_code == 409
        assert "停用" in str(off_exc.value.detail)
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, off.id])


@pytest.mark.asyncio
async def test_accept_produces_independent_draft_copy(db_session, share_table):
    """接受分享 = 得到一份属于自己的副本，版本是草稿、停在向导。"""
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        )
        await db_session.commit()

        copy = await AgentShareService.accept(
            db_session,
            records[0].id,
            {"role": "user", "user_name": peer_name, "user_id": peer.id},
            name=f"copy-{uuid.uuid4().hex[:6]}",
        )
        await db_session.commit()

        assert copy.created_by == peer_name, "副本必须归接收方"
        assert copy.is_system is False
        assert copy.onboarding_step == "BASIC", "副本要走向导：从重新命名开始，而不是直接可用"

        versions = await db_session.execute(
            AIAgentVersion.__table__.select().where(AIAgentVersion.agent_id == copy.id)
        )
        rows = versions.fetchall()
        assert len(rows) == 1
        assert rows[0].status == "DRAFT", "副本应当是草稿，由接收方自己发布"
        assert rows[0].system_prompt == "原始提示词"
        assert rows[0].temperature == 0.7, "除提示词外的配置要原样带过来"

        refreshed = await db_session.get(AgentShareRequest, records[0].id)
        assert refreshed.status == STATUS_ACCEPTED
        assert refreshed.new_agent_id == copy.id

        await _cleanup(db_session, [copy.id], [])
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_accept_rejects_other_peoples_share_and_duplicate_accept(db_session, share_table):
    """别人收到的分享不能代接受；同一条不能接受两次。"""
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    stranger_name = f"stranger-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    stranger = await _make_user(db_session, name=stranger_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        )
        await db_session.commit()

        with pytest.raises(HTTPException) as wrong_user:
            await AgentShareService.accept(
                db_session, records[0].id,
                {"role": "user", "user_name": stranger_name, "user_id": stranger.id},
                name=f"x-{uuid.uuid4().hex[:6]}",
            )
        assert wrong_user.value.status_code == 404

        copy = await AgentShareService.accept(
            db_session, records[0].id,
            {"role": "user", "user_name": peer_name, "user_id": peer.id},
            name=f"copy-{uuid.uuid4().hex[:6]}",
        )
        await db_session.commit()

        with pytest.raises(HTTPException) as twice:
            await AgentShareService.accept(
                db_session, records[0].id,
                {"role": "user", "user_name": peer_name, "user_id": peer.id},
                name=f"copy2-{uuid.uuid4().hex[:6]}",
            )
        assert twice.value.status_code == 409

        await _cleanup(db_session, [copy.id], [])
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id, stranger.id])


@pytest.mark.asyncio
async def test_reject_and_expire(db_session, share_table):
    """拒绝与过期都要落状态，且不能再被接受。"""
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        )
        await db_session.commit()

        rejected = await AgentShareService.reject(db_session, records[0].id, ctx)
        await db_session.commit()
        assert rejected.status == STATUS_REJECTED

        with pytest.raises(HTTPException) as after_reject:
            await AgentShareService.accept(
                db_session, records[0].id, ctx, name=f"y-{uuid.uuid4().hex[:6]}"
            )
        assert after_reject.value.status_code == 409

        # 另一条：手工把过期时间调到过去，读取时应惰性判定为 EXPIRED
        again = (await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        ))[0]
        again.expires_at = datetime.now() - timedelta(minutes=1)
        await db_session.commit()

        received = await AgentShareService.list_received(db_session, ctx)
        assert any(item.status == STATUS_EXPIRED for item in received)
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_becoming_system_revokes_pending_shares(db_session, share_table):
    """升级为系统级时，未接受的分享必须作废——否则仍能凭快照造出副本。"""
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        )
        await db_session.commit()

        revoked = await AgentShareService.revoke_pending_for_agent(
            db_session, agent.id, "该智能体已发布为系统级，请向管理员申请使用权限"
        )
        await db_session.commit()
        assert revoked == 1

        refreshed = await db_session.get(AgentShareRequest, records[0].id)
        assert refreshed.status == STATUS_REVOKED
        assert "系统级" in (refreshed.share_reason or "")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await AgentShareService.accept(
                db_session, records[0].id, ctx, name=f"z-{uuid.uuid4().hex[:6]}"
            )
        assert exc.value.status_code == 409
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_sent_list_and_withdraw(db_session, share_table):
    """分享人要能查到「我发出去的」并撤回还没被接受的那条。"""
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    owner_ctx = {"role": "user", "user_name": owner_name, "user_id": owner.id}
    peer_ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name], owner_ctx
        )
        await db_session.commit()

        sent = await AgentShareService.list_sent_shares(db_session, owner_ctx)
        assert [item.id for item in sent] == [records[0].id]
        assert sent[0].recipient_user_name == peer_name

        # 只有分享人能撤回
        with pytest.raises(HTTPException) as not_owner:
            await AgentShareService.withdraw(db_session, records[0].id, peer_ctx)
        assert not_owner.value.status_code == 403

        withdrawn = await AgentShareService.withdraw(db_session, records[0].id, owner_ctx)
        await db_session.commit()
        assert withdrawn.status == STATUS_WITHDRAWN

        # 撤回后不能再被接受
        with pytest.raises(HTTPException) as after:
            await AgentShareService.accept(
                db_session, records[0].id, peer_ctx, name=f"w-{uuid.uuid4().hex[:6]}"
            )
        assert after.value.status_code == 409
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_menu_counts(db_session, share_table):
    """侧边角标：待接受 / 待对方处理 / 我发出的总数。"""
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    owner_ctx = {"role": "user", "user_name": owner_name, "user_id": owner.id}
    peer_ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}

    try:
        await AgentShareService.create_shares(db_session, agent.id, [peer_name], owner_ctx)
        await db_session.commit()

        owner_counts = await AgentShareService.menu_counts(db_session, owner_ctx)
        assert owner_counts["sent_pending"] == 1
        assert owner_counts["sent_total"] == 1
        assert owner_counts["received_pending"] == 0

        peer_counts = await AgentShareService.menu_counts(db_session, peer_ctx)
        assert peer_counts["received_pending"] == 1
        assert peer_counts["sent_total"] == 0
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_accept_generates_unique_name_without_user_input(db_session, share_table):
    """不传名字时服务端给一个不冲突的名字；重名不把人卡住。"""
    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    owner_ctx = {"role": "user", "user_name": owner_name, "user_id": owner.id}
    peer_ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}
    created_ids = []

    try:
        # 同一个人接受两次（两条独立分享），第二次必须自动换个名字
        r1 = (await AgentShareService.create_shares(db_session, agent.id, [peer_name], owner_ctx))[0]
        r2 = (await AgentShareService.create_shares(db_session, agent.id, [peer_name], owner_ctx))[0]
        await db_session.commit()

        first = await AgentShareService.accept(db_session, r1.id, peer_ctx)
        second = await AgentShareService.accept(db_session, r2.id, peer_ctx)
        await db_session.commit()
        created_ids = [first.id, second.id]

        assert first.name.endswith("-copy")
        assert second.name != first.name
        assert second.name.endswith("-copy-2")
    finally:
        await _cleanup(db_session, [agent.id, *created_ids], [owner.id, peer.id])


def test_snapshot_is_mapped_as_column():
    """``snapshot`` 必须是真正的列。

    曾经写成 ``snapshot = _snapshot_text()``：SQLAlchemy 不认这是个列，属性保留成
    ``Text`` 类型对象，接受分享时 ``json.loads`` 直接抛
    "the JSON object must be str, bytes or bytearray, not Text"。
    """
    from app.models.agent_share import AgentShareRequest

    assert "snapshot" in AgentShareRequest.__table__.c
    assert AgentShareRequest.snapshot.property.columns[0] is AgentShareRequest.__table__.c["snapshot"]


@pytest.mark.asyncio
async def test_accept_rejects_missing_snapshot(db_session, share_table):
    """快照为空时不能造空壳副本，要明确提示重新分享。

    历史数据里出现过 snapshot 为 NULL 的记录（模型漏了 Column 映射），
    直接 json.loads("{}") 会造出一个没有提示词、没有工具的空智能体。
    """
    from fastapi import HTTPException

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    ctx = {"role": "user", "user_name": peer_name, "user_id": peer.id}

    try:
        records = await AgentShareService.create_shares(
            db_session, agent.id, [peer_name],
            {"role": "user", "user_name": owner_name, "user_id": owner.id},
        )
        await db_session.commit()

        records[0].snapshot = None
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await AgentShareService.accept(db_session, records[0].id, ctx, name="x")
        assert exc.value.status_code == 409
        assert "重新分享" in str(exc.value.detail)
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])


@pytest.mark.asyncio
async def test_revoke_notifies_recipients(db_session, share_table):
    """源变成系统级时，待接受的分享要作废，并且通知到接收人。

    commit 正文与文档都承诺了这条通知。只改状态不发信的话，接收方只会看到
    记录凭空消失，既不知道原因也不知道该去哪儿申请授权。
    """
    from app.models.portal_notification import PortalNotification

    owner_name = f"owner-{uuid.uuid4().hex[:6]}"
    peer_name = f"peer-{uuid.uuid4().hex[:6]}"
    owner = await _make_user(db_session, name=owner_name)
    peer = await _make_user(db_session, name=peer_name)
    agent = await _make_agent(db_session, owner=owner_name)
    await db_session.commit()
    ctx = {"role": "user", "user_name": owner_name, "user_id": owner.id}

    try:
        records = await AgentShareService.create_shares(db_session, agent.id, [peer_name], ctx)
        await db_session.commit()

        revoked = await AgentShareService.revoke_pending_for_agent(
            db_session, agent.id, "该智能体已发布为系统级，请向管理员申请使用权限"
        )
        await db_session.commit()
        assert revoked == 1

        rows = (
            await db_session.execute(
                select(PortalNotification).where(
                    PortalNotification.user_id == peer.id,
                    PortalNotification.resource_type == "agent_share",
                )
            )
        ).scalars().all()
        assert rows, "作废分享必须给接收人发站内信"
        assert any("作废" in (item.title or "") for item in rows)
    finally:
        await _cleanup(db_session, [agent.id], [owner.id, peer.id])

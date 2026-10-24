"""系统级发布的状态机与硬约束。

覆盖用户明确要求的几条边界：

* **没有已发布版本就不能设为系统级**——运行时只按 ``status='PUBLISHED'`` 取配置，
  设为系统级后取不到版本等于一个点了就报错的智能体；申请、直接发布、审核通过三处都要拦。
* 非 LOCAL 引擎（RAGFLOW / OPENCLAW）没有版本概念，同样拦掉。
* 待审期间冻结**所有者**（管理员豁免），撤回或驳回后立即解冻。
* 下架保留授权（重新上架自动恢复），通知可以关掉，但关掉了也要留下「影响了几个人」。
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_system import AgentSystemEvent, AgentSystemRequest
from app.models.permission import ResourcePermission
from app.models.portal_notification import PortalNotification
from app.models.user import User
from app.schemas.agent import AIAgentVersionBase
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.agent_system_columns import (
    SYSTEM_STATUS_PENDING,
    read_system_status,
    reset_probe_state_for_tests,
    supports_system_status,
    write_system_status,
)
from app.services.ai.skill_resolver import MAIN_GENERAL_AGENT_ID
from app.services.ai.agent_system_publication_service import (
    ACTION_PUBLISH,
    ACTION_UNPUBLISH,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AgentSystemPublicationService as Svc,
)


@pytest.fixture(autouse=True)
async def _require_migration():
    """未执行 V185 迁移时跳过：功能依赖新表与新列，跳过比误报红更诚实。"""
    reset_probe_state_for_tests(None)
    async with AsyncSessionLocal() as session:
        if not await supports_system_status(session):
            pytest.skip("需要先执行 db-prod/V185-create_agent_system_publication.sql")


async def _make_user(session, *, role: str = "user") -> User:
    user = User(
        user_name=f"syspub_{uuid.uuid4().hex[:10]}",
        real_name="发布测试用户",
        role=role,
        password_hash="x",
        # api_key_hash 在库里是 NOT NULL（带唯一索引），测试用户也要给一个占位值
        api_key_hash=uuid.uuid4().hex,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_agent(
    session,
    *,
    owner: str,
    engine: str = "LOCAL",
    published: bool = True,
    versions: int = 1,
) -> AIAgent:
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"syspub-{uuid.uuid4().hex[:10]}",
        display_name="系统级测试智能体",
        created_by=owner,
        is_system=False,
        is_enabled=True,
        engine_type=engine,
    )
    session.add(agent)
    await session.flush()

    for index in range(versions):
        session.add(
            AIAgentVersion(
                id=str(uuid.uuid4()),
                agent_id=agent.id,
                version_number=index + 1,
                system_prompt="你是一个测试助手",
                model_name="deepseek-chat",
                status="PUBLISHED" if published else "DRAFT",
            )
        )
    await session.flush()
    return agent


async def _cleanup(*agent_ids: str, user_ids: tuple = ()) -> None:
    async with AsyncSessionLocal() as session:
        if agent_ids:
            await session.execute(
                delete(AgentSystemRequest).where(
                    AgentSystemRequest.agent_id.in_(list(agent_ids))
                )
            )
            await session.execute(
                delete(AgentSystemEvent).where(
                    AgentSystemEvent.agent_id.in_(list(agent_ids))
                )
            )
            await session.execute(
                delete(ResourcePermission).where(
                    ResourcePermission.resource_type == "agent",
                    ResourcePermission.resource_id.in_(list(agent_ids)),
                )
            )
            await session.execute(
                delete(PortalNotification).where(
                    PortalNotification.resource_type == "agent",
                    PortalNotification.resource_id.in_(list(agent_ids)),
                )
            )
            await session.execute(
                delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids)))
            )
            await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
        if user_ids:
            await session.execute(delete(User).where(User.id.in_(list(user_ids))))
        await session.commit()


async def test_publish_requires_published_version():
    """只有草稿版本时，直接发布与提交申请都必须 400，且不改 is_system。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(
            session, owner=owner.user_name, published=False, versions=2
        )
        agent_id, owner_id = agent.id, owner.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            with pytest.raises(HTTPException) as draft_publish:
                await Svc.publish_system(session, agent_id, actor, reason=None)
            assert draft_publish.value.status_code == 400
            assert "发布至少一个版本" in draft_publish.value.detail

            with pytest.raises(HTTPException) as draft_apply:
                await Svc.submit_request(session, agent_id, actor, comment=None)
            assert draft_apply.value.status_code == 400

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent_id)
            assert stored.is_system is False
            status, _ = await read_system_status(session, agent_id)
            assert status == "NONE"
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_publish_rejects_non_local_engine():
    """RAGFLOW 这类没有版本的引擎不能进系统级。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name, engine="RAGFLOW")
        agent_id, owner_id = agent.id, owner.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            with pytest.raises(HTTPException) as exc:
                await Svc.publish_system(session, agent_id, actor, reason=None)
            assert exc.value.status_code == 400
            assert "引擎" in exc.value.detail
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_submit_freezes_owner_and_cancel_unfreezes():
    """申请后所有者编辑被冻结（409），撤回后恢复；管理员始终不受影响。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id = agent.id, owner.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            request = await Svc.submit_request(session, agent_id, actor, comment="稳定运行两个月")

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent_id)
            status, pending = await read_system_status(session, agent_id)
            assert status == "PENDING_REVIEW"
            assert pending is not None

            owner_actor = {"user_id": owner_id, "user_name": stored.created_by, "role": "user"}
            assert await Svc.is_frozen(session, stored) is True
            with pytest.raises(HTTPException) as frozen:
                await Svc.assert_not_frozen(session, stored, owner_actor)
            assert frozen.value.status_code == 409

            # 管理员是审核方，待审期间照常可改
            await Svc.assert_not_frozen(
                session, stored, {"user_id": 1, "user_name": "admin", "role": "admin"}
            )

        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            cancelled = await Svc.cancel_request(session, agent_id, actor)
            assert cancelled.status == STATUS_CANCELLED

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent_id)
            status, pending = await read_system_status(session, agent_id)
            assert status == "NONE"
            assert pending is None
            await Svc.assert_not_frozen(
                session, stored, {"user_id": owner_id, "user_name": stored.created_by, "role": "user"}
            )
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_approve_publishes_and_grants_owner():
    """审核通过：置系统级、记 PUBLISH 事件、自动授权所有者、申请单置 APPROVED。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id = agent.id, owner.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            request = await Svc.submit_request(session, agent_id, actor, comment=None)
            request_id = request.id

        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            published = await Svc.approve_request(session, request_id, admin, comment="配置已核对")
            assert published.is_system is True
            status, pending = await read_system_status(session, agent_id)
            assert status == "NONE"
            assert pending is None

        async with AsyncSessionLocal() as session:
            request = await session.get(AgentSystemRequest, request_id)
            assert request.status == STATUS_APPROVED
            assert request.review_comment == "配置已核对"

            events = (
                await session.execute(
                    select(AgentSystemEvent).where(
                        AgentSystemEvent.agent_id == agent_id,
                        AgentSystemEvent.action == ACTION_PUBLISH,
                    )
                )
            ).scalars().all()
            assert len(events) == 1
            assert events[0].snapshot is not None
            assert "system_prompt" in events[0].snapshot

            grants = (
                await session.execute(
                    select(ResourcePermission).where(
                        ResourcePermission.resource_type == "agent",
                        ResourcePermission.resource_id == agent_id,
                    )
                )
            ).scalars().all()
            assert any(int(g.user_id) == owner_id for g in grants)
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_reject_requires_reason_and_unfreezes():
    """驳回必须给理由；驳回后申请单关闭、内容解冻、通知申请人。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id = agent.id, owner.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            request = await Svc.submit_request(session, agent_id, actor, comment=None)
            request_id = request.id

        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            with pytest.raises(HTTPException) as exc:
                await Svc.reject_request(session, request_id, admin, comment="  ")
            assert exc.value.status_code == 400

        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            rejected = await Svc.reject_request(
                session, request_id, admin, comment="工具依赖个人凭证，请先换成公共工具"
            )
            assert rejected.status == STATUS_REJECTED

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent_id)
            status, _ = await read_system_status(session, agent_id)
            assert status == "NONE"
            assert stored.is_system is False

            notifications = (
                await session.execute(
                    select(PortalNotification).where(
                        PortalNotification.resource_id == agent_id,
                        PortalNotification.user_id == owner_id,
                    )
                )
            ).scalars().all()
            assert any("驳回" in n.title for n in notifications)
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_unpublish_keeps_grant_and_records_impact():
    """下架保留授权（可恢复），记录影响人数与是否通知。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        # 「已分配」口径指的是别人能用它，所以要有一个真正的被分配人；
        # 所有者自己也会被授权（申请通过/发布时自动授权），但不该计入
        colleague = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id, colleague_id = agent.id, owner.id, colleague.id
        agent.is_system = True
        for uid in (owner_id, colleague_id):
            session.add(
                ResourcePermission(
                    user_id=uid, resource_type="agent", resource_id=agent_id, enabled=True
                )
            )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            result = await Svc.unpublish_system(
                session, agent_id, admin, reason="业务迁移到 2.0", notify=False
            )
            assert result["affected_user_count"] == 1, "所有者不计入「已分配」，只有同事算"
            assert result["notified_user_count"] == 0

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent_id)
            assert stored.is_system is False

            event = (
                await session.execute(
                    select(AgentSystemEvent).where(
                        AgentSystemEvent.agent_id == agent_id,
                        AgentSystemEvent.action == ACTION_UNPUBLISH,
                    )
                )
            ).scalars().first()
            assert event.affected_user_count == 1
            assert event.notified is False
            assert "业务迁移" in event.reason

            grants = (
                await session.execute(
                    select(ResourcePermission).where(
                        ResourcePermission.resource_type == "agent",
                        ResourcePermission.resource_id == agent_id,
                    )
                )
            ).scalars().all()
            assert len(grants) == 2, "下架不能删授权，否则重新上架要重新分配"
    finally:
        await _cleanup(agent_id, user_ids=(owner_id, colleague_id))


async def test_unpublish_notifies_recipients_when_enabled():
    """勾选通知时，直配用户会收到站内信。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id = agent.id, owner.id
        agent.is_system = True
        session.add(
            ResourcePermission(
                user_id=owner_id, resource_type="agent", resource_id=agent_id, enabled=True
            )
        )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            result = await Svc.unpublish_system(
                session, agent_id, admin, reason="不再维护", notify=True
            )
            assert result["notified_user_count"] == 1

        async with AsyncSessionLocal() as session:
            notifications = (
                await session.execute(
                    select(PortalNotification).where(
                        PortalNotification.resource_id == agent_id,
                        PortalNotification.user_id == owner_id,
                    )
                )
            ).scalars().all()
            assert any("已下架" in n.title for n in notifications)
            assert notifications[0].category == "agent"
    finally:
        await _cleanup(agent_id, user_ids=(owner_id,))


async def test_history_only_visible_to_admin_and_owner():
    """发布历史只对管理员与创建者开放。"""
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        other = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name)
        agent_id, owner_id, other_id = agent.id, owner.id, other.id
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            await Svc.submit_request(session, agent_id, actor, comment=None)

        async with AsyncSessionLocal() as session:
            owner_actor = {"user_id": owner_id, "user_name": owner.user_name, "role": "user"}
            result = await Svc.history(session, agent_id, owner_actor)
            assert result["total"] >= 1
            assert result["items"][0]["kind"] == "request"

        async with AsyncSessionLocal() as session:
            outsider = {"user_id": other_id, "user_name": other.user_name, "role": "user"}
            with pytest.raises(HTTPException) as exc:
                await Svc.history(session, agent_id, outsider)
            assert exc.value.status_code == 403

        async with AsyncSessionLocal() as session:
            admin = {"user_id": 1, "user_name": "admin", "role": "admin"}
            assert (await Svc.history(session, agent_id, admin))["total"] >= 1
    finally:
        await _cleanup(agent_id, user_ids=(owner_id, other_id))


@pytest.mark.asyncio
async def test_system_agent_owner_can_edit_drafts_but_not_publish(db_session):
    """上架为系统级后，所有者可以改草稿，但**不能发布上线**。

    边界划在「线上基线只能由管理员变更」，而不是「一个字都不能改」：
    完全只读会把所有者逼去「复制一份」另起一条，代价是版本历史断裂，
    而且他已经无法在原有配置上继续迭代了。所以放开建/改/删草稿，只拦发布。
    """
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    stranger = await _make_user(db_session, role="user")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)

    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=99,
        system_prompt="草稿内容",
        model_name="deepseek-chat",
        status="DRAFT",
    )
    db_session.add(draft)
    await db_session.flush()

    try:
        await Svc.publish_system(
            db_session,
            agent.id,
            {"role": "admin", "user_name": admin.user_name},
            reason="作为基线",
        )

        payload = AIAgentVersionBase(system_prompt="继续迭代", model_name="deepseek-chat")
        actor = {"role": "user", "user_name": owner.user_name}

        created = await AgentManagerService.create_agent_version(
            db_session, agent.id, payload, user=actor
        )
        assert created is not None, "系统级之后所有者仍可以新建草稿版本"

        updated = await AgentManagerService.update_agent_version(
            db_session, agent.id, draft.id, payload, user=actor
        )
        assert updated is not None, "系统级之后所有者仍可以改草稿"

        assert (
            await AgentManagerService.publish_version(
                db_session, agent.id, draft.id, user=actor
            )
            is False
        ), "系统级之后所有者不能把草稿发布上线"

        assert (
            await AgentManagerService.delete_version(
                db_session, agent.id, draft.id, user=actor
            )
            is True
        ), "系统级之后所有者仍可以删自己的草稿"

        # 非所有者：连草稿都不能碰（系统级内容对他人始终只读）
        stranger_payload = AIAgentVersionBase(system_prompt="路人改", model_name="deepseek-chat")
        stranger_actor = {"role": "user", "user_name": stranger.user_name}
        assert (
            await AgentManagerService.create_agent_version(
                db_session, agent.id, stranger_payload, user=stranger_actor
            )
            is None
        ), "非所有者不能给系统级智能体建版本"

        # 管理员不受影响
        admin_created = await AgentManagerService.create_agent_version(
            db_session, agent.id, payload, user={"role": "admin", "user_name": admin.user_name}
        )
        assert admin_created is not None, "管理员仍然可以给系统级智能体建版本"
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id, stranger.id))


@pytest.mark.asyncio
async def test_owner_can_request_release_of_draft_version(db_session):
    """系统级所有者改完草稿后，必须有通道把它推上线。

    不能发布是设计约束（线上基线要管理员点头），但「改完就卡住」不是——
    这条通道就是补这一公里：提交申请 → 冻结 → 管理员审。
    """
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)

    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=7,
        system_prompt="改好的新版本",
        model_name="deepseek-chat",
        status="DRAFT",
    )
    db_session.add(draft)
    await db_session.flush()

    try:
        await Svc.publish_system(
            db_session, agent.id, {"role": "admin", "user_name": admin.user_name}, reason="基线"
        )

        request = await Svc.submit_release_request(
            db_session,
            agent.id,
            draft.id,
            {"role": "user", "user_name": owner.user_name},
            comment="修了工具描述",
        )
        assert request.request_type == "VERSION_RELEASE"
        assert request.snapshot_version_id == draft.id

        # 待审期间冻结所有者，避免「申请的是这版，审核时被改成别的」
        status, pending_id = await read_system_status(db_session, agent.id)
        assert status == "PENDING_REVIEW"
        assert pending_id == request.id
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id))


@pytest.mark.asyncio
async def test_release_request_rejects_wrong_cases(db_session):
    """上线申请的四条边界：非系统级、管理员、非草稿、已有待审。"""
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)
    actor = {"role": "user", "user_name": owner.user_name}

    try:
        # 非系统级：直接发布即可，不需要申请
        with pytest.raises(HTTPException) as not_system:
            await Svc.submit_release_request(db_session, agent.id, "any", actor)
        assert not_system.value.status_code == 409

        await Svc.publish_system(
            db_session, agent.id, {"role": "admin", "user_name": admin.user_name}, reason="基线"
        )

        # 管理员：可以直接发，不该走申请
        with pytest.raises(HTTPException) as admin_case:
            await Svc.submit_release_request(
                db_session, agent.id, "any", {"role": "admin", "user_name": admin.user_name}
            )
        assert admin_case.value.status_code == 400

        # 非草稿：已发布版本没有「上线」可言
        published = await Svc.assert_publishable(db_session, agent)
        with pytest.raises(HTTPException) as not_draft:
            await Svc.submit_release_request(db_session, agent.id, published.id, actor)
        assert not_draft.value.status_code == 409

        # 已有待审：不能叠第二张单子
        draft = AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=8,
            system_prompt="草稿",
            model_name="deepseek-chat",
            status="DRAFT",
        )
        db_session.add(draft)
        await db_session.flush()
        await Svc.submit_release_request(db_session, agent.id, draft.id, actor)
        with pytest.raises(HTTPException) as duplicated:
            await Svc.submit_release_request(db_session, agent.id, draft.id, actor)
        assert duplicated.value.status_code == 409
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id))


@pytest.mark.asyncio
async def test_approving_release_request_publishes_that_version(db_session):
    """审核通过：申请的那一版变成线上版本，旧线上版本归档，并解冻。"""
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)

    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=9,
        system_prompt="要上线的草稿",
        model_name="deepseek-chat",
        status="DRAFT",
    )
    db_session.add(draft)
    await db_session.flush()

    try:
        await Svc.publish_system(
            db_session, agent.id, {"role": "admin", "user_name": admin.user_name}, reason="基线"
        )
        old_published = await Svc.assert_publishable(db_session, agent)

        request = await Svc.submit_release_request(
            db_session, agent.id, draft.id, {"role": "user", "user_name": owner.user_name}
        )

        await Svc.approve_request(
            db_session, request.id, {"role": "admin", "user_name": admin.user_name}, comment="可以"
        )

        await db_session.refresh(draft)
        await db_session.refresh(old_published)
        assert draft.status == "PUBLISHED", "申请的草稿应被发布上线"
        assert old_published.status == "ARCHIVED", "旧线上版本应归档"

        status, pending_id = await read_system_status(db_session, agent.id)
        assert status == "NONE" and pending_id is None, "审核完必须解冻"

        # 系统级状态没有被误改（这条通道只发版本，不碰 is_system）
        await db_session.refresh(agent)
        assert bool(agent.is_system) is True
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id))


@pytest.mark.asyncio
async def test_rejecting_release_request_keeps_version_as_draft(db_session):
    """驳回：版本仍是草稿，智能体解冻，可以继续改完再申请。"""
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)

    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=10,
        system_prompt="草稿",
        model_name="deepseek-chat",
        status="DRAFT",
    )
    db_session.add(draft)
    await db_session.flush()

    try:
        await Svc.publish_system(
            db_session, agent.id, {"role": "admin", "user_name": admin.user_name}, reason="基线"
        )
        request = await Svc.submit_release_request(
            db_session, agent.id, draft.id, {"role": "user", "user_name": owner.user_name}
        )

        await Svc.reject_request(
            db_session, request.id, {"role": "admin", "user_name": admin.user_name}, comment="描述不清楚"
        )

        await db_session.refresh(draft)
        assert draft.status == "DRAFT", "驳回后版本必须还是草稿"

        status, pending_id = await read_system_status(db_session, agent.id)
        assert status == "NONE" and pending_id is None
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id))


@pytest.mark.asyncio
async def test_baseline_publish_and_review_timeline_are_recorded(db_session):
    """四条审计观感问题的回归：

    * 管理员给系统级智能体发新版本 = 换线上基线，发布历史里必须有一条；
    * 审核队列要显示「谁提交的」（前端一直在读 requested_by_name，后端曾不返回）；
    * 已审完的申请在时间线上用**审核时间**排序展示，否则「通过」会落在提交时刻；
    * 下架时所有者即使不在「已分配」名单里，也要单独收到通知。
    """
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)
    # 带上 user_id：申请单的 requested_by 与事件 actor_id 都取自它
    admin_actor = {"role": "admin", "user_name": admin.user_name, "user_id": admin.id}
    owner_actor = {"role": "user", "user_name": owner.user_name, "user_id": owner.id}

    try:
        # 所有者申请 → 管理员通过，制造一条已审完的申请单
        submitted = await Svc.submit_request(
            db_session, agent.id, owner_actor, comment="想做成公司基线"
        )
        submitted_id = submitted.id
        approved = await Svc.approve_request(
            db_session, submitted_id, admin_actor, comment="内容达标"
        )
        assert bool(approved.is_system) is True

        # I3：审核队列里能看出申请人是谁
        queue = await Svc.list_requests(db_session, status="APPROVED")
        item = next(entry for entry in queue if entry["id"] == submitted_id)
        assert item["requested_by_name"] == owner.user_name, "审核队列缺申请人姓名"

        # I4：已审完的单子在时间线上取审核时间，并保留提交时间
        history = await Svc.history(db_session, agent.id, admin_actor, limit=50)
        entry = next(e for e in history["items"] if e["id"] == submitted_id)
        assert entry["reviewed_at"] is not None
        assert entry["at"] == entry["reviewed_at"], "时间线应显示审核时间而不是提交时间"
        assert entry["requested_at"] is not None

        # I1：发布新基线要留痕
        new_version = AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=7,
            system_prompt="第二版基线",
            model_name="deepseek-chat",
            status="DRAFT",
        )
        db_session.add(new_version)
        await db_session.flush()
        published = await AgentManagerService.publish_version(
            db_session, agent.id, new_version.id, user=admin_actor
        )
        assert published is True

        history = await Svc.history(db_session, agent.id, admin_actor, limit=50)
        baseline_events = [
            e for e in history["items"] if e.get("action") == "BASELINE_PUBLISH"
        ]
        assert baseline_events, "发布新基线应有事件记录"
        assert baseline_events[0]["version_number"] == 7

        # I2：下架时所有者单独收到「已解冻」通知
        await Svc.unpublish_system(
            db_session, agent.id, admin_actor, reason="退回团队自维护", notify=True
        )
        owner_notifications = (
            await db_session.execute(
                select(PortalNotification).where(
                    PortalNotification.user_id == owner.id,
                    PortalNotification.title.like("%已下架为普通智能体%"),
                )
            )
        ).scalars().all()
        assert owner_notifications, "所有者应收到下架通知"
        assert "可以继续编辑" in (owner_notifications[0].content or "")
    finally:
        await _cleanup(agent.id, user_ids=(owner.id, admin.id))


# ----------------------------------------------------------------------
# 权属人变更：原所有者离职后，智能体不能变成没人能编辑的孤儿
# ----------------------------------------------------------------------


async def test_admin_can_transfer_owner_and_it_lands_in_history():
    """管理员把智能体转给别人：created_by 变更、事件落库、新所有者收到站内信。"""
    async with AsyncSessionLocal() as session:
        old_owner = await _make_user(session)
        new_owner = await _make_user(session)
        admin = await _make_user(session, role="admin")
        agent = await _make_agent(session, owner=old_owner.user_name)
        agent_id = agent.id
        user_ids = (old_owner.id, new_owner.id, admin.id)
        admin_ctx = {"role": "admin", "user_name": admin.user_name, "user_id": admin.id, "id": admin.id}

        try:
            await session.commit()
            moved = await Svc.transfer_owner(
                session, agent_id, new_owner.user_name, admin_ctx, reason="原负责人离职"
            )
            assert moved.created_by == new_owner.user_name

            fresh = await session.execute(select(AIAgent).where(AIAgent.id == agent_id))
            assert fresh.scalars().first().created_by == new_owner.user_name

            events = await session.execute(
                select(AgentSystemEvent).where(
                    AgentSystemEvent.agent_id == agent_id,
                    AgentSystemEvent.action == "TRANSFER_OWNER",
                )
            )
            event = events.scalars().first()
            assert event is not None, "权属人变更必须留下事件"
            assert old_owner.user_name in (event.reason or "")
            assert new_owner.user_name in (event.reason or "")
            assert "原负责人离职" in (event.reason or "")

            notes = await session.execute(
                select(PortalNotification).where(
                    PortalNotification.resource_type == "agent",
                    PortalNotification.resource_id == agent_id,
                )
            )
            assert any(n.user_id == new_owner.id for n in notes.scalars().all()), \
                "新所有者必须收到站内信"
        finally:
            await _cleanup(agent_id, user_ids=user_ids)


async def test_transfer_owner_rejects_wrong_cases():
    """非管理员 / 主助手 / 停用账号 / 同名 / 待审中都必须被挡住。"""
    async with AsyncSessionLocal() as session:
        old_owner = await _make_user(session)
        new_owner = await _make_user(session)
        disabled = await _make_user(session)
        disabled.status = 0
        admin = await _make_user(session, role="admin")
        agent = await _make_agent(session, owner=old_owner.user_name)
        pending_agent = await _make_agent(session, owner=old_owner.user_name)
        agent_ids = (agent.id, pending_agent.id)
        user_ids = (old_owner.id, new_owner.id, disabled.id, admin.id)

        user_ctx = {"role": "user", "user_name": old_owner.user_name, "user_id": old_owner.id, "id": old_owner.id}
        admin_ctx = {"role": "admin", "user_name": admin.user_name, "user_id": admin.id, "id": admin.id}

        try:
            await session.commit()

            with pytest.raises(HTTPException) as denied:
                await Svc.transfer_owner(session, agent.id, new_owner.user_name, user_ctx)
            assert denied.value.status_code == 403

            # 主助手在库里真实存在（固定 ID），直接拿它试；不能新建，主键会撞
            main_row = await session.execute(
                select(AIAgent).where(AIAgent.id == MAIN_GENERAL_AGENT_ID)
            )
            main_agent = main_row.scalars().first()
            assert main_agent is not None, "库里应存在主助手"
            with pytest.raises(HTTPException) as main_denied:
                await Svc.transfer_owner(session, main_agent.id, new_owner.user_name, admin_ctx)
            assert main_denied.value.status_code == 409
            assert "主助手" in main_denied.value.detail

            with pytest.raises(HTTPException) as same_owner:
                await Svc.transfer_owner(session, agent.id, old_owner.user_name, admin_ctx)
            assert same_owner.value.status_code == 400

            with pytest.raises(HTTPException) as missing:
                await Svc.transfer_owner(session, agent.id, "no-such-user", admin_ctx)
            assert missing.value.status_code == 404

            with pytest.raises(HTTPException) as inactive:
                await Svc.transfer_owner(session, agent.id, disabled.user_name, admin_ctx)
            assert inactive.value.status_code == 409

            # 待审期间不允许换人：申请单记的是当时的申请人，换了人对不上
            await write_system_status(
                session, pending_agent.id, status=SYSTEM_STATUS_PENDING, pending_request_id=None
            )
            await session.commit()
            with pytest.raises(HTTPException) as pending:
                await Svc.transfer_owner(
                    session, pending_agent.id, new_owner.user_name, admin_ctx
                )
            assert pending.value.status_code == 409
        finally:
            await _cleanup(*agent_ids, user_ids=user_ids)


async def test_transfer_owner_reports_tools_the_new_owner_cannot_use():
    """转交后要告诉管理员：新权属人有几个工具用不了。

    这不是假设——智能体的工具里可能引用原所有者私有的 MCP 服务，换人之后那些
    引用对新所有者永久不可见（工具选择器不渲染，也就取消不掉）。转交本身不改
    配置（身份变更不该顺手动内容），但必须把这份清单交出来，否则新权属人接手后
    只会看到「配置都在、就是跑不起来」。
    """
    import uuid as _uuid

    from app.models.mcp import McpServer, McpToolCache

    async with AsyncSessionLocal() as session:
        old_owner = await _make_user(session)
        new_owner = await _make_user(session)
        admin = await _make_user(session, role="admin")
        agent = await _make_agent(session, owner=old_owner.user_name)
        agent_id = agent.id
        user_ids = (old_owner.id, new_owner.id, admin.id)
        admin_ctx = {"role": "admin", "user_name": admin.user_name, "user_id": admin.id, "id": admin.id}

        private_server = McpServer(
            id=str(_uuid.uuid4()),
            server_name=f"xfer-{_uuid.uuid4().hex[:8]}",
            sse_url="http://localhost:1/sse",
            enabled_status=1,
            scope="personal",
            user_id=old_owner.id,
        )
        session.add(private_server)
        await session.flush()
        session.add(
            McpToolCache(
                id=str(_uuid.uuid4()),
                server_id=private_server.id,
                tool_name="secret-tool",
                is_published=True,
                is_available=True,
            )
        )
        await session.flush()

        version_row = await session.execute(
            select(AIAgentVersion).where(AIAgentVersion.agent_id == agent_id)
        )
        version = version_row.scalars().first()
        version.tools = [f"{private_server.server_name}:secret-tool"]
        await session.commit()

        try:
            moved = await Svc.transfer_owner(
                session, agent_id, new_owner.user_name, admin_ctx
            )
            dropped = getattr(moved, "dropped_tool_refs", [])
            assert [d["name"] for d in dropped] == [
                f"{private_server.server_name}:secret-tool"
            ], "转交时应报出新权属人用不了的工具"

            # 清单要落到历史里，事后能查到「转交时就有这个问题」
            events = await session.execute(
                select(AgentSystemEvent).where(
                    AgentSystemEvent.agent_id == agent_id,
                    AgentSystemEvent.action == "TRANSFER_OWNER",
                )
            )
            reasons = [e.reason or "" for e in events.scalars().all()]
            assert any("无权使用" in r for r in reasons), "工具不可用的情况应记入历史"
        finally:
            await session.execute(
                delete(McpToolCache).where(McpToolCache.server_id == private_server.id)
            )
            await session.delete(private_server)
            await session.commit()
            await _cleanup(agent_id, user_ids=user_ids)


async def test_publish_version_records_event_for_non_system_agent():
    """普通智能体发布新版本也要留痕，不只是系统级。

    普通智能体没有审核环节，发布完全由所有者自行决定；如果这里不记事件，
    「线上什么时候变成了 V3、是谁发的」在发布历史里查不到。
    """
    async with AsyncSessionLocal() as session:
        owner = await _make_user(session)
        agent = await _make_agent(session, owner=owner.user_name, published=False, versions=1)
        agent_id = agent.id
        owner_ctx = {"role": "user", "user_name": owner.user_name, "user_id": owner.id, "id": owner.id}

        version_row = await session.execute(
            select(AIAgentVersion).where(AIAgentVersion.agent_id == agent_id)
        )
        version = version_row.scalars().first()
        version_id = version.id
        await session.commit()

        try:
            assert agent.is_system is False
            await AgentManagerService.publish_version(session, agent_id, version_id, user=owner_ctx)

            events = await session.execute(
                select(AgentSystemEvent).where(
                    AgentSystemEvent.agent_id == agent_id,
                    AgentSystemEvent.action == "BASELINE_PUBLISH",
                )
            )
            event = events.scalars().first()
            assert event is not None, "普通智能体的版本发布也必须进发布历史"
            assert event.version_number == 1
            assert event.actor_name == owner.user_name
        finally:
            await _cleanup(agent_id, user_ids=(owner.id,))


async def test_list_agents_marks_orphaned_owner(db_session):
    """权属人停用后，列表要标出 ``owner_active=False``，管理员才能一次筛出来。"""
    from app.models.user import User as _User

    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    agent = await _make_agent(db_session, owner=owner.user_name)
    agent_id = agent.id
    alive_agent = await _make_agent(db_session, owner=other.user_name)
    alive_id = alive_agent.id

    owner.status = 0
    await db_session.commit()

    admin = await _make_user(db_session, role="admin")
    admin_ctx = {"role": "admin", "user_name": admin.user_name, "user_id": admin.id, "id": admin.id}
    # rollback 会让 ORM 实例过期，之后读属性会触发隐含 IO；先把要用的标量取出来
    user_ids = (owner.id, other.id, admin.id)

    try:
        rows = await AgentManagerService.list_agents(db_session, user=admin_ctx)
        by_id = {item.id: item for item in rows}
        assert by_id[agent_id].owner_active is False, "权属人停用的智能体必须被标出来"
        assert by_id[alive_id].owner_active is True
    finally:
        # 先结束本 session 的事务：select 之后仍持有共享锁，
        # 另开 session 删除会撞上 Lock wait timeout
        await db_session.rollback()
        await _cleanup(agent_id, alive_id, user_ids=user_ids)

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
    read_system_status,
    reset_probe_state_for_tests,
    supports_system_status,
)
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
async def test_system_agent_content_versions_are_read_only_for_owner(db_session):
    """上架为系统级后，所有者连**版本**都不能动。

    版本才是内容的主要载体（提示词、工具、模型）。只拦元数据等于没拦——
    所有者改个草稿再等管理员发布，线上基线就被换掉了。
    """
    owner = await _make_user(db_session, role="user")
    admin = await _make_user(db_session, role="admin")
    agent = await _make_agent(db_session, owner=owner.user_name, published=True)

    # 额外造一个草稿版本，用来验证「改 / 发布 / 删」三条路径
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

        payload = AIAgentVersionBase(system_prompt="偷偷改内容", model_name="deepseek-chat")
        actor = {"role": "user", "user_name": owner.user_name}

        assert (
            await AgentManagerService.create_agent_version(
                db_session, agent.id, payload, user=actor
            )
            is None
        ), "系统级之后所有者不能新建版本"
        assert (
            await AgentManagerService.update_agent_version(
                db_session, agent.id, draft.id, payload, user=actor
            )
            is None
        ), "系统级之后所有者不能改草稿"
        assert (
            await AgentManagerService.publish_version(
                db_session, agent.id, draft.id, user=actor
            )
            is False
        ), "系统级之后所有者不能发布版本"
        assert (
            await AgentManagerService.delete_version(
                db_session, agent.id, draft.id, user=actor
            )
            is False
        ), "系统级之后所有者不能删版本"

        # 管理员不受影响：这条线对他来说本来就只是「改个状态」
        created = await AgentManagerService.create_agent_version(
            db_session, agent.id, payload, user={"role": "admin", "user_name": admin.user_name}
        )
        assert created is not None, "管理员仍然可以给系统级智能体建版本"
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

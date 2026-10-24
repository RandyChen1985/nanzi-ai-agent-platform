"""智能体系统级发布：申请、审核、发布、下架与留痕。

设计要点（与 ``docs/superpowers/specs/2026-10-23-agent-system-publication-design.md`` 对齐）：

* ``ai_agents.is_system`` 仍是运行时的**唯一事实源**，发布/下架只改它，不动任何版本状态，
  也不跑 readiness 校验——版本是否可用由既有的「发布版本」流程负责。
* 「发布为系统级」必须有至少一个 ``PUBLISHED`` 版本：运行时按 ``status='PUBLISHED'`` 取配置
  （``_latest_published_versions_by_agent``），没有已发布版本的智能体被设为系统级后直接不可用。
  非 ``LOCAL`` 引擎（RAGFLOW / OPENCLAW）根本没有版本概念，一并拦掉。
* 申请待审期间只冻结**所有者**（管理员可以照常改），避免申请者一边审一边改导致审核内容与
  上架内容不一致。
* 下架保留授权记录（重新上架自动恢复），并通知「用户直配 ∪ 角色成员」；管理员可以选择不通知，
  此时只在事件里记人数与 ``notified=False``。
* 所有写操作都在本层 ``commit``，与 ``AgentManagerService`` 的既有风格一致。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_system import AgentSystemEvent, AgentSystemRequest
from app.models.permission import ResourcePermission
from app.models.user import User
from app.services.ai.agent_system_columns import (
    SYSTEM_STATUS_NONE,
    SYSTEM_STATUS_PENDING,
    assert_migrated,
    read_system_status,
    write_system_status,
)
from app.services.ai.agent_system_notifier import AgentSystemNotifier

# ---- 申请单状态 ----
STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

# ---- 事件动作 ----
ACTION_PUBLISH = "PUBLISH"
ACTION_UNPUBLISH = "UNPUBLISH"
ACTION_META_UPDATE = "META_UPDATE"
ACTION_TOGGLE_ENABLED = "TOGGLE_ENABLED"
# 管理员给系统级智能体发布新版本 = 换掉线上基线，属于「发布记录」的一部分
ACTION_BASELINE_PUBLISH = "BASELINE_PUBLISH"

# 只有本地引擎有版本概念；另外两种引擎的配置在远端，拿不到也不该参与委派
PUBLISHABLE_ENGINE_TYPES = frozenset({"LOCAL"})

REQUEST_COMMENT_MAX = 500
UNPUBLISH_REASON_MAX = 500
PUBLISH_REASON_MAX = 500


def _is_main_agent(agent: AIAgent) -> bool:
    """主助手是固定入口，不能下架成普通智能体。"""
    from app.services.ai.agent_manager import _is_main_general_agent_record

    return _is_main_general_agent_record(agent)


class AgentSystemPublicationService:
    """系统级智能体生命周期的全部状态迁移。"""

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_actor(user: Any) -> Tuple[bool, str, Optional[int]]:
        """统一解析调用者：返回 ``(是否管理员, 用户名, 用户 ID)``。

        接口层传进来的是 ``require_api_key`` 的 dict；内部调用可能传 ORM 对象。
        """
        if isinstance(user, dict):
            is_admin = str(user.get("role") or "") == "admin"
            username = str(user.get("user_name") or "")
            raw_id = user.get("user_id")
        else:
            is_admin = str(getattr(user, "role", "") or "") == "admin"
            username = str(getattr(user, "user_name", "") or "")
            raw_id = getattr(user, "id", None)

        try:
            user_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            user_id = None
        return is_admin, username, user_id

    @staticmethod
    async def _get_agent(session: AsyncSession, agent_id: str) -> AIAgent:
        agent = await session.get(AIAgent, str(agent_id))
        if not agent:
            raise HTTPException(status_code=404, detail="智能体不存在")
        return agent

    @staticmethod
    async def _published_version(
        session: AsyncSession, agent_id: str
    ) -> Optional[AIAgentVersion]:
        """该智能体最新的一条 PUBLISHED 版本（运行时用的就是它）。"""
        row = await session.execute(
            select(AIAgentVersion)
            .where(
                AIAgentVersion.agent_id == str(agent_id),
                AIAgentVersion.status == "PUBLISHED",
            )
            .order_by(desc(AIAgentVersion.version_number))
            .limit(1)
        )
        return row.scalars().first()

    @staticmethod
    async def assert_publishable(
        session: AsyncSession, agent: AIAgent
    ) -> AIAgentVersion:
        """设为系统级的前置条件：LOCAL 引擎 + 至少一个 PUBLISHED 版本。"""
        engine = str(agent.engine_type or "LOCAL").upper()
        if engine not in PUBLISHABLE_ENGINE_TYPES:
            raise HTTPException(
                status_code=400,
                detail="该引擎类型不支持发布为系统级（只有本地引擎的智能体有版本可发布）",
            )

        version = await AgentSystemPublicationService._published_version(session, agent.id)
        if version is None:
            raise HTTPException(
                status_code=400,
                detail="请先发布至少一个版本，再设为系统级",
            )
        return version

    @staticmethod
    def build_snapshot(agent: AIAgent, version: Optional[AIAgentVersion]) -> Dict[str, Any]:
        """发布/申请时的内容快照。

        **刻意不存 ``engine_config``**：那是引擎侧的连接配置，可能含凭证，而能被发布为
        系统级的只有 LOCAL 引擎（配置为空），存它没有收益只有风险。
        """
        snapshot: Dict[str, Any] = {
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "display_name": agent.display_name,
                "description": agent.description,
                "avatar_url": agent.avatar_url,
                "capabilities": agent.capabilities,
                "agent_type": agent.agent_type,
                "engine_type": agent.engine_type,
                "created_by": agent.created_by,
                "owner_group": agent.owner_group,
            },
            "version": None,
        }
        if version is not None:
            snapshot["version"] = {
                "id": version.id,
                "version_number": version.version_number,
                "model_name": version.model_name,
                "temperature": version.temperature,
                "synthesis_model_name": version.synthesis_model_name,
                "synthesis_temperature": version.synthesis_temperature,
                "allow_model_switch": version.allow_model_switch,
                "toolcall_timeout_seconds": version.toolcall_timeout_seconds,
                "system_prompt": version.system_prompt,
                "tools": version.tools,
                "skills": version.skills,
                "skills_custom": version.skills_custom,
                "welcome_config": version.welcome_config,
                "comment": version.comment,
            }
        return snapshot

    @staticmethod
    async def record_event(
        session: AsyncSession,
        *,
        agent: AIAgent,
        action: str,
        actor_name: str,
        actor_id: Optional[int],
        reason: Optional[str] = None,
        version: Optional[AIAgentVersion] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        affected_user_count: Optional[int] = None,
        notified: Optional[bool] = None,
    ) -> AgentSystemEvent:
        event = AgentSystemEvent(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            action=action,
            version_id=version.id if version is not None else None,
            version_number=version.version_number if version is not None else None,
            snapshot=json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
            reason=reason,
            affected_user_count=affected_user_count,
            notified=notified,
            actor_id=actor_id,
            actor_name=actor_name or "system",
            created_at=datetime.now(),
        )
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    def is_frozen_status(status: Optional[str]) -> bool:
        """流程状态是否处于「待审冻结」。

        待审时 ``is_system`` 必然是 False（提交申请的前提就是非系统级），
        所以只判状态即可，不用再读布尔列。
        """
        return str(status or SYSTEM_STATUS_NONE) == SYSTEM_STATUS_PENDING

    @staticmethod
    async def is_frozen(session: AsyncSession, agent: AIAgent) -> bool:
        status, _ = await read_system_status(session, agent.id)
        return AgentSystemPublicationService.is_frozen_status(status)

    @staticmethod
    async def assert_not_frozen(session: AsyncSession, agent: AIAgent, user: Any) -> None:
        """写操作前的冻结校验。

        只拦所有者，管理员豁免——管理员是审核方，待审期间仍要能改（比如帮申请人修配置）。
        """
        is_admin, username, _ = AgentSystemPublicationService.resolve_actor(user)
        if is_admin:
            return
        status, _ = await read_system_status(session, agent.id)
        if not AgentSystemPublicationService.is_frozen_status(status):
            return
        if agent.created_by and agent.created_by != username:
            # 本来就无权编辑，交给各自的权限判定去回 403，这里不抢答
            return
        raise HTTPException(
            status_code=409,
            detail="该智能体正在审核中，内容已冻结；请先撤回申请，或复制一份继续开发",
        )

    @staticmethod
    async def _pending_request(
        session: AsyncSession, agent_id: str
    ) -> Optional[AgentSystemRequest]:
        row = await session.execute(
            select(AgentSystemRequest)
            .where(
                AgentSystemRequest.agent_id == str(agent_id),
                AgentSystemRequest.status == STATUS_PENDING,
            )
            .order_by(desc(AgentSystemRequest.requested_at))
            .limit(1)
        )
        return row.scalars().first()

    @staticmethod
    async def _get_request(
        session: AsyncSession, request_id: str, *, expect_status: Optional[str] = None
    ) -> AgentSystemRequest:
        request = await session.get(AgentSystemRequest, str(request_id))
        if not request:
            raise HTTPException(status_code=404, detail="申请单不存在")
        if expect_status and request.status != expect_status:
            raise HTTPException(
                status_code=409,
                detail=f"申请单当前状态为 {request.status}，不能执行该操作",
            )
        return request

    @staticmethod
    async def _owner_user_id(
        session: AsyncSession, created_by: Optional[str]
    ) -> Optional[int]:
        login = str(created_by or "").strip()
        if not login:
            return None
        row = await session.execute(
            select(User.id).where(User.user_name == login).limit(1)
        )
        value = row.scalars().first()
        return int(value) if value is not None else None

    @staticmethod
    async def _affected_user_count(session: AsyncSession, agent_id: str) -> int:
        return len(await AgentSystemNotifier.recipient_user_ids(session, agent_id))

    # ------------------------------------------------------------------
    # 申请
    # ------------------------------------------------------------------

    @staticmethod
    async def submit_request(
        session: AsyncSession,
        agent_id: str,
        user: Any,
        comment: Optional[str] = None,
    ) -> AgentSystemRequest:
        """所有者提交「发布为系统级」申请，提交即快照并冻结内容。"""
        await assert_migrated(session)
        is_admin, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        if bool(agent.is_system):
            raise HTTPException(status_code=409, detail="该智能体已经是系统级")
        if not is_admin and agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有创建者可以申请发布")

        current_status, pending_request_id = await read_system_status(session, agent.id)
        if current_status == SYSTEM_STATUS_PENDING or pending_request_id:
            raise HTTPException(status_code=409, detail="该智能体已有待审的发布申请")

        version = await AgentSystemPublicationService.assert_publishable(session, agent)
        snapshot = AgentSystemPublicationService.build_snapshot(agent, version)

        text = (comment or "").strip()[:REQUEST_COMMENT_MAX] or None
        request = AgentSystemRequest(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            snapshot_version_id=version.id,
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            status=STATUS_PENDING,
            request_comment=text,
            requested_by=user_id,
            requested_at=datetime.now(),
        )
        session.add(request)
        await session.flush()
        await write_system_status(
            session,
            agent.id,
            status=SYSTEM_STATUS_PENDING,
            pending_request_id=request.id,
        )

        await AgentSystemNotifier.notify_submitted(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_name=username or "某位同事",
            comment=text,
        )
        await session.commit()
        return request

    @staticmethod
    async def cancel_request(
        session: AsyncSession, agent_id: str, user: Any
    ) -> AgentSystemRequest:
        """申请人撤回待审申请，立即解冻。"""
        await assert_migrated(session)
        is_admin, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        request = await AgentSystemPublicationService._pending_request(session, agent.id)
        if not request:
            raise HTTPException(status_code=409, detail="该智能体没有待审的发布申请")
        if not is_admin and request.requested_by != user_id and agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有申请人或创建者可以撤回申请")

        request.status = STATUS_CANCELLED
        request.reviewed_at = datetime.now()
        request.reviewed_by = user_id
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )

        await AgentSystemNotifier.notify_cancelled(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_name=username or "申请人",
        )
        await session.commit()
        return request

    @staticmethod
    async def reject_request(
        session: AsyncSession, request_id: str, user: Any, comment: str
    ) -> AgentSystemRequest:
        """管理员驳回申请：必须写理由，申请单关闭并解冻。"""
        await assert_migrated(session)
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        reason = (comment or "").strip()
        if not reason:
            raise HTTPException(status_code=400, detail="驳回时必须填写理由")

        request = await AgentSystemPublicationService._get_request(
            session, request_id, expect_status=STATUS_PENDING
        )
        agent = await AgentSystemPublicationService._get_agent(session, request.agent_id)

        request.status = STATUS_REJECTED
        request.reviewed_by = user_id
        request.reviewed_at = datetime.now()
        request.review_comment = reason
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )

        await AgentSystemNotifier.notify_rejected(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_user_id=request.requested_by,
            comment=reason,
        )
        await session.commit()
        return request

    @staticmethod
    async def approve_request(
        session: AsyncSession, request_id: str, user: Any, comment: Optional[str] = None
    ) -> AIAgent:
        """管理员通过申请：只把智能体置为系统级并记事件，不改任何版本状态。"""
        await assert_migrated(session)
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        request = await AgentSystemPublicationService._get_request(
            session, request_id, expect_status=STATUS_PENDING
        )
        agent = await AgentSystemPublicationService._get_agent(session, request.agent_id)

        # 兜底：申请到审核之间版本可能被删/被归档，不能放出去一个取不到配置的系统智能体
        version = await AgentSystemPublicationService.assert_publishable(session, agent)
        snapshot = AgentSystemPublicationService.build_snapshot(agent, version)

        request.status = STATUS_APPROVED
        request.reviewed_by = user_id
        request.reviewed_at = datetime.now()
        request.review_comment = (comment or "").strip() or None

        agent.is_system = True
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )

        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_PUBLISH,
            actor_name=username,
            actor_id=user_id,
            reason="审核通过发布申请" + (f"：{request.request_comment}" if request.request_comment else ""),
            version=version,
            snapshot=snapshot,
        )
        await AgentSystemPublicationService.grant_agent_to_user(
            session, agent.id, request.requested_by
        )
        await session.flush()

        await AgentSystemNotifier.notify_approved(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_user_id=request.requested_by,
            review_comment=request.review_comment,
        )
        await session.commit()
        return agent

    # ------------------------------------------------------------------
    # 发布 / 下架
    # ------------------------------------------------------------------

    @staticmethod
    async def grant_agent_to_user(
        session: AsyncSession, agent_id: str, user_id: Optional[int]
    ) -> None:
        """把智能体授权给某个用户（已有授权则跳过），用于发布后自动授权所有者。"""
        if user_id is None:
            return
        exists = await session.execute(
            select(ResourcePermission.id)
            .where(
                ResourcePermission.user_id == int(user_id),
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id == str(agent_id),
            )
            .limit(1)
        )
        if exists.scalars().first() is not None:
            return
        session.add(
            ResourcePermission(
                user_id=int(user_id),
                resource_type="agent",
                resource_id=str(agent_id),
                enabled=True,
            )
        )
        await session.flush()

    @staticmethod
    async def publish_system(
        session: AsyncSession,
        agent_id: str,
        user: Any,
        reason: Optional[str] = None,
        notify_restore: bool = True,
    ) -> AIAgent:
        """直接把智能体设为系统级（管理员/持权限者），可选通知所有者与恢复用户。"""
        await assert_migrated(session)
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        if bool(agent.is_system):
            raise HTTPException(status_code=409, detail="该智能体已经是系统级")

        version = await AgentSystemPublicationService.assert_publishable(session, agent)
        snapshot = AgentSystemPublicationService.build_snapshot(agent, version)
        note = (reason or "").strip()[:PUBLISH_REASON_MAX] or None

        agent.is_system = True
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )

        # 直接发布时顺手关掉待审申请，避免留下一条永远审不掉的单子
        pending = await AgentSystemPublicationService._pending_request(session, agent.id)
        if pending is not None:
            pending.status = STATUS_APPROVED
            pending.reviewed_by = user_id
            pending.reviewed_at = datetime.now()
            pending.review_comment = note or "管理员直接发布，申请自动通过"

        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_PUBLISH,
            actor_name=username,
            actor_id=user_id,
            reason=note,
            version=version,
            snapshot=snapshot,
        )

        owner_uid = await AgentSystemPublicationService._owner_user_id(session, agent.created_by)
        await AgentSystemPublicationService.grant_agent_to_user(session, agent.id, owner_uid)
        await session.flush()

        if owner_uid is not None and owner_uid != user_id:
            await AgentSystemNotifier.notify_published_to_owner(
                session,
                agent_id=agent.id,
                display_name=agent.display_name,
                owner_user_id=owner_uid,
                direct=True,
            )

        # 重新上架：只有上次下架确实通知过用户，这次才补发「已恢复」，否则用户会莫名其妙
        if notify_restore:
            last_unpublish = await AgentSystemPublicationService._last_unpublish_event(
                session, agent.id
            )
            if last_unpublish is not None and last_unpublish.notified:
                await AgentSystemNotifier.notify_restored(
                    session, agent_id=agent.id, display_name=agent.display_name
                )

        await session.commit()
        return agent

    @staticmethod
    async def _last_unpublish_event(
        session: AsyncSession, agent_id: str
    ) -> Optional[AgentSystemEvent]:
        row = await session.execute(
            select(AgentSystemEvent)
            .where(
                AgentSystemEvent.agent_id == str(agent_id),
                AgentSystemEvent.action == ACTION_UNPUBLISH,
            )
            .order_by(desc(AgentSystemEvent.created_at))
            .limit(1)
        )
        return row.scalars().first()

    @staticmethod
    async def unpublish_system(
        session: AsyncSession,
        agent_id: str,
        user: Any,
        reason: str,
        notify: bool = True,
    ) -> Dict[str, Any]:
        """下架：取消系统级、物归原主、按需通知已分配用户；授权记录保留以便恢复。"""
        await assert_migrated(session)
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        if not bool(agent.is_system):
            raise HTTPException(status_code=409, detail="该智能体不是系统级，无需下架")
        if _is_main_agent(agent):
            raise HTTPException(status_code=400, detail="主助手不能下架")

        note = (reason or "").strip()[:UNPUBLISH_REASON_MAX]
        if not note:
            raise HTTPException(status_code=400, detail="下架时必须填写原因")

        agent.is_system = False
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )

        # 所有者的账号可能已经不存在，这时把归属转给本次操作的管理员，
        # 否则下架后的智能体没有任何人能在界面上编辑。
        if await AgentSystemPublicationService._owner_user_id(session, agent.created_by) is None:
            if username:
                agent.created_by = username

        # 归属转交之后再算影响范围。所有者不算「被分配的人」：他本来就能用，
        # 把他算进「已分配给 N 人」会让人误以为还有别人在用；而给他发
        # 「你暂时无法使用」也是错的信息。所以这里先剔除，再单独通知他。
        owner_uid = await AgentSystemPublicationService._owner_user_id(
            session, agent.created_by
        )
        recipients = await AgentSystemNotifier.recipient_user_ids(session, agent.id)
        if owner_uid is not None:
            recipients.discard(owner_uid)
        affected = len(recipients)

        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_UNPUBLISH,
            actor_name=username,
            actor_id=user_id,
            reason=note,
            affected_user_count=affected,
            notified=bool(notify),
        )
        await session.flush()

        notified_count = 0
        if notify and affected:
            notified_count = await AgentSystemNotifier.notify_unpublished(
                session,
                agent_id=agent.id,
                display_name=agent.display_name,
                reason=note,
                recipients=recipients,
            )

        # 所有者的通知文案不同：他不是「用不了」，而是「解冻了、可以继续改」
        if notify and owner_uid is not None:
            sent = await AgentSystemNotifier.send(
                session,
                {owner_uid},
                agent_id=agent.id,
                title=f"智能体「{agent.display_name}」已下架为普通智能体",
                content=(
                    f"管理员已将智能体「{agent.display_name}」下架（取消系统级）。\n\n"
                    f"下架原因：{note}\n\n"
                    "内容已解冻，你可以继续编辑；如需再次发布为系统级，请重新提交发布申请。"
                ),
                level="info",
            )
            notified_count += sent

        await session.commit()
        return {
            "agent_id": agent.id,
            "affected_user_count": affected,
            "notified_user_count": notified_count,
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    async def list_requests(
        session: AsyncSession,
        status: str = STATUS_PENDING,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """待审列表（按提交时间升序：等得最久的排最前）。"""
        await assert_migrated(session)
        rows = await session.execute(
            select(AgentSystemRequest, AIAgent)
            .join(AIAgent, AIAgent.id == AgentSystemRequest.agent_id)
            .where(AgentSystemRequest.status == status)
            .order_by(AgentSystemRequest.requested_at.asc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
        )
        now = datetime.now()
        pairs = rows.all()

        # 审核人要知道「谁提交的」，一次批量反查用户名（不逐条查）
        applicant_ids = {r.requested_by for r, _ in pairs if r.requested_by is not None}
        applicant_names: Dict[int, str] = {}
        if applicant_ids:
            name_rows = await session.execute(
                select(User.id, User.user_name).where(User.id.in_(list(applicant_ids)))
            )
            applicant_names = {int(uid): str(name) for uid, name in name_rows if uid is not None}

        items: List[Dict[str, Any]] = []
        for request, agent in pairs:
            items.append(
                {
                    "id": request.id,
                    "agent_id": request.agent_id,
                    "agent_name": agent.display_name,
                    "agent_identifier": agent.name,
                    "engine_type": agent.engine_type,
                    "status": request.status,
                    "request_comment": request.request_comment,
                    "requested_by": request.requested_by,
                    "requested_by_name": applicant_names.get(int(request.requested_by))
                    if request.requested_by is not None
                    else None,
                    "requested_at": request.requested_at,
                    "waiting_hours": round(
                        (now - request.requested_at).total_seconds() / 3600, 1
                    )
                    if request.requested_at
                    else 0,
                }
            )
        return items

    @staticmethod
    async def pending_count(session: AsyncSession) -> int:
        await assert_migrated(session)
        rows = await session.execute(
            select(AgentSystemRequest.id).where(
                AgentSystemRequest.status == STATUS_PENDING
            )
        )
        return len(rows.scalars().all())

    @staticmethod
    async def request_detail(
        session: AsyncSession, request_id: str
    ) -> Dict[str, Any]:
        await assert_migrated(session)
        request = await AgentSystemPublicationService._get_request(session, request_id)
        agent = await AgentSystemPublicationService._get_agent(session, request.agent_id)
        snapshot: Optional[Dict[str, Any]] = None
        if request.snapshot:
            try:
                snapshot = json.loads(request.snapshot)
            except (TypeError, ValueError):
                snapshot = None

        applicant = None
        if request.requested_by is not None:
            applicant = await session.get(User, int(request.requested_by))

        reviewer = None
        if request.reviewed_by is not None:
            reviewer = await session.get(User, int(request.reviewed_by))

        return {
            "id": request.id,
            "agent_id": request.agent_id,
            "agent_name": agent.display_name,
            "agent_identifier": agent.name,
            "engine_type": agent.engine_type,
            "status": request.status,
            "snapshot": snapshot,
            "request_comment": request.request_comment,
            "requested_by": request.requested_by,
            "requested_by_name": getattr(applicant, "user_name", None),
            "requested_at": request.requested_at,
            "reviewed_by": request.reviewed_by,
            "reviewed_by_name": getattr(reviewer, "user_name", None),
            "reviewed_at": request.reviewed_at,
            "review_comment": request.review_comment,
        }

    @staticmethod
    async def history(
        session: AsyncSession,
        agent_id: str,
        user: Any,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """发布历史：申请单与操作事件合并成一条按时间倒序的时间线。

        只有管理员与该智能体的所有者能看——普通用户没有理由看到别人的审批往来。
        """
        await assert_migrated(session)
        is_admin, username, _ = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)
        if not is_admin and agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有管理员或创建者可以查看发布历史")

        requests = (
            await session.execute(
                select(AgentSystemRequest)
                .where(AgentSystemRequest.agent_id == agent.id)
                .order_by(desc(AgentSystemRequest.requested_at))
            )
        ).scalars().all()
        events = (
            await session.execute(
                select(AgentSystemEvent)
                .where(AgentSystemEvent.agent_id == agent.id)
                .order_by(desc(AgentSystemEvent.created_at))
            )
        ).scalars().all()

        # 申请单只存了用户 ID，时间线要显示「谁提交的」，一次批量反查用户名
        applicant_ids = {r.requested_by for r in requests if r.requested_by is not None}
        applicant_names: Dict[int, str] = {}
        if applicant_ids:
            rows = await session.execute(
                select(User.id, User.user_name).where(User.id.in_(list(applicant_ids)))
            )
            applicant_names = {int(uid): str(name) for uid, name in rows if uid is not None}

        entries: List[Dict[str, Any]] = []
        for request in requests:
            snapshot = None
            if request.snapshot:
                try:
                    snapshot = json.loads(request.snapshot)
                except (TypeError, ValueError):
                    snapshot = None
            entries.append(
                {
                    "kind": "request",
                    "id": request.id,
                    "status": request.status,
                    # 已审完的单子要显示「什么时候审的」，否则时间线会把「通过」
                    # 摆在提交时刻，看不出审批耗时
                    "at": request.reviewed_at or request.requested_at,
                    "requested_at": request.requested_at,
                    "actor_id": request.requested_by,
                    "actor_name": applicant_names.get(int(request.requested_by))
                    if request.requested_by is not None
                    else None,
                    "comment": request.request_comment,
                    "review_comment": request.review_comment,
                    "reviewed_at": request.reviewed_at,
                    "snapshot": snapshot,
                }
            )

        for event in events:
            snapshot = None
            if event.snapshot:
                try:
                    snapshot = json.loads(event.snapshot)
                except (TypeError, ValueError):
                    snapshot = None
            entries.append(
                {
                    "kind": "event",
                    "id": event.id,
                    "action": event.action,
                    "at": event.created_at,
                    "actor_name": event.actor_name,
                    "version_number": event.version_number,
                    "reason": event.reason,
                    "affected_user_count": event.affected_user_count,
                    "notified": event.notified,
                    "snapshot": snapshot,
                }
            )

        entries.sort(key=lambda item: item["at"] or datetime.min, reverse=True)
        total = len(entries)
        window = entries[max(0, offset) : max(0, offset) + max(1, min(limit, 100))]
        return {"total": total, "items": window}

    @staticmethod
    async def unpublish_preview(session: AsyncSession, agent_id: str) -> Dict[str, Any]:
        """下架前的影响范围：影响多少「实际能用」的人 + 前 20 个用户名。"""
        await assert_migrated(session)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)
        if not bool(agent.is_system):
            raise HTTPException(status_code=409, detail="该智能体不是系统级")

        recipients = await AgentSystemNotifier.recipient_user_ids(session, agent.id)
        # 与下架时的口径保持一致：所有者不计入「已分配」
        preview_owner_uid = await AgentSystemPublicationService._owner_user_id(
            session, agent.created_by
        )
        if preview_owner_uid is not None:
            recipients.discard(preview_owner_uid)
        names: List[str] = []
        if recipients:
            rows = await session.execute(
                select(User.user_name)
                .where(User.id.in_(list(recipients)))
                .order_by(User.id)
                .limit(20)
            )
            names = [str(value) for (value,) in rows if value]

        return {
            "agent_id": agent.id,
            "affected_user_count": len(recipients),
            "sample_user_names": names,
        }

    # ------------------------------------------------------------------
    # 与既有写操作的挂接
    # ------------------------------------------------------------------

    @staticmethod
    async def record_enabled_change(
        session: AsyncSession, agent: AIAgent, enabled: bool, user: Any
    ) -> None:
        """系统级智能体被启用/禁用时记一条事件。

        非系统级不记：这类操作每天都在发生，记录只会淹没真正重要的变更。
        静默、不发通知——用户已经习惯「禁用就是暂时用不了」。
        """
        if not bool(agent.is_system):
            return
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_TOGGLE_ENABLED,
            actor_name=username,
            actor_id=user_id,
            reason="启用" if enabled else "停用",
        )

    @staticmethod
    async def record_meta_update(
        session: AsyncSession, agent: AIAgent, user: Any, changed_fields: List[str]
    ) -> None:
        """管理员改了系统级智能体的元数据时记一条事件（不含版本内容）。"""
        if not bool(agent.is_system) or not changed_fields:
            return
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_META_UPDATE,
            actor_name=username,
            actor_id=user_id,
            reason="修改字段：" + "、".join(sorted(changed_fields)),
        )

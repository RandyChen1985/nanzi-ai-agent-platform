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
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import case, desc, func, select
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
    supports_system_status,
    write_system_status,
)
from app.services.ai.agent_system_notifier import AgentSystemNotifier

logger = logging.getLogger(__name__)

# ---- 申请单状态 ----
STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

# ---- 事件动作 ----
# 申请类型：系统级化 vs 把某个草稿版本发布上线（系统级内容只能管理员发布）
REQUEST_TYPE_SYSTEM = "SYSTEM_PUBLISH"
REQUEST_TYPE_RELEASE = "VERSION_RELEASE"

ACTION_PUBLISH = "PUBLISH"
ACTION_UNPUBLISH = "UNPUBLISH"
ACTION_META_UPDATE = "META_UPDATE"
ACTION_TOGGLE_ENABLED = "TOGGLE_ENABLED"
# 管理员给系统级智能体发布新版本 = 换掉线上基线，属于「发布记录」的一部分
ACTION_BASELINE_PUBLISH = "BASELINE_PUBLISH"
# 权属人变更：原所有者离职/转岗后智能体不能变成没人能编辑的孤儿
ACTION_TRANSFER_OWNER = "TRANSFER_OWNER"
# 分享：把配置副本发给某个同事。发出去、被接受、被拒绝、被作废都要留痕，
# 否则「谁在什么时候把这个智能体给了谁」事后查不到。
ACTION_SHARE_SEND = "SHARE_SEND"
ACTION_SHARE_ACCEPT = "SHARE_ACCEPT"
ACTION_SHARE_REJECT = "SHARE_REJECT"
ACTION_SHARE_REVOKE = "SHARE_REVOKE"

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
    async def _close_pending_requests(
        session: AsyncSession,
        agent_id: str,
        reason: str,
        actor_name: str = "system",
        actor_id: Optional[int] = None,
    ) -> List[str]:
        """把某个智能体名下所有待审申请单关掉，返回被关掉的申请单 ID。"""
        rows = await session.execute(
            select(AgentSystemRequest).where(
                AgentSystemRequest.agent_id == str(agent_id),
                AgentSystemRequest.status == STATUS_PENDING,
            )
        )
        pending = list(rows.scalars().all())
        closed: List[str] = []
        for request in pending:
            request.status = STATUS_REJECTED
            request.reviewed_at = datetime.now()
            request.review_comment = reason
            # reviewed_by 是用户数字 ID（Integer），系统动作留空
            if actor_id is not None:
                request.reviewed_by = actor_id
            closed.append(str(request.id))
        if closed:
            await session.flush()
        return closed

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
    ) -> Optional[AgentSystemEvent]:
        # 事件表与 system_status 列来自同一个迁移。未迁移的环境里表根本不存在，
        # 直接写会抛错——而发布、改权属人这些动作不该因为「审计表没建」就整条失败。
        # 探测有缓存，热路径上不额外查库。
        if not await supports_system_status(session):
            return None

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
        # 放进 SAVEPOINT：审计写失败时只回滚这一条，调用方的业务事务保持可用。
        # 裸写 + 外层 try/except 是行不通的——flush 抛错后 session 已被标记需 rollback，
        # 异常即便被吞掉，后面的 commit() 也会抛 PendingRollbackError，
        # 结果整个发布/分享请求 500，业务数据一起丢。
        try:
            async with session.begin_nested():
                session.add(event)
                await session.flush()
        except Exception as exc:  # pragma: no cover - 审计失败不阻断主流程
            logger.warning(f"写入发布事件失败（{action}）：{exc}")
            return None
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
        if agent.created_by != username:
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
    async def _revoke_pending_shares(session: AsyncSession, agent_id: str) -> None:
        """系统级升级时作废待接受的分享。

        分享表与系统级事件表来自不同迁移，缺表时直接跳过——
        升级为系统级这件事不能被「分享表还没建」挡住。
        """
        from app.services.ai.agent_share_service import AgentShareService

        # 分享表还没建（未执行 V186/V86）时跳过：升级为系统级不能被它挡住。
        if not await AgentShareService.supports_share(session):
            return

        # 表在就必须作废成功。这里刻意不吞异常：作废失败若被静默忽略，
        # 智能体已经是系统级、待接受的分享却还能生成副本，等于绕开授权治理。
        # 失败就让整个发布事务回滚，宁可让管理员重试一次。
        await AgentShareService.revoke_pending_for_agent(
            session, str(agent_id), "该智能体已发布为系统级，请向管理员申请使用权限"
        )

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
    async def submit_release_request(
        session: AsyncSession,
        agent_id: str,
        version_id: str,
        user: Any,
        comment: Optional[str] = None,
    ) -> AgentSystemRequest:
        """所有者申请把某个草稿版本发布上线。

        系统级智能体的线上基线只能由管理员变更，所有者改完草稿后需要这条通道
        把版本推上去；否则他能改、能调试，却永远没法让它生效。
        待审期间同样冻结所有者，避免「申请的是 A 版本，审核时变成了 B」。
        """
        await assert_migrated(session)
        is_admin, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        if not bool(agent.is_system):
            raise HTTPException(
                status_code=409,
                detail="只有系统级智能体需要提交上线申请；普通智能体可以直接发布版本",
            )
        if is_admin:
            raise HTTPException(
                status_code=400, detail="管理员可以直接发布版本，无需提交上线申请"
            )
        if agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有创建者可以申请发布版本")

        current_status, pending_request_id = await read_system_status(session, agent.id)
        if current_status == SYSTEM_STATUS_PENDING or pending_request_id:
            raise HTTPException(status_code=409, detail="该智能体已有待审的申请")

        from app.models.agent import AIAgentVersion

        version = await session.get(AIAgentVersion, version_id)
        if not version or str(version.agent_id) != str(agent.id):
            raise HTTPException(status_code=404, detail="版本不存在")
        if str(version.status) != "DRAFT":
            raise HTTPException(status_code=409, detail="只有草稿版本可以申请上线")

        snapshot = AgentSystemPublicationService.build_snapshot(agent, version)
        text = (comment or "").strip()[:REQUEST_COMMENT_MAX] or None
        request = AgentSystemRequest(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            request_type=REQUEST_TYPE_RELEASE,
            snapshot_version_id=version.id,
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            status=STATUS_PENDING,
            request_comment=text,
            requested_by=user_id,
            requested_at=datetime.now(),
        )
        session.add(request)
        await write_system_status(
            session,
            agent.id,
            status=SYSTEM_STATUS_PENDING,
            pending_request_id=request.id,
        )
        await session.flush()

        await AgentSystemNotifier.notify_submitted(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_name=username or "某位同事",
            comment=text,
            version_number=version.version_number,
        )
        await session.commit()
        return request

    @staticmethod
    async def _approve_release_request(
        session: AsyncSession,
        request: AgentSystemRequest,
        agent: AIAgent,
        actor_name: str,
        actor_id: Optional[int],
        comment: Optional[str],
    ) -> AIAgent:
        """通过「发布版本上线」申请：把申请里那个草稿版本真正发布出去。

        发布复用 ``AgentManagerService.publish_version``，它自己会 commit 并清运行时缓存，
        所以必须先把冻结状态清掉——否则会被 ``assert_not_frozen`` 拦住。
        """
        from app.models.agent import AIAgentVersion
        from app.services.ai.agent_manager import AgentManagerService, AgentNotReadyError

        version = await session.get(AIAgentVersion, request.snapshot_version_id)
        if not version or str(version.agent_id) != str(agent.id):
            raise HTTPException(status_code=409, detail="申请的版本已不存在，请让申请人重新提交")
        if str(version.status) != "DRAFT":
            raise HTTPException(
                status_code=409, detail="申请的版本已不是草稿（可能已被发布或删除），请让申请人重新提交"
            )

        request.status = STATUS_APPROVED
        request.reviewed_by = actor_id
        request.reviewed_at = datetime.now()
        request.review_comment = (comment or "").strip() or None

        # 先解冻，publish_version 内部会校验冻结状态
        await write_system_status(
            session, agent.id, status=SYSTEM_STATUS_NONE, pending_request_id=None
        )
        await session.flush()

        try:
            # 这里刻意合成 admin：publish_version 内部要求角色是 admin 才能落基线，
            # 而调用本方法的 approve_request 已经在路由层校验过
            # element:agent:system_publish。合成是为了复用发布逻辑，不是提权。
            # 注意：任何新增调用方都必须自己先做权限判定——服务层不做鉴权。
            published = await AgentManagerService.publish_version(
                session,
                agent.id,
                version.id,
                user={"role": "admin", "user_name": actor_name},
            )
        except AgentNotReadyError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"该版本还不满足上线条件：{'、'.join(exc.missing or [])}",
            ) from exc

        if not published:
            raise HTTPException(status_code=409, detail="发布失败，请检查该版本状态后重试")

        # publish_version 已 commit（含上面的申请单状态），这里只补通知
        await AgentSystemNotifier.notify_approved(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            applicant_user_id=request.requested_by,
            review_comment=request.review_comment,
            version_number=version.version_number,
        )
        await session.commit()
        return agent

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
        # 智能体可能已经被删：这类孤儿申请单必须能直接关掉，
        # 否则它永远挂在待审里，角标一直亮、列表里又找不到人处理。
        agent = await session.get(AIAgent, request.agent_id)

        request.status = STATUS_REJECTED
        request.reviewed_by = user_id
        request.reviewed_at = datetime.now()
        request.review_comment = reason

        if agent is not None:
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
        """管理员通过申请。

        * SYSTEM_PUBLISH：只把智能体置为系统级，不改任何版本状态；
        * VERSION_RELEASE：把申请里指定的草稿版本发布上线（系统级内容只能管理员发布）。
        """
        await assert_migrated(session)
        _, username, user_id = AgentSystemPublicationService.resolve_actor(user)
        request = await AgentSystemPublicationService._get_request(
            session, request_id, expect_status=STATUS_PENDING
        )
        agent = await AgentSystemPublicationService._get_agent(session, request.agent_id)

        if (getattr(request, "request_type", None) or REQUEST_TYPE_SYSTEM) == REQUEST_TYPE_RELEASE:
            return await AgentSystemPublicationService._approve_release_request(
                session, request, agent, username, user_id, comment
            )

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
        # 升级为系统级后，之前发出去还没被接受的分享一律作废：
        # 否则接收方仍能凭快照造出副本，等于绕开系统级的授权治理。
        await AgentSystemPublicationService._revoke_pending_shares(session, agent.id)

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
    # 权属人变更
    # ------------------------------------------------------------------

    @staticmethod
    async def transfer_owner(
        session: AsyncSession,
        agent_id: str,
        new_owner: str,
        user: Any,
        reason: Optional[str] = None,
    ) -> AIAgent:
        """把智能体转交给另一个人。

        存在的理由很具体：所有者离职或转岗后，这个智能体既没人能编辑、也没人能
        发布，只有管理员能碰——必须先有「换个人」这个动作，才谈得上交接。

        约束：
        * 只有管理员能做（高风险动作，不是普通编辑权限）；
        * 主助手禁止转交（它是系统固定入口，没有「归谁」的概念）；
        * 待审申请期间禁止转交（申请单记的是当时的申请人，换了人对不上）；
        * 目标用户必须存在且启用，转给一个停用账号等于换个方式变孤儿。
        """
        await assert_migrated(session)
        is_admin, actor_name, actor_id = AgentSystemPublicationService.resolve_actor(user)
        if not is_admin:
            raise HTTPException(status_code=403, detail="只有管理员可以变更智能体权属人")

        agent = await AgentSystemPublicationService._get_agent(session, agent_id)

        from app.services.ai.agent_manager import _is_main_general_agent_record

        if _is_main_general_agent_record(agent):
            raise HTTPException(
                status_code=409, detail="主助手是系统固定入口，不允许变更权属人"
            )

        previous_owner = (agent.created_by or "").strip()
        target = (new_owner or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="请选择新的权属人")
        if target == previous_owner:
            raise HTTPException(status_code=400, detail="该用户已经是这个智能体的权属人")

        current_status, pending_request_id = await read_system_status(session, agent.id)
        if current_status == SYSTEM_STATUS_PENDING or pending_request_id:
            raise HTTPException(
                status_code=409, detail="该智能体有待审的申请，请先处理完申请再变更权属人"
            )

        target_user = await session.execute(
            select(User).where(User.user_name == target).limit(1)
        )
        new_owner_row = target_user.scalars().first()
        if new_owner_row is None:
            raise HTTPException(status_code=404, detail="目标用户不存在")
        if int(getattr(new_owner_row, "status", 1) or 0) != 1:
            raise HTTPException(status_code=409, detail="目标用户已停用，不能作为权属人")

        agent.created_by = target
        await session.flush()

        text = (reason or "").strip()[:REQUEST_COMMENT_MAX] or None
        suffix = f"（{text}）" if text else ""
        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_TRANSFER_OWNER,
            actor_name=actor_name,
            actor_id=actor_id,
            reason=f"权属人从 {previous_owner or '未指定'} 变更为 {target}{suffix}",
        )

        # 系统级智能体还要补授权：列表按 created_by 判定可见性，但对话走 allowed_agents，
        # 靠的是 ResourcePermission。不补的话新所有者「看得到、用不了」。
        if bool(agent.is_system):
            await AgentSystemPublicationService.grant_agent_to_user(
                session, agent.id, getattr(new_owner_row, "id", None)
            )

        await AgentSystemNotifier.notify_owner_transferred(
            session,
            agent_id=agent.id,
            display_name=agent.display_name,
            previous_owner_user_name=previous_owner,
            new_owner_user_id=getattr(new_owner_row, "id", None),
            actor_name=actor_name,
        )
        await session.commit()

        # 换人之后，配置里可能留着只有原所有者能用的私有 MCP 引用：新所有者
        # 工具选择器里看不到它们，也就取消不掉。这里按新权属人的视角体检一遍。
        #
        # 刻意只体检、不改配置——转交是身份变更，顺手改版本内容等于悄悄动了别人的
        # 配置，而且必须重新发布才生效，容易变成「看起来修好了、线上还是旧的」。
        # 让管理员带着这份清单去编辑器里一键移除，动静分明。
        from app.services.ai.agent_manager import AgentManagerService

        current_version = await AgentSystemPublicationService._pick_relevant_version(
            session, agent.id
        )
        if current_version is not None:
            new_owner_ctx = {
                "role": getattr(new_owner_row, "role", "user") or "user",
                "user_name": target,
                "user_id": getattr(new_owner_row, "id", None),
                "id": getattr(new_owner_row, "id", None),
            }
            _, dropped = await AgentManagerService.drop_inaccessible_mcp_tools(
                session, current_version.tools, new_owner_ctx
            )
            if dropped:
                setattr(agent, "dropped_tool_refs", dropped)
                await AgentSystemPublicationService.record_event(
                    session,
                    agent=agent,
                    action=ACTION_TRANSFER_OWNER,
                    actor_name=actor_name,
                    actor_id=actor_id,
                    reason=(
                        f"转交后新权属人有 {len(dropped)} 个工具无权使用，"
                        "需在版本配置中移除失效引用后重新发布"
                    ),
                )
                await session.commit()

        return agent

    @staticmethod
    async def _pick_relevant_version(
        session: AsyncSession, agent_id: str
    ) -> Optional[AIAgentVersion]:
        """体检用哪个版本：优先线上版本，没有就取最新一版。"""
        latest = await session.execute(
            select(AIAgentVersion)
            .where(AIAgentVersion.agent_id == agent_id)
            .order_by(
                # PUBLISHED 优先，其余按版本号倒序
                case((AIAgentVersion.status == "PUBLISHED", 0), else_=1),
                AIAgentVersion.version_number.desc(),
            )
            .limit(1)
        )
        return latest.scalars().first()

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
        # 升级为系统级后，之前发出去还没被接受的分享一律作废：
        # 否则接收方仍能凭快照造出副本，等于绕开系统级的授权治理。
        await AgentSystemPublicationService._revoke_pending_shares(session, agent.id)

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

        # 下架要连同待审申请单一起关掉。否则那张单子会变成孤儿：智能体已经不是
        # 系统级了，审核抽屉里却还挂着一条 PENDING，管理员点通过还会在普通智能体
        # 上发布版本，而这条路径本来只该服务于系统级基线。
        await AgentSystemPublicationService._close_pending_requests(
            session,
            agent_id=agent.id,
            reason="智能体已下架，申请单自动关闭",
            actor_id=user_id,
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
        # LEFT JOIN：申请单对应的智能体可能已经被删掉（历史遗留或绕过拦截的删除）。
        # 用 INNER JOIN 时这类孤儿单会从列表消失，但 pending_count 仍把它算进去，
        # 结果是「角标显示 1 条，点进去空空如也」——而且永远清不掉。
        rows = await session.execute(
            select(AgentSystemRequest, AIAgent)
            .outerjoin(AIAgent, AIAgent.id == AgentSystemRequest.agent_id)
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

        # 版本上线申请要显示「哪一版要上线」，一次批量取版本号
        version_ids = {r.snapshot_version_id for r, _ in pairs if r.snapshot_version_id}
        version_numbers: Dict[str, int] = {}
        if version_ids:
            version_rows = await session.execute(
                select(AIAgentVersion.id, AIAgentVersion.version_number).where(
                    AIAgentVersion.id.in_(list(version_ids))
                )
            )
            version_numbers = {vid: num for vid, num in version_rows if vid is not None}

        items: List[Dict[str, Any]] = []
        for request, agent in pairs:
            items.append(
                {
                    "id": request.id,
                    "agent_id": request.agent_id,
                    # 申请单没冗余存名字，智能体没了就退化到 ID 前 8 位
                    "agent_name": (
                        agent.display_name if agent else f"（智能体已删除）{str(request.agent_id)[:8]}"
                    ),
                    "agent_identifier": agent.name if agent else None,
                    "engine_type": agent.engine_type if agent else None,
                    # 前端据此提示「智能体已删除，这条申请需要直接关闭」
                    "agent_missing": agent is None,
                    "status": request.status,
                    "request_type": getattr(request, "request_type", None) or REQUEST_TYPE_SYSTEM,
                    "version_number": version_numbers.get(request.snapshot_version_id)
                    if request.snapshot_version_id
                    else None,
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
        # 用 COUNT：这里只需要一个数字，没必要把 PENDING 的主键全拉回内存再数长度。
        # 口径与 list_requests 完全一致（都按申请单状态，不看智能体是否还在）。
        rows = await session.execute(
            select(func.count())
            .select_from(AgentSystemRequest)
            .where(AgentSystemRequest.status == STATUS_PENDING)
        )
        return int(rows.scalar() or 0)

    @staticmethod
    async def request_detail(
        session: AsyncSession, request_id: str
    ) -> Dict[str, Any]:
        await assert_migrated(session)
        request = await AgentSystemPublicationService._get_request(session, request_id)
        # 智能体可能已被删除：详情仍要能打开，否则孤儿申请单在列表里看得见、
        # 点进去却是 404，连带「驳回」按钮都出不来，这条就永远关不掉。
        agent = await session.get(AIAgent, request.agent_id)
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
            "agent_name": (
                agent.display_name if agent else f"（智能体已删除）{str(request.agent_id)[:8]}"
            ),
            "agent_identifier": agent.name if agent else None,
            "engine_type": agent.engine_type if agent else None,
            "agent_missing": agent is None,
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

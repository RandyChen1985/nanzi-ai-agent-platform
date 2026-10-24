"""系统级智能体的站内信通知。

收件人一律以「实际能用这个智能体的人」为准：``ai_agent_resource_permissions`` 里
用户直配 ∪ 命中角色的成员。角色成员按 ``ai_agent_user_role_relations`` 展开，
与 ``PermissionService.get_user_permissions`` 的计算口径一致——否则会出现
「通知发了但收不到的人没授权」或「有授权却没收到通知」。

通知分类统一用 ``agent``：前端 ``PortalNotificationBell`` 已有「智能体」标签映射，
自造新分类只会显示成「系统」，反而看不出是什么事。

下架时管理员可以选「不通知」，此时一条站内信都不落，只在事件表里记受影响人数——
「当时影响了 12 人，只是没告知」这件事本身也要能被追溯。
"""
from typing import Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import ResourcePermission, UserRoleRelation
from app.models.user import User
from app.services.portal_notification_service import PortalNotificationService

# 与 menu_seed 里的权限点保持同一常量，避免两处字符串漂移
SYSTEM_PUBLISH_PERMISSION = "element:agent:system_publish"

# 批量落库的分批大小：一次下架可能命中上千角色成员，逐条 create 会把事务拖长
_BATCH_SIZE = 500

NOTIFICATION_CATEGORY = "agent"


class AgentSystemNotifier:
    """站内信的唯一出口，收件人解析与文案都集中在这里。"""

    @staticmethod
    async def recipient_user_ids(session: AsyncSession, agent_id: str) -> Set[int]:
        """能用这个智能体的用户 ID 集合（用户直配 ∪ 角色成员）。"""
        rows = await session.execute(
            select(ResourcePermission.user_id, ResourcePermission.role_id).where(
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id == str(agent_id),
                ResourcePermission.enabled == True,  # noqa: E712 - SQLAlchemy 需用 == 比较
            )
        )
        user_ids: Set[int] = set()
        role_ids: Set[int] = set()
        for user_id, role_id in rows:
            if user_id is not None:
                user_ids.add(int(user_id))
            if role_id is not None:
                role_ids.add(int(role_id))

        if role_ids:
            member_rows = await session.execute(
                select(UserRoleRelation.user_id).where(
                    UserRoleRelation.role_id.in_(list(role_ids))
                )
            )
            for (user_id,) in member_rows:
                if user_id is not None:
                    user_ids.add(int(user_id))

        return user_ids

    @staticmethod
    async def reviewer_user_ids(session: AsyncSession) -> Set[int]:
        """能审核发布申请的人：所有管理员 ∪ 被显式授予审核权限的用户/角色成员。

        管理员天然通过 ``require_permission`` 的豁免，所以必须显式并进来，
        否则「申请提交了但没人收到提醒」。
        """
        user_ids: Set[int] = set()

        admin_rows = await session.execute(
            select(User.id).where(User.role == "admin")
        )
        for (user_id,) in admin_rows:
            if user_id is not None:
                user_ids.add(int(user_id))

        grant_rows = await session.execute(
            select(ResourcePermission.user_id, ResourcePermission.role_id).where(
                ResourcePermission.resource_type == "element",
                ResourcePermission.resource_id == SYSTEM_PUBLISH_PERMISSION,
                ResourcePermission.enabled == True,  # noqa: E712
            )
        )
        role_ids: Set[int] = set()
        for user_id, role_id in grant_rows:
            if user_id is not None:
                user_ids.add(int(user_id))
            if role_id is not None:
                role_ids.add(int(role_id))

        if role_ids:
            member_rows = await session.execute(
                select(UserRoleRelation.user_id).where(
                    UserRoleRelation.role_id.in_(list(role_ids))
                )
            )
            for (user_id,) in member_rows:
                if user_id is not None:
                    user_ids.add(int(user_id))

        return user_ids

    @staticmethod
    async def send(
        session: AsyncSession,
        user_ids: Iterable[int],
        *,
        agent_id: str,
        title: str,
        content: str,
        level: str = "info",
    ) -> int:
        """给一批用户发同一条站内信，返回落库条数。

        同一 user_id 只发一次（并集里可能既有直配又有角色命中）。
        """
        targets = sorted({int(uid) for uid in user_ids if uid is not None})
        for index, user_id in enumerate(targets):
            await PortalNotificationService.create(
                session,
                user_id=user_id,
                title=title,
                content=content,
                level=level,
                category=NOTIFICATION_CATEGORY,
                resource_type="agent",
                resource_id=str(agent_id),
            )
            if (index + 1) % _BATCH_SIZE == 0:
                await session.flush()

        if targets:
            await session.flush()
        return len(targets)

    # ------------------------------------------------------------------
    # 场景文案：集中在一处，避免接口层各处手写导致口径不一致
    # ------------------------------------------------------------------

    @staticmethod
    async def notify_unpublished(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        reason: str,
        recipients: Optional[Set[int]] = None,
    ) -> int:
        if recipients is None:
            recipients = await AgentSystemNotifier.recipient_user_ids(session, agent_id)
        return await AgentSystemNotifier.send(
            session,
            recipients,
            agent_id=agent_id,
            title=f"智能体「{display_name}」已下架",
            content=(
                f"管理员已下架智能体「{display_name}」，你暂时无法使用。\n\n"
                f"下架原因：{reason}\n\n"
                "如需继续使用，请联系管理员。"
            ),
            level="warning",
        )

    @staticmethod
    async def notify_restored(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
    ) -> int:
        recipients = await AgentSystemNotifier.recipient_user_ids(session, agent_id)
        return await AgentSystemNotifier.send(
            session,
            recipients,
            agent_id=agent_id,
            title=f"智能体「{display_name}」已重新上架",
            content=f"智能体「{display_name}」已重新上架为系统级，你可以继续使用。",
            level="info",
        )

    @staticmethod
    async def notify_published_to_owner(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        owner_user_id: Optional[int],
        direct: bool = True,
    ) -> int:
        """通知所有者「你的智能体被发布/成为了系统级」。

        直接发布（管理员操作别人的智能体）与普通上架走同一句文案，
        ``direct`` 只影响措辞里是否点明「由管理员操作」。
        """
        if owner_user_id is None:
            return 0
        lead = "管理员已把你创建的智能体" if direct else "你创建的智能体"
        return await AgentSystemNotifier.send(
            session,
            [owner_user_id],
            agent_id=agent_id,
            title=f"智能体「{display_name}」已成为系统级",
            content=(
                f"{lead}「{display_name}」发布为系统级，全平台被授权的同事可以使用。\n\n"
                "成为系统级后，它的内容对你变为只读：不能编辑、不能新建版本。"
                "如需继续迭代，可以在智能体中心「复制智能体」另开一条。"
            ),
            level="info",
        )

    @staticmethod
    async def notify_submitted(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        applicant_name: str,
        comment: Optional[str],
    ) -> int:
        reviewers = await AgentSystemNotifier.reviewer_user_ids(session)
        reason_line = f"\n\n申请理由：{comment}" if comment else ""
        return await AgentSystemNotifier.send(
            session,
            reviewers,
            agent_id=agent_id,
            title=f"新的发布申请：{display_name}",
            content=(
                f"{applicant_name} 申请把智能体「{display_name}」发布为系统级。"
                f"{reason_line}\n\n请前往「智能体中心 → 发布审核」处理。"
            ),
            level="info",
        )

    @staticmethod
    async def notify_cancelled(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        applicant_name: str,
    ) -> int:
        reviewers = await AgentSystemNotifier.reviewer_user_ids(session)
        return await AgentSystemNotifier.send(
            session,
            reviewers,
            agent_id=agent_id,
            title=f"发布申请已撤回：{display_name}",
            content=f"{applicant_name} 撤回了智能体「{display_name}」的发布申请，无需再审核。",
            level="info",
        )

    @staticmethod
    async def notify_rejected(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        applicant_user_id: Optional[int],
        comment: str,
    ) -> int:
        if applicant_user_id is None:
            return 0
        return await AgentSystemNotifier.send(
            session,
            [applicant_user_id],
            agent_id=agent_id,
            title=f"发布申请被驳回：{display_name}",
            content=(
                f"智能体「{display_name}」的发布申请未通过。\n\n"
                f"驳回理由：{comment}\n\n"
                "申请单已关闭、内容已解冻，你可以继续编辑后重新提交。"
            ),
            level="warning",
        )

    @staticmethod
    async def notify_approved(
        session: AsyncSession,
        *,
        agent_id: str,
        display_name: str,
        applicant_user_id: Optional[int],
        review_comment: Optional[str],
    ) -> int:
        if applicant_user_id is None:
            return 0
        extra = f"\n\n审核意见：{review_comment}" if review_comment else ""
        return await AgentSystemNotifier.send(
            session,
            [applicant_user_id],
            agent_id=agent_id,
            title=f"发布申请已通过：{display_name}",
            content=(
                f"智能体「{display_name}」已通过审核并发布为系统级，被授权的同事可以使用。"
                f"{extra}\n\n内容现在对你只读，如需继续迭代请复制一份另开新智能体。"
            ),
            level="success",
        )

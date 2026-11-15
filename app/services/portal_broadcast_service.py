"""管理员站内信群发：收件人解析 + 批量落库。

与 ``PortalNotificationService``（单条写入，供业务事件使用）分开的原因：

- 收件人是「范围」而不是具体某人，解析规则（部门子树、角色成员、去重、
  只发启用账号）只在这里实现一份；
- 群发是一次写几百上千行的广播动作，批量 insert 与单条 create 的事务边界
  不同，混进去会让原来那条按事件触发的链路承担不该有的失败模式。

**权限不在这里判断**：本模块假定调用方已通过 ``require_admin``。保留这一句是
故意的——把权限再写一遍会让「唯一入口是管理端」这件事变得模棱两可。真正的
用户自助发送仍然是 ``send_portal_notification`` 工具，收件人恒为当前登录人。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import desc, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, UserDepartment
from app.models.permission import UserRoleRelation
from app.models.portal_broadcast import PortalBroadcastRecord
from app.models.portal_notification import PortalNotification
from app.models.user import USER_STATUS_ENABLED, User
from app.schemas.portal_broadcast import BroadcastAudience
from app.services.department_service import subtree_like_prefix

logger = logging.getLogger(__name__)


# 站内信分类：前端铃铛据此显示「公告」标签，与 task_center / saved_report 等并列
BROADCAST_CATEGORY = "system_broadcast"
# 批量写入分片：与 AgentSystemNotifier._BATCH_SIZE 同量级，避免一次 executemany 带了
# 上千行参数打爆 MySQL 的 max_allowed_packet
_BATCH_SIZE = 200


class BroadcastAudienceChanged(Exception):
    """重新解析出的命中人数与前端预览时不一致。

    这不是错误配置，而是「世界变了」：预览之后有人入职/离职、部门调了人。
    此时按新人数静默发送是最危险的选择——管理员点的是「发给刚才那 300 人」。
    """

    def __init__(self, expected: Optional[int], actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"命中人数已变化：预览时 {expected} 人，当前 {actual} 人，请重新预览后再发送"
        )


def merge_recipient_scope(
    direct_ids: Sequence[int],
    dept_ids: Sequence[int],
    role_ids: Sequence[int],
) -> Set[int]:
    """三个维度取并集（纯函数，便于单测）。

    并集而不是交集：管理员勾「研发部 + 角色『值班经理』」的语义是这两拨人都要收到。
    多部门/多角色的成员天然会重复，靠 set 去重。
    """
    merged: Set[int] = set()
    for group in (direct_ids, dept_ids, role_ids):
        for raw in group or ():
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                merged.add(value)
    return merged


class PortalBroadcastService:
    @staticmethod
    async def _dept_member_ids(db: AsyncSession, dept_ids: Sequence[int]) -> Set[int]:
        """部门子树成员：勾选的部门 + 其全部下级部门。

        用物化路径 ``path`` 前缀匹配而不是递归 CTE：Department 表的 path 形如
        ``/1/7/23/``，``subtree_like_prefix`` 已保证 ``/1/7/`` 不会误匹配 ``/1/70/``。
        """
        rows = await db.execute(
            select(Department.id, Department.path).where(Department.id.in_(list(dept_ids)))
        )
        pairs = [(int(dept_id), str(path or "/")) for dept_id, path in rows.all()]
        if not pairs:
            return set()

        conditions = [Department.path.like(subtree_like_prefix(path)) for _, path in pairs]
        subtree_rows = await db.execute(select(Department.id).where(or_(*conditions)))
        subtree_ids = [int(value) for value in subtree_rows.scalars().all() if value is not None]
        if not subtree_ids:
            return set()

        member_rows = await db.execute(
            select(UserDepartment.user_id).where(UserDepartment.dept_id.in_(subtree_ids))
        )
        return {int(uid) for uid in member_rows.scalars().all() if uid is not None}

    @staticmethod
    async def _role_member_ids(db: AsyncSession, role_ids: Sequence[int]) -> Set[int]:
        rows = await db.execute(
            select(UserRoleRelation.user_id).where(
                UserRoleRelation.role_id.in_(list(role_ids))
            )
        )
        return {int(uid) for uid in rows.scalars().all() if uid is not None}

    @staticmethod
    async def _enabled_users(
        db: AsyncSession, candidate_ids: Set[int]
    ) -> List[Dict[str, Any]]:
        """过滤成启用账号，并按 ID 升序返回（顺序稳定，便于测试与预览一致）。"""
        if not candidate_ids:
            return []
        rows = await db.execute(
            select(User.id, User.user_name, User.real_name)
            .where(User.id.in_(list(candidate_ids)), User.status == USER_STATUS_ENABLED)
            .order_by(User.id)
        )
        return [
            {"user_id": int(uid), "user_name": str(user_name or ""), "real_name": real_name}
            for uid, user_name, real_name in rows.all()
        ]

    @classmethod
    async def resolve_recipients(
        cls, db: AsyncSession, audience: BroadcastAudience
    ) -> List[Dict[str, Any]]:
        """范围 → 确定的收件人列表（只含启用账号，已去重、按 ID 升序）。"""
        if audience.is_empty():
            return []

        dept_members = (
            await cls._dept_member_ids(db, audience.dept_ids) if audience.dept_ids else set()
        )
        role_members = (
            await cls._role_member_ids(db, audience.role_ids) if audience.role_ids else set()
        )
        candidate = merge_recipient_scope(
            audience.user_ids, dept_members, role_members
        )
        return await cls._enabled_users(db, candidate)

    @classmethod
    async def preview(
        cls, db: AsyncSession, audience: BroadcastAudience, limit: int = 20
    ) -> Dict[str, Any]:
        recipients = await cls.resolve_recipients(db, audience)
        sample = recipients[: max(0, int(limit or 0))]
        return {"total": len(recipients), "sample": sample}

    @classmethod
    def _build_metadata(
        cls,
        *,
        operator: Dict[str, Any],
        audience: BroadcastAudience,
        total: int,
        record_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """每条站内信都带来源信息。

        用户点开详情时得能看出「这是管理员发的公告」，而不是一条无从追溯的系统消息；
        管理员侧也能从消息反查这是哪次群发（``broadcast_record_id``）。
        """
        return {
            "source": "admin_broadcast",
            "broadcast_record_id": record_id,
            "sender_user_id": int(operator.get("user_id") or 0) or None,
            "sender_user_name": operator.get("user_name"),
            "sender_real_name": operator.get("real_name"),
            "audience": {
                "user_count": len(audience.user_ids),
                "dept_count": len(audience.dept_ids),
                "role_count": len(audience.role_ids),
                "recipient_total": total,
            },
        }

    @classmethod
    def _build_rows(
        cls,
        *,
        recipients: Sequence[Dict[str, Any]],
        title: str,
        content: str,
        level: str,
        metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """构造批量 insert 的行参数。

        ``meta_info`` 这个键名不是随手写的：模型里该列声明为
        ``meta_info = Column("metadata", JSON)``，列名是 metadata，插入参数要用
        属性名 meta_info。写成 metadata 会直接抛 CompileError（未消费的列名），
        所以这里单独抽出来，让测试能拿真实表结构跑一次插入。
        """
        now = datetime.now()
        return [
            {
                "user_id": int(item["user_id"]),
                "category": BROADCAST_CATEGORY,
                "level": level,
                "title": title,
                "content": content,
                "resource_type": None,
                "resource_id": None,
                "meta_info": metadata,
                "read_at": None,
                "created_at": now,
            }
            for item in recipients
        ]

    @classmethod
    async def send(
        cls,
        db: AsyncSession,
        *,
        operator: Dict[str, Any],
        audience: BroadcastAudience,
        title: str,
        content: str,
        level: str = "info",
        confirm_total: Optional[int] = None,
    ) -> int:
        """解析范围并批量写入站内信，返回写入条数。

        调用方（端点）负责参数校验与权限；这里只保证「写哪些人、写几条」是确定的。
        """
        recipients = await cls.resolve_recipients(db, audience)
        if not recipients:
            return 0

        if confirm_total is not None and int(confirm_total) != len(recipients):
            raise BroadcastAudienceChanged(confirm_total, len(recipients))

        # 记录与站内信同一个事务：任何一步失败都不该留下「有历史记录但没发出去」
        # 或反过来的半成品
        try:
            record = PortalBroadcastRecord(
                sender_user_id=int(operator.get("user_id") or 0),
                sender_user_name=str(operator.get("user_name") or ""),
                sender_real_name=operator.get("real_name"),
                title=title,
                content=content,
                level=level,
                audience_user_ids=list(audience.user_ids),
                audience_dept_ids=list(audience.dept_ids),
                audience_role_ids=list(audience.role_ids),
                recipient_count=len(recipients),
                created_at=datetime.now(),
            )
            db.add(record)
            # flush 拿到自增 id，写进每条消息的 metadata，便于从消息反查这次群发
            await db.flush()

            metadata = cls._build_metadata(
                operator=operator,
                audience=audience,
                total=len(recipients),
                record_id=int(record.id) if record.id is not None else None,
            )
            rows = cls._build_rows(
                recipients=recipients,
                title=title,
                content=content,
                level=level,
                metadata=metadata,
            )

            # 分片 executemany：一次几百行，既比逐条 create 快，又不会把参数包撑爆
            for start in range(0, len(rows), _BATCH_SIZE):
                await db.execute(
                    insert(PortalNotification), rows[start : start + _BATCH_SIZE]
                )
            await db.commit()
        except Exception:
            # 显式回滚：失败的事务留在 session 上会让后续请求撞 PendingRollbackError，
            # 而群发失败的典型原因是单行超长/连接中断，不值得连带污染整个连接
            await db.rollback()
            raise

        logger.info(
            "管理员群发站内信：sender=%s sent=%s title=%s audience_users=%s dept=%s role=%s",
            operator.get("user_name"),
            len(rows),
            title[:60],
            len(audience.user_ids),
            len(audience.dept_ids),
            len(audience.role_ids),
        )
        return len(rows)

    # ------------------------------------------------------------------ #
    # 历史发送记录
    # ------------------------------------------------------------------ #

    @staticmethod
    def record_to_summary(record: PortalBroadcastRecord) -> Dict[str, Any]:
        """ORM 行 → 列表项（纯函数，便于单测）。

        正文只给截断预览：列表页不需要全文，全文走详情接口。截断在换行/空白处
        之前先压平空白，避免多行 Markdown 在列表里撑成一大块。
        """
        raw_content = str(record.content or "")
        flattened = " ".join(raw_content.split())
        preview = flattened[:160] + ("…" if len(flattened) > 160 else "")
        created_at = record.created_at
        return {
            "id": int(record.id),
            "title": record.title,
            "level": record.level or "info",
            "content_preview": preview,
            "audience": BroadcastAudience(
                user_ids=list(record.audience_user_ids or []),
                dept_ids=list(record.audience_dept_ids or []),
                role_ids=list(record.audience_role_ids or []),
            ).model_dump(),
            "recipient_count": int(record.recipient_count or 0),
            "sender_user_name": record.sender_user_name or "",
            "sender_real_name": record.sender_real_name,
            "created_at": created_at.isoformat() if created_at else None,
        }

    @classmethod
    def record_to_detail(cls, record: PortalBroadcastRecord) -> Dict[str, Any]:
        """列表项 + 正文全文，供「回填重发」使用。"""
        detail = cls.record_to_summary(record)
        detail["content"] = str(record.content or "")
        return detail

    @classmethod
    async def list_records(
        cls, db: AsyncSession, *, limit: int, offset: int
    ) -> tuple[List[Dict[str, Any]], int]:
        total = int(
            (await db.execute(select(func.count()).select_from(PortalBroadcastRecord))).scalar()
            or 0
        )
        if total == 0:
            return [], 0
        rows = (
            await db.execute(
                select(PortalBroadcastRecord)
                .order_by(desc(PortalBroadcastRecord.created_at), desc(PortalBroadcastRecord.id))
                .offset(max(0, int(offset)))
                .limit(max(1, int(limit)))
            )
        ).scalars().all()
        return [cls.record_to_summary(row) for row in rows], total

    @classmethod
    async def get_record(
        cls, db: AsyncSession, record_id: int
    ) -> Optional[Dict[str, Any]]:
        record = (
            await db.execute(
                select(PortalBroadcastRecord).where(
                    PortalBroadcastRecord.id == int(record_id)
                )
            )
        ).scalar_one_or_none()
        return cls.record_to_detail(record) if record is not None else None

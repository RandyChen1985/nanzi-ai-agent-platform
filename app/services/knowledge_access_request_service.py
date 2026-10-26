"""知识库访问申请：提交 → 审批 → 落授权。

审批通过只是写一条 ``ResourcePermission``，与手工授权完全等价——
这样撤回、审计、归属人转移全部沿用既有链路，不产生第二套权限事实。

两个刻意的限制：
- 同一人对同一库只能有一条 pending，否则审批列表会被重复申请刷屏；
- 不能审批自己的申请，否则「申请-审批」形同自助开权限。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.models.knowledge import KnowledgeAccessRequest
from app.models.permission import ResourcePermission

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"


class AccessRequestError(Exception):
    """业务规则不满足：重复申请、状态已变、越权审批等。"""


class KnowledgeAccessRequestService:
    def __init__(self, db):
        self.db = db

    async def pending_for(self, dataset_id: str, user_id: int) -> Optional[KnowledgeAccessRequest]:
        result = await self.db.execute(
            select(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.dataset_id == dataset_id,
                KnowledgeAccessRequest.applicant_user_id == user_id,
                KnowledgeAccessRequest.status == STATUS_PENDING,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def create(
        self,
        *,
        dataset_id: str,
        dataset_name: Optional[str],
        user: dict,
        reason: Optional[str] = None,
        dept_id: Optional[int] = None,
    ) -> KnowledgeAccessRequest:
        user_id = int(user["user_id"])
        if await self.pending_for(dataset_id, user_id):
            raise AccessRequestError("你已经提交过申请，正在等待审批")

        row = KnowledgeAccessRequest(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            applicant_user_id=user_id,
            applicant_user_name=user.get("user_name"),
            applicant_dept_id=dept_id,
            reason=(reason or "").strip() or None,
            status=STATUS_PENDING,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get(self, request_id: int) -> KnowledgeAccessRequest:
        row = await self.db.get(KnowledgeAccessRequest, request_id)
        if not row:
            raise AccessRequestError("申请不存在或已被删除")
        return row

    async def cancel(self, request_id: int, user: dict) -> KnowledgeAccessRequest:
        row = await self.get(request_id)
        if int(row.applicant_user_id) != int(user["user_id"]):
            raise AccessRequestError("只能撤回自己的申请")
        if row.status != STATUS_PENDING:
            raise AccessRequestError(f"这条申请已经处理过（当前状态：{row.status}）")
        row.status = STATUS_CANCELLED
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def decide(
        self,
        *,
        request_id: int,
        reviewer: dict,
        approve: bool,
        comment: Optional[str] = None,
    ) -> KnowledgeAccessRequest:
        row = await self.get(request_id)
        if row.status != STATUS_PENDING:
            raise AccessRequestError(f"这条申请已经处理过（当前状态：{row.status}）")
        if int(row.applicant_user_id) == int(reviewer["user_id"]):
            raise AccessRequestError("不能审批自己提交的申请")

        row.status = STATUS_APPROVED if approve else STATUS_REJECTED
        row.reviewer_user_id = int(reviewer["user_id"])
        row.reviewer_user_name = reviewer.get("user_name")
        row.review_comment = (comment or "").strip() or None
        row.reviewed_at = datetime.now()

        if approve:
            await self._grant(row.dataset_id, int(row.applicant_user_id))

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def _grant(self, dataset_id: str, user_id: int) -> None:
        existing = (
            await self.db.execute(
                select(ResourcePermission)
                .where(
                    ResourcePermission.resource_type == "dataset",
                    ResourcePermission.resource_id == dataset_id,
                    ResourcePermission.user_id == user_id,
                )
                .limit(1)
            )
        ).scalars().first()
        if existing:
            # 之前授权被禁用过又被重新申请：启用回原记录，别插重复行
            existing.enabled = True
            return
        self.db.add(
            ResourcePermission(
                user_id=user_id,
                resource_type="dataset",
                resource_id=dataset_id,
                enabled=True,
            )
        )

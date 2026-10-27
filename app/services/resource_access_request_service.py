"""资源访问申请：知识库与数据集共用的「申请 → 审批 → 落授权」链路。

为什么共用一张表、一个服务：

- 统一审批页要把两类申请按时间混排分页，分表就得 UNION，排序与总数都要重写；
- 审批通过的动作对两类完全一样——写一条 ``ResourcePermission``，与手工授权等价，
  所以撤回、审计、归属人转移都沿用既有链路，不产生第二套权限事实。

三个刻意的限制：
- 同一人对同一资源只能有一条 pending，否则审批列表会被重复申请刷屏；
- 不能审批自己的申请，否则「申请-审批」形同自助开权限；
- 私有知识库与停用数据集不接受/不通过申请——批了也打不开，只会制造误解。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset, MetaMetric, MetaTable
from app.core.orm import AsyncSessionLocal
from app.models.permission import ResourcePermission, UserRoleRelation
from app.models.user import User

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

#: 知识库（RAGFlow Dataset）
RESOURCE_TYPE_KNOWLEDGE = "dataset"
#: 数据集（meta_datasets）
RESOURCE_TYPE_METADATA = "metadata"
SUPPORTED_RESOURCE_TYPES = (RESOURCE_TYPE_KNOWLEDGE, RESOURCE_TYPE_METADATA)


def _safe_int(value: Any) -> Optional[int]:
    """引擎侧的计数可能是字符串或空串；拿不到就返回 None，别显示成 0。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class AccessRequestError(Exception):
    """业务规则不满足：重复申请、状态已变、越权审批、资源不可用等。"""


class ResourceAccessRequestService:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    async def pending_for(
        self, resource_type: str, resource_id: str, user_id: int
    ) -> Optional[KnowledgeAccessRequest]:
        result = await self.db.execute(
            select(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.resource_type == resource_type,
                KnowledgeAccessRequest.dataset_id == str(resource_id),
                KnowledgeAccessRequest.applicant_user_id == int(user_id),
                KnowledgeAccessRequest.status == STATUS_PENDING,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def get(
        self, request_id: int, *, for_update: bool = False
    ) -> KnowledgeAccessRequest:
        stmt = select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.id == int(request_id)
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db.execute(stmt)).scalars().first()
        if not row:
            raise AccessRequestError("申请不存在或已被删除")
        return row

    # ------------------------------------------------------------------ #
    # 申请
    # ------------------------------------------------------------------ #

    async def create(
        self,
        *,
        resource_type: str,
        resource_id: str,
        resource_name: Optional[str],
        user: Mapping[str, Any],
        reason: Optional[str] = None,
        dept_id: Optional[int] = None,
    ) -> KnowledgeAccessRequest:
        if resource_type not in SUPPORTED_RESOURCE_TYPES:
            raise AccessRequestError("不支持的资源类型")

        resource_id = str(resource_id or "").strip()
        if not resource_id:
            raise AccessRequestError("缺少资源 ID")

        user_id = int(user["user_id"])
        if str(user.get("role") or "") == "admin":
            raise AccessRequestError("管理员对所有资源都有访问权，无需申请")
        # 列是 VARCHAR(1000)：超长在 MySQL 严格模式会直接 1406 → 500，
        # 前端只能看到「提交申请失败」。在这里挡住并给出可执行的文案。
        if reason and len(str(reason)) > 1000:
            raise AccessRequestError("申请理由最多 1000 个字符")

        if resource_type == RESOURCE_TYPE_KNOWLEDGE:
            meta = await self._require_knowledge(resource_id)
            if (meta.visibility or "private") == "private":
                raise AccessRequestError("该知识库为个人私有，不接受申请")
            # 前端只传 ID，名称由服务端回填：申请被审批时资源可能已改名或删除，
            # 记录里的名称快照要保证审批人看得懂当时申请的是什么
            resource_name = resource_name or meta.name
        else:
            # 归一化成 str(int(id))：授权行与全站判定（广场 has_access、只读概览、
            # 数据集选择器）都用规范值，传 "05"/"+5" 会写出一条永不生效的授权，
            # 而且 pending 去重按原始串比较，同一资源能被重复申请。
            try:
                resource_id = str(int(resource_id))
            except (TypeError, ValueError):
                raise AccessRequestError("数据集 ID 不合法") from None
            dataset = await self._require_metadata(resource_id)
            if int(dataset.status or 0) != 1:
                raise AccessRequestError("数据集已停用，不能申请")
            resource_name = resource_name or dataset.display_name or dataset.name

        if await self._has_access(resource_type, resource_id, user_id, user.get("user_name")):
            raise AccessRequestError("你已经有访问权限，无需重复申请")
        if await self.pending_for(resource_type, resource_id, user_id):
            raise AccessRequestError("你已经提交过申请，正在等待审批")

        # 申请人部门由服务端补：兼容层以前把「库归属部门」塞进这一列，
        # 语义完全错位，一旦后续按部门审批/统计就会直接误判。
        if dept_id is None:
            dept_id = await self._resolve_applicant_dept(user_id)

        row = KnowledgeAccessRequest(
            resource_type=resource_type,
            dataset_id=resource_id,
            dataset_name=(resource_name or "").strip() or None,
            applicant_user_id=user_id,
            applicant_user_name=user.get("user_name"),
            applicant_dept_id=dept_id,
            reason=(reason or "").strip() or None,
            status=STATUS_PENDING,
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            # 迁移 V194/V94 在 (申请人, 资源类型, 资源) 上对 pending 建了唯一索引。
            # 应用层的 pending_for 是 check-then-insert，两个请求同时到会一起通过检查，
            # 这里把数据库抛的冲突翻译回同一条业务文案。
            await self.db.rollback()
            raise AccessRequestError("你已经提交过申请，正在等待审批") from None
        await self.db.refresh(row)

        await self._audit(
            resource_type,
            resource_id,
            "request_access",
            user,
            detail={"request_id": row.id, "reason": row.reason},
        )
        await self._notify_created(row)
        return row

    # ------------------------------------------------------------------ #
    # 审批与撤回
    # ------------------------------------------------------------------ #

    async def decide(
        self,
        *,
        request_id: int,
        reviewer: Mapping[str, Any],
        approve: bool,
        comment: Optional[str] = None,
    ) -> KnowledgeAccessRequest:
        if str(reviewer.get("role") or "") != "admin":
            raise AccessRequestError("只有管理员可以审批资源访问申请")
        if comment and len(str(comment)) > 1000:
            raise AccessRequestError("审批意见最多 1000 个字符")

        # 行锁：两个管理员同时点「通过」会各自读到 pending，然后各写一次授权、
        # 各发一条通知；批准与撤回并发时甚至会出现「已撤回却拿到权限」。
        row = await self.get(request_id, for_update=True)
        if row.status != STATUS_PENDING:
            raise AccessRequestError(f"这条申请已经处理过（当前状态：{row.status}）")
        if int(row.applicant_user_id) == int(reviewer["user_id"]):
            raise AccessRequestError("不能审批自己提交的申请")

        # 申请挂了一天，资源可能已停用或被删：这时批准只会让申请人点开一个空页面
        if approve:
            await self._ensure_resource_available(row)

        row.status = STATUS_APPROVED if approve else STATUS_REJECTED
        row.reviewer_user_id = int(reviewer["user_id"])
        row.reviewer_user_name = reviewer.get("user_name")
        row.review_comment = (comment or "").strip() or None
        row.reviewed_at = datetime.now()

        applicant_id = int(row.applicant_user_id)
        if approve:
            await self._grant(row.resource_type, row.dataset_id, applicant_id)

        await self.db.commit()
        await self.db.refresh(row)

        await self._audit(
            row.resource_type,
            row.dataset_id,
            "grant" if approve else "reject",
            reviewer,
            detail={
                "request_id": row.id,
                "applicant_user_id": applicant_id,
                "applicant_user_name": row.applicant_user_name,
                "comment": row.review_comment,
            },
        )
        if approve:
            await self._invalidate(applicant_id, row.resource_type)
        await self._notify_decided(row)
        return row

    async def cancel(
        self, request_id: int, user: Mapping[str, Any]
    ) -> KnowledgeAccessRequest:
        row = await self.get(request_id, for_update=True)
        if int(row.applicant_user_id) != int(user["user_id"]):
            raise AccessRequestError("只能撤回自己的申请")
        if row.status != STATUS_PENDING:
            raise AccessRequestError(f"这条申请已经处理过（当前状态：{row.status}）")

        row.status = STATUS_CANCELLED
        await self.db.commit()
        await self.db.refresh(row)

        await self._audit(
            row.resource_type, row.dataset_id, "cancel", user, detail={"request_id": row.id}
        )
        return row

    # ------------------------------------------------------------------ #
    # 列表（带资源信息）
    # ------------------------------------------------------------------ #

    async def list_mine(
        self, user_id: int, *, resource_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        stmt = select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.applicant_user_id == int(user_id)
        )
        if resource_type:
            stmt = stmt.where(KnowledgeAccessRequest.resource_type == resource_type)
        stmt = stmt.order_by(
            KnowledgeAccessRequest.created_at.desc(), KnowledgeAccessRequest.id.desc()
        )
        return await self._enrich(list((await self.db.execute(stmt)).scalars().all()))

    @staticmethod
    def _page_window(page: int, size: int) -> tuple[int, int]:
        page = max(1, int(page or 1))
        size = max(1, min(500, int(size or 100)))
        return (page - 1) * size, size

    async def list_pending(
        self,
        *,
        resource_type: Optional[str] = None,
        page: int = 1,
        size: int = 200,
    ) -> tuple[List[Dict[str, Any]], int]:
        conditions = [KnowledgeAccessRequest.status == STATUS_PENDING]
        if resource_type:
            conditions.append(KnowledgeAccessRequest.resource_type == resource_type)
        # 待审批积压时不能一次全量返回并逐个补资源信息，否则列表页越用越慢
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(KnowledgeAccessRequest)
                .where(*conditions)
            )
        ).scalar()
        offset, limit = self._page_window(page, size)
        stmt = (
            select(KnowledgeAccessRequest)
            .where(*conditions)
            .order_by(
                KnowledgeAccessRequest.created_at.asc(), KnowledgeAccessRequest.id.asc()
            )
            .offset(offset)
            .limit(limit)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._enrich(rows), int(total or 0)

    async def list_reviews(
        self,
        *,
        resource_type: Optional[str] = None,
        page: int = 1,
        size: int = 100,
    ) -> tuple[List[Dict[str, Any]], int]:
        conditions = [
            KnowledgeAccessRequest.status.in_([STATUS_APPROVED, STATUS_REJECTED])
        ]
        if resource_type:
            conditions.append(KnowledgeAccessRequest.resource_type == resource_type)
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(KnowledgeAccessRequest)
                .where(*conditions)
            )
        ).scalar()
        offset, limit = self._page_window(page, size)
        stmt = (
            select(KnowledgeAccessRequest)
            .where(*conditions)
            .order_by(
                KnowledgeAccessRequest.reviewed_at.desc(), KnowledgeAccessRequest.id.desc()
            )
            .offset(offset)
            .limit(limit)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._enrich(rows), int(total or 0)

    # ------------------------------------------------------------------ #
    # 内部：资源校验
    # ------------------------------------------------------------------ #

    async def _resolve_applicant_dept(self, user_id: int) -> Optional[int]:
        """申请人所属部门（取主部门）。查不到就留空，不能因为部门异常拒掉申请。"""
        try:
            from app.models.department import UserDepartment

            value = (
                await self.db.execute(
                    select(UserDepartment.dept_id)
                    .where(UserDepartment.user_id == int(user_id))
                    .limit(1)
                )
            ).scalar()
            return int(value) if value is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取申请人部门失败（已忽略）: user_id=%s err=%s", user_id, exc)
            return None

    async def _require_knowledge(self, resource_id: str) -> KnowledgeBaseMetadata:
        row = (
            await self.db.execute(
                select(KnowledgeBaseMetadata)
                .where(KnowledgeBaseMetadata.ragflow_dataset_id == resource_id)
                .limit(1)
            )
        ).scalars().first()
        if not row or (row.status or "active") == "deleted":
            raise AccessRequestError("知识库不存在或已被删除")
        return row

    async def _require_metadata(self, resource_id: str) -> MetaDataset:
        try:
            dataset_id = int(resource_id)
        except (TypeError, ValueError):
            raise AccessRequestError("数据集 ID 不合法")
        row = await self.db.get(MetaDataset, dataset_id)
        if not row:
            raise AccessRequestError("数据集不存在或已被删除")
        return row

    async def _ensure_resource_available(self, row: KnowledgeAccessRequest) -> None:
        if row.resource_type == RESOURCE_TYPE_METADATA:
            dataset = await self._require_metadata(row.dataset_id)
            if int(dataset.status or 0) != 1:
                raise AccessRequestError("数据集已停用，不能通过这条申请")
        else:
            meta = await self._require_knowledge(row.dataset_id)
            # 申请挂起期间库可能被改成个人私有。private 的语义是「有且仅有归属人」，
            # 权限解析会刻意忽略授权记录——批了等于没批，还会给申请人发一条
            # 「权限已生效」的通知，事后完全无法解释。
            if (meta.visibility or "private") == "private":
                raise AccessRequestError("该知识库已改为个人私有，无法通过，请驳回")

    async def _has_access(
        self,
        resource_type: str,
        resource_id: str,
        user_id: int,
        user_name: Optional[str] = None,
    ) -> bool:
        if resource_type == RESOURCE_TYPE_KNOWLEDGE:
            from app.services.permission_service import PermissionService

            # user_name 必须一起传：owner_user_id 为空的历史库靠 created_by 认归属，
            # 漏传会让归属人对自己的库被判「无权限」，于是能再提一次无意义的申请
            access = await PermissionService(self.db).get_knowledge_base_access(
                int(user_id), user_name
            )
            return resource_id in (access.get("accessible_ids") or set())

        role_ids = (
            await self.db.execute(
                select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == int(user_id))
            )
        ).scalars().all()

        condition = ResourcePermission.user_id == int(user_id)
        if role_ids:
            condition = condition | ResourcePermission.role_id.in_(role_ids)
        stmt = select(ResourcePermission).where(
            ResourcePermission.resource_type == RESOURCE_TYPE_METADATA,
            ResourcePermission.resource_id == resource_id,
            ResourcePermission.enabled.is_(True),
        ).where(condition)
        return (await self.db.execute(stmt.limit(1))).scalars().first() is not None

    # ------------------------------------------------------------------ #
    # 内部：授权与缓存
    # ------------------------------------------------------------------ #

    async def _grant(self, resource_type: str, resource_id: str, user_id: int) -> None:
        existing = (
            await self.db.execute(
                select(ResourcePermission)
                .where(
                    ResourcePermission.resource_type == resource_type,
                    ResourcePermission.resource_id == resource_id,
                    ResourcePermission.user_id == int(user_id),
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
                user_id=int(user_id),
                resource_type=resource_type,
                resource_id=resource_id,
                enabled=True,
            )
        )

    async def _invalidate(self, user_id: int, resource_type: str) -> None:
        """授权生效后必须清缓存，否则申请人最长要等一小时才看得见。

        两套缓存：权限缓存（accessible_ids）与智能体数据集菜单缓存。
        """
        from app.services.permission_service import PermissionService

        # DB 已经提交、申请已经是 approved，此时缓存没清就是「批了但没生效」。
        # 单次失败原来只 warning 一句，用户要等满 TTL（1 小时）才突然可用，中间
        # 还会再提一次申请。这里重试一次，仍失败则升到 error 并写明后果。
        for attempt in (1, 2):
            try:
                await PermissionService(self.db).invalidate_cached_permissions_for_users(
                    [int(user_id)]
                )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    logger.error(
                        "清理权限缓存失败（授权已生效，最长 1 小时内不可见）: user=%s err=%s",
                        user_id,
                        exc,
                    )
                else:
                    logger.warning("清理权限缓存失败，重试一次: user=%s err=%s", user_id, exc)

        if resource_type == RESOURCE_TYPE_METADATA:
            for attempt in (1, 2):
                try:
                    from app.services.ai.config import AgentConfigProvider

                    await AgentConfigProvider.invalidate_dataset_menu_cache(
                        user_id=int(user_id)
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        logger.error(
                            "清理数据集菜单缓存失败（授权已生效）: user=%s err=%s",
                            user_id,
                            exc,
                        )
                    else:
                        logger.warning(
                            "清理数据集菜单缓存失败，重试一次: user=%s err=%s",
                            user_id,
                            exc,
                        )

    # ------------------------------------------------------------------ #
    # 内部：审计与通知
    # ------------------------------------------------------------------ #

    async def _audit(
        self,
        resource_type: str,
        resource_id: str,
        action: str,
        user: Mapping[str, Any],
        *,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            if resource_type == RESOURCE_TYPE_METADATA:
                from app.services.metadata_audit_service import MetadataAuditService

                await MetadataAuditService.log(
                    metadata_dataset_ids=[resource_id],
                    action=f"metadata:{action}",
                    user=dict(user),
                    detail=detail,
                )
            else:
                from app.services.knowledge_audit_service import KnowledgeAuditService

                await KnowledgeAuditService.log(
                    dataset_ids=[resource_id],
                    action=f"datasets:{action}",
                    user=dict(user),
                    detail=detail,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("写访问申请审计失败（已忽略）: %s", exc)

    async def _admin_user_ids(self, exclude: Optional[int] = None) -> List[int]:
        rows = (
            await self.db.execute(
                select(User.id).where(User.role == "admin", User.status == 1)
            )
        ).scalars().all()
        return [int(i) for i in rows if exclude is None or int(i) != int(exclude)]

    async def _notify_created(self, row: KnowledgeAccessRequest) -> None:
        """通知所有管理员：申请到了，等你们处理。

        申请人自己就是管理员时不发（``create`` 已拦），这里再排一次是为了防止
        一个管理员用另一个账号提申请时给自己也推一条。
        """
        try:
            from app.services.portal_notification_service import PortalNotificationService

            targets = await self._admin_user_ids(exclude=row.applicant_user_id)
            if not targets:
                return
            kind = "知识库" if row.resource_type == RESOURCE_TYPE_KNOWLEDGE else "数据集"
            label = row.dataset_name or row.dataset_id
            # 独立 session：通知是旁路。用主 session 写的话，一条通知失败（收件人
            # 已删除触发外键、metadata 序列化失败等）会把 session 打成需回滚状态，
            # 于是本已提交成功的申请在返回时读属性直接 500。
            async with AsyncSessionLocal() as notif_db:
                for admin_id in targets:
                    await PortalNotificationService.create(
                        notif_db,
                        user_id=admin_id,
                        title=f"{row.applicant_user_name or '有同事'} 申请访问{kind}「{label}」",
                        content=row.reason or "对方没有填写申请理由。",
                        level="info",
                        category="resource_access",
                        resource_type=row.resource_type,
                        resource_id=row.dataset_id,
                        metadata={
                            "open": "resource_access_review",
                            "request_id": row.id,
                            "resource_type": row.resource_type,
                        },
                    )
                await notif_db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("访问申请通知发送失败（已忽略）: %s", exc)

    async def _notify_decided(self, row: KnowledgeAccessRequest) -> None:
        try:
            from app.services.portal_notification_service import PortalNotificationService

            approved = row.status == STATUS_APPROVED
            label = row.dataset_name or row.dataset_id
            # 同样用独立 session，理由见 _notify_created
            async with AsyncSessionLocal() as notif_db:
                await PortalNotificationService.create(
                    notif_db,
                    user_id=int(row.applicant_user_id),
                    title=(
                        f"你申请访问「{label}」已通过"
                        if approved
                        else f"你申请访问「{label}」被驳回"
                    ),
                    content=row.review_comment
                    or ("权限已生效，现在可以打开查看。" if approved else "管理员没有填写驳回原因。"),
                    level="success" if approved else "warning",
                    category="resource_access_result",
                    resource_type=row.resource_type,
                    resource_id=row.dataset_id,
                    metadata={
                        "open": "resource_access_mine",
                        "request_id": row.id,
                        "status": row.status,
                    },
                )
                await notif_db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("访问申请结果通知发送失败（已忽略）: %s", exc)

    # ------------------------------------------------------------------ #
    # 内部：列表补资源信息
    # ------------------------------------------------------------------ #

    async def _enrich(
        self, rows: Sequence[KnowledgeAccessRequest]
    ) -> List[Dict[str, Any]]:
        """把申请记录与资源当前状态拼起来。

        审批人需要在通过前看到「这个资源还在不在、是不是停用了」，
        申请人需要在列表里看到资源名与规模，所以一次批量查完，避免 N+1。
        """
        kb_ids = [
            row.dataset_id
            for row in rows
            if row.resource_type == RESOURCE_TYPE_KNOWLEDGE
        ]
        ds_ids: List[int] = []
        for row in rows:
            if row.resource_type != RESOURCE_TYPE_METADATA:
                continue
            try:
                ds_ids.append(int(row.dataset_id))
            except (TypeError, ValueError):
                continue

        # 引擎侧（RAGFlow）当前到底还有没有这个库：本地 status 是平台自己的状态，
        # 巡检只写 is_missing 快照、从不改 status，所以「库在 RAGFlow 被删了」
        # 在审批页上一直显示成「资源存在」，批准后申请人打开只有 documents_error。
        # 读取失败时（engine_ids is None）退回只看本地状态，避免一次抖动把整页标红。
        engine_ids: Optional[set[str]] = None
        engine_stats: Dict[str, Dict[str, Any]] = {}
        if kb_ids:
            try:
                from app.services.ai.ragflow_client import RagFlowClient

                engine_items = await RagFlowClient(
                    config_prefix="knowledge_ragflow"
                ).list_all_datasets_cached()
                engine_ids = {str(item.get("id") or "") for item in (engine_items or [])}
                engine_stats = {
                    str(item.get("id") or ""): {
                        "doc_count": item.get("document_count"),
                        "chunk_count": item.get("chunk_count"),
                    }
                    for item in (engine_items or [])
                }
            except Exception as exc:  # noqa: BLE001 - 引擎失联不该让审批列表 500
                logger.warning("读取引擎数据集清单失败（降级为只看本地状态）: %s", exc)
                engine_ids = None

        kb_map: Dict[str, KnowledgeBaseMetadata] = {}
        if kb_ids:
            kb_rows = (
                await self.db.execute(
                    select(KnowledgeBaseMetadata).where(
                        KnowledgeBaseMetadata.ragflow_dataset_id.in_(kb_ids)
                    )
                )
            ).scalars().all()
            kb_map = {row.ragflow_dataset_id: row for row in kb_rows}

        ds_map: Dict[int, MetaDataset] = {}
        table_counts: Dict[int, int] = {}
        metric_counts: Dict[int, int] = {}
        if ds_ids:
            ds_rows = (
                await self.db.execute(select(MetaDataset).where(MetaDataset.id.in_(ds_ids)))
            ).scalars().all()
            ds_map = {int(row.id): row for row in ds_rows}
            table_counts = dict(
                (
                    await self.db.execute(
                        select(MetaTable.dataset_id, func.count())
                        .where(MetaTable.dataset_id.in_(ds_ids))
                        .group_by(MetaTable.dataset_id)
                    )
                ).all()
            )
            metric_counts = dict(
                (
                    await self.db.execute(
                        select(MetaMetric.dataset_id, func.count())
                        .where(MetaMetric.dataset_id.in_(ds_ids))
                        .group_by(MetaMetric.dataset_id)
                    )
                ).all()
            )

        items: List[Dict[str, Any]] = []
        for row in rows:
            item: Dict[str, Any] = {
                "id": row.id,
                "resource_type": row.resource_type,
                "resource_id": row.dataset_id,
                "resource_name": row.dataset_name,
                "applicant_user_id": row.applicant_user_id,
                "applicant_user_name": row.applicant_user_name,
                "reason": row.reason,
                "status": row.status,
                "reviewer_user_name": row.reviewer_user_name,
                "review_comment": row.review_comment,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            }

            if row.resource_type == RESOURCE_TYPE_KNOWLEDGE:
                meta = kb_map.get(row.dataset_id)
                alive = bool(meta) and (meta.status or "active") != "deleted"
                in_engine = True if engine_ids is None else row.dataset_id in engine_ids
                stats = engine_stats.get(row.dataset_id) or {}
                item.update(
                    {
                        "resource_exists": alive,
                        # 本地还在、引擎里没了 = 批了也打不开，审批人必须看得见
                        "resource_enabled": alive and in_engine,
                        "visibility": getattr(meta, "visibility", None),
                        "owner": getattr(meta, "owner", None),
                        "doc_count": _safe_int(stats.get("doc_count")),
                        "chunk_count": _safe_int(stats.get("chunk_count")),
                    }
                )
            else:
                dataset_id: Optional[int]
                try:
                    dataset_id = int(row.dataset_id)
                except (TypeError, ValueError):
                    dataset_id = None
                dataset = ds_map.get(dataset_id) if dataset_id is not None else None
                item.update(
                    {
                        "resource_exists": dataset is not None,
                        "resource_enabled": bool(dataset) and int(dataset.status or 0) == 1,
                        "data_source": getattr(dataset, "data_source", None),
                        "table_count": int(table_counts.get(dataset_id, 0)) if dataset else 0,
                        "metric_count": int(metric_counts.get(dataset_id, 0)) if dataset else 0,
                        "quality_score": getattr(dataset, "quality_score", None),
                    }
                )
            items.append(item)
        return items

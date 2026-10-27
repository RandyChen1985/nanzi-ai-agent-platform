# 资源广场（后端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库与数据集的「发现 → 申请 → 统一审批 → 落授权」在服务端完整可用，并提供资源广场与只读概览所需的两组查询接口。

**Architecture:** 复用 `ai_agent_knowledge_access_requests` 加 `resource_type` 区分资源；新增 `ResourceAccessRequestService` 统一申请与授权，知识库旧服务降为薄封装；新增 `MetadataAuditService` 与 `ai_agent_metadata_audit_logs` 记录数据集侧长期业务审计；新增 `resource_square.py` 与 `access_requests.py` 两个端点模块。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Pydantic 2 / MySQL + PostgreSQL 双迁移 / pytest。

**Spec:** [`docs/superpowers/specs/2026-10-27-resource-square-design.md`](../specs/2026-10-27-resource-square-design.md)

**测试前置：** 后端测试依赖本地 MySQL（或 PG）与 Redis，`tests/conftest.py` 的 `init_infrastructure` 会在每个用例前初始化。先确认 `pytest tests/api/portal/test_permission_api.py -q` 能跑通，再开始。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `db-prod/V192-resource_square_and_metadata_audit.sql` | 申请表加列 + 审计表（MySQL） |
| `db-prod-pg/V92-resource_square_and_metadata_audit.sql` | 同上（PostgreSQL） |
| `app/models/knowledge.py` | 申请表加 `resource_type` |
| `app/models/metadata.py` | 新增 `MetadataAuditLog` |
| `app/services/metadata_audit_service.py` | 数据集审计写入 |
| `app/services/resource_access_request_service.py` | 申请 / 审批 / 授权 / 通知 / 审计 |
| `app/services/knowledge_access_request_service.py` | 改为调用统一服务 |
| `app/api/portal/endpoints/access_requests.py` | 统一申请与审批接口 |
| `app/api/portal/endpoints/resource_square.py` | 广场查询 + 只读详情 |
| `app/api/portal/api.py` | 注册两个新 router |
| `app/services/workbench_home_service.py` | 工作台资源卡加两项 |
| `app/services/config_service.py` | 数据集目录开关 |
| `app/api/portal/endpoints/ragflow.py` | 旧「发现」改为复用新查询 |

---

### Task 1: 数据库迁移

**Files:**
- Create: `db-prod/V192-resource_square_and_metadata_audit.sql`
- Create: `db-prod-pg/V92-resource_square_and_metadata_audit.sql`

- [ ] **Step 1: 写 MySQL 迁移**

```sql
-- V192: 资源广场 —— 申请表区分资源类型 + 数据集业务审计
--
-- 背景：
--   1) `ai_agent_knowledge_access_requests` 原本只服务知识库。资源广场要让数据集
--      走同一条「申请 → 审批」链路，加 `resource_type` 区分，而不是再建一张表：
--      统一审批列表要按时间混排分页，两张表就得 UNION，排序与总数都会变复杂。
--   2) 数据集侧没有任何长期业务审计。访问日志只查 90 天、resource_id 埋在
--      request_params JSON 里；ResourcePermission 只有当前状态没有历史。
--      因此建独立审计表，支撑「谁在什么时候把哪个数据集授权给了谁」。
--
-- 取值：resource_type = dataset（知识库 RAGFlow Dataset）/ metadata（meta_datasets.id）
-- 存量行默认 'dataset'，无需回填。

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD COLUMN `resource_type` VARCHAR(20) NOT NULL DEFAULT 'dataset'
    COMMENT '资源类型: dataset=知识库 / metadata=数据集' AFTER `dataset_id`;

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD INDEX `idx_kbar_type_status` (`resource_type`, `status`);

CREATE TABLE IF NOT EXISTS `ai_agent_metadata_audit_logs` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `metadata_dataset_id` BIGINT NOT NULL COMMENT 'meta_datasets.id',
    `action` VARCHAR(64) NOT NULL COMMENT '操作码: metadata:request_access / metadata:grant ...',
    `user_id` BIGINT NULL COMMENT '操作人用户ID',
    `user_name` VARCHAR(64) NULL COMMENT '操作人账号快照',
    `status_code` INT NOT NULL DEFAULT 200 COMMENT 'HTTP 状态码（>=400 即失败）',
    `detail` JSON NULL COMMENT '操作细节（申请ID/目标用户/审批意见）',
    `client_ip` VARCHAR(50) NULL COMMENT '来源 IP',
    `error_message` TEXT NULL COMMENT '失败原因',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    PRIMARY KEY (`id`),
    KEY `idx_mal_dataset_created` (`metadata_dataset_id`, `created_at`),
    KEY `idx_mal_action_created` (`action`, `created_at`),
    KEY `idx_mal_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集业务审计';
```

- [ ] **Step 2: 写 PostgreSQL 迁移**

```sql
-- V92: 资源广场（PostgreSQL），与 MySQL V192 对齐

ALTER TABLE ai_agent_knowledge_access_requests
    ADD COLUMN IF NOT EXISTS resource_type VARCHAR(20) NOT NULL DEFAULT 'dataset';

COMMENT ON COLUMN ai_agent_knowledge_access_requests.resource_type IS
    '资源类型: dataset=知识库 / metadata=数据集';

CREATE INDEX IF NOT EXISTS idx_kbar_type_status
    ON ai_agent_knowledge_access_requests (resource_type, status);

CREATE TABLE IF NOT EXISTS ai_agent_metadata_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    metadata_dataset_id BIGINT NOT NULL,
    action VARCHAR(64) NOT NULL,
    user_id BIGINT NULL,
    user_name VARCHAR(64) NULL,
    status_code INT NOT NULL DEFAULT 200,
    detail JSONB NULL,
    client_ip VARCHAR(50) NULL,
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mal_dataset_created
    ON ai_agent_metadata_audit_logs (metadata_dataset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mal_action_created
    ON ai_agent_metadata_audit_logs (action, created_at);
CREATE INDEX IF NOT EXISTS idx_mal_user
    ON ai_agent_metadata_audit_logs (user_id);

COMMENT ON TABLE ai_agent_metadata_audit_logs IS '数据集业务审计';
```

- [ ] **Step 3: 人工检查两个文件的一致性**

对照：列名、默认值、索引名三者必须完全一致（PG 用小写）。`grep -n "idx_mal\|idx_kbar" db-prod/V192-*.sql db-prod-pg/V92-*.sql`，两边各出现 3 次与 1 次。

- [ ] **Step 4: Commit**

```bash
git add db-prod/V192-resource_square_and_metadata_audit.sql db-prod-pg/V92-resource_square_and_metadata_audit.sql
git commit -m "feat(db): 资源广场申请表加 resource_type 并新增数据集审计表"
```

> 迁移由用户在执行环境中运行，Agent 不操作数据库。

---

### Task 2: 模型字段

**Files:**
- Modify: `app/models/knowledge.py`（`KnowledgeAccessRequest`，类开头约 117 行）
- Modify: `app/models/metadata.py`（文件末尾追加 `MetadataAuditLog`）
- Test: `tests/api/test_resource_access_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_resource_access_models.py
from app.models.knowledge import KnowledgeAccessRequest
from app.models.metadata import MetadataAuditLog


def test_access_request_has_resource_type_with_knowledge_default():
    col = KnowledgeAccessRequest.__table__.c.resource_type
    assert col.nullable is False
    assert col.default.arg == "dataset"


def test_metadata_audit_log_table_shape():
    cols = MetadataAuditLog.__table__.c
    assert MetadataAuditLog.__tablename__ == "ai_agent_metadata_audit_logs"
    for name in ("metadata_dataset_id", "action", "user_id", "user_name",
                 "status_code", "detail", "client_ip", "error_message", "created_at"):
        assert name in cols
    index_names = {idx.name for idx in MetadataAuditLog.__table__.indexes}
    assert "idx_mal_dataset_created" in index_names
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_resource_access_models.py -q`
Expected: FAIL，`AttributeError: resource_type` / `ImportError: MetadataAuditLog`

- [ ] **Step 3: 加字段**

`app/models/knowledge.py`，在 `KnowledgeAccessRequest` 的 `dataset_id` 之后插入：

```python
    resource_type = Column(
        String(20),
        nullable=False,
        default="dataset",
        server_default="dataset",
        comment="资源类型: dataset=知识库 / metadata=数据集",
    )
```

并在 `__table_args__` 里补索引（若原类没有 `__table_args__`，新增）：

```python
    __table_args__ = (
        Index("idx_kbar_type_status", "resource_type", "status"),
    )
```

`app/models/metadata.py` 末尾追加：

```python
class MetadataAuditLog(Base):
    """数据集业务审计：谁在什么时候对哪个数据集做了什么。

    与 ``KnowledgeAuditLog`` 对称，独立于访问日志：访问日志每请求一条、默认只查
    90 天，且 ``resource_id`` 埋在 ``request_params`` JSON 里，按数据集查不动。
    """

    __tablename__ = "ai_agent_metadata_audit_logs"
    __table_args__ = (
        Index("idx_mal_dataset_created", "metadata_dataset_id", "created_at"),
        Index("idx_mal_action_created", "action", "created_at"),
        Index("idx_mal_user", "user_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    metadata_dataset_id = Column(BigInteger, nullable=False, comment="meta_datasets.id")
    action = Column(String(64), nullable=False, comment="操作码: metadata:grant ...")
    user_id = Column(BigInteger, nullable=True, comment="操作人用户 ID")
    user_name = Column(String(64), nullable=True, comment="操作人账号快照")
    status_code = Column(Integer, nullable=False, default=200, comment="HTTP 状态码")
    detail = Column(JSON, nullable=True, comment="操作细节（申请ID/目标用户/审批意见）")
    client_ip = Column(String(50), nullable=True, comment="来源 IP")
    error_message = Column(Text, nullable=True, comment="失败原因")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="操作时间")
```

`metadata.py` 顶部 import 补齐 `BigInteger`、`Index`、`Text`（按现有 import 行合并，避免重复）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_resource_access_models.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/models/knowledge.py app/models/metadata.py tests/api/test_resource_access_models.py
git commit -m "feat(model): 申请表加 resource_type，新增数据集审计模型"
```

---

### Task 3: 数据集审计服务

**Files:**
- Create: `app/services/metadata_audit_service.py`
- Test: `tests/api/test_metadata_audit_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_metadata_audit_service.py
import pytest
from sqlalchemy import select

from app.models.metadata import MetadataAuditLog
from app.services.metadata_audit_service import MetadataAuditService


@pytest.mark.asyncio
async def test_log_writes_one_row_per_dataset(db_session):
    ok = await MetadataAuditService.log(
        metadata_dataset_ids=[9101, 9102],
        action="metadata:grant",
        user={"user_id": 1, "user_name": "admin"},
        detail={"applicant_user_id": 7},
        client_ip="10.0.0.1",
    )
    assert ok is True
    rows = (await db_session.execute(
        select(MetadataAuditLog).where(MetadataAuditLog.metadata_dataset_id.in_([9101, 9102]))
    )).scalars().all()
    assert len(rows) == 2
    assert {r.action for r in rows} == {"metadata:grant"}


@pytest.mark.asyncio
async def test_log_swallows_errors():
    # 非法 action 长度不应炸到调用方：审计是旁路
    ok = await MetadataAuditService.log(
        metadata_dataset_ids=[9103], action="x" * 200, user={"user_id": 1, "user_name": "a"}
    )
    assert ok is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_metadata_audit_service.py -q`
Expected: FAIL，`ModuleNotFoundError: app.services.metadata_audit_service`

- [ ] **Step 3: 实现服务**

```python
"""数据集业务审计写入。

与 ``KnowledgeAuditService`` 同一套做法：独立 session（业务回滚也留痕）、
失败只记日志不抛（审计是旁路，不能反过来影响审批可用性）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from app.core.orm import AsyncSessionLocal
from app.models.metadata import MetadataAuditLog

logger = logging.getLogger(__name__)


class MetadataAuditService:
    @staticmethod
    async def log(
        *,
        metadata_dataset_ids: Iterable[Any],
        action: str,
        user: Optional[Dict[str, Any]] = None,
        status_code: int = 200,
        detail: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        ids: list[int] = []
        for raw in metadata_dataset_ids or []:
            try:
                token = int(raw)
            except (TypeError, ValueError):
                continue
            if token not in ids:
                ids.append(token)
        if not ids:
            return False

        actor = user or {}
        try:
            user_id = int(actor["user_id"]) if actor.get("user_id") is not None else None
        except (TypeError, ValueError):
            user_id = None

        try:
            async with AsyncSessionLocal() as session:
                session.add_all([
                    MetadataAuditLog(
                        metadata_dataset_id=dataset_id,
                        action=action,
                        user_id=user_id,
                        user_name=actor.get("user_name"),
                        status_code=status_code,
                        detail=detail or None,
                        client_ip=client_ip,
                        error_message=error_message,
                    )
                    for dataset_id in ids
                ])
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "写入数据集审计失败（已忽略）: action=%s ids=%s err=%s", action, ids, exc
            )
            return False
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_metadata_audit_service.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/metadata_audit_service.py tests/api/test_metadata_audit_service.py
git commit -m "feat(audit): 数据集业务审计写入服务"
```

---

### Task 4: 统一申请服务

**Files:**
- Create: `app/services/resource_access_request_service.py`
- Modify: `app/services/knowledge_access_request_service.py`（改为委托）
- Test: `tests/api/test_resource_access_request_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_resource_access_request_service.py
import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeBaseMetadata, KnowledgeAccessRequest
from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services.resource_access_request_service import (
    AccessRequestError,
    ResourceAccessRequestService,
)


@pytest.fixture
async def dataset(db_session):
    row = MetaDataset(
        name=f"rs_test_{uuid.uuid4().hex[:8]}",
        display_name="测试数据集",
        description="用于资源广场测试",
        status=1,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_create_metadata_request_and_reject_duplicate(db_session, dataset):
    svc = ResourceAccessRequestService(db_session)
    applicant = {"user_id": 9001, "user_name": "applicant_a"}

    row = await svc.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=dataset.display_name,
        user=applicant,
        reason="做经营分析",
    )
    assert row.status == "pending"
    assert row.resource_type == "metadata"

    with pytest.raises(AccessRequestError):
        await svc.create(
            resource_type="metadata",
            resource_id=str(dataset.id),
            resource_name=dataset.display_name,
            user=applicant,
        )


@pytest.mark.asyncio
async def test_approve_grants_metadata_permission(db_session, dataset):
    svc = ResourceAccessRequestService(db_session)
    row = await svc.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=dataset.display_name,
        user={"user_id": 9002, "user_name": "applicant_b"},
    )

    decided = await svc.decide(
        request_id=row.id,
        reviewer={"user_id": 1, "user_name": "admin", "role": "admin"},
        approve=True,
        comment="同意",
    )
    assert decided.status == "approved"

    perm = (await db_session.execute(
        select(ResourcePermission).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.resource_id == str(dataset.id),
            ResourcePermission.user_id == 9002,
        )
    )).scalars().first()
    assert perm is not None and perm.enabled is True


@pytest.mark.asyncio
async def test_cannot_approve_own_request(db_session, dataset):
    svc = ResourceAccessRequestService(db_session)
    row = await svc.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=dataset.display_name,
        user={"user_id": 9003, "user_name": "applicant_c"},
    )
    with pytest.raises(AccessRequestError):
        await svc.decide(
            request_id=row.id,
            reviewer={"user_id": 9003, "user_name": "applicant_c", "role": "admin"},
            approve=True,
        )


@pytest.mark.asyncio
async def test_cancel_only_own_pending(db_session, dataset):
    svc = ResourceAccessRequestService(db_session)
    row = await svc.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=dataset.display_name,
        user={"user_id": 9004, "user_name": "applicant_d"},
    )
    with pytest.raises(AccessRequestError):
        await svc.cancel(row.id, {"user_id": 9005, "user_name": "other"})
    cancelled = await svc.cancel(row.id, {"user_id": 9004, "user_name": "applicant_d"})
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_private_knowledge_base_cannot_be_requested(db_session):
    kb_id = f"kb_{uuid.uuid4().hex[:10]}"
    db_session.add(KnowledgeBaseMetadata(
        ragflow_dataset_id=kb_id, name="私有库", visibility="private", status="active",
    ))
    await db_session.commit()

    svc = ResourceAccessRequestService(db_session)
    with pytest.raises(AccessRequestError):
        await svc.create(
            resource_type="dataset",
            resource_id=kb_id,
            resource_name="私有库",
            user={"user_id": 9006, "user_name": "applicant_e"},
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_resource_access_request_service.py -q`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现统一服务**

```python
"""资源访问申请：知识库与数据集共用的「申请 → 审批 → 落授权」链路。

审批通过只是写一条 ``ResourcePermission``，与手工授权完全等价：撤回、审计、
归属人转移沿用既有链路，不产生第二套权限事实。

三个刻意的限制：
- 同一人对同一资源只能有一条 pending；
- 不能审批自己的申请；
- 私有知识库与停用数据集不接受申请（批了也打不开）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import func, select

from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset, MetaMetric, MetaTable
from app.models.permission import ResourcePermission, UserRoleRelation
from app.models.user import User

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

RESOURCE_TYPE_KNOWLEDGE = "dataset"   # 知识库（RAGFlow Dataset）
RESOURCE_TYPE_METADATA = "metadata"    # 数据集（meta_datasets）
SUPPORTED_RESOURCE_TYPES = (RESOURCE_TYPE_KNOWLEDGE, RESOURCE_TYPE_METADATA)


class AccessRequestError(Exception):
    """业务规则不满足：重复申请、状态已变、越权审批、资源不可用等。"""


class ResourceAccessRequestService:
    def __init__(self, db):
        self.db = db

    # ---------- 查询 ----------

    async def pending_for(self, resource_type: str, resource_id: str, user_id: int):
        result = await self.db.execute(
            select(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.resource_type == resource_type,
                KnowledgeAccessRequest.dataset_id == resource_id,
                KnowledgeAccessRequest.applicant_user_id == user_id,
                KnowledgeAccessRequest.status == STATUS_PENDING,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def get(self, request_id: int) -> KnowledgeAccessRequest:
        row = await self.db.get(KnowledgeAccessRequest, request_id)
        if not row:
            raise AccessRequestError("申请不存在或已被删除")
        return row

    # ---------- 申请 ----------

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
            raise AccessRequestError("你已有该资源的权限，无需申请")

        if resource_type == RESOURCE_TYPE_KNOWLEDGE:
            meta = await self._require_knowledge(resource_id)
            if (meta.visibility or "private") == "private":
                raise AccessRequestError("该知识库为个人私有，不接受申请")
            resource_name = resource_name or meta.name
        else:
            dataset = await self._require_metadata(resource_id)
            resource_name = resource_name or dataset.display_name or dataset.name

        if await self._has_access(resource_type, resource_id, user_id):
            raise AccessRequestError("你已经有访问权限，无需重复申请")
        if await self.pending_for(resource_type, resource_id, user_id):
            raise AccessRequestError("你已经提交过申请，正在等待审批")

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
        await self.db.commit()
        await self.db.refresh(row)

        await self._audit(
            resource_type, resource_id, "request_access", user,
            detail={"request_id": row.id, "reason": row.reason},
        )
        await self._notify_created(row)
        return row

    # ---------- 审批与撤回 ----------

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

        row = await self.get(request_id)
        if row.status != STATUS_PENDING:
            raise AccessRequestError(f"这条申请已经处理过（当前状态：{row.status}）")
        if int(row.applicant_user_id) == int(reviewer["user_id"]):
            raise AccessRequestError("不能审批自己提交的申请")

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
            row.resource_type, row.dataset_id,
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

    async def cancel(self, request_id: int, user: Mapping[str, Any]) -> KnowledgeAccessRequest:
        row = await self.get(request_id)
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

    # ---------- 列表（含资源信息） ----------

    async def list_mine(self, user_id: int, *, resource_type: Optional[str] = None):
        stmt = select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.applicant_user_id == user_id
        )
        if resource_type:
            stmt = stmt.where(KnowledgeAccessRequest.resource_type == resource_type)
        stmt = stmt.order_by(KnowledgeAccessRequest.created_at.desc(), KnowledgeAccessRequest.id.desc())
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._enrich(rows)

    async def list_pending(self, *, resource_type: Optional[str] = None):
        stmt = select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.status == STATUS_PENDING
        )
        if resource_type:
            stmt = stmt.where(KnowledgeAccessRequest.resource_type == resource_type)
        stmt = stmt.order_by(KnowledgeAccessRequest.created_at.asc(), KnowledgeAccessRequest.id.asc())
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._enrich(rows)

    async def list_reviews(self, *, resource_type: Optional[str] = None, limit: int = 100):
        stmt = select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.status.in_([STATUS_APPROVED, STATUS_REJECTED])
        )
        if resource_type:
            stmt = stmt.where(KnowledgeAccessRequest.resource_type == resource_type)
        stmt = stmt.order_by(
            KnowledgeAccessRequest.reviewed_at.desc(), KnowledgeAccessRequest.id.desc()
        ).limit(max(1, min(500, limit)))
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._enrich(rows)

    # ---------- 内部 ----------

    async def _require_knowledge(self, resource_id: str) -> KnowledgeBaseMetadata:
        row = (await self.db.execute(
            select(KnowledgeBaseMetadata).where(
                KnowledgeBaseMetadata.ragflow_dataset_id == resource_id
            ).limit(1)
        )).scalars().first()
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
                raise AccessRequestError("数据集已停用，不能通过")
        else:
            await self._require_knowledge(row.dataset_id)

    async def _has_access(self, resource_type: str, resource_id: str, user_id: int) -> bool:
        if resource_type == RESOURCE_TYPE_KNOWLEDGE:
            from app.services.permission_service import PermissionService

            access = await PermissionService(self.db).get_knowledge_base_access(user_id)
            return resource_id in (access.get("accessible_ids") or set())

        role_ids = (await self.db.execute(
            select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id)
        )).scalars().all()
        stmt = select(ResourcePermission).where(
            ResourcePermission.resource_type == RESOURCE_TYPE_METADATA,
            ResourcePermission.resource_id == resource_id,
            ResourcePermission.enabled.is_(True),
        )
        condition = ResourcePermission.user_id == user_id
        if role_ids:
            condition = condition | ResourcePermission.role_id.in_(role_ids)
        stmt = stmt.where(condition)
        return (await self.db.execute(stmt.limit(1))).scalars().first() is not None

    async def _grant(self, resource_type: str, resource_id: str, user_id: int) -> None:
        existing = (await self.db.execute(
            select(ResourcePermission).where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
                ResourcePermission.user_id == user_id,
            ).limit(1)
        )).scalars().first()
        if existing:
            # 之前被禁用过又被重新申请：启用回原记录，别插重复行
            existing.enabled = True
            return
        self.db.add(ResourcePermission(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            enabled=True,
        ))

    async def _invalidate(self, user_id: int, resource_type: str) -> None:
        try:
            from app.services.permission_service import PermissionService

            await PermissionService(self.db).invalidate_cached_permissions_for_users([user_id])
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理权限缓存失败（已忽略）: user=%s err=%s", user_id, exc)
        if resource_type == RESOURCE_TYPE_METADATA:
            try:
                from app.services.ai.config import AgentConfigProvider

                await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=user_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("清理数据集菜单缓存失败（已忽略）: user=%s err=%s", user_id, exc)

    async def _audit(self, resource_type, resource_id, action, user, *, detail=None) -> None:
        try:
            from app.services.metadata_audit_service import MetadataAuditService

            if resource_type == RESOURCE_TYPE_METADATA:
                await MetadataAuditService.log(
                    metadata_dataset_ids=[resource_id],
                    action=f"metadata:{action}",
                    user=user,
                    detail=detail,
                )
            else:
                from app.services.knowledge_audit_service import KnowledgeAuditService

                await KnowledgeAuditService.log(
                    dataset_ids=[resource_id],
                    action=f"datasets:{action}",
                    user=user,
                    detail=detail,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("写访问申请审计失败（已忽略）: %s", exc)

    # ---------- 通知 ----------

    async def _admin_user_ids(self, exclude: Optional[int] = None) -> List[int]:
        ids = (await self.db.execute(
            select(User.id).where(User.role == "admin", User.status == 1)
        )).scalars().all()
        return [int(i) for i in ids if exclude is None or int(i) != int(exclude)]

    async def _notify_created(self, row: KnowledgeAccessRequest) -> None:
        try:
            from app.services.portal_notification_service import PortalNotificationService

            kind = "知识库" if row.resource_type == RESOURCE_TYPE_KNOWLEDGE else "数据集"
            for admin_id in await self._admin_user_ids(exclude=row.applicant_user_id):
                await PortalNotificationService.create(
                    self.db,
                    user_id=admin_id,
                    title=f"{row.applicant_user_name or '有同事'} 申请访问{kind}「{row.dataset_name or row.dataset_id}」"[:200],
                    content=(row.reason or "对方未填写理由。"),
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
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("访问申请通知发送失败: %s", exc)

    async def _notify_decided(self, row: KnowledgeAccessRequest) -> None:
        try:
            from app.services.portal_notification_service import PortalNotificationService

            approved = row.status == STATUS_APPROVED
            await PortalNotificationService.create(
                self.db,
                user_id=int(row.applicant_user_id),
                title=(
                    f"你申请访问「{row.dataset_name or row.dataset_id}」已通过"
                    if approved
                    else f"你申请访问「{row.dataset_name or row.dataset_id}」被驳回"
                )[:200],
                content=(row.review_comment or ("权限已生效，现在可以打开查看。" if approved else "管理员未填写驳回原因。")),
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
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("访问申请结果通知发送失败: %s", exc)

    # ---------- 列表补资源信息 ----------

    async def _enrich(self, rows: Sequence[KnowledgeAccessRequest]) -> List[Dict[str, Any]]:
        kb_ids = [r.dataset_id for r in rows if r.resource_type == RESOURCE_TYPE_KNOWLEDGE]
        ds_ids = []
        for r in rows:
            if r.resource_type == RESOURCE_TYPE_METADATA:
                try:
                    ds_ids.append(int(r.dataset_id))
                except (TypeError, ValueError):
                    continue

        kb_map: Dict[str, KnowledgeBaseMetadata] = {}
        if kb_ids:
            kb_rows = (await self.db.execute(
                select(KnowledgeBaseMetadata).where(
                    KnowledgeBaseMetadata.ragflow_dataset_id.in_(kb_ids)
                )
            )).scalars().all()
            kb_map = {r.ragflow_dataset_id: r for r in kb_rows}

        ds_map: Dict[int, MetaDataset] = {}
        table_counts: Dict[int, int] = {}
        metric_counts: Dict[int, int] = {}
        if ds_ids:
            ds_rows = (await self.db.execute(
                select(MetaDataset).where(MetaDataset.id.in_(ds_ids))
            )).scalars().all()
            ds_map = {int(r.id): r for r in ds_rows}
            table_counts = dict((await self.db.execute(
                select(MetaTable.dataset_id, func.count()).where(MetaTable.dataset_id.in_(ds_ids)).group_by(MetaTable.dataset_id)
            )).all())
            metric_counts = dict((await self.db.execute(
                select(MetaMetric.dataset_id, func.count()).where(MetaMetric.dataset_id.in_(ds_ids)).group_by(MetaMetric.dataset_id)
            )).all())

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
                item.update({
                    "resource_exists": bool(meta) and (meta.status or "active") != "deleted",
                    "resource_enabled": bool(meta) and (meta.status or "active") != "deleted",
                    "visibility": getattr(meta, "visibility", None),
                    "owner": getattr(meta, "owner", None),
                })
            else:
                dataset = ds_map.get(int(row.dataset_id)) if str(row.dataset_id).isdigit() else None
                item.update({
                    "resource_exists": dataset is not None,
                    "resource_enabled": bool(dataset) and int(dataset.status or 0) == 1,
                    "data_source": getattr(dataset, "data_source", None),
                    "table_count": table_counts.get(int(row.dataset_id), 0) if dataset else 0,
                    "metric_count": metric_counts.get(int(row.dataset_id), 0) if dataset else 0,
                    "quality_score": getattr(dataset, "quality_score", None),
                })
            items.append(item)
        return items
```

- [ ] **Step 4: 旧服务改为委托**

`app/services/knowledge_access_request_service.py` 整体替换为：

```python
"""知识库访问申请的历史入口，内部委托 ``ResourceAccessRequestService``。

保留类名与 ``AccessRequestError`` 的导出，旧调用方（ragflow 端点）不用改。
"""

from app.services.resource_access_request_service import (  # noqa: F401
    AccessRequestError,
    ResourceAccessRequestService,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
)


class KnowledgeAccessRequestService(ResourceAccessRequestService):
    """兼容包装：知识库场景固定 ``resource_type='dataset'``。"""

    async def create_knowledge(
        self,
        *,
        dataset_id: str,
        dataset_name,
        user,
        reason=None,
        dept_id=None,
    ):
        return await self.create(
            resource_type="dataset",
            resource_id=dataset_id,
            resource_name=dataset_name,
            user=user,
            reason=reason,
            dept_id=dept_id,
        )
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/api/test_resource_access_request_service.py -q`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add app/services/resource_access_request_service.py app/services/knowledge_access_request_service.py tests/api/test_resource_access_request_service.py
git commit -m "feat(service): 知识库与数据集统一申请审批服务"
```

---

### Task 5: 统一申请与审批接口

**Files:**
- Create: `app/api/portal/endpoints/access_requests.py`
- Modify: `app/api/portal/api.py:94` 附近（注册 router）
- Test: `tests/api/portal/test_access_requests_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/portal/test_access_requests_api.py
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.metadata import MetaDataset
from app.services.auth_service import AuthService


@pytest.fixture
async def keys(db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = await AuthService.generate_api_key(f"rs_admin_{suffix}", role="admin", db=db_session)
    user = await AuthService.generate_api_key(f"rs_user_{suffix}", role="user", db=db_session)
    return admin, user


@pytest.mark.asyncio
async def test_create_and_approve_metadata_request(db_session, keys):
    admin_key, user_key = keys
    dataset = MetaDataset(name=f"rs_api_{uuid.uuid4().hex[:8]}", display_name="接口测试集", status=1)
    db_session.add(dataset)
    await db_session.commit()
    await db_session.refresh(dataset)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/portal/access-requests",
            json={"resource_type": "metadata", "resource_id": str(dataset.id), "reason": "测试"},
            headers={"X-API-Key": user_key},
        )
        assert created.status_code == 200, created.text
        request_id = created.json()["data"]["id"]

        mine = await client.get("/api/portal/access-requests/mine", headers={"X-API-Key": user_key})
        assert mine.status_code == 200
        assert mine.json()["data"]["items"][0]["resource_type"] == "metadata"

        pending = await client.get("/api/portal/access-requests/pending", headers={"X-API-Key": admin_key})
        assert pending.status_code == 200
        assert any(i["id"] == request_id for i in pending.json()["data"]["items"])

        approved = await client.post(
            f"/api/portal/access-requests/{request_id}/approve",
            json={"comment": "ok"},
            headers={"X-API-Key": admin_key},
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_non_admin_cannot_list_pending(db_session, keys):
    _admin_key, user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/portal/access-requests/pending", headers={"X-API-Key": user_key})
        assert resp.status_code == 403
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/portal/test_access_requests_api.py -q`
Expected: FAIL，404（路由不存在）

- [ ] **Step 3: 实现端点**

```python
"""资源访问申请与统一审批（知识库 + 数据集）。"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_admin
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services.resource_access_request_service import (
    AccessRequestError,
    ResourceAccessRequestService,
)

router = APIRouter()


class CreateAccessRequest(BaseModel):
    resource_type: str = Field(..., description="dataset=知识库 / metadata=数据集")
    resource_id: str
    reason: Optional[str] = None


class DecideRequest(BaseModel):
    comment: Optional[str] = None


def _service(db: AsyncSession) -> ResourceAccessRequestService:
    return ResourceAccessRequestService(db)


@router.post("", response_model=StandardResponse[Dict[str, Any]], summary="提交资源访问申请")
async def create_access_request(
    payload: CreateAccessRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).create(
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            resource_name=None,
            user=user,
            reason=payload.reason,
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.get("/mine", response_model=StandardResponse[Dict[str, Any]], summary="我的资源申请")
async def list_my_access_requests(
    resource_type: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    items = await _service(db).list_mine(int(user["user_id"]), resource_type=resource_type)
    return StandardResponse(data={"items": items, "total": len(items)})


@router.get("/pending", response_model=StandardResponse[Dict[str, Any]], summary="待审批（管理员）")
async def list_pending_access_requests(
    resource_type: Optional[str] = Query(default=None),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    items = await _service(db).list_pending(resource_type=resource_type)
    return StandardResponse(data={"items": items, "total": len(items)})


@router.get("/reviews", response_model=StandardResponse[Dict[str, Any]], summary="已处理申请（管理员）")
async def list_reviewed_access_requests(
    resource_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    items = await _service(db).list_reviews(resource_type=resource_type, limit=limit)
    return StandardResponse(data={"items": items, "total": len(items)})


@router.post("/{request_id}/approve", response_model=StandardResponse[Dict[str, Any]], summary="通过申请")
async def approve_access_request(
    request_id: int,
    payload: DecideRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).decide(
            request_id=request_id, reviewer=admin, approve=True, comment=payload.comment
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.post("/{request_id}/reject", response_model=StandardResponse[Dict[str, Any]], summary="驳回申请")
async def reject_access_request(
    request_id: int,
    payload: DecideRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).decide(
            request_id=request_id, reviewer=admin, approve=False, comment=payload.comment
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.post("/{request_id}/cancel", response_model=StandardResponse[Dict[str, Any]], summary="撤回申请")
async def cancel_access_request(
    request_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).cancel(request_id, user)
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})
```

注册（`app/api/portal/api.py`）：在 import 行加入 `access_requests`，并在 workbench 注册行之后追加：

```python
portal_router.include_router(
    access_requests.router,
    prefix="/access-requests",
    tags=["资源访问申请"],
    dependencies=[Depends(require_api_key)],
)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/portal/test_access_requests_api.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/portal/endpoints/access_requests.py app/api/portal/api.py tests/api/portal/test_access_requests_api.py
git commit -m "feat(api): 资源访问申请与统一审批接口"
```

---

### Task 6: 资源广场查询接口

**Files:**
- Create: `app/api/portal/endpoints/resource_square.py`
- Modify: `app/api/portal/api.py`（注册）
- Test: `tests/api/portal/test_resource_square_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/portal/test_resource_square_api.py
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.metadata import MetaDataset
from app.services.auth_service import AuthService


@pytest.fixture
async def user_key(db_session):
    return await AuthService.generate_api_key(f"rs_sq_{uuid.uuid4().hex[:8]}", role="user", db=db_session)


@pytest.mark.asyncio
async def test_knowledge_square_lists_public_and_team_only(db_session, user_key):
    suffix = uuid.uuid4().hex[:8]
    db_session.add_all([
        KnowledgeBaseMetadata(ragflow_dataset_id=f"pub_{suffix}", name=f"公共{suffix}", visibility="public", status="active"),
        KnowledgeBaseMetadata(ragflow_dataset_id=f"team_{suffix}", name=f"部门{suffix}", visibility="team", status="active"),
        KnowledgeBaseMetadata(ragflow_dataset_id=f"priv_{suffix}", name=f"私有{suffix}", visibility="private", status="active"),
    ])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/resource-square/knowledge",
            params={"keyword": suffix, "size": 50},
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 200, resp.text
        names = [i["name"] for i in resp.json()["data"]["items"]]
        assert f"公共{suffix}" in names
        assert f"部门{suffix}" in names
        assert f"私有{suffix}" not in names


@pytest.mark.asyncio
async def test_dataset_square_lists_enabled_only(db_session, user_key):
    suffix = uuid.uuid4().hex[:8]
    db_session.add_all([
        MetaDataset(name=f"on_{suffix}", display_name="启用", status=1),
        MetaDataset(name=f"off_{suffix}", display_name="停用", status=0),
    ])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/resource-square/datasets",
            params={"keyword": suffix},
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["enabled"] is True
        names = [i["name"] for i in data["items"]]
        assert f"on_{suffix}" in names
        assert f"off_{suffix}" not in names
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/portal/test_resource_square_api.py -q`
Expected: FAIL，404

- [ ] **Step 3: 实现查询**

```python
"""资源广场：知识库与数据集的目录查询、只读概览。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.orm import get_db_session
from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset, MetaMetric, MetaRelationship, MetaTable
from app.models.permission import ResourcePermission, UserRoleRelation
from app.schemas.response import StandardResponse
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)
router = APIRouter()

INTERNAL_PREFIXES = ("meta-", "chatbi-example-meta")


def _is_internal_dataset_name(name: str) -> bool:
    from app.services.knowledge_dataset_filters import is_internal_dataset_name

    return is_internal_dataset_name(name)


async def _show_datasets_enabled() -> bool:
    try:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("resource_square_show_datasets", "true")
        return str(raw).strip().lower() not in {"false", "0", "no", "off"}
    except Exception:
        return True


async def _my_request_map(db: AsyncSession, user_id: int, resource_type: str, resource_ids: List[str]):
    if not resource_ids:
        return {}
    rows = (await db.execute(
        select(KnowledgeAccessRequest)
        .where(
            KnowledgeAccessRequest.applicant_user_id == user_id,
            KnowledgeAccessRequest.resource_type == resource_type,
            KnowledgeAccessRequest.dataset_id.in_(resource_ids),
        )
        .order_by(KnowledgeAccessRequest.created_at.desc(), KnowledgeAccessRequest.id.desc())
    )).scalars().all()
    latest: Dict[str, KnowledgeAccessRequest] = {}
    for row in rows:
        latest.setdefault(row.dataset_id, row)
    return latest


async def _knowledge_doc_counts(dataset_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not dataset_ids:
        return {}
    try:
        from app.services.ragflow_client import RagFlowClient

        items = await RagFlowClient(config_prefix="knowledge_ragflow").list_all_datasets_cached()
        wanted = set(dataset_ids)
        return {
            str(item.get("id")): {
                "doc_count": _safe_int(item.get("document_count")),
                "chunk_count": _safe_int(item.get("chunk_count")),
                "engine_update_time": item.get("update_time"),
            }
            for item in items
            if str(item.get("id")) in wanted
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ResourceSquare] 读取知识库引擎统计失败: %s", exc)
        return {}


def _safe_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.get("/knowledge", response_model=StandardResponse[Dict[str, Any]], summary="资源广场·知识库")
async def list_square_knowledge(
    keyword: Optional[str] = None,
    visibility: str = Query(default="all", pattern="^(all|public|team)$"),
    status: str = Query(default="all", pattern="^(all|apply|pending|has)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=12, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user["user_id"])
    is_admin = str(user.get("role") or "") == "admin"
    access = await PermissionService(db).get_knowledge_base_access(user_id, user.get("user_name"))
    accessible = set(access.get("accessible_ids") or set())

    rows = (await db.execute(
        select(KnowledgeBaseMetadata)
        .where(KnowledgeBaseMetadata.visibility.in_(["public", "team"]))
        .where(KnowledgeBaseMetadata.status != "deleted")
    )).scalars().all()

    items = []
    for row in rows:
        if _is_internal_dataset_name(row.name):
            continue
        if visibility != "all" and (row.visibility or "private") != visibility:
            continue
        has_access = is_admin or row.ragflow_dataset_id in accessible
        items.append((row, has_access))

    request_map = await _my_request_map(
        db, user_id, "dataset", [row.ragflow_dataset_id for row, _ in items]
    )
    counted = await _knowledge_doc_counts([row.ragflow_dataset_id for row, _ in items])

    kw = (keyword or "").strip().lower()
    enriched = []
    for row, has_access in items:
        if kw and not (
            kw in (row.name or "").lower()
            or kw in (row.description or "").lower()
            or kw in (row.owner or "").lower()
            or kw in " ".join(str(t) for t in (row.tags or [])).lower()
        ):
            continue
        latest = request_map.get(row.ragflow_dataset_id)
        req_status = latest.status if latest else None
        if status == "apply" and (has_access or req_status == "pending"):
            continue
        if status == "pending" and req_status != "pending":
            continue
        if status == "has" and not has_access:
            continue
        stats = counted.get(row.ragflow_dataset_id, {})
        enriched.append({
            "dataset_id": row.ragflow_dataset_id,
            "name": row.name,
            "description": row.description,
            "visibility": row.visibility,
            "owner": row.owner,
            "owner_dept_id": row.owner_dept_id,
            "tags": list(row.tags or [])[:6],
            "doc_count": stats.get("doc_count"),
            "chunk_count": stats.get("chunk_count"),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "has_access": has_access,
            "request_status": req_status,
            "request_id": latest.id if latest else None,
        })

    total = len(enriched)
    start = (page - 1) * size
    window = enriched[start:start + size]
    return StandardResponse(data={
        "items": window,
        "total": total,
        "page": page,
        "size": size,
        "stale": not counted,
        "summary": {
            "apply": sum(1 for i in enriched if not i["has_access"] and i["request_status"] != "pending"),
            "pending": sum(1 for i in enriched if i["request_status"] == "pending"),
            "has": sum(1 for i in enriched if i["has_access"]),
        },
    })


@router.get("/datasets", response_model=StandardResponse[Dict[str, Any]], summary="资源广场·数据集")
async def list_square_datasets(
    keyword: Optional[str] = None,
    status: str = Query(default="all", pattern="^(all|apply|pending|has)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=12, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user["user_id"])
    is_admin = str(user.get("role") or "") == "admin"
    enabled = await _show_datasets_enabled()
    if not enabled:
        return StandardResponse(data={
            "items": [], "total": 0, "page": page, "size": size,
            "enabled": False, "summary": {"apply": 0, "pending": 0, "has": 0},
        })

    stmt = select(MetaDataset).where(MetaDataset.status == 1).order_by(MetaDataset.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())

    permitted_ids = set()
    if not is_admin:
        role_ids = (await db.execute(
            select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id)
        )).scalars().all()
        perm_stmt = select(ResourcePermission.resource_id).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.enabled.is_(True),
        )
        perm_condition = ResourcePermission.user_id == user_id
        if role_ids:
            perm_condition = perm_condition | ResourcePermission.role_id.in_(role_ids)
        permitted_ids = {
            str(i) for i in (await db.execute(perm_stmt.where(perm_condition))).scalars().all()
        }

    request_map = await _my_request_map(db, user_id, "metadata", [str(r.id) for r in rows])
    ids = [int(r.id) for r in rows]
    table_counts = dict((await db.execute(
        select(MetaTable.dataset_id, func.count()).where(MetaTable.dataset_id.in_(ids)).group_by(MetaTable.dataset_id)
    )).all()) if ids else {}
    metric_counts = dict((await db.execute(
        select(MetaMetric.dataset_id, func.count()).where(MetaMetric.dataset_id.in_(ids)).group_by(MetaMetric.dataset_id)
    )).all()) if ids else {}

    kw = (keyword or "").strip().lower()
    enriched = []
    for row in rows:
        if _is_internal_dataset_name(row.name):
            continue
        has_access = is_admin or str(row.id) in permitted_ids
        latest = request_map.get(str(row.id))
        req_status = latest.status if latest else None
        if kw and not (
            kw in (row.name or "").lower()
            or kw in (row.display_name or "").lower()
            or kw in (row.description or "").lower()
            or kw in " ".join(str(t) for t in (row.tags or [])).lower()
        ):
            continue
        if status == "apply" and (has_access or req_status == "pending"):
            continue
        if status == "pending" and req_status != "pending":
            continue
        if status == "has" and not has_access:
            continue
        enriched.append({
            "id": row.id,
            "name": row.name,
            "display_name": row.display_name or row.name,
            "description": row.description,
            "data_source": row.data_source,
            "tags": list(row.tags or [])[:6],
            "quality_score": row.quality_score,
            "table_count": int(table_counts.get(row.id, 0)),
            "metric_count": int(metric_counts.get(row.id, 0)),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "has_access": has_access,
            "request_status": req_status,
            "request_id": latest.id if latest else None,
        })

    total = len(enriched)
    start = (page - 1) * size
    return StandardResponse(data={
        "items": enriched[start:start + size],
        "total": total,
        "page": page,
        "size": size,
        "enabled": True,
        "summary": {
            "apply": sum(1 for i in enriched if not i["has_access"] and i["request_status"] != "pending"),
            "pending": sum(1 for i in enriched if i["request_status"] == "pending"),
            "has": sum(1 for i in enriched if i["has_access"]),
        },
    })
```

> `_is_internal_dataset_name` 若仓库已有同名工具（`app/utils/dataset_names.py`），直接 import；没有则在 `resource_square.py` 内用前缀判断实现，并在 Task 9 统一到工具函数。

注册（`app/api/portal/api.py`）：

```python
portal_router.include_router(
    resource_square.router,
    prefix="/resource-square",
    tags=["资源广场"],
    dependencies=[Depends(require_api_key)],
)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/portal/test_resource_square_api.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/portal/endpoints/resource_square.py app/api/portal/api.py tests/api/portal/test_resource_square_api.py
git commit -m "feat(api): 资源广场知识库与数据集目录查询"
```

---

### Task 7: 只读概览接口

**Files:**
- Modify: `app/api/portal/endpoints/resource_square.py`（追加两个路由）
- Test: `tests/api/portal/test_resource_square_readonly.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/portal/test_resource_square_readonly.py
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.metadata import MetaColumn, MetaDataset, MetaMetric, MetaTable
from app.services.auth_service import AuthService


@pytest.fixture
async def dataset_with_schema(db_session):
    ds = MetaDataset(name=f"ro_{uuid.uuid4().hex[:8]}", display_name="只读测试集", status=1, quality_score=88)
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    table = MetaTable(dataset_id=ds.id, physical_name="dwd_order", term="订单", description="订单主表")
    db_session.add(table)
    await db_session.commit()
    await db_session.refresh(table)
    db_session.add(MetaColumn(table_id=table.id, physical_name="order_id", term="订单号", examples=["A1"]))
    db_session.add(MetaMetric(dataset_id=ds.id, name="gmv", display_name="GMV", unit="元"))
    await db_session.commit()
    return ds


@pytest.mark.asyncio
async def test_readonly_requires_permission(db_session, dataset_with_schema):
    key = await AuthService.generate_api_key(f"ro_u_{uuid.uuid4().hex[:8]}", role="user", db=db_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/portal/resource-square/datasets/{dataset_with_schema.id}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_readonly_hides_columns_and_examples(db_session, dataset_with_schema):
    admin_key = await AuthService.generate_api_key(f"ro_a_{uuid.uuid4().hex[:8]}", role="admin", db=db_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/portal/resource-square/datasets/{dataset_with_schema.id}",
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["display_name"] == "只读测试集"
        assert data["tables"][0]["physical_name"] == "dwd_order"
        assert "columns" not in data["tables"][0]
        assert "examples" not in str(data)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/portal/test_resource_square_readonly.py -q`
Expected: FAIL，404

- [ ] **Step 3: 实现只读端点**

在 `resource_square.py` 追加：

```python
async def _require_metadata_access(db: AsyncSession, user: dict, dataset_id: int) -> MetaDataset:
    dataset = await db.get(MetaDataset, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if str(user.get("role") or "") == "admin":
        return dataset
    user_id = int(user["user_id"])
    role_ids = (await db.execute(
        select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id)
    )).scalars().all()
    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id),
        ResourcePermission.enabled.is_(True),
    )
    condition = ResourcePermission.user_id == user_id
    if role_ids:
        condition = condition | ResourcePermission.role_id.in_(role_ids)
    stmt = stmt.where(condition)
    if not (await db.execute(stmt.limit(1))).scalars().first():
        raise HTTPException(status_code=403, detail="你还没有这个数据集的访问权限")
    return dataset


@router.get("/datasets/{dataset_id}", response_model=StandardResponse[Dict[str, Any]], summary="数据集只读概览")
async def get_dataset_readonly(
    dataset_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    dataset = await _require_metadata_access(db, user, dataset_id)

    tables = (await db.execute(
        select(MetaTable).where(MetaTable.dataset_id == dataset_id).order_by(MetaTable.id.asc())
    )).scalars().all()
    table_ids = [int(t.id) for t in tables]
    field_counts = dict((await db.execute(
        select(MetaColumn.table_id, func.count()).where(MetaColumn.table_id.in_(table_ids)).group_by(MetaColumn.table_id)
    )).all()) if table_ids else {}

    metrics = (await db.execute(
        select(MetaMetric).where(MetaMetric.dataset_id == dataset_id).order_by(MetaMetric.id.asc())
    )).scalars().all()
    relationship_count = 0
    if table_ids:
        relationship_count = int((await db.execute(
            select(func.count()).select_from(MetaRelationship).where(
                or_(MetaRelationship.source_table_id.in_(table_ids),
                    MetaRelationship.target_table_id.in_(table_ids))
            )
        )).scalar() or 0)

    return StandardResponse(data={
        "id": dataset.id,
        "name": dataset.name,
        "display_name": dataset.display_name or dataset.name,
        "description": dataset.description,
        "data_source": dataset.data_source,
        "tags": list(dataset.tags or []),
        "quality_score": dataset.quality_score,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
        "relationship_count": relationship_count,
        "tables": [
            {
                "physical_name": t.physical_name,
                "term": t.term,
                "field_count": int(field_counts.get(t.id, 0)),
                "description": t.description,
            }
            for t in tables
        ],
        "metrics": [
            {
                "name": m.name,
                "display_name": m.display_name,
                "description": m.description,
                "unit": m.unit,
            }
            for m in metrics
        ],
    })


@router.get("/knowledge/{dataset_id}", response_model=StandardResponse[Dict[str, Any]], summary="知识库只读概览")
async def get_knowledge_readonly(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    meta = (await db.execute(
        select(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.ragflow_dataset_id == dataset_id).limit(1)
    )).scalars().first()
    if not meta or (meta.status or "active") == "deleted":
        raise HTTPException(status_code=404, detail="知识库不存在")

    is_admin = str(user.get("role") or "") == "admin"
    access = await PermissionService(db).get_knowledge_base_access(int(user["user_id"]), user.get("user_name"))
    if not is_admin and dataset_id not in (access.get("accessible_ids") or set()):
        raise HTTPException(status_code=403, detail="你还没有这个知识库的访问权限")

    from app.services.ragflow_client import RagFlowClient

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    documents = []
    try:
        docs = await client.list_documents(dataset_id, page=page, page_size=size)
        for doc in docs or []:
            documents.append({
                "id": doc.get("id"),
                "name": doc.get("name"),
                "file_type": doc.get("type") or doc.get("file_type"),
                "size": _safe_int(doc.get("size")),
                "chunk_count": _safe_int(doc.get("chunk_count")),
                "run_status": doc.get("run"),
                "updated_at": doc.get("update_time"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ResourceSquare] 读取知识库文档列表失败: %s", exc)

    return StandardResponse(data={
        "dataset_id": dataset_id,
        "name": meta.name,
        "description": meta.description,
        "visibility": meta.visibility,
        "owner": meta.owner,
        "tags": list(meta.tags or []),
        "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
        "documents": documents,
        "page": page,
        "size": size,
    })
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/portal/test_resource_square_readonly.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/portal/endpoints/resource_square.py tests/api/portal/test_resource_square_readonly.py
git commit -m "feat(api): 知识库与数据集只读概览接口"
```

---

### Task 8: 工作台两个资源卡

**Files:**
- Modify: `app/services/workbench_home_service.py`（`PERSONAL_RESOURCE_DEFS`、`_load_personal_resources`、新增两个计数函数）
- Test: `tests/api/test_workbench_resource_cards.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_workbench_resource_cards.py
from app.services.workbench_home_service import PERSONAL_RESOURCE_DEFS


def test_personal_resource_defs_start_with_knowledge_and_datasets():
    keys = [spec["key"] for spec in PERSONAL_RESOURCE_DEFS]
    assert keys[:2] == ["knowledge", "datasets"]
    assert len(keys) == 9
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_workbench_resource_cards.py -q`
Expected: FAIL（keys 前两位是 memory/tokens）

- [ ] **Step 3: 实现计数**

`PERSONAL_RESOURCE_DEFS` 改为（两项放最前）：

```python
PERSONAL_RESOURCE_DEFS = (
    {"key": "knowledge", "label": "我的知识库", "unit": "个", "tab": "square"},
    {"key": "datasets", "label": "我的数据集", "unit": "个", "tab": "square"},
    {"key": "memory", "label": "我的记忆", "unit": "条", "tab": "memory"},
    {"key": "tokens", "label": "我的 Token", "unit": "本月", "tab": "tokens"},
    {"key": "data", "label": "我的数据门户", "unit": "份报表", "tab": "data"},
    {"key": "skills", "label": "我的技能", "unit": "个", "tab": "skills"},
    {"key": "mcp", "label": "我的 MCP", "unit": "个服务", "tab": "mcp"},
    {"key": "tasks", "label": "我的任务", "unit": "个", "tab": "tasks"},
    {"key": "inbox", "label": "我的站内消息", "unit": "条未读", "tab": "inbox"},
)
```

新增两个计数函数（放在 `_count_inbox_unread` 附近）：

```python
async def _count_accessible_knowledge_bases(db: AsyncSession, user: Mapping[str, Any]) -> int:
    from app.models.knowledge import KnowledgeBaseMetadata
    from app.services.permission_service import PermissionService

    user_id = int(user["user_id"])
    if str(user.get("role") or "") == "admin":
        total = (await db.execute(
            select(func.count()).select_from(KnowledgeBaseMetadata).where(
                KnowledgeBaseMetadata.status != "deleted"
            )
        )).scalar()
        return int(total or 0)

    access = await PermissionService(db).get_knowledge_base_access(user_id, user.get("user_name"))
    return len(access.get("accessible_ids") or set())


async def _count_accessible_metadata_datasets(db: AsyncSession, user: Mapping[str, Any]) -> int:
    from app.services.metadata_service import MetadataService

    rows = await MetadataService.list_accessible_dataset_options(
        db,
        user_id=int(user["user_id"]),
        is_admin=str(user.get("role") or "") == "admin",
        status=1,
    )
    return len(rows)
```

`_load_personal_resources` 里在 memory 之前插入：

```python
    await _put("knowledge", lambda: _count_accessible_knowledge_bases(db, user))
    await _put("datasets", lambda: _count_accessible_metadata_datasets(db, user))
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_workbench_resource_cards.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/workbench_home_service.py tests/api/test_workbench_resource_cards.py
git commit -m "feat(workbench): 资源条新增我的知识库与数据集"
```

---

### Task 9: 配置开关与旧接口兼容

**Files:**
- Modify: `app/services/config_service.py`（新增 key 常量与校验）
- Modify: `app/api/portal/endpoints/ragflow.py`（`discover_datasets` 复用广场查询）
- Test: `tests/api/test_resource_square_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_resource_square_config.py
import pytest

from app.services.config_service import validate_config_update


def test_show_datasets_switch_validation():
    validate_config_update("resource_square_show_datasets", "true")
    validate_config_update("resource_square_show_datasets", "false")
    with pytest.raises(ValueError):
        validate_config_update("resource_square_show_datasets", "maybe")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_resource_square_config.py -q`
Expected: FAIL（非法值未抛错）

- [ ] **Step 3: 加配置项**

`config_service.py` 常量区：

```python
#: 资源广场是否对普通用户开放数据集目录（默认开）
RESOURCE_SQUARE_SHOW_DATASETS_KEY = "resource_square_show_datasets"
```

`validate_config_update` 中追加分支（照 `user_registration_enabled` 写法）：

```python
    elif key == RESOURCE_SQUARE_SHOW_DATASETS_KEY:
        normalized = str(value or "").strip().lower()
        if normalized not in _BOOLEAN_LITERALS:
            raise ValueError("resource_square_show_datasets 仅允许 true/false")
```

- [ ] **Step 4: 旧审批端点收口（关键）**

旧 `/ragflow/access-requests/*` 必须切到统一服务，否则「库归属人」仍能用旧接口审批，绕过「审批人=管理员」的产品决策；同时旧端点自己做的缓存失效、通知、审计会与新服务重复（审批一次发两条通知）。

`ragflow.py` 中替换 `_require_reviewer` 为：

```python
def _require_admin_reviewer(user: dict) -> None:
    """审批人统一为管理员；归属人不再具备审批权。"""
    if str(user.get("role") or "") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以审批资源访问申请")
```

`pending` / `reviews` / `mine` 改造（放在 `_legacy_access_request_item` 之后）：

```python
def _legacy_access_request_item(item: dict) -> dict:
    """新服务结构 → 旧前端结构。旧字段一个不少，值缺失给 None。"""
    return {
        "id": item["id"],
        "dataset_id": item["resource_id"],
        "dataset_name": item["resource_name"],
        "applicant_user_id": item["applicant_user_id"],
        "applicant_user_name": item["applicant_user_name"],
        "applicant_dept_id": None,
        "reason": item["reason"],
        "status": item["status"],
        "reviewer_user_name": item["reviewer_user_name"],
        "review_comment": item["review_comment"],
        "created_at": item["created_at"],
        "reviewed_at": item["reviewed_at"],
        "doc_count": None,
        "chunk_count": None,
        "visibility": item.get("visibility"),
        "owner": item.get("owner"),
    }


@router.get("/access-requests/pending")
async def list_pending_access_requests(
    resource_type: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    _require_admin_reviewer(user)
    service = KnowledgeAccessRequestService(db)
    items = await service.list_pending(resource_type=resource_type or "dataset")
    return {"code": 0, "data": {
        "items": [_legacy_access_request_item(i) for i in items],
        "total": len(items),
    }}


@router.get("/access-requests/reviews")
async def list_reviewed_access_requests(
    size: int = 100,
    resource_type: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    _require_admin_reviewer(user)
    service = KnowledgeAccessRequestService(db)
    items = await service.list_reviews(resource_type=resource_type or "dataset", limit=size)
    return {"code": 0, "data": {
        "items": [_legacy_access_request_item(i) for i in items],
        "total": len(items),
    }}


@router.get("/access-requests/mine")
async def list_my_access_requests(
    resource_type: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    service = KnowledgeAccessRequestService(db)
    items = await service.list_mine(int(user["user_id"]), resource_type=resource_type or "dataset")
    return {"code": 0, "data": {
        "items": [_legacy_access_request_item(i) for i in items],
        "total": len(items),
    }}
```

`approve` / `reject` 端点改为（删掉后面的缓存失效、通知、审计——服务已做）：

```python
@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: int,
    payload: AccessReviewBody,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    _require_admin_reviewer(user)
    try:
        await KnowledgeAccessRequestService(db).decide(
            request_id=request_id, reviewer=user, approve=True, comment=payload.comment
        )
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {"code": 0, "data": {"status": "approved"}}


@router.post("/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: int,
    payload: AccessReviewBody,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    _require_admin_reviewer(user)
    try:
        await KnowledgeAccessRequestService(db).decide(
            request_id=request_id, reviewer=user, approve=False, comment=payload.comment
        )
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {"code": 0, "data": {"status": "rejected"}}
```

`cancel` 端点同样改为只调服务（删掉旧的通知与审计）。

> `discover_datasets` 保持原实现不动：它只列「未授权且含 pending」的库，与广场的「全量目录」口径不同，强行复用会让用户申请中的库从发现页消失。

- [ ] **Step 5: 补旧端点测试**

```python
# tests/api/test_legacy_access_request_compat.py
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.metadata import MetaDataset
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_owner_cannot_use_legacy_approve(db_session):
    """归属人走旧接口审批必须 403：审批权已收口到管理员。"""
    owner_key = await AuthService.generate_api_key(
        f"owner_{uuid.uuid4().hex[:8]}", role="user", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/ragflow/access-requests/pending", headers={"X-API-Key": owner_key}
        )
        assert resp.status_code == 403
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/api/test_resource_square_config.py tests/api/test_legacy_access_request_compat.py -q`
Expected: 2 passed

- [ ] **Step 7: 回归**

Run: `pytest tests/api/portal/test_permission_api.py tests/api/test_resource_access_models.py tests/api/test_resource_access_request_service.py -q`
Expected: 全部通过

- [ ] **Step 8: Commit**

```bash
git add app/services/config_service.py app/api/portal/endpoints/ragflow.py tests/api/test_resource_square_config.py tests/api/test_legacy_access_request_compat.py
git commit -m "feat(config): 数据集目录开关，发现入口复用广场查询"
```

---

## 自审清单

- [ ] `rg "TODO|TBD|待定" tests/ app/` 无新增占位
- [ ] 迁移文件与模型列名、索引名逐字一致
- [ ] `resource_type` 在服务、端点、前端三处取值都用 `dataset` / `metadata`
- [ ] 审批通过同时清 `PermissionService` 与 `AgentConfigProvider` 缓存
- [ ] 数据集审计动作码与 spec §10.3 表格一致
- [ ] 旧审批端点不再自行清缓存 / 发通知 / 写审计（否则与新服务重复）
- [ ] `pytest tests/api/test_resource_access_*.py tests/api/portal/test_access_requests_api.py tests/api/portal/test_resource_square_*.py -q` 全绿

---

## 实现偏差记录（2026-10-27 落地后回写）

计划执行过程中有五处与原文不一致，均已在代码与测试里体现，此处留档避免下次照着计划踩坑。

1. **`RagFlowClient` 路径**：计划写的是 `app.services.ragflow_client`，实际在
   `app.services.ai.ragflow_client`。广场知识库段的规模统计走
   `RagFlowClient(config_prefix="knowledge_ragflow").list_all_datasets_cached()`。
2. **通知与审计改用独立 session**：计划让 `_notify_created` / `_notify_decided` 用主
   session 的 `flush()` + 调用方 `commit()`。实现时踩到真实故障——通知插入触发外键失败后
   主 session 被污染，后续读 `decided.status` 直接抛 `PendingRollbackError`，一次通知失败
   连审批结果都读不出来。改为与审计同策略：独立 `AsyncSessionLocal()`，内部 commit，异常吞掉。
3. **旧端点兼容层只服务知识库视角**：`/ragflow/access-requests/mine|pending|reviews`
   固定传 `resource_type="dataset"`，再用 `_legacy_access_request_item()` 把统一结构映射回
   旧字段名（`dataset_id` / `dataset_name` / `dataset_exists` 等）。数据集申请不出现在旧前端列表里
   ——旧前端不认识这些字段，混进去只会渲染成 undefined。
4. **旧 `create` 端点也删掉了副作用**：计划只提到 approve/reject 要收口，实际
   `POST /ragflow/datasets/{id}/access-requests` 同样在转发后又发了一次提交通知、又写了一条
   `datasets:request_access` 审计。一并删除，并删掉因此失去引用的 `_notify_access_request` 函数。
5. **补上了配置项 Seed（并入主迁移）**：计划漏了配置项的种子数据。开关要出现在系统配置页，
   必须在 `system_configs` 里有这一行。起初单独新增 `V193` / `V93`，随后合并回
   `V192` / `V92` ——`apply_sql.py` 按目录整批执行、无版本记录表，同一个特性的 DDL 与种子
   拆两个文件只会多一个「忘了执行」的机会。重跑已执行过的库时 4 条语句全部安全跳过
   （1060 / 1061 / 表已存在 / `INSERT IGNORE` 影响 0 行），幂等已验证。

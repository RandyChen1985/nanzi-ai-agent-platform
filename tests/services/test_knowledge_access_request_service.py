"""知识库访问申请的兼容入口：签名映射 + 申请/审批/撤回的核心规则。

``KnowledgeAccessRequestService`` 只是 ``ResourceAccessRequestService`` 的旧签名
包装（``dataset_id``/``dataset_name``），业务规则全部在统一服务里，所以这里用
替身库（不连真库）验证规则与映射本身；端到端的落库/授权链路由
``tests/api/test_resource_access_request_service.py`` 覆盖。

替身库按调用顺序供给查询结果，因此每条用例都要把「服务会发起的查询」按顺序
列出来：知识库存在性 → 权限（已打桩）→ 重复申请 → 部门（返回空）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.permission import ResourcePermission
from app.services.knowledge_access_request_service import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessRequestError,
    KnowledgeAccessRequestService,
)

pytestmark = pytest.mark.no_infrastructure

#: 管理员身份：审批要求 role=admin（普通用户看不到审批入口）
ADMIN = {"user_id": 1, "user_name": "alice", "role": "admin"}
APPLICANT = {"user_id": 9, "user_name": "bob"}


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None


class _DB:
    """按顺序返回预置查询结果的替身 session。"""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        return _Result(self.results.pop(0) if self.results else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None


def _meta(**kwargs):
    """知识库元数据（visibility=public 才接受申请）。"""
    base = {
        "ragflow_dataset_id": "ds-1",
        "name": "制度库",
        "visibility": "public",
        "status": "active",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _request(**kwargs):
    base = {
        "id": 1,
        "resource_type": "dataset",
        "dataset_id": "ds-1",
        "dataset_name": "制度库",
        "applicant_user_id": 9,
        "applicant_user_name": "bob",
        "applicant_dept_id": None,
        "reason": None,
        "status": STATUS_PENDING,
        "reviewer_user_id": None,
        "reviewer_user_name": None,
        "review_comment": None,
        "reviewed_at": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _stub_side_effects():
    """把旁路副作用（审计/通知/缓存失效/权限判定）打桩。

    这些旁路要么写别的表、要么另开 session（通知用独立 ``AsyncSessionLocal``），
    不属于本文件要验证的规则；尤其在 ``no_infrastructure`` 用例里不能让它们
    真去连库。
    """
    with patch.object(KnowledgeAccessRequestService, "_audit", new=AsyncMock()), patch.object(
        KnowledgeAccessRequestService, "_notify_created", new=AsyncMock()
    ), patch.object(KnowledgeAccessRequestService, "_notify_decided", new=AsyncMock()), patch.object(
        KnowledgeAccessRequestService, "_invalidate", new=AsyncMock()
    ), patch(
        "app.services.permission_service.PermissionService.get_knowledge_base_access",
        new=AsyncMock(return_value={"accessible_ids": set()}),
    ):
        yield


# --------------------------------------------------------------------------- #
# 申请
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_pending_request_is_rejected():
    db = _DB(results=[[_meta()], [_request()]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).create(
            dataset_id="ds-1",
            dataset_name="制度库",
            user=APPLICANT,
        )

    assert "等待审批" in str(exc.value)
    assert db.added == []


@pytest.mark.asyncio
async def test_create_stores_snapshot_and_pending_status():
    db = _DB(results=[[_meta()], []])

    row = await KnowledgeAccessRequestService(db).create(
        dataset_id="ds-1",
        dataset_name=None,  # 名称由服务端按知识库元数据回填
        user=APPLICANT,
        reason="  季度对账要用  ",
    )

    assert row.status == STATUS_PENDING
    assert row.resource_type == "dataset"
    assert row.applicant_user_id == 9
    assert row.dataset_name == "制度库"
    assert row.reason == "季度对账要用"
    # 兼容层曾把「库归属部门」写进申请人部门列；现在统一由服务端解析申请人部门，
    # 查不到就留空，不能因为部门异常拒掉申请。
    assert row.applicant_dept_id is None
    assert db.added == [row]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_private_knowledge_base_rejects_application():
    db = _DB(results=[[_meta(visibility="private")]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).create(
            dataset_id="ds-1",
            dataset_name="制度库",
            user=APPLICANT,
        )

    assert "个人私有" in str(exc.value)
    assert db.added == []


@pytest.mark.asyncio
async def test_deleted_knowledge_base_rejects_application():
    db = _DB(results=[[_meta(status="deleted")]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).create(
            dataset_id="ds-1",
            dataset_name="制度库",
            user=APPLICANT,
        )

    assert "不存在或已被删除" in str(exc.value)


@pytest.mark.asyncio
async def test_admin_does_not_need_to_apply():
    db = _DB()

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).create(
            dataset_id="ds-1",
            dataset_name="制度库",
            user=ADMIN,
        )

    assert "无需申请" in str(exc.value)
    assert db.results == []  # 管理员在查库之前就被拦下


# --------------------------------------------------------------------------- #
# 审批
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_approve_grants_resource_permission():
    row = _request()
    db = _DB(results=[[row], [_meta()], []])

    result = await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer=ADMIN,
        approve=True,
        comment="同意",
    )

    assert result.status == STATUS_APPROVED
    assert result.reviewer_user_name == "alice"
    assert result.review_comment == "同意"
    granted = [item for item in db.added if isinstance(item, ResourcePermission)]
    assert len(granted) == 1
    assert granted[0].user_id == 9
    assert granted[0].resource_type == "dataset"
    assert granted[0].resource_id == "ds-1"
    assert granted[0].enabled is True


@pytest.mark.asyncio
async def test_approve_reenables_existing_permission_instead_of_duplicating():
    row = _request()
    existing = SimpleNamespace(user_id=9, resource_type="dataset", resource_id="ds-1", enabled=False)
    db = _DB(results=[[row], [_meta()], [existing]])

    result = await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer=ADMIN,
        approve=True,
    )

    assert result.status == STATUS_APPROVED
    assert existing.enabled is True
    assert [item for item in db.added if isinstance(item, ResourcePermission)] == []


@pytest.mark.asyncio
async def test_approve_rejects_knowledge_base_turned_private():
    """申请挂起期间库被改成私有：批了也打不开，必须在审批时就挡住。"""
    row = _request()
    db = _DB(results=[[row], [_meta(visibility="private")]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1, reviewer=ADMIN, approve=True
        )

    assert "个人私有" in str(exc.value)
    assert row.status == STATUS_PENDING
    assert db.commits == 0


@pytest.mark.asyncio
async def test_reject_does_not_grant_anything():
    row = _request()
    db = _DB(results=[[row]])

    result = await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer=ADMIN,
        approve=False,
        comment="申请理由不足",
    )

    assert result.status == STATUS_REJECTED
    assert result.review_comment == "申请理由不足"
    assert [item for item in db.added if isinstance(item, ResourcePermission)] == []
    assert db.commits == 1


@pytest.mark.asyncio
async def test_only_admin_can_review():
    db = _DB()

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1,
            reviewer={"user_id": 1, "user_name": "alice"},  # 没有 role=admin
            approve=True,
        )

    assert "只有管理员" in str(exc.value)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_cannot_review_own_request():
    row = _request(applicant_user_id=1, applicant_user_name="alice")
    db = _DB(results=[[row]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1,
            reviewer=ADMIN,
            approve=True,
        )

    assert "不能审批自己" in str(exc.value)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_already_decided_request_is_rejected():
    row = _request(status=STATUS_APPROVED)
    db = _DB(results=[[row]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1,
            reviewer=ADMIN,
            approve=False,
        )

    assert "已经处理过" in str(exc.value)
    assert db.commits == 0


# --------------------------------------------------------------------------- #
# 撤回
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel_only_by_applicant_and_only_when_pending():
    row = _request()
    db = _DB(results=[[row]])
    service = KnowledgeAccessRequestService(db)

    with pytest.raises(AccessRequestError) as exc:
        await service.cancel(1, {"user_id": 999})
    assert "只能撤回自己" in str(exc.value)
    assert row.status == STATUS_PENDING

    db.results = [[row]]
    cancelled = await service.cancel(1, APPLICANT)
    assert cancelled.status == STATUS_CANCELLED
    assert db.commits == 1

    # 已撤回的申请不能再撤回：再次取出同一行时状态已经是 cancelled
    db.results = [[row]]
    with pytest.raises(AccessRequestError) as exc:
        await service.cancel(1, APPLICANT)
    assert "已经处理过" in str(exc.value)

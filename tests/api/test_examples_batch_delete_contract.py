"""案例集管理：废弃后可删除 + 各 Tab 批量操作的后端契约。

钉住三件事：
1. 「删除」只对 `deprecated` 开放，别的状态必须被拒（前端也按同一口径隐藏按钮）；
2. 批量接口一次校验 action 对应的功能点权限码，与单条接口共用一套映射；
3. 批量里单条失败（如数据查询类缺 SQL）不阻断整批，失败明细如实返回。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _admin_user():
    return {"user_id": 1, "user_name": "admin", "role": "admin"}


class _FakeScalarsResult:
    """模拟 db.execute() 的返回：scalars().first() 给出目标行。"""

    def __init__(self, example):
        self._example = example

    def scalars(self):
        return self

    def first(self):
        return self._example


def test_batch_action_permission_mapping():
    from app.api.portal.endpoints.examples import (
        BATCH_ACTION_PERMISSIONS,
        BATCH_AUDIT_ACTIONS,
        permission_for_audit_status,
    )

    # 废弃/删除共用「废弃删除」功能点，通过/驳回共用审核功能点
    assert permission_for_audit_status("deprecated") == "element:chatbi_example:delete"
    assert permission_for_audit_status("approved") == "element:chatbi_example:audit"
    assert BATCH_ACTION_PERMISSIONS["deprecate"] == "element:chatbi_example:delete"
    assert BATCH_ACTION_PERMISSIONS["delete"] == "element:chatbi_example:delete"
    assert BATCH_ACTION_PERMISSIONS["approve"] == "element:chatbi_example:audit"
    assert BATCH_ACTION_PERMISSIONS["reject"] == "element:chatbi_example:audit"
    assert BATCH_ACTION_PERMISSIONS["sync"] == "element:chatbi_example:sync"
    # 批量审核的动作必须都指向合法状态
    assert BATCH_AUDIT_ACTIONS["approve"][0] == "approved"
    assert BATCH_AUDIT_ACTIONS["reject"][0] == "rejected"
    assert BATCH_AUDIT_ACTIONS["deprecate"][0] == "deprecated"


@pytest.mark.asyncio
async def test_batch_rejects_unknown_action():
    from fastapi import BackgroundTasks, HTTPException

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )

    with pytest.raises(HTTPException) as exc:
        await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[1], action="nuke"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_batch_rejects_empty_selection():
    from fastapi import BackgroundTasks, HTTPException

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )

    with pytest.raises(HTTPException) as exc:
        await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[], action="deprecate"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_batch_rejects_oversized_selection():
    from fastapi import BackgroundTasks, HTTPException

    from app.api.portal.endpoints.examples import (
        BATCH_MAX_SIZE,
        BatchExampleActionRequest,
        batch_operate_examples,
    )

    with pytest.raises(HTTPException) as exc:
        await batch_operate_examples(
            request=BatchExampleActionRequest(ids=list(range(BATCH_MAX_SIZE + 1)), action="delete"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_batch_deprecate_dedupes_ids_and_reports_success():
    from fastapi import BackgroundTasks

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )
    from app.services.chatbi_example_service import ExampleService

    audit_mock = AsyncMock(return_value=True)
    with patch.object(ExampleService, "audit_example", audit_mock):
        res = await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[5, 6, 6], action="deprecate"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )

    assert res["code"] == 200
    assert res["data"]["success_ids"] == [5, 6]
    assert res["data"]["failed"] == []
    assert audit_mock.await_count == 2
    assert audit_mock.await_args_list[0].args[2] == "deprecated"


@pytest.mark.asyncio
async def test_batch_keeps_partial_failures_without_aborting():
    from fastapi import BackgroundTasks

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )
    from app.services.chatbi_example_service import ExampleService

    async def _audit(db, example_id, status):
        # 模拟数据查询类缺 SQL：拒绝通过，但其余条目照常处理
        if example_id == 2:
            raise ValueError("数据查询类型的案例必须包含有效的 SQL 语句才能通过审核")
        return True

    with patch.object(ExampleService, "audit_example", _audit):
        res = await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[1, 2, 3], action="approve"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )

    assert res["data"]["success_ids"] == [1, 3]
    assert [item["id"] for item in res["data"]["failed"]] == [2]
    assert "SQL" in res["data"]["failed"][0]["reason"]
    assert "成功 2 条" in res["message"]
    assert "失败 1 条" in res["message"]


@pytest.mark.asyncio
async def test_batch_delete_reports_not_found():
    from fastapi import BackgroundTasks

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )
    from app.services.chatbi_example_service import ExampleService

    with patch.object(ExampleService, "delete_example", AsyncMock(return_value=False)):
        res = await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[9], action="delete"),
            background_tasks=BackgroundTasks(),
            user=_admin_user(),
            db=AsyncMock(),
        )

    assert res["data"]["success_ids"] == []
    assert res["data"]["failed"] == [{"id": 9, "reason": "案例不存在或已被删除"}]


@pytest.mark.asyncio
async def test_batch_sync_schedules_background_tasks():
    from fastapi import BackgroundTasks

    from app.api.portal.endpoints.examples import (
        BatchExampleActionRequest,
        batch_operate_examples,
    )
    from app.services.chatbi_example_service import ExampleService

    approved = SimpleNamespace(id=11, status="approved", feedback_type="up")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeScalarsResult(approved))

    with patch.object(
        ExampleService, "sync_to_local_redis", AsyncMock(return_value=None)
    ) as sync_mock, patch(
        "app.api.portal.endpoints.examples.ConfigService.get",
        AsyncMock(return_value="local"),
    ):
        background_tasks = BackgroundTasks()
        res = await batch_operate_examples(
            request=BatchExampleActionRequest(ids=[11], action="sync"),
            background_tasks=background_tasks,
            user=_admin_user(),
            db=db,
        )

        assert res["data"]["success_ids"] == [11]
        assert len(background_tasks.tasks) == 1
        # 真正执行后台任务，确认挂的是本地同步函数
        await background_tasks()
        sync_mock.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_delete_endpoint_rejects_non_deprecated_with_400():
    from fastapi import HTTPException

    from app.api.portal.endpoints.examples import delete_example
    from app.services.chatbi_example_service import ExampleService

    with patch.object(
        ExampleService,
        "delete_example",
        AsyncMock(side_effect=ValueError("只有「已废弃」状态的案例允许删除，请先废弃该案例")),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_example(id=7, db=AsyncMock(), _=None)

    assert exc.value.status_code == 400
    assert "已废弃" in exc.value.detail


@pytest.mark.asyncio
async def test_delete_endpoint_returns_404_when_missing():
    from fastapi import HTTPException

    from app.api.portal.endpoints.examples import delete_example
    from app.services.chatbi_example_service import ExampleService

    with patch.object(ExampleService, "delete_example", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await delete_example(id=8, db=AsyncMock(), _=None)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_service_delete_example_only_allows_deprecated():
    from app.services.chatbi_example_service import ExampleService

    approved = SimpleNamespace(
        id=21, status="approved", rag_doc_id=None, feedback_type="up",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeScalarsResult(approved))

    with pytest.raises(ValueError):
        await ExampleService.delete_example(db, 21)

    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_delete_example_purges_vector_and_usage_rows():
    from app.services.chatbi_example_service import ExampleService
    from app.services.ai.example_index_service import ExampleIndexService

    deprecated = SimpleNamespace(
        id=22, status="deprecated", rag_doc_id=None, feedback_type="down",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeScalarsResult(deprecated))

    with patch.object(
        ExampleIndexService, "delete_vector", AsyncMock(return_value=None)
    ) as vector_mock:
        ok = await ExampleService.delete_example(db, 22)

    assert ok is True
    vector_mock.assert_awaited_once_with(22)
    db.delete.assert_awaited_once_with(deprecated)
    db.commit.assert_awaited()
    # 第一次 execute 取案例，第二次 execute 删引用流水
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_service_delete_example_survives_vector_cleanup_failure():
    """向量清理失败不能让库里的脏行删不掉。"""
    from app.services.chatbi_example_service import ExampleService
    from app.services.ai.example_index_service import ExampleIndexService

    deprecated = SimpleNamespace(
        id=23, status="deprecated", rag_doc_id=None, feedback_type="down",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeScalarsResult(deprecated))

    with patch.object(
        ExampleIndexService,
        "delete_vector",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        ok = await ExampleService.delete_example(db, 23)

    assert ok is True
    db.delete.assert_awaited_once_with(deprecated)

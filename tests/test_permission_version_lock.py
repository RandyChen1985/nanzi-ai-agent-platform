"""权限保存的乐观锁：过期快照必须被拒绝，而不是静默覆盖。

背景：``update_user_permissions`` / ``update_role_permissions`` 是**全量语义**——
先删光该主体在各类资源上的授权行，再按提交清单重建。而前端提交的是打开弹窗那一刻的
快照。这期间别处完全可能新增授权：

- 管理员在知识库页面把某个库分配给这个用户/角色；
- 用户提交的访问申请被审批通过（``ResourceAccessRequestService._grant``）。

这些写入都是**增量 upsert**，本身不会互相覆盖；但如果随后有人在用户/角色页点保存，
它们就会被这次"整份重建"抹掉，而审计里只留一条 update，事后查不出删了什么。

修法不是改全量语义（那要动前端与接口契约），而是加版本指纹：GET 带回、PUT 带回、
不符则 409。
"""

from unittest.mock import AsyncMock

import pytest

from app.schemas.permission import PermissionUpdate
from app.services.permission_service import (
    PermissionService,
    PermissionVersionConflict,
)

pytestmark = pytest.mark.no_infrastructure


class _RecordingDb:
    """只记录 execute 调用：用来证明"校验失败时一行都没删"。"""

    def __init__(self) -> None:
        self.executed: list = []
        self.committed = 0
        self.rolled_back = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        raise AssertionError("校验失败时不该执行任何 SQL")

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


def _service(version: str) -> PermissionService:
    service = PermissionService.__new__(PermissionService)
    service.db = _RecordingDb()
    service.get_permission_version = AsyncMock(return_value=version)  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_stale_version_is_rejected():
    service = _service("5:120:2026-11-05T10:00:00")

    with pytest.raises(PermissionVersionConflict) as excinfo:
        await service._assert_permission_version("4:118:2026-11-05T09:00:00", user_id=7)

    # 异常里带上当前版本，便于排查"差在哪"
    assert excinfo.value.current == "5:120:2026-11-05T10:00:00"


@pytest.mark.asyncio
async def test_matching_version_passes():
    service = _service("5:120:2026-11-05T10:00:00")

    await service._assert_permission_version("5:120:2026-11-05T10:00:00", user_id=7)


@pytest.mark.asyncio
async def test_absent_version_skips_check():
    """旧前端 / 脚本不带版本时不做校验——不能因为升级后端就把它们全拦下来。"""
    service = _service("5:120:2026-11-05T10:00:00")

    await service._assert_permission_version(None, user_id=7)
    await service._assert_permission_version("", user_id=7)

    service.get_permission_version.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_conflict_aborts_before_deleting_anything():
    """关键顺序：校验必须在 delete 之前，否则"先删后报错"一样会丢数据。"""
    service = _service("9:300:2026-11-05T12:00:00")
    updates = PermissionUpdate(permissions_version="8:290:2026-11-05T11:00:00")

    with pytest.raises(PermissionVersionConflict):
        await service.update_user_permissions(7, updates)

    assert service.db.executed == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_role_conflict_aborts_before_deleting_anything():
    service = _service("9:300:2026-11-05T12:00:00")
    updates = PermissionUpdate(permissions_version="8:290:2026-11-05T11:00:00")

    with pytest.raises(PermissionVersionConflict):
        await service.update_role_permissions(3, updates)

    assert service.db.executed == []  # type: ignore[attr-defined]


def test_version_fingerprint_covers_three_dimensions():
    """指纹必须同时含条数、最大 id、最大 updated_at。

    只用其中任意一项都有检测不到的变更：删一行加一行条数不变、纯删除不改 id、
    纯删除也不改时间戳。
    """
    import inspect

    from app.services.permission_service import PermissionService as Service

    source = inspect.getsource(Service.get_permission_version)
    assert "func.count(ResourcePermission.id)" in source
    assert "func.max(ResourcePermission.id)" in source
    assert "func.max(ResourcePermission.updated_at)" in source

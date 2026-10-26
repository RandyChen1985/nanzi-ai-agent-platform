"""授权弹窗的候选用户必须带部门。

授权时最容易搞混的就是「这个人到底在不在我要授权的那个部门」——只显示
姓名和账号根本判断不了：一个部门级知识库要加人，管理员得先知道谁是开发部的。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import metadata

pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """按调用顺序返回：角色 → 用户 → 部门归属。"""

    def __init__(self):
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        if self.calls == 1:
            return _FakeResult([SimpleNamespace(id=1, code="default", name="默认角色")])
        if self.calls == 2:
            return _FakeResult(
                [
                    SimpleNamespace(id=7, user_name="chenxiaolong", real_name="陈小龙"),
                    SimpleNamespace(id=8, user_name="test", real_name="test"),
                ]
            )
        if self.calls == 3:
            # (user_id, dept_id, dept_name)
            return _FakeResult([(7, 12, "开发部")])
        return _FakeResult([])


@pytest.mark.asyncio
async def test_candidates_include_departments():
    res = await metadata.get_metadata_auth_candidates(
        conn=_FakeSession(),
        user={"user_id": 1, "user_name": "admin", "role": "admin"},
    )
    users = res["data"]["users"]

    assert users[0]["departments"] == [{"id": 12, "name": "开发部"}]
    # 没有部门的用户给出空列表（前端据此显示「未分配部门」），不能缺字段
    assert users[1]["departments"] == []


def test_candidates_accept_either_permission():
    """只读端点：元数据编辑权 或 用户编辑权 任一即可。

    只认 ``element:metadata:edit`` 时，有用户管理权但没有元数据编辑权的管理员
    打开知识库的「分配授权成员」弹窗会拿到空列表，看起来像"没有可选用户"。
    """
    import inspect

    src = inspect.getsource(metadata.get_metadata_auth_candidates)
    assert "require_any_permission" in src
    assert "element:metadata:edit" in src
    assert "element:user:edit" in src

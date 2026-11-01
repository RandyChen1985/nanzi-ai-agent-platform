"""创建角色接口的 code 校验。

`get_user_permissions` 会把业务角色（`ai_agent_roles.code`）一并并进 `roles`
列表，而 `PermissionService.is_admin_user` 判的就是 `"admin" in roles`，
`check_permission` 对管理员**直接放行**。

于是「建一个 code=admin 的业务角色，把自己加进去」= 一键提权成系统管理员，
而此前 `POST /roles` 只校验了 code 重复。这里锁住提权 code 的拒绝行为。

注意 `ai_agent_roles` 里从来没有 code=admin 的种子行（管理员身份来自
`ai_agent_users.role` 字段），所以不存在「本来就有」的豁免。

**反面同样要锁**：早前把整个 `SYSTEM_ROLE_CODES` 都当成创建保留字，导致角色
管理页的「一键初始化 default 角色」必然 400 —— 用户看到的却是「default 角色
已存在」，而列表里根本没有这个角色（线上反馈）。`default` / `system` /
`miniprogram_user` 都不带特权，必须允许补建，只有 `admin` 是提权 code。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.roles import (
    PRIVILEGED_ROLE_CODES,
    SYSTEM_ROLE_CODES,
    CreateRoleRequest,
    create_role,
)

pytestmark = pytest.mark.no_infrastructure

ROLES_VUE = Path(__file__).resolve().parents[3] / "frontend/src/views/Roles.vue"


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """只支撑 create_role 的动作：查重、add、commit/refresh，不连库。"""

    def __init__(self, existing_id=None):
        self.existing_id = existing_id
        self.added: list = []
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return _FakeResult(self.existing_id)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        # 真实链路里 id / created_at 由数据库回填，这里补上以免响应组装修饰失败
        obj.id = 7
        obj.created_at = datetime(2026, 1, 1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["admin", "ADMIN", "  Admin  "])
async def test_create_role_rejects_privileged_code(code):
    # 校验发生在查重之前，所以拒绝路径不需要可用的 db
    with pytest.raises(HTTPException) as exc:
        await create_role(CreateRoleRequest(code=code, name="提权尝试"), admin={}, db=None)
    assert exc.value.status_code == 400
    assert "reserved" in str(exc.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["default", "Default", "  default  ", "system", "miniprogram_user"],
)
async def test_create_role_allows_system_codes_without_privilege(code):
    """补建内置角色必须成功——「一键初始化 default 角色」走的就是这个接口。"""
    session = _FakeSession()

    payload = await create_role(
        CreateRoleRequest(code=code, name="内置角色"), admin={}, db=session
    )

    expected = code.strip()
    assert payload["code"] == expected
    assert [role.code for role in session.added] == [expected]


@pytest.mark.asyncio
async def test_create_role_rejects_blank_code():
    with pytest.raises(HTTPException) as exc:
        await create_role(CreateRoleRequest(code="   ", name="空 code"), admin={}, db=None)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_check_normalizes_case_and_space():
    """PG 不忽略大小写，不归一就能建出 ``Default`` 与既有 ``default`` 并存的异形角色。"""
    session = _FakeSession(existing_id=3)

    with pytest.raises(HTTPException) as exc:
        await create_role(
            CreateRoleRequest(code=" Default ", name="重复角色"), admin={}, db=session
        )

    assert exc.value.status_code == 400
    assert "already exists" in str(exc.value.detail)
    assert session.added == []
    sql = session.statements[0]
    assert "lower" in sql and "trim" in sql, "查重必须做去空白 + 小写归一"


def test_only_admin_is_a_privileged_code():
    """提权清单只认 admin（is_admin_user 的全部口径）；它必须是系统角色子集。"""
    assert PRIVILEGED_ROLE_CODES == {"admin"}
    assert PRIVILEGED_ROLE_CODES <= SYSTEM_ROLE_CODES


def test_system_roles_stay_delete_protected():
    """放宽创建不等于放宽删除：四个系统角色仍然禁删（删除保护是另一份清单）。"""
    assert SYSTEM_ROLE_CODES == {"default", "admin", "system", "miniprogram_user"}


def test_role_code_input_hint_matches_backend_rule():
    """前端「角色代码」提示要说明只有 admin 是保留字，否则管理员不敢建 default。"""
    src = ROLES_VUE.read_text(encoding="utf-8")

    assert "admin 为系统保留" in src
    assert "admin / default / system / miniprogram_user 为系统保留" not in src

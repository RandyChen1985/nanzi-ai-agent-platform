"""角色 code 的保留字校验。

`get_user_permissions` 会把业务角色（`ai_agent_roles.code`）一并并进 `roles`
列表，而 `PermissionService.is_admin_user` 判的就是 `"admin" in roles`，
`check_permission` 对管理员**直接放行**。

于是「建一个 code=admin 的业务角色，把自己加进去」= 一键提权成系统管理员，
而此前 `POST /roles` 只校验了 code 重复。这里锁住保留 code 的拒绝行为。

注意 `ai_agent_roles` 里从来没有 code=admin 的种子行（管理员身份来自
`ai_agent_users.role` 字段），所以不存在「本来就有」的豁免。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.roles import (
    SYSTEM_ROLE_CODES,
    CreateRoleRequest,
    create_role,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["admin", "ADMIN", "  Admin  ", "default", "system", "miniprogram_user"],
)
async def test_create_role_rejects_reserved_codes(code):
    # 校验发生在查重之前，所以拒绝路径不需要可用的 db
    with pytest.raises(HTTPException) as exc:
        await create_role(CreateRoleRequest(code=code, name="提权尝试"), admin={}, db=None)
    assert exc.value.status_code == 400
    assert "reserved" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_create_role_rejects_blank_code():
    with pytest.raises(HTTPException) as exc:
        await create_role(CreateRoleRequest(code="   ", name="空 code"), admin={}, db=None)
    assert exc.value.status_code == 400


def test_reserved_codes_cover_every_system_role():
    """保留清单必须与删除保护用同一份，避免两处各自维护后漂移。"""
    assert SYSTEM_ROLE_CODES == {"default", "admin", "system", "miniprogram_user"}
    assert "admin" in SYSTEM_ROLE_CODES


def test_role_code_input_warns_about_reserved_words():
    """前端「角色代码」是用户手填的，必须在输入框旁说明保留字，否则被 400 拦下也不明所以。"""
    from pathlib import Path

    page = Path(__file__).resolve().parents[3] / "frontend/src/views/Roles.vue"
    src = page.read_text(encoding="utf-8")
    assert "为系统保留" in src
    for code in SYSTEM_ROLE_CODES:
        assert code in src, f"前端提示缺少保留字 {code}"

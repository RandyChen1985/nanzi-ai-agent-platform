"""建号入口不得旁路写部门快照。

`AuthService.generate_api_key()` 名义上是"发 key"，实际是**建 User 行的唯一入口**
（注册、SSO 同步、第三方同步、小程序、运维脚本都走它）。它保留了 `dept_code` /
`org_path` 两个参数，能直接写进 `users`，**完全绕开** `DepartmentService`：一旦有人
往里塞外部系统的部门标识（比如 `yovole/sh/dc1`），`users.org_path` 就会冒出第三种
格式，而且与关系表分叉——行级过滤、MCP 断言、`{user.org_path}` 全都会跟着出错。

所以这里不改签名（删参数属于改契约，仓库外若有脚本 import 会直接炸），而是
**显式拒绝**：传了就报错，并指路到唯一合法的写入方。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.auth_service import AuthService

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "app/services/auth_service.py").read_text(encoding="utf-8")
MANAGEMENT = (ROOT / "app/api/portal/endpoints/management.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rejects_dept_code():
    with pytest.raises(ValueError, match="sync_primary_snapshot"):
        await AuthService.generate_api_key("probe_user", dept_code="dev")


@pytest.mark.asyncio
async def test_rejects_org_path():
    with pytest.raises(ValueError, match="sync_primary_snapshot"):
        await AuthService.generate_api_key("probe_user", org_path="/main/dev/")


@pytest.mark.asyncio
async def test_guard_runs_before_touching_the_database():
    """必须在不碰库的情况下就失败，否则测试环境/未迁移库上会先炸别的错。"""
    with pytest.raises(ValueError):
        await AuthService.generate_api_key("probe_user", dept_code="dev")


def test_guard_sits_before_session_acquisition():
    body = SOURCE.split("async def generate_api_key", 1)[1].split("session, is_local", 1)[0]
    assert "sync_primary_snapshot" in body, "拒绝信息要指路到唯一合法写入方"
    assert "raise" in body


def test_create_user_endpoint_no_longer_passes_the_dead_params():
    """创建用户端点前面已经 400 拦掉了，这两行传参是死的，留着会误导。"""
    body = MANAGEMENT.split("api_key = await AuthService.generate_api_key(", 1)[1].split(")", 1)[0]
    assert "dept_code=request.dept_code" not in body
    assert "org_path=request.org_path" not in body


def test_request_schema_still_rejects_explicit_values():
    """字段保留是为了**显式拒绝**而不是静默忽略——忽略会让调用方以为写成功了。"""
    assert "dept_code / org_path 已由主部门自动派生" in MANAGEMENT

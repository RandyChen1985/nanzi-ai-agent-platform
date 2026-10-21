"""部门接口的 code 定位 + 编码实时校验 + 「删除部门不碰员工账号」的固化。

三个需求点（用户提出）：

1. **删除部门不影响员工信息**：删除只删部门行与（必然为空的）关系行，绝不碰
   `ai_agent_users`。账号、角色、权限、其它部门归属都不动。唯一会变的是
   `dept_code` / `org_path` 快照——因为被移出主部门的用户必须重新选一个主部门，
   这是"快照跟随真源"的必然结果，不是副作用。
2. **用 code 关联而不是自增 id**：存储仍用 id（内部主键，改 code 便宜、索引小），
   但 API 全面支持 code 定位——树、成员、排序都能用 code 寻址，外部系统迁移时
   按 code 写入即可，不需要 id 映射表。
3. **code 不能重复，页面实时检测**：后端唯一约束之外，还要一个可预检的接口，
   让人在输入时就得到反馈，而不是提交后才 400。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ENDPOINTS = (ROOT / "app/api/portal/endpoints/departments.py").read_text(encoding="utf-8")
SERVICE_PATH = ROOT / "app/services/department_service.py"


# ---------------------------------------------------------------- 1) 删除不动员工

class _FakeDept:
    id = 7
    code = "EAST"
    name = "华东"
    parent_id = None
    path = "/7/"


class _RecordingSession:
    """记录所有语句，用于断言「删除部门不会写 users 表」。"""

    def __init__(self, *, child_count=0, member_count=0):
        self._counts = [child_count, member_count]
        self.statements = []
        self.deleted = []

    async def get(self, model, pk):
        return _FakeDept()

    async def execute(self, stmt):
        self.statements.append(stmt)
        # 只有前两次是 count 查询；DELETE 语句不关心返回值
        value = self._counts.pop(0) if self._counts else 0

        class _R:
            def scalar(_self):
                return value

        return _R()

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        return None


def _compiled_all(session) -> str:
    from sqlalchemy.dialects import mysql

    return " ".join(str(s.compile(dialect=mysql.dialect())).lower() for s in session.statements)


@pytest.mark.asyncio
async def test_delete_department_never_writes_users_table():
    from app.services.department_service import DepartmentService

    session = _RecordingSession(child_count=0, member_count=0)
    await DepartmentService(session).delete(7)

    sql = _compiled_all(session)
    # `\b` 保证不会把 ai_agent_user_departments 误判成 ai_agent_users
    assert not re.search(r"\bai_agent_users\b", sql), f"删除部门不得触碰员工账号：{sql}"
    assert not re.search(r"\bdelete from ai_agent_users\b", sql)
    # 关系行仍然要显式清一次（无外键，残留就是悬挂数据）
    assert "ai_agent_user_departments" in sql
    # 部门行本身被删
    assert session.deleted and session.deleted[0] is not None


@pytest.mark.asyncio
async def test_delete_still_refuses_when_members_exist():
    """「不影响员工」不等于「可以带着成员删」：必须先移出，成员才不会失去归属。"""
    from app.services.department_service import DepartmentService

    session = _RecordingSession(child_count=0, member_count=3)
    with pytest.raises(ValueError):
        await DepartmentService(session).delete(7)
    assert not session.deleted


# ---------------------------------------------------------------- 2) code 定位

def test_department_code_must_not_be_pure_digits():
    """纯数字 code 会与自增 id 混淆，路径参数无法判断该按哪个解析。"""
    from app.services.department_service import validate_department_code

    for bad in ("123", "0", "007"):
        with pytest.raises(ValueError):
            validate_department_code(bad)
    assert validate_department_code("EAST-01") == "EAST-01"
    assert validate_department_code("  华东  ") == "华东"


def test_department_ref_kind_distinguishes_id_and_code():
    from app.services.department_service import looks_like_department_id

    assert looks_like_department_id("42") is True
    assert looks_like_department_id(" 42 ") is True
    assert looks_like_department_id("EAST") is False
    assert looks_like_department_id("EAST-01") is False
    assert looks_like_department_id("") is False


def test_all_department_routes_accept_code_or_id():
    """9 个端点统一用 `dept_ref`：纯数字按 id、其它按 code，调用方不必关心内部主键。"""
    assert ENDPOINTS.count("dept_ref: str") >= 6, "成员与结构端点都应支持 code 定位"
    assert "get_by_ref(" in ENDPOINTS, "路径参数要经统一的 id/code 解析"


def test_create_accepts_parent_code():
    assert "parent_code" in ENDPOINTS
    service = SERVICE_PATH.read_text(encoding="utf-8")
    assert "parent_code" in service, "服务层要能用 code 指定上级，迁移时无需 id 映射"


def test_reorder_accepts_parent_code():
    assert "ordered_codes" in ENDPOINTS or "parent_code" in ENDPOINTS


def test_set_user_departments_accepts_codes():
    management = (ROOT / "app/api/portal/endpoints/management.py").read_text(encoding="utf-8")
    segment = management.split("class SetUserDepartmentsRequest", 1)[1].split("class ", 1)[0]
    assert "dept_codes" in segment
    assert "primary_dept_code" in segment


# ---------------------------------------------------------------- 3) 编码预检

def test_code_availability_endpoint_exists():
    assert '@router.get("/code-available")' in ENDPOINTS
    segment = ENDPOINTS.split('@router.get("/code-available")', 1)[1].split("@router.", 1)[0]
    assert "available" in segment


def test_code_availability_is_registered_before_dynamic_route():
    """`/code-available` 必须先于 `/{dept_ref}` 注册，否则会被当成部门标识。"""
    assert ENDPOINTS.index('"/code-available"') < ENDPOINTS.index('"/{dept_ref}')


# ---------------------------------------------------------------- 4) 可读编码路径

def test_compose_code_path_turns_id_chain_into_codes():
    from app.services.department_service import compose_code_path

    assert compose_code_path("/1/7/", {1: "ROOT", 7: "EAST"}) == "/ROOT/EAST/"
    assert compose_code_path("/1/7/23/", {1: "ROOT", 7: "EAST", 23: "SH"}) == "/ROOT/EAST/SH/"


def test_compose_code_path_returns_empty_when_a_link_is_missing():
    """链路缺一环宁可留空，也不能拼出缺一截的假路径。"""
    from app.services.department_service import compose_code_path

    assert compose_code_path("/1/7/", {1: "ROOT"}) == ""
    assert compose_code_path("/", {}) == ""
    assert compose_code_path(None, {}) == ""


def test_tree_nodes_expose_code_path():
    """对外展示与外部系统对接要可读层级，但落库仍是 id 路径（改编码不必级联）。"""
    service_src = SERVICE_PATH.read_text(encoding="utf-8")
    tree_segment = service_src.split("async def build_tree", 1)[1].split("async def create", 1)[0]
    assert "code_path" in tree_segment
    assert "code_path_map" in tree_segment

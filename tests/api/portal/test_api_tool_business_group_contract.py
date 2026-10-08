"""API 工具业务分组（``sys_api_tools.group_name``）契约测试。

背景（用户反馈）：通过「API 工具注册表」登记的自定义接口在智能体配置里全部落进
「其他扩展工具」兜底组（截图里 64/67），因为分组是按工具名关键字硬编码猜测的，而
``sys_api_tools`` 根本没有分组字段。用户选定方案：给该表加业务分组字段。

用户补充约束（必须守住）：「我截屏的这些不是我们环境的，不能写死处理」
→ 迁移**只加列、不 UPDATE/INSERT/DELETE 任何行**，代码里不得出现客户前缀硬编码。
因此下面既测 schema/端点的行为，也把「迁移不改数据」写成断言。
"""
import re
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
MYSQL_MIGRATION = ROOT / "db-prod/V165-add_api_tool_group_name.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V66-add_api_tool_group_name.sql"

FORBIDDEN_IN_MIGRATION = ("UPDATE ", "INSERT ", "DELETE ", "DROP ")


# --------------------------------------------------------------------------- #
# schema：归一化、长度、round-trip
# --------------------------------------------------------------------------- #
def test_group_name_is_trimmed():
    from app.schemas.tool import SysApiToolCreate

    tool = SysApiToolCreate(name="t", url_template="http://x", group_name="  履约罚款 ")
    assert tool.group_name == "履约罚款"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", None])
def test_blank_group_name_normalizes_to_none(blank):
    """空串不能变成「看不见的分组」。"""
    from app.schemas.tool import SysApiToolCreate

    tool = SysApiToolCreate(name="t", url_template="http://x", group_name=blank)
    assert tool.group_name is None


def test_group_name_over_64_chars_is_rejected():
    from app.schemas.tool import SysApiToolCreate

    with pytest.raises(ValidationError):
        SysApiToolCreate(name="t", url_template="http://x", group_name="履" * 65)


def test_update_schema_normalizes_and_keeps_unset_semantics():
    from app.schemas.tool import SysApiToolUpdate

    assert SysApiToolUpdate(group_name=" 百亿竞价 ").group_name == "百亿竞价"
    assert SysApiToolUpdate(group_name="   ").group_name is None
    # 未传该字段时不应进入 partial update，否则改别的字段会把分组清空
    assert "group_name" not in SysApiToolUpdate().model_dump(exclude_unset=True)


def test_response_schema_reads_group_name_from_orm_object():
    from app.schemas.tool import SysApiToolResponse

    row = SimpleNamespace(
        id="tool-1",
        name="t",
        description=None,
        method="GET",
        url_template="http://x",
        headers="{}",
        parameter_schema="{}",
        is_active=True,
        group_name="履约罚款",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    assert SysApiToolResponse.model_validate(row).group_name == "履约罚款"


def test_batch_request_schema_normalizes_group_name():
    from app.schemas.tool import SysApiToolBatchGroupRequest

    payload = SysApiToolBatchGroupRequest(ids=["a", "b"], group_name="  履约罚款 ")
    assert payload.group_name == "履约罚款"
    assert SysApiToolBatchGroupRequest(ids=["a"], group_name="  ").group_name is None


# --------------------------------------------------------------------------- #
# 端点：路由存在 + 批量设置行为（依赖覆盖，不连真实库）
# --------------------------------------------------------------------------- #
class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    def __init__(self, items):
        self._items = items
        self.committed = False

    async def execute(self, _stmt):
        return _FakeResult(self._items)

    async def commit(self):
        self.committed = True


def _build_app(session=None):
    from app.api.portal.endpoints import tools as tools_module
    from app.core.dependencies import require_api_key
    from app.core.orm import get_db_session

    app = FastAPI()
    app.dependency_overrides[require_api_key] = lambda: {"user_id": 1, "role": "admin"}
    app.dependency_overrides[tools_module.SYSTEM_CONFIG_SAVE_PERMISSION] = lambda: {"user_id": 1}
    if session is not None:
        app.dependency_overrides[get_db_session] = lambda: session
    portal = APIRouter()
    portal.include_router(tools_module.router, prefix="/tools")
    app.include_router(portal, prefix="/api/portal")
    return app


@pytest.mark.asyncio
async def test_batch_group_route_is_registered():
    """本仓库 FastAPI 版本用懒展开路由（``_IncludedRouter``），``app.routes`` 取不到真实
    路径，因此直接发请求验证：目标路径可用、相邻的未知路径仍是 404。"""
    app = _build_app(_FakeSession([SimpleNamespace(id="a", group_name=None)]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        registered = await client.post(
            "/api/portal/tools/batch-group", json={"ids": ["a"], "group_name": "履约罚款"}
        )
        unknown = await client.post("/api/portal/tools/batch-group/extra", json={})

    assert registered.status_code == 200, registered.text
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_batch_group_sets_group_name_on_every_selected_tool():
    rows = [SimpleNamespace(id="a", group_name=None), SimpleNamespace(id="b", group_name="旧分组")]
    session = _FakeSession(rows)
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/portal/tools/batch-group",
            json={"ids": ["a", "b"], "group_name": " 履约罚款 "},
        )

    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2
    assert response.json()["group_name"] == "履约罚款"
    assert [row.group_name for row in rows] == ["履约罚款", "履约罚款"]
    assert session.committed is True


@pytest.mark.asyncio
async def test_batch_group_can_clear_the_group():
    rows = [SimpleNamespace(id="a", group_name="履约罚款")]
    session = _FakeSession(rows)
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/portal/tools/batch-group",
            json={"ids": ["a"], "group_name": "   "},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1, "group_name": None}
    assert rows[0].group_name is None


@pytest.mark.asyncio
async def test_batch_group_rejects_empty_selection():
    app = _build_app(_FakeSession([]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/portal/tools/batch-group",
            json={"ids": [], "group_name": "履约罚款"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_batch_group_rejects_oversized_selection():
    app = _build_app(_FakeSession([]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/portal/tools/batch-group",
            json={"ids": [str(uuid.uuid4()) for _ in range(501)], "group_name": "履约罚款"},
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 迁移脚本：只加列，绝不动数据（用户明确要求）
# --------------------------------------------------------------------------- #
def test_mysql_migration_only_adds_the_column():
    sql = MYSQL_MIGRATION.read_text(encoding="utf-8")
    upper = sql.upper()
    assert "ALTER TABLE" in upper and "SYS_API_TOOLS" in upper
    assert re.search(r"ADD COLUMN\s+`?group_name`?", sql, re.IGNORECASE)
    for forbidden in FORBIDDEN_IN_MIGRATION:
        assert forbidden not in upper, f"迁移不得包含 {forbidden.strip()}：只允许加列"


def test_pg_migration_is_idempotent_and_only_adds_the_column():
    sql = PG_MIGRATION.read_text(encoding="utf-8")
    upper = sql.upper()
    assert re.search(r'ADD COLUMN IF NOT EXISTS\s+"group_name"', sql, re.IGNORECASE)
    for forbidden in FORBIDDEN_IN_MIGRATION:
        assert forbidden not in upper, f"迁移不得包含 {forbidden.strip()}：只允许加列"

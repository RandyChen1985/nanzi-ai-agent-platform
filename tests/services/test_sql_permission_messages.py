from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import sql_query_execution_service as sql_service


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_table_permission_denied_message_includes_user_identity(monkeypatch):
    async def fake_allowed(_session, _user_id):
        return set()

    async def fake_registered(_session):
        return {"ck_fact_donghuan_real_metric_hbase"}

    monkeypatch.setattr(sql_service, "_fetch_allowed_physical_lowers_for_user", fake_allowed)
    monkeypatch.setattr(sql_service, "_fetch_all_registered_physical_lowers", fake_registered)

    result = await sql_service.enforce_physical_table_permissions_for_select(
        AsyncMock(),
        sql="SELECT * FROM ck_fact_donghuan_real_metric_hbase",
        dialect="clickhouse",
        user_id_eff=4,
        is_admin_eff=False,
        user_identity_label="chenxiaolong(4)",
    )

    assert result == "[Permission Denied] chenxiaolong(4) 无权访问表 'ck_fact_donghuan_real_metric_hbase'"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_table_unregistered_validation_failed_message(monkeypatch):
    async def fake_allowed(_session, _user_id):
        return set()

    async def fake_registered(_session):
        return set()  # 表未在元数据中注册

    monkeypatch.setattr(sql_service, "_fetch_allowed_physical_lowers_for_user", fake_allowed)
    monkeypatch.setattr(sql_service, "_fetch_all_registered_physical_lowers", fake_registered)

    result = await sql_service.enforce_physical_table_permissions_for_select(
        AsyncMock(),
        sql="SELECT * FROM missing_table",
        dialect="clickhouse",
        user_id_eff=4,
        is_admin_eff=False,
        user_identity_label="chenxiaolong(4)",
    )

    assert "[Validation Failed]" in result
    assert "物理表 'missing_table' 未在元数据中注册或不存在" in result


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_oracle_dual_skips_table_permission_check():
    result = await sql_service.enforce_physical_table_permissions_for_select(
        AsyncMock(),
        sql="SELECT LEVEL FROM dual CONNECT BY LEVEL <= 3",
        dialect="oracle",
        user_id_eff=4,
        is_admin_eff=False,
        user_identity_label="tester(4)",
    )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_dataset_table_consistency_validation_failed(monkeypatch):
    class FakeTable:
        def __init__(self, physical_name, status=1):
            self.physical_name = physical_name
            self.status = status

    class FakeDataset:
        def __init__(self, name, tables):
            self.id = 1
            self.name = name
            self.tables = tables
            self.enable_data_perm = False

    fake_ds = FakeDataset(
        name="test_dataset",
        tables=[FakeTable("allowed_table_1"), FakeTable("allowed_table_2")]
    )

    from app.services.metadata_service import MetadataService
    async def fake_get_dataset_by_name(_session, name):
        if name == "test_dataset":
            return fake_ds
        return None

    monkeypatch.setattr(MetadataService, "get_dataset_by_name", fake_get_dataset_by_name)

    async def fake_enforce(*args, **kwargs):
        return None
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_physical_table_permissions",
        fake_enforce,
    )
    # 表级授权门禁会额外查库，本用例只验证「数据集归属一致性」，表级白名单在
    # tests/services/test_dataset_scope_permissions.py 与门禁用例里单独覆盖。
    monkeypatch.setattr(
        "app.services.dataset_scope_service.enforce_table_scope_for_refs",
        fake_enforce,
    )

    from unittest.mock import MagicMock
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = [
        ("allowed_table_1", "业务表一"),
        ("allowed_table_2", "业务表二"),
    ]
    mock_session.execute.return_value = mock_res

    # 1. 正常场景：查询的表完全在当前数据集中
    result_ok = await sql_service.execute_sql_query_core(
        mock_session,
        sql="SELECT * FROM allowed_table_1",
        data_source="clickhouse_datasource",
        dataset_name="test_dataset",
        user_id=4,
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
    )
    assert "[DRY_RUN]" in result_ok

    # 2. 异常场景：查询的表不属于当前数据集，错误信息应引导回正确查询路径
    result_fail = await sql_service.execute_sql_query_core(
        mock_session,
        sql="SELECT * FROM other_table",
        data_source="clickhouse_datasource",
        dataset_name="test_dataset",
        user_id=4,
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
    )
    assert "[Validation Failed]" in result_fail
    assert "表 'other_table' 不属于当前指定的数据集 'test_dataset'" in result_fail
    assert "普通 execute_sql_query" in result_fail
    assert "跨数据集联邦查询流程" in result_fail
    assert "后端会尝试按 relation/维表做维度补全" in result_fail
    # 不应回显整张数据集表清单（与 get_dataset_schema 语义检索设计保持一致）
    assert "业务表二" not in result_fail
    assert "get_dataset_schema" in result_fail

    # 3. 异常场景：误把业务术语当表名，应精准提示对应的物理表名
    result_term = await sql_service.execute_sql_query_core(
        mock_session,
        sql="SELECT * FROM 业务表一",
        data_source="clickhouse_datasource",
        dataset_name="test_dataset",
        user_id=4,
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
    )
    assert "[Validation Failed]" in result_term
    assert "是业务术语" in result_term
    assert "物理表名 'allowed_table_1'" in result_term

    # 4. 异常场景：误把数据集名称当表名
    result_ds = await sql_service.execute_sql_query_core(
        mock_session,
        sql="SELECT * FROM test_dataset",
        data_source="clickhouse_datasource",
        dataset_name="test_dataset",
        user_id=4,
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
    )
    assert "[Validation Failed]" in result_ds
    assert "是数据集名称" in result_ds

    # 5. Oracle dual 为内置虚拟表，不参与数据集归属校验
    result_dual = await sql_service.execute_sql_query_core(
        mock_session,
        sql="""
        WITH all_months AS (
          SELECT TO_CHAR(ADD_MONTHS(DATE '2025-12-01', LEVEL - 1), 'YYYY-MM') AS month_label
          FROM dual
          CONNECT BY LEVEL <= 7
        )
        SELECT * FROM all_months am
        LEFT JOIN allowed_table_1 t ON 1=1
        """,
        data_source="oracle_datasource",
        dataset_name="test_dataset",
        user_id=4,
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
    )
    assert "[DRY_RUN]" in result_dual
    assert "不属于当前指定的数据集" not in result_dual
    assert "表 'dual'" not in result_dual


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_execute_sql_query_core_marks_permission_notice_when_row_filter_rewrites(monkeypatch):
    fake_ds = SimpleNamespace(
        id=1,
        name="sales_dataset",
        enable_data_perm=True,
        row_filter_config={
            "default_policy": [
                {"target_table": "orders", "condition": "orders.dept_code = {user.dept_code}"}
            ]
        },
    )

    async def fake_get_dataset_by_name(_session, name):
        return fake_ds if name == "sales_dataset" else None

    async def fake_enforce(*args, **kwargs):
        return None

    class FakePermissionService:
        def __init__(self, _session):
            pass

        async def get_user_permissions(self, _user_id, agent_id=None):
            return SimpleNamespace(roles=[])

    async def fake_sandbox_verify(_session, *, sql, data_source, dialect):
        return SimpleNamespace(allowed=True, optimized_sql=sql, message="")

    monkeypatch.setattr(sql_service.MetadataService, "get_dataset_by_name", fake_get_dataset_by_name)
    monkeypatch.setattr(sql_service, "PermissionService", FakePermissionService)
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_physical_table_permissions",
        fake_enforce,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_dataset_table_scope",
        fake_enforce,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.build_data_perm_table_metadata",
        lambda _binding, _dataset_name: {"orders": {"dept_code"}},
    )
    # 行级策略解析需要查库（授权行上的 row_policy），这里固定走 inherit。
    async def fake_row_policy(_session, _user_id, _dataset_id):
        return ("inherit", None)

    monkeypatch.setattr(
        "app.services.dataset_scope_service.resolve_effective_row_policy",
        fake_row_policy,
    )
    monkeypatch.setattr(
        "app.services.dataset_scope_service.enforce_table_scope_for_refs",
        fake_enforce,
    )
    monkeypatch.setattr(
        "app.services.ai.sql_sandbox_gate.SQLSandboxGate.verify_and_optimize",
        fake_sandbox_verify,
    )

    permission_notice = {}
    result = await sql_service.execute_sql_query_core(
        AsyncMock(),
        sql="SELECT * FROM orders o",
        data_source="clickhouse_datasource",
        dataset_name="sales_dataset",
        user_id=4,
        user_dimensions={"dept_code": "D001"},
        dry_run=True,
        is_admin=False,
        bypass_table_auth=False,
        permission_notice=permission_notice,
    )

    assert "[DRY_RUN]" in result
    assert permission_notice["row_filter_applied"] is True
    assert permission_notice["dataset_name"] == "sales_dataset"
    assert permission_notice["rule_count"] == 1
    assert permission_notice["message"] == "已按你的数据权限自动过滤结果"
    assert "dept_code" in str(permission_notice.get("executed_sql") or "")
    assert "D001" in str(permission_notice.get("executed_sql") or "")


@pytest.mark.asyncio
async def test_custom_row_policy_rejects_when_condition_does_not_apply(monkeypatch):
    """custom 行级条件引用的表不在本次查询里时必须拒绝，不能静默放行全表。

    重写器对「条件与查询表不匹配」是静默返回原 SQL 的（对 inherit 是既有语义，
    因为数据集策略按表生效）；但 custom 是这条授权独立的行级限制，
    静默放行 = 配了限制却查到全表，属于权限放大。
    """
    fake_ds = SimpleNamespace(
        id=1, name="sales_dataset", enable_data_perm=False, row_filter_config={}
    )

    async def fake_get_dataset_by_name(_session, name):
        return fake_ds if name == "sales_dataset" else None

    async def fake_enforce(*args, **kwargs):
        return None

    async def fake_row_policy(_session, _user_id, _dataset_id):
        return ("custom", "fact_a.dept_code = 'FIN'")

    async def fake_sandbox_verify(_session, *, sql, data_source, dialect):
        return SimpleNamespace(allowed=True, optimized_sql=sql, message="")

    monkeypatch.setattr(
        sql_service.MetadataService, "get_dataset_by_name", fake_get_dataset_by_name
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_physical_table_permissions",
        fake_enforce,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_dataset_table_scope",
        fake_enforce,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.build_data_perm_table_metadata",
        lambda _binding, _dataset_name: {"fact_b": {"dept_code"}},
    )
    monkeypatch.setattr(
        "app.services.dataset_scope_service.resolve_effective_row_policy", fake_row_policy
    )
    monkeypatch.setattr(
        "app.services.dataset_scope_service.enforce_table_scope_for_refs", fake_enforce
    )
    monkeypatch.setattr(
        "app.services.ai.sql_sandbox_gate.SQLSandboxGate.verify_and_optimize",
        fake_sandbox_verify,
    )

    result = await sql_service.execute_sql_query_core(
        session=None,
        sql="SELECT * FROM fact_b",
        data_source="mysql_oa",
        dataset_name="sales_dataset",
        user_id=4,
        user_dimensions={"user_name": "chenxiaolong", "dept_code": "FIN"},
        dry_run=False,
        is_admin=False,
    )

    assert "[Permission Denied]" in result
    assert "自定义行级条件未生效" in result

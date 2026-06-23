import pytest

from app.services.ai.chatbi_sql_query_binding import (
    SqlQueryBinding,
    TableBinding,
    apply_subquery_dataset_bindings,
    build_sql_query_binding,
    extract_schema_table_bindings,
    format_table_dataset_binding_block,
    pick_table_binding_candidate,
)
from app.services.ai.executors.prompts import DataQueryPrompts

pytestmark = pytest.mark.no_infrastructure

SCHEMA_OUTPUT = """
--- [Schema:1] type=table dataset=meta_yes_crm_ds table=VIEW_AI_VISIT_LOG score=0.91 ---
dataset: meta_yes_crm_ds
data_source: oracle
table_name: VIEW_AI_VISIT_LOG
columns:
  - name: FOLLOW_UP_PERSON
    type: NUMBER
  - name: FOLLOW_UP_DATE
    type: VARCHAR2
    examples:
      - '2026-06-01'
---
--- [Schema:2] type=table dataset=HR_ds table=HRMRESOURCE score=0.88 ---
dataset: HR_ds
data_source: oracle
table_name: HRMRESOURCE
columns:
  - name: ID
    type: NUMBER
  - name: LASTNAME
    type: VARCHAR2
"""


def test_extract_schema_table_bindings_includes_dataset_per_table():
    bindings = extract_schema_table_bindings(SCHEMA_OUTPUT)
    assert bindings["view_ai_visit_log"].dataset_name == "meta_yes_crm_ds"
    assert bindings["view_ai_visit_log"].data_source == "oracle"
    assert [column.name for column in bindings["view_ai_visit_log"].columns] == [
        "FOLLOW_UP_PERSON",
        "FOLLOW_UP_DATE",
    ]
    assert bindings["hrmresource"].dataset_name == "HR_ds"
    assert bindings["hrmresource"].data_source == "oracle"


def test_extract_schema_table_bindings_clears_conflicting_dataset():
    schema = """
--- [Schema:1] type=table dataset=meta_yes_crm_ds table=SHARED_TABLE ---
dataset: meta_yes_crm_ds
table_name: SHARED_TABLE
---
--- [Schema:2] type=table dataset=HR_ds table=SHARED_TABLE ---
dataset: HR_ds
table_name: SHARED_TABLE
"""
    bindings = extract_schema_table_bindings(schema)
    assert bindings["shared_table"].dataset_name == ""


def test_pick_table_binding_candidate_disambiguates_by_hint():
    candidates = [
        TableBinding(physical_name="SHARED_TABLE", dataset_name="meta_yes_crm_ds"),
        TableBinding(physical_name="SHARED_TABLE", dataset_name="HR_ds"),
    ]
    picked = pick_table_binding_candidate(candidates, hint_dataset_name="HR_ds")
    assert picked is not None
    assert picked.dataset_name == "HR_ds"


def test_pick_table_binding_candidate_returns_none_when_ambiguous():
    candidates = [
        TableBinding(physical_name="SHARED_TABLE", dataset_name="meta_yes_crm_ds"),
        TableBinding(physical_name="SHARED_TABLE", dataset_name="HR_ds"),
    ]
    assert pick_table_binding_candidate(candidates) is None


def test_build_sql_query_binding_merges_sql_tables():
    bindings = extract_schema_table_bindings(SCHEMA_OUTPUT)
    binding = build_sql_query_binding(
        schema_output=SCHEMA_OUTPUT,
        sql=(
            "SELECT v.FOLLOW_UP_PERSON, h.LASTNAME "
            "FROM VIEW_AI_VISIT_LOG v JOIN HRMRESOURCE h ON v.FOLLOW_UP_PERSON = h.ID"
        ),
        primary_dataset_name="meta_yes_crm_ds",
        table_bindings=bindings,
        dialect="oracle",
    )
    assert binding.involved_datasets() == {"meta_yes_crm_ds", "HR_ds"}
    assert binding.get_table("hrmresource").dataset_name == "HR_ds"


def test_format_table_dataset_binding_block_lists_hard_constraints():
    binding = build_sql_query_binding(schema_output=SCHEMA_OUTPUT)
    block = format_table_dataset_binding_block(binding)
    assert "物理表与数据集绑定" in block
    assert "VIEW_AI_VISIT_LOG" in block
    assert "meta_yes_crm_ds" in block
    assert "HRMRESOURCE" in block
    assert "HR_ds" in block


def test_build_federated_plan_prompt_includes_table_binding_block():
    binding = build_sql_query_binding(schema_output=SCHEMA_OUTPUT)
    prompt = DataQueryPrompts.build_federated_plan_prompt(
        SCHEMA_OUTPUT,
        "查询跟进记录和人员姓名",
        dataset_dialect_map={"meta_yes_crm_ds": "oracle", "HR_ds": "oracle"},
        sql_query_binding=binding,
    )
    assert "物理表与数据集绑定" in prompt
    assert "HRMRESOURCE" in prompt
    assert "HR_ds" in prompt


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_enforce_dataset_table_scope_uses_binding_dataset():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.ai.chatbi_sql_query_binding import (
        SqlQueryBinding,
        TableBinding,
        enforce_dataset_table_scope,
    )

    binding = SqlQueryBinding(
        tables={
            "hrmresource": TableBinding(
                physical_name="HRMRESOURCE",
                dataset_name="HR_ds",
            )
        }
    )
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = [("allowed_table_1", "业务表一")]
    mock_session.execute.return_value = mock_res

    class FakeDataset:
        id = 1
        name = "meta_yes_crm_ds"
        display_name = "meta_yes_crm_ds"

    err = await enforce_dataset_table_scope(
        mock_session,
        refs={"hrmresource": "HRMRESOURCE"},
        dataset_name="meta_yes_crm_ds",
        ds=FakeDataset(),
        binding=binding,
    )
    assert err is not None
    assert "不属于当前指定的数据集 'meta_yes_crm_ds'" in err


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_enforce_dataset_table_scope_allows_bound_primary_dataset():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.ai.chatbi_sql_query_binding import (
        SqlQueryBinding,
        TableBinding,
        enforce_dataset_table_scope,
    )

    binding = SqlQueryBinding(
        tables={
            "view_ai_visit_log": TableBinding(
                physical_name="VIEW_AI_VISIT_LOG",
                dataset_name="meta_yes_crm_ds",
            )
        }
    )
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = []
    mock_session.execute.return_value = mock_res

    class FakeDataset:
        id = 1
        name = "meta_yes_crm_ds"
        display_name = "meta_yes_crm_ds"

    err = await enforce_dataset_table_scope(
        mock_session,
        refs={"view_ai_visit_log": "VIEW_AI_VISIT_LOG"},
        dataset_name="meta_yes_crm_ds",
        ds=FakeDataset(),
        binding=binding,
    )
    assert err is None


def test_apply_subquery_dataset_bindings_corrects_wrong_dataset_name():
    binding = SqlQueryBinding(
        tables={
            "view_ai_visit_log": TableBinding(
                physical_name="VIEW_AI_VISIT_LOG",
                dataset_name="meta_yes_crm_ds",
                data_source="oracle",
            ),
            "hrmresource": TableBinding(
                physical_name="HRMRESOURCE",
                dataset_name="HR_ds",
                data_source="oracle",
            ),
        }
    )
    sub_queries = [
        {
            "dataset_name": "meta_yes_crm_ds",
            "temp_table": "t_visit",
            "sql": "SELECT FOLLOW_UP_PERSON FROM VIEW_AI_VISIT_LOG",
        },
        {
            "dataset_name": "meta_yes_crm_ds",
            "temp_table": "t_hr",
            "sql": "SELECT ID, LASTNAME FROM HRMRESOURCE",
        },
    ]
    corrected = apply_subquery_dataset_bindings(sub_queries, binding, default_dialect="oracle")
    assert corrected[0]["dataset_name"] == "meta_yes_crm_ds"
    assert corrected[1]["dataset_name"] == "HR_ds"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_resolve_sql_schema_preflight_marks_binding_validated():
    from unittest.mock import AsyncMock

    from app.services.ai.chatbi_sql_query_binding import (
        SchemaColumnMeta,
        SqlQueryBinding,
        TableBinding,
        resolve_sql_schema_preflight_with_binding,
    )

    binding = SqlQueryBinding(
        tables={
            "view_ai_visit_log": TableBinding(
                physical_name="VIEW_AI_VISIT_LOG",
                dataset_name="meta_yes_crm_ds",
                columns=[
                    SchemaColumnMeta(name="ID"),
                    SchemaColumnMeta(name="FOLLOW_UP_DATE"),
                ],
            )
        }
    )
    sql = "SELECT vl.ID FROM VIEW_AI_VISIT_LOG vl"
    err = await resolve_sql_schema_preflight_with_binding(
        AsyncMock(),
        sql=sql,
        binding=binding,
        schema_table_columns={},
        data_source="oracle",
        user_id=1,
        is_admin=False,
    )
    assert err == ""
    assert binding.preflight_validated is True
    assert binding.sql == sql


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_resolve_sql_schema_preflight_enriches_permission_allowed_table(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.ai.chatbi_sql_query_binding import (
        SchemaColumnMeta,
        SqlQueryBinding,
        TableBinding,
        resolve_sql_schema_preflight_with_binding,
    )

    async def fake_permission_check(*args, **kwargs):
        return None

    async def fake_resolve_table_bindings_from_db(session, physical_names, **kwargs):
        return {
            "hrmresource": TableBinding(
                physical_name="HRMRESOURCE",
                dataset_name="HR_ds",
                data_source="oracle",
                columns=[SchemaColumnMeta(name="ID")],
            )
        }

    monkeypatch.setattr(
        "app.services.sql_query_execution_service.check_physical_table_refs_permission",
        fake_permission_check,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.resolve_table_bindings_from_db",
        fake_resolve_table_bindings_from_db,
    )

    binding = SqlQueryBinding(
        tables={
            "view_ai_visit_log": TableBinding(
                physical_name="VIEW_AI_VISIT_LOG",
                columns=[SchemaColumnMeta(name="ID"), SchemaColumnMeta(name="FOLLOW_UP_PERSON")],
            )
        }
    )
    sql = (
        "SELECT vl.ID FROM VIEW_AI_VISIT_LOG vl "
        "LEFT JOIN hrmresource hr ON vl.FOLLOW_UP_PERSON = hr.ID"
    )
    err = await resolve_sql_schema_preflight_with_binding(
        AsyncMock(),
        sql=sql,
        binding=binding,
        schema_table_columns=binding.schema_table_columns(),
        data_source="oracle",
        user_id=1,
        is_admin=False,
    )
    assert err == ""
    assert binding.get_table("hrmresource").dataset_name == "HR_ds"
    assert binding.preflight_validated is True

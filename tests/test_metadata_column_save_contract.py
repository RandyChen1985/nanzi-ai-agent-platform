"""元数据字段保存契约：维度字段取值边界 + 列写回覆盖面 + 过时字段清理守卫。

本文件锁定三处一旦回退就会造成"静默丢数据/静默失效"的行为：

1. `ColumnSchema` 的维度字段必须收敛取值与长度
   （`dimension_role` 为枚举、`hierarchy_group` 不得超过 DB 列宽 VARCHAR(100)），
   否则超长值会绕到数据库层才报错，表现为整张表保存 500。
2. `save_table_metadata` 的列更新分支必须写回每一个可编辑字段。
   此前漏写 `type`，导致元数据编辑表单里改字段类型保存后不生效（刷新即回退）。
3. 过时字段清理必须对"空字段列表"做守卫。
   SQLAlchemy 把 `not_in([])` 渲染为 `NOT IN (NULL) OR (1 = 1)`（恒真），
   不守卫就会把该表所有字段元数据删光。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.metadata import ColumnSchema

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app" / "services" / "metadata_service.py"

# physical_name 是 upsert 的匹配键，不通过赋值更新；
# is_primary 目前没有前端编辑入口（仅只读展示 KeyIcon），暂不纳入写回契约。
NON_WRITEBACK_FIELDS = {"physical_name", "is_primary"}


def _column_update_branch() -> str:
    """截取 save_table_metadata 中 `if existing_col:` 的更新分支源码。"""
    source = SERVICE.read_text(encoding="utf-8")
    block_start = source.index("# 2. Handle Columns (Upsert + Delete stale)")
    block_end = source.index("await MetadataService._mark_dataset_as_modified", block_start)
    block = source[block_start:block_end]
    branch_start = block.index("if existing_col:")
    branch_end = block.index("# Create", branch_start)
    return block[branch_start:branch_end]


# --- 1. 维度字段取值边界 ---


@pytest.mark.parametrize("role", ["none", "time", "geo", "category", "identifier"])
def test_dimension_role_accepts_the_documented_enum(role: str) -> None:
    column = ColumnSchema(physical_name="city", term="城市", dimension_role=role)
    assert column.dimension_role == role


def test_dimension_role_defaults_to_none() -> None:
    column = ColumnSchema(physical_name="city", term="城市")
    assert column.dimension_role == "none"


def test_dimension_role_rejects_value_outside_the_enum() -> None:
    """任意字符串会顺着 Schema 进入 ChatBI 的 prompt，必须挡在入口。"""
    with pytest.raises(ValidationError):
        ColumnSchema(physical_name="city", term="城市", dimension_role="garbage")


def test_hierarchy_group_rejects_value_wider_than_the_db_column() -> None:
    """meta_columns.hierarchy_group 是 VARCHAR(100)，超长应在校验层就拒绝。"""
    with pytest.raises(ValidationError):
        ColumnSchema(physical_name="city", term="城市", hierarchy_group="x" * 101)


def test_hierarchy_group_accepts_value_exactly_at_the_db_width() -> None:
    value = "x" * 100
    column = ColumnSchema(physical_name="city", term="城市", hierarchy_group=value)
    assert column.hierarchy_group == value


# --- 2. 列写回覆盖面 ---


def test_column_update_branch_writes_back_every_editable_field() -> None:
    """防止再次出现"表单能改、后端不写"的静默失效（type 曾漏写）。"""
    branch = _column_update_branch()
    missing = [
        name
        for name in ColumnSchema.model_fields
        if name not in NON_WRITEBACK_FIELDS and f"existing_col.{name} = " not in branch
    ]
    assert missing == [], (
        f"列更新分支未写回这些可编辑字段，改它们会在保存后静默回退：{missing}"
    )


def test_column_type_is_written_back_on_update() -> None:
    """回归：编辑弹窗的字段类型下拉必须能真正落库。"""
    assert "existing_col.type = " in _column_update_branch()


# --- 3. 过时字段清理守卫 ---


def test_stale_column_cleanup_is_guarded_by_a_non_empty_list() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    guard_at = source.index("if incoming_col_names:")
    else_at = source.index("else:", guard_at)
    guarded_block = source[guard_at:else_at]

    assert "delete(MetaColumn)" in guarded_block, "过时字段清理必须位于非空守卫内"
    assert "await db.execute(delete_stmt)" in guarded_block


def test_empty_not_in_predicate_matches_every_row() -> None:
    """固化被守卫挡住的那个危险语义，说明守卫为什么必须存在。"""
    from app.models.metadata import MetaColumn

    rendered = str(
        MetaColumn.physical_name.not_in([]).compile(compile_kwargs={"literal_binds": True})
    )
    assert "1 = 1" in rendered, "SQLAlchemy 对空列表的处理是本契约的前提，变化时请同步复核"

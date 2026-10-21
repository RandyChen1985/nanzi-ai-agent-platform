"""pending ChatBI state 恢复：binding 不能停留在 dict。"""

from dataclasses import asdict

from app.services.ai.chatbi_sql_query_binding import SqlQueryBinding, TableBinding
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.sql_gates import SchemaColumnMeta
from app.services.ai.runners.chatbi.state_serialization import pending_state_to_data_run_state


def test_pending_state_restores_bindings_to_dataclasses() -> None:
    state = DataRunState()
    state.table_bindings = {
        "orders": TableBinding(
            physical_name="orders",
            dataset_name="sales",
            columns=[SchemaColumnMeta(name="id", col_type="int")],
        )
    }
    state.sql_query_binding = SqlQueryBinding(sql="SELECT 1", tables=state.table_bindings)

    restored, _ = pending_state_to_data_run_state({"data_run_state": asdict(state)})

    binding = restored.table_bindings["orders"]
    assert isinstance(binding, TableBinding)
    assert binding.dataset_name == "sales"
    assert isinstance(binding.columns[0], SchemaColumnMeta)
    assert isinstance(restored.sql_query_binding, SqlQueryBinding)
    assert isinstance(restored.sql_query_binding.tables["orders"], TableBinding)

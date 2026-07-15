import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]


def _table_collation(sql_path: Path) -> str:
    source = sql_path.read_text(encoding="utf-8")
    match = re.search(r"COLLATE=([a-zA-Z0-9_]+)", source)
    assert match, f"{sql_path.name} 未声明表排序规则"
    return match.group(1)


def test_saved_report_run_migration_uses_parent_table_collation():
    parent = ROOT / "db-prod/V82-create_portal_saved_reports.sql"
    child = ROOT / "db-prod/V96-create-portal-saved-report-runs.sql"

    assert _table_collation(child) == _table_collation(parent)

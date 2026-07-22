from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).parents[2]


def test_data_source_management_exposes_postgresql_option():
    source = (ROOT / "frontend/src/views/DataSourceManagement.vue").read_text()

    assert "postgresql" in source
    assert "PostgreSQL" in source
    assert "defaultPort: 5432" in source
    assert "@input=\"sanitizeNameSuffix\"" in source
    assert "@paste=\"handleNamePaste\"" in source
    assert "replace(/[^a-zA-Z0-9_]/g, '')" in source

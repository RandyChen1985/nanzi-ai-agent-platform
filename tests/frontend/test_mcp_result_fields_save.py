from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def test_result_fields_and_parameter_sources_save_as_one_form():
    result = subprocess.run(
        ["node", "tests/frontend/mcp_result_fields_save.mjs"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

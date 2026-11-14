"""真实 Vue 表单的固定模式候选选择与统一保存；网络边界使用合成元数据。"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", [
    "immediate_selector", "new_profile", "restore_fixed", "type_version_reload",
    "late_response", "error_retry", "empty_candidates", "save_together",
    "save_failure", "unavailable_binding", "infer_unchanged",
])
def test_fixed_credential_behavior(case):
    result = subprocess.run(
        ["node", "tests/frontend/mcp_fixed_credential_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

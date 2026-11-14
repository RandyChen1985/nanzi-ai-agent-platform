"""离线运行真实 Vue 组件，验证创建开关不阻断类型查看及已有凭据选择。"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", [
    "type_status", "type_filter", "type_details", "personal_options", "admin_options",
    "picker_global_disabled", "picker_type_disabled", "picker_existing", "picker_stale",
])
def test_creation_gate_behavior(case):
    result = subprocess.run(
        ["node", "tests/frontend/credential_creation_gate_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

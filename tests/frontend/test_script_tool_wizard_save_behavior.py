"""真实 Vue 数字输入事件后的脚本向导校验、摘要与保存，无数据库依赖。"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", [
    "create_numeric_timeout", "edit_numeric_timeout", "clear_timeout",
    "integer_bounds", "loaded_timeout", "saving_guard",
])
def test_script_tool_wizard_save_behavior(case):
    result = subprocess.run(
        ["node", "tests/frontend/script_tool_wizard_save_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

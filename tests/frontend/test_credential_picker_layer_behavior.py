"""真实选择器在高层级弹窗中的展开、选择及权限范围提示，无数据库依赖。"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", ["nested_modal", "reopen_higher_modal", "plain_page", "empty_system"])
def test_credential_picker_layer_behavior(case):
    result = subprocess.run(
        ["node", "tests/frontend/credential_picker_layer_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""离线渲染任务凭据弹窗并验证异步状态。"""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", ["late_get", "late_post", "background", "poll", "poll_limit"])
def test_task_credential_wait_modal(case):
    result = subprocess.run(
        ["node", "tests/frontend/task_credential_wait_modal_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

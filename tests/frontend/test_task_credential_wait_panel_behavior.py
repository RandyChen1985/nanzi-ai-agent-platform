"""渲染任务凭据卡片并验证选择、过期和异步状态。"""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("case", ["inline", "stale_trace", "expired", "late_get", "late_post", "background", "poll", "poll_limit"])
def test_task_credential_wait_panel(case):
    result = subprocess.run(
        ["node", "tests/frontend/task_credential_wait_panel_behavior.cjs", case],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

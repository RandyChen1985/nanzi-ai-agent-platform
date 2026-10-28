"""分享有效期/密码纯函数的行为测试（node 真跑 TS）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行行为测试")
def test_share_expiry_behaviour():
    script = Path("tests/frontend/share_expiry.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "会话分享有效期测试全部通过" in proc.stdout

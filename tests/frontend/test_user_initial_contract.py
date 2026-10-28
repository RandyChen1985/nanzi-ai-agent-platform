"""左下角用户头像简称：英文取前两字母，中文取后两汉字。

需求来源（用户截图 + 原文）：「你看看 左下角 用户名字简称，优化一下，如果英文名取
前两个字母，中文名取后两个汉字，如果只有两个字就直接取两个字。」

判定口径：
- ``demo`` → ``DE``（前两个字母、大写）；
- ``李世民`` → ``世民``、``曹老板`` → ``老板``（后两个汉字）；
- ``俊超`` → ``俊超``（只有两个字就是整名）；
- 姓名缺失时回退，头像不得出现空白方块。

行为用 node 真跑一遍 ``frontend/src/utils/userInitial.ts``（照 ``email_mask.mjs`` 先例）；
接线部分读源码断言，防止侧边栏与个人中心各自漂移。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "user_initial.mjs"
UTIL = ROOT / "frontend/src/utils/userInitial.ts"
DASHBOARD = ROOT / "frontend/src/views/Dashboard.vue"
PERSONAL_CENTER = ROOT / "frontend/src/views/PersonalCenter.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_user_initial_behaviour_via_node():
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(RUNNER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_shared_helper_exists():
    source = _read(UTIL)
    assert "export function userInitialOf" in source
    # 英文侧必须先剔除空白与标点，否则 "Eric Wang" 会取到 "E "
    assert "LATIN" in source and "HAN" in source


def test_dashboard_sidebar_uses_helper():
    source = _read(DASHBOARD)
    assert "userInitialOf" in source
    # 侧边栏头像走 computed + 自适应字号，不再是裸 substring
    assert "sidebarInitial" in source
    assert "sidebarInitialClass" in source
    assert "substring(0, 2)" not in source


def test_personal_center_uses_helper():
    source = _read(PERSONAL_CENTER)
    assert "userInitialOf" in source
    assert "substring(0, 2)" not in source


def test_both_views_import_shared_helper():
    for path in (DASHBOARD, PERSONAL_CENTER):
        source = _read(path)
        assert 'from "../utils/userInitial"' in source or "from '../utils/userInitial'" in source

"""用户 / 部门列表的邮箱展示：没有 element:user:edit 时必须脱敏。

需求来源（用户）：如果用户没有编辑权限，用户管理界面和部门管理页面列表中的
用户邮箱要脱敏显示。

判定口径：
- 有 ``element:user:edit`` → 展示完整邮箱（他们要联系用户、核对账号）；
- 没有 → 只展示 ``a***@domain``，域名保留，星号固定三个。

行为用 node 真跑一遍 ``frontend/src/utils/maskEmail.ts``（照
``tool_import_roundtrip.mjs`` 的先例）；接线部分读源码断言，防止某个视图漏改。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "email_mask.mjs"
USERS = ROOT / "frontend/src/views/Users.vue"
MEMBERS_PANEL = ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_mask_email_behaviour_via_node():
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(RUNNER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_user_management_masks_email_without_edit_permission():
    source = _read(USERS)
    assert "maskEmail" in source
    # 三处展示：待审核卡片、列表表格、卡片视图
    assert source.count("canEditUser ? user.email : maskEmail(user.email)") >= 3


def test_department_members_mask_email_without_edit_permission():
    panel = _read(MEMBERS_PANEL)
    assert "maskEmail" in panel
    # 成员列表 + 添加成员候选列表
    assert panel.count("canEditUser ? member.email : maskEmail(member.email)") >= 1
    assert panel.count("canEditUser ? user.email : maskEmail(user.email)") >= 1

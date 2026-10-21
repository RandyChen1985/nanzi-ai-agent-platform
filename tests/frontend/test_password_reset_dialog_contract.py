"""重置密码弹窗：等保三级规范的唯一实现。

背景：用户管理页与部门管理页都要重置密码。两处曾经各写一套——用户管理有完整的
"密码复杂度规则说明 + 实时 6 项合规指标 + 输入框染色 + 按钮禁用"，部门页只有两个
裸输入框。规则函数（`checkPasswordPolicy`）虽然同一个，但**呈现**差了一个档次，
管理员会以为部门页的密码要求更宽松。

所以把弹窗抽成一个组件，两处复用；这里的测试守住"规则完整 + 两处不再各写一套"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/PasswordResetDialog.vue"
USERS = ROOT / "frontend/src/views/Users.vue"
MEMBERS = ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue"


def test_component_uses_the_shared_policy_function():
    src = COMPONENT.read_text(encoding="utf-8")
    assert "checkPasswordPolicy" in src
    assert "passwordPolicy" in src


def test_component_states_the_compliance_rule():
    """等保三级要求写在界面上，否则用户只能靠试错猜规则。"""
    src = COMPONENT.read_text(encoding="utf-8")
    assert "等保三级" in src
    assert "8-32" in src
    assert "特殊符号" in src


def test_component_shows_live_indicator_per_rule():
    src = COMPONENT.read_text(encoding="utf-8")
    for rule in ("hasLength", "hasUpper", "hasLower", "hasDigit", "hasSpecial", "categoryCount"):
        assert rule in src, f"缺少实时合规指标：{rule}"


def test_component_blocks_submit_until_valid_and_confirmed():
    """合规没过、或两次输入不一致，按钮必须点不动——不靠 toast 事后阻拦。"""
    src = COMPONENT.read_text(encoding="utf-8")
    bindings = [line for line in src.splitlines() if ":disabled=" in line and "policy.valid" in line]
    assert bindings, "提交按钮必须同时绑定合规结论与二次确认"
    assert "password !== confirm" in " ".join(bindings)


def test_both_pages_reuse_the_component():
    for path in (USERS, MEMBERS):
        src = path.read_text(encoding="utf-8")
        assert "PasswordResetDialog" in src, f"{path.name} 仍在用自己的一套密码弹窗"

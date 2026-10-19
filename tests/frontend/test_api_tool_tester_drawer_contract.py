"""试跑区是右侧抽屉：入口在表单里，参数和结果在抽屉里操作。"""
from pathlib import Path

TESTER = Path("frontend/src/components/system/ApiToolTester.vue")


def test_tester_opens_a_right_side_drawer():
    source = TESTER.read_text(encoding="utf-8")
    assert 'teleport to="body"' in source
    assert "showDrawer" in source
    assert "打开试跑" in source
    assert "translate-x-full" in source
    assert "max-w-2xl" in source
    assert "关闭试跑抽屉" in source


def test_drawer_keeps_request_details_and_system_param_notice():
    source = TESTER.read_text(encoding="utf-8")
    assert "实际发出的请求（含系统级参数）" in source
    assert "系统级参数：" in source
    assert "平台自动注入，无需填写" in source

"""代客签发权限码契约。

「代他人签发嵌入凭证」使用**独立的功能权限码** `element:agent:embed_ticket_issue`，
可在后台按角色分配。历史上该能力借用 `GET:/api/v1/users/profile`（获取用户画像）
API 权限，已改为独立权限码——该 API 权限回归本义，不再兼作签发凭证。

本契约锁定三件事：
1. 权限码声明在权限树的「智能体中心」节点下（后台据此渲染可分配项）；
2. 后端按 `element` 类型检查该权限码；
3. 后端不再借用 `GET:/api/v1/users/profile` 作为代客签发凭证。

另有一组断言锁定**对外文案**同步：嵌入调试面板的权限提示与接入文档的排障说明
都必须指向新权限码，不能继续告诉用户「需要获取用户画像权限」。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PERMISSIONS_TS = ROOT / "frontend/src/constants/permissions.ts"
EMBED_SERVICE = ROOT / "app/services/embed_service.py"
WIDGET_DEBUGGER = ROOT / "frontend/src/views/WidgetDebugger.vue"
INTEGRATION_GUIDE = ROOT / "docs/md/embed_integration_guide.md"

pytestmark = pytest.mark.no_infrastructure

PERMISSION_ID = "element:agent:embed_ticket_issue"
PERMISSION_LABEL = "代他人签发嵌入凭证"
AGENT_MENU_ID = "menu:agent_management"


def _agent_management_node(source: str) -> str:
    """截取权限树中「智能体中心」节点定义。"""
    start = source.index(f"id: '{AGENT_MENU_ID}'")
    # 到下一个顶层菜单节点为止
    rest = source[start:]
    end = rest.index("id: 'menu:", 1) if "id: 'menu:" in rest[1:] else len(rest)
    return rest[:end]


def test_permission_declared_under_agent_management_menu():
    """权限码必须挂在「智能体中心」下，否则后台角色页不会显示该项。"""
    node = _agent_management_node(PERMISSIONS_TS.read_text(encoding="utf-8"))

    assert PERMISSION_ID in node
    assert "代他人签发嵌入凭证" in node


def test_backend_checks_element_permission_type():
    """后端必须按 element 类型检查该权限码。"""
    source = EMBED_SERVICE.read_text(encoding="utf-8")

    assert PERMISSION_ID in source
    assert '"element"' in source


def test_backend_does_not_borrow_profile_api_permission():
    """代客签发不得再借用 GET:/api/v1/users/profile 作为凭证。

    注意：旧权限码可能仍出现在说明注释里，因此这里只检查 check_permission
    的实际调用参数，而不是整个文件的文本。
    """
    source = EMBED_SERVICE.read_text(encoding="utf-8")

    call_start = source.index("check_permission(")
    call_block = source[call_start : call_start + 400]

    assert PERMISSION_ID in call_block
    assert '"api"' not in call_block
    assert "GET:/api/v1/users/profile" not in call_block


# --- 对外文案必须同步 ---


def test_widget_debugger_hint_points_to_new_permission():
    """嵌入调试面板的权限提示必须指向新权限码。

    原文案写的是「代他人签发需管理员或『获取用户画像』权限」，会让管理员
    去授一个语义无关的权限。
    """
    source = WIDGET_DEBUGGER.read_text(encoding="utf-8")

    assert PERMISSION_LABEL in source
    assert "获取用户画像」权限" not in source


def test_integration_guide_troubleshooting_points_to_new_permission():
    """接入文档排障章节必须指向新权限码，而不是已废弃的借用方式。

    这里只约束「解决方案」这一行本身——文档允许（也应该）保留一段历史沿革说明，
    告诉老用户旧权限已不再生效，那属于迁移提示而非错误指引。
    """
    source = INTEGRATION_GUIDE.read_text(encoding="utf-8")

    assert PERMISSION_ID in source

    section = source.split("### Q2:", 1)[1].split("### Q3:", 1)[0]
    solution = section.split("- **解决方案**", 1)[1].split("\n", 1)[0]

    assert "GET:/api/v1/users/profile" not in solution

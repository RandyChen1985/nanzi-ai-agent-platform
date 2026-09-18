"""代客签发权限码契约。

「代他人签发嵌入凭证」使用独立的 **API 权限码** `POST:/api/v1/embed/tickets`，
可在后台「API 权限」中按角色分配。历史上该能力借用 `GET:/api/v1/users/profile`
（获取用户画像）权限，两者语义无关——现已改用专用权限码，profile 权限回归本义。

注意该权限码的实际语义是「**可代表他人**调用签发接口」：`/embed/*` 在 V1 接口
白名单内不做拦截，而自己为自己签发也不需要本权限。

本契约锁定：
1. 权限码登记在 `ASSIGNABLE_V1_API_RESOURCES`（后台「API 权限」据此渲染）；
2. profile 权限的描述不再声称可用于代客签发；
3. 后端按 `api` 类型检查该权限码；
4. 该能力不再以 element 形式挂在权限树里；
5. 对外文案同步指向新的分配入口。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
V1_API_ACCESS = ROOT / "app/core/v1_api_access.py"
EMBED_SERVICE = ROOT / "app/services/embed_service.py"
PERMISSIONS_TS = ROOT / "frontend/src/constants/permissions.ts"
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"
USERS_VUE = ROOT / "frontend/src/views/Users.vue"
WIDGET_DEBUGGER = ROOT / "frontend/src/views/WidgetDebugger.vue"
INTEGRATION_GUIDE = ROOT / "docs/md/embed_integration_guide.md"

pytestmark = pytest.mark.no_infrastructure

API_PERMISSION_ID = "POST:/api/v1/embed/tickets"
LEGACY_PERMISSION_ID = "GET:/api/v1/users/profile"
ELEMENT_PERMISSION_ID = "element:agent:embed_ticket_issue"
PERMISSION_LABEL = "代他人签发嵌入凭证"


def test_api_permission_registered_as_assignable_resource():
    """权限码必须在可分配 API 清单里，否则后台「API 权限」不会显示该项。"""
    source = V1_API_ACCESS.read_text(encoding="utf-8")

    assert API_PERMISSION_ID in source
    assert PERMISSION_LABEL in source


def test_profile_permission_description_no_longer_claims_ticket_issuance():
    """profile 权限的描述不得再声称可用于代客签发。"""
    source = V1_API_ACCESS.read_text(encoding="utf-8")

    start = source.index(f'"id": "{LEGACY_PERMISSION_ID}"')
    entry = source[start : start + 400]

    assert "签发" not in entry
    assert "Ticket" not in entry


def test_backend_checks_api_permission_type():
    """后端必须按 api 类型检查新权限码，且不再指向旧的 profile 权限。"""
    source = EMBED_SERVICE.read_text(encoding="utf-8")

    call_start = source.index("check_permission(")
    call_block = source[call_start : call_start + 400]

    assert API_PERMISSION_ID in call_block
    assert '"api"' in call_block
    assert LEGACY_PERMISSION_ID not in call_block


def test_capability_not_declared_as_element_permission():
    """该能力不应再以 element 形式出现在权限树中。"""
    source = PERMISSIONS_TS.read_text(encoding="utf-8")

    assert ELEMENT_PERMISSION_ID not in source


# --- 权限列表的可读性 ---


def test_profile_permission_display_name_is_unambiguous():
    """profile 权限的显示名应为「获取用户信息」。

    「用户画像」在中文语境里是 user persona（标签化建模），与本接口实际行为
    （查询用户资料，且返回真实 API Key）不符，容易让管理员误判权限用途。
    注意这里只改**显示名**，权限 ID `GET:/api/v1/users/profile` 保持不变，
    因此已分配的权限不会失效。
    """
    source = V1_API_ACCESS.read_text(encoding="utf-8")

    assert "获取用户信息" in source
    assert "获取用户画像" not in source


@pytest.mark.parametrize(
    "page,label",
    [(ROLES_VUE, "角色管理"), (USERS_VUE, "用户管理（编辑用户）")],
    ids=["roles", "users"],
)
def test_permission_cards_render_api_path_and_method(page: Path, label: str):
    """API 权限卡片必须显示接口路径与请求方法。

    **该卡片在「角色管理」与「用户管理（编辑用户）」两处各有一份独立实现**，
    两处都必须覆盖——只改一处会让另一个页面的管理员看不到路径。

    卡片同时服务 agents/datasets/metadata/apis 四个 tab，非 API 资源没有
    path/method 字段，因此两处都必须带 v-if 守卫，否则会渲染空行。
    """
    source = page.read_text(encoding="utf-8")

    assert "res.path" in source, f"{label} 的权限卡片未渲染接口路径"
    assert 'v-if="res.path"' in source, f"{label} 的路径行缺少 v-if 守卫"
    assert "res.method" in source, f"{label} 的权限卡片未渲染请求方法"
    assert 'v-if="res.method"' in source, f"{label} 的方法徽章缺少 v-if 守卫"


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

    assert API_PERMISSION_ID in source

    section = source.split("### Q2:", 1)[1].split("### Q3:", 1)[0]
    solution = section.split("- **解决方案**", 1)[1].split("\n", 1)[0]

    assert LEGACY_PERMISSION_ID not in solution

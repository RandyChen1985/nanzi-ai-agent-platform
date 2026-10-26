"""知识库「反向授权」入口的权限码，必须与用户页/角色页同口径。

历史实现用的是 `menu:system:users`（菜单码），但 `menu:*` 只负责侧栏是否可见，
拿它做后端功能校验等于「看得见菜单 = 有权改授权」。而用户页保存整份权限用的是
`element:user:edit`、角色页用 `element:role:edit` —— 两个入口各认一套，结果同一个人
会在一个入口能授权、另一个入口被拒。

统一为 `element:user:edit` 后，还有一条不能破的约束：**前端按钮显隐必须同码**，
否则又会回到「按钮亮着、点了 403」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "app/api/portal/endpoints/ragflow.py"
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def backend() -> str:
    return BACKEND.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_grant_endpoints_use_the_element_code(backend):
    assert (
        '@router.post("/datasets/{dataset_id}/permissions", '
        'dependencies=[Depends(require_permission("element", "element:user:edit"))])'
    ) in backend
    assert (
        '@router.delete("/datasets/{dataset_id}/permissions", '
        'dependencies=[Depends(require_permission("element", "element:user:edit"))])'
    ) in backend


def test_grant_endpoints_no_longer_gate_on_the_menu_code(backend):
    """菜单码只该出现在「谁看得见这个菜单」的判定里，不该守功能接口。"""
    assert 'require_permission("menu", "menu:system:users")' not in backend


def test_frontend_button_uses_the_same_code(backend, page):
    assert "hasPermission('element:user:edit')" in page
    # 前后端必须是同一个码，写死对比一次，改动时两边一起变
    assert 'element:user:edit' in backend


def test_grant_button_still_requires_write_access(page):
    """权限码只是第一道；对库没写权限的人仍然不该看到入口。"""
    block = page.split("const canManagePermissions = computed(() => {", 1)[1].split("})", 1)[0]
    assert "isReadOnlyDataset(dataset)" in block

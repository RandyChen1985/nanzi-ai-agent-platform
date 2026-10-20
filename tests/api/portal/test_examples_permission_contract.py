"""案例集页面的功能点权限：后端按码校验 + 前端按码隐藏。

用户反馈「dashboard/examples 没配权限，按钮也能操作、没隐藏」。核实结果：
`examples.py` 的 7 个端点原本只 `require_api_key`、零 `element:` 校验；前端也只有
「同步」一处 `hasPermission`。本文件钉住三件事：

1. 每个端点绑定哪个功能点码（读接口跟随页面菜单）；
2. audit 的「状态 → 码」映射（通过/驳回 = audit，废弃 = delete，不能共用审核码）；
3. 前端按钮与同一组码的对应关系（含「无任何操作权限时不显示空下拉」）。
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.examples import permission_for_audit_status

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "app/api/portal/endpoints/examples.py"
VIEW = ROOT / "frontend/src/views/ExampleManagement.vue"

AUDIT = "element:chatbi_example:audit"
SYNC = "element:chatbi_example:sync"
DELETE = "element:chatbi_example:delete"
MENU = "menu:chatbi_examples"


def _segment(name: str, next_name: str | None = None) -> str:
    source = API.read_text(encoding="utf-8")
    segment = source.split(f"async def {name}(", 1)[1]
    if next_name:
        segment = segment.split(f"async def {next_name}(", 1)[0]
    return segment


def test_list_and_search_test_follow_the_page_menu():
    assert f'require_permission("menu", "{MENU}")' in _segment(
        "list_examples", "sync_all_examples"
    )
    # search-test 是只读模拟，跟随页面入口即可（没有独立功能点）
    assert f'require_permission("menu", "{MENU}")' in _segment(
        "search_test_examples"
    )


def test_enhance_and_update_require_audit_code():
    assert f'require_permission("element", "{AUDIT}")' in _segment(
        "trigger_manual_enhance", "update_example"
    )
    assert f'require_permission("element", "{AUDIT}")' in _segment(
        "update_example", "list_examples"
    )


def test_sync_endpoints_require_sync_code():
    assert f'require_permission("element", "{SYNC}")' in _segment(
        "sync_all_examples", "audit_example"
    )
    assert f'require_permission("element", "{SYNC}")' in _segment(
        "sync_example", "search_test_examples"
    )


def test_audit_maps_status_to_independent_codes():
    assert permission_for_audit_status("approved") == AUDIT
    assert permission_for_audit_status("rejected") == AUDIT
    assert permission_for_audit_status("deprecated") == DELETE

    source = _segment("audit_example", "sync_example")
    assert "permission_for_audit_status(request.status)" in source
    assert "check_permission(" in source, "audit 没有按状态做权限校验"
    assert 'user.get("role") != "admin"' in source, "非 admin 必须走 check_permission"


def test_frontend_defines_one_predicate_per_code():
    view = VIEW.read_text(encoding="utf-8")
    for code, predicate in (
        (AUDIT, "canAuditExample"),
        (SYNC, "canSyncExample"),
        (DELETE, "canDeleteExample"),
    ):
        assert f"hasPermission('{code}')" in view, f"前端没有判断 {code}"
        assert f"const {predicate} = computed" in view


def test_frontend_row_menu_entries_are_gated():
    view = VIEW.read_text(encoding="utf-8")
    row_menu = view.split("行内「更多」操作浮动下拉菜单", 1)[1].split("<!-- 案例集设计规范", 1)[0]
    assert 'v-if="canOpenRowMenu"' in view, "没有任何操作权限时不应显示「更多」按钮"
    assert "openRowMenuExample.status !== 'approved' && canAuditExample" in row_menu
    assert "openRowMenuExample.status !== 'rejected' && canAuditExample" in row_menu
    assert "openRowMenuExample.status === 'approved' && canSyncExample" in row_menu
    assert "openRowMenuExample.status !== 'deprecated' && canDeleteExample" in row_menu


def test_frontend_detail_actions_are_gated():
    view = VIEW.read_text(encoding="utf-8")
    # 只看「保存修改」按钮前的窗口，避免页面别处的 canEditExample 造成假绿
    assert "&& canEditExample" in view.split("保存修改", 1)[0][-600:]
    assert "canEditExample && (currentExample.enhance_status !== 'pending'" in view
    assert view.count("!canEditExample") >= 4, "详情输入框应随无审核权限一起禁用"
    # 帮助弹窗里的「一键全量同步」入口同样要受 sync 码控制（说明段落里也有这个词，
    # 用带箭头的按钮文本定位，避免匹配到说明文字）
    assert 'v-if="canSyncExample"' in view.split("一键全量同步 &rarr;", 1)[0][-400:]


@pytest.mark.asyncio
async def test_audit_endpoint_enforces_status_specific_code(monkeypatch):
    """真实端点函数 + fake 权限服务：非 admin 无授权时必须 403，且按状态选码。

    不连库：把 PermissionService 换成记录调用的假实现，直接断言端点抛出的 403
    文案与查询的权限码——避免「只在源码里写了 require_permission」的假绿。
    """
    from app.api.portal.endpoints import examples as mod

    seen: list[tuple[str, str]] = []

    class FakePermissionService:
        def __init__(self, db):
            self.db = db

        async def check_permission(self, user_id, resource_type, resource_id):
            seen.append((resource_type, resource_id))
            return False

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService", FakePermissionService
    )

    for status, expected in (("approved", AUDIT), ("deprecated", DELETE)):
        with pytest.raises(HTTPException) as exc:
            await mod.audit_example(
                mod.AuditRequest(id=1, status=status),
                None,
                user={"role": "user", "user_id": 7},
                db=object(),
            )
        assert exc.value.status_code == 403
        assert expected in exc.value.detail
        assert seen[-1] == ("element", expected)

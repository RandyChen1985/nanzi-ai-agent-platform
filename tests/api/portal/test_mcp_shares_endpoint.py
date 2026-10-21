"""个人 MCP 分享端点契约。"""
from pathlib import Path

import pytest

from app.api.portal.endpoints import mcp as mcp_endpoints

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT = ROOT / "app/api/portal/endpoints/management.py"
NOTIFY = ROOT / "app/services/mcp_share_service.py"


def _routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in mcp_endpoints.router.routes:
        for method in getattr(route, "methods", set()) or set():
            found.add((method.upper(), route.path))
    return found


def test_share_routes_are_registered():
    routes = _routes()
    for path, method in (
        ("/share-targets", "GET"),
        ("/servers/{server_id}/shares", "POST"),
        ("/shares/summary", "GET"),
        ("/shares/sent", "GET"),
        ("/shares/sent/{share_id}", "DELETE"),
        ("/shares/received", "GET"),
        ("/shares/received/{share_id}/accept", "POST"),
        ("/shares/received/{share_id}/reject", "POST"),
    ):
        assert (method, path) in routes, f"缺少路由 {method} {path}"


def test_share_routes_do_not_collide_with_server_routes():
    routes = [path for _method, path in _routes()]
    assert routes.count("/servers/{server_id}/shares") == 1


def test_accept_endpoint_accepts_install_overrides():
    """安装向导允许覆盖名字/备注/地址，端点必须把这三项透传下去。"""
    source = NOTIFY.read_text(encoding="utf-8")
    assert "server_name: str | None = None" in source
    assert "remark: str | None = None" in source
    assert "sse_url: str | None = None" in source

    endpoint_source = (ROOT / "app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")
    assert "class McpShareAcceptRequest(BaseModel)" in endpoint_source
    accept_body = endpoint_source.split("async def accept_mcp_received_share(", 1)[1]
    for field in ("server_name=overrides.server_name", "remark=overrides.remark", "sse_url=overrides.sse_url"):
        assert field in accept_body


def test_received_payload_exposes_suggested_name_for_wizard():
    """列表要给出「默认落地名」，否则安装向导弹层无法预填后缀。"""
    source = NOTIFY.read_text(encoding="utf-8")
    assert "suggested_server_name" in source
    assert "_suggested_name_from_payload" in source


def test_share_errors_map_to_expected_status_codes():
    """服务层异常到 HTTP 状态的映射；漏了就会把 409 变成 500。"""
    from app.services import mcp_share_service as svc

    cases = [
        (svc.McpShareNotFoundError("gone"), 404),
        (svc.McpShareConflictError("conflict"), 409),
        (svc.McpSharePermissionError("denied"), 403),
    ]
    for exc, expected in cases:
        mapped = mcp_endpoints._share_http_exception(exc)
        assert mapped is not None, f"{type(exc).__name__} 没有映射 HTTP 状态"
        assert mapped.status_code == expected

    # 未预期异常必须继续往上抛（由端点转 500），不要被误吞成 4xx
    assert mcp_endpoints._share_http_exception(ValueError("boom")) is None


def test_share_request_defaults_to_carrying_credentials():
    payload = mcp_endpoints.McpServerShareRequest(target_user_ids=[9])
    assert payload.include_credentials is True
    assert payload.message is None
    # 安装向导三项默认全部沿用快照
    accept = mcp_endpoints.McpShareAcceptRequest()
    assert (accept.server_name, accept.remark, accept.sse_url) == (None, None, None)


def test_share_summary_exposes_menu_counts():
    """左侧菜单角标依赖这三个计数键，改名会让前端静默显示 0。"""
    source = NOTIFY.read_text(encoding="utf-8")
    for key in ('"received_pending"', '"sent_pending"', '"sent_total"'):
        assert key in source


def test_delete_user_cleans_mcp_shares():
    source = MANAGEMENT.read_text(encoding="utf-8")
    body = source.split("async def delete_user(", 1)[1].split("\n@router.", 1)[0]
    assert "McpServerShare" in body
    assert "target_user_id" in body
    assert body.index("McpServerShare") < body.index("await db.delete(user)")


def test_share_notifications_use_dedicated_category():
    source = NOTIFY.read_text(encoding="utf-8")
    assert 'resource_type="mcp_share"' in source
    assert '"mcp_share" if for_receiver else "mcp_share_result"' in source
    assert '"target_tab": target_tab' in source

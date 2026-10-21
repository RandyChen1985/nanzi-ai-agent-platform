"""契约：连接测试（POST /verify）是创建与编辑共用的第一步，不能只认 edit 码。

事故背景：向导第一步固定调用 `/verify`，而该端点只要求 `element:mcp_management:edit`。
权限树里 create / edit 是两项独立授权，只有 create 的角色能打开新建向导，却永远卡在
第一步（后端 403），与前端"可创建"的语义矛盾。
"""

from pathlib import Path

import pytest

from app.api.portal.endpoints import mcp


pytestmark = pytest.mark.no_infrastructure


def test_verify_accepts_create_or_edit_permission():
    source = Path("app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")
    marker = '"/verify"'
    start = source.index(marker)
    decorator = source[max(0, start - 120) : start + 400]

    assert "require_any_permission" in decorator, "连接测试应接受 create 与 edit 任一权限"
    assert '"element:mcp_management:create"' in decorator
    assert '"element:mcp_management:edit"' in decorator


def test_verify_route_is_wired():
    route = next(
        route
        for route in mcp.router.routes
        if getattr(route, "path", None) == "/verify" and "POST" in route.methods
    )
    assert route.dependencies

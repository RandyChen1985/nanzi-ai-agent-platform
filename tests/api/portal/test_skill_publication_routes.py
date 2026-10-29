import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.portal.endpoints import personal_skills, skills
from app.core.dependencies import require_api_key, require_permission
from app.core.orm import get_db_session


pytestmark = pytest.mark.no_infrastructure


async def _fake_user():
    return {"user_id": 7, "user_name": "tester", "role": "user"}


async def _fake_admin():
    return {"user_id": 1, "user_name": "admin", "role": "admin"}


async def _fake_db_session():
    """端点里的 session 只被透传给 service；用例都拦在 service 边界。"""
    yield SimpleNamespace()


def _route_paths(app: FastAPI) -> set[str]:
    """收集完整路由路径。

    starlette 1.6 起 ``app.include_router()`` 不再把子路由摊平进 ``app.routes``，
    而是包成 ``_IncludedRouter``（没有 ``path`` 属性），完整路径要递归拼
    ``include_context.prefix``。旧写法 ``route.path`` 会直接 AttributeError。
    """

    def walk(routes, prefix=""):
        for route in routes:
            path = getattr(route, "path", None)
            if path is not None:
                yield prefix + path
                continue
            inner = getattr(route, "original_router", None)
            if inner is not None:
                ctx = getattr(route, "include_context", None)
                yield from walk(inner.routes, prefix + getattr(ctx, "prefix", ""))

    return set(walk(app.routes))


def _build_app() -> FastAPI:
    portal = APIRouter()
    portal.include_router(personal_skills.router, prefix="/skills/personal")
    portal.include_router(skills.router, prefix="/skills")
    app = FastAPI()
    app.dependency_overrides[require_api_key] = _fake_user
    app.include_router(portal, prefix="/api/portal")
    return app


def _build_admin_app() -> FastAPI:
    app = _build_app()
    app.dependency_overrides[skills.skill_platform_admin] = _fake_admin
    app.dependency_overrides[skills.skill_publication_reviewer] = _fake_admin
    app.dependency_overrides[get_db_session] = _fake_db_session
    return app


def test_personal_publication_submit_route_exists():
    routes = _route_paths(_build_app())
    assert "/api/portal/skills/personal/{skill_id}/publication-requests" in routes
    assert "/api/portal/skills/personal/{skill_id}/publication-requests/withdraw" in routes


def test_admin_publication_review_routes_exist():
    routes = _route_paths(_build_app())
    assert "/api/portal/skills/publication-requests" in routes
    assert "/api/portal/skills/publication-requests/{version_id}/approve" in routes
    assert "/api/portal/skills/publication-requests/{version_id}/reject" in routes


def test_personal_submit_returns_publication_summary(monkeypatch):
    expected = {
        "publication_id": "publication-1",
        "version_id": "version-1",
        "publication_status": "PENDING",
        "version_number": 1,
    }
    monkeypatch.setattr(
        "app.api.portal.endpoints.personal_skills.submit_personal_skill",
        AsyncMock(return_value=expected),
    )

    response = TestClient(_build_app()).post(
        "/api/portal/skills/personal/demo/publication-requests"
    )

    assert response.status_code == 200
    assert response.json()["data"] == expected


def test_personal_withdraw_returns_publication_summary(monkeypatch):
    expected = {
        "publication_id": "publication-1",
        "version_id": "version-1",
        "publication_status": "WITHDRAWN",
        "version_status": "WITHDRAWN",
    }
    monkeypatch.setattr(
        "app.api.portal.endpoints.personal_skills.withdraw_personal_skill_publication",
        AsyncMock(return_value=expected),
    )

    response = TestClient(_build_app()).post(
        "/api/portal/skills/personal/demo/publication-requests/withdraw"
    )

    assert response.status_code == 200
    assert response.json()["data"] == expected


def test_reject_requires_non_blank_comment():
    app = _build_app()
    app.dependency_overrides[skills.skill_publication_reviewer] = _fake_admin
    with patch(
        "app.api.portal.endpoints.skills.reject_publication",
        new=AsyncMock(side_effect=ValueError("reject comment is required")),
    ):
        response = TestClient(app).post(
            "/api/portal/skills/publication-requests/version-1/reject",
            json={"comment": "   "},
        )
    assert response.status_code in {400, 422}


# ---------------------------------------------------------------------------
# 下架：列表 / 下架两个新接口
# ---------------------------------------------------------------------------
def test_revoke_routes_exist():
    # 本仓库 FastAPI 用懒展开路由（app.routes 里是 _IncludedRouter），直接看 router 定义
    routes = {route.path for route in skills.router.routes}
    assert "/publications" in routes
    assert "/publications/{version_id}/revoke" in routes


def test_publications_route_is_registered_before_dynamic_skill_route():
    """路由遮蔽防护：``/publications`` 必须排在 ``/{skill_id}`` 之前。

    FastAPI 按注册顺序匹配，动态路由先注册时 ``GET /skills/publications`` 会被当成
    ``skill_id="publications"`` 吞掉（随后报 400 保留字/技能不存在），下架列表永远打不开。
    """
    paths = [route.path for route in skills.router.routes]
    publication_index = paths.index("/publications")
    dynamic_index = paths.index("/{skill_id}")
    assert publication_index < dynamic_index
    # 发布审核那批老朋友同理，一起钉死
    for path in ("/publication-requests", "/publications/{version_id}/revoke"):
        assert paths.index(path) < dynamic_index


def test_publications_route_is_not_shadowed_by_skill_detail():
    """直接用 TestClient 打真实路由：拿到的是列表结构，而不是「技能」详情/校验错误。"""
    app = _build_admin_app()
    item = {"publication_id": "publication-1", "version_id": "version-1", "name": "demo"}
    with patch(
        "app.api.portal.endpoints.skills.list_published_publications",
        new=AsyncMock(return_value=[item]),
    ):
        response = TestClient(app).get("/api/portal/skills/publications")

    assert response.status_code == 200
    assert response.json() == {"status": "success", "data": {"items": [item]}}


def test_revoke_route_is_not_shadowed_and_returns_synced_report():
    app = _build_admin_app()
    payload = {
        "publication_id": "publication-1",
        "version_id": "version-1",
        "platform_skill_id": "public-demo-12345678",
        "name": "demo",
        "publication_status": "REVOKED",
    }
    with patch(
        "app.api.portal.endpoints.skills.revoke_published_version",
        new=AsyncMock(return_value=dict(payload)),
    ), patch(
        "app.api.portal.endpoints.skills.remove_skill_references",
        new=AsyncMock(return_value={"synced": {"conversations": 2}, "warnings": ["w"]}),
    ) as cleanup:
        response = TestClient(app).post(
            "/api/portal/skills/publications/version-1/revoke",
            json={"comment": "内容不合规"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["publication_status"] == "REVOKED"
    # 与删除/重命名同一风格：把引用同步结果如实带出来
    assert body["data"]["synced"] == {"conversations": 2}
    assert body["data"]["warnings"] == ["w"]
    assert cleanup.await_args.kwargs["skill_id"] == "public-demo-12345678"
    assert cleanup.await_args.kwargs["scope"] == "global"


@pytest.mark.parametrize("body", [{}, {"comment": ""}, {"comment": "   "}])
def test_revoke_requires_a_non_blank_comment(body):
    app = _build_admin_app()
    with patch(
        "app.api.portal.endpoints.skills.revoke_published_version",
        new=AsyncMock(side_effect=AssertionError("空原因不该走到 service")),
    ):
        response = TestClient(app).post(
            "/api/portal/skills/publications/version-1/revoke", json=body
        )
    assert response.status_code == 422


def test_revoke_maps_not_found_and_conflict():
    app = _build_admin_app()
    from app.services.skill_publication_service import (
        PublicationConflictError,
        PublicationNotFoundError,
    )

    with patch(
        "app.api.portal.endpoints.skills.revoke_published_version",
        new=AsyncMock(side_effect=PublicationNotFoundError("publication version not found")),
    ):
        response = TestClient(app).post(
            "/api/portal/skills/publications/missing/revoke", json={"comment": "下架"}
        )
    assert response.status_code == 404

    with patch(
        "app.api.portal.endpoints.skills.revoke_published_version",
        new=AsyncMock(side_effect=PublicationConflictError("already revoked")),
    ):
        response = TestClient(app).post(
            "/api/portal/skills/publications/version-1/revoke", json={"comment": "下架"}
        )
    assert response.status_code == 409


def test_revoke_endpoints_reuse_skill_admin_permission_dependency():
    """权限沿用技能管理端现有依赖（element:skills:admin），不新造一套。"""
    for path in ("/publications", "/publications/{version_id}/revoke"):
        route = next(route for route in skills.router.routes if route.path == path)
        calls = [getattr(item, "call", None) for item in route.dependant.dependencies]
        assert skills.skill_publication_reviewer in calls
        assert skills.skill_publication_reviewer is skills.skill_platform_admin


def test_publications_is_a_reserved_skill_id():
    """``publications`` 被字面路由占用，用户不能再建同名技能撞车。"""
    from fastapi import HTTPException

    assert "publications" in skills._RESERVED_SKILL_IDS
    with pytest.raises(HTTPException) as exc:
        skills.validate_secure_skill_path("publications")
    assert exc.value.status_code == 400

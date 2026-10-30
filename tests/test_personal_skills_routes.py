import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.portal.endpoints import personal_skills, skills
from app.core.dependencies import require_api_key

pytestmark = pytest.mark.no_infrastructure


async def _fake_user():
    return {"user_id": 1, "user_name": "tester", "role": "user"}


def _find_route(app: FastAPI, path: str, method: str):
    """按完整路径定位路由对象。

    starlette 1.6 起 ``include_router()`` 不再把子路由摊平进 ``app.routes``，
    而是包成 ``_IncludedRouter``（没有 ``path``），完整路径要递归拼
    ``include_context.prefix``。旧写法直接遍历 ``app.routes`` 会 StopIteration。
    """

    def walk(routes, prefix=""):
        for route in routes:
            route_path = getattr(route, "path", None)
            if route_path is not None:
                if prefix + route_path == path:
                    yield route
                continue
            inner = getattr(route, "original_router", None)
            if inner is not None:
                ctx = getattr(route, "include_context", None)
                yield from walk(inner.routes, prefix + getattr(ctx, "prefix", ""))

    for route in walk(app.routes):
        if method in (getattr(route, "methods", None) or set()):
            return route
    return None


def _build_app(*, personal_first: bool, platform_admin: bool = True) -> FastAPI:
    portal_router = APIRouter()
    if personal_first:
        portal_router.include_router(personal_skills.router, prefix="/skills/personal")
        portal_router.include_router(skills.router, prefix="/skills")
    else:
        portal_router.include_router(skills.router, prefix="/skills")
        portal_router.include_router(personal_skills.router, prefix="/skills/personal")

    app = FastAPI()
    app.dependency_overrides[require_api_key] = _fake_user
    if platform_admin:
        app.dependency_overrides[skills.skill_platform_admin] = _fake_user
    app.include_router(portal_router, prefix="/api/portal")
    return app


def test_platform_skill_list_is_readable_without_management_permission():
    """平台技能目录可被登录用户查询，不应绑定平台技能管理权限。"""
    with tempfile.TemporaryDirectory() as tmp:
        global_dir = Path(tmp) / "global_skills"
        global_dir.mkdir()
        skill_dir = global_dir / "public-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: Public Skill\ndescription: visible to signed-in users\n---\n",
            encoding="utf-8",
        )

        with patch("app.api.portal.endpoints.skills.settings", SimpleNamespace(SKILLS_DIR=str(global_dir))):
            app = _build_app(personal_first=True, platform_admin=False)
            list_route = _find_route(app, "/api/portal/skills", "GET")
            assert list_route is not None, "GET /api/portal/skills 路由不存在"
            dependency_calls = {dependency.call for dependency in list_route.dependant.dependencies}
            assert require_api_key in dependency_calls
            assert skills.skill_platform_admin not in dependency_calls

            client = TestClient(app)
            response = client.get("/api/portal/skills")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "success"
        assert payload["data"][0]["id"] == "public-skill"


def test_list_personal_skills_route_must_register_before_platform_skill_id():
    """GET /skills/personal 必须命中个人技能列表，而非平台 /skills/{skill_id}。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        global_dir = root / "global_skills"
        global_dir.mkdir()
        personal_root = root / "agent_workspaces" / "tester__1" / "skills"
        personal_root.mkdir(parents=True)
        skill_dir = personal_root / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: My Skill\ndescription: test\n---\n",
            encoding="utf-8",
        )

        with patch("app.api.portal.endpoints.skills.settings", SimpleNamespace(SKILLS_DIR=str(global_dir))):
            with patch(
                "app.api.portal.endpoints.personal_skills.get_user_personal_skills_dir",
                return_value=str(personal_root),
            ):
                broken_app = _build_app(personal_first=False)
                broken_client = TestClient(broken_app)
                broken_resp = broken_client.get("/api/portal/skills/personal")
                assert broken_resp.status_code != 200

                fixed_app = _build_app(personal_first=True)
                fixed_client = TestClient(fixed_app)
                fixed_resp = fixed_client.get("/api/portal/skills/personal")
                assert fixed_resp.status_code == 200
                payload = fixed_resp.json()
                assert payload["status"] == "success"
                assert len(payload["data"]) == 1
                assert payload["data"][0]["id"] == "my-skill"
                assert payload["data"][0]["scope"] == "personal"


def test_import_personal_skill_writes_to_personal_directory():
    """POST /skills/personal/import 应写入个人技能目录，而非平台目录。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        global_dir = root / "global_skills"
        global_dir.mkdir()
        personal_root = root / "agent_workspaces" / "tester__1" / "skills"
        personal_root.mkdir(parents=True)

        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "imported-personal/SKILL.md",
                "---\nname: Imported\ndescription: personal import\n---\n",
            )
        archive = buf.getvalue()

        with patch("app.api.portal.endpoints.skills.settings", SimpleNamespace(SKILLS_DIR=str(global_dir))):
            with patch(
                "app.api.portal.endpoints.personal_skills.get_user_personal_skills_dir",
                return_value=str(personal_root),
            ):
                client = TestClient(_build_app(personal_first=True))
                response = client.post(
                    "/api/portal/skills/personal/import",
                    files={"file": ("imported-personal.zip", archive, "application/zip")},
                    data={"overwrite": "false"},
                )
                assert response.status_code == 200
                payload = response.json()
                assert payload["status"] == "success"
                assert (personal_root / "imported-personal" / "SKILL.md").exists()
                assert not (global_dir / "imported-personal").exists()

"""技能广场接口契约。

两个 GET，没有写接口——安装靠复制提示词，服务端不落任何文件，这条边界值得锁住。

重点盯三件事：

1. **路由不能被 ``GET /skills/{skill_id}`` 吞掉**。``skills.py`` 里有那条路径参数
   路由，FastAPI 按注册顺序匹配，``/skills/gallery`` 必须先注册，否则会被当成
   ``skill_id="gallery"``，列表接口永远 404。
2. **已安装状态按目录名判定**，个人库优先于平台库（平台既有优先级：个人覆盖公共）。
3. **介绍正文只来自本地 docs**，不可能是上游 SKILL.md 原文。
"""

import os
from typing import Any, Dict

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import require_api_key
from app.main import app
from app.services.ai import skill_resolver
from app.services.ai.skill_gallery import catalog as gallery_catalog


@pytest.fixture(autouse=True)
def _clear_installed_cache():
    """已安装扫描有 30 秒进程内缓存：测试之间必须清掉，否则 monkeypatch 不生效。"""
    from app.api.portal.endpoints import skill_gallery as endpoint

    endpoint._installed_cache.clear()
    yield
    endpoint._installed_cache.clear()


@pytest.fixture()
def api_client():
    app.dependency_overrides[require_api_key] = lambda: {"user_id": 7, "role": "user", "username": "tester"}
    # 平台提示词的裁剪会查 element:skills:admin；测试里不连真库，也不去读真实授权。
    from app.core.orm import get_db_session

    async def _fake_session():
        yield None

    app.dependency_overrides[get_db_session] = _fake_session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client():
    """平台管理员视图：role=admin 走 require_permission 的 bypass 口径。"""
    app.dependency_overrides[require_api_key] = lambda: {"user_id": 1, "role": "admin", "username": "root"}
    from app.core.orm import get_db_session

    async def _fake_session():
        yield None

    app.dependency_overrides[get_db_session] = _fake_session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


@pytest.fixture()
def platform_skills_dir(monkeypatch, tmp_path):
    """把公共技能目录指到临时目录。

    ``Settings.SKILLS_DIR`` 是只读 property（容器内固定 /app/data/skills，宿主机回退
    到 ~/.agents/skills），实例上设不了，只能在类上换成新的 property——否则测试结果
    会取决于跑测机器上真实装了哪些技能。
    """
    from app.core.config import settings

    def _use(path):
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(type(settings), "SKILLS_DIR", property(lambda self: str(path)))
        return path

    return _use


@pytest.fixture()
def empty_libraries(monkeypatch):
    """默认让个人库与平台库都为空，避免测试结果取决于跑测机器上的真实技能。"""
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._platform_skill_dirs",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._personal_skill_dirs",
        lambda user: [],
    )


@pytest.mark.asyncio
async def test_list_returns_cards_and_categories(api_client, empty_libraries):
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code == 200
    payload = response.json()
    assert payload["skills"], "技能广场目录不应为空"
    assert payload["categories"]
    assert payload["source_note"]

    first = payload["skills"][0]
    assert first["id"]
    assert first["summary"]
    assert first["install_command"]
    assert "intro" not in first, "列表不该带介绍正文"
    assert first["installed"] is None


@pytest.mark.asyncio
async def test_gallery_route_is_not_swallowed_by_skill_id_route(api_client, empty_libraries):
    """回归保护：``/skills/gallery`` 必须命中列表，而不是被当成 skill_id。"""
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code == 200
    body = response.json()
    # 被 /skills/{skill_id} 吞掉时的表现是 404 + 「技能不存在」
    assert "skills" in body
    assert body.get("detail") is None


@pytest.mark.asyncio
async def test_detail_returns_intro_from_local_doc(api_client, empty_libraries):
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery/pdf")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == "pdf"
    assert detail["intro"]
    # 介绍是我们自己写的导读：不该带上游 SKILL.md 的 frontmatter
    assert not detail["intro"].startswith("---")


# --- 平台提示词按权限下发 -----------------------------------------------------


@pytest.mark.asyncio
async def test_detail_hides_platform_prompt_from_regular_user(api_client, empty_libraries):
    """普通用户拿不到「安装到平台」那段提示词。

    前端按钮已经用 ``canInstallPlatform`` 隐藏，但那段文本本身写着「我确认有管理员
    权限」——原样下发等于把一句可被模型当真的话交给任何登录用户。接口层必须一起裁掉。
    """
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery/pdf")

    detail = response.json()
    assert "install_prompt_global" not in detail
    # 个人版提示词不受影响
    assert detail["install_prompt"]


@pytest.mark.asyncio
async def test_detail_exposes_platform_prompt_to_admin(admin_client, empty_libraries):
    async with admin_client as client:
        response = await client.get("/api/portal/skills/gallery/pdf")

    detail = response.json()
    assert detail["install_prompt_global"]
    assert "scope 传 global" in detail["install_prompt_global"]


@pytest.mark.asyncio
async def test_list_hides_platform_prompt_from_regular_user(api_client, empty_libraries):
    """列表接口也下发 install_prompt_global，裁剪口径必须与详情页一致。"""
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")

    for skill in response.json()["skills"]:
        assert "install_prompt_global" not in skill
        assert skill["install_prompt"]


@pytest.mark.asyncio
async def test_platform_prompt_follows_element_permission_not_role(
    api_client, empty_libraries, monkeypatch
):
    """判定口径是 ``element:skills:admin``：被单独授权的非 admin 角色同样能拿到。"""

    async def _granted(user, db):
        return True

    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._can_manage_platform_skills", _granted
    )

    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery/pdf")

    assert response.json()["install_prompt_global"]


@pytest.mark.asyncio
async def test_detail_unknown_skill_returns_404(api_client, empty_libraries):
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery/no-such-skill")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_installed_prefers_personal_over_platform(api_client, monkeypatch):
    """同一个 ID 两处都有时按 personal 记：个人覆盖公共是平台既有优先级。"""
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._platform_skill_dirs",
        lambda: [("pdf", "/tmp/gallery-test/platform/pdf"), ("docx", "/tmp/gallery-test/platform/docx")],
    )
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._personal_skill_dirs",
        lambda user: [("pdf", "/tmp/gallery-test/personal/pdf")],
    )

    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")
    by_id = {item["id"]: item["installed"] for item in response.json()["skills"]}
    assert by_id["pdf"] == "personal"
    assert by_id["docx"] == "global"
    assert by_id.get("xlsx") is None


@pytest.mark.asyncio
async def test_detail_carries_installed_state(api_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._platform_skill_dirs",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery._personal_skill_dirs",
        lambda user: [("pdf", "/tmp/gallery-test/personal/pdf")],
    )
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery/pdf")
    assert response.json()["installed"] == "personal"


@pytest.mark.asyncio
async def test_requires_login(monkeypatch):
    """不做依赖覆盖时应被鉴权拦下。"""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_gallery_has_no_write_endpoints(api_client, empty_libraries):
    """广场是只读的：安装靠复制提示词，服务端不该有 install/POST 入口。"""
    async with api_client as client:
        response = await client.post("/api/portal/skills/gallery/pdf/install", json={})
    # 405（方法不允许）或 404（没有这条路由）都算合格，只要不是 200
    assert response.status_code in (404, 405)


def test_personal_skill_dirs_uses_resolver_layout(monkeypatch, tmp_path, platform_skills_dir):
    """个人库判定走 ``iter_personal_skill_dirs``，.seed 历史布局也要能识别。"""
    from app.api.portal.endpoints import skill_gallery as endpoint

    platform_skills_dir(tmp_path / "platform-empty")

    root = tmp_path / "skills"
    (root / "pdf").mkdir(parents=True)
    (root / "pdf" / "SKILL.md").write_text("---\nname: pdf\n---\n", encoding="utf-8")
    seed = root / ".seed" / "legacy-skill"
    seed.mkdir(parents=True)
    (seed / "SKILL.md").write_text("---\nname: legacy-skill\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.api.portal.endpoints.skill_gallery.get_user_personal_skills_dir",
        lambda user: str(root),
    )
    ids = {skill_id for skill_id, _path in endpoint._personal_skill_dirs({"user_id": 7})}
    assert "pdf" in ids
    assert "legacy-skill" in ids


def test_platform_skill_dirs_skips_hidden_dirs(tmp_path, platform_skills_dir):
    root = platform_skills_dir(tmp_path / "platform-skills")
    (root / "pdf").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "afile.txt").write_text("x", encoding="utf-8")

    from app.api.portal.endpoints import skill_gallery as endpoint

    ids = {skill_id for skill_id, _path in endpoint._platform_skill_dirs()}
    assert ids == {"pdf"}


@pytest.mark.asyncio
async def test_broken_catalog_returns_500_not_crash(monkeypatch, api_client):
    """目录读取整体失败时给 500 + 人话，而不是把异常抛成堆栈。"""

    def _boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.api.portal.endpoints.skill_gallery.gallery_catalog.list_skills", _boom)

    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code == 500
    assert "技能广场" in response.json()["detail"]


# --- 列表瘦身 / 协商缓存 / 已安装版本 / 行为事件 -----------------------------


@pytest.mark.asyncio
async def test_list_drops_detail_only_fields(api_client, empty_libraries):
    """列表只给卡片字段：详情专用字段与平台提示词一律不下发。

    两份提示词各约 1.5KB，21 条就是 60KB；而卡片只需要摘要、标签和一份个人提示词。
    """
    async with api_client as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code == 200
    first = response.json()["skills"][0]
    for field in (
        "install_prompt_global",
        "intro_doc",
        "source_url",
        "license",
        "updated_at",
        "repository",
        "repository_url",
        "highlights",
    ):
        assert field not in first, f"列表不该下发 {field}"
    # 卡片仍然要能「一键复制」：个人提示词与命令必须留着
    assert first["install_prompt"]
    assert first["install_command"]


@pytest.mark.asyncio
async def test_admin_list_also_hides_platform_prompt(admin_client, empty_libraries):
    """平台提示词只在详情页按权限下发，列表对管理员也不带。"""
    async with admin_client as client:
        response = await client.get("/api/portal/skills/gallery")
    assert response.status_code == 200
    for skill in response.json()["skills"]:
        assert "install_prompt_global" not in skill


@pytest.mark.asyncio
async def test_list_supports_etag_304(api_client, empty_libraries):
    """目录是静态代码资产：带 If-None-Match 命中时回 304，省掉整次传输。"""
    async with api_client as client:
        first = await client.get("/api/portal/skills/gallery")
        etag = first.headers.get("etag")
        assert etag
        second = await client.get(
            "/api/portal/skills/gallery", headers={"If-None-Match": etag}
        )
    assert first.status_code == 200
    assert second.status_code == 304
    assert second.content == b""


def test_installed_version_reads_skill_md(tmp_path, platform_skills_dir):
    """``installed_version`` 读已装技能 SKILL.md 的 frontmatter，不猜目录名。"""
    root = platform_skills_dir(tmp_path / "platform-with-version")
    skill_dir = root / "pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf\nversion: 1.2.3\n---\n", encoding="utf-8"
    )

    from app.api.portal.endpoints import skill_gallery as endpoint

    entries = endpoint._installed_entries({"user_id": 7})
    assert entries["pdf"][0] == "global"
    assert entries["pdf"][2] == "1.2.3"


def test_installed_version_is_empty_when_missing(tmp_path, platform_skills_dir):
    """第三方技能多半不写 version：留空（前端显示「未知」）而不是拿目录名冒充。"""
    root = platform_skills_dir(tmp_path / "platform-no-version")
    skill_dir = root / "pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: pdf\n---\n", encoding="utf-8")

    from app.api.portal.endpoints import skill_gallery as endpoint

    entries = endpoint._installed_entries({"user_id": 7})
    assert entries["pdf"][2] == ""


def test_installed_scan_is_cached(monkeypatch):
    """同一用户连续翻列表不该重复扫目录：30 秒内只扫一次。"""
    from app.api.portal.endpoints import skill_gallery as endpoint

    calls = {"n": 0}

    def _platform_dirs():
        calls["n"] += 1
        return []

    monkeypatch.setattr(endpoint, "_platform_skill_dirs", _platform_dirs)
    monkeypatch.setattr(endpoint, "_personal_skill_dirs", lambda user: [])

    endpoint._installed_entries({"user_id": 7})
    endpoint._installed_entries({"user_id": 7})
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_reports_known_skill_event(api_client):
    """行为事件先落结构化日志：只认白名单里的三种。"""
    async with api_client as client:
        response = await client.post(
            "/api/portal/skills/gallery/events",
            json={"event": "prompt_copy", "skill_id": "pdf", "scope": "personal"},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_rejects_unknown_skill_event(api_client):
    """未登记的事件名直接拒绝，避免前端把任意字符串写进日志。"""
    async with api_client as client:
        response = await client.post(
            "/api/portal/skills/gallery/events", json={"event": "drop_table"}
        )
    assert response.status_code == 422

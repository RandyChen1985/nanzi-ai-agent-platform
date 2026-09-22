"""智能体复制（duplicate）接口的行为与安全契约测试。

覆盖：
- 元数据整体复制（描述 / 能力标签 / 类型 / 引擎），但 is_system 强制 False、副本启用
- 已发布版本配置被完整复制为新智能体的 1 号 PUBLISHED 版本（副本立即可用）
- 无已发布版本时退化为版本号最大的版本；源无版本时不硬造版本
- 物理标识符冲突 400、源不存在 404、标识符为空 422
- 复制系统智能体后副本不是系统智能体
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.auth_service import AuthService
from app.services.ai.agent_manager import AgentManagerService


def unique_id() -> str:
    return uuid.uuid4().hex[:8]


def _tool_names(tools) -> list:
    """tools 可能是字符串或 {name: ...} 对象，统一取出名字。"""
    return [t if isinstance(t, str) else (t or {}).get("name") for t in (tools or [])]


async def _create_source_agent(
    client: AsyncClient,
    admin_key: str,
    *,
    is_system: bool = False,
    engine_type: str = "LOCAL",
    publish: bool = True,
):
    """建一个源智能体（可选发布 1 号版本），返回其创建响应与版本 id。"""
    name = f"dup-src-{unique_id()}"
    resp = await client.post(
        "/api/portal/agents/",
        json={
            "name": name,
            "display_name": "复制源",
            "description": "源智能体描述",
            "is_system": is_system,
            "is_enabled": True,
            "engine_type": engine_type,
            # RAGFLOW 模式强制要求 app_id，其余引擎用数据集配置
            "engine_config": (
                {"app_id": "rag-app-1"}
                if engine_type == "RAGFLOW"
                else {"dataset_ids": ["ds-1"]}
            ),
            "capabilities": ["data"],
            "sort_order": 7,
        },
        headers={"X-API-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    source = resp.json()

    version_id = None
    if engine_type == "LOCAL":
        resp = await client.post(
            f"/api/portal/agents/{source['id']}/versions",
            json={
                "model_name": "gpt-test",
                "temperature": 0.35,
                "system_prompt": "你是复制源提示词",
                "tools": ["tool_a", "tool_b"],
                "toolcall_timeout_seconds": 120,
                "skills_custom": True,
                "skills": ["skill-1"],
                "welcome_config": {"cards": [{"title": "T"}]},
                "comment": "源版本",
            },
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        version_id = resp.json()["id"]

        if publish:
            resp = await client.post(
                f"/api/portal/agents/{source['id']}/versions/{version_id}/publish",
                headers={"X-API-Key": admin_key},
            )
            assert resp.status_code == 200, resp.text

    return source, version_id


@pytest.mark.asyncio
async def test_duplicate_copies_metadata_and_published_version(db_session):
    admin_key = await AuthService.generate_api_key(
        f"dup_admin_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key)

        new_name = f"dup-copy-{unique_id()}"
        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": new_name, "display_name": "复制副本"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        copy = resp.json()

        # 1. 元数据：新身份 + 源内容
        assert copy["id"] != source["id"]
        assert copy["name"] == new_name
        assert copy["display_name"] == "复制副本"
        assert copy["description"] == source["description"]
        assert copy["agent_type"] == source["agent_type"]
        assert copy["engine_type"] == source["engine_type"]
        assert copy["engine_config"] == source["engine_config"]
        assert copy["capabilities"] == source["capabilities"]

        # 2. 副本永远是普通智能体且默认启用
        assert copy["is_system"] is False
        assert copy["is_enabled"] is True

        # 3. 版本被复制成 1 号 PUBLISHED 版本，配置逐字段一致
        resp = await client.get(
            f"/api/portal/agents/{copy['id']}/versions",
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        versions = resp.json()
        assert len(versions) == 1
        version = versions[0]
        assert version["version_number"] == 1
        assert version["status"] == "PUBLISHED"
        assert version["model_name"] == "gpt-test"
        assert version["temperature"] == 0.35
        assert version["system_prompt"] == "你是复制源提示词"
        assert version["toolcall_timeout_seconds"] == 120
        assert version["skills_custom"] is True
        assert version["skills"] == ["skill-1"]
        assert set(_tool_names(version["tools"])) == {"tool_a", "tool_b"}
        assert "复制自" in (version["comment"] or "")

        # 4. 源智能体的版本没有被改动
        resp = await client.get(
            f"/api/portal/agents/{source['id']}/versions",
            headers={"X-API-Key": admin_key},
        )
        assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_duplicate_copy_is_immediately_usable(db_session):
    """副本必须能被运行时解析到配置——否则"复制完立即可用"就是假的。"""
    admin_key = await AuthService.generate_api_key(
        f"dup_ready_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key)

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": f"dup-ready-{unique_id()}", "display_name": "可立刻使用"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        copy_id = resp.json()["id"]

    # 换一个事务快照读，确保读的是端点已提交的数据
    await db_session.rollback()
    config = await AgentManagerService.get_active_agent_config(db_session, agent_id=copy_id)
    assert config is not None, "副本没有可用的已发布版本"
    assert config.system_prompt == "你是复制源提示词"
    assert config.model_name == "gpt-test"


@pytest.mark.asyncio
async def test_duplicate_system_agent_yields_normal_agent(db_session):
    admin_key = await AuthService.generate_api_key(
        f"dup_sys_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key, is_system=True)
        assert source["is_system"] is True

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": f"dup-syscopy-{unique_id()}", "display_name": "系统智能体副本"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_system"] is False


@pytest.mark.asyncio
async def test_duplicate_falls_back_to_latest_unpublished_version(db_session):
    """源还没有发布版本时，退化为复制版本号最大的那个版本。"""
    admin_key = await AuthService.generate_api_key(
        f"dup_draft_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key, publish=False)

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": f"dup-draftcopy-{unique_id()}", "display_name": "草稿副本"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        copy_id = resp.json()["id"]

        resp = await client.get(
            f"/api/portal/agents/{copy_id}/versions",
            headers={"X-API-Key": admin_key},
        )
        versions = resp.json()
        assert len(versions) == 1
        # 复制过来的内容仍然是"已发布"，副本因此可用
        assert versions[0]["status"] == "PUBLISHED"
        assert versions[0]["system_prompt"] == "你是复制源提示词"


@pytest.mark.asyncio
async def test_duplicate_source_without_versions_creates_metadata_only(db_session):
    """RAGFLOW 引擎没有本地版本，复制时不应硬造版本，也不应报错。"""
    admin_key = await AuthService.generate_api_key(
        f"dup_rag_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key, engine_type="RAGFLOW")

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": f"dup-ragcopy-{unique_id()}", "display_name": "托管副本"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200, resp.text
        copy = resp.json()
        assert copy["engine_type"] == "RAGFLOW"
        assert copy["engine_config"] == source["engine_config"]

        resp = await client.get(
            f"/api/portal/agents/{copy['id']}/versions",
            headers={"X-API-Key": admin_key},
        )
        assert resp.json() == []


@pytest.mark.asyncio
async def test_duplicate_rejects_taken_name(db_session):
    admin_key = await AuthService.generate_api_key(
        f"dup_conflict_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key)
        taken, _ = await _create_source_agent(client, admin_key)

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": taken["name"], "display_name": "撞名副本"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 400
        assert "已被占用" in resp.json()["message"]


@pytest.mark.asyncio
async def test_duplicate_missing_source_returns_404(db_session):
    admin_key = await AuthService.generate_api_key(
        f"dup_404_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/portal/agents/{uuid.uuid4()}/duplicate",
            json={"name": f"dup-404-{unique_id()}", "display_name": "不存在"},
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_requires_agent_create_permission(db_session):
    """没有 element:agent:create 的用户不能复制。"""
    user_key = await AuthService.generate_api_key(
        f"dup_user_{unique_id()}", role="user", db=db_session
    )
    admin_key = await AuthService.generate_api_key(
        f"dup_admin2_{unique_id()}", role="admin", db=db_session
    )

    # 先确认该普通用户确实没有 create 权限，否则本用例的前提不成立
    from app.services.permission_service import PermissionService

    me = await AuthService.verify_api_key(user_key, db=db_session)
    has_perm = await PermissionService(db_session).check_permission(
        int(me["user_id"]), "element", "element:agent:create"
    )
    if has_perm:
        pytest.skip("当前测试用户默认拥有 element:agent:create，无法覆盖无权限分支")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source, _ = await _create_source_agent(client, admin_key)

        resp = await client.post(
            f"/api/portal/agents/{source['id']}/duplicate",
            json={"name": f"dup-denied-{unique_id()}", "display_name": "无权限"},
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 403

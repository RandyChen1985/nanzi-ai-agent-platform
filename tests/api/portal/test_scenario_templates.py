import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.agent import AIAgent, AIAgentVersion
from app.models.scenario_template import ScenarioTemplateInstallRun, ScenarioTemplateInstance
from app.core.orm import engine
from app.services.auth_service import AuthService


async def _ensure_scenario_template_tables():
    async with engine.begin() as conn:
        await conn.run_sync(ScenarioTemplateInstance.__table__.create, checkfirst=True)
        await conn.run_sync(ScenarioTemplateInstallRun.__table__.create, checkfirst=True)


@pytest.mark.asyncio
async def test_chatbi_scenario_template_precheck_and_install(db_session):
    await _ensure_scenario_template_tables()
    suffix = str(id(db_session))[-8:]
    admin_key = await AuthService.generate_api_key(
        f"test_scenario_template_admin_{suffix}",
        role="admin",
        db=db_session,
    )
    instance_name = f"test-sales-analysis-{suffix}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_resp = await client.get(
            "/api/portal/scenario-templates",
            headers={"X-API-Key": admin_key},
        )
        assert list_resp.status_code == 200
        templates = list_resp.json()["data"]
        template_ids = {t["id"] for t in templates}
        assert {
            "chatbi-business-analysis",
            "knowledge-qa-assistant",
            "ops-inspection-assistant",
            "finance-expense-analysis",
            "sales-customer-insight",
            "support-ticket-analysis",
            "hr-policy-qa",
            "legal-contract-review",
        }.issubset(template_ids)
        assert len(template_ids) >= 8

        template = next(t for t in templates if t["id"] == "chatbi-business-analysis")
        assert template["target_departments"] == ["经营管理", "销售管理", "财务分析"]
        assert template["delivery_time"] == "0.5-1 天"
        assert template["maturity"] == "标准版"
        assert "Agent" in template["included_capabilities"]
        assert "推荐问题" in template["deliverables"]

        precheck_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/precheck",
            json={"instance_name": instance_name, "display_name": "测试经营分析助手"},
            headers={"X-API-Key": admin_key},
        )
        assert precheck_resp.status_code == 200
        precheck = precheck_resp.json()["data"]
        assert precheck["can_install"] is False
        assert precheck["target_agent_name"] == instance_name
        assert any(check["key"] == "resources" and check["status"] == "error" for check in precheck["checks"])

        payload = {
            "instance_name": instance_name,
            "display_name": "测试经营分析助手",
            "resource_bindings": {
                "metadata_dataset": ["101"],
                "knowledge_base": ["kb_sales_policy", "kb_metric_manual"],
                "owner": "经营部",
            },
        }

        ok_precheck_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/precheck",
            json=payload,
            headers={"X-API-Key": admin_key},
        )
        assert ok_precheck_resp.status_code == 200
        ok_precheck = ok_precheck_resp.json()["data"]
        assert ok_precheck["can_install"] is True

        install_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/install",
            json=payload,
            headers={"X-API-Key": admin_key},
        )
        assert install_resp.status_code == 200
        installed = install_resp.json()["data"]
        assert installed["created"] is True
        assert installed["instance"]["status"] == "installed"
        assert installed["run"]["status"] == "success"
        assert installed["agent"]["name"] == instance_name
        assert installed["version"]["status"] == "PUBLISHED"
        assert installed["resource_bindings"]["metadata_dataset"] == ["101"]
        assert installed["resource_bindings"]["knowledge_base"] == ["kb_sales_policy", "kb_metric_manual"]
        assert installed["enabled_tools"] == ["get_dataset_schema", "execute_sql_query", "search_knowledge_base"]
        assert "本月销售额同比和环比怎么样？" in installed["sample_questions"]
        assert installed["resource_summary"] == [
            {"type": "metadata_dataset", "label": "元数据集", "count": 1, "ids": ["101"], "names": ["101"]},
            {
                "type": "knowledge_base",
                "label": "知识库",
                "count": 2,
                "ids": ["kb_sales_policy", "kb_metric_manual"],
                "names": ["kb_sales_policy", "kb_metric_manual"],
            },
        ]

        duplicate_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/install",
            json=payload,
            headers={"X-API-Key": admin_key},
        )
        assert duplicate_resp.status_code == 200
        duplicate = duplicate_resp.json()["data"]
        assert duplicate["created"] is False
        assert duplicate["agent"]["id"] == installed["agent"]["id"]
        assert duplicate["instance"]["id"] == installed["instance"]["id"]

        instances_resp = await client.get(
            "/api/portal/scenario-templates/instances",
            headers={"X-API-Key": admin_key},
        )
        assert instances_resp.status_code == 200
        instances = instances_resp.json()["data"]
        delivered = next(item for item in instances if item["agent"]["id"] == installed["agent"]["id"])
        assert delivered["template_id"] == "chatbi-business-analysis"
        assert delivered["agent"]["name"] == instance_name
        delivered_metadata = next(item for item in delivered["resource_summary"] if item["type"] == "metadata_dataset")
        assert delivered_metadata["names"] == ["101"]
        assert "本月销售额同比和环比怎么样？" in delivered["sample_questions"]

        instance_detail_resp = await client.get(
            f"/api/portal/scenario-templates/instances/{delivered['id']}",
            headers={"X-API-Key": admin_key},
        )
        assert instance_detail_resp.status_code == 200
        instance_detail = instance_detail_resp.json()["data"]
        assert instance_detail["instance"]["id"] == delivered["id"]
        assert instance_detail["agent"]["id"] == installed["agent"]["id"]
        assert instance_detail["version"]["id"] == installed["version"]["id"]
        assert instance_detail["resource_bindings"]["knowledge_base"] == ["kb_sales_policy", "kb_metric_manual"]
        assert {
            item["type"]: item for item in instance_detail["resource_summary"]
        } == {
            item["type"]: item for item in installed["resource_summary"]
        }

    agent = (
        await db_session.execute(select(AIAgent).where(AIAgent.name == instance_name))
    ).scalar_one()
    versions = (
        await db_session.execute(
            select(AIAgentVersion).where(
                AIAgentVersion.agent_id == agent.id,
                AIAgentVersion.status == "PUBLISHED",
            )
        )
    ).scalars().all()
    assert len(versions) == 1
    assert "经营分析" in versions[0].system_prompt
    assert agent.engine_config["dataset_ids"] == ["kb_sales_policy", "kb_metric_manual"]
    assert agent.engine_config["resource_bindings"]["knowledge_base"] == ["kb_sales_policy", "kb_metric_manual"]
    schema_tool = next(
        item for item in versions[0].tools
        if isinstance(item, dict) and item.get("name") == "get_dataset_schema"
    )
    assert schema_tool["metadata_dataset_ids"] == ["101"]

    instance = (
        await db_session.execute(
            select(ScenarioTemplateInstance).where(
                ScenarioTemplateInstance.template_id == "chatbi-business-analysis",
                ScenarioTemplateInstance.agent_id == agent.id,
            )
        )
    ).scalar_one()
    assert instance.resource_bindings["metadata_dataset"] == ["101"]

    runs = (
        await db_session.execute(
            select(ScenarioTemplateInstallRun).where(
                ScenarioTemplateInstallRun.template_id == "chatbi-business-analysis",
                ScenarioTemplateInstallRun.agent_id == agent.id,
            )
        )
    ).scalars().all()
    assert len(runs) >= 2


@pytest.mark.asyncio
async def test_scenario_template_rejects_foreign_agent_name_collision(db_session):
    await _ensure_scenario_template_tables()
    suffix = str(id(db_session))[-8:]
    admin_key = await AuthService.generate_api_key(
        f"test_scenario_template_collision_admin_{suffix}",
        role="admin",
        db=db_session,
    )
    instance_name = f"test-foreign-agent-{suffix}"
    db_session.add(
        AIAgent(
            id=f"foreign-agent-{suffix}",
            name=instance_name,
            display_name="已有非模板智能体",
            capabilities=["knowledge_search"],
            is_system=False,
            is_enabled=True,
            engine_type="LOCAL",
            engine_config={},
        )
    )
    await db_session.commit()

    payload = {
        "instance_name": instance_name,
        "display_name": "测试经营分析助手",
        "resource_bindings": {"metadata_dataset": ["101"]},
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        precheck_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/precheck",
            json=payload,
            headers={"X-API-Key": admin_key},
        )
        assert precheck_resp.status_code == 200
        precheck = precheck_resp.json()["data"]
        assert precheck["can_install"] is False
        assert any(check["key"] == "agent_name" and check["status"] == "error" for check in precheck["checks"])

        install_resp = await client.post(
            "/api/portal/scenario-templates/chatbi-business-analysis/install",
            json=payload,
            headers={"X-API-Key": admin_key},
        )
        assert install_resp.status_code == 400

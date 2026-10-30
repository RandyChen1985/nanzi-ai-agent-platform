"""工具包导出/导入的真实数据库回归。

纯逻辑用例（``test_tool_bundle_contract.py``）用假会话；这里补的是只有真实 SQL
才能验的部分：名字查重跨两张表、覆盖导入的 UPDATE、以及「凭据不进文件」在真实
行上的表现。

用例自带清理：工具名以 ``bundle_it_`` + 随机串开头，结束时按前缀删除，
不会在开发库里留下配置。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.script_tool import SysScriptTool
from app.models.tool import SysApiTool
from app.services.tool_bundle_service import (
    apply_bundle_import,
    build_bundle,
    normalize_decisions,
    parse_bundle,
    plan_import,
    precheck_bundle,
    validate_plan_names,
)

PREFIX = "bundle_it_"


def _name(tag: str) -> str:
    return f"{PREFIX}{tag}_{uuid.uuid4().hex[:8]}"


async def _cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SysApiTool).where(SysApiTool.name.like(f"{PREFIX}%")))
        await db.execute(delete(SysScriptTool).where(SysScriptTool.name.like(f"{PREFIX}%")))
        await db.commit()


@pytest.fixture(autouse=True)
async def _clean_before_and_after():
    await _cleanup()
    yield
    await _cleanup()


async def test_export_never_carries_real_credential_from_db():
    name = _name("secret")
    async with AsyncSessionLocal() as db:
        row = SysApiTool(
            id=str(uuid.uuid4()),
            name=name,
            description="集成测试",
            method="POST",
            url_template="https://api.example.com/v1/echo",
            headers=json.dumps(
                {"Authorization": "Bearer sk-integration-secret", "Content-Type": "application/json"},
                ensure_ascii=False,
            ),
            parameter_schema="{}",
            is_active=True,
        )
        db.add(row)
        await db.commit()

        bundle = build_bundle(api_rows=[row])
        dumped = json.dumps(bundle, ensure_ascii=False)
        assert "sk-integration-secret" not in dumped
        assert bundle["tools"][0]["headers"]["Authorization"] == "${CREDENTIAL:authorization}"


async def test_precheck_detects_cross_table_name_conflict():
    """同名脚本工具会挡住 API 工具的导入（名字在平台内全局唯一）。"""
    name = _name("cross")
    async with AsyncSessionLocal() as db:
        db.add(
            SysScriptTool(
                id=str(uuid.uuid4()),
                name=name,
                display_name="占用名字",
                source="custom",
                language="python",
                code="print(1)",
                parameter_schema="{}",
                requirements="[]",
                is_active=True,
            )
        )
        await db.commit()

        parsed = parse_bundle(
            build_bundle(
                api_rows=[
                    SysApiTool(
                        id=str(uuid.uuid4()),
                        name=name,
                        method="GET",
                        url_template="https://api.example.com/v1/x",
                        headers="{}",
                        parameter_schema="{}",
                        is_active=True,
                    )
                ]
            )
        )
        checks = await precheck_bundle(db, parsed)
        assert len(checks) == 1
        assert checks[0].status == "conflict"
        assert checks[0].can_overwrite is False, "跨类型同名不能被覆盖"
        assert checks[0].suggested_name and checks[0].suggested_name != name


async def test_overwrite_keeps_existing_credential_and_signing_key():
    name = _name("overwrite")
    async with AsyncSessionLocal() as db:
        row = SysApiTool(
            id=str(uuid.uuid4()),
            name=name,
            description="旧描述",
            method="GET",
            url_template="https://api.example.com/v1/old",
            headers=json.dumps({"Authorization": "Bearer real-token"}, ensure_ascii=False),
            parameter_schema="{}",
            signing_enabled=True,
            signing_key_id=f"{uuid.uuid4().hex[:16]}",
            signing_secret_encrypted="cipher-blob",
            is_active=True,
        )
        db.add(row)
        await db.commit()
        key_id_before = row.signing_key_id

        parsed = parse_bundle(
            build_bundle(
                api_rows=[
                    SysApiTool(
                        id="other",
                        name=name,
                        description="新描述",
                        method="POST",
                        url_template="https://api.example.com/v1/new",
                        headers=json.dumps(
                            {"Authorization": "${CREDENTIAL:authorization}", "X-Trace": "1"},
                            ensure_ascii=False,
                        ),
                        parameter_schema="{}",
                        signing_enabled=False,
                        is_active=True,
                    )
                ]
            )
        )

        checks = await precheck_bundle(db, parsed)
        assert checks[0].status == "conflict" and checks[0].can_overwrite is True

        decisions = normalize_decisions([{"index": 0, "action": "overwrite"}])
        plan = plan_import(parsed, checks, decisions)
        await validate_plan_names(db, plan)
        result = await apply_bundle_import(db, parsed, plan)
        assert result["updated"] == 1
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(SysApiTool).where(SysApiTool.name == name))
        ).scalars().first()
        assert row is not None
        headers = json.loads(row.headers)
        # 覆盖更新了描述与地址
        assert row.description == "新描述"
        assert row.method == "POST"
        assert row.url_template == "https://api.example.com/v1/new"
        # 但凭据与签名密钥保留
        assert headers["Authorization"] == "Bearer real-token"
        assert headers["X-Trace"] == "1"
        assert row.signing_key_id == key_id_before
        assert row.signing_secret_encrypted == "cipher-blob"


async def test_create_import_writes_row_and_reports_pending_credential():
    name = _name("create")
    async with AsyncSessionLocal() as db:
        parsed = parse_bundle(
            build_bundle(
                api_rows=[
                    SysApiTool(
                        id="src",
                        name=name,
                        description="来自别人",
                        method="GET",
                        url_template="https://api.example.com/v1/x",
                        headers=json.dumps({"Authorization": "${CREDENTIAL:authorization}"}, ensure_ascii=False),
                        parameter_schema=json.dumps({"type": "object", "properties": {}}),
                        is_active=True,
                    )
                ]
            )
        )
        checks = await precheck_bundle(db, parsed)
        assert checks[0].status == "new"

        plan = plan_import(parsed, checks, normalize_decisions([{"index": 0, "action": "create"}]))
        await validate_plan_names(db, plan)
        result = await apply_bundle_import(db, parsed, plan)
        assert result["created"] == 1
        assert result["pending_credentials"] == {name: ["Authorization"]}
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(SysApiTool).where(SysApiTool.name == name))
        ).scalars().first()
        assert row is not None
        assert json.loads(row.headers)["Authorization"] == "${CREDENTIAL:authorization}"

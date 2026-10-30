"""工具包导出 / 导入端点。

挂载在 ``/api/portal/tools`` 下，与 ``tools.py``（API 工具）、``script_tools.py``
（脚本工具）同级；单独成模块是因为它同时操作两类工具，塞进任何一边都会让那边继续膨胀。

权限：三个端点都要 ``element:system:config_save``——导出的是配置（含 URL 结构与 header 名），
与写操作同权，避免只读账号把它当「公开数据集」批量搬走。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal.endpoints.tools import SYSTEM_CONFIG_SAVE_PERMISSION
from app.core.orm import get_db_session
from app.models.script_tool import SysScriptTool
from app.models.tool import SysApiTool
from app.services.ai.tools.script_tool_factory import merge_effective_script_tools
from app.services.tool_bundle_service import (
    MAX_BUNDLE_BYTES,
    ToolBundleError,
    apply_bundle_import,
    bundle_filename,
    build_bundle,
    normalize_decisions,
    parse_bundle,
    plan_import,
    precheck_bundle,
    validate_plan_names,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_EXPORT_KINDS = ("api", "script", "all")


class ToolBundleImportRequest(BaseModel):
    bundle: Dict[str, Any] = Field(..., description="导出得到的完整 JSON 对象")
    decisions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="逐条决策：[{index, action: create|skip|overwrite|rename, rename_to?}]",
    )


class ToolBundlePrecheckRequest(BaseModel):
    bundle: Dict[str, Any] = Field(..., description="导出得到的完整 JSON 对象")


def _bundle_size(payload: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:  # 极少数情况下 handler 收到不可序列化对象
        raise HTTPException(status_code=400, detail="工具包内容无法序列化") from exc


def _check_size(payload: Dict[str, Any]) -> None:
    size = _bundle_size(payload)
    if size > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"工具包过大（{size} 字节），上限 {MAX_BUNDLE_BYTES} 字节",
        )


def _parse_or_400(payload: Dict[str, Any]):
    _check_size(payload)
    try:
        return parse_bundle(payload)
    except ToolBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _split_ids(raw: Optional[str]) -> Optional[set[str]]:
    if not raw:
        return None
    ids = {token.strip() for token in raw.split(",") if token.strip()}
    return ids or None


@router.get("/export", summary="导出工具为 JSON 文件")
async def export_tools(
    kind: str = Query("all", description="api | script | all"),
    ids: Optional[str] = Query(None, description="逗号分隔的工具 ID；留空表示该类型全部"),
    db: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    export_kind = str(kind or "all").strip().lower()
    if export_kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=400, detail=f"未知的 kind：{kind!r}")
    wanted = _split_ids(ids)

    api_rows: List[SysApiTool] = []
    script_effectives = []

    if export_kind in ("api", "all"):
        stmt = select(SysApiTool).order_by(SysApiTool.created_at.desc())
        if wanted is not None:
            stmt = select(SysApiTool).where(SysApiTool.id.in_(wanted)).order_by(
                SysApiTool.created_at.desc()
            )
        api_rows = list((await db.execute(stmt)).scalars().all())

    if export_kind in ("script", "all"):
        rows = list(
            (
                await db.execute(select(SysScriptTool).order_by(SysScriptTool.created_at.desc()))
            )
            .scalars()
            .all()
        )
        script_effectives = list(merge_effective_script_tools(rows))
        if wanted is not None:
            script_effectives = [item for item in script_effectives if item.id in wanted]

    bundle = build_bundle(
        api_rows=api_rows,
        script_effectives=script_effectives,
        exported_by=(user or {}).get("username"),
    )
    body = json.dumps(bundle, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle_filename()}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/import/precheck", summary="导入前预检（不落库）")
async def precheck_tools_import(
    payload: ToolBundlePrecheckRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    parsed = _parse_or_400(payload.bundle)
    checks = await precheck_bundle(db, parsed)
    summary = {"new": 0, "conflict": 0, "invalid": 0}
    for item in checks:
        summary[item.status] = summary.get(item.status, 0) + 1
    return {
        "ok": True,
        "exported_at": parsed.exported_at,
        "exported_by": parsed.exported_by,
        "total": len(checks),
        "summary": summary,
        "items": [item.as_dict() for item in checks],
    }


@router.post("/import", summary="导入工具包（单事务）")
async def import_tools(
    payload: ToolBundleImportRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    parsed = _parse_or_400(payload.bundle)
    try:
        decisions = normalize_decisions(payload.decisions)
        checks = await precheck_bundle(db, parsed)
        plan = plan_import(parsed, checks, decisions)
        await validate_plan_names(db, plan)
        result = await apply_bundle_import(db, parsed, plan)
    except ToolBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 落库阶段异常统一回滚并给出可读提示
        logger.exception("[tool_bundle] 导入失败")
        raise HTTPException(status_code=400, detail=f"导入失败：{exc}") from exc

    return result

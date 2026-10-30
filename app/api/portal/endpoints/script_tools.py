"""脚本工具管理端点（与 API 工具并列的第二种自定义工具）。

挂载在 ``/api/portal/tools`` 下，路径以 ``/script`` 开头，与 ``/tools/mcp``、
``/tools/forbidden-catalog`` 同级；写在独立模块里是为了让 ``tools.py``
（API 工具 + MCP + 禁用目录）不再继续膨胀。

权限口径与 API 工具完全一致：读=登录用户，写=``element:system:config_save``。
内置脚本（``source='builtin'``）不能改名或删除，但可以改代码/参数/依赖——保存后
标记为「已覆盖」；只改分组/描述时不会写入 code，避免误冻结内置版本。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key, require_permission
from app.core.orm import get_db_session
from app.models.script_tool import SysScriptTool
from app.schemas.script_tool import (
    ScriptToolCapabilities,
    SysScriptToolBatchGroupRequest,
    SysScriptToolCreate,
    SysScriptToolPreviewRequest,
    SysScriptToolPreviewResponse,
    SysScriptToolResponse,
    SysScriptToolUpdate,
)
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.script_tool_builtins import (
    BuiltinScriptTool,
    get_builtin_script_tool,
)
from app.services.ai.tools.script_tool_factory import (
    EffectiveScriptTool,
    config_from_row,
    merge_effective_script_tools,
    runtime_metadata_from_row,
)
from app.services.config_service import ConfigService, resolve_effective_sandbox_policy
from app.services.tool_bundle_service import name_conflict_reason

logger = logging.getLogger(__name__)
router = APIRouter()

SANDBOX_POLICY_KEY = "sandbox_policy"


# --------------------------------------------------------------------- 工具函数


def _serialize(row: SysScriptTool) -> SysScriptToolResponse:
    builtin = get_builtin_script_tool(row.name) if row.source == "builtin" else None
    schema, requirements, timeout_seconds = runtime_metadata_from_row(row, builtin=builtin)
    if isinstance(schema, str):
        try:
            schema = json.loads(schema) if schema else {}
        except ValueError:
            schema = {}
    return SysScriptToolResponse(
        id=row.id,
        name=row.name,
        display_name=row.display_name or (builtin.display_name if builtin else None),
        description=row.description or (builtin.description if builtin else None),
        source=row.source or "custom",
        language=row.language or "python",
        # 内置行：库里存了 code 就是「已覆盖」，否则回落到代码库版本
        code=row.code or (builtin.code if builtin is not None else None),
        is_overridden=bool(row.code) and builtin is not None,
        parameter_schema=schema or {},
        requirements=list(requirements),
        timeout_seconds=timeout_seconds,
        is_active=bool(row.is_active),
        group_name=row.group_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _assert_name_available(db: AsyncSession, name: str, *, exclude_id: Optional[str] = None) -> None:
    """工具名全平台唯一：registry 按 name 解析，重名会出现「命中谁看运气」。

    规则集中在 ``tool_bundle_service.name_conflict_reason``：新建、改名与导入预检
    必须同一口径，否则会出现「预检说能导、保存却报重名」。
    """
    reason = await name_conflict_reason(db, name, exclude_id=exclude_id)
    if reason:
        raise HTTPException(status_code=400, detail=reason)


def _schema_json(payload_schema: Any) -> Optional[str]:
    if payload_schema is None:
        return None
    return json.dumps(payload_schema, ensure_ascii=False)


async def _get_row_or_404(db: AsyncSession, tool_id: str) -> SysScriptTool:
    row = (await db.execute(select(SysScriptTool).where(SysScriptTool.id == tool_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="脚本工具不存在")
    return row


async def current_sandbox_policy() -> str:
    try:
        raw = await ConfigService.get(SANDBOX_POLICY_KEY, default="local")
    except Exception:
        raw = "local"
    return resolve_effective_sandbox_policy(raw, "local")


def _capabilities_for(policy: str) -> ScriptToolCapabilities:
    if policy in {"e2b", "ssh"}:
        return ScriptToolCapabilities(
            sandbox_policy=policy,
            sandbox_label="远端沙箱",
            sandbox_enabled=True,
            script_tool_supported=False,
            notice=(
                f"当前沙箱策略（{policy}）暂不支持脚本工具：脚本需要把会话工作区路径映射进沙箱，"
                "该策略暂无此映射。改用 docker / k8s 策略后即可使用。"
            ),
        )
    sandboxed = policy in {"docker", "k8s"}
    if sandboxed:
        return ScriptToolCapabilities(
            sandbox_policy=policy,
            sandbox_label="会话沙箱",
            sandbox_enabled=True,
            notice=(
                "脚本在会话沙箱内执行：工作区挂载的是你的用户目录（同一账号的其他会话也能读到），"
                "无法访问平台后端进程、数据库或你的本机文件。"
            ),
        )
    return ScriptToolCapabilities(
        sandbox_policy=policy,
        sandbox_label="本地工作区",
        sandbox_enabled=False,
        notice=(
            "当前部署未启用容器沙箱（本地策略）：脚本以后端进程的权限运行，不是安全边界——"
            "可读环境变量、数据库连接等。请只运行你自己写的或完全信任的脚本。"
        ),
    )


# --------------------------------------------------------------------- 端点


async def apply_update_fields(
    db: AsyncSession, row: SysScriptTool, payload: SysScriptToolUpdate
) -> None:
    """把 update 载荷落到行上。

    ``model_fields_set`` 用来区分「没传这个字段」和「显式传了 null」：前端清空
    分组/超时/描述时提交的是 null，只看 ``is not None`` 会静默忽略，用户以为
    保存成功、实际旧值还在。
    """
    # 用 model_fields_set 区分「没传这个字段」和「显式传了 null」：
    # 前端清空分组/超时/描述时提交的是 null，只看 `is not None` 会静默忽略，
    # 用户以为保存成功、实际旧值还在。
    explicit = payload.model_fields_set
    builtin = get_builtin_script_tool(row.name) if row.source == "builtin" else None

    # 内置脚本的运行配置（参数/依赖/超时）被改动时，同样要落 code 标记覆盖：
    # 否则页面没有「已覆盖」提示，平台后续改内置 schema 也会被这行旧配置挡住。
    behavioral_changed = False
    if builtin is not None and row.code is None:
        # 与列表/运行时同一口径：未覆盖的内置行以代码库元数据为准。否则老环境
        # 的 seed schema 与代码库不一致时，用户只改描述也会被误判成改参数。
        current_schema, current_requirements, current_timeout = runtime_metadata_from_row(
            row, builtin=builtin
        )
        if "parameter_schema" in explicit and payload.parameter_schema is not None:
            if payload.parameter_schema != current_schema:
                behavioral_changed = True
        if "requirements" in explicit and payload.requirements is not None:
            if tuple(payload.requirements) != tuple(current_requirements):
                behavioral_changed = True
        if "timeout_seconds" in explicit and payload.timeout_seconds != current_timeout:
            behavioral_changed = True
        if behavioral_changed:
            # 存代码库原码而不是空串：运行时会以库里的 code 为准，
            # reset-to-builtin 再把它清回 NULL。
            row.code = builtin.code

    if "name" in explicit and payload.name is not None and payload.name != row.name:
        await _assert_name_available(db, payload.name, exclude_id=row.id)
        row.name = payload.name
    if "display_name" in explicit:
        row.display_name = payload.display_name
    if "description" in explicit:
        row.description = payload.description
    if "language" in explicit and payload.language is not None:
        row.language = payload.language
    if "code" in explicit and payload.code is not None:
        # 内置脚本：编辑分组/描述时前端也会把完整 code 一起提交；如果内容和
        # 代码库版本一致，不能落库，否则会误标「已覆盖」并冻结后续内置升级。
        if builtin is None or row.code is not None or payload.code != builtin.code:
            row.code = payload.code
    if "parameter_schema" in explicit and payload.parameter_schema is not None:
        row.parameter_schema = _schema_json(payload.parameter_schema)
    if "requirements" in explicit and payload.requirements is not None:
        row.requirements = json.dumps(list(payload.requirements), ensure_ascii=False)
    if "timeout_seconds" in explicit:
        # None = 跟随平台配置
        row.timeout_seconds = payload.timeout_seconds
    if "is_active" in explicit and payload.is_active is not None:
        row.is_active = payload.is_active
    if "group_name" in explicit:
        row.group_name = (payload.group_name or "").strip() or None


def ensure_editable(row: SysScriptTool, payload: SysScriptToolUpdate) -> None:
    """内置脚本可改代码/参数/依赖，但不能改名。

    名字是内置脚本的身份：改名会让「恢复内置默认」和 registry 的内置兜底匹配失效，
    也会在智能体配置里留下一个「看起来是内置、其实不对应任何内置定义」的工具。
    """
    if row.source != "builtin":
        return
    if payload.name is not None and payload.name != row.name:
        raise HTTPException(
            status_code=403,
            detail="内置脚本不能改名（会失去与内置版本的对应关系）；代码与参数可以改",
        )


def apply_builtin_defaults(row: SysScriptTool, builtin: BuiltinScriptTool) -> None:
    """把行的可变部分恢复成代码库里的内置定义（code 置空 = 用代码库源码）。"""
    row.code = None
    row.parameter_schema = json.dumps(builtin.parameter_schema, ensure_ascii=False)
    row.requirements = json.dumps(list(builtin.requirements), ensure_ascii=False)
    row.timeout_seconds = builtin.timeout_seconds
    row.description = builtin.description


@router.get("/script/capabilities", response_model=ScriptToolCapabilities)
async def script_tool_capabilities(user: Dict = Depends(require_api_key)):
    """前端据此如实显示「沙箱内执行」或「本地工作区执行」。"""
    return _capabilities_for(await current_sandbox_policy())


@router.get("/script", response_model=List[SysScriptToolResponse])
async def list_script_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """列出脚本工具（含未落库的内置定义），供注册表页与智能体配置勾选。"""
    try:
        rows = (
            await db.execute(select(SysScriptTool).order_by(SysScriptTool.created_at.desc()))
        ).scalars().all()
    except Exception:  # noqa: BLE001 - 迁移尚未执行时表不存在，退回内置定义
        logger.warning("[script_tool] sys_script_tools 不可读，列表退回内置定义（迁移是否已执行？）")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        rows = []
    effective = merge_effective_script_tools(list(rows))
    # 库里真有这一行才算「可操作」；否则是代码库兜底（迁移未执行）
    existing_names = {row.name for row in rows}
    return [_response_from_effective(item, existing_names) for item in effective]


def _response_from_effective(
    item: EffectiveScriptTool, existing_names: set[str]
) -> SysScriptToolResponse:
    """生效工具 → 接口响应。

    ``is_fallback`` 由「库里有没有这一行」决定，不能靠 id 前缀：库里的内置行
    id 本来就是 ``builtin-script-*``，用前缀判断会把正常行也标成兜底，
    页面于是永远提示「迁移未执行」并锁死新建入口。
    """
    return SysScriptToolResponse(
        id=item.id,
        name=item.name,
        display_name=item.display_name,
        description=item.description,
        source=item.source,
        language=item.language,
        code=item.code,
        is_overridden=item.is_overridden,
        is_fallback=item.name not in existing_names,
        parameter_schema=item.parameter_schema or {},
        requirements=list(item.requirements),
        timeout_seconds=item.timeout_seconds,
        is_active=item.is_active,
        group_name=item.group_name,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("/script", response_model=SysScriptToolResponse)
async def create_script_tool(
    payload: SysScriptToolCreate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    await _assert_name_available(db, payload.name)
    row = SysScriptTool(
        id=str(uuid.uuid4()),
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        source="custom",
        language=payload.language,
        code=payload.code,
        parameter_schema=_schema_json(payload.parameter_schema),
        requirements=json.dumps(list(payload.requirements), ensure_ascii=False),
        timeout_seconds=payload.timeout_seconds,
        group_name=payload.group_name,
        is_active=payload.is_active,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="工具名已存在") from exc
    await db.refresh(row)
    ToolRegistry.clear_db_tool_cache()
    return _serialize(row)


@router.put("/script/{tool_id}", response_model=SysScriptToolResponse)
async def update_script_tool(
    tool_id: str,
    payload: SysScriptToolUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    row = await _get_row_or_404(db, tool_id)

    ensure_editable(row, payload)

    await apply_update_fields(db, row, payload)

    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="工具名已存在") from exc
    await db.refresh(row)
    ToolRegistry.clear_db_tool_cache()
    return _serialize(row)


@router.post("/script/{tool_id}/reset-to-builtin", response_model=SysScriptToolResponse)
async def reset_script_tool_to_builtin(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """把内置脚本恢复成代码库里的版本（清空库里覆盖的代码与参数）。

    这是「可编辑内置」的安全阀：改坏了、或者想接收平台后续修复，一键回到默认。
    """
    row = await _get_row_or_404(db, tool_id)
    builtin = get_builtin_script_tool(row.name) if row.source == "builtin" else None
    if builtin is None:
        raise HTTPException(status_code=400, detail="只有内置脚本可以恢复默认版本")

    apply_builtin_defaults(row, builtin)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    ToolRegistry.clear_db_tool_cache()
    return _serialize(row)


@router.delete("/script/{tool_id}")
async def delete_script_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    row = await _get_row_or_404(db, tool_id)
    if (row.source or "custom") == "builtin":
        raise HTTPException(status_code=403, detail="内置脚本不能删除，可将其停用")
    await db.delete(row)
    await db.commit()
    ToolRegistry.clear_db_tool_cache()
    return {"ok": True}


@router.post("/script/batch-group")
async def batch_set_script_group(
    payload: SysScriptToolBatchGroupRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    rows = (
        await db.execute(select(SysScriptTool).where(SysScriptTool.id.in_(payload.ids)))
    ).scalars().all()
    for row in rows:
        row.group_name = payload.group_name
        db.add(row)
    await db.commit()
    ToolRegistry.clear_db_tool_cache()
    return {"ok": True, "updated": len(rows)}


@router.post("/script/preview", response_model=SysScriptToolPreviewResponse)
async def preview_script_tool(
    payload: SysScriptToolPreviewRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """试运行：走与真实调用完全相同的执行器，但落在预览目录、不进沙箱。

    试跑不写库；``tool_id`` 存在时用库里的代码，允许用提交的字段覆盖。
    """
    from dataclasses import replace as dataclass_replace

    from app.services.ai.tools.script_tool_runtime import (
        ScriptToolConfig,
        ScriptToolError,
        ScriptToolRunner,
    )

    row: Optional[SysScriptTool] = None
    if payload.tool_id:
        row = await _get_row_or_404(db, payload.tool_id)

    builtin = get_builtin_script_tool(row.name) if row is not None else None
    try:
        if row is not None:
            config = config_from_row(row, builtin=builtin)
        else:
            if not payload.code or not payload.code.strip():
                raise ScriptToolError("请先填写脚本代码再试运行")
            config = ScriptToolConfig(
                name="preview_script",
                code=payload.code,
                parameter_schema=payload.parameter_schema,
                requirements=tuple(payload.requirements),
                timeout_seconds=payload.timeout_seconds,
                language=payload.language,
            )
    except ScriptToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 允许用草稿字段覆盖已保存配置（编辑器里改了还没保存也能试跑）
    overrides: Dict[str, Any] = {}
    if payload.code and payload.code.strip():
        overrides["code"] = payload.code
    if payload.parameter_schema:
        overrides["parameter_schema"] = payload.parameter_schema
    if payload.requirements:
        overrides["requirements"] = tuple(payload.requirements)
    if payload.timeout_seconds:
        overrides["timeout_seconds"] = payload.timeout_seconds
    if overrides:
        config = dataclass_replace(config, **overrides)

    workdir = await _preview_workdir(user)
    runner = ScriptToolRunner(config, workdir=workdir, sandbox_ws=None, sandbox_root=None, user_info=user)
    policy = await current_sandbox_policy()

    try:
        result = await runner.run(payload.params or {})
    except ScriptToolError as exc:
        return SysScriptToolPreviewResponse(
            ok=False,
            sandbox_policy=policy,
            message=str(exc),
        )

    return SysScriptToolPreviewResponse(
        ok=result.ok,
        exit_code=result.exit_code,
        stdout=result.stdout[:20000],
        stderr=result.stderr[:8000],
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        sandbox_policy=policy,
        message=result.message,
    )


async def _preview_workdir(user: Dict[str, Any]) -> Path:
    """试运行目录：与真实会话工作区隔离，避免试跑污染用户产出。"""
    from app.services.ai.runtime.agentscope.workspace import (
        default_workspace_root,
        resolve_workspace_root,
    )

    try:
        root = await resolve_workspace_root()
    except Exception:
        root = default_workspace_root()
    user_key = str(user.get("user_id") or user.get("id") or "anonymous")
    safe_key = "".join(ch for ch in user_key if ch.isalnum() or ch in ("-", "_")) or "anonymous"
    workdir = Path(root) / "_script_tool_preview" / safe_key
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir

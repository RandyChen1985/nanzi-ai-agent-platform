from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from sqlalchemy import select, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import re
import uuid
import json

import httpx

from app.core.dependencies import require_admin, require_permission, require_api_key
from app.core.orm import get_db_session
from app.models.tool import SysApiTool
from app.models.script_tool import SysScriptTool
from app.services.ai.tools.script_tool_factory import merge_effective_script_tools
from app.models.mcp import McpServer, McpToolCache
from app.schemas.tool import (
    SysApiToolBatchGroupRequest,
    SysApiToolCreate,
    SysApiToolPreviewRequest,
    SysApiToolPreviewRequestInfo,
    SysApiToolPreviewResponse,
    SysApiToolResponse,
    SysApiToolUpdate,
)
from app.services.ai.tools.forbidden_catalog import (
    BUILTIN_FORBIDDEN_TOOL_CANDIDATES,
    api_tool_group,
    mcp_tool_group,
    script_tool_group,
    ForbiddenToolCandidate,
    merge_forbidden_tool_candidates,
)
from app.services.ai.tools.generic_api import GenericApiToolFactory, UnsupportedMethodError
from app.services.ai.tools.request_signing import (
    SIGNATURE_ALGORITHM,
    SigningConfigError,
    encrypt_signing_secret,
    validate_no_reserved_signing_headers,
    validate_signing_key_id,
    validate_signing_secret,
)
from app.services.ai.tools.system_params import SystemParamConfigError, validate_system_params
from app.services.ai.tools.user_placeholders import UserPlaceholderError
from app.utils.masking import mask_sensitive_data

router = APIRouter()

logger = logging.getLogger(__name__)

# 注册表写操作所需的权限依赖抽成具名对象：避免同一字符串散落多处，也便于测试覆盖。
SYSTEM_CONFIG_SAVE_PERMISSION = require_permission("element", "element:system:config_save")


def _validate_signing_algorithm(value: Any) -> str:
    algorithm = str(value or SIGNATURE_ALGORITHM).strip().lower()
    if algorithm != SIGNATURE_ALGORITHM:
        raise SigningConfigError(f"不支持的签名算法：{algorithm}")
    return algorithm


async def _ensure_signing_key_id_available(
    db: AsyncSession,
    key_id: str | None,
    *,
    exclude_tool_id: str | None = None,
) -> None:
    """提前给出可读错误；最终唯一性由数据库唯一索引兜底。"""
    if not key_id:
        return
    stmt = select(SysApiTool).where(SysApiTool.signing_key_id == key_id)
    if exclude_tool_id:
        stmt = stmt.where(SysApiTool.id != exclude_tool_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise SigningConfigError(f"签名 Key-Id 已被工具「{existing.name}」使用，请重新生成")


def _prepare_signing_for_create(payload: SysApiToolCreate) -> dict[str, Any]:
    data = payload.model_dump()
    secret = data.pop("signing_secret", None)
    algorithm = _validate_signing_algorithm(data.get("signing_algorithm"))

    if data.get("signing_enabled"):
        key_id = validate_signing_key_id(data.get("signing_key_id"))
        secret = validate_signing_secret(secret)
        data["signing_key_id"] = key_id
        data["signing_algorithm"] = algorithm
        data["signing_secret_encrypted"] = encrypt_signing_secret(secret)
    else:
        # 关闭签名时忽略密钥配置：不加密、不落库，也不因为前端回传了旧字段而拒绝保存。
        data["signing_key_id"] = None
        data["signing_secret_encrypted"] = None
        data["signing_algorithm"] = algorithm

    validate_no_reserved_signing_headers(payload.headers)
    return data


def _apply_signing_for_update(tool: SysApiTool, payload: SysApiToolUpdate) -> dict[str, Any]:
    update_data = payload.model_dump(exclude_unset=True)
    secret = update_data.pop("signing_secret", None)
    # 显式关闭时忽略密钥字段，但保留已保存密钥（重新开启即可复用）。
    if "signing_enabled" in update_data and not update_data["signing_enabled"]:
        update_data.pop("signing_key_id", None)
        update_data["signing_secret_encrypted"] = tool.signing_secret_encrypted
        if "signing_algorithm" in update_data:
            update_data["signing_algorithm"] = _validate_signing_algorithm(
                update_data.get("signing_algorithm")
            )
        if "headers" in update_data:
            validate_no_reserved_signing_headers(update_data.get("headers"))
        return update_data

    has_secret = bool(secret)
    has_key_id = "signing_key_id" in update_data

    if has_secret or has_key_id:
        if not update_data.get("signing_key_id"):
            raise SigningConfigError("轮换签名密钥必须同时提供新的 Key-Id 和密钥")
        key_id = validate_signing_key_id(update_data["signing_key_id"])
        update_data["signing_key_id"] = key_id
        if has_secret:
            if key_id == str(tool.signing_key_id or ""):
                # Key-Id 是业务方选密钥的版本标识；换了密钥却沿用旧 Key-Id，
                # 业务方无法灰度/区分新旧密钥，因此轮换必须同时换版本。
                raise SigningConfigError("轮换签名密钥必须同时提供新的 Key-Id 和密钥")
            update_data["signing_secret_encrypted"] = encrypt_signing_secret(
                validate_signing_secret(secret)
            )
        elif key_id != str(tool.signing_key_id or ""):
            # 只改 Key-Id 不改密钥会让业务方拿到一把对不上的 key，必须拦住；
            # 同一个 Key-Id 随表单回传是正常保存，不算轮换。
            raise SigningConfigError("轮换签名密钥必须同时提供新的 Key-Id 和密钥")

    if "signing_algorithm" in update_data:
        update_data["signing_algorithm"] = _validate_signing_algorithm(
            update_data.get("signing_algorithm")
        )

    effective_enabled = (
        update_data["signing_enabled"]
        if "signing_enabled" in update_data and update_data["signing_enabled"] is not None
        else bool(tool.signing_enabled)
    )
    if effective_enabled:
        effective_key_id = update_data.get("signing_key_id", tool.signing_key_id)
        effective_secret = update_data.get(
            "signing_secret_encrypted", tool.signing_secret_encrypted
        )
        validate_signing_key_id(effective_key_id)
        if not effective_secret:
            raise SigningConfigError("已开启请求签名，但没有配置签名密钥")

    if "headers" in update_data:
        validate_no_reserved_signing_headers(update_data.get("headers"))
    return update_data


async def _resolve_preview_signing(
    payload: SysApiToolPreviewRequest,
    db: AsyncSession,
) -> dict[str, Any]:
    """解析试跑草稿的签名配置：新草稿用本次密钥，已保存工具读库解密。"""
    if not payload.signing_enabled:
        return {
            "signing_enabled": False,
            "signing_key_id": None,
            "signing_algorithm": SIGNATURE_ALGORITHM,
            "signing_secret_encrypted": None,
        }

    algorithm = _validate_signing_algorithm(payload.signing_algorithm)
    if payload.signing_secret:
        key_id = validate_signing_key_id(payload.signing_key_id)
        return {
            "signing_enabled": True,
            "signing_key_id": key_id,
            "signing_algorithm": algorithm,
            "signing_secret_encrypted": encrypt_signing_secret(
                validate_signing_secret(payload.signing_secret)
            ),
        }

    if not payload.tool_id:
        raise SigningConfigError("已开启请求签名，但没有提供签名密钥或已保存的工具 ID")

    tool = await db.get(SysApiTool, payload.tool_id)
    if not tool:
        raise SigningConfigError("要试跑的工具不存在，无法读取已保存的签名密钥")
    if not tool.signing_secret_encrypted:
        raise SigningConfigError("该工具没有已保存的签名密钥，请先填写或重新生成密钥")
    saved_key_id = validate_signing_key_id(tool.signing_key_id)
    if payload.signing_key_id and payload.signing_key_id != saved_key_id:
        raise SigningConfigError("试跑提交的 Key-Id 与工具已保存的 Key-Id 不一致")
    return {
        "signing_enabled": True,
        "signing_key_id": saved_key_id,
        "signing_algorithm": algorithm,
        "signing_secret_encrypted": tool.signing_secret_encrypted,
    }

def _visible_published_mcp_tool_stmt(user: Dict[str, Any]):
    """已发布且可用的 MCP 工具：平台公共服务 + 当前登录用户的个人私有服务。"""
    from sqlalchemy.orm import joinedload

    current_user_id = user.get("user_id") if user.get("user_id") is not None else user.get("id")
    try:
        current_user_id = int(current_user_id) if current_user_id is not None else None
    except Exception:
        pass

    global_cond = or_(McpServer.scope == "global", McpServer.scope.is_(None))
    personal_cond = and_(McpServer.scope == "personal", McpServer.user_id == current_user_id)

    return (
        select(McpToolCache)
        .join(McpToolCache.server)
        .options(joinedload(McpToolCache.server))
        .where(
            McpToolCache.is_published == True,  # noqa: E712 - 与既有查询写法一致
            McpToolCache.is_available == True,  # noqa: E712
            McpServer.enabled_status == 1,
            or_(global_cond, personal_cond),
        )
    )


async def _list_visible_published_mcp_tools(db: AsyncSession, user: Dict[str, Any]):
    result = await db.execute(_visible_published_mcp_tool_stmt(user))
    return result.scalars().all()


@router.get("/mcp", response_model=List[Dict[str, Any]])
async def list_published_mcp_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """列出已发布的 MCP 工具：包含平台公共 MCP 工具，以及当前登录用户自己创建的个人私有 MCP 工具。"""
    tools = await _list_visible_published_mcp_tools(db, user)

    return [
        {
            "id": t.id,
            "name": t.tool_name,
            "description": t.tool_description,
            "server_name": t.server.server_name if t.server else "Unknown",
            "server_remark": (t.server.remark if t.server else None) or None,
            "scope": t.server.scope if (t.server and t.server.scope) else "global",
            "parameter_schema": json.loads(t.parameter_schema or "{}")
        } for t in tools
    ]


def _list_forbidden_skill_candidates() -> list[dict[str, Any]]:
    """可禁用的技能候选：只列**已启用的平台（全局）技能**。

    个人技能属于用户自己的资源，不在管理员配置范围内；而列出本来就已经禁用的技能也没有
    意义（禁用是为了拦住"目前能用"的技能）。
    """
    from app.services.ai.skill_resolver import list_global_skill_metas
    from app.utils.skill_metadata import is_skill_enabled

    candidates: list[dict[str, Any]] = []
    for meta in list_global_skill_metas():
        skill_id = str(meta.get("id") or "").strip()
        if not skill_id or not is_skill_enabled(meta):
            continue
        candidates.append(
            {
                "id": skill_id,
                "label": str(meta.get("name") or skill_id),
                "description": str(meta.get("description") or ""),
                # 技能暂不细分来源，统一一个分组；仍带 group_key/group 以便前端复用同一套分组渲染
                "group_key": "skill:__all__",
                "group": "公共技能",
            }
        )
    return candidates


@router.get("/forbidden-catalog", response_model=Dict[str, Any])
async def list_forbidden_tool_catalog(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:user:edit")),
):
    """返回可禁用的工具候选：内置高危工具（含运行时别名）+ 自定义 API 与已发布 MCP 工具 + 公共技能。

    只读、幂等；不返回任何用户已配置的禁用项（已选项由权限接口回显）。
    权限与"保存禁用项"的接口保持一致：能配禁用才需要看候选，避免任意登录用户枚举全部工具/MCP/技能清单。
    """
    api_tools = (
        await db.execute(select(SysApiTool).order_by(SysApiTool.created_at.desc()))
    ).scalars().all()
    mcp_tools = await _list_visible_published_mcp_tools(db, user)

    # 分组字段（v2）：API 按业务分组、MCP 按服务名，缺失时各自兜底。
    dynamic_candidates: list[ForbiddenToolCandidate] = []
    for tool in api_tools:
        name = str(tool.name or "").strip()
        if not name:
            continue
        group_key, group = api_tool_group(getattr(tool, "group_name", None))
        dynamic_candidates.append(
            ForbiddenToolCandidate(
                id=name,
                label=name,
                description=str(tool.description or "自定义 API 工具"),
                category="api",
                aliases=(name,),
                group_key=group_key,
                group=group,
            )
        )
    try:
        script_rows = (
            await db.execute(select(SysScriptTool).order_by(SysScriptTool.created_at.desc()))
        ).scalars().all()
    except Exception:  # noqa: BLE001 - 迁移漏跑时不能让整个禁用目录 500
        # 与 list_script_tools 同口径：表不存在时仍返回内置脚本候选。
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        script_rows = []
    for script in merge_effective_script_tools(list(script_rows)):
        if not script.is_active:
            # 停用的工具本来就不会被智能体拿到，列进「可禁用」只会让管理员困惑
            continue
        group_key, group = script_tool_group(script.group_name)
        dynamic_candidates.append(
            ForbiddenToolCandidate(
                id=script.name,
                label=script.name,
                description=str(script.description or "脚本工具"),
                category="script",
                aliases=(script.name,),
                group_key=group_key,
                group=group,
            )
        )
    for tool in mcp_tools:
        name = str(tool.tool_name or "").strip()
        if not name:
            continue
        server = getattr(tool, "server", None)
        group_key, group = mcp_tool_group(getattr(server, "server_name", None))
        dynamic_candidates.append(
            ForbiddenToolCandidate(
                id=name,
                label=name,
                description=str(tool.tool_description or "MCP 注册工具"),
                category="mcp",
                aliases=(name,),
                group_key=group_key,
                group=group,
            )
        )

    merged = merge_forbidden_tool_candidates(
        BUILTIN_FORBIDDEN_TOOL_CANDIDATES,
        dynamic_candidates,
    )
    return {
        "candidates": [
            {
                "id": candidate.id,
                "label": candidate.label,
                "description": candidate.description,
                "category": candidate.category,
                "aliases": list(candidate.aliases),
                "group_key": candidate.group_key,
                "group": candidate.group,
            }
            for candidate in merged
        ],
        "skill_candidates": _list_forbidden_skill_candidates(),
    }


@router.get("", response_model=List[SysApiToolResponse])
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """列出系统 HTTP/API 工具（任意持有有效 API Key 的登录用户可读，供智能体配置勾选）。"""
    query = select(SysApiTool).order_by(SysApiTool.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()

@router.post("", response_model=SysApiToolResponse)
async def create_tool(
    tool_in: SysApiToolCreate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Create a new API tool"""
    # ... (rest of function)
    # Check if name exists
    existing = await db.execute(select(SysApiTool).where(SysApiTool.name == tool_in.name))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Tool name already exists")

    try:
        validate_system_params(tool_in.parameter_schema)
        data = _prepare_signing_for_create(tool_in)
        await _ensure_signing_key_id_available(db, data.get("signing_key_id"))
    except SystemParamConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SigningConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    # Dump Dicts to JSON strings for DB
    if data.get("headers") is not None:
        data["headers"] = json.dumps(data["headers"], ensure_ascii=False)
    if data.get("parameter_schema") is not None:
        data["parameter_schema"] = json.dumps(data["parameter_schema"], ensure_ascii=False)
        
    new_tool = SysApiTool(
        id=str(uuid.uuid4()),
        **data
    )
    db.add(new_tool)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="签名 Key-Id 已被其他工具使用") from exc
    await db.refresh(new_tool)
    return new_tool

@router.put("/{tool_id}", response_model=SysApiToolResponse)
async def update_tool(
    tool_id: str,
    tool_in: SysApiToolUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Update an API tool"""
    # ... (rest of function)
    result = await db.execute(select(SysApiTool).where(SysApiTool.id == tool_id))
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    if "parameter_schema" in tool_in.model_fields_set:
        try:
            validate_system_params(tool_in.parameter_schema)
        except SystemParamConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        update_data = _apply_signing_for_update(tool, tool_in)
        await _ensure_signing_key_id_available(
            db, update_data.get("signing_key_id"), exclude_tool_id=tool.id
        )
    except SigningConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    # Dump Dicts to JSON strings for DB
    if "headers" in update_data:
         val = update_data["headers"]
         update_data["headers"] = json.dumps(val, ensure_ascii=False) if val is not None else None
         
    if "parameter_schema" in update_data:
        val = update_data["parameter_schema"]
        update_data["parameter_schema"] = json.dumps(val, ensure_ascii=False) if val is not None else None

    for field, value in update_data.items():
        setattr(tool, field, value)
        
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="签名 Key-Id 已被其他工具使用") from exc
    await db.refresh(tool)
    return tool

@router.post("/batch-group")
async def batch_set_tool_group(
    payload: SysApiToolBatchGroupRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    """批量设置业务分组（注册表页多选后一次改完）。

    只改 ``group_name``：``group_name`` 留空表示清除分组，回到「未分组」状态。
    刻意不做「按名称前缀自动归类」——分组口径由使用者决定，代码里不写死任何前缀。
    """
    result = await db.execute(select(SysApiTool).where(SysApiTool.id.in_(payload.ids)))
    tools = result.scalars().all()
    for tool in tools:
        tool.group_name = payload.group_name
    await db.commit()
    return {"updated": len(tools), "group_name": payload.group_name}


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Delete an API tool"""
    result = await db.execute(select(SysApiTool).where(SysApiTool.id == tool_id))
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    await db.delete(tool)
    await db.commit()
    return {"status": "success", "message": "Tool deleted"}


# --------------------------------------------------------------------------- #
# 保存前试跑
# --------------------------------------------------------------------------- #

#: 试跑响应体回显上限：足够看清结构，又不会被超大响应拖垮界面与审计日志。
PREVIEW_BODY_LIMIT = 2000

#: URL 模板里未替换的参数占位符。
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: 运行时真正支持的方法——与 ``generic_api`` 的请求分支逐字对齐。
_RUNTIME_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@router.post("/preview", response_model=SysApiToolPreviewResponse)
async def preview_tool(
    payload: SysApiToolPreviewRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    """保存前试跑一次（不落库、不新增权限）。

    **走的是与运行时完全相同的请求内核**（``GenericApiToolFactory._perform_request``）：
    另写一套请求逻辑必然导致「试跑能过、运行时调不通」的口径分裂。

    对目标地址**不额外设限**——能创建工具的人本来就能让智能体请求同一地址，
    在试跑上加限制只会误伤内网自建 API，并不提高安全水位。私网地址由前端醒目
    提示；每次试跑都会经审计中间件留痕（请求体里的凭据由 ``mask_sensitive_data``
    脱敏后落库）。

    失败一律以 ``ok=False`` + ``error_kind`` 返回，不抛 500：试跑的价值就在于
    「把原因说清楚」，而不是让界面拿到一个无法解释的错误。
    """
    # 只有「模板里有占位符，**且**参数里没给值」才算没填。
    # 只扫模板会误杀：用户明明把 order_id 填成 11333，却被回一句「地址里还有没填的参数」。
    # 判定口径要与运行时的路径替换一致——那边也是拿 params 里的值去替换 {name}。
    # 系统级参数由后端注入，试跑者不需要填写，预检必须排除它们。
    try:
        system_specs = validate_system_params(payload.parameter_schema)
    except SystemParamConfigError as exc:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="invalid_parameter_schema",
            message=str(exc),
        )
    system_param_names = {spec.name for spec in system_specs}
    params = payload.params or {}
    unfilled = [
        name
        for name in _PLACEHOLDER_RE.findall(payload.url_template)
        if name not in system_param_names
        and (params.get(name) is None or not str(params.get(name)).strip())
    ]
    if unfilled:
        names = "、".join(f"{{{name}}}" for name in unfilled)
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="missing_param",
            message=f"地址里还有没填的参数：{names}。请先填入测试值再试跑",
        )

    method = (payload.method or "GET").strip().upper()
    if method not in _RUNTIME_METHODS:
        supported = " / ".join(sorted(_RUNTIME_METHODS))
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="unsupported_method",
            message=f"平台运行时不支持 {method} 方法，可用的只有 {supported}",
        )

    try:
        validate_no_reserved_signing_headers(payload.headers)
        signing_fields = await _resolve_preview_signing(payload, db)
    except SigningConfigError as exc:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="invalid_signing_config",
            message=str(exc),
        )

    # 内存草稿：与运行时拿到的 SysApiTool 同形（headers / schema 都是 JSON 字符串）
    draft = SysApiTool(
        id="__preview__",
        name="__preview__",
        method=method,
        url_template=payload.url_template,
        headers=json.dumps(payload.headers or {}, ensure_ascii=False),
        parameter_schema=json.dumps(payload.parameter_schema or {}, ensure_ascii=False),
        is_active=False,
        **signing_fields,
    )
    capture: dict[str, Any] = {}

    def request_info() -> SysApiToolPreviewRequestInfo | None:
        """把内核捕获的最终请求转成响应；凭据只以脱敏形式离开服务端。"""
        if not capture:
            return None
        return SysApiToolPreviewRequestInfo(
            method=str(capture.get("method") or ""),
            url=str(mask_sensitive_data(capture.get("url") or "") or ""),
            headers=mask_sensitive_data(capture.get("headers") or {}) or {},
            query=mask_sensitive_data(capture.get("query") or {}) or {},
            body=mask_sensitive_data(capture.get("body")),
        )

    try:
        response, elapsed_ms, _url = await GenericApiToolFactory._perform_request(
            draft,
            dict(payload.params or {}),
            # 非 2xx 也要拿到原始响应，用于区分「接口通了但拒绝」与「根本没连上」
            raise_on_http_error=False,
            # 试跑没有 AgentContext，显式传入当前登录用户
            user_info=user,
            capture=capture,
        )
    except UnsupportedMethodError as exc:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="unsupported_method", message=str(exc),
            request=request_info(),
        )
    except UserPlaceholderError as exc:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="missing_user_variable", message=str(exc),
            request=request_info(),
        )
    except SystemParamConfigError as exc:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="invalid_parameter_schema", message=str(exc),
            request=request_info(),
        )
    except SigningConfigError as exc:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="invalid_signing_config", message=str(exc),
            request=request_info(),
        )
    except httpx.TimeoutException:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="timeout",
            message="请求超时（30 秒）。目标接口没有在时限内响应，请确认地址是否可达",
            request=request_info(),
        )
    except httpx.InvalidURL:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="invalid_url", message="地址格式不合法，无法发起请求",
            request=request_info(),
        )
    except httpx.HTTPError as exc:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="connection",
            message=f"没能连上目标接口（{type(exc).__name__}）：{exc}",
            request=request_info(),
        )
    except Exception as exc:  # noqa: BLE001 —— 试跑必须给出原因，而不是甩一个 500
        logger.warning("[ToolPreview] unexpected failure: %s", exc)
        return SysApiToolPreviewResponse(
            ok=False, error_kind="connection", message=f"试跑失败：{exc}",
            request=request_info(),
        )

    raw_body = response.text or ""
    truncated = len(raw_body) > PREVIEW_BODY_LIMIT
    body_text = raw_body[:PREVIEW_BODY_LIMIT]

    if 200 <= response.status_code < 300:
        return SysApiToolPreviewResponse(
            ok=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            body=body_text,
            body_truncated=truncated,
            response_headers=mask_sensitive_data(dict(response.headers)) or {},
            request=request_info(),
            message=f"HTTP {response.status_code}",
        )

    return SysApiToolPreviewResponse(
        ok=False,
        status_code=response.status_code,
        elapsed_ms=elapsed_ms,
        body=body_text,
        body_truncated=truncated,
        response_headers=mask_sensitive_data(dict(response.headers)) or {},
        request=request_info(),
        error_kind="http_error",
        message=f"接口返回 HTTP {response.status_code}——地址是通的，但目标接口拒绝了这次请求",
    )

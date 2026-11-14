"""管理 MCP 工具的组凭据映射及使用范围。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal.endpoints.mcp import _clear_runtime_tool_cache, _ensure_server_control_access, _get_user_id
from app.core.dependencies import require_admin, require_api_key, require_permission
from app.core.orm import get_db_session
from app.models.credential_type import McpCredentialProfile, McpCredentialUseGrant
from app.models.mcp import McpServer, McpToolCache
from app.models.secret import SecretBinding
from app.services.ai.tools.model_tool_name import build_model_tool_name
from app.services.secrets import mcp_profiles, type_catalog
from app.services.secrets.policy import CONSUMER_MCP

router = APIRouter()
_manage = [Depends(require_permission("element", "element:mcp_management:publish"))]
_LEGACY_GRANT_NOTICE = "逐工具凭据授权已停用；系统共享权限请在凭据中心管理。历史记录不再影响凭据使用。"


class ProfileRequest(BaseModel):
    type_key: str = Field(min_length=1, max_length=64)
    type_version: int = Field(ge=1)
    selection_mode: Literal["fixed", "infer", "ask"]
    mappings: dict[str, str]
    # 由服务层校验并清空无效路径，避免校验错误保留旧的可见范围。
    model_result_fields: Any = None
    expected_result_target: str | None = Field(default=None, min_length=64, max_length=64)
    expected_revision: int = Field(ge=0)
    expected_profile_id: str | None = Field(default=None, max_length=36)
    # 省略时兼容原客户端；显式 null 仅清除当前用户的固定绑定。
    fixed_secret_id: str | None = Field(default=None, min_length=1, max_length=36)


class ModelResultFieldsRequest(BaseModel):
    model_result_fields: Any = None
    expected_revision: int = Field(ge=0)
    expected_target: str = Field(min_length=64, max_length=64)


class FixedRequest(BaseModel):
    secret_id: str | None = Field(default=None, max_length=36)
    expected_revision: int = Field(ge=1)
    expected_profile_id: str = Field(min_length=1, max_length=36)


class GrantRequest(BaseModel):
    secret_id: str = Field(min_length=1, max_length=36)
    grantee_type: Literal["all", "user", "dept"]
    grantee_id: str = Field(default="", max_length=64)
    expires_at: datetime | None = None
    expected_revision: int = Field(ge=1)
    expected_profile_id: str = Field(min_length=1, max_length=36)


def _fail(exc) -> HTTPException:
    return HTTPException(exc.status_code, detail={"code": exc.code, "message": str(exc)})


async def _resource(
    db, tool_id: str, user: dict, *, control: bool = False, lock: bool = False, lock_target: bool = False,
):
    stmt = select(McpToolCache).where(McpToolCache.id == tool_id)
    if lock_target:
        # 同步工具时先锁服务再写工具，保存结果字段采用同一顺序。
        server_id = await db.scalar(select(McpToolCache.server_id).where(McpToolCache.id == tool_id))
        server = await db.get(McpServer, server_id, with_for_update=True) if server_id else None
        tool = await db.scalar(stmt.with_for_update()) if server else None
        if tool is None or tool.server_id != server_id:
            server = None
    else:
        tool = await db.scalar(stmt.with_for_update() if lock else stmt)
        server = await db.get(McpServer, tool.server_id) if tool else None
    if server is None:
        raise HTTPException(404, detail="工具不存在")
    if control:
        _ensure_server_control_access(server, user)
    elif (
        server.enabled_status != 1 or not tool.is_available or not tool.is_published
        or (server.scope == "personal" and server.user_id != _get_user_id(user))
    ):
        raise HTTPException(404, detail="工具不存在")
    return tool, server


def _serialize(profile) -> dict | None:
    if profile is None:
        return None
    return {
        "revision": profile.revision, "profile_id": profile.profile_id, "type_key": profile.type_key, "type_version": profile.type_version,
        "selection_mode": profile.selection_mode, "mappings": profile.mappings,
        "model_result_fields": profile.model_result_fields or [],
    }


async def _current(db, tool, server, revision: int | None = None, profile_id: str | None = None):
    profile = await db.get(McpCredentialProfile, tool.id)
    if profile is None or (revision is not None and (revision, profile_id) != (profile.revision, profile.profile_id)):
        raise HTTPException(409, detail="工具凭据配置已变化，请刷新后重试")
    if not mcp_profiles.profile_is_current(profile, tool, server):
        raise HTTPException(409, detail="工具参数或目标已变化，请重新保存凭据配置")
    return profile


@router.get("/tools/{tool_id}/credential-profile")
async def get_profile(tool_id: str, user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    tool, server = await _resource(db, tool_id, user, control=True)
    profile = await db.get(McpCredentialProfile, tool.id)
    schema = mcp_profiles.parse_schema(tool)
    props = schema.get("properties") or {}
    sensitive = mcp_profiles.unbound_sensitive_args(schema, [])
    result_revision, result_fields, result_stale = mcp_profiles.tool_result_policy(tool, server)
    if result_stale:
        # 管理界面保留已保存的合法路径供用户重新确认；执行仍使用 tool_result_policy 的空范围。
        result_fields, _ = mcp_profiles.validate_model_result_fields(tool.model_result_policy.get("fields"))
    return {"code": 0, "data": {
        "enabled": True,  # 兼容仍读取此字段的门户客户端
        "profile": _serialize(profile),
        "stale": bool(profile and not mcp_profiles.profile_is_current(profile, tool, server)),
        "model_tool_name": build_model_tool_name(tool.tool_name),
        "model_result_policy": {
            "revision": result_revision, "model_result_fields": result_fields, "stale": result_stale,
            "target": mcp_profiles.tool_result_target(tool, server),
        },
        "parameters": [
            {"name": name, "type": spec.get("type"), "required": name in (schema.get("required") or []),
             "sensitive": name in sensitive, "group_binding_required": mcp_profiles.group_binding_required(name, spec)}
            for name, spec in props.items() if isinstance(spec, dict)
        ] if isinstance(props, dict) else [],
    }}


@router.put("/tools/{tool_id}/credential-profile", dependencies=_manage)
async def put_profile(
    tool_id: str, payload: ProfileRequest,
    user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session),
):
    update_fixed = "fixed_secret_id" in payload.model_fields_set
    if update_fixed and payload.selection_mode != "fixed":
        raise HTTPException(422, detail="仅固定凭据模式可设置固定绑定")
    tool, server = await _resource(db, tool_id, user, control=True, lock_target=True)
    warnings: list[dict[str, str]] = []
    try:
        profile = await mcp_profiles.save_profile(
            db, tool, server, **payload.model_dump(exclude={"fixed_secret_id"}),
            actor_id=str(_get_user_id(user)), warnings=warnings, commit=False,
        )
        if update_fixed:
            await db.flush()
            await _save_fixed_binding(db, tool, server, profile, payload.fixed_secret_id, user)
        await db.commit()
    except (mcp_profiles.CredentialInvocationError, type_catalog.CredentialTypeError) as exc:
        await db.rollback()
        raise _fail(exc) from None
    except Exception:
        await db.rollback()
        raise
    _clear_runtime_tool_cache()
    result = {"code": 0, "data": _serialize(profile)}
    if warnings:
        result["warnings"] = warnings
    return result


@router.put("/tools/{tool_id}/model-result-fields", dependencies=_manage)
async def put_model_result_fields(
    tool_id: str, payload: ModelResultFieldsRequest,
    user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session),
):
    tool, server = await _resource(db, tool_id, user, control=True, lock_target=True)
    try:
        revision, fields, error = await mcp_profiles.save_tool_result_policy(
            db, tool, server, paths=payload.model_result_fields,
            expected_revision=payload.expected_revision, expected_target=payload.expected_target,
            actor_id=str(_get_user_id(user)),
        )
    except mcp_profiles.CredentialInvocationError as exc:
        raise _fail(exc) from None
    response = {"code": 0, "data": {
        "revision": revision, "model_result_fields": fields, "stale": False, "target": payload.expected_target,
    }}
    if error:
        response["warnings"] = [{"code": "MCP_RESULT_FIELDS_INVALID", "message": error}]
    return response


@router.delete("/tools/{tool_id}/credential-profile", dependencies=_manage)
async def delete_profile(
    tool_id: str, expected_revision: int, expected_profile_id: str,
    user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session),
):
    tool, server = await _resource(db, tool_id, user, control=True, lock=True)
    profile = await db.get(McpCredentialProfile, tool.id)
    if profile is None or (expected_revision, expected_profile_id) != (profile.revision, profile.profile_id):
        raise HTTPException(409, detail="工具凭据配置已变化，请刷新后重试")
    await mcp_profiles.clear_profile_access(db, tool.id, server.id)
    mcp_profiles.clear_tool_result_policy(tool, server)
    await db.delete(profile)
    type_catalog.audit_change(db, "mcp_profile_delete", str(_get_user_id(user)), {"tool_id": tool_id})
    await db.commit()
    _clear_runtime_tool_cache()
    return {"code": 0}


@router.get("/tools/{tool_id}/credential-profile/candidates")
async def candidates(
    tool_id: str, user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session),
    type_key: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    type_version: Annotated[int | None, Query(ge=1)] = None,
):
    draft = type_key is not None or type_version is not None
    if draft and (type_key is None or type_version is None):
        raise HTTPException(422, detail="请同时指定凭据类型和版本")
    tool, server = await _resource(db, tool_id, user, control=draft)
    user_id = str(_get_user_id(user) or "")
    if draft:
        if not user_id:
            raise HTTPException(403, detail="需要用户身份")
        if type_key in type_catalog.LEGACY_TYPES:
            raise HTTPException(400, detail="请选择结构化凭据类型")
        try:
            await type_catalog.get_version(db, type_key, type_version)
        except type_catalog.CredentialTypeError as exc:
            raise _fail(exc) from None
        # 草稿只用于筛选候选元数据；不写入、启用或修改工具配置。
        selection = SimpleNamespace(type_key=type_key, type_version=type_version)
        profile = await db.get(McpCredentialProfile, tool.id)
    else:
        profile = selection = await _current(db, tool, server)
    rows = await mcp_profiles.list_candidates(db, selection, server, user_id=user_id, dept_code=user.get("dept_code"))
    from app.services.secrets.resolver import SecretResolver

    bound = None
    if not draft or (
        profile and profile.selection_mode == "fixed"
        and (profile.type_key, profile.type_version) == (type_key, type_version)
    ):
        # 失效绑定仍回传引用，避免编辑表单误将“不可用”当作“未绑定”自动清除。
        bound = await SecretResolver._pick_binding(db, CONSUMER_MCP, server.id, f"group:{tool_id}", user_id)
    return {"code": 0, "data": {
        "profile": _serialize(profile), "fixed_secret_id": bound.secret_id if bound else None,
        "items": [
            {"id": row[0].id, "name": row[0].name, "account": row.account, "owner_scope": row[0].owner_scope}
            for row in rows
        ],
    }}


@router.put("/tools/{tool_id}/credential-profile/fixed")
async def set_fixed(
    tool_id: str, payload: FixedRequest,
    user: dict = Depends(require_api_key), db: AsyncSession = Depends(get_db_session),
):
    tool, server = await _resource(db, tool_id, user, lock=True)
    profile = await _current(db, tool, server, payload.expected_revision, payload.expected_profile_id)
    await _save_fixed_binding(db, tool, server, profile, payload.secret_id, user)
    await db.commit()
    return {"code": 0}


async def _save_fixed_binding(db, tool, server, profile, secret_id: str | None, user: dict) -> None:
    """与映射共用事务；独立绑定接口也复用同一套候选与权限检查。"""
    if profile.selection_mode != "fixed":
        raise HTTPException(409, detail="当前工具未设置为固定凭据")
    user_id = str(_get_user_id(user) or "")
    if not user_id:
        raise HTTPException(403, detail="需要用户身份")
    if secret_id:
        rows = await mcp_profiles.list_candidates(
            db, profile, server, user_id=user_id, dept_code=user.get("dept_code"), secret_id=secret_id,
        )
        if len(rows) != 1:
            raise HTTPException(404, detail="凭据不可用")
    from uuid import uuid4

    slot = f"group:{tool.id}"
    await db.execute(delete(SecretBinding).where(
        SecretBinding.consumer_type == CONSUMER_MCP, SecretBinding.consumer_id == server.id,
        SecretBinding.slot == slot, SecretBinding.bound_by_user_id == user_id,
    ))
    if secret_id:
        db.add(SecretBinding(
            id=str(uuid4()), secret_id=secret_id, consumer_type=CONSUMER_MCP,
            consumer_id=server.id, slot=slot, bound_by_user_id=user_id, strategy="fixed",
        ))
    type_catalog.audit_change(db, "mcp_profile_fixed", user_id, {"tool_id": tool.id, "secret_id": secret_id})


@router.get("/tools/{tool_id}/credential-profile/grants", deprecated=True)
async def list_grants(
    tool_id: str, admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    await _resource(db, tool_id, admin, control=True)
    model = McpCredentialUseGrant
    grants = (await db.scalars(select(model).where(model.tool_id == tool_id))).all()
    return {"code": 0, "deprecated": True, "effective": False, "message": _LEGACY_GRANT_NOTICE, "data": [
        {"secret_id": row.secret_id, "grantee_type": row.grantee_type, "grantee_id": row.grantee_id,
         "expires_at": row.expires_at}
        for row in grants
    ]}


@router.put("/tools/{tool_id}/credential-profile/grants", deprecated=True)
async def put_grant(
    tool_id: str, payload: GrantRequest,
    admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    await _resource(db, tool_id, admin, control=True)
    # 不把旧的逐工具操作隐式变为全局共享授权，也不成功写入无效权限。
    raise HTTPException(410, detail={"code": "CREDENTIAL_TOOL_GRANTS_RETIRED", "message": _LEGACY_GRANT_NOTICE})


@router.delete("/tools/{tool_id}/credential-profile/grants", deprecated=True)
async def delete_grant(
    tool_id: str, secret_id: str, grantee_type: Literal["all", "user", "dept"], grantee_id: str = "",
    admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    tool, _ = await _resource(db, tool_id, admin, control=True, lock=True)
    grant = await db.get(McpCredentialUseGrant, (tool.id, secret_id, grantee_type, grantee_id))
    if grant is not None:
        await db.delete(grant)
        type_catalog.audit_change(db, "mcp_profile_revoke", str(_get_user_id(admin)), {
            "tool_id": tool_id, "secret_id": secret_id, "grantee_type": grantee_type, "grantee_id": grantee_id,
            "legacy_only": True,
        })
    await db.commit()
    return {"code": 0, "deprecated": True, "effective": False, "message": _LEGACY_GRANT_NOTICE}

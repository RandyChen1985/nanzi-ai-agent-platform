from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from typing import List, Optional, Dict, Any, Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func, case, or_, desc
import uuid
import json
import re
import time
import logging
import secrets

from app.core.config import settings
from app.core.orm import get_db_session
from app.core.dependencies import (
    require_admin,
    require_any_permission,
    require_permission,
    require_api_key,
)
from app.models.mcp import McpServer, McpToolCache, McpOutboundAuditLog
from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.tools.mcp_client import McpClientService, McpSseSession
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.mcp.mcp_auth_policy import (
    _parse_auth_headers,
    build_mcp_user_assertion_headers,
    encrypt_mcp_auth_headers,
    generate_mcp_private_key_pem,
    mcp_auth_headers_configured,
    mcp_auth_headers_summary,
    resolve_mcp_auth_headers,
)
from app.services.mcp.stale_tool_cleanup import cleanup_stale_tools
from app.services.mcp_share_service import (
    McpShareConflictError,
    McpShareNotFoundError,
    McpSharePermissionError,
    accept_share as accept_mcp_share,
    annotate_personal_servers,
    list_received_shares as list_received_mcp_shares,
    list_sent_shares as list_sent_mcp_shares,
    reject_share as reject_mcp_share,
    search_share_targets,
    share_menu_counts as mcp_share_menu_counts,
    share_server as share_mcp_server,
    withdraw_share as withdraw_mcp_share,
)
from app.services.mcp.echo_server import (
    ECHO_SERVER_ID,
    ECHO_SERVER_NAME,
    ECHO_TOOL_NAME,
    ECHO_TOOL_DESCRIPTION,
    build_echo_server_url,
    echo_tool_schema,
    personal_echo_server_id,
    personal_echo_tool_name,
    resolve_echo_base_url,
)
from app.utils.encryption import get_api_key_manager
from pydantic import BaseModel, Field, ConfigDict, model_validator

logger = logging.getLogger(__name__)
router = APIRouter()


def _clear_runtime_tool_cache() -> None:
    """Make MCP configuration changes effective without waiting for TTL."""
    from app.services.ai.tools.registry import ToolRegistry

    ToolRegistry.clear_db_tool_cache()

class McpServerBase(BaseModel):
    server_name: str
    sse_url: str
    enabled_status: Optional[int] = 1
    scope: Optional[str] = "global"
    remark: Optional[str] = Field(default=None, max_length=500)
    credential_mode: Literal["static", "fixed_token_signed_user"] = "static"
    user_assertion_enabled: bool = False
    user_assertion_header: str = "X-Nanzi-User-Assertion"
    user_assertion_audience: Optional[str] = None
    user_assertion_key_id: Optional[str] = None
    user_assertion_issuer: Optional[str] = "nanzi-platform"


class McpServerWrite(McpServerBase):
    """MCP Server 写入模型；fixed_token 只允许写入，不进入响应模型。"""

    auth_headers: Optional[str] = "{}"
    fixed_token: Optional[str] = Field(default=None, exclude=True)
    authorization_enabled: Optional[bool] = None
    auth_headers_patch: Optional[Dict[str, Optional[str]]] = None

    @model_validator(mode="after")
    def validate_user_assertion_config(self):
        if str(self.user_assertion_header or "").strip().lower() == "authorization":
            raise ValueError("UserContext Header 不能使用 Authorization")
        return self


class McpVerifyRequest(McpServerWrite):
    """连接测试请求；编辑态携带 server_id，用于复用已保存的凭据。"""

    server_id: Optional[str] = None


def _normalized_remark(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text[:500] if text else None

class McpServerResponse(McpServerBase):
    id: str
    auth_headers_configured: bool = False
    authorization_configured: bool = False
    masked_auth_headers: Dict[str, str] = Field(default_factory=dict)
    scope: str = "global"
    user_id: Optional[int] = None
    last_sync_at: Optional[Any] = None
    tool_count: int = 0
    published_tool_count: int = 0
    stale_tool_count: int = 0
    # 分享血缘：这条服务是不是从别人的分享安装来的、以及我把它分享出去过几次
    shared_from: Optional[Dict[str, Any]] = None
    share_count: int = 0

    model_config = ConfigDict(from_attributes=True)

class McpToolResponse(BaseModel):
    id: str
    server_id: str
    tool_name: str
    tool_description: Optional[str]
    parameter_schema: str
    is_published: bool
    is_available: bool = True

    model_config = ConfigDict(from_attributes=True)

class McpToolResponseWithUsage(McpToolResponse):
    usage_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class McpAgentUsageItem(BaseModel):
    id: str
    name: str
    display_name: str
    is_enabled: bool
    active: bool
    version_count: int


class McpServerUsageResponse(BaseModel):
    server_id: str
    bound_agent_count: int
    active_agent_count: int
    bound_version_count: int
    agents: List[McpAgentUsageItem]


def _normalized_server_name(value: str) -> str:
    return str(value or "").strip()


def _default_user_assertion_audience(server_id: str) -> str:
    return f"mcp:{server_id}"


def _configured_tool_names(value: Any) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {value}
    if not isinstance(value, list):
        return set()

    names = set()
    for item in value:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]))
    return names


def _rename_tool_reference(value: Any, old_prefix: str, new_prefix: str) -> Any:
    """Rename one MCP server prefix while preserving tool config shape."""
    if isinstance(value, str):
        if value.startswith(f"{old_prefix}:"):
            return f"{new_prefix}{value[len(old_prefix):]}"
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return _rename_tool_reference(parsed, old_prefix, new_prefix)
    if isinstance(value, list):
        return [_rename_tool_reference(item, old_prefix, new_prefix) for item in value]
    if isinstance(value, dict):
        renamed = dict(value)
        if isinstance(renamed.get("name"), str):
            renamed["name"] = _rename_tool_reference(renamed["name"], old_prefix, new_prefix)
        return renamed
    return value


async def _migrate_server_name_references(
    db: AsyncSession,
    server_id: str,
    old_name: str,
    new_name: str,
) -> list[tuple[str, str]]:
    """Keep cached tools and all agent-version references valid after rename.

    Returns:
        ［(tool_cache_id, new_tool_name)］：供调用方同步会话资源里的旧引用。
    """
    old_prefix = f"{old_name}:"
    new_prefix = f"{new_name}:"
    renamed_cache: list[tuple[str, str]] = []
    tool_result = await db.execute(
        select(McpToolCache).where(McpToolCache.server_id == server_id)
    )
    cached_tools = tool_result.scalars().all()
    cached_by_name = {tool.tool_name: tool for tool in cached_tools}
    for tool in cached_tools:
        if not tool.tool_name.startswith(old_prefix):
            continue
        renamed_name = f"{new_name}{tool.tool_name[len(old_name):]}"
        existing = cached_by_name.get(renamed_name)
        if existing is not None and existing is not tool:
            existing.is_published = bool(existing.is_published or tool.is_published)
            existing.is_available = bool(
                getattr(existing, "is_available", True) or getattr(tool, "is_available", True)
            )
            if not existing.tool_description:
                existing.tool_description = tool.tool_description
            if not existing.parameter_schema:
                existing.parameter_schema = tool.parameter_schema
            await db.delete(tool)
            continue
        cached_by_name.pop(tool.tool_name, None)
        tool.tool_name = renamed_name
        cached_by_name[renamed_name] = tool
        renamed_cache.append((tool.id, renamed_name))

    version_result = await db.execute(select(AIAgentVersion))
    for version in version_result.scalars().all():
        renamed_version_tools = _rename_tool_reference(version.tools, old_name, new_name)
        if renamed_version_tools != version.tools:
            version.tools = renamed_version_tools
    return renamed_cache


async def _sync_renamed_tools_to_conversations(
    renamed_tools: list[tuple[str, str]],
    *,
    owner_id: Optional[int],
) -> None:
    """服务改名后，把已挂载会话里的旧工具名按缓存 id 纠成新名。

    智能体版本里的引用由 ``_migrate_server_name_references`` 直接改写；会话资源存在
    Redis 里、按运行时注入，必须单独同步，否则用户会看到「清单里有、调用时未注册」。
    """
    if not renamed_tools:
        return
    from app.services.conversation_resource_service import ConversationResourceService

    for tool_id, new_name in renamed_tools:
        try:
            await ConversationResourceService.rewrite_mcp_tool_name(
                owner_id=owner_id,
                tool_id=tool_id,
                new_name=new_name,
            )
        except Exception:  # noqa: BLE001
            pass


def _ensure_server_control_access(server: McpServer, user: Dict) -> None:
    is_admin = user.get("role") == "admin"
    if server.scope == "global" and not is_admin:
        raise HTTPException(status_code=403, detail="只有系统管理员才能管理平台公共 MCP 服务")
    if server.scope == "personal" and server.user_id != _get_user_id(user) and not is_admin:
        raise HTTPException(status_code=403, detail="无法管理其他用户的私有 MCP 服务")


async def _find_server_with_name(
    db: AsyncSession,
    server_name: str,
    *,
    exclude_server_id: Optional[str] = None,
) -> Optional[McpServer]:
    """MCP display names are globally unique across public and personal scopes."""
    stmt = select(McpServer).where(
        func.lower(McpServer.server_name) == server_name.lower()
    ).limit(1)
    if exclude_server_id:
        stmt = stmt.where(McpServer.id != exclude_server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _remove_header_case_insensitive(headers: Dict[str, str], target: str) -> None:
    for key in list(headers):
        if str(key).strip().casefold() == target.casefold():
            headers.pop(key, None)


def _set_header_case_insensitive(headers: Dict[str, str], key: str, value: str) -> None:
    _remove_header_case_insensitive(headers, key)
    headers[key] = value


def _apply_mcp_auth_update(server: McpServer, data: McpServerWrite) -> None:
    """应用认证更新；编辑界面只提交 Header 增量，避免回显或覆盖旧密钥。"""
    has_patch = "auth_headers_patch" in data.model_fields_set
    has_authorization_control = (
        "authorization_enabled" in data.model_fields_set
        or data.fixed_token is not None
    )

    if not has_patch and not has_authorization_control:
        if "auth_headers" in data.model_fields_set:
            server.auth_headers = encrypt_mcp_auth_headers(data.auth_headers)
        return

    headers = resolve_mcp_auth_headers(server)
    for key, value in (data.auth_headers_patch or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        if normalized_key.casefold() == "authorization":
            raise HTTPException(status_code=400, detail="Authorization 请通过独立开关配置")
        if value is None:
            _remove_header_case_insensitive(headers, normalized_key)
        else:
            _set_header_case_insensitive(headers, normalized_key, str(value))

    if data.authorization_enabled is False:
        _remove_header_case_insensitive(headers, "Authorization")
        server.fixed_token_encrypted = None
    elif data.fixed_token is not None:
        token = str(data.fixed_token).strip()
        if not token:
            raise HTTPException(status_code=400, detail="Authorization Token 不能为空")
        server.fixed_token_encrypted = get_api_key_manager().encrypt_api_key(token)
        _remove_header_case_insensitive(headers, "Authorization")
    elif data.authorization_enabled is True and not any(
        str(key).strip().casefold() == "authorization" and str(value).strip()
        for key, value in headers.items()
    ):
        raise HTTPException(status_code=400, detail="开启 Authorization 后请输入 Token")

    if server.fixed_token_encrypted:
        _remove_header_case_insensitive(headers, "Authorization")
    server.auth_headers = encrypt_mcp_auth_headers(headers)


async def _load_verify_saved_server(
    db: AsyncSession,
    data: McpVerifyRequest,
    user: Dict,
) -> Optional[McpServer]:
    """编辑态探测的凭据来源；地址变更时不返回，避免旧凭据被发往新地址。"""
    server_id = str(data.server_id or "").strip()
    if not server_id:
        return None

    server = (
        await db.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    _ensure_server_control_access(server, user)
    if str(data.sse_url or "").strip() != str(server.sse_url or "").strip():
        # 地址已改：不复用旧凭据，必须由本次请求显式提供 Token。
        return None
    return server


def _build_verify_auth_headers(
    server: Optional[McpServer],
    data: McpVerifyRequest,
) -> Dict[str, str]:
    """构造探测请求的出站 Header；编辑态未改动的凭据沿用已保存配置。"""
    if server is None:
        headers = _parse_auth_headers(data.auth_headers)
    else:
        headers = resolve_mcp_auth_headers(server)
        if "auth_headers" in data.model_fields_set and data.auth_headers:
            headers.update(_parse_auth_headers(data.auth_headers))

    for key, value in (data.auth_headers_patch or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        if normalized_key.casefold() == "authorization":
            raise HTTPException(status_code=400, detail="Authorization 请通过独立开关配置")
        if value is None:
            _remove_header_case_insensitive(headers, normalized_key)
        else:
            _set_header_case_insensitive(headers, normalized_key, str(value))

    if data.authorization_enabled is False:
        _remove_header_case_insensitive(headers, "Authorization")
    elif data.fixed_token:
        _set_header_case_insensitive(headers, "Authorization", f"Bearer {data.fixed_token}")
    return headers


def _build_verify_user_assertion_headers(
    server: Optional[McpServer],
    data: McpVerifyRequest,
    user: Dict,
) -> Dict[str, str]:
    """编辑态探测时按已保存私钥签发用户断言，保持与真实调用一致。"""
    if not data.user_assertion_enabled:
        return {}
    if server is None or not bool(getattr(server, "user_assertion_enabled", False)):
        raise HTTPException(
            status_code=400,
            detail="开启用户身份传递后需先保存 MCP 生成签名密钥，保存后再测试连接",
        )
    try:
        return build_mcp_user_assertion_headers(
            server,
            user_info=user,
            agent_info={"agent_id": "mcp-connection-test", "agent_name": "MCP 连接测试"},
            assertion_header=data.user_assertion_header,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"用户身份传递配置无效: {exc}")


# 连接测试是「新建」与「编辑」共用的第一步（向导第 1 步必须调它），所以 create / edit
# 任一权限即可；它只做探测、不落库，不构成写操作。
@router.post(
    "/verify",
    dependencies=[
        Depends(
            require_any_permission(
                ("element", "element:mcp_management:create"),
                ("element", "element:mcp_management:edit"),
            )
        )
    ],
)
async def verify_mcp_server(
    data: McpVerifyRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    """Test connection and return discovered tools without saving"""
    saved_server = await _load_verify_saved_server(db, data, user)
    auth_headers = _build_verify_auth_headers(saved_server, data)
    auth_headers.update(_build_verify_user_assertion_headers(saved_server, data, user))

    temp_id = f"verify_{uuid.uuid4().hex[:8]}"
    McpClientService._sessions[temp_id] = McpSseSession(temp_id, data.sse_url, auth_headers)
    
    try:
        tools = await McpClientService.list_remote_tools(temp_id)
        if temp_id in McpClientService._sessions:
            await McpClientService._sessions[temp_id].close()
            del McpClientService._sessions[temp_id]
            
        return {
            "status": "success",
            "tools": [
                {"name": t.name if hasattr(t, 'name') else t.get('name'), 
                 "description": t.description if hasattr(t, 'description') else t.get('description')} 
                for t in tools
            ]
        }
    except Exception as e:
        if temp_id in McpClientService._sessions:
            await McpClientService._sessions[temp_id].close()
            del McpClientService._sessions[temp_id]
        raise HTTPException(status_code=400, detail=f"连接失败: {str(e)}")

def _get_user_id(user: Dict) -> Optional[int]:
    val = user.get("user_id") if user.get("user_id") is not None else user.get("id")
    try:
        return int(val) if val is not None else None
    except Exception:
        return None

@router.get("/servers", response_model=List[McpServerResponse])
async def list_mcp_servers(
    scope: str = Query("global"),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    """List MCP servers filtered by scope (global / personal). Personal servers are strictly isolated by current user."""
    if scope == "personal":
        user_id = _get_user_id(user)
        stmt = select(McpServer).where(McpServer.scope == "personal", McpServer.user_id == user_id)
    else:
        stmt = select(McpServer).where(McpServer.scope == "global")

    result = await db.execute(stmt)
    servers = result.scalars().all()

    # 「我的 MCP」额外带上分享血缘：谁分享给我的、我分享出去过几次
    annotations: Dict[str, Any] = {"shared_from": {}, "share_counts": {}}
    if scope == "personal":
        annotations = await annotate_personal_servers(session=db, user=user)

    res = []
    for s in servers:
        # Total count
        count_stmt = select(func.count(McpToolCache.id)).where(McpToolCache.server_id == s.id)
        total_count = (await db.execute(count_stmt.where(McpToolCache.is_available == True))).scalar() or 0
        stale_count_stmt = select(func.count(McpToolCache.id)).where(
            McpToolCache.server_id == s.id,
            McpToolCache.is_available == False,
        )
        stale_count = (await db.execute(stale_count_stmt)).scalar() or 0
        
        # Published count
        pub_stmt = select(func.count(McpToolCache.id)).where(
            McpToolCache.server_id == s.id,
            McpToolCache.is_available == True,
            McpToolCache.is_published == True
        )
        pub_count = (await db.execute(pub_stmt)).scalar() or 0
        
        item = McpServerResponse.model_validate(s)
        item.share_count = int(annotations["share_counts"].get(s.id, 0))
        item.shared_from = annotations["shared_from"].get(s.id)
        item.auth_headers_configured = mcp_auth_headers_configured(s)
        item.authorization_configured, item.masked_auth_headers = mcp_auth_headers_summary(s)
        item.tool_count = total_count
        item.published_tool_count = pub_count
        item.stale_tool_count = stale_count
        res.append(item)
    return res


def _echo_owner_name_part(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return text or "user"


async def _require_echo_scope_access(
    scope: str,
    user: Dict,
    db: AsyncSession,
) -> Optional[int]:
    """平台 Echo 仍限管理员；个人 Echo 与手动创建同权，统一走 create 码。"""
    if scope == "global":
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="只有系统管理员才能创建平台 Echo 测试 MCP")
        return None

    owner_id = _get_user_id(user)
    if owner_id is None:
        raise HTTPException(status_code=403, detail="无法识别当前用户，不能创建个人 Echo 测试 MCP")
    if user.get("role") == "admin":
        return owner_id

    from app.services.permission_service import PermissionService

    if not await PermissionService(db).check_permission(
        owner_id, "element", "element:mcp_management:create"
    ):
        raise HTTPException(status_code=403, detail="没有创建个人 MCP 服务的权限")
    return owner_id


async def _unique_personal_echo_name(
    db: AsyncSession,
    base_name: str,
    server_id: str,
) -> str:
    """显示名全局唯一：被同名服务占用时追加序号，最多 100 字符。"""
    candidate = base_name[:100]
    suffix = 1
    while await _find_server_with_name(db, candidate, exclude_server_id=server_id) is not None:
        suffix += 1
        candidate = f"{base_name[: 96 - len(str(suffix))]}-{suffix}"
    return candidate


@router.post("/servers/echo-test", response_model=McpServerResponse)
async def create_echo_test_mcp(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
    scope: str = Query("global", pattern="^(global|personal)$"),
):
    """幂等创建/恢复 Echo 测试 MCP；固定 Token 与签名私钥只加密保存，不返回前端。"""
    # 直接调用（测试/内部）时默认值还是 Query 对象，这里统一回落平台实例。
    scope = scope if isinstance(scope, str) and scope in ("global", "personal") else "global"
    owner_id = await _require_echo_scope_access(scope, user, db)
    is_personal = scope == "personal"
    server_id = personal_echo_server_id(owner_id) if is_personal else ECHO_SERVER_ID

    base_url = resolve_echo_base_url(str(request.base_url), settings.APP_PUBLIC_URL)
    echo_url = build_echo_server_url(base_url, server_id)
    tool_id = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"nanzi:mcp:echo:personal:{owner_id}:tool:echo"))
        if is_personal
        else str(uuid.uuid5(uuid.NAMESPACE_URL, "nanzi:mcp:echo:tool:echo"))
    )
    manager = get_api_key_manager()
    server = (
        await db.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one_or_none()

    if server is None:
        if is_personal:
            server_name = await _unique_personal_echo_name(
                db,
                f"mcp-private-{_echo_owner_name_part(user.get('user_name'))}-echo",
                server_id,
            )
            remark = "个人 Echo 测试 MCP，用于验证本人调用链路的用户身份透传"
        else:
            server_name = ECHO_SERVER_NAME
            remark = "平台内置 Echo 测试 MCP，用于验证协议和用户身份透传"
        server = McpServer(
            id=server_id,
            server_name=server_name,
            remark=remark,
            sse_url=echo_url,
            auth_headers="{}",
            credential_mode="fixed_token_signed_user",
            fixed_token_encrypted=manager.encrypt_api_key(secrets.token_urlsafe(32)),
            user_assertion_enabled=True,
            user_assertion_header="X-Nanzi-User-Assertion",
            user_assertion_audience=f"mcp:{server_id}",
            user_assertion_key_id=f"echo-{uuid.uuid4().hex[:16]}",
            user_assertion_issuer="nanzi-platform",
            user_assertion_private_key_encrypted=manager.encrypt_api_key(
                generate_mcp_private_key_pem()
            ),
            enabled_status=1,
            scope=scope,
            user_id=owner_id,
        )
        db.add(server)
        tool_name = personal_echo_tool_name(server_name) if is_personal else ECHO_TOOL_NAME
        db.add(
            McpToolCache(
                id=tool_id,
                server_id=server_id,
                tool_name=tool_name,
                tool_description=ECHO_TOOL_DESCRIPTION,
                parameter_schema=echo_tool_schema(),
                is_published=True,
                is_available=True,
            )
        )
    else:
        # 重新点击入口只恢复可用状态，不轮换既有的 Token / 私钥。
        if not is_personal:
            server.server_name = ECHO_SERVER_NAME
            server.remark = "平台内置 Echo 测试 MCP，用于验证协议和用户身份透传"
        server.sse_url = echo_url
        server.auth_headers = "{}"
        server.credential_mode = "fixed_token_signed_user"
        server.user_assertion_enabled = True
        server.user_assertion_header = "X-Nanzi-User-Assertion"
        server.user_assertion_audience = server.user_assertion_audience or f"mcp:{server_id}"
        server.user_assertion_key_id = server.user_assertion_key_id or f"echo-{uuid.uuid4().hex[:16]}"
        server.user_assertion_issuer = "nanzi-platform"
        server.enabled_status = 1
        server.scope = scope
        server.user_id = owner_id
        if not server.fixed_token_encrypted:
            server.fixed_token_encrypted = manager.encrypt_api_key(secrets.token_urlsafe(32))
        if not server.user_assertion_private_key_encrypted:
            server.user_assertion_private_key_encrypted = manager.encrypt_api_key(
                generate_mcp_private_key_pem()
            )

        tool_name = personal_echo_tool_name(server.server_name) if is_personal else ECHO_TOOL_NAME
        tool = (
            await db.execute(
                select(McpToolCache).where(
                    McpToolCache.server_id == server_id,
                    McpToolCache.tool_name == tool_name,
                )
            )
        ).scalar_one_or_none()
        if tool is None:
            # 任何一次改名（编辑平台 Echo、迁移历史个人 Echo）都会让工具标识与常量对不上。
            # tool_id 是这个实例的稳定标识：先按它兜底改名，避免同主键重复插入报 500。
            stale_tool = (
                await db.execute(
                    select(McpToolCache).where(
                        McpToolCache.server_id == server_id,
                        McpToolCache.id == tool_id,
                    )
                )
            ).scalar_one_or_none()
            if stale_tool is not None:
                stale_tool.tool_name = tool_name
                tool = stale_tool
                try:
                    from app.services.conversation_resource_service import (
                        ConversationResourceService,
                    )

                    await ConversationResourceService.rewrite_mcp_tool_name(
                        owner_id=owner_id,
                        tool_id=stale_tool.id,
                        new_name=tool_name,
                    )
                except Exception:  # noqa: BLE001
                    pass
        if tool is None and is_personal:
            # 兼容旧数据：上一版个人 Echo 复用了平台 Echo 的工具名，这里就地改名，
            # 否则平台/个人两个 Echo 同时存在时按名称查询会命中多行。
            legacy_tool = (
                await db.execute(
                    select(McpToolCache).where(
                        McpToolCache.server_id == server_id,
                        McpToolCache.tool_name == ECHO_TOOL_NAME,
                    )
                )
            ).scalar_one_or_none()
            if legacy_tool is not None:
                legacy_tool.tool_name = tool_name
                tool = legacy_tool
                # 已挂载的会话里存的是旧工具名，按工具缓存 id 精确纠名，用户不必重新勾选。
                try:
                    from app.services.conversation_resource_service import (
                        ConversationResourceService,
                    )

                    await ConversationResourceService.rewrite_mcp_tool_name(
                        owner_id=owner_id,
                        tool_id=legacy_tool.id,
                        new_name=tool_name,
                    )
                except Exception:  # noqa: BLE001
                    pass
        if tool is None:
            db.add(
                McpToolCache(
                    id=tool_id,
                    server_id=server_id,
                    tool_name=tool_name,
                    tool_description=ECHO_TOOL_DESCRIPTION,
                    parameter_schema=echo_tool_schema(),
                    is_published=True,
                    is_available=True,
                )
            )
        else:
            tool.tool_description = ECHO_TOOL_DESCRIPTION
            tool.parameter_schema = echo_tool_schema()
            tool.is_published = True
            tool.is_available = True

    await db.commit()
    _clear_runtime_tool_cache()
    response = McpServerResponse.model_validate(server)
    response.tool_count = 1
    response.published_tool_count = 1
    response.stale_tool_count = 0
    response.auth_headers_configured = mcp_auth_headers_configured(server)
    response.authorization_configured, response.masked_auth_headers = mcp_auth_headers_summary(server)
    return response

@router.post("/servers", response_model=McpServerResponse, dependencies=[Depends(require_permission("element", "element:mcp_management:create"))])
async def create_mcp_server(
    data: McpServerWrite,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    is_admin = user.get("role") == "admin"
    target_scope = data.scope or "global"
    
    if target_scope == "global" and not is_admin:
        raise HTTPException(status_code=403, detail="只有系统管理员才能创建平台公共 MCP 服务")

    server_name = _normalized_server_name(data.server_name)
    if not server_name:
        raise HTTPException(status_code=400, detail="服务显示名称不能为空")

    # The full tool identity is server_name:remote_tool_name, so the server
    # display name must be unique across all scopes.
    existing_name = await _find_server_with_name(db, server_name)
    if existing_name:
        raise HTTPException(status_code=400, detail=f"服务显示名称 '{server_name}' 已存在，请修改名称后保存")

    user_id = _get_user_id(user) if target_scope == "personal" else None
    server_id = str(uuid.uuid4())
    server_data = data.model_dump(exclude={"fixed_token", "authorization_enabled", "auth_headers_patch"})
    auth_headers = _parse_auth_headers(data.auth_headers)
    raw_authorization = next(
        (
            value
            for key, value in auth_headers.items()
            if str(key).strip().casefold() == "authorization"
        ),
        None,
    )
    if data.authorization_enabled is False:
        _remove_header_case_insensitive(auth_headers, "Authorization")
    elif data.fixed_token:
        _remove_header_case_insensitive(auth_headers, "Authorization")
    elif data.authorization_enabled is True and not raw_authorization:
        raise HTTPException(status_code=400, detail="开启 Authorization 后请输入 Token")
    server_data["auth_headers"] = encrypt_mcp_auth_headers(auth_headers)
    server_data["server_name"] = server_name
    server_data["remark"] = _normalized_remark(data.remark)
    server_data["scope"] = target_scope
    server_data["user_id"] = user_id
    
    if data.fixed_token:
        server_data["fixed_token_encrypted"] = get_api_key_manager().encrypt_api_key(data.fixed_token)
    if data.user_assertion_enabled:
        server_data["user_assertion_audience"] = (
            data.user_assertion_audience or _default_user_assertion_audience(server_id)
        )
        server_data["user_assertion_issuer"] = "nanzi-platform"
        server_data["user_assertion_private_key_encrypted"] = get_api_key_manager().encrypt_api_key(
            generate_mcp_private_key_pem()
        )
        server_data["user_assertion_key_id"] = data.user_assertion_key_id or f"mcp-{uuid.uuid4().hex[:16]}"
    new_server = McpServer(id=server_id, **server_data)
    db.add(new_server)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to commit new McpServer: {e}")
        raise HTTPException(status_code=400, detail="服务保存冲突，请检查服务名称是否重复")
    
    # Auto-sync tools immediately after creation
    try:
        await McpClientService.sync_tools(server_id)
    except Exception as e:
        logger.warning(f"Initial sync failed for new server {server_id}: {e}")

    authorization_configured, masked_auth_headers = mcp_auth_headers_summary(new_server)
    return {
        "server_name": server_name,
        "sse_url": data.sse_url,
        "enabled_status": data.enabled_status,
        "scope": target_scope,
        "remark": server_data["remark"],
        "credential_mode": data.credential_mode,
        "user_assertion_enabled": data.user_assertion_enabled,
        "user_assertion_header": data.user_assertion_header,
        "user_assertion_audience": server_data.get("user_assertion_audience"),
        "user_assertion_key_id": server_data.get("user_assertion_key_id"),
        "user_assertion_issuer": server_data.get("user_assertion_issuer"),
        "auth_headers_configured": mcp_auth_headers_configured(new_server),
        "authorization_configured": authorization_configured,
        "masked_auth_headers": masked_auth_headers,
        "id": server_id,
        "tool_count": 0,
        "published_tool_count": 0,
        "stale_tool_count": 0,
    }

@router.put("/servers/{server_id}", response_model=McpServerResponse, dependencies=[Depends(require_permission("element", "element:mcp_management:edit"))])
async def update_mcp_server(
    server_id: str,
    data: McpServerWrite,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(stmt)).scalar_one_or_none()
    if not server: raise HTTPException(status_code=404, detail="Server not found")
    
    is_admin = user.get("role") == "admin"
    if server.scope == "global" and not is_admin:
        raise HTTPException(status_code=403, detail="只有系统管理员才能编辑平台公共 MCP 服务")
    if server.scope == "personal" and server.user_id != _get_user_id(user):
        raise HTTPException(status_code=403, detail="无法修改其他用户的私有 MCP 服务")

    server_name = _normalized_server_name(data.server_name)
    if not server_name:
        raise HTTPException(status_code=400, detail="服务显示名称不能为空")

    duplicate_name = await _find_server_with_name(
        db,
        server_name,
        exclude_server_id=server_id,
    )
    if duplicate_name:
        raise HTTPException(status_code=400, detail=f"服务显示名称 '{server_name}' 已存在，请修改名称后保存")

    if server.server_name != server_name:
        renamed_tools = await _migrate_server_name_references(
            db, server_id, server.server_name, server_name
        )
        await _sync_renamed_tools_to_conversations(
            renamed_tools,
            owner_id=server.user_id if server.scope == "personal" else None,
        )
    server.server_name = server_name
    server.sse_url = data.sse_url
    _apply_mcp_auth_update(server, data)
    server.credential_mode = data.credential_mode
    server.user_assertion_enabled = data.user_assertion_enabled
    server.user_assertion_header = data.user_assertion_header
    if data.user_assertion_enabled:
        server.user_assertion_audience = (
            data.user_assertion_audience
            or server.user_assertion_audience
            or _default_user_assertion_audience(server_id)
        )
        server.user_assertion_key_id = data.user_assertion_key_id or server.user_assertion_key_id
        server.user_assertion_issuer = "nanzi-platform"
    else:
        server.user_assertion_audience = data.user_assertion_audience
        server.user_assertion_key_id = data.user_assertion_key_id
        server.user_assertion_issuer = data.user_assertion_issuer
    if data.user_assertion_enabled and not server.user_assertion_key_id:
        server.user_assertion_key_id = f"mcp-{uuid.uuid4().hex[:16]}"
    if data.user_assertion_enabled and not server.user_assertion_private_key_encrypted:
        server.user_assertion_private_key_encrypted = get_api_key_manager().encrypt_api_key(
            generate_mcp_private_key_pem()
        )
    server.enabled_status = data.enabled_status
    # 启用/禁用等局部更新可能不传 remark，避免误清空
    if "remark" in data.model_fields_set:
        server.remark = _normalized_remark(data.remark)
    await db.commit()
    _clear_runtime_tool_cache()

    # 会话在建立时缓存了 sse_url 与解密后的 Header，唯一兜底是 300s 空闲回收；
    # 改了地址 / Token / 断言配置后必须先淘汰，否则接下来的同步与调用还在用旧连接。
    try:
        await McpClientService.evict_session(server_id)
    except Exception as e:
        logger.warning(f"Evicting session failed during update for server {server_id}: {e}")

    # Only enabled servers should be synchronized. Syncing a disabled server
    # would mark it enabled again inside McpClientService.sync_tools().
    if server.enabled_status == 1:
        try:
            await McpClientService.sync_tools(server_id)
        except Exception as e:
            logger.warning(f"Sync failed during update for server {server_id}: {e}")
    
    # Return with updated counts
    count_stmt = select(func.count(McpToolCache.id)).where(McpToolCache.server_id == server_id)
    total = (await db.execute(count_stmt.where(McpToolCache.is_available == True))).scalar() or 0
    stale_stmt = select(func.count(McpToolCache.id)).where(
        McpToolCache.server_id == server_id,
        McpToolCache.is_available == False,
    )
    stale = (await db.execute(stale_stmt)).scalar() or 0
    pub_stmt = select(func.count(McpToolCache.id)).where(
        McpToolCache.server_id == server_id,
        McpToolCache.is_available == True,
        McpToolCache.is_published == True,
    )
    pub = (await db.execute(pub_stmt)).scalar() or 0
    
    response_data = data.model_dump(exclude={"fixed_token", "auth_headers"})
    response_data["server_name"] = server_name
    response_data["user_assertion_audience"] = server.user_assertion_audience
    response_data["user_assertion_key_id"] = server.user_assertion_key_id
    response_data["user_assertion_issuer"] = server.user_assertion_issuer
    response_data["auth_headers_configured"] = mcp_auth_headers_configured(server)
    response_data["authorization_configured"], response_data["masked_auth_headers"] = (
        mcp_auth_headers_summary(server)
    )
    return {
        **response_data,
        "id": server_id,
        "scope": server.scope,
        "user_id": server.user_id,
        "tool_count": total,
        "published_tool_count": pub,
        "stale_tool_count": stale,
    }

@router.delete("/servers/{server_id}", dependencies=[Depends(require_permission("element", "element:mcp_management:delete"))])
async def delete_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(stmt)).scalar_one_or_none()
    if not server: raise HTTPException(status_code=404, detail="Server not found")

    is_admin = user.get("role") == "admin"
    if server.scope == "global" and not is_admin:
        raise HTTPException(status_code=403, detail="只有系统管理员才能删除平台公共 MCP 服务")
    if server.scope == "personal" and server.user_id != _get_user_id(user):
        raise HTTPException(status_code=403, detail="无法删除其他用户的私有 MCP 服务")

    # 1. Cascade delete associated tools first
    await db.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
    
    # 2. Delete the server itself
    await db.execute(delete(McpServer).where(McpServer.id == server_id))
    
    await db.commit()
    _clear_runtime_tool_cache()
    try:
        await McpClientService.evict_session(server_id)
    except Exception as e:
        logger.warning(f"Evicting session failed during delete for server {server_id}: {e}")
    return {"message": "Server and associated tools deleted"}

@router.delete(
    "/servers/{server_id}/stale-tools",
    dependencies=[Depends(require_permission("element", "element:mcp_management:delete"))],
)
async def cleanup_mcp_server_stale_tools(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """清理该服务下「远端已删除」的工具缓存，并从智能体版本里剔除死引用。

    与删除服务不同：这里不删服务本身，也不动出站调用审计（审计表对
    ``sys_mcp_servers`` 是 ``ondelete=CASCADE``，删服务会连带清空历史）。
    """
    server = (
        await db.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    _ensure_server_control_access(server, user)

    result = await cleanup_stale_tools(db, server_id)
    if result["removed_count"] == 0:
        return {
            "status": "success",
            "message": "没有需要清理的远端已删除工具",
            **result,
        }

    _clear_runtime_tool_cache()
    message = f"已清理 {result['removed_count']} 个远端已删除的工具"
    if result["affected_agent_versions"]:
        message += f"，并从 {result['affected_agent_versions']} 个智能体版本配置中移除了对应引用"
    return {"status": "success", "message": message, **result}


@router.post("/servers/{server_id}/sync", dependencies=[Depends(require_permission("element", "element:mcp_management:sync"))])
async def sync_mcp_tools(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(stmt)).scalar_one_or_none()
    if not server: raise HTTPException(status_code=404, detail="Server not found")

    is_admin = user.get("role") == "admin"
    if server.scope == "global" and not is_admin:
        raise HTTPException(status_code=403, detail="只有系统管理员才能同步平台公共 MCP 服务")
    if server.scope == "personal" and server.user_id != _get_user_id(user) and not is_admin:
        raise HTTPException(status_code=403, detail="无法同步其他用户的私有 MCP 服务")

    try:
        sync_result = await McpClientService.sync_tools(server_id) or {}
        _clear_runtime_tool_cache()
        stale_unpublished = int(sync_result.get("stale_unpublished", 0))
        return {
            "status": "success",
            "message": (
                f"工具同步成功，已标记 {int(sync_result.get('remote_deleted_count', 0))} 个远端已删除工具"
                if sync_result.get("remote_deleted_count")
                else "工具同步成功"
            ),
            "stale_unpublished": stale_unpublished,
            "remote_deleted_count": int(sync_result.get("remote_deleted_count", 0)),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/{server_id}/usage", response_model=McpServerUsageResponse)
async def get_mcp_server_usage(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    server_stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(server_stmt)).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.scope == "personal" and server.user_id != _get_user_id(user) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无法查看其他用户的私有 MCP 使用情况")

    tool_rows = (
        await db.execute(
            select(McpToolCache.tool_name, McpToolCache.is_published).where(
                McpToolCache.server_id == server_id
            )
        )
    ).all()
    all_tool_names = {row[0] for row in tool_rows if row[0]}
    published_tool_names = {row[0] for row in tool_rows if row[0] and row[1]}

    version_rows = (
        await db.execute(
            select(AIAgentVersion, AIAgent)
            .join(AIAgent, AIAgent.id == AIAgentVersion.agent_id)
        )
    ).all()

    usage_by_agent: Dict[str, Dict[str, Any]] = {}
    bound_version_count = 0
    for version, agent in version_rows:
        matched_tool_names = _configured_tool_names(version.tools) & all_tool_names
        if not matched_tool_names:
            continue

        bound_version_count += 1
        item = usage_by_agent.setdefault(
            agent.id,
            {
                "id": agent.id,
                "name": agent.name,
                "display_name": agent.display_name or agent.name,
                "is_enabled": bool(agent.is_enabled),
                "active": False,
                "version_count": 0,
            },
        )
        item["version_count"] += 1
        if (
            agent.is_enabled
            and str(version.status or "").upper() == "PUBLISHED"
            and matched_tool_names & published_tool_names
        ):
            item["active"] = True

    agents = sorted(
        usage_by_agent.values(),
        key=lambda item: (not item["active"], item["display_name"] or item["name"]),
    )
    return {
        "server_id": server_id,
        "bound_agent_count": len(agents),
        "active_agent_count": sum(1 for item in agents if item["active"]),
        "bound_version_count": bound_version_count,
        "agents": agents,
    }

@router.get("/servers/{server_id}/tools", response_model=List[McpToolResponseWithUsage])
async def list_mcp_server_tools(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    server_stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(server_stmt)).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.scope == "personal" and server.user_id != _get_user_id(user) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无法查看其他用户的私有 MCP 工具")

    stmt = select(McpToolCache).where(McpToolCache.server_id == server_id)
    tools = (await db.execute(stmt)).scalars().all()
    
    v_stmt = select(AIAgentVersion.tools)
    all_versions_tools = (await db.execute(v_stmt)).scalars().all()
    
    usage_map = {}
    for tool_config in all_versions_tools:
        if not tool_config: continue
        actual_list = tool_config
        if isinstance(tool_config, str):
            try: actual_list = json.loads(tool_config)
            except: continue
        if not isinstance(actual_list, list): continue
        for t in actual_list:
            t_name = t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else None)
            if t_name: usage_map[t_name] = usage_map.get(t_name, 0) + 1

    res = []
    for t in tools:
        item = McpToolResponseWithUsage.model_validate(t)
        item.usage_count = usage_map.get(t.tool_name, 0)
        res.append(item)
    return res

class ToolExecutionRequest(BaseModel):
    arguments: Dict[str, Any]

@router.post("/tools/{tool_id}/execute", dependencies=[Depends(require_permission("element", "element:mcp_management:execute"))])
async def execute_mcp_tool(
    tool_id: str,
    req: ToolExecutionRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
):
    stmt = select(McpToolCache).where(McpToolCache.id == tool_id)
    tool = (await db.execute(stmt)).scalar_one_or_none()
    if not tool: raise HTTPException(status_code=404, detail="Tool not found")

    server_stmt = select(McpServer).where(McpServer.id == tool.server_id)
    server = (await db.execute(server_stmt)).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    _ensure_server_control_access(server, user)
    if server.enabled_status != 1:
        raise HTTPException(status_code=409, detail="MCP 服务已禁用，无法执行工具")
    if not tool.is_available:
        raise HTTPException(status_code=409, detail="工具已被远端 MCP 服务删除，无法执行")
    if not tool.is_published:
        raise HTTPException(status_code=409, detail="工具尚未发布，无法执行")

    mcp_auth = {
        "user_assertion_sent": False,
        "header": None,
        "value_masked": None,
        "audience": None,
        "issuer": None,
        "key_id": None,
    }
    try:
        signed_user_mode = bool(server.user_assertion_enabled)
        if signed_user_mode:
            test_user_info = {
                key: user.get(key)
                for key in (
                    "user_id",
                    "user_name",
                    "real_name",
                    "dept_code",
                    "org_path",
                    "extra_data",
                )
                if user.get(key) is not None
            }
            result = await McpClientService.call_remote_tool(
                server_id=tool.server_id,
                tool_name=tool.tool_name.split(":", 1)[-1],
                arguments=req.arguments,
                user_info=test_user_info,
                agent_info={
                    "agent_id": "mcp-tool-tester",
                    "agent_name": "MCP 工具测试台",
                },
                request_id=str(uuid.uuid4()),
            )
            mcp_auth = {
                "user_assertion_sent": True,
                "header": server.user_assertion_header or "X-Nanzi-User-Assertion",
                "value_masked": "********",
                "audience": server.user_assertion_audience,
                "issuer": server.user_assertion_issuer or "nanzi-platform",
                "key_id": server.user_assertion_key_id,
            }
        else:
            lc_tool = McpToolFactory.create_tool(tool)
            result = await lc_tool.ainvoke(req.arguments)
        return {"status": "success", "result": result, "mcp_auth": mcp_auth}
    except Exception as e:
        return {"status": "error", "message": str(e), "mcp_auth": mcp_auth}

@router.put("/tools/{tool_id}/publish", dependencies=[Depends(require_permission("element", "element:mcp_management:publish"))])
async def toggle_tool_publish(
    tool_id: str,
    published: bool,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key)
) -> Dict:
    tool_stmt = select(McpToolCache).where(McpToolCache.id == tool_id)
    tool = (await db.execute(tool_stmt)).scalar_one_or_none()
    if not tool: raise HTTPException(status_code=404, detail="Tool not found")

    server_stmt = select(McpServer).where(McpServer.id == tool.server_id)
    server = (await db.execute(server_stmt)).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    _ensure_server_control_access(server, user)
    if server.enabled_status != 1:
        raise HTTPException(status_code=409, detail="MCP 服务已禁用，无法修改工具发布状态")
    if not tool.is_available:
        raise HTTPException(status_code=409, detail="远端已删除的工具不能发布或下线")

    stmt = update(McpToolCache).where(McpToolCache.id == tool_id).values(is_published=published)
    await db.execute(stmt)
    await db.commit()
    _clear_runtime_tool_cache()
    return {"status": "success", "is_published": published}


@router.get("/servers/{server_id}/outbound-logs")
async def get_mcp_server_outbound_logs(
    server_id: str,
    tool_name: Optional[str] = None,
    status: Optional[str] = None,
    range: str = "7d",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
) -> Dict[str, Any]:
    server_stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(server_stmt)).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.scope == "personal" and server.user_id != _get_user_id(user) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无法查看其他用户的私有 MCP 调用日志")

    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if range == "24h":
        start_time = now - timedelta(hours=24)
    elif range == "30d":
        start_time = now - timedelta(days=30)
    else:
        start_time = now - timedelta(days=7)

    filters = [
        McpOutboundAuditLog.server_id == server_id,
        McpOutboundAuditLog.created_at >= start_time,
    ]
    if tool_name:
        filters.append(McpOutboundAuditLog.tool_name == tool_name)
    if status and status != "all":
        filters.append(McpOutboundAuditLog.status == status)

    # 聚合指标计算
    summary_stmt = select(
        func.count().label("total"),
        func.sum(case((McpOutboundAuditLog.status == "success", 1), else_=0)).label("success_count"),
        func.sum(case((McpOutboundAuditLog.status != "success", 1), else_=0)).label("failed_count"),
        func.avg(McpOutboundAuditLog.latency_ms).label("avg_latency"),
    ).where(McpOutboundAuditLog.server_id == server_id, McpOutboundAuditLog.created_at >= start_time)

    summary_row = (await db.execute(summary_stmt)).one()
    total_calls = summary_row.total or 0
    success_calls = int(summary_row.success_count or 0)
    failed_calls = int(summary_row.failed_count or 0)
    avg_latency = round(float(summary_row.avg_latency or 0), 1)
    success_rate = round((success_calls / total_calls * 100), 1) if total_calls > 0 else 100.0

    # 分页流水查询
    count_stmt = select(func.count()).select_from(McpOutboundAuditLog).where(*filters)
    filtered_total = (await db.execute(count_stmt)).scalar() or 0

    logs_stmt = (
        select(McpOutboundAuditLog)
        .where(*filters)
        .order_by(McpOutboundAuditLog.created_at.desc())
        .offset((max(1, page) - 1) * page_size)
        .limit(page_size)
    )
    log_rows = (await db.execute(logs_stmt)).scalars().all()

    items = [
        {
            "id": log.id,
            "server_id": log.server_id,
            "server_name": log.server_name,
            "tool_name": log.tool_name,
            "agent_id": log.agent_id,
            "agent_name": log.agent_name,
            "user_id": log.user_id,
            "user_name": log.user_name,
            "trace_id": log.trace_id,
            "status": log.status,
            "latency_ms": log.latency_ms,
            "error_message": log.error_message,
            "tool_input": log.tool_input,
            "tool_output": log.tool_output,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in log_rows
    ]

    return {
        "items": items,
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "total_calls": total_calls,
            "success_calls": success_calls,
            "failed_calls": failed_calls,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
        },
    }


@router.get("/outbound-summary/mine")
async def get_my_mcp_outbound_summary(
    range: str = "7d",
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
) -> Dict[str, Any]:
    """「我的 MCP」调用概览：只统计本人名下的个人 MCP。

    全平台审计（``/outbound-logs``）只给管理员，因为里面混着别人的调用明细；
    普通用户需要的是「我的服务被调了多少次、哪个工具最热门、失败率多少」，
    这部分只按 ``user_id = 自己`` 聚合，不返回任何他人调用的明细行。
    """
    user_id = _get_user_id(user)
    window = range if range in {"24h", "7d", "30d"} else "7d"

    server_rows = (
        await db.execute(
            select(McpServer.id, McpServer.server_name).where(
                McpServer.scope == "personal",
                McpServer.user_id == user_id,
            )
        )
    ).all()
    server_names = {row.id: row.server_name for row in server_rows}
    server_ids = list(server_names)

    empty_summary = {
        "total_calls": 0,
        "success_calls": 0,
        "failed_calls": 0,
        "success_rate": 100.0,
        "avg_latency_ms": 0.0,
    }
    if not server_ids:
        return {
            "range": window,
            "summary": empty_summary,
            "servers": [],
            "tools": [],
            "daily": [],
        }

    from datetime import datetime, timedelta

    now = datetime.utcnow()
    if window == "24h":
        start_time = now - timedelta(hours=24)
    elif window == "30d":
        start_time = now - timedelta(days=30)
    else:
        start_time = now - timedelta(days=7)

    scope_filters = (
        McpOutboundAuditLog.server_id.in_(server_ids),
        McpOutboundAuditLog.created_at >= start_time,
    )

    summary_row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((McpOutboundAuditLog.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((McpOutboundAuditLog.status != "success", 1), else_=0)).label("failed_count"),
                func.avg(McpOutboundAuditLog.latency_ms).label("avg_latency"),
            ).where(*scope_filters)
        )
    ).one()

    total_calls = int(summary_row.total or 0)
    success_calls = int(summary_row.success_count or 0)
    failed_calls = int(summary_row.failed_count or 0)
    summary = {
        "total_calls": total_calls,
        "success_calls": success_calls,
        "failed_calls": failed_calls,
        "success_rate": round(success_calls / total_calls * 100, 1) if total_calls else 100.0,
        "avg_latency_ms": round(float(summary_row.avg_latency or 0), 1),
    }

    total_label = func.count().label("total_calls")
    per_server_rows = (
        await db.execute(
            select(
                McpOutboundAuditLog.server_id,
                total_label,
                func.sum(case((McpOutboundAuditLog.status != "success", 1), else_=0)).label("failed_calls"),
                func.max(McpOutboundAuditLog.created_at).label("last_called_at"),
            )
            .where(*scope_filters)
            .group_by(McpOutboundAuditLog.server_id)
            .order_by(desc(total_label))
            .limit(5)
        )
    ).all()
    servers = [
        {
            "server_id": row.server_id,
            "server_name": server_names.get(row.server_id) or "",
            "total_calls": int(row.total_calls or 0),
            "failed_calls": int(row.failed_calls or 0),
            "last_called_at": row.last_called_at.isoformat() if row.last_called_at else None,
        }
        for row in per_server_rows
    ]

    tool_label = func.count().label("total_calls")
    per_tool_rows = (
        await db.execute(
            select(McpOutboundAuditLog.tool_name, tool_label)
            .where(*scope_filters)
            .group_by(McpOutboundAuditLog.tool_name)
            .order_by(desc(tool_label))
            .limit(5)
        )
    ).all()
    tools = [
        {"tool_name": row.tool_name, "total_calls": int(row.total_calls or 0)}
        for row in per_tool_rows
    ]

    day_label = func.date(McpOutboundAuditLog.created_at).label("day")
    per_day_rows = (
        await db.execute(
            select(day_label, total_label)
            .where(*scope_filters)
            .group_by(day_label)
            .order_by(day_label)
        )
    ).all()
    daily = [
        {
            "date": str(row.day),
            "total_calls": int(row.total_calls or 0),
        }
        for row in per_day_rows
    ]

    return {
        "range": window,
        "summary": summary,
        "servers": servers,
        "tools": tools,
        "daily": daily,
    }


@router.get("/outbound-logs")
async def get_all_mcp_outbound_logs(
    server_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    status: Optional[str] = None,
    range: str = "7d",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有系统管理员可查看全平台 MCP 审计日志")

    # 获取所有 MCP servers 供下拉列表和范围筛选
    server_query = select(McpServer.id, McpServer.server_name).order_by(McpServer.server_name)
    server_rows = (await db.execute(server_query)).all()
    accessible_server_ids = [row.id for row in server_rows]
    server_options = [{"id": row.id, "server_name": row.server_name} for row in server_rows]

    if not accessible_server_ids:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "server_options": [],
            "summary": {
                "total_calls": 0,
                "success_calls": 0,
                "failed_calls": 0,
                "success_rate": 100.0,
                "avg_latency_ms": 0,
            },
        }

    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if range == "24h":
        start_time = now - timedelta(hours=24)
    elif range == "30d":
        start_time = now - timedelta(days=30)
    else:
        start_time = now - timedelta(days=7)

    filters = [
        McpOutboundAuditLog.created_at >= start_time,
    ]
    if server_id:
        if server_id not in accessible_server_ids:
            raise HTTPException(status_code=403, detail="无权查看该 MCP 服务的调用日志")
        filters.append(McpOutboundAuditLog.server_id == server_id)
    else:
        filters.append(McpOutboundAuditLog.server_id.in_(accessible_server_ids))

    if tool_name:
        filters.append(McpOutboundAuditLog.tool_name.ilike(f"%{tool_name}%"))
    if status and status != "all":
        filters.append(McpOutboundAuditLog.status == status)

    # 聚合指标计算
    summary_filters = [
        McpOutboundAuditLog.created_at >= start_time,
    ]
    if server_id:
        summary_filters.append(McpOutboundAuditLog.server_id == server_id)
    else:
        summary_filters.append(McpOutboundAuditLog.server_id.in_(accessible_server_ids))

    summary_stmt = select(
        func.count().label("total"),
        func.sum(case((McpOutboundAuditLog.status == "success", 1), else_=0)).label("success_count"),
        func.sum(case((McpOutboundAuditLog.status != "success", 1), else_=0)).label("failed_count"),
        func.avg(McpOutboundAuditLog.latency_ms).label("avg_latency"),
    ).where(*summary_filters)

    summary_row = (await db.execute(summary_stmt)).one()
    total_calls = summary_row.total or 0
    success_calls = int(summary_row.success_count or 0)
    failed_calls = int(summary_row.failed_count or 0)
    avg_latency = round(float(summary_row.avg_latency or 0), 1)
    success_rate = round((success_calls / total_calls * 100), 1) if total_calls > 0 else 100.0

    # 分页流水查询
    count_stmt = select(func.count()).select_from(McpOutboundAuditLog).where(*filters)
    filtered_total = (await db.execute(count_stmt)).scalar() or 0

    logs_stmt = (
        select(McpOutboundAuditLog)
        .where(*filters)
        .order_by(McpOutboundAuditLog.created_at.desc())
        .offset((max(1, page) - 1) * page_size)
        .limit(page_size)
    )
    log_rows = (await db.execute(logs_stmt)).scalars().all()

    items = [
        {
            "id": log.id,
            "server_id": log.server_id,
            "server_name": log.server_name,
            "tool_name": log.tool_name,
            "agent_id": log.agent_id,
            "agent_name": log.agent_name,
            "user_id": log.user_id,
            "user_name": log.user_name,
            "trace_id": log.trace_id,
            "status": log.status,
            "latency_ms": log.latency_ms,
            "error_message": log.error_message,
            "tool_input": log.tool_input,
            "tool_output": log.tool_output,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in log_rows
    ]

    return {
        "items": items,
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
        "server_options": server_options,
        "summary": {
            "total_calls": total_calls,
            "success_calls": success_calls,
            "failed_calls": failed_calls,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
        },
    }


# ---------------------------------------------------------------------------
# 个人 MCP 分享：搜索接收人 / 发送 / 收到的分享 / 我的分享
# ---------------------------------------------------------------------------

def _share_http_exception(exc: Exception) -> Optional[HTTPException]:
    """把域异常映射成 HTTP 错误；不是分享域异常时返回 None 交给调用方兜底。"""
    if isinstance(exc, McpShareNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, McpSharePermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, McpShareConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return None


def _raise_if_share_error(exc: Exception) -> None:
    mapped = _share_http_exception(exc)
    if mapped is not None:
        raise mapped


class McpServerShareRequest(BaseModel):
    target_user_ids: List[int] = Field(default_factory=list)
    message: Optional[str] = None
    # 默认携带凭证：私有 MCP 没有 Token 就是连不上的空壳
    include_credentials: bool = True


class McpShareAcceptRequest(BaseModel):
    """安装向导里允许用户改的三项；不传则全部沿用分享快照。"""

    server_name: Optional[str] = None
    remark: Optional[str] = None
    sse_url: Optional[str] = None


@router.get("/share-targets", summary="搜索可分享的同事")
async def list_mcp_share_targets(
    keyword: str = Query(""),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    targets = await search_share_targets(
        db, user=user, keyword=keyword, limit=max(1, min(limit, 50))
    )
    return {"status": "success", "data": targets}


@router.post("/servers/{server_id}/shares", summary="把个人 MCP 分享给指定同事")
async def create_mcp_server_share(
    server_id: str,
    data: McpServerShareRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    try:
        result = await share_mcp_server(
            db,
            user=user,
            server_id=server_id,
            target_user_ids=data.target_user_ids,
            message=data.message,
            include_credentials=data.include_credentials,
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        _raise_if_share_error(exc)
        logger.error("[MCP] 分享失败 server=%s: %s", server_id, exc)
        raise HTTPException(status_code=500, detail="分享 MCP 失败")


@router.get("/shares/summary", summary="分享角标计数")
async def get_mcp_share_summary(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    data = await mcp_share_menu_counts(db, user=user)
    return {"status": "success", "data": data}


@router.get("/shares/sent", summary="我发出的 MCP 分享")
async def list_mcp_sent_shares(
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    data = await list_sent_mcp_shares(
        db, user=user, status=status, limit=limit, offset=offset
    )
    return {"status": "success", "data": data}


@router.get("/shares/received", summary="我收到的 MCP 分享")
async def list_mcp_received_shares(
    status: str = Query("PENDING"),
    limit: int = Query(100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    data = await list_received_mcp_shares(
        db, user=user, status=status, limit=limit, offset=offset
    )
    return {"status": "success", "data": data}


@router.post("/shares/received/{share_id}/accept", summary="接受 MCP 分享并安装到我的 MCP")
async def accept_mcp_received_share(
    share_id: str,
    data: Optional[McpShareAcceptRequest] = None,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    overrides = data or McpShareAcceptRequest()
    try:
        result = await accept_mcp_share(
            db,
            user=user,
            share_id=share_id,
            server_name=overrides.server_name,
            remark=overrides.remark,
            sse_url=overrides.sse_url,
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        _raise_if_share_error(exc)
        logger.error("[MCP] 接受分享失败 share=%s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="接受 MCP 分享失败")


@router.post("/shares/received/{share_id}/reject", summary="拒绝 MCP 分享")
async def reject_mcp_received_share(
    share_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    try:
        data = await reject_mcp_share(db, user=user, share_id=share_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_share_error(exc)
        logger.error("[MCP] 拒绝分享失败 share=%s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="拒绝 MCP 分享失败")


@router.delete("/shares/sent/{share_id}", summary="撤回 MCP 分享")
async def withdraw_mcp_sent_share(
    share_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    try:
        data = await withdraw_mcp_share(db, user=user, share_id=share_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        _raise_if_share_error(exc)
        logger.error("[MCP] 撤回分享失败 share=%s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="撤回 MCP 分享失败")

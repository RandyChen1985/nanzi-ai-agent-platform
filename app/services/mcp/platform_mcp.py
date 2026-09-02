"""NanZi Platform MCP Resource Server。

Platform MCP 与现有出站 MCP 工具集是两条方向相反的链路：这里负责外部系统
调用 NanZi，认证凭证是 NanZi OAuth2 签发的 Bearer Access Token。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from fastapi import HTTPException

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP

from app.core.config import settings
from app.core import redis
from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion
from app.models.audit import AgentExecutionHistory
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.metadata import MetaDataset, MetaMetric, MetaTable
from app.models.platform_mcp import McpInboundAuditLog, McpOAuthClient
from app.models.user import User
from app.services.ai.knowledge_utils import (
    is_knowledge_base_enabled,
    normalize_dataset_ids,
    resolve_rag_retrieval_params,
)
from app.services.ai.ragflow_client import RagFlowClient
from app.services.config_service import ConfigService
from app.services.mcp.platform_oauth import (
    MCP_RESOURCE,
    MCP_RESOURCE_URI_SUFFIX,
    McpPrincipal,
    PlatformMcpTokenVerifier,
    access_token_to_principal,
    intersect_authorized_ids,
)
from app.services.mcp.platform_config import PlatformMcpConfigService
from app.services.mcp.platform_mcp_support import (
    build_platform_user_info,
    decode_platform_cursor,
    encode_platform_cursor,
    serialize_metadata_dataset,
    serialize_metadata_metric,
    serialize_metadata_schema,
)
from app.services.mcp.security_audit import write_security_audit
from app.services.permission_service import PermissionService
from app.services.metadata_service import MetadataService
from app.services.mcp.transport_security import build_mcp_transport_security


logger = logging.getLogger(__name__)

PLATFORM_MCP_NAME = "NanZi Platform MCP"
PLATFORM_MCP_SCOPE = "knowledge:search"
PLATFORM_MCP_RESOURCE_URI_SUFFIX = MCP_RESOURCE_URI_SUFFIX


@dataclass(frozen=True)
class PlatformMcpMethodDefinition:
    name: str
    scope: str
    capability_group: str
    requires_user: bool
    description: str
    implemented: bool = False


async def check_platform_mcp_rate_limit(principal: McpPrincipal) -> None:
    """按 Client 和用户分别执行一分钟固定窗口限流；Redis 不可用时不阻断业务。"""
    client_limit = int(settings.MCP_RATE_LIMIT_CLIENT_PER_MINUTE)
    user_limit = int(settings.MCP_RATE_LIMIT_USER_PER_MINUTE)
    if client_limit <= 0 or user_limit <= 0:
        return
    client_redis = await redis.get_redis()
    if client_redis is None:
        return
    window = int(time.time() // 60)
    for identity, limit in (
        (f"client:{principal.client_id}", client_limit),
        (f"user:{principal.user_id or 'anonymous'}", user_limit),
    ):
        key = f"mcp_rate_limit:{identity}:{window}"
        current = await client_redis.incr(key)
        if current == 1:
            await client_redis.expire(key, 70)
        if current > limit:
            raise HTTPException(status_code=429, detail="Platform MCP 请求过于频繁，请稍后重试")


PLATFORM_MCP_METHODS = (
    PlatformMcpMethodDefinition(
        name="agent.list_allowed",
        scope="agent:list",
        capability_group="agent",
        requires_user=True,
        description="查询当前 NanZi 用户可以使用的智能体。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="agent.invoke",
        scope="agent:invoke",
        capability_group="agent",
        requires_user=True,
        description="以当前 NanZi 用户身份调用智能体。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="conversation.continue",
        scope="conversation:continue",
        capability_group="conversation",
        requires_user=True,
        description="继续当前 NanZi 用户有权访问的会话。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="knowledge.search",
        scope="knowledge:search",
        capability_group="knowledge",
        requires_user=True,
        description="在当前用户和外部 Client 共同允许的知识库范围内检索。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="metadata.list_datasets",
        scope="metadata:read",
        capability_group="metadata",
        requires_user=True,
        description="列出当前用户可访问的数据集。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="metadata.search",
        scope="metadata:search",
        capability_group="metadata",
        requires_user=True,
        description="搜索当前用户可访问的元数据。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="metadata.get_dataset",
        scope="metadata:read",
        capability_group="metadata",
        requires_user=True,
        description="获取一个有权限的数据集元信息。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="metadata.get_schema",
        scope="metadata:read",
        capability_group="metadata",
        requires_user=True,
        description="获取有权限的数据集表字段结构。",
        implemented=True,
    ),
    PlatformMcpMethodDefinition(
        name="metadata.get_metrics",
        scope="metadata:metrics:read",
        capability_group="metadata",
        requires_user=True,
        description="获取有权限的数据集指标口径。",
        implemented=True,
    ),
)


def get_method_definition(name: str) -> PlatformMcpMethodDefinition | None:
    return next((item for item in PLATFORM_MCP_METHODS if item.name == name), None)


def _platform_base_url() -> str:
    value = str(settings.APP_PUBLIC_URL or "http://localhost:8001").rstrip("/")
    return value


def platform_mcp_resource_url() -> str:
    return f"{_platform_base_url()}{MCP_RESOURCE_URI_SUFFIX}"


async def is_platform_mcp_enabled() -> bool:
    return await PlatformMcpConfigService.get_flag("platform_enabled")


async def is_platform_mcp_capability_enabled(capability_group: str) -> bool:
    return await PlatformMcpConfigService.get_flag(f"{capability_group}_enabled")


class PlatformFastMCP(FastMCP):
    """把能力组开关应用到 tools/list，调用时仍会再次校验。"""

    async def list_tools(self):  # type: ignore[no-untyped-def]
        if not await is_platform_mcp_enabled():
            return []
        tools = await super().list_tools()
        visible = []
        for tool in tools:
            definition = get_method_definition(tool.name)
            if definition is None or await is_platform_mcp_capability_enabled(definition.capability_group):
                visible.append(tool)
        return visible


platform_mcp = PlatformFastMCP(
    name=PLATFORM_MCP_NAME,
    instructions="NanZi 平台级能力入口；所有调用必须使用 NanZi OAuth2 Bearer Access Token。",
    streamable_http_path="/platform",
    json_response=True,
    stateless_http=True,
    transport_security=build_mcp_transport_security(settings.APP_PUBLIC_URL),
    token_verifier=PlatformMcpTokenVerifier(),
    auth=AuthSettings(
        issuer_url=_platform_base_url(),
        resource_server_url=platform_mcp_resource_url(),
        # MCP 入口只要求“已认证”；具体方法在工具内部校验各自 Scope，
        # 不能把所有方法 Scope 配成全局 required_scopes，否则任何 token 都会被
        # 当成必须同时拥有全部能力而拒绝。
        required_scopes=[],
    ),
)


@asynccontextmanager
async def platform_mcp_lifespan():
    """接入宿主 FastAPI 生命周期，避免首次 MCP 请求缺少 Task Group。"""
    async with platform_mcp.session_manager.run():
        yield


def _principal_from_context() -> McpPrincipal:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("Platform MCP 需要 OAuth2 Bearer Access Token")
    principal = access_token_to_principal(access_token)
    if principal.resource != platform_mcp_resource_url():
        raise PermissionError("OAuth Access Token 的 resource 不匹配 Platform MCP")
    return principal


def _require_scope(principal: McpPrincipal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PermissionError(f"缺少 MCP Scope: {scope}")


async def _resolve_knowledge_scope(
    principal: McpPrincipal,
    requested_ids: str | list[str] | None,
) -> tuple[list[str], list[str]]:
    """返回 (有效 dataset ids, 审计/诊断信息)。"""
    if not principal.is_user_delegated or principal.user_id is None:
        raise PermissionError("knowledge.search 需要用户授权模式")

    requested = normalize_dataset_ids(requested_ids)
    if requested_ids is not None and not requested:
        raise ValueError("knowledge_base_ids 格式无效")

    async with AsyncSessionLocal() as db:
        client = await db.execute(
            select(McpOAuthClient).where(McpOAuthClient.client_id == principal.client_id)
        )
        client_row = client.scalar_one_or_none()
        if client_row is None or client_row.status != "active":
            raise PermissionError("OAuth Client 不存在或已禁用")

        access = await PermissionService(db).get_knowledge_base_access(
            int(principal.user_id),
            principal.user_name,
        )
        user_allowed = access.get("accessible_ids")
        if user_allowed is None and requested is None:
            # 管理员没有资源 ID 限制时，从平台目录得到实际 dataset ID，不能
            # 把 None 误当成空权限。
            user_allowed = (
                await db.execute(
                    select(KnowledgeBaseMetadata.ragflow_dataset_id).where(
                        KnowledgeBaseMetadata.status != "deleted"
                    )
                )
            ).scalars().all()
        effective = intersect_authorized_ids(user_allowed, requested or None)
        if not effective:
            # 对外不区分“用户无权限”和“Client 未配置”，避免探测资源存在性。
            raise PermissionError("没有可检索的知识库范围")
        return effective, [
            f"user_id={principal.user_id}",
            f"client_id={principal.client_id}",
            f"effective_knowledge_base_count={len(effective)}",
        ]


async def _write_audit(
    principal: McpPrincipal,
    *,
    method_name: str = "knowledge.search",
    request_id: str,
    result_status: str,
    status_code: int,
    client_request_id: str | None = None,
    agent_id: str | None = None,
    conversation_id: str | None = None,
    dataset_id: str | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                McpInboundAuditLog(
                    id=str(uuid.uuid4()),
                    request_id=request_id,
                    client_id=principal.client_id,
                    user_id=principal.user_id,
                    auth_type=principal.auth_type,
                    method_name=method_name,
                    client_request_id=client_request_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    dataset_id=dataset_id,
                    scopes=list(principal.scopes),
                    status_code=status_code,
                    result_status=result_status,
                    error_code=error_code,
                    latency_ms=latency_ms,
                )
            )
            if status_code == 429:
                await write_security_audit(
                    db,
                    event_type="mcp_rate_limited",
                    client_id=principal.client_id,
                    user_id=principal.user_id,
                    result_status="denied",
                    error_code="MCP_RATE_LIMITED",
                )
            await db.commit()
    except Exception as exc:  # 审计失败不能把检索结果变成不可用
        logger.warning("Platform MCP audit write failed: %s", exc)


def _request_id(principal: McpPrincipal) -> str:
    return str(principal.claims.get("request_id") or uuid.uuid4())


async def _load_client(db: Any, principal: McpPrincipal) -> McpOAuthClient:
    client = (
        await db.execute(
            select(McpOAuthClient).where(
                McpOAuthClient.client_id == principal.client_id,
                McpOAuthClient.status == "active",
            )
        )
    ).scalar_one_or_none()
    if client is None:
        raise PermissionError("OAuth Client 不存在或已禁用")
    return client


async def _load_principal_user(
    db: Any,
    principal: McpPrincipal,
) -> tuple[User, dict[str, Any]]:
    if not principal.is_user_delegated or principal.user_id is None:
        raise PermissionError("当前 Platform MCP 方法需要用户授权模式")
    try:
        user_id = int(principal.user_id)
    except (TypeError, ValueError) as exc:
        raise PermissionError("OAuth 用户身份格式无效") from exc
    user = (
        await db.execute(
            select(User).where(User.id == user_id, User.status == 1)
        )
    ).scalar_one_or_none()
    if user is None:
        raise PermissionError("NanZi 用户不存在或已禁用")
    return user, build_platform_user_info(user)


async def _validate_platform_method(
    principal: McpPrincipal,
    method_name: str,
) -> PlatformMcpMethodDefinition:
    await check_platform_mcp_rate_limit(principal)
    definition = get_method_definition(method_name)
    if definition is None or not definition.implemented:
        raise RuntimeError(f"MCP_METHOD_NOT_IMPLEMENTED: {method_name}")
    _require_scope(principal, definition.scope)
    if not await is_platform_mcp_enabled():
        raise RuntimeError("MCP_PLATFORM_DISABLED")
    if not await is_platform_mcp_capability_enabled(definition.capability_group):
        raise RuntimeError(f"MCP_{definition.capability_group.upper()}_DISABLED")
    if definition.requires_user and not principal.is_user_delegated:
        raise PermissionError(f"{method_name} 需要用户授权模式")
    return definition


def _validate_limit(value: int, *, default: int, maximum: int) -> int:
    limit = default if value is None else int(value)
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit 必须在 1 到 {maximum} 之间")
    return limit


def _mcp_error_status(exc: BaseException) -> int:
    if isinstance(exc, HTTPException):
        return exc.status_code
    if isinstance(exc, ValueError):
        return 400
    if isinstance(exc, LookupError):
        return 404
    return 503


def _normalize_requested_ids(
    raw: str | Iterable[str] | None,
    *,
    field_name: str,
    maximum: int = 100,
) -> list[str] | None:
    if raw is None:
        return None
    values = [raw] if isinstance(raw, str) else list(raw)
    if len(values) > maximum:
        raise ValueError(f"{field_name} 数量不能超过 {maximum}")
    result = [str(value).strip() for value in values if str(value).strip()]
    if not result:
        raise ValueError(f"{field_name} 格式无效")
    return list(dict.fromkeys(result))


async def _resolve_metadata_dataset_ids(
    db: Any,
    principal: McpPrincipal,
    requested_ids: str | Iterable[str] | None = None,
) -> list[str]:
    await _load_client(db, principal)

    user, user_info = await _load_principal_user(db, principal)
    accessible = await MetadataService.list_accessible_dataset_options(
        db,
        user_id=int(user.id),
        is_admin=user_info.get("role") == "admin",
        status=1,
    )
    user_allowed: Iterable[Any] | None = [item.id for item in accessible]

    return intersect_authorized_ids(
        user_allowed,
        _normalize_requested_ids(
            requested_ids,
            field_name="dataset_ids",
        ),
    )


async def _load_authorized_metadata_datasets(
    db: Any,
    dataset_ids: list[str],
) -> list[MetaDataset]:
    numeric_ids: list[int] = []
    for value in dataset_ids:
        try:
            numeric_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not numeric_ids:
        return []
    result = await db.execute(
        select(MetaDataset)
        .where(MetaDataset.id.in_(numeric_ids), MetaDataset.status == 1)
        .options(
            selectinload(MetaDataset.tables).selectinload(MetaTable.columns),
            selectinload(MetaDataset.metrics),
        )
        .order_by(MetaDataset.id.asc())
    )
    return list(result.scalars().unique().all())


async def _load_authorized_agent(
    db: Any,
    principal: McpPrincipal,
    user_info: dict[str, Any],
    agent_id: str,
) -> AIAgent:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise ValueError("agent_id 不能为空")
    agent = await db.get(AIAgent, normalized_agent_id)
    if agent is None or not agent.is_enabled:
        raise LookupError("agent_not_found")

    from app.services.ai.agent_manager import AgentManagerService

    if not await AgentManagerService._user_can_execute_agent(db, agent, user_info):
        raise PermissionError("agent_forbidden")
    return agent


async def _load_owned_conversation(
    db: Any,
    user_id: str,
    conversation_id: str,
) -> AgentExecutionHistory | None:
    normalized = str(conversation_id or "").strip()
    if not normalized:
        raise ValueError("conversation_id 不能为空")
    row = (
        await db.execute(
            select(AgentExecutionHistory)
            .where(
                AgentExecutionHistory.conversation_id == normalized,
                AgentExecutionHistory.user_id == str(user_id),
            )
            .order_by(
                AgentExecutionHistory.created_at.desc(),
                AgentExecutionHistory.id.desc(),
            )
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise PermissionError("会话不存在或无权访问")
    return row


async def _load_usage(db: Any, trace_id: str | None) -> dict[str, int]:
    if not trace_id:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    row = (
        await db.execute(
            select(
                AgentExecutionHistory.prompt_tokens,
                AgentExecutionHistory.completion_tokens,
                AgentExecutionHistory.total_tokens,
            ).where(AgentExecutionHistory.trace_id == trace_id)
        )
    ).first()
    if row is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": int(row[0] or 0),
        "output_tokens": int(row[1] or 0),
        "total_tokens": int(row[2] or 0),
    }


async def _invoke_agent(
    db: Any,
    *,
    agent: AIAgent,
    message: str,
    conversation_id: str,
    user_info: dict[str, Any],
) -> dict[str, Any]:
    from app.services.ai.agent_service import agent_service

    result = await agent_service.chat_completion(
        messages=[{"role": "user", "content": message}],
        agent_id=str(agent.id),
        conversation_id=conversation_id,
        user_info=user_info,
        # 入站 OAuth Token 不是 NanZi API Key，绝不能把它传入执行链。
        api_key=None,
        enable_multi_agent=True,
    )
    raw_status = str(result.get("status") or "success")
    status = "completed" if raw_status in {"success", "completed", "ok"} else raw_status
    usage = result.get("usage")
    if not isinstance(usage, Mapping):
        usage = await _load_usage(db, result.get("trace_id"))
    return {
        "status": status,
        "content": str(result.get("content") or ""),
        "citations": result.get("citations") if isinstance(result.get("citations"), list) else [],
        "usage": dict(usage),
    }


@platform_mcp.tool(
    name="agent.list_allowed",
    description="查询当前 OAuth 主体可以使用的已启用 NanZi 智能体。",
    structured_output=True,
)
async def agent_list_allowed(
    keyword: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "agent.list_allowed")
        normalized_keyword = str(keyword or "").strip()
        if len(normalized_keyword) > 100:
            raise ValueError("keyword 不能超过 100 个字符")
        safe_limit = _validate_limit(limit, default=20, maximum=100)
        offset = decode_platform_cursor("agent.list_allowed", cursor)
        if cursor and offset is None:
            raise ValueError("cursor 无效或已被篡改")
        offset = offset or 0

        async with AsyncSessionLocal() as db:
            await _load_client(db, principal)
            _, user_info = await _load_principal_user(db, principal)
            from app.services.ai.agent_manager import AgentManagerService

            agents = await AgentManagerService.list_allowed_agents(
                db,
                user=user_info,
                keyword=normalized_keyword or None,
            )
            agents = list(agents)
            versions: dict[str, AIAgentVersion] = {}
            agent_ids = [str(agent.id) for agent in agents]
            if agent_ids:
                version_rows = (
                    await db.execute(
                        select(AIAgentVersion)
                        .where(
                            AIAgentVersion.agent_id.in_(agent_ids),
                            AIAgentVersion.status == "PUBLISHED",
                        )
                        .order_by(
                            AIAgentVersion.agent_id,
                            AIAgentVersion.version_number.desc(),
                        )
                    )
                ).scalars().all()
                for version in version_rows:
                    versions.setdefault(str(version.agent_id), version)

            page = agents[offset:offset + safe_limit]
            items = [
                {
                    "agent_id": str(agent.id),
                    "name": agent.display_name or agent.name,
                    "description": agent.description or "",
                    "version_id": str(versions[str(agent.id)].id)
                    if str(agent.id) in versions else None,
                    "enabled": bool(agent.is_enabled),
                }
                for agent in page
            ]
            next_cursor = (
                encode_platform_cursor("agent.list_allowed", offset + safe_limit)
                if offset + safe_limit < len(agents) else None
            )
        await _write_audit(
            principal,
            method_name="agent.list_allowed",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"items": items, "next_cursor": next_cursor, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="agent.list_allowed",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="agent.list_allowed",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


async def _agent_call_common(
    principal: McpPrincipal,
    *,
    method_name: str,
    agent_id: str,
    message: str,
    conversation_id: str | None,
    client_request_id: str | None,
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = _request_id(principal)
    normalized_conversation_id = str(conversation_id or "").strip() or None
    target_agent_id = str(agent_id or "").strip() or None
    normalized_message = str(message or "").strip()
    try:
        if not normalized_message:
            raise ValueError("message 不能为空")
        if len(normalized_message) > 10000:
            raise ValueError("message 不能超过 10000 个字符")

        async with AsyncSessionLocal() as db:
            await _load_client(db, principal)
            _, user_info = await _load_principal_user(db, principal)
            agent = await _load_authorized_agent(db, principal, user_info, agent_id)
            if normalized_conversation_id:
                existing = await _load_owned_conversation(
                    db,
                    user_info["user_id"],
                    normalized_conversation_id,
                )
                if str(existing.agent_id) != str(agent.id):
                    raise PermissionError("会话不属于指定智能体")
            else:
                normalized_conversation_id = f"mcp_{uuid.uuid4().hex}"
            result = await _invoke_agent(
                db,
                agent=agent,
                message=normalized_message,
                conversation_id=normalized_conversation_id,
                user_info=user_info,
            )
        await _write_audit(
            principal,
            method_name=method_name,
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=str(agent.id),
            conversation_id=normalized_conversation_id,
            result_status="completed" if result["status"] == "completed" else "failed",
            status_code=200 if result["status"] == "completed" else 503,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {
            "request_id": request_id,
            "conversation_id": normalized_conversation_id,
            "agent_id": str(agent.id),
            **result,
        }
    except PermissionError:
        await _write_audit(
            principal,
            method_name=method_name,
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=target_agent_id,
            conversation_id=normalized_conversation_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name=method_name,
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=target_agent_id,
            conversation_id=normalized_conversation_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


@platform_mcp.tool(
    name="agent.invoke",
    description="以当前 NanZi 用户身份调用一个已授权智能体。",
    structured_output=True,
)
async def agent_invoke(
    agent_id: str,
    message: str,
    conversation_id: str | None = None,
    client_request_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "agent.invoke")
    except PermissionError:
        await _write_audit(
            principal,
            method_name="agent.invoke",
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=str(agent_id or "")[:128] or None,
            conversation_id=str(conversation_id or "")[:128] or None,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="agent.invoke",
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=str(agent_id or "")[:128] or None,
            conversation_id=str(conversation_id or "")[:128] or None,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    return await _agent_call_common(
        principal,
        method_name="agent.invoke",
        agent_id=agent_id,
        message=message,
        conversation_id=conversation_id,
        client_request_id=client_request_id,
    )


@platform_mcp.tool(
    name="conversation.continue",
    description="继续当前 NanZi 用户拥有的会话。",
    structured_output=True,
)
async def conversation_continue(
    conversation_id: str,
    message: str,
    client_request_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "conversation.continue")
        normalized_message = str(message or "").strip()
        if not normalized_message:
            raise ValueError("message 不能为空")
        if len(normalized_message) > 10000:
            raise ValueError("message 不能超过 10000 个字符")
        async with AsyncSessionLocal() as db:
            await _load_client(db, principal)
            _, user_info = await _load_principal_user(db, principal)
            existing = await _load_owned_conversation(
                db,
                user_info["user_id"],
                conversation_id,
            )
            agent = await _load_authorized_agent(
                db,
                principal,
                user_info,
                str(existing.agent_id),
            )
            result = await _invoke_agent(
                db,
                agent=agent,
                message=normalized_message,
                conversation_id=str(conversation_id).strip(),
                user_info=user_info,
            )
        await _write_audit(
            principal,
            method_name="conversation.continue",
            request_id=request_id,
            client_request_id=client_request_id,
            agent_id=str(agent.id),
            conversation_id=str(conversation_id).strip(),
            result_status="completed" if result["status"] == "completed" else "failed",
            status_code=200 if result["status"] == "completed" else 503,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {
            "request_id": request_id,
            "conversation_id": str(conversation_id).strip(),
            "agent_id": str(agent.id),
            **result,
        }
    except PermissionError:
        await _write_audit(
            principal,
            method_name="conversation.continue",
            request_id=request_id,
            client_request_id=client_request_id,
            conversation_id=str(conversation_id or "")[:128],
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="conversation.continue",
            request_id=request_id,
            client_request_id=client_request_id,
            conversation_id=str(conversation_id or "")[:128],
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


@platform_mcp.tool(
    name="metadata.list_datasets",
    description="列出当前 OAuth 主体可以查看的 NanZi 元数据数据集。",
    structured_output=True,
)
async def metadata_list_datasets(
    limit: int = 100,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "metadata.list_datasets")
        safe_limit = _validate_limit(limit, default=100, maximum=100)
        async with AsyncSessionLocal() as db:
            dataset_ids = await _resolve_metadata_dataset_ids(db, principal)
            datasets = await _load_authorized_metadata_datasets(db, dataset_ids)
            items = [serialize_metadata_dataset(item) for item in datasets[:safe_limit]]
        await _write_audit(
            principal,
            method_name="metadata.list_datasets",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            dataset_id=",".join(dataset_ids)[:128] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"items": items, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="metadata.list_datasets",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="metadata.list_datasets",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


def _metadata_search_items(
    datasets: list[MetaDataset],
    query: str,
    resource_types: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    needle = query.casefold()
    items: list[dict[str, Any]] = []
    for dataset in datasets:
        dataset_label = dataset.display_name or dataset.name
        dataset_text = " ".join(
            str(value or "") for value in (dataset.name, dataset_label, dataset.description)
        ).casefold()
        if "dataset" in resource_types and needle in dataset_text:
            items.append(
                {
                    "resource_type": "dataset",
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset_label,
                    "name": dataset.name,
                    "description": dataset.description or "",
                }
            )
        for table in (getattr(dataset, "tables", None) or []):
            if getattr(table, "status", 1) != 1:
                continue
            table_label = table.term or table.physical_name
            table_text = " ".join(
                str(value or "")
                for value in (table.physical_name, table_label, table.description)
            ).casefold()
            matched_columns: list[dict[str, Any]] = []
            for column in (getattr(table, "columns", None) or []):
                column_text = " ".join(
                    str(value or "")
                    for value in (column.physical_name, column.term, column.description, column.type)
                ).casefold()
                if needle in column_text:
                    matched_columns.append(
                        {
                            "name": column.physical_name,
                            "display_name": column.term or column.physical_name,
                            "type": column.type or "unknown",
                            "description": column.description or "",
                        }
                    )
                    if "column" in resource_types:
                        items.append(
                            {
                                "resource_type": "column",
                                "dataset_id": str(dataset.id),
                                "dataset_name": dataset_label,
                                "table_name": table.physical_name,
                                **matched_columns[-1],
                            }
                        )
            if "table" in resource_types and (needle in table_text or matched_columns):
                items.append(
                    {
                        "resource_type": "table",
                        "dataset_id": str(dataset.id),
                        "dataset_name": dataset_label,
                        "table_name": table.physical_name,
                        "name": table_label,
                        "description": table.description or "",
                        "matched_fields": matched_columns if "column" in resource_types else [],
                    }
                )
        if "metric" in resource_types:
            for metric in (getattr(dataset, "metrics", None) or []):
                metric_text = " ".join(
                    str(value or "")
                    for value in (
                        metric.name,
                        metric.display_name,
                        metric.description,
                        metric.calculation_logic,
                    )
                ).casefold()
                if needle in metric_text:
                    items.append(
                        {
                            "resource_type": "metric",
                            "dataset_id": str(dataset.id),
                            "dataset_name": dataset_label,
                            **serialize_metadata_metric(metric),
                        }
                    )
        if len(items) >= limit:
            return items[:limit]
    return items[:limit]


@platform_mcp.tool(
    name="metadata.search",
    description="在有权限的 NanZi 元数据数据集、表、字段和指标中搜索。",
    structured_output=True,
)
async def metadata_search(
    query: str,
    dataset_ids: list[str] | None = None,
    resource_types: list[str] | None = None,
    limit: int = 10,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "metadata.search")
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        if len(normalized_query) > 200:
            raise ValueError("query 不能超过 200 个字符")
        safe_limit = _validate_limit(limit, default=10, maximum=100)
        selected_types = set(resource_types or ["dataset", "table", "column", "metric"])
        supported_types = {"dataset", "table", "column", "metric"}
        if not selected_types or not selected_types.issubset(supported_types):
            raise ValueError("resource_types 包含不支持的类型")
        normalized_dataset_ids = _normalize_requested_ids(
            dataset_ids,
            field_name="dataset_ids",
        )
        async with AsyncSessionLocal() as db:
            effective_ids = await _resolve_metadata_dataset_ids(
                db,
                principal,
                normalized_dataset_ids,
            )
            datasets = await _load_authorized_metadata_datasets(db, effective_ids)
            items = _metadata_search_items(
                datasets,
                normalized_query,
                selected_types,
                safe_limit,
            )
        await _write_audit(
            principal,
            method_name="metadata.search",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            dataset_id=",".join(effective_ids)[:128] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"items": items, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="metadata.search",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="metadata.search",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


async def _load_one_authorized_dataset(
    db: Any,
    principal: McpPrincipal,
    dataset_id: str,
) -> tuple[MetaDataset, list[str]]:
    normalized_id = str(dataset_id or "").strip()
    try:
        int(normalized_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dataset_id 格式无效") from exc
    effective_ids = await _resolve_metadata_dataset_ids(db, principal, [normalized_id])
    if normalized_id not in effective_ids:
        raise PermissionError("数据集不存在或无权访问")
    datasets = await _load_authorized_metadata_datasets(db, [normalized_id])
    if not datasets:
        raise PermissionError("数据集不存在或无权访问")
    return datasets[0], effective_ids


@platform_mcp.tool(
    name="metadata.get_dataset",
    description="获取一个已授权 NanZi 元数据数据集的安全摘要。",
    structured_output=True,
)
async def metadata_get_dataset(
    dataset_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "metadata.get_dataset")
        async with AsyncSessionLocal() as db:
            dataset, _ = await _load_one_authorized_dataset(db, principal, dataset_id)
            payload = serialize_metadata_dataset(dataset)
        await _write_audit(
            principal,
            method_name="metadata.get_dataset",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            dataset_id=str(dataset.id),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {**payload, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="metadata.get_dataset",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            dataset_id=str(dataset_id or "")[:128],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="metadata.get_dataset",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            dataset_id=str(dataset_id or "")[:128],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


@platform_mcp.tool(
    name="metadata.get_schema",
    description="获取已授权 NanZi 数据集的表和字段结构。",
    structured_output=True,
)
async def metadata_get_schema(
    dataset_id: str,
    table_names: list[str] | None = None,
    include_relationships: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "metadata.get_schema")
        selected_names = _normalize_requested_ids(table_names, field_name="table_names")
        selected_name_set = set(selected_names) if selected_names is not None else None
        async with AsyncSessionLocal() as db:
            dataset, _ = await _load_one_authorized_dataset(db, principal, dataset_id)
            payload = serialize_metadata_schema(dataset, table_names=selected_name_set)
            if include_relationships:
                from app.services.metadata_service import MetadataService

                relationships = await MetadataService.get_relationships_by_dataset(db, int(dataset.id))
                allowed_tables = {
                    table["name"] for table in payload["tables"]
                }
                payload["relationships"] = [
                    {
                        "source_table": rel.source_table.physical_name,
                        "target_table": rel.target_table.physical_name,
                        "join_condition": rel.join_condition,
                        "join_type": rel.join_type or "LEFT",
                        "description": rel.description or "",
                    }
                    for rel in relationships
                    if rel.source_table
                    and rel.target_table
                    and rel.source_table.physical_name in allowed_tables
                    and rel.target_table.physical_name in allowed_tables
                ]
        await _write_audit(
            principal,
            method_name="metadata.get_schema",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            dataset_id=str(dataset.id),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {**payload, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="metadata.get_schema",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            dataset_id=str(dataset_id or "")[:128],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="metadata.get_schema",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            dataset_id=str(dataset_id or "")[:128],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


@platform_mcp.tool(
    name="metadata.get_metrics",
    description="获取当前 OAuth 主体可以查看的 NanZi 业务指标口径。",
    structured_output=True,
)
async def metadata_get_metrics(
    dataset_id: str | None = None,
    limit: int = 100,
    ctx: Context | None = None,
) -> dict[str, Any]:
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = _request_id(principal)
    try:
        await _validate_platform_method(principal, "metadata.get_metrics")
        safe_limit = _validate_limit(limit, default=100, maximum=100)
        requested_ids = [dataset_id] if dataset_id else None
        async with AsyncSessionLocal() as db:
            effective_ids = await _resolve_metadata_dataset_ids(db, principal, requested_ids)
            numeric_ids = [int(value) for value in effective_ids if str(value).isdigit()]
            if numeric_ids:
                metrics = list(
                    (
                        await db.execute(
                            select(MetaMetric)
                            .where(MetaMetric.dataset_id.in_(numeric_ids))
                            .order_by(MetaMetric.dataset_id.asc(), MetaMetric.id.asc())
                        )
                    ).scalars().all()
                )
            else:
                metrics = []
            items = [serialize_metadata_metric(item) for item in metrics[:safe_limit]]
        await _write_audit(
            principal,
            method_name="metadata.get_metrics",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            dataset_id=",".join(effective_ids)[:128] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"items": items, "request_id": request_id}
    except PermissionError:
        await _write_audit(
            principal,
            method_name="metadata.get_metrics",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            dataset_id=str(dataset_id or "")[:128] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="metadata.get_metrics",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            dataset_id=str(dataset_id or "")[:128] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


@platform_mcp.tool(
    name="knowledge.search",
    description="在当前 NanZi 用户与 OAuth Client 共同允许的知识库范围内搜索文档片段。",
    structured_output=True,
)
async def knowledge_search(
    query: str,
    knowledge_base_ids: str | list[str] | None = None,
    top_k: int = 5,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """执行用户授权范围内的知识库检索。"""
    del ctx
    started = time.monotonic()
    principal = _principal_from_context()
    request_id = str(principal.claims.get("request_id") or uuid.uuid4())
    try:
        _require_scope(principal, PLATFORM_MCP_SCOPE)
        if not await is_platform_mcp_enabled():
            raise RuntimeError("MCP_PLATFORM_DISABLED")
        if not await is_platform_mcp_capability_enabled("knowledge"):
            raise RuntimeError("MCP_KNOWLEDGE_DISABLED")
        if not await is_knowledge_base_enabled():
            raise RuntimeError("KNOWLEDGE_BASE_DISABLED")
        if not str(query or "").strip():
            raise ValueError("query 不能为空")
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k 必须在 1 到 20 之间")

        target_ids, resolution_log = await _resolve_knowledge_scope(
            principal,
            knowledge_base_ids,
        )
        threshold = await ConfigService.get("knowledge_ragflow_similarity_threshold")
        weight = await ConfigService.get("knowledge_ragflow_vector_weight")
        configured_top_k = await ConfigService.get("knowledge_ragflow_metadata_top_k")
        resolved_threshold, vector_weight, configured_limit = resolve_rag_retrieval_params(
            system_threshold=threshold,
            system_weight=weight,
            system_top_k=configured_top_k,
        )
        effective_top_k = min(top_k, configured_limit)
        chunks = await RagFlowClient(config_prefix="knowledge_ragflow").retrieve(
            query.strip(),
            target_ids,
            top_k=effective_top_k,
            similarity_threshold=resolved_threshold,
            vector_similarity_weight=vector_weight,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        await _write_audit(
            principal,
            method_name="knowledge.search",
            request_id=request_id,
            result_status="completed",
            status_code=200,
            latency_ms=elapsed,
        )
        return {
            "status": "ok" if chunks else "empty",
            "request_id": request_id,
            "knowledge_base_ids": target_ids,
            "results": chunks,
            "citations": chunks,
            "diagnostics": {"resolution": resolution_log, "latency_ms": elapsed},
        }
    except PermissionError:
        await _write_audit(
            principal,
            method_name="knowledge.search",
            request_id=request_id,
            result_status="denied",
            status_code=403,
            error_code="MCP_ACCESS_DENIED",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        await _write_audit(
            principal,
            method_name="knowledge.search",
            request_id=request_id,
            result_status="failed",
            status_code=_mcp_error_status(exc),
            error_code=str(exc)[:64],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise


__all__ = [
    "MCP_RESOURCE",
    "MCP_RESOURCE_URI_SUFFIX",
    "PLATFORM_MCP_RESOURCE_URI_SUFFIX",
    "PLATFORM_MCP_METHODS",
    "PlatformMcpMethodDefinition",
    "get_method_definition",
    "is_platform_mcp_capability_enabled",
    "is_platform_mcp_enabled",
    "agent_list_allowed",
    "agent_invoke",
    "conversation_continue",
    "knowledge_search",
    "metadata_list_datasets",
    "metadata_search",
    "metadata_get_dataset",
    "metadata_get_schema",
    "metadata_get_metrics",
    "platform_mcp",
    "platform_mcp_lifespan",
    "platform_mcp_resource_url",
]

"""按凭据聚合可见的固定绑定、动态候选与成功注入的历史目标。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential_type import CredentialTypeVersion, McpCredentialProfile, StructuredCredential
from app.models.mcp import McpServer, McpToolCache
from app.models.script_tool import SysScriptTool
from app.models.secret import SecretBinding, SecretUsageLog, UserSecret
from app.models.tool import SysApiTool
from app.services.ai.tools.model_tool_name import build_model_tool_name, to_runtime_tool_names
from app.services.permission_service import PermissionService
from app.services.secrets import mcp_profiles, structured_payload
from app.services.secrets.center import visible_fixed_binding_conditions
from app.services.secrets.credential_schema import CredentialSchemaError
from app.services.secrets.payload_config import load_payload_config
from app.services.secrets.payload_crypto import PayloadCryptoError
from app.services.secrets.policy import CONSUMER_API_TOOL, CONSUMER_MCP, CONSUMER_SCRIPT, SCENE_MCP
from app.services.secrets.resolver import RESULT_SUCCESS_FALLBACK, SecretResolver

MCP_TOOL = "mcp_tool"
DYNAMIC_MODES = ("infer", "ask")
Consumer = tuple[str | None, str | None, str | None]
Target = tuple[str, str, str, str | None]


@dataclass
class ToolRelation:
    kind: str
    id: str
    name: str
    server_name: str | None = None
    selection_mode: str | None = None
    used: bool = False
    bindings: list[dict] = field(default_factory=list)


def _mcp_scope(user_id: int):
    return or_(
        McpServer.scope == "global", McpServer.scope.is_(None),
        and_(McpServer.scope == "personal", McpServer.user_id == user_id),
    )


def _visible_mcp_tool(user_id: int):
    # 本人管理的服务可以查看停用或未发布工具的历史。公共工具沿用门户目录的可见范围。
    public_tool = and_(
        McpServer.enabled_status == 1, McpToolCache.is_published.is_(True), McpToolCache.is_available.is_(True),
    )
    return and_(_mcp_scope(user_id), or_(McpServer.scope == "personal", public_tool))


async def _dynamic_tools(
    db: AsyncSession, secret: UserSecret, *, user_id: str, dept_code: str | None, now: datetime,
) -> list[tuple[Target, str]]:
    if (
        secret.credential_format != structured_payload.STRUCTURED_FORMAT
        or secret.status != "active" or SCENE_MCP not in (secret.allowed_scenarios or ())
    ):
        return []
    # 仅取配置和类型元数据。候选检查既不解密实例，也不执行工具。
    config = await load_payload_config(db)
    try:
        config.parse_keyring()
    except PayloadCryptoError:
        return []

    profile = McpCredentialProfile
    tool = McpToolCache
    payload = StructuredCredential
    stmt = (
        select(
            profile, tool.id, tool.tool_name, tool.parameter_schema,
            McpServer.id, McpServer.server_name, McpServer.sse_url, CredentialTypeVersion.schema_json,
        )
        .select_from(profile)
        .join(tool, and_(tool.id == profile.tool_id, tool.server_id == profile.server_id))
        .join(McpServer, McpServer.id == tool.server_id)
        .join(payload, and_(payload.type_key == profile.type_key, payload.type_version == profile.type_version))
        .join(CredentialTypeVersion, and_(
            CredentialTypeVersion.type_key == profile.type_key, CredentialTypeVersion.version == profile.type_version,
        ))
        .where(
            payload.secret_id == secret.id, payload.owner_scope == secret.owner_scope,
            payload.owner_id == secret.owner_id, profile.selection_mode.in_(DYNAMIC_MODES),
            _mcp_scope(int(user_id)), McpServer.enabled_status == 1,
            tool.is_available.is_(True), tool.is_published.is_(True),
        )
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    permissions = await PermissionService(db).get_user_permissions(int(user_id))
    forbidden = {
        alias for name in permissions.permissions.forbidden_tools for alias in to_runtime_tool_names(name)
    } if "admin" not in permissions.roles else set()
    _, allow_external, _ = await SecretResolver._platform_flags()
    result = []
    for credential_profile, tool_id, tool_name, schema, server_id, server_name, url, type_schema in rows:
        if build_model_tool_name(tool_name) in forbidden:
            continue
        # 只投影资格检查所需的列，避开连接认证、远端默认值的回显。
        tool_metadata = _ProfileTool(tool_name, schema)
        server = _ProfileServer(server_id, url)
        try:
            if not mcp_profiles.profile_is_current(credential_profile, tool_metadata, server):
                continue
            mcp_profiles.compile_mappings(mcp_profiles.parse_schema(tool_metadata), type_schema, credential_profile.mappings)
        except (mcp_profiles.CredentialInvocationError, CredentialSchemaError):
            continue
        if mcp_profiles.allows_mcp_target(
            secret, server, user_id=user_id, dept_code=dept_code, allow_external=allow_external, now=now,
        ):
            result.append(((MCP_TOOL, tool_id, tool_name, server_name), credential_profile.selection_mode))
    return result


@dataclass(frozen=True, repr=False)
class _ProfileTool:
    tool_name: str
    parameter_schema: str | None


@dataclass(frozen=True, repr=False)
class _ProfileServer:
    id: str
    sse_url: str


async def _consumer_targets(
    db: AsyncSession, consumers: Iterable[Consumer], *, user_id: int,
) -> dict[Consumer, Target]:
    """批量解析消费方身份，无法确认的目标不返回历史名称或标识。"""
    ids_by_type = defaultdict(set)
    group_ids, arg_server_ids, header_server_ids = set(), set(), set()
    for kind, consumer_id, slot in consumers:
        if not kind or not consumer_id:
            continue
        if kind != CONSUMER_MCP:
            ids_by_type[kind].add(consumer_id)
        elif slot and slot.startswith("group:"):
            group_ids.add(slot.removeprefix("group:"))
        elif slot and slot.startswith("arg:"):
            arg_server_ids.add(consumer_id)
        elif slot and slot.startswith("header:"):
            header_server_ids.add(consumer_id)

    targets = {}
    tools_by_id, tools_by_slot = {}, defaultdict(list)
    if group_ids or arg_server_ids:
        tool = McpToolCache
        conditions = []
        if group_ids:
            conditions.append(tool.id.in_(group_ids))
        if arg_server_ids:
            conditions.append(tool.server_id.in_(arg_server_ids))
        rows = await db.execute(
            select(tool.id, tool.tool_name, tool.server_id, McpServer.server_name)
            .join(McpServer, McpServer.id == tool.server_id)
            .where(_visible_mcp_tool(user_id), or_(*conditions))
        )
        for tool_id, name, server_id, server_name in rows:
            target = (MCP_TOOL, tool_id, name, server_name)
            tools_by_id[(server_id, tool_id)] = target
            tools_by_slot[(server_id, name.split(":", 1)[-1])].append(target)

    servers = {}
    if header_server_ids:
        servers = dict((await db.execute(
            select(McpServer.id, McpServer.server_name).where(
                McpServer.id.in_(header_server_ids), _mcp_scope(user_id),
            )
        )).all())

    tool_names = {}
    for kind, model in ((CONSUMER_API_TOOL, SysApiTool), (CONSUMER_SCRIPT, SysScriptTool)):
        if ids_by_type[kind]:
            tool_names.update({
                (kind, target_id): name for target_id, name in await db.execute(
                    select(model.id, model.name).where(model.id.in_(ids_by_type[kind])),
                )
            })

    for consumer in consumers:
        kind, consumer_id, slot = consumer
        prefix, _, name = (slot or "").partition(":")
        if (kind, consumer_id) in tool_names:
            targets[consumer] = (kind, consumer_id, tool_names[(kind, consumer_id)], None)
        elif kind == CONSUMER_MCP:
            if prefix == "group" and (consumer_id, name) in tools_by_id:
                targets[consumer] = tools_by_id[(consumer_id, name)]
            elif prefix == "arg" and ":" in name:
                # 保留远端工具名中的冒号，只去掉参数名这一段。
                matches = tools_by_slot.get((consumer_id, name.rsplit(":", 1)[0]), [])
                if len(matches) == 1:
                    targets[consumer] = matches[0]
            elif prefix == "header" and consumer_id in servers:
                # 旧 Header 流水只记录连接，无法归因到某个工具。
                targets[consumer] = (CONSUMER_MCP, consumer_id, servers[consumer_id], None)
    return targets


async def list_tool_relations(
    db: AsyncSession, secret: UserSecret, *, user_id: str, dept_code: str | None, now: datetime,
) -> dict:
    """调用方已校验当前用户能查看此凭据。"""
    binding = SecretBinding
    bindings = (await db.scalars(select(binding).where(
        binding.secret_id == secret.id, *visible_fixed_binding_conditions(user_id),
    ).order_by(binding.slot, binding.id))).all()
    log = SecretUsageLog
    usage = (await db.execute(
        select(log.consumer_type, log.consumer_id, log.slot).where(
            log.secret_id == secret.id, log.user_id == user_id,
            log.result.in_(("success", RESULT_SUCCESS_FALLBACK)),
            log.scene != "variable_read",
        ).distinct()
    )).all()
    used_consumers = {tuple(row) for row in usage}
    bound_consumers = {(row.consumer_type, row.consumer_id, row.slot) for row in bindings}
    consumers = used_consumers | bound_consumers
    targets = await _consumer_targets(db, consumers, user_id=int(user_id))
    relations: dict[tuple[str, str], ToolRelation] = {}

    def relation(target: Target) -> ToolRelation:
        kind, target_id, name, server_name = target
        key = (kind, target_id)
        if key not in relations:
            relations[key] = ToolRelation(kind, target_id, name, server_name)
        return relations[key]

    for target, mode in await _dynamic_tools(db, secret, user_id=user_id, dept_code=dept_code, now=now):
        relation(target).selection_mode = mode
    unresolved_bindings = []
    for row in bindings:
        target = targets.get((row.consumer_type, row.consumer_id, row.slot))
        binding_ref = {"id": row.id, "slot": row.slot if target else None, "bound_by_me": row.bound_by_user_id == user_id}
        if target:
            relation(target).bindings.append(binding_ref)
        else:
            unresolved_bindings.append(binding_ref)
    for consumer in used_consumers & targets.keys():
        relation(targets[consumer]).used = True

    return {
        "items": sorted(relations.values(), key=lambda item: (item.kind, item.name, item.id)),
        "unresolved_usage": bool(used_consumers - targets.keys()),
        "unresolved_bindings": unresolved_bindings,
    }

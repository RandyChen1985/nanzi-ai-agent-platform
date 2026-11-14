"""MCP 结构化凭据配置、模型投影和受保护的出站准备。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import load_only

from app.models.credential_type import McpCredentialProfile, McpCredentialUseGrant, StructuredCredential
from app.models.mcp import McpServer, McpToolCache
from app.models.secret import SecretBinding, SecretGrant, UserSecret
from app.models.user import User
from app.services.secrets import structured_payload, type_catalog
from app.services.secrets.credential_approval import claim_approval, matching_approval
from app.services.secrets.credential_schema import (
    CredentialSchemaError, SCALAR_FIELD_TYPES, account_key, schema_digest, sensitive_field, validate_payload,
)
from app.services.secrets.injection import apply_mcp_arg_bindings, host_of, mcp_arg_slots
from app.services.secrets.mcp_model_result import compile_result_fields
from app.services.secrets.payload_config import load_payload_config
from app.services.secrets.payload_crypto import PayloadCryptoError
from app.services.secrets.policy import CONSUMER_MCP, SCENE_MCP, SecretContext, evaluate_secret
from app.services.secrets.resolver import SecretResolver, _to_record

ACCOUNT_PARAM = "credential_account"
SELECTION_MODES = {"fixed", "infer", "ask"}
_CANDIDATE_QUERY_BATCH_SIZE = 100
_CONNECTION_FIELDS = {
    "username", "host", "hostname", "port", "server", "endpoint", "url", "base_url", "token_url",
}
_BUSINESS_SCHEMA_KEYS = {
    "description", "enum", "const", "format", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems",
}


class CredentialInvocationError(ValueError):
    def __init__(self, code: str, message: str = "凭据校验未通过", status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(repr=False, frozen=True)
class McpCredentialInvocation:
    tool_id: str
    profile_revision: int | None
    user_query: str = ""
    selected_secret_id: str | None = None
    explicit_account: str | None = None
    profile_id: str | None = None
    arguments_digest: str | None = None


@dataclass(repr=False, frozen=True)
class PreparedMcpArguments:
    arguments: dict[str, Any]
    protect_payload: bool
    model_result_fields: tuple[str, ...] = ()
    protected_values: tuple[Any, ...] = ()
    result_policy_stale: bool = False


def _fail(code: str, message: str = "凭据校验未通过") -> CredentialInvocationError:
    return CredentialInvocationError(code, message)


def parse_schema(tool) -> dict:
    try:
        raw = tool.parameter_schema
        schema = json.loads(raw or "{}") if not isinstance(raw, dict) else raw
        if not isinstance(schema, dict):
            raise ValueError
        return schema
    except (TypeError, ValueError):
        raise _fail("CREDENTIAL_SCHEMA_INVALID", "请同步工具后重新检查参数定义") from None


def endpoint_digest(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _is_sensitive_arg(name: str, spec: Any) -> bool:
    # 远端参数可能用 camelCase 或连字符命名。
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_")
    return sensitive_field(normalized, spec if isinstance(spec, dict) else {})


def unbound_sensitive_args(tool_schema: dict, declared: list[str]) -> set[str]:
    props = tool_schema.get("properties")
    if not isinstance(props, dict):
        return set()
    return {name for name, spec in props.items() if name not in declared and _is_sensitive_arg(name, spec)}


def group_binding_required(name: str, spec: Any) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_").lower()
    return _is_sensitive_arg(name, spec) or normalized in _CONNECTION_FIELDS or _has_nested_binding_fields(spec)


def _has_nested_binding_fields(spec: Any) -> bool:
    if isinstance(spec, list):
        return any(_has_nested_binding_fields(value) for value in spec)
    if not isinstance(spec, dict):
        return False
    if _is_sensitive_arg("", spec):
        return True
    properties = spec.get("properties")
    if isinstance(properties, dict) and any(group_binding_required(name, value) for name, value in properties.items()):
        return True
    return any(_has_nested_binding_fields(spec.get(key)) for key in (
        "items", "prefixItems", "additionalProperties", "patternProperties",
        "anyOf", "oneOf", "allOf", "$defs", "definitions",
    ))


def compile_mappings(tool_schema: dict, type_schema: dict, mappings: dict[str, str]) -> dict[str, str]:
    props = tool_schema.get("properties")
    if not isinstance(props, dict) or ACCOUNT_PARAM in props:
        raise _fail("CREDENTIAL_SCHEMA_INVALID", "工具参数定义无效或含平台保留字段")
    type_fields = type_schema["properties"]
    if not isinstance(mappings, dict) or not mappings or len(mappings) > 32:
        raise _fail("CREDENTIAL_MAPPING_INVALID", "至少映射一个凭据字段，最多 32 个")
    for target, source in mappings.items():
        dest = props.get(target)
        if not isinstance(dest, dict) or not isinstance(source, str) or source not in type_fields:
            raise _fail("CREDENTIAL_MAPPING_INVALID", "映射必须引用已声明的工具参数和类型字段")
        source_kind = type_fields[source]["type"]
        declared = dest.get("type")
        # 按声明类型整体注入顶层字段，无字符串强转。
        if source_kind != declared and not (source_kind == "integer" and declared == "number"):
            raise _fail("CREDENTIAL_MAPPING_INVALID", "凭据字段和工具参数类型不匹配")
    if any(group_binding_required(name, spec) and name not in mappings for name, spec in props.items()):
        raise _fail("CREDENTIAL_MAPPING_REQUIRED", "敏感参数和连接参数需要绑定服务端凭据字段")
    return dict(mappings)


def _business_schema_metadata(spec: dict) -> dict:
    result = {}
    for key in _BUSINESS_SCHEMA_KEYS:
        value = spec.get(key)
        if key in {"description", "format"}:
            if isinstance(value, str):
                result[key] = value
        elif key in {"enum", "const"}:
            if key not in spec or (key == "enum" and not isinstance(value, list)):
                continue
            values = value if key == "enum" else [value]
            if all(
                item is None or type(item) in (str, int, bool) or type(item) is float and math.isfinite(item)
                for item in values
            ):
                result[key] = value
        elif type(value) is int or type(value) is float and math.isfinite(value):
            result[key] = value
    return result


def structural_schema(spec: Any, *, business_metadata: bool = False) -> dict:
    """投影已声明的参数结构。组凭据保留安全业务字段的说明与约束。"""
    if not isinstance(spec, dict):
        return {}
    kind = spec.get("type")
    metadata = _business_schema_metadata(spec) if business_metadata else {}
    if business_metadata and isinstance(kind, list):
        kinds = list(dict.fromkeys(item for item in kind if isinstance(item, str)))
        non_null = [item for item in kinds if item != "null"]
        if len(kinds) == 2 and len(non_null) == 1:
            projected = structural_schema({**spec, "type": non_null[0]}, business_metadata=True)
            return {**projected, "type": kinds} if projected else {}
        alternatives = [structural_schema({**spec, "type": item}, business_metadata=True) for item in kinds]
        return {"anyOf": alternatives, **metadata} if alternatives and all(alternatives) else {}
    if business_metadata:
        for key in ("anyOf", "oneOf"):
            branches = spec.get(key)
            if isinstance(branches, list) and branches:
                common = {name: value for name, value in spec.items() if name not in {"anyOf", "oneOf"}}
                alternatives = [
                    structural_schema({**common, **branch}, business_metadata=True)
                    for branch in branches if isinstance(branch, dict)
                ]
                return {key: alternatives, **metadata} if len(alternatives) == len(branches) and all(alternatives) else {}
    if business_metadata and not isinstance(kind, str):
        if isinstance(spec.get("properties"), dict):
            kind = "object"
        elif isinstance(spec.get("items"), dict):
            kind = "array"
    if isinstance(kind, str) and kind in SCALAR_FIELD_TYPES | {"null"}:
        return {"type": kind, **metadata}
    if kind == "array":
        return {
            "type": "array",
            "items": structural_schema(spec.get("items"), business_metadata=business_metadata),
            **metadata,
        }
    if kind == "object":
        props = spec.get("properties")
        props = props if isinstance(props, dict) else {}
        result = {
            "type": "object",
            "properties": {
                name: structural_schema(value, business_metadata=business_metadata)
                for name, value in props.items()
                if name != ACCOUNT_PARAM and not _is_sensitive_arg(name, value)
            },
        }
        if business_metadata:
            result["additionalProperties"] = False
            result.update(metadata)
        required = spec.get("required")
        if isinstance(required, list):
            safe_required = [name for name in required if isinstance(name, str) and name in result["properties"]]
            if safe_required:
                result["required"] = safe_required
        return result
    return {}


def model_schema(tool_schema: dict, profile: McpCredentialProfile, *, type_name: str | None = None) -> dict:
    props = tool_schema.get("properties")
    props = props if isinstance(props, dict) else {}
    public_props = {
        name: spec for name, spec in props.items()
        if name not in profile.mappings and not group_binding_required(name, spec)
    }
    # 根级描述可能同时介绍真实凭据参数，因此只投影根级业务字段和 required。
    public = structural_schema(
        {"type": "object", "properties": public_props, "required": tool_schema.get("required")},
        business_metadata=True,
    )
    if profile.selection_mode == "infer":
        label = f"{type_name}（{profile.type_key}）" if type_name else profile.type_key
        public["properties"][ACCOUNT_PARAM] = {
            "type": "string",
            "description": f"凭据中心的账号标识，绑定类型为 {label} v{profile.type_version}。"
                           "可依据用户消息、历史、邮件、网页或工具结果选择完整账号。"
                           "不确定时使用 list_mcp_credential_candidates 查询本工具的候选，"
                           "仍无法确定则留空。平台会校验权限并在服务端注入凭据。",
        }
    return public


def _matches_business_type(value: Any, kind: str) -> bool:
    return (
        (kind == "null" and value is None)
        or (kind == "boolean" and type(value) is bool)
        or (kind == "integer" and type(value) is int)
        or (kind == "number" and type(value) in (int, float))
        or (kind == "string" and isinstance(value, str))
        or (kind == "object" and isinstance(value, dict))
        or (kind == "array" and isinstance(value, list))
    )


def _public_mcp_arguments(value: Any, schema: dict) -> Any:
    """按业务字段白名单构造独立出站对象。"""
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if _matches_business_type(value, item)), None)
        if kind is None:
            raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
    branches = schema.get("oneOf") or schema.get("anyOf")
    if branches:
        projected = []
        for branch in branches:
            try:
                projected.append(_public_mcp_arguments(value, branch))
            except CredentialInvocationError:
                continue
        if not projected:
            raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
        # 当多条对象分支均为可选字段时，优先保留本次传入且在某分支声明的字段。
        if isinstance(value, dict):
            projected.sort(key=len, reverse=True)
        if schema.get("oneOf") and len(projected) > 1:
            if not isinstance(value, dict) or len(projected[0]) == len(projected[1]):
                raise _fail("MCP_ARGUMENTS_INVALID", "工具参数匹配到多种业务结构")
        return projected[0]
    if isinstance(value, dict) and kind == "object":
        properties = schema["properties"]
        if any(name not in value for name in schema.get("required", ())):
            raise _fail("MCP_ARGUMENTS_INVALID", "缺少已声明的业务参数")
        return {
            name: _public_mcp_arguments(value[name], child)
            for name, child in properties.items() if name in value and name != ACCOUNT_PARAM
        }
    if isinstance(value, list) and kind == "array":
        return [_public_mcp_arguments(child, schema["items"]) for child in value]
    if isinstance(value, (dict, list)) or kind is not None and not _matches_business_type(value, kind):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数与已声明的业务结构不符")
    if ("const" in schema and value != schema["const"]) or ("enum" in schema and value not in schema["enum"]):
        raise _fail("MCP_ARGUMENTS_INVALID", "工具参数不在允许的业务范围内")
    return value



def validate_model_result_fields(value: Any) -> tuple[list[str], str | None]:
    if value is None:
        return [], None
    try:
        compile_result_fields(value)
    except ValueError as exc:
        return [], str(exc)
    return list(dict.fromkeys(value)), None


def tool_result_target(tool, server) -> str:
    return schema_digest({
        "server_id": server.id,
        "tool_name": tool.tool_name,
        "endpoint": endpoint_digest(server.sse_url),
        "schema": parse_schema(tool),
        "credential_params": getattr(tool, "credential_params", None),
        "credential_mode": getattr(server, "credential_mode", None),
        "user_assertion_enabled": bool(getattr(server, "user_assertion_enabled", False)),
    })


def tool_result_policy(tool, server) -> tuple[int, list[str], bool]:
    policy = getattr(tool, "model_result_policy", None)
    if not isinstance(policy, dict):
        return 0, [], False
    fields, error = validate_model_result_fields(policy.get("fields"))
    revision = policy.get("revision")
    revision = revision if type(revision) is int and revision > 0 else 0
    stale = bool(fields and policy.get("target") != tool_result_target(tool, server))
    return revision, [] if not revision or stale or error else fields, stale


async def save_tool_result_policy(
    session, tool, server, *, paths: Any, expected_revision: int, expected_target: str, actor_id: str,
    credential_params: list[str] | None = None,
) -> tuple[int, list[str], str | None]:
    revision, _, _ = tool_result_policy(tool, server)
    target = tool_result_target(tool, server)
    if revision != expected_revision or target != expected_target:
        raise _fail("MCP_RESULT_FIELDS_CHANGED", "结果字段已更新，请刷新后重试")
    if await session.get(McpCredentialProfile, tool.id) is not None:
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "请在同一条凭据配置中保存结果字段")
    fields, error = validate_model_result_fields(paths)
    if credential_params is not None:
        # 先以用户打开编辑器时的快照做并发校验，再一次批准最终的参数来源和结果范围。
        # 不能先提交参数再单独保存结果，否则刚批准的目标指纹会立即失效。
        tool.credential_params = json.dumps(credential_params, ensure_ascii=False) if credential_params else None
        target = tool_result_target(tool, server)
    tool.model_result_policy = {
        "revision": revision + 1, "fields": fields, "target": target,
    }
    type_catalog.audit_change(session, "mcp_result_fields_save", actor_id, {
        "tool_id": tool.id, "field_count": len(fields), "revision": revision + 1,
    })
    await session.commit()
    return revision + 1, fields, error


def clear_tool_result_policy(tool, server) -> None:
    revision, _, _ = tool_result_policy(tool, server)
    tool.model_result_policy = {
        "revision": revision + 1, "fields": [], "target": tool_result_target(tool, server),
    }


async def clear_profile_access(session, tool_id: str, server_id: str) -> None:
    await session.execute(delete(SecretBinding).where(
        SecretBinding.consumer_type == CONSUMER_MCP, SecretBinding.consumer_id == server_id,
        SecretBinding.slot == f"group:{tool_id}",
    ))
    await session.execute(delete(McpCredentialUseGrant).where(McpCredentialUseGrant.tool_id == tool_id))


async def save_profile(
    session, tool, server, *, type_key: str, type_version: int, selection_mode: str,
    mappings: dict, expected_revision: int, actor_id: str, expected_profile_id: str | None = None,
    model_result_fields: Any = None, expected_result_target: str | None = None,
    warnings: list[dict[str, str]] | None = None, commit: bool = True,
) -> McpCredentialProfile:
    if type_key in type_catalog.LEGACY_TYPES or selection_mode not in SELECTION_MODES:
        raise _fail("CREDENTIAL_PROFILE_INVALID", "请选择结构化凭据类型和有效的选择方式")
    version = await type_catalog.get_version(session, type_key, type_version)
    remote_schema = parse_schema(tool)
    compiled = compile_mappings(remote_schema, version.schema_json, mappings)
    profile = await session.get(McpCredentialProfile, tool.id)
    revision = profile.revision if profile else 0
    if revision != expected_revision or (profile is not None and profile.profile_id != expected_profile_id):
        raise _fail("CREDENTIAL_REVISION_CONFLICT", "配置已更新，请刷新后重试")
    is_current = profile is not None and profile_is_current(profile, tool, server)
    model_result_fields, error = validate_model_result_fields(model_result_fields)
    if model_result_fields and (
        (expected_result_target is not None and expected_result_target != tool_result_target(tool, server))
        or (expected_result_target is None and profile is not None and not is_current)
    ):
        raise _fail("MCP_RESULT_FIELDS_CHANGED", "结果字段目标已变化，请刷新后重试")
    if error and warnings is not None:
        warnings.append({"code": "MCP_RESULT_FIELDS_INVALID", "message": error})
    if profile is None:
        profile = McpCredentialProfile(
            tool_id=tool.id, profile_id=str(uuid.uuid4()), server_id=server.id, tool_name=tool.tool_name,
        )
        session.add(profile)
        # 进入组凭据后废弃旧的独立规则，切回逐参数模式时须重新确认。
        result_revision, _, _ = tool_result_policy(tool, server)
        if result_revision:
            clear_tool_result_policy(tool, server)
    else:
        if (
            not is_current or profile.mappings != compiled
            or (profile.type_key, profile.type_version, profile.selection_mode) != (type_key, type_version, selection_mode)
        ):
            await clear_profile_access(session, tool.id, server.id)
        profile.server_id = server.id
        profile.tool_name = tool.tool_name
    profile.revision = revision + 1
    profile.type_key, profile.type_version = type_key, type_version
    profile.selection_mode, profile.mappings = selection_mode, compiled
    profile.model_result_fields = model_result_fields
    profile.schema_digest = schema_digest(remote_schema)
    profile.endpoint_digest = endpoint_digest(server.sse_url)
    type_catalog.audit_change(session, "mcp_profile_save", actor_id, {
        "tool_id": tool.id, "type_key": type_key, "type_version": type_version,
        "selection_mode": selection_mode, "revision": profile.revision, "fields": sorted(compiled),
        "model_result_field_count": len(profile.model_result_fields),
    })
    if commit:
        await session.commit()
    return profile


def profile_is_current(profile, tool, server) -> bool:
    return (
        profile.server_id == server.id and profile.tool_name == tool.tool_name
        and profile.schema_digest == schema_digest(parse_schema(tool))
        and profile.endpoint_digest == endpoint_digest(server.sse_url)
    )


def allows_mcp_target(parent, server, *, user_id: str, dept_code: str | None,
                      allow_external: bool, now: datetime) -> bool:
    host = host_of(server.sse_url)
    return bool(host) and evaluate_secret(_to_record(parent), SecretContext(
        user_id=user_id, dept_code=dept_code, scene=SCENE_MCP,
        target_host=host, allow_external_host=allow_external,
    ), now=now) is None


async def list_candidates(session, profile, server, *, user_id: str, dept_code: str | None,
                          secret_id: str | None = None, digest: str | None = None,
                          keyword: str = "", limit: int | None = None, only_usable: bool = True):
    """只查询工具可见的元数据。调用方选择唯一凭据后才读取对应密文。"""
    payload = StructuredCredential
    now = datetime.utcnow()
    shared = SecretGrant
    shared_grantees = [
        and_(shared.grantee_type == "user", shared.grantee_id == user_id), shared.grantee_type == "all",
    ]
    if dept_code:
        shared_grantees.append(and_(shared.grantee_type == "dept", shared.grantee_id == dept_code))
    shared_access = select(shared.id).where(
        shared.secret_id == UserSecret.id, or_(*shared_grantees),
        or_(shared.expires_at.is_(None), shared.expires_at > now),
    ).exists()
    statement = select(
        UserSecret, payload.account, payload.account_digest, payload.value_revision,
    ).options(load_only(
        UserSecret.id, UserSecret.name, UserSecret.owner_scope, UserSecret.owner_id,
        UserSecret.secret_key, UserSecret.secret_type, UserSecret.status, UserSecret.expires_at,
        UserSecret.previous_expires_at, UserSecret.allowed_hosts, UserSecret.allowed_scenarios, raiseload=True,
    )).join(payload, and_(
        payload.secret_id == UserSecret.id,
        payload.owner_id == UserSecret.owner_id,
        payload.owner_scope == UserSecret.owner_scope,
    )).where(
        payload.type_key == profile.type_key,
        payload.type_version == profile.type_version,
        UserSecret.credential_format == structured_payload.STRUCTURED_FORMAT,
        or_(
            and_(UserSecret.owner_scope == "user", UserSecret.owner_id == user_id),
            # 凭据中心共享授权是系统凭据的唯一使用授权来源。
            and_(UserSecret.owner_scope == "system", shared_access),
        ),
    ).order_by(payload.account, UserSecret.id)
    if only_usable:
        statement = statement.where(UserSecret.status == "active")
    if secret_id:
        statement = statement.where(UserSecret.id == secret_id)
    if digest:
        statement = statement.where(payload.account_digest == digest)
    if keyword:
        statement = statement.where(or_(
            payload.account.icontains(keyword, autoescape=True),
            UserSecret.name.icontains(keyword, autoescape=True),
        ))
    if only_usable:
        _, allow_external, _ = await SecretResolver._platform_flags()
    candidates = []
    batch_size = min(max(limit, 1), _CANDIDATE_QUERY_BATCH_SIZE) if limit is not None else None
    cursor = None
    while True:
        query = statement
        if batch_size is not None:
            query = query.limit(batch_size)
            if cursor is not None:
                account, secret_id_cursor = cursor
                query = query.where(or_(
                    payload.account > account,
                    and_(payload.account == account, UserSecret.id > secret_id_cursor),
                ))
        rows = list(await session.execute(query))
        for row in rows:
            if only_usable and not allows_mcp_target(
                row[0], server, user_id=user_id, dept_code=dept_code, allow_external=allow_external, now=now,
            ):
                continue
            candidates.append(row)
            if limit is not None and len(candidates) >= limit:
                return candidates
        if batch_size is None or len(rows) < batch_size:
            return candidates
        # 只有通过策略校验的记录才计入 limit；游标按已扫描的最后一条推进，
        # 同账号用唯一 id 续读，避免无效候选占满一页或跳过共享的同名账号。
        cursor = (rows[-1].account, rows[-1][0].id)
        batch_size = min(batch_size * 2, _CANDIDATE_QUERY_BATCH_SIZE)


async def has_visible_candidate(session, profile, server, *, user_id: str, dept_code: str | None, digest: str) -> bool:
    return bool(await list_candidates(
        session, profile, server, user_id=user_id, dept_code=dept_code, digest=digest, only_usable=False, limit=1,
    ))


async def active_invocation_user(session, user_id: str):
    if not (user_id.isascii() and user_id.isdecimal() and len(user_id) <= 10):
        return None
    return (await session.execute(select(User.id, User.dept_code).where(
        User.id == int(user_id), User.status == 1,
    ))).one_or_none()


async def invocation_principal(session, server, user_id: str):
    user = await active_invocation_user(session, user_id)
    if user is None or (server.scope == "personal" and server.user_id != user.id):
        raise _fail("CREDENTIAL_ACCESS_DENIED")
    return user


async def pending_target(session, ref: dict, user_id: str, *, lock: bool = False):
    config = await load_payload_config(session)
    try:
        config.cipher()
    except PayloadCryptoError:
        raise CredentialInvocationError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "结构化凭据密钥环不可用", 503) from None
    tool = await session.get(McpToolCache, ref["tool_id"], with_for_update=True) if lock else await session.get(
        McpToolCache, ref["tool_id"],
    )
    profile = await session.get(McpCredentialProfile, ref["tool_id"])
    server = await session.get(McpServer, tool.server_id) if tool else None
    if (
        profile is None or profile.revision != ref["revision"] or profile.profile_id != ref.get("profile_id")
        or server is None or server.enabled_status != 1
        or not tool.is_available or not tool.is_published or not profile_is_current(profile, tool, server)
    ):
        raise _fail("CREDENTIAL_PROFILE_CHANGED")
    user = await invocation_principal(session, server, user_id)
    version = await type_catalog.get_version(session, profile.type_key, profile.type_version)
    compile_mappings(parse_schema(tool), version.schema_json, profile.mappings)
    return profile, server, user


async def pending_candidates(session, ref: dict, user_id: str, *, secret_id: str | None = None,
                             account_digest: str | None = None):
    profile, server, user = await pending_target(session, ref, user_id)
    return await list_candidates(
        session, profile, server, user_id=user_id, dept_code=user.dept_code,
        secret_id=secret_id, digest=account_digest,
    )


def pending_account_hint(pending, profile) -> str | None:
    if profile.selection_mode != "infer":
        return None
    try:
        raw = pending.snapshot.tool_call.get("input") or "{}"
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        account, _ = account_key(arguments.get(ACCOUNT_PARAM))
    except (AttributeError, ValueError, TypeError):
        return None
    return account


def _task_secret_id(profile, user_id: str) -> str | None:
    # 定时任务仅通过服务端上下文传递引用，模型参数不能提供 secret_id。
    from app.services.secrets.task_credential_wait import task_credential_secret_id

    return task_credential_secret_id({
        "tool_id": profile.tool_id, "profile_id": profile.profile_id,
        "revision": profile.revision, "selection_mode": profile.selection_mode,
    }, user_id)


async def _selection_candidates(
    session, profile, server, *, user_id: str, dept_code: str | None, arguments: dict,
    selected_id: str | None = None, explicit_account: str | None = None,
    allow_task_reselection: bool = False,
):
    """同一候选逻辑服务预检与实际出站。返回候选及受控选择原因。"""
    account = digest = None
    if selected_id is None and profile.selection_mode == "infer":
        raw = explicit_account if explicit_account is not None else arguments.get(ACCOUNT_PARAM)
        if raw is not None and (not isinstance(raw, str) or raw.strip()):
            try:
                account, digest = account_key(raw)
            except CredentialSchemaError:
                return None, "credential_selection_invalid"

    # 任务引用是缺少明确账号时的默认值。显式账号仍通过同一套 ACL 精确选择。
    if selected_id is None and digest is None:
        try:
            selected_id = _task_secret_id(profile, user_id)
        except CredentialInvocationError as exc:
            if allow_task_reselection and exc.code == "CREDENTIAL_PROFILE_CHANGED":
                return None, "credential_selection_invalid"
            raise
    if selected_id is None and profile.selection_mode == "fixed":
        # 已存在但失效的本人绑定仍优先。不得静默回退到全局绑定。
        binding = await SecretResolver._pick_binding(
            session, CONSUMER_MCP, server.id, f"group:{profile.tool_id}", user_id,
        )
        selected_id = binding.secret_id if binding else None
    if selected_id is None and digest is None:
        return None, "credential_selection_required"

    candidates = await list_candidates(
        session, profile, server, user_id=user_id, dept_code=dept_code, secret_id=selected_id, digest=digest,
    )
    if len(candidates) > 1:
        return None, "credential_ambiguous"
    if not candidates or (account is not None and candidates[0].account != account):
        if selected_id or (digest and await has_visible_candidate(
            session, profile, server, user_id=user_id, dept_code=dept_code, digest=digest,
        )):
            return None, "credential_selection_invalid"
        return None, "credential_missing"
    return candidates[0], None


async def credential_selection_reason(ref: dict, arguments: dict, user_id: str) -> str | None:
    from app.core.orm import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            profile, server, user = await pending_target(session, ref, user_id)
            _, reason = await _selection_candidates(
                session, profile, server, user_id=user_id, dept_code=user.dept_code,
                arguments=arguments, allow_task_reselection=True,
            )
            return reason
    except CredentialInvocationError:
        raise
    except type_catalog.CredentialTypeError as exc:
        raise CredentialInvocationError(exc.code, str(exc), exc.status_code) from None
    except Exception:
        raise CredentialInvocationError("CREDENTIAL_QUERY_UNAVAILABLE", "凭据查询暂不可用", 503) from None


async def requires_interactive_selection(
    ref: dict, arguments: dict, user_id: str, user_query: str = "",
) -> bool:
    """兼容旧签名。模型可从已有上下文推导账号，执行授权由候选查询确定。"""
    return await credential_selection_reason(ref, arguments, user_id) is not None


async def _prepare_legacy_arguments(tool, server, arguments: dict, user_info: dict) -> tuple[dict, bool]:
    from app.services.secrets.injection import DENIAL_REASONS, denial_hint
    from app.services.secrets.locator import choice_field

    fields = [name for name, _ in mcp_arg_slots(tool)]
    if unbound_sensitive_args(parse_schema(tool), fields):
        raise _fail("CREDENTIAL_MAPPING_REQUIRED", "请先为敏感参数配置凭据绑定")
    if not fields:
        return arguments, False
    target = host_of(server.sse_url)
    if not target or not user_info.get("user_id"):
        raise _fail("CREDENTIAL_ACCESS_DENIED")
    values = {key: value for key, value in arguments.items() if key not in fields}
    denials: dict[str, str] = {}
    values = await apply_mcp_arg_bindings(tool, values, user_info=user_info, denials=denials, target_host=target)
    if denials or any(field not in values for field in fields):
        reasons = [DENIAL_REASONS.get(reason, "凭据不可用") for reason in denials.values()]
        hints = [denial_hint(reason) for reason in denials.values()]
        messages = list(dict.fromkeys(text for text in [*reasons, *hints] if text))
        detail = "。".join(messages) or "请在我的 MCP 中绑定参数凭据"
        raise CredentialInvocationError("CREDENTIAL_SELECTION_REQUIRED", f"工具缺少可用的参数凭据。{detail}。", 400)
    for field in fields:
        values.pop(choice_field(field), None)
    return values, True


async def prepare_mcp_arguments(
    session, server, tool_name: str, arguments: dict, call: McpCredentialInvocation,
    *, user_info: dict, trace_id: str | None, conversation_id: str | None,
) -> PreparedMcpArguments:
    profile = await session.get(McpCredentialProfile, call.tool_id)
    current_revision = profile.revision if profile else None
    if call.profile_revision != current_revision or (profile is not None and profile.profile_id != call.profile_id):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具凭据配置已变化，请重新调用")
    tool = await session.get(McpToolCache, call.tool_id)
    if (tool is None or tool.server_id != server.id or tool.tool_name.split(":", 1)[-1] != tool_name
            or not tool.is_available or not tool.is_published):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具配置已变化，请重新调用")
    if profile is None:
        result_revision, result_fields, stale = tool_result_policy(tool, server)
        if result_revision and server.scope == "personal":
            await invocation_principal(session, server, str(user_info.get("user_id") or ""))
        outgoing, protected = await _prepare_legacy_arguments(tool, server, arguments, user_info)
        return PreparedMcpArguments(
            outgoing, protected or bool(result_revision), tuple(result_fields),
            result_policy_stale=stale,
        )

    config = await load_payload_config(session)
    try:
        cipher = config.cipher()
    except PayloadCryptoError:
        raise CredentialInvocationError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "结构化凭据密钥环不可用", 503) from None
    if not profile_is_current(profile, tool, server):
        raise _fail("CREDENTIAL_PROFILE_CHANGED", "工具参数定义或目标已变化，请重新配置")

    user_id = str(user_info.get("user_id") or "")
    user = await invocation_principal(session, server, user_id)
    version = await type_catalog.get_version(session, profile.type_key, profile.type_version)
    schema = parse_schema(tool)
    mappings = compile_mappings(schema, version.schema_json, profile.mappings)
    outgoing = _public_mcp_arguments(arguments, model_schema(schema, profile))
    dept_code = user.dept_code
    approval = matching_approval(
        profile, user_id=user_id, conversation_id=conversation_id,
        arguments=arguments, original_digest=call.arguments_digest,
    )
    if profile.selection_mode == "fixed" and call.selected_secret_id is not None and approval is None:
        raise _fail("CREDENTIAL_SELECTION_INVALID")
    selected_id = approval.secret_id if approval else call.selected_secret_id
    try:
        candidate, reason = await _selection_candidates(
            session, profile, server, user_id=user_id, dept_code=dept_code, arguments=arguments,
            selected_id=selected_id, explicit_account=call.explicit_account,
        )
    except CredentialInvocationError:
        raise
    except Exception:
        raise CredentialInvocationError("CREDENTIAL_QUERY_UNAVAILABLE", "凭据查询暂不可用", 503) from None
    if reason:
        message = {
            "credential_missing": "未找到与该账号匹配的可用凭据，请选择或补录",
            "credential_ambiguous": "有多条符合条件的凭据，请在界面选择",
            "credential_selection_invalid": "所选凭据不可用，请在界面重新选择",
            "credential_selection_required": "请在界面选择凭据后继续",
        }[reason]
        raise _fail(reason.upper(), message)
    parent, _, _, value_revision = candidate
    payload = await session.get(StructuredCredential, parent.id)
    if (payload is None or payload.value_revision != value_revision
            or (payload.type_key, payload.type_version, payload.owner_scope, payload.owner_id)
            != (profile.type_key, profile.type_version, parent.owner_scope, parent.owner_id)):
        raise _fail("CREDENTIAL_PROFILE_CHANGED")
    try:
        prepared = validate_payload(
            version.schema_json, structured_payload.decrypt_fields(payload, cipher), payload.extra_schema,
        )
        outgoing.update({target: prepared.values[source] for target, source in mappings.items()})
    except (CredentialSchemaError, PayloadCryptoError, KeyError):
        raise _fail("CREDENTIAL_PAYLOAD_UNAVAILABLE", "凭据字段缺失或无法解密") from None
    if approval is not None and not await claim_approval(session, approval):
        raise _fail("CREDENTIAL_APPROVAL_CONSUMED", "本次授权已使用或过期")
    await SecretResolver._log_usage(
        session, _to_record(parent), user_id=user_id, scene=SCENE_MCP, consumer_type=CONSUMER_MCP,
        consumer_id=server.id, slot=f"group:{profile.tool_id}", target_host=host_of(server.sse_url),
        trace_id=trace_id, conversation_id=conversation_id, result="success",
    )
    raw_fields = profile.model_result_fields
    result_fields = tuple(raw_fields) if isinstance(raw_fields, list) else ()
    return PreparedMcpArguments(outgoing, True, result_fields)

"""将凭据选择限定在服务端的一次工具确认中。"""

from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.models.secret import SecretAuditLog
from app.services.secrets.credential_schema import schema_digest

MCP_CREDENTIAL_REF = "mcp_credential_ref"


@dataclass(repr=False)
class CredentialApproval:
    request_id: str
    user_id: str
    conversation_id: str | None
    tool_id: str
    profile_id: str
    profile_revision: int
    arguments_digest: str
    secret_id: str
    expires_at: float
    used: bool = False
    scheduled_task_id: int | None = None
    remember_credential: bool = False


current_credential_approval: ContextVar[CredentialApproval | None] = ContextVar(
    "current_credential_approval", default=None,
)


def approved_arguments_digest(arguments: dict) -> str:
    # StructuredTool 会补可选参数的 None。空值在授权与调用两侧采用同一口径。
    return schema_digest({key: value for key, value in arguments.items() if value is not None})


def confirmation_ref(tool: Any) -> dict | None:
    metadata = getattr(getattr(tool, "spec", tool), "metadata", None) or getattr(tool, "runtime_metadata", None)
    if not isinstance(metadata, dict):
        return None
    ref = metadata.get(MCP_CREDENTIAL_REF)
    if isinstance(ref, dict) and ref.get("selection_mode") in {"ask", "infer", "fixed"}:
        return ref
    return None


def matching_approval(profile, *, user_id: str, conversation_id: str | None,
                      arguments: dict, original_digest: str | None = None):
    approval = current_credential_approval.get()
    if (
        approval is None or approval.used or time.time() >= approval.expires_at
        or (approval.user_id, approval.conversation_id) != (user_id, conversation_id)
        or (approval.tool_id, approval.profile_id, approval.profile_revision) != (profile.tool_id, profile.profile_id, profile.revision)
        or approval.arguments_digest != (original_digest or approved_arguments_digest(arguments))
    ):
        return None
    return approval


def approval_claim_id(request_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "nanzi-mcp-credential-approval:" + request_id))


async def claim_approval(session, approval: CredentialApproval) -> bool:
    """同一请求跨 worker 只有一次有效消费，先落库再出站。"""
    if approval.used or time.time() >= approval.expires_at:
        return False
    approval.used = True
    # 已有凭据审计表提供唯一键和持久化。重复确认、重连以及 worker 重启都无法重复消费。
    claim_id = approval_claim_id(approval.request_id)
    detail = {
        "request_id": approval.request_id, "tool_id": approval.tool_id,
        "profile_id": approval.profile_id, "revision": approval.profile_revision,
    }
    task_ref = None
    try:
        if approval.remember_credential and approval.scheduled_task_id is not None:
            from app.services.secrets import task_credential_wait

            task_ref = await task_credential_wait.record_task_credential_claim(session, approval, claim_id)
            if task_ref is None:
                return False
            detail["task_id"] = approval.scheduled_task_id
        session.add(SecretAuditLog(
            id=claim_id, action="mcp_approval_claim", actor_id=approval.user_id,
            secret_id=approval.secret_id, detail=json.dumps(detail),
        ))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    if task_ref is not None:
        task_credential_wait.remember_task_credential_for_run(approval.tool_id, task_ref)
    return True


def pending_ref(pending) -> dict | None:
    ref = pending.snapshot.runner_context.get(MCP_CREDENTIAL_REF)
    return ref if isinstance(ref, dict) else None


def approval_for_pending(pending, secret_id: str, *, remember_credential: bool = False) -> CredentialApproval:
    from app.services.ai.runtime.agentscope.pending_store import PENDING_CONFIRMATION_TTL_SECONDS

    ref = pending_ref(pending)
    if ref is None:
        raise ValueError("当前确认不需要选择凭据")
    raw = pending.snapshot.tool_call.get("input") or "{}"
    arguments = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(arguments, dict):
        raise ValueError("工具参数格式无效")
    return CredentialApproval(
        request_id=pending.request_id, user_id=str(pending.user_id),
        conversation_id=pending.snapshot.conversation_id, tool_id=ref["tool_id"],
        profile_id=ref["profile_id"], profile_revision=ref["revision"], arguments_digest=approved_arguments_digest(arguments),
        secret_id=secret_id, expires_at=pending.snapshot.created_at + PENDING_CONFIRMATION_TTL_SECONDS,
        scheduled_task_id=ref.get("scheduled_task_id"), remember_credential=remember_credential,
    )

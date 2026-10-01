"""System tools for reading the authenticated user's own profile."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.ai.tools.tool_compat import BaseTool
from app.services.ai.user_extra_data import parse_extra_data
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


class _NoArguments(BaseModel):
    """Keep the public tool schema explicitly empty."""


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _safe_user_payload(user: Any) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "user_name": getattr(user, "user_name", None),
        "real_name": getattr(user, "real_name", None),
        "email": getattr(user, "email", None),
        "role": getattr(user, "role", None),
        "dept_code": getattr(user, "dept_code", None),
        "org_path": getattr(user, "org_path", None),
        "extra_data": parse_extra_data(getattr(user, "extra_data", None)),
        "remark": getattr(user, "remark", None),
        "status": getattr(user, "status", None),
        "created_at": _to_jsonable(getattr(user, "created_at", None)),
        "updated_at": _to_jsonable(getattr(user, "updated_at", None)),
    }


class CurrentUserInfoTool(BaseTool):
    name = "get_myinfo"
    description = (
        "读取当前登录用户本人的基本信息（含邮箱）、部门、组织路径、扩展信息、角色和权限。"
        "用户 ID 只能从当前认证上下文获取；不接受 userid 或任何其他参数，禁止读取其他用户。"
    )
    args_schema = _NoArguments

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> str:
        arguments = arguments or {}
        if arguments:
            if "userid" in arguments or "user_id" in arguments:
                return "get_myinfo 仅允许从当前上下文读取本人信息，不接受 userid 参数。"
            return "get_myinfo 仅允许从当前上下文读取本人信息，不接受任何参数。"

        ctx = get_current_agent_context()
        user_id = getattr(ctx, "user_id", None) if ctx else None
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return "无法识别当前用户，拒绝读取用户信息。"

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if user is None:
                    return "无法找到当前用户，拒绝读取用户信息。"
                if getattr(user, "status", 1) != 1:
                    return "当前用户已禁用，拒绝读取用户信息。"

                permission_response = await PermissionService(db).get_user_permissions(user_id)
                payload = {
                    "user": _safe_user_payload(user),
                    "permissions": _to_jsonable(permission_response),
                }
                return json.dumps(payload, ensure_ascii=False)
        except Exception:
            logger.error("[get_myinfo] failed for current user", exc_info=True)
            return "获取当前用户信息失败，请稍后重试。"


get_myinfo = CurrentUserInfoTool()

import json
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services.auth_service import AuthService
from app.services.sql_query_execution_service import execute_sql_query_core

logger = logging.getLogger(__name__)

OPENCLAW_OPENAI_SESSION_PATTERN = re.compile(
    r"^agent:[^:]+:openai-user:(?P<username>.+)-"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

router = APIRouter()
"""需 API Key + `verify_v1_api_access` 的 ChatBI 路由（与 `app/api/v1/api.py` 中 secured 子路由挂载）。"""

public_router = APIRouter()
"""无需 API Key：`sql/checkauth` 通过 Body `username` 解析用户后做权限校验（见 `api.py` 单独挂载）。"""


class ChatBiSqlExecuteRequest(BaseModel):
    sql: str = Field(..., description="只读 SELECT 语句")
    data_source: str = Field(..., description="数据源标识，如 default_clickhouse、mysql_oa")
    sessionid: str = Field(..., description="OpenClaw 会话 ID，用于后续通过 session_status 工具获取会话状态")


class ChatBiSqlExecuteData(BaseModel):
    """外部 SQL 执行 API 返回的 data 负载（常见为 columns / items）。"""

    model_config = ConfigDict(extra="allow")


class ChatBiSqlCheckAuthData(BaseModel):
    """仅权限与语法校验通过时的响应体（不执行 SQL）。"""

    allowed: bool = Field(True, description="为 True 表示当前用户对该 SQL 在校验链路上通过")


class ChatBiSqlCheckAuthRequest(BaseModel):
    username: str = Field(..., description="平台用户登录名，对应 ai_agent_users.user_name")
    sql: str = Field(..., description="只读 SELECT 语句")
    data_source: str = Field(..., description="数据源标识，如 default_clickhouse、mysql_oa")


def _map_sql_tool_error_to_http(result: str) -> None:
    if result.startswith("Error: Dataset"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result)
    if result.startswith("[Validation Failed]"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result)
    if result.startswith("[Permission Denied]"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result)
    if result.startswith("[Security Error]"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result)
    if result.startswith("[TOOL_ERROR]"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result)


def _chatbi_user_dimensions(user_info: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    return {
        "id": user_id,
        "user_name": user_info.get("user_name"),
        "real_name": user_info.get("real_name"),
        "role": user_info.get("role"),
        "dept_code": user_info.get("dept_code"),
        "org_path": user_info.get("org_path"),
        "extra_data": user_info.get("extra_data"),
    }


def _openclaw_openai_username_from_sessionid(sessionid: str) -> Optional[str]:
    match = OPENCLAW_OPENAI_SESSION_PATTERN.match((sessionid or "").strip())
    if not match:
        return None
    username = match.group("username").strip()
    return username or None


async def _enforce_openclaw_session_sql_auth(
    db: AsyncSession,
    body: ChatBiSqlExecuteRequest,
) -> None:
    username = _openclaw_openai_username_from_sessionid(body.sessionid)
    if not username:
        return

    session_user_info = await AuthService.resolve_user_by_username(username, db)
    if not session_user_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OpenClaw 会话用户不存在或已禁用")

    session_user_id = int(session_user_info["user_id"])
    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=None,
        user_id=session_user_id,
        user_dimensions=_chatbi_user_dimensions(session_user_info, session_user_id),
        trace_logs=None,
        api_key=None,
        agent_context=None,
        dry_run=False,
        is_admin=session_user_info.get("role") == "admin",
        auth_check_only=True,
    )

    raw = result_str.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("allowed") is True:
            return
    except json.JSONDecodeError:
        pass

    _map_sql_tool_error_to_http(result_str)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result_str)


@router.post(
    "/sql/execute",
    response_model=StandardResponse[ChatBiSqlExecuteData],
    summary="执行 ChatBI 只读 SQL",
    description=(
        "**执行流程**\n\n"
        "- 校验 API Key 与请求参数。\n"
        "- `sessionid` 必填，用于阻断未携带会话的直接调用。\n"
        "- 若 `sessionid` 符合 `agent:<agent_name>:openai-user:<username>-<uuid>`，"
        "先按其中的 `<username>` 复用 `sql/checkauth` 等价链路做额外权限校验。\n"
        "- 校验数据集、物理表权限、行级权限重写与只读 SQL 语法。\n"
        "- 通过后按当前执行模式调用本地数据源或远程 SQL 服务执行并返回结果。"
    ),
)
async def chatbi_sql_execute(
    body: ChatBiSqlExecuteRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    import os
    from app.services.config_service import ConfigService

    user_id = int(user_info["user_id"])
    user_dimensions = _chatbi_user_dimensions(user_info, user_id)

    await _enforce_openclaw_session_sql_auth(db, body)

    is_openclaw_user = bool(_openclaw_openai_username_from_sessionid(body.sessionid))
    bypass_table_auth = not is_openclaw_user

    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=None,
        user_id=user_id,
        user_dimensions=user_dimensions,
        trace_logs=None,
        api_key=None,
        agent_context=None,
        dry_run=False,
        is_admin=user_info.get("role") == "admin",
        bypass_table_auth=bypass_table_auth,
    )

    # 动态判定当前物理执行分流模式
    env_mode = os.environ.get("SQL_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("local", "remote"):
        execution_mode = env_mode
    else:
        try:
            execution_mode = await ConfigService.get("sql_execution_mode", default="remote")
            execution_mode = execution_mode.strip().lower()
        except Exception:
            execution_mode = "remote"

        if execution_mode not in ("local", "remote"):
            execution_mode = "remote"

    raw = result_str.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return StandardResponse(
                data=ChatBiSqlExecuteData.model_validate(parsed),
                execution_mode=execution_mode
            )
        return StandardResponse(
            data=ChatBiSqlExecuteData.model_validate({"rows": parsed}),
            execution_mode=execution_mode
        )
    except json.JSONDecodeError:
        pass

    if raw.startswith("[DRY_RUN]"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该接口不支持 dry_run，请检查调用参数。",
        )
    _map_sql_tool_error_to_http(result_str)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result_str)


@public_router.post(
    "/sql/checkauth",
    response_model=StandardResponse[ChatBiSqlCheckAuthData],
    summary="校验 ChatBI SQL 权限（不执行）",
    description=(
        "**校验流程**\n\n"
        "- 无需 API Key，按 Body 中的 `username` 解析平台用户。\n"
        "- 校验数据集是否存在。\n"
        "- 校验 SQL 涉及的物理表是否在该用户可访问范围内。\n"
        "- 若数据集开启行级权限，则按用户维度与角色尝试重写 SQL。\n"
        "- 校验只读 SQL 语法与安全策略。\n"
        "- 全部通过返回 `allowed: true`，不通过返回对应错误；本接口不执行 SQL。"
    ),
)
async def chatbi_sql_checkauth(
    body: ChatBiSqlCheckAuthRequest,
    db: AsyncSession = Depends(get_db_session),
):
    user_info = await AuthService.resolve_user_by_username(body.username, db)
    if not user_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或已禁用")

    user_id = int(user_info["user_id"])
    user_dimensions = _chatbi_user_dimensions(user_info, user_id)

    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=None,
        user_id=user_id,
        user_dimensions=user_dimensions,
        trace_logs=None,
        api_key=None,
        agent_context=None,
        dry_run=False,
        is_admin=user_info.get("role") == "admin",
        auth_check_only=True,
    )

    # 动态判定当前物理执行分流模式
    import os
    from app.services.config_service import ConfigService
    env_mode = os.environ.get("SQL_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("local", "remote"):
        execution_mode = env_mode
    else:
        try:
            execution_mode = await ConfigService.get("sql_execution_mode", default="remote")
            execution_mode = execution_mode.strip().lower()
        except Exception:
            execution_mode = "remote"

        if execution_mode not in ("local", "remote"):
            execution_mode = "remote"

    raw = result_str.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("allowed") is True:
            return StandardResponse(
                data=ChatBiSqlCheckAuthData.model_validate(parsed),
                execution_mode=execution_mode
            )
    except json.JSONDecodeError:
        pass

    if raw.startswith("[DRY_RUN]"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该接口不支持 dry_run，请检查调用参数。",
        )
    _map_sql_tool_error_to_http(result_str)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result_str)

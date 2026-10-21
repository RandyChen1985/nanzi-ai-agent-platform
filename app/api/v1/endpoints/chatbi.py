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
from app.services.ai.chatbi_sql_query_binding import (
    normalize_sql_identifier,
    resolve_table_bindings_from_db,
)
from app.services.metadata_service import MetadataService
from app.services.user_dimension_service import build_sql_user_dimensions
from app.services.sql_query_execution_service import (
    attach_permission_notice_to_payload,
    dialect_from_data_source,
    execute_sql_query_core,
    extract_physical_table_refs_from_select_sql,
)

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
    dataset_name: Optional[str] = Field(default=None, description="可选数据集名称；用于行级数据权限重写")
    sessionid: str = Field(..., description="OpenClaw 会话 ID，用于后续通过 session_status 工具获取会话状态")


class ChatBiSqlExecuteData(BaseModel):
    """外部 SQL 执行 API 返回的 data 负载（常见为 columns / items）。"""

    model_config = ConfigDict(extra="allow")
    permission_notice: Optional[Dict[str, Any]] = Field(
        default=None,
        description="当本次结果已应用行级数据权限过滤时返回的非敏感提示信息",
    )


class ChatBiSqlCheckAuthData(BaseModel):
    """仅权限与语法校验通过时的响应体（不执行 SQL）。"""

    allowed: bool = Field(True, description="为 True 表示当前用户对该 SQL 在校验链路上通过")


class ChatBiSqlCheckAuthRequest(BaseModel):
    username: str = Field(..., description="平台用户登录名，对应 ai_agent_users.user_name")
    sql: str = Field(..., description="只读 SELECT 语句")
    data_source: str = Field(..., description="数据源标识，如 default_clickhouse、mysql_oa")
    dataset_name: Optional[str] = Field(default=None, description="可选数据集名称；用于行级数据权限重写")


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


def _attach_permission_notice(payload: Dict[str, Any], permission_notice: Dict[str, Any]) -> Dict[str, Any]:
    return attach_permission_notice_to_payload(payload, permission_notice)


async def _dataset_names_with_row_filter(db: AsyncSession, dataset_names: set[str]) -> set[str]:
    enabled: set[str] = set()
    for name in dataset_names:
        dn = str(name or "").strip()
        if not dn:
            continue
        ds = await MetadataService.get_dataset_by_name(db, dn)
        if ds and ds.enable_data_perm:
            enabled.add(dn)
    return enabled


async def _collect_candidate_dataset_names(
    db: AsyncSession,
    *,
    refs: Dict[str, str],
    bindings: Dict[str, Any],
) -> set[str]:
    from app.services.ai.chatbi_sql_query_binding import _fetch_table_binding_candidates_from_db

    dataset_names = {
        str(binding.dataset_name or "").strip()
        for binding in bindings.values()
        if str(getattr(binding, "dataset_name", "") or "").strip()
    }
    if len(bindings) >= len(refs):
        return dataset_names

    candidates_map = await _fetch_table_binding_candidates_from_db(db, list(refs.values()))
    for physical_name in refs.values():
        key = normalize_sql_identifier(physical_name)
        if key in bindings:
            continue
        for candidate in candidates_map.get(key, []):
            dn = str(candidate.dataset_name or "").strip()
            if dn:
                dataset_names.add(dn)
    return dataset_names


async def _raise_if_row_filter_requires_dataset_resolution(
    db: AsyncSession,
    *,
    refs: Dict[str, str],
    bindings: Dict[str, Any],
    reason: str,
) -> None:
    dataset_names = await _collect_candidate_dataset_names(db, refs=refs, bindings=bindings)
    if not dataset_names:
        return
    if await _dataset_names_with_row_filter(db, dataset_names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "[Validation Failed] 无法安全确定 SQL 所属数据集，但涉及的表已开启行级数据权限；"
                "请在请求中显式传入 dataset_name，或将 SQL 限定到单一数据集。"
                f"（{reason}）"
            ),
        )


async def _resolve_direct_sql_dataset_name(
    db: AsyncSession,
    *,
    sql: str,
    data_source: str,
    explicit_dataset_name: Optional[str],
) -> Optional[str]:
    explicit = str(explicit_dataset_name or "").strip()
    if explicit:
        return explicit

    dialect = dialect_from_data_source(data_source)
    parse_error, refs = extract_physical_table_refs_from_select_sql(sql, dialect)
    if parse_error or not refs:
        return None

    try:
        bindings = await resolve_table_bindings_from_db(db, list(refs.values()))
    except Exception as exc:
        logger.warning("[ChatBI SQL] Failed to infer dataset from SQL refs: %s", exc)
        return None
    if len(bindings) < len(refs):
        await _raise_if_row_filter_requires_dataset_resolution(
            db,
            refs=refs,
            bindings=bindings,
            reason="部分物理表无法唯一绑定到数据集",
        )
        return None

    dataset_names = {
        str(binding.dataset_name or "").strip()
        for binding in bindings.values()
        if str(binding.dataset_name or "").strip()
    }
    if len(dataset_names) == 1:
        return next(iter(dataset_names))
    if len(dataset_names) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "[Validation Failed] 直连 SQL 引用了多个数据集，属于跨数据集查询，"
                "无法安全自动应用行级权限；"
                "请改走联邦查询流程，或将 SQL 限定到单一数据集。"
            ),
        )
    await _raise_if_row_filter_requires_dataset_resolution(
        db,
        refs=refs,
        bindings=bindings,
        reason="未能从 SQL 引用表推断出数据集名称",
    )
    return None


def _openclaw_openai_username_from_sessionid(sessionid: str) -> Optional[str]:
    match = OPENCLAW_OPENAI_SESSION_PATTERN.match((sessionid or "").strip())
    if not match:
        return None
    username = match.group("username").strip()
    return username or None


async def _enforce_openclaw_session_sql_auth(
    db: AsyncSession,
    body: ChatBiSqlExecuteRequest,
) -> Optional[Dict[str, Any]]:
    username = _openclaw_openai_username_from_sessionid(body.sessionid)
    if not username:
        return None

    session_user_info = await AuthService.resolve_user_by_username(username, db)
    if not session_user_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OpenClaw 会话用户不存在或已禁用")

    session_user_id = int(session_user_info["user_id"])
    dataset_name = await _resolve_direct_sql_dataset_name(
        db,
        sql=body.sql,
        data_source=body.data_source,
        explicit_dataset_name=body.dataset_name,
    )
    permission_notice: Dict[str, Any] = {}
    # 一次查库算好多部门维度，行级规则可按 IN ({user.dept_codes}) 匹配
    chatbi_dims = await build_sql_user_dimensions(db, session_user_info, session_user_id)
    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=dataset_name,
        user_id=session_user_id,
        user_dimensions=chatbi_dims,
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
            return session_user_info
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
    user_dimensions = await build_sql_user_dimensions(db, user_info, user_id)

    session_user_info = await _enforce_openclaw_session_sql_auth(db, body)
    if session_user_info:
        user_id = int(session_user_info["user_id"])
        user_dimensions = await build_sql_user_dimensions(db, session_user_info, user_id)

    # 表级门禁不能由调用方传入的 sessionid 决定：OpenClaw 会话在上一行已经
    # 按会话用户做过一次等价校验，非 OpenClaw 调用方也必须走完整的表权限校验。
    dataset_name = await _resolve_direct_sql_dataset_name(
        db,
        sql=body.sql,
        data_source=body.data_source,
        explicit_dataset_name=body.dataset_name,
    )

    permission_notice: Dict[str, Any] = {}
    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=dataset_name,
        user_id=user_id,
        user_dimensions=user_dimensions,
        trace_logs=None,
        api_key=None,
        agent_context=None,
        dry_run=False,
        is_admin=user_info.get("role") == "admin",
        bypass_table_auth=False,
        permission_notice=permission_notice,
    )

    # 行级重写后的 SQL 只用于内部审计/工具日志，不回传给调用方：
    # permission_notice 的契约是“非敏感提示”，不应暴露过滤条件表达式。
    permission_notice.pop("executed_sql", None)

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
                data=ChatBiSqlExecuteData.model_validate(_attach_permission_notice(parsed, permission_notice)),
                execution_mode=execution_mode
            )
        return StandardResponse(
            data=ChatBiSqlExecuteData.model_validate(
                _attach_permission_notice({"rows": parsed}, permission_notice)
            ),
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
    user_dimensions = await build_sql_user_dimensions(db, user_info, user_id)
    dataset_name = await _resolve_direct_sql_dataset_name(
        db,
        sql=body.sql,
        data_source=body.data_source,
        explicit_dataset_name=body.dataset_name,
    )

    result_str = await execute_sql_query_core(
        db,
        sql=body.sql,
        data_source=body.data_source,
        dataset_name=dataset_name,
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

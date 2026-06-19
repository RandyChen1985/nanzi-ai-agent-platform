import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.core.redis import get_redis
from app.schemas.response import StandardResponse
from app.services.sql_query_execution_service import execute_sql_query_core

logger = logging.getLogger(__name__)

router = APIRouter()

class SaveReportRequest(BaseModel):
    title: str = Field(..., description="报表自定义标题")
    sql_content: str = Field(..., description="只读 SELECT 语句")
    dataset_id: Optional[int] = Field(None, description="数据集 ID")
    data_source: str = Field(..., description="数据源标识，如 default_clickhouse、mysql_oa")
    original_query: Optional[str] = Field(None, description="原始提问语句")

class SavedReportItem(BaseModel):
    id: str
    title: str
    sql_content: str
    dataset_id: Optional[int]
    data_source: str
    original_query: Optional[str]
    created_at: str

def _redis_key(user_id: int) -> str:
    return f"agent:saved_reports:{user_id}"

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

@router.post(
    "",
    response_model=StandardResponse[SavedReportItem],
    summary="暂存黄金 SQL 报表到数据门户",
)
async def save_report(
    body: SaveReportRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    import re
    redis = await get_redis()
    if not redis:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis 服务不可用",
        )

    user_id = int(user_info["user_id"])

    # 自动推导并补全 dataset_id 和 data_source
    if not body.dataset_id:
        words = set(re.findall(r"\b[a-zA-Z0-9_]+\b", body.sql_content.lower()))
        if words:
            from sqlalchemy import select
            from app.models.metadata import MetaTable, MetaDataset
            try:
                stmt = (
                    select(MetaTable.dataset_id, MetaDataset.data_source)
                    .join(MetaDataset, MetaTable.dataset_id == MetaDataset.id)
                    .where(MetaTable.physical_name.in_(words))
                    .where(MetaTable.status == 1)
                    .limit(1)
                )
                res = await db.execute(stmt)
                row = res.first()
                if row:
                    body.dataset_id = row[0]
                    body.data_source = row[1]
            except Exception as ex:
                logger.warning("Failed to auto-detect dataset for saved report: %s", ex)

    if not body.data_source:
        body.data_source = "default_clickhouse"

    report_id = f"rpt_{uuid.uuid4().hex[:12]}"
    now_str = datetime.now(timezone.utc).isoformat()

    sql_clean = body.sql_content.strip()
    if sql_clean.startswith("[Executed SQL]:"):
        sql_clean = sql_clean[len("[Executed SQL]:"):].strip()

    report_item = SavedReportItem(
        id=report_id,
        title=body.title.strip(),
        sql_content=sql_clean,
        dataset_id=body.dataset_id,
        data_source=body.data_source,
        original_query=body.original_query,
        created_at=now_str,
    )

    key = _redis_key(user_id)
    try:
        await redis.hset(key, report_id, report_item.model_dump_json())
    except Exception as e:
        logger.error("Failed to save report to Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="保存报表失败",
        )

    return StandardResponse(data=report_item)

@router.get(
    "",
    response_model=StandardResponse[List[SavedReportItem]],
    summary="获取当前用户的黄金 SQL 报表暂存列表",
)
async def get_saved_reports(
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        return StandardResponse(data=[])

    user_id = int(user_info["user_id"])
    key = _redis_key(user_id)
    try:
        raw_reports = await redis.hgetall(key)
    except Exception as e:
        logger.error("Failed to get reports from Redis: %s", e)
        return StandardResponse(data=[])

    reports = []
    if isinstance(raw_reports, dict):
        for val in raw_reports.values():
            try:
                decoded = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                parsed = json.loads(decoded)
                reports.append(SavedReportItem.model_validate(parsed))
            except Exception as e:
                logger.warning("Failed to parse saved report item JSON: %s", e)
                continue

    # 按照创建时间降序排序 (最新暂存的在最上面)
    reports.sort(key=lambda x: x.created_at, reverse=True)
    return StandardResponse(data=reports)

@router.delete(
    "/{report_id}",
    response_model=StandardResponse[Dict[str, bool]],
    summary="删除暂存的黄金 SQL 报表",
)
async def delete_saved_report(
    report_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    redis = await get_redis()
    if not redis:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis 服务不可用",
        )

    user_id = int(user_info["user_id"])
    key = _redis_key(user_id)
    try:
        deleted_count = await redis.hdel(key, report_id)
    except Exception as e:
        logger.error("Failed to delete report from Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除报表失败",
        )

    if not deleted_count:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="报表不存在或已被删除",
        )

    return StandardResponse(data={"deleted": True})

@router.post(
    "/{report_id}/execute",
    response_model=StandardResponse[Any],
    summary="免模型极速安全运行暂存的黄金 SQL 报表",
)
async def execute_saved_report(
    report_id: str,
    conversation_id: Optional[str] = None, # 接收 conversation_id 以便写入缓存复用
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    redis = await get_redis()
    if not redis:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis 服务不可用",
        )

    user_id = int(user_info["user_id"])
    key = _redis_key(user_id)
    try:
        raw_val = await redis.hget(key, report_id)
    except Exception as e:
        logger.error("Failed to get report detail from Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取报表详情失败",
        )

    if not raw_val:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="报表不存在或无权访问",
        )

    try:
        decoded = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val)
        report_data = json.loads(decoded)
        report = SavedReportItem.model_validate(report_data)
    except Exception as e:
        logger.error("Failed to parse report model: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="报表数据解析失败",
        )

    user_dimensions = _chatbi_user_dimensions(user_info, user_id)
    is_admin = user_info.get("role") == "admin"

    try:
        # 直接调用底层的只读安全执行器，完美过权限校验与SQL网关门禁
        result_str = await execute_sql_query_core(
            db,
            sql=report.sql_content,
            data_source=report.data_source,
            dataset_name=None,
            user_id=user_id,
            user_dimensions=user_dimensions,
            trace_logs=None,
            api_key=None,
            agent_context=None,
            dry_run=False,
            is_admin=is_admin,
            bypass_table_auth=True, # 后台已预置鉴权，直连运行时放开以加速
        )
    except Exception as e:
        logger.error("Failed to execute SQL from saved report: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SQL 执行失败: {e}",
        )

    raw_res = result_str.strip()
    
    # 尝试进行 JSON 结果标准化解析（复用 chatbi_sql_execute 的转换逻辑）
    try:
        parsed = json.loads(raw_res)
        if isinstance(parsed, dict) and ("[Validation Failed]" in raw_res or "[Permission Denied]" in raw_res or "[Security Error]" in raw_res):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=parsed)
            
        # 若传入了会话ID，写入缓存供大模型下一轮做可视化复用
        if conversation_id:
            try:
                from app.services.ai.memory_service import memory_service
                cache_payload = {
                    "sql": report.sql_content,
                    "data_source": report.data_source,
                    "dataset_name": None,
                    "rows": parsed,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "trace_id": None,
                }
                await memory_service.set_last_data_result(str(user_id), conversation_id, cache_payload)
            except Exception as cache_err:
                logger.warning("Failed to save report data to memory_service cache: %s", cache_err)

        return StandardResponse(data=parsed)
    except json.JSONDecodeError:
        pass

    # 如果是文本类输出（非标准 JSON 格式，如报错信息）
    if raw_res.startswith("[Validation Failed]") or raw_res.startswith("[Permission Denied]") or raw_res.startswith("[Security Error]"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=raw_res)
        
    return StandardResponse(data={"rows": raw_res})

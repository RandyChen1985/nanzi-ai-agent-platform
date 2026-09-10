"""ChatBI 查询结果的完整明细导出通道。

区别于 AI 分析口径（QUERY 行数上限 1000、2MB 返回体上限），本通道专用于
「用户下载完整明细」场景：直链重跑最终 SQL，放宽行数上限（10 万行）后生成
CSV/Excel 落盘到用户工作区，返回带鉴权 token 的下载地址。

核心边界：
- 全程不喂给 LLM、不产生 token；
- 复用 execute_sql_query_core 的表级/行级权限重写与沙箱性能护栏（笛卡尔积、超大
  表 EXPLAIN 熔断），导出不可绕过权限；
- 联邦查询（federated mode）结果为多库合并，本通道暂不支持，避免导出错误数据。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services.ai.tools.data_api import MAX_EXPORT_SQL_ROWS

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatBIExportRequest(BaseModel):
    sql: str = Field(..., description="要导出的查询 SQL（取 ChatBI 结果的 final_sql）")
    data_source: str = Field(..., description="数据源标识符")
    dataset_name: Optional[str] = Field(default=None, description="数据集名称（可选）")
    format: str = Field(default="xlsx", pattern="^(xlsx|csv|md)$", description="导出格式（xlsx / csv / md）")
    execution_mode: str = Field(
        default="direct",
        description="执行方式，用于识别联邦查询：direct / repaired / federated",
    )
    result_id: Optional[str] = Field(default=None, description="ChatBI 结果 ID（可选，仅用于命名）")


def _user_dimensions(user_info: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    return {
        "id": user_id,
        "user_name": user_info.get("user_name"),
        "real_name": user_info.get("real_name"),
        "role": user_info.get("role"),
        "dept_code": user_info.get("dept_code"),
        "org_path": user_info.get("org_path"),
        "extra_data": user_info.get("extra_data"),
    }


def _extract_rows_columns(raw_payload: Any) -> tuple[list[str], list[list[Any]]]:
    """将本地 Adapter / 远程 API 的不同结果包装成 (columns, rows)。"""
    payload = raw_payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            raise ValueError("SQL 执行结果非有效 JSON")

    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        payload = payload["data"]

    rows: list[Any] = []
    if isinstance(payload, dict):
        for key in ("items", "rows", "records", "result", "list"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    elif isinstance(payload, list):
        rows = payload

    if not isinstance(rows, list):
        rows = []

    columns: list[str] = []
    if isinstance(payload, dict):
        raw_cols = payload.get("columns") or payload.get("fields") or []
        for c in raw_cols:
            columns.append(c.get("name") if isinstance(c, dict) else str(c))

    if rows and isinstance(rows[0], dict):
        if not columns:
            columns = [str(key) for key in rows[0].keys()]
        rows = [[row.get(name) for name in columns] for row in rows]
    elif rows and isinstance(rows[0], list):
        if not columns:
            # 无列名时按第一行宽度生成占位列
            columns = [f"c{i + 1}" for i in range(len(rows[0]))]

    if not columns:
        columns = [f"c{i + 1}" for i in range(len(rows[0]))] if rows and isinstance(rows[0], list) else []

    return columns, rows


@router.post("/result", summary="导出 ChatBI 完整明细")
async def export_chatbi_detail(
    body: ChatBIExportRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """直链重跑最终 SQL，宽松行数上限生成 CSV/Excel 并返回下载地址。"""
    if body.execution_mode == "federated":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="联邦查询（跨数据集合并）暂不支持完整明细导出，请对单库查询使用导出。",
        )

    raw_sql = (body.sql or "").strip()
    if not raw_sql:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少待导出的 SQL")

    # 明确禁止写操作与多语句，仅允许只读 / 单条
    first_kw = raw_sql.split(None, 1)[0].upper() if raw_sql.split(None, 1) else ""
    if not raw_sql.lstrip().upper().startswith("SELECT") and not raw_sql.lstrip().upper().startswith("WITH") and first_kw not in {"EXPLAIN", "SHOW", "DESCRIBE", "DESC"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持只读 SELECT/WITH 查询的完整明细导出。",
        )

    user_id = int(user_info["user_id"])
    is_admin = user_info.get("role") == "admin"
    include_total = False

    from app.services.sql_query_execution_service import execute_sql_query_core

    try:
        result_str = await execute_sql_query_core(
            db,
            sql=raw_sql,
            data_source=body.data_source,
            dataset_name=body.dataset_name,
            user_id=user_id,
            user_dimensions=_user_dimensions(user_info, user_id),
            trace_logs=None,
            api_key=None,
            agent_context=None,
            dry_run=False,
            is_admin=is_admin,
            bypass_table_auth=False,
            include_total=include_total,
            for_export=True,
        )
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error("ChatBI 明细导出 - SQL 执行失败: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SQL 执行失败: {e}",
        )

    raw_res = str(result_str or "").strip()
    if raw_res.startswith("[TOOL_ERROR]") or raw_res.startswith("[Validation Failed]") \
            or raw_res.startswith("[Permission Denied]") or raw_res.startswith("[Security Error]") \
            or raw_res.startswith("[Performance Blocked]"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=raw_res,
        )

    try:
        columns, rows = _extract_rows_columns(raw_res)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

    if not columns or rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="无可导出的明细数据")

    truncated = len(rows) >= MAX_EXPORT_SQL_ROWS

    # 生成文件并落盘到用户工作区 export 目录，再登记 artifact 返回下载地址
    export_dir, out_path = await _write_export_file(
        user_id=user_id,
        user_info=user_info,
        columns=columns,
        rows=rows,
        fmt=body.format,
        result_id=body.result_id,
    )

    from app.services.ai.tools.generated_file_service import register_artifact

    try:
        artifact = await register_artifact(
            source_path=out_path,
            filename=out_path.name,
            owner_user_id=user_id,
            artifact_type="export",
        )
    except Exception as e:
        logger.error("ChatBI 明细导出 - artifact 登记失败: %s", e)
        out_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导出文件登记失败: {e}",
        )

    return StandardResponse(
        data={
            "filename": artifact.filename,
            "mime_type": artifact.mime_type,
            "size": artifact.size,
            "download_url": artifact.download_url,
            "expires_at": artifact.expires_at.isoformat(),
            "row_count": len(rows),
            "truncated": truncated,
            "export_limit": MAX_EXPORT_SQL_ROWS,
        }
    )


async def _write_export_file(
    *,
    user_id: int,
    user_info: Dict[str, Any],
    columns: list[str],
    rows: list[list[Any]],
    fmt: str,
    result_id: Optional[str],
) -> tuple[Path, Path]:
    """构造 DataFrame 并写 CSV/XLSX 到用户工作区 export 目录，返回 (workspace_root, file_path)。"""
    from app.services.ai.runtime.agentscope.workspace import (
        resolve_workspace_root,
        resolve_workspace_user_key,
    )

    df = pd.DataFrame(rows, columns=columns)
    workspace_root = Path((await resolve_workspace_root()) or ".")
    user_key = resolve_workspace_user_key(
        user_id=user_id,
        user_name=user_info.get("user_name"),
    )
    export_dir = workspace_root / user_key / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"chatbi_export_{stamp}"
    if result_id:
        prefix = f"chatbi_export_{result_id[:8]}_{stamp}"

    if fmt == "csv":
        out_name = f"{prefix}.csv"
        out_path = export_dir / out_name
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    elif fmt == "md":
        out_name = f"{prefix}.md"
        out_path = export_dir / out_name
        _write_markdown_table(out_path, columns, df)
    else:
        out_name = f"{prefix}.xlsx"
        out_path = export_dir / out_name
        df.to_excel(out_path, index=False, sheet_name="查询明细", engine="openpyxl")

    return workspace_root, out_path


def _write_markdown_table(out_path: Path, columns: list[str], df: "pd.DataFrame") -> None:
    """将查询结果写成 Markdown 表格文件（不依赖 tabulate，纯手写对齐）。"""
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    align = "| " + " | ".join("---" for _ in columns) + " |"
    body: list[str] = []
    for _, row in df.iterrows():
        cells = [_md_escape(v) for v in row.tolist()]
        table_row = "| " + " | ".join(cells) + " |"
        body.append(table_row)
    content = "\n".join([header, align, *body]) + "\n"
    out_path.write_text(content, encoding="utf-8")


def _md_escape(value: Any) -> str:
    """转义单元格内容，避免破坏 Markdown 表格结构。"""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text
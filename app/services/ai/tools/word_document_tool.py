"""Controlled Word document tools for configured agents."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from docx import Document
from pydantic import Field

from app.core.context import get_current_agent_context
from app.services.ai.tools.document_paths import (
    DocumentPathError,
    resolve_context_user_name,
    resolve_document_input_path,
    resolve_document_output_path,
)
from app.services.ai.tools.generated_file_service import register_artifact
from app.services.ai.tools.tool_compat import tool

_EXTENSIONS = {".docx"}
_MAX_PREVIEW_PARAGRAPHS = 20
_MAX_PREVIEW_TABLES = 10
_MAX_PREVIEW_TABLE_COLS = 20
_MAX_PREVIEW_CELL_CHARS = 80
_MAX_CONTENT_PARAGRAPHS = 50
_MAX_TABLES_PER_CALL = 10
_MAX_TABLE_ROWS = 100
_WORD_READ_ACTIONS = ("inspect", "read_content", "read_tables")
_WORD_WRITE_ACTIONS = ("create", "replace_text", "append_paragraphs", "append_table")


def _coerce_bounded_int(
    value: Any,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """将工具数值参数安全转换为有界整数，兼容 LLM 传入 None 或数字字符串。"""
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        raise DocumentPathError(f"参数 {name} 必须是整数")
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise DocumentPathError(f"参数 {name} 必须是整数") from exc
    else:
        raise DocumentPathError(f"参数 {name} 必须是整数")
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _extract_row_cells(
    row: Any,
    *,
    max_cols: int | None = None,
    max_cell_chars: int | None = None,
) -> list[str]:
    """安全提取表格行单元格文本；畸形 vMerge 导致 row.cells 异常时降级读底层 tc 节点。"""
    try:
        raw_cells = row.cells
        texts = [cell.text.strip() for cell in (raw_cells[:max_cols] if max_cols is not None else raw_cells)]
    except (AttributeError, IndexError, ValueError):
        tc_list = getattr(getattr(row, "_tr", None), "tc_lst", ())
        if max_cols is not None:
            tc_list = tc_list[:max_cols]
        texts = ["".join(tc.itertext()).strip() for tc in tc_list]
    if max_cell_chars is not None:
        return [
            f"{text[:max_cell_chars]}…" if len(text) > max_cell_chars else text
            for text in texts
        ]
    return texts


def _context():
    context = get_current_agent_context()
    if not context:
        raise DocumentPathError("无法获取当前执行上下文")
    return context


async def _input_path(path: str):
    context = _context()
    return await resolve_document_input_path(
        path,
        allowed_attachment_paths=context.authorized_attachment_paths,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        allowed_extensions=_EXTENSIONS,
        user_name=resolve_context_user_name(context),
    )


async def _output_path(filename: str):
    context = _context()
    return await resolve_document_output_path(
        filename,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        allowed_extensions=_EXTENSIONS,
        user_name=resolve_context_user_name(context),
    )


async def _result(output_path, summary: str, changes: dict[str, Any]) -> dict[str, Any]:
    context = _context()
    artifact = await register_artifact(
        source_path=output_path,
        filename=output_path.name,
        owner_user_id=context.user_id,
        artifact_type="word",
        conversation_id=context.conversation_id,
        trace_id=context.trace_id,
    )
    return {"status": "ok", "summary": summary, "changes": changes, "artifact": artifact.to_tool_payload()}


@tool
async def word_document_read(
    action: Annotated[
        Literal["inspect", "read_content", "read_tables"],
        Field(
            description=(
                "操作类型："
                "'inspect' 查看文档段落数、表格数、前 20 段预览和前 10 个表格的表头预览；"
                "'read_content' 分页读取正文段落（单次最多 50 段）；"
                "'read_tables' 分页读取内嵌表格内容（单次最多 10 个表格，单表最多 100 行）。"
            )
        ),
    ],
    path: Annotated[str, Field(description="要读取的 .docx 文档路径（支持 /workspace/... 或附件路径）。")],
    start: Annotated[
        int,
        Field(description="起始索引（从 0 开始）。read_content 表示起始段落号；read_tables 表示起始表格号。"),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="单次读取条数。read_content 上限 50（默认 20）；read_tables 上限 10（传更大值会截断到 10）。"),
    ] = 20,
    row_start: Annotated[
        int,
        Field(description="仅 read_tables 使用：每张表格的起始行号（从 0 开始），用于超大表格分页。"),
    ] = 0,
    row_limit: Annotated[
        int,
        Field(description="仅 read_tables 使用：每张表格最多返回的行数（上限 100，默认 100）。"),
    ] = 100,
) -> dict[str, Any]:
    """Inspect a Word (.docx) document, read paragraphs, or read embedded tables.

    Supported actions:
    - ``inspect``: return paragraph count, table count, first 20 paragraphs, and previews of up to 10 tables.
    - ``read_content``: read paragraphs starting at ``start`` (0-based), up to 50 paragraphs per call.
    - ``read_tables``: read embedded tables starting at ``start`` (0-based), up to 10 tables per call;
      for large tables, paginate rows with ``row_start`` and ``row_limit`` (max 100 rows per table per call).
    """
    if action not in _WORD_READ_ACTIONS:
        raise DocumentPathError("word_document_read 仅支持 inspect、read_content 或 read_tables")
    document = Document(await _input_path(path))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    tables = document.tables
    if action == "inspect":
        table_previews = [
            {
                "index": idx,
                "row_count": len(table.rows),
                "col_count": len(table.columns),
                "header": _extract_row_cells(
                    table.rows[0],
                    max_cols=_MAX_PREVIEW_TABLE_COLS,
                    max_cell_chars=_MAX_PREVIEW_CELL_CHARS,
                ) if table.rows else [],
            }
            for idx, table in enumerate(tables[:_MAX_PREVIEW_TABLES])
        ]
        return {
            "status": "ok",
            "summary": f"文档包含 {len(paragraphs)} 个段落和 {len(tables)} 个表格",
            "data": {
                "paragraph_count": len(paragraphs),
                "table_count": len(tables),
                "preview": paragraphs[:_MAX_PREVIEW_PARAGRAPHS],
                "table_previews": table_previews,
            },
            "truncated": len(paragraphs) > _MAX_PREVIEW_PARAGRAPHS or len(tables) > _MAX_PREVIEW_TABLES,
        }
    if action == "read_tables":
        table_start = _coerce_bounded_int(start, name="start", default=0, minimum=0)
        table_limit = _coerce_bounded_int(
            limit, name="limit", default=_MAX_TABLES_PER_CALL, minimum=1, maximum=_MAX_TABLES_PER_CALL
        )
        safe_row_start = _coerce_bounded_int(row_start, name="row_start", default=0, minimum=0)
        safe_row_limit = _coerce_bounded_int(
            row_limit, name="row_limit", default=_MAX_TABLE_ROWS, minimum=1, maximum=_MAX_TABLE_ROWS
        )
        selected_tables = tables[table_start:table_start + table_limit]
        result_tables: list[dict[str, Any]] = []
        for offset, table in enumerate(selected_tables):
            all_rows = table.rows
            total_rows = len(all_rows)
            rows_slice = all_rows[safe_row_start:safe_row_start + safe_row_limit]
            rows_data = [_extract_row_cells(row) for row in rows_slice]
            result_tables.append({
                "index": table_start + offset,
                "row_count": total_rows,
                "col_count": len(table.columns),
                "row_start": safe_row_start,
                "rows": rows_data,
                "truncated": safe_row_start + len(rows_data) < total_rows,
            })
        has_more_tables = table_start + len(result_tables) < len(tables)
        has_truncated_rows = any(item["truncated"] for item in result_tables)
        return {
            "status": "ok",
            "summary": f"已读取 {len(result_tables)} 个表格（共 {len(tables)} 个）",
            "data": {
                "tables": result_tables,
                "start": table_start,
                "limit": table_limit,
                "total_tables": len(tables),
            },
            "truncated": has_more_tables or has_truncated_rows,
        }
    para_start = _coerce_bounded_int(start, name="start", default=0, minimum=0)
    para_limit = _coerce_bounded_int(
        limit, name="limit", default=20, minimum=1, maximum=_MAX_CONTENT_PARAGRAPHS
    )
    page = paragraphs[para_start:para_start + para_limit]
    return {
        "status": "ok",
        "summary": f"已读取 {len(page)} 个段落",
        "data": {
            "paragraphs": page,
            "start": para_start,
            "limit": para_limit,
            "total_paragraphs": len(paragraphs),
        },
        "truncated": para_start + len(page) < len(paragraphs),
    }


@tool
async def word_document_write(
    action: Annotated[
        Literal["create", "replace_text", "append_paragraphs", "append_table"],
        Field(
            description=(
                "操作类型："
                "'create' 新建文档（可选 title、paragraphs）；"
                "'replace_text' 按 replacements 替换文本（需 path、replacements）；"
                "'append_paragraphs' 追加段落（需 path、paragraphs）；"
                "'append_table' 追加表格（需 path、headers、rows）。"
            )
        ),
    ],
    output_filename: Annotated[str, Field(description="输出 .docx 文件名，例如 report_v2.docx。")],
    path: Annotated[str | None, Field(description="源 .docx 文档路径；除 action='create' 外均必填。")] = None,
    replacements: Annotated[
        list[dict[str, str]] | None,
        Field(description="仅 replace_text 使用：替换项列表，每项为 {'find': '旧文本', 'replace': '新文本'}。"),
    ] = None,
    paragraphs: Annotated[
        list[str] | None,
        Field(description="create 或 append_paragraphs 使用：要写入的段落文本列表。"),
    ] = None,
    headers: Annotated[
        list[str] | None,
        Field(description="仅 append_table 使用：表头列名列表。"),
    ] = None,
    rows: Annotated[
        list[list[str]] | None,
        Field(description="仅 append_table 使用：数据行二维列表，每行列数需与 headers 一致。"),
    ] = None,
    title: Annotated[
        str | None,
        Field(description="仅 create 使用：文档一级标题。"),
    ] = None,
) -> dict[str, Any]:
    """Create or modify a Word (.docx) document copy and return a download link.

    Supported actions: ``create``, ``replace_text``, ``append_paragraphs``, ``append_table``.
    Copy artifact.download_url verbatim in the final response; do not alter its
    protocol, host, path, or token.
    """
    if action not in _WORD_WRITE_ACTIONS:
        raise DocumentPathError("word_document_write 仅支持 create、replace_text、append_paragraphs 或 append_table")
    if action == "create":
        document = Document()
        if title:
            document.add_heading(title, level=1)
    else:
        if not path:
            raise DocumentPathError("修改文档需要 path")
        document = Document(await _input_path(path))
    changes: dict[str, Any] = {}
    if action == "replace_text":
        count = 0
        for replacement in replacements or []:
            find, replace = replacement.get("find", ""), replacement.get("replace", "")
            if not find:
                raise DocumentPathError("替换目标不能为空")
            for paragraph in document.paragraphs:
                for run in paragraph.runs:
                    if find in run.text:
                        run.text = run.text.replace(find, replace)
                        count += 1
        changes["replacements"] = count
    elif action == "append_paragraphs":
        for text in paragraphs or []:
            document.add_paragraph(text)
        changes["appended_paragraphs"] = len(paragraphs or [])
    elif action == "append_table":
        if not headers:
            raise DocumentPathError("append_table 需要 headers")
        table = document.add_table(rows=1, cols=len(headers))
        for index, header in enumerate(headers):
            table.rows[0].cells[index].text = str(header)
        for row in rows or []:
            if len(row) != len(headers):
                raise DocumentPathError("表格行列数必须与 headers 一致")
            cells = table.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = str(value)
        changes["appended_table_rows"] = len(rows or [])
    else:
        for text in paragraphs or []:
            document.add_paragraph(text)
        changes["created_document"] = True
    output_path = await _output_path(output_filename)
    document.save(output_path)
    return await _result(output_path, "已生成 Word 文档", changes)

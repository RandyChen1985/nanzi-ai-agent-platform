"""将 ChatBI SQL 工具层错误映射为用户可见文案。

仅用于 SSE 正文与终态 Guard；repair 提示、Agent 对话历史仍使用工具原始返回，
避免影响模型理解与自动修正。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


GENERIC_SQL_ERROR_CONTENT = (
    "数据查询遇到了一些技术问题，暂时无法获取结果。\n\n"
    "💡 **建议您可以尝试**：\n"
    "1. 稍微修改提问的表述，避免过于复杂的交叉分析或含糊的口径。\n"
    "2. 若多次尝试依然失败，可能是底层服务正在维护，请稍后重试或联系管理员。"
)

EMPTY_FILTER_RESULT_FALLBACK_CONTENT = (
    "SQL 已成功执行，但按当前筛选条件未返回数据。\n\n"
    "💡 **建议您可以尝试**：\n"
    "1. 检查筛选值是否与库内真实取值完全一致（注意大小写、空格、前后缀或编码格式差异）。\n"
    "2. 适当放宽或修正筛选条件后重新提问。\n"
    "3. 确认当前数据集是否包含您想查询的信息。"
)


@dataclass(frozen=True)
class SqlUserErrorPresentation:
    title: str
    content: str
    specific: bool = True


def format_empty_filter_result_content(diagnostics: list[dict[str, Any]] | None = None) -> str:
    """D：空结果 + 文本筛选诊断后的用户可见文案（区别于 SQL 技术故障）。"""
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterDiagnosticResult,
        format_empty_filter_guard_message,
    )

    if not diagnostics:
        return EMPTY_FILTER_RESULT_FALLBACK_CONTENT
    parsed: list[FilterDiagnosticResult] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        parsed.append(
            FilterDiagnosticResult(
                column=str(item.get("column") or ""),
                table=str(item.get("table") or ""),
                operator=str(item.get("operator") or ""),
                used_values=tuple(item.get("used_values") or ()),
                diagnostic_sql=str(item.get("diagnostic_sql") or ""),
                candidates=[str(value) for value in (item.get("candidates") or [])],
                suggested_values=[str(value) for value in (item.get("suggested_values") or [])],
                suspect_wrong_column=bool(item.get("suspect_wrong_column")),
                alternative_columns=[str(value) for value in (item.get("alternative_columns") or [])],
                matched_alternative_column=str(item.get("matched_alternative_column") or ""),
                matched_alternative_values=[
                    str(value) for value in (item.get("matched_alternative_values") or [])
                ],
                error=str(item.get("error") or ""),
            )
        )
    specific = format_empty_filter_guard_message(parsed)
    return specific or EMPTY_FILTER_RESULT_FALLBACK_CONTENT


def map_sql_tool_error_for_user(raw: str) -> SqlUserErrorPresentation:
    text = str(raw or "").strip()
    if not text:
        return SqlUserErrorPresentation(
            title="数据查询失败",
            content=GENERIC_SQL_ERROR_CONTENT,
            specific=False,
        )

    if text.startswith("[Permission Denied]"):
        return _map_permission_denied(text)

    if text.startswith("[Security Error]"):
        return SqlUserErrorPresentation(
            title="数据权限处理失败",
            content=(
                "⚠️ 系统在应用行级数据权限时遇到问题，本次查数已终止。\n"
                "请稍后重试；若问题持续，请联系管理员检查数据权限配置。"
            ),
        )

    dataset_match = re.match(r"Error: Dataset '([^']+)' not found", text)
    if dataset_match:
        dataset_name = dataset_match.group(1)
        return SqlUserErrorPresentation(
            title="数据集不存在",
            content=(
                f"⚠️ 未找到名为「{dataset_name}」的数据集，本次查数已终止。\n\n"
                "建议：\n"
                "1. 检查数据集名称是否正确；\n"
                "2. 先检索您有权限访问的数据集，确认名称后再查数。"
            ),
        )

    if "[Validation Failed]" in text:
        return _map_validation_failed(text)

    text_lower = text.lower()
    if any(
        marker in text_lower
        for marker in (
            "unknown column",
            "unknown table",
            "syntax error",
            "sql syntax",
            "invalid expression",
            "unexpected token",
        )
    ):
        return SqlUserErrorPresentation(
            title="SQL 语法或字段问题",
            content=(
                "⚠️ 生成的 SQL 存在语法、字段或表引用问题，暂时无法执行。\n\n"
                "建议：简化提问、补充更明确的表名/字段名，或换一种表述后重试。"
            ),
        )

    if any(
        marker in text_lower
        for marker in ("access denied", "permission denied", "unauthorized")
    ):
        return SqlUserErrorPresentation(
            title="数据访问被拒绝",
            content=(
                "⚠️ 数据权限校验未通过，本次查数已终止。\n"
                "请确认数据集与表的访问权限，或联系管理员。"
            ),
        )

    return SqlUserErrorPresentation(
        title="数据查询失败",
        content=GENERIC_SQL_ERROR_CONTENT,
        specific=False,
    )


def _map_permission_denied(text: str) -> SqlUserErrorPresentation:
    if "缺少用户身份" in text:
        return SqlUserErrorPresentation(
            title="无法校验数据权限",
            content=(
                "⚠️ 当前会话无法确认您的身份，数据权限校验未通过，本次查数已终止。\n"
                "请重新登录或联系管理员。"
            ),
        )

    # 字段级拒绝要排在表级之前：文案是「无权访问表 'X' 的字段 'y'」，
    # 表级正则会先命中并说成「没有这张表的权限」—— 用户明明能看这张表，只是看不到某几列。
    column_match = re.search(r"无权访问表\s*'([^']+)'\s*的字段\s*'([^']+)'", text)
    if column_match:
        table_name, column_name = column_match.group(1), column_match.group(2)
        more = re.search(r"等\s*(\d+)\s*个", text)
        suffix = f" 等 {more.group(1)} 个字段" if more else ""
        return SqlUserErrorPresentation(
            title="无权访问部分字段",
            content=(
                f"⚠️ 您可以查询数据表「{table_name}」，但没有字段「{column_name}」{suffix}的访问权限，"
                "本次查数已终止。\n\n"
                "建议：\n"
                "1. 换一个不涉及该字段的问法（例如只看汇总或其它列）；\n"
                "2. 联系管理员在「权限管理」里把该字段加入您的授权范围。"
            ),
        )

    table_match = re.search(r"无权访问表\s*'([^']+)'", text)
    if table_match:
        table_name = table_match.group(1)
        return SqlUserErrorPresentation(
            title="无权访问数据表",
            content=(
                f"⚠️ 您没有访问数据表「{table_name}」的权限，本次查数已终止。\n\n"
                "建议：\n"
                "1. 确认是否选对了数据集；\n"
                "2. 联系管理员申请该表的访问权限后重试。"
            ),
        )

    # 字段级：无权字段出现在 WHERE / GROUP BY / ORDER BY 里。删掉会改变口径，
    # 所以只能拒绝 —— 文案要说明「为什么这次不能像少一列那样直接省略」。
    if "筛选/分组/排序条件里" in text or "出现在筛选" in text:
        missing = re.findall(r"'([^']+)'", text)
        names = "、".join(f"「{n}」" for n in missing[:3]) or "部分字段"
        return SqlUserErrorPresentation(
            title="查询条件用到了无权字段",
            content=(
                f"⚠️ 本次查询的筛选/分组/排序条件里用到了你没有权限的字段{names}。\n"
                "这类条件没法像返回列那样直接省略（删掉会改变统计口径），因此本次查数已终止。\n\n"
                "建议：换用你有权限的字段作为筛选条件，或联系管理员开通该字段。"
            ),
        )

    # 字段级：用户要的列全都没权限
    if "没有任何可返回的字段" in text or "都不在你的授权范围内" in text:
        missing = re.findall(r"'([^']+)'", text)
        names = "、".join(f"「{n}」" for n in missing[:5])
        return SqlUserErrorPresentation(
            title="无权访问请求的字段",
            content=(
                f"⚠️ 你请求的字段{(' ' + names) if names else ''}都不在你的数据权限范围内，"
                "本次查数没有可返回的内容。\n\n"
                "建议：换一个不涉及这些字段的问法，或联系管理员在「权限管理」里放开。"
            ),
        )

    return SqlUserErrorPresentation(
        title="数据访问被拒绝",
        content=(
            "⚠️ 数据权限校验未通过，本次查数已终止。\n"
            "请确认数据集与表的访问权限，或联系管理员。"
        ),
    )


def _map_validation_failed(text: str) -> SqlUserErrorPresentation:
    if "未在元数据中注册" in text or ("物理表" in text and "不存在" in text):
        table_match = re.search(r"物理表\s*'([^']+)'", text)
        table_name = table_match.group(1) if table_match else "指定表"
        return SqlUserErrorPresentation(
            title="数据表不存在",
            content=(
                f"⚠️ 数据表「{table_name}」未在元数据中注册或不存在，无法继续查询。\n\n"
                "建议：先检索数据集定义，使用返回的物理表名（table_name）再查数。"
            ),
        )

    term_match = re.search(r"'([^']+)'\s*是业务术语", text)
    if term_match:
        term = term_match.group(1)
        physical_match = re.search(r"物理表名\s*'([^']+)'", text)
        physical_hint = (
            f"请改用物理表名「{physical_match.group(1)}」。"
            if physical_match
            else "请改用数据集定义中的物理表名（table_name）。"
        )
        return SqlUserErrorPresentation(
            title="请使用物理表名",
            content=(
                f"⚠️ 「{term}」是业务术语，不能直接写在 SQL 的 FROM/JOIN 中。{physical_hint}\n\n"
                "建议：重新检索数据集定义后再查数。"
            ),
        )

    dataset_name_match = re.search(r"'([^']+)'\s*是数据集名称", text)
    if dataset_name_match:
        name = dataset_name_match.group(1)
        return SqlUserErrorPresentation(
            title="请勿将数据集名当作表名",
            content=(
                f"⚠️ 「{name}」是数据集名称，不能用于 SQL 的 FROM/JOIN。\n\n"
                "建议：检索数据集定义后，使用其中的物理表名查询。"
            ),
        )

    cross_dataset_match = re.search(
        r"表\s*'([^']+)'\s*不属于当前指定的数据集\s*'([^']+)'",
        text,
    )
    if cross_dataset_match:
        table_name, dataset_name = cross_dataset_match.groups()
        return SqlUserErrorPresentation(
            title="表与数据集不匹配",
            content=(
                f"⚠️ 表「{table_name}」不属于数据集「{dataset_name}」，"
                "无法跨数据集或凭空猜表查询。\n\n"
                "建议：重新检索该数据集下的可用表名后再查数。"
            ),
        )

    if "SQL语法错误" in text:
        return SqlUserErrorPresentation(
            title="SQL 语法问题",
            content=(
                "⚠️ 生成的 SQL 存在语法或结构问题，暂时无法执行。\n\n"
                "建议：简化提问或换一种表述；系统也会尝试自动修正。"
            ),
        )

    return SqlUserErrorPresentation(
        title="SQL 校验未通过",
        content=(
            "⚠️ 本次 SQL 未通过平台校验（表名、数据集范围或语法可能不正确）。\n\n"
            "建议：补充更明确的表名/指标口径，或让系统重新检索数据集定义后再查数。"
        ),
    )

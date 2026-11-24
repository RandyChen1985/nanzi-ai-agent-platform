"""AgentScope 工具结果状态与安全错误摘要。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.services.ai.error_response_service import sanitize_error_text
from app.services.ai.grounding.ledger import classify_evidence_result
from app.services.ai.grounding.models import EvidenceStatus, ToolResultEnvelope
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.ai.runtime.agentscope.tool_result_context import (
    IMAGE_USAGE_METADATA_KEY,
    TOOL_CALL_ID_METADATA_KEY,
    TOOL_RESULT_CONTEXT_VERSION,
    attach_tool_call_id_metadata,
    image_usage_from_metadata,
    is_trusted_tool_result_context,
    tool_call_id_from_metadata,
)

_FAILURE_STATES = frozenset({
    "error",
    "failed",
    "failure",
    "denied",
    "interrupted",
    "timeout",
    "timed_out",
})
_SUCCESS_STATES = frozenset({"success", "succeeded", "finished", "completed"})
#: 中断（整轮被取消）时给用户看的统一说法。原文来自 AgentScope 的内部提示
#: `<system-reminder>The tool call has been interrupted by the user.</system-reminder>`，
#: 而取消也可能来自断流、看门狗或上游中断，不能一口咬定是用户点的停止。
_INTERRUPTED_TOOL_REASON = "本轮已取消，工具未返回结果"
#: AgentScope 注入的内部标记，不是工具输出的一部分，展示前必须剥掉。
_INTERNAL_MARKUP_RE = re.compile(r"</?system-reminder>")
_BASH_FAILURE_MARKER_RE = re.compile(
    r"(?im)^(?:command\s+(?:failed|timed out)|error\s*:|stderr\s*:|"
    r"permission denied\b|permissiondenied\b)"
)
_LEGACY_FAILURE_MARKER_RE = re.compile(
    r"(?i)(?:安全策略拦截|permission\s+denied|permissiondenied)"
)
_EXPLICIT_FAILURE_TEXT_RE = re.compile(
    r"(?i)^(?:\s*"
    r"\[(?:tool\s*_?error|mcp\s+error|execution\s+error|error)\]"
    r"|error\s*:|stderr\s*:|command\s+(?:failed|timed\s+out)\b"
    r"|permission\s+denied\b|permissiondenied\b"
    r"|安全策略拦截|权限(?:不足|拒绝))"
)
_FAILURE_RESULT_STATES = frozenset({
    "error",
    "failed",
    "failure",
    "denied",
    "interrupted",
    "timeout",
    "timed_out",
})


def normalize_tool_result_state(value: Any) -> str:
    """将 AgentScope 枚举或兼容实现转换成稳定的小写状态名。"""

    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def is_tool_result_success_state(value: Any) -> bool:
    """判断 AgentScope 最终结果是否为成功状态。"""

    return normalize_tool_result_state(value) in _SUCCESS_STATES


def is_tool_result_failure_state(value: Any) -> bool:
    """判断 AgentScope 最终结果是否为失败状态。"""

    return normalize_tool_result_state(value) in _FAILURE_STATES


def _payload_has_explicit_execution_failure(result: Any) -> bool:
    """只识别结构化或明确控制标记，不把普通中文正文当成执行失败。"""

    if result is None:
        return False
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        return True

    result_state = normalize_tool_result_state(getattr(result, "state", None))
    if result_state in _FAILURE_RESULT_STATES:
        return True

    if isinstance(result, str):
        return bool(_EXPLICIT_FAILURE_TEXT_RE.match(result.strip()))

    if isinstance(result, dict):
        if bool(result.get("isError") or result.get("is_error")):
            return True
        if result.get("success") is False:
            return True
        status = normalize_tool_result_state(result.get("status"))
        if status in _FAILURE_RESULT_STATES:
            return True
        state = normalize_tool_result_state(result.get("state"))
        if state in _FAILURE_RESULT_STATES:
            return True
        try:
            if int(result.get("code")) >= 400:
                return True
        except (TypeError, ValueError):
            pass
        if result.get("error") not in (None, "", False, [], {}):
            return True
        message = result.get("message")
        if isinstance(message, str) and re.search(
            r"(?i)(?:(?:执行|调用|查询|读取|检索|搜索|连接|认证|授权).{0,6}"
            r"(?:失败|异常|错误|拒绝)|(?:无权限|权限不足))",
            message,
        ):
            return True
        return False

    if isinstance(result, (list, tuple, set, frozenset)):
        return any(
            _payload_has_explicit_execution_failure(item)
            for item in result
            if hasattr(item, "state")
            or hasattr(item, "isError")
            or hasattr(item, "is_error")
            or isinstance(item, dict)
        )
    return False


def is_tool_execution_success(result: Any, *, result_state: Any = None) -> bool:
    """判断工具调用是否执行成功，与证据是否可用保持独立。

    空结果、成功但无返回内容的动作工具仍属于执行成功；只有显式失败状态、
    结构化失败字段或明确的错误控制文本才判定为执行失败。
    """

    if _payload_has_explicit_execution_failure(result):
        return False
    state = normalize_tool_result_state(result_state)
    if not state:
        state = normalize_tool_result_state(getattr(result, "state", None))
    if state in _FAILURE_RESULT_STATES:
        return False
    if state in _SUCCESS_STATES:
        return True
    # 调用已经正常返回但没有 AgentScope 最终状态时，视为执行完成；
    # 若调用方明确提供了未知状态，则 fail-closed。
    return not bool(result_state is not None and state)


def _coerce_datetime(value: Any, *, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _result_metadata(result: Any, *keys: str) -> Any:
    payload = result
    if isinstance(result, str) and result.strip().startswith(("{", "[")):
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            payload = None
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "否"}:
            return False
        if normalized in {"1", "true", "yes", "on", "是"}:
            return True
    return bool(value)


def build_tool_result_envelope(
    *,
    call_id: str,
    producer: str,
    result: Any,
    evidence_policy: str = "non_empty",
    result_state: Any = None,
    source_ref: str | None = None,
    observed_at: datetime | str | None = None,
    source_as_of: datetime | str | None = None,
    truncated: bool | None = None,
) -> ToolResultEnvelope:
    """在工具调用最终收口处构造统一取证凭证。

    ``result_state`` 是 AgentScope 的最终状态时优先用于排除失败调用；
    payload 仅负责区分成功非空与成功为空。凭证不会替换工具原始返回值。
    """

    normalized_state = normalize_tool_result_state(result_state)
    payload_status = classify_evidence_result(result)
    if normalized_state in _FAILURE_STATES:
        status = EvidenceStatus.FAILED
    elif normalized_state and normalized_state not in _SUCCESS_STATES:
        status = EvidenceStatus.UNKNOWN
    else:
        # 成功状态不能覆盖 payload 自身的错误/拒绝标记，保持 fail-closed。
        status = payload_status

    eligible = status in {
        EvidenceStatus.SUCCESS_NON_EMPTY,
        EvidenceStatus.SUCCESS_EMPTY,
    }
    if status is EvidenceStatus.SUCCESS_EMPTY and evidence_policy != "allow_empty_success":
        eligible = False

    now = datetime.now(timezone.utc)
    result_observed_at = _result_metadata(result, "observed_at", "saved_at")
    result_source_as_of = _result_metadata(result, "source_as_of", "data_as_of")
    result_source_ref = _result_metadata(result, "source_ref")
    result_truncated = _result_metadata(result, "truncated", "is_truncated", "output_truncated")
    return ToolResultEnvelope(
        status=status,
        call_id=str(call_id),
        producer=str(producer),
        result=result,
        source_ref=(
            str(source_ref)
            if source_ref not in (None, "")
            else (str(result_source_ref) if result_source_ref not in (None, "") else None)
        ),
        observed_at=(
            _coerce_datetime(observed_at)
            or _coerce_datetime(result_observed_at)
            or now
        ),
        source_as_of=(
            _coerce_datetime(source_as_of)
            or _coerce_datetime(result_source_as_of)
        ),
        truncated=(
            _coerce_bool(truncated)
            if truncated is not None
            else _coerce_bool(result_truncated)
        ),
        evidence_eligible=eligible,
    )


#: 「清单型」工具：结果是全序枚举，模型靠"列表里有没有它"判断某个对象是否存在。
#:
#: 这类结果被字符层拦腰截断有两个后果：① 尾部条目看不见 → 模型断言"不存在"；
#: ② 截断点落在条目中间 → JSON 语法损坏，模型连"一共几条"都读不出来。
#: 两者叠加就是「一会能查到一会查不到」：同一份清单在同一轮里完整可见（64Ki 闸门），
#: 到了下一轮重建上下文时只剩开头几条（800 字符闸门）。
CATALOG_TOOL_NAMES = frozenset(
    {
        "list_available_skills",
        "list_accessible_datasets",
        "list_accessible_directories",
        "list_accessible_knowledge_bases",
        "list_available_agents",
        "list_my_credentials",
        "list_notification_channels",
    }
)

#: 清单工具中真正支持 ``keyword`` 参数的子集。
#:
#: 其余无参或只按枚举筛选的清单工具（知识库/智能体/目录/消息群）若被提示「用 keyword
#: 收窄」，会诱导模型传非法参数报错。
CATALOG_KEYWORD_TOOL_NAMES = frozenset(
    {
        "list_available_skills",
        "list_accessible_datasets",
        "list_my_credentials",
    }
)

#: 单个清单型工具在跨轮上下文里的正文上限（字符）。
#:
#: 必须给 ``build_final_tool_result_context`` 的 ``max_total_chars``（默认 4000）留出
#: 工具名/入参前缀与其它工具行的空间，否则清单行会被总长闸门二次截断——那就又回到
#: "合法 JSON 被硬切"的老问题。
CATALOG_TOOL_OUTPUT_MAX_CHARS = 3400

#: 清单型返回里承载条目的常见键名（新工具用对象包裹，旧工具是裸数组）。
_CATALOG_LIST_KEYS = (
    "skills",
    "items",
    "datasets",
    "agents",
    "directories",
    "knowledge_bases",
    "credentials",
    "channels",
    "results",
    "blocked",
)

#: 跨轮重建时不保留的容器标量字段：它们描述**上一层**的截断状态，由下面重新生成。
#: ``keyword`` 必须保留：它标明 ``total`` 是该 keyword 过滤后的总数而非全量。
_CATALOG_DROPPED_EXTRAS = frozenset({"total", "returned", "truncated", "note"})


def _catalog_payload_parts(parsed: Any) -> tuple[str, list[Any], dict[str, Any] | None]:
    """取出（承载列表的键名，条目列表，原始容器）。

    兼容两种返回形态：裸数组（``[ {...}, ... ]``）与对象（``{"skills": [...], ...}``）。
    对双列表容器（如 ``list_available_agents`` 的 ``items`` + ``blocked``），优先选
    首个**非空**列表，避免 ``items`` 为空但 ``blocked`` 很长时退回字符硬切。
    """
    if isinstance(parsed, list):
        return "items", parsed, None
    if isinstance(parsed, dict):
        first_empty: tuple[str, list[Any], dict[str, Any]] | None = None
        for key in _CATALOG_LIST_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                if value:
                    return key, value, parsed
                if first_empty is None:
                    first_empty = (key, value, parsed)
        # 兜底：任意一个 list 字段都算清单（工具名各异，不硬编码全部字段名）。
        for key, value in parsed.items():
            if isinstance(value, list):
                if value:
                    return str(key), value, parsed
                if first_empty is None:
                    first_empty = (str(key), value, parsed)
        if first_empty is not None:
            return first_empty
    return "", [], None


def _clip_catalog_payload(text: str, *, tool_name: str, max_chars: int) -> str:
    """清单型工具的结构化截断：按条目裁剪，结果仍是合法 JSON 且标明总数。

    未超配额时原样返回（不改写工具自己的输出格式）。超配额时丢弃尾部条目，并补上
    ``total`` / ``returned`` / ``truncated`` 与一句中文说明——让模型明确知道"还有没展示的"，
    这是"没看到 ≠ 不存在"能被模型正确推理的前提。
    """
    raw = str(text or "")
    if max_chars <= 0 or len(raw) <= max_chars:
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        # 不是 JSON（工具走了错误文案等分支）：退回通用截断，不强求结构。
        return truncate_for_context(raw, max_len=max_chars)

    key, items, container = _catalog_payload_parts(parsed)
    if not key or not items:
        return truncate_for_context(raw, max_len=max_chars)

    total = len(items)
    # 上一层可能已经按 limit 截过（工具的 total 才是真实总数）：跨轮重建不能再缩水，
    # 否则模型看到的"共 N 条"比实际更少，又变成一种无声的信息丢失。
    if isinstance(container, dict):
        declared_total = container.get("total")
        if (
            isinstance(declared_total, int)
            and not isinstance(declared_total, bool)
            and declared_total > total
        ):
            total = declared_total
    # 容器上的少量标量字段（如 scope / keyword）保留：它们解释清单本身，比条目更省字符。
    # 旧的 total/returned/truncated/note 由下面重新生成。
    extras = {
        str(k): v
        for k, v in (container or {}).items()
        if str(k) != key and not isinstance(v, (list, dict))
    }
    # 记录被省略的其它非空列表字段名（如 list_available_agents 的 blocked），避免静默消失。
    omitted_list_keys = [
        str(k)
        for k, v in (container or {}).items()
        if str(k) != key and isinstance(v, list) and v
    ]
    supports_keyword = tool_name in CATALOG_KEYWORD_TOOL_NAMES

    def _render(kept: list[str], kept_count: int) -> tuple[str, str]:
        # key 走 json.dumps 转义：它是工具提供的字段名，直接拼进字符串会破坏 JSON 语法。
        payload = "{" + json.dumps(key, ensure_ascii=False) + ":[" + ",".join(kept) + "]"
        for extra_key, extra_value in extras.items():
            if extra_key in _CATALOG_DROPPED_EXTRAS:
                continue
            payload += ",%s:%s" % (
                json.dumps(extra_key, ensure_ascii=False),
                json.dumps(extra_value, ensure_ascii=False),
            )
        is_truncated = kept_count < total or bool(omitted_list_keys)
        payload += ',"total":%d,"returned":%d,"truncated":%s}' % (
            total,
            kept_count,
            "true" if is_truncated else "false",
        )
        if not is_truncated:
            return payload, ""
        extra_hint = (
            f"；另有分组 {', '.join(omitted_list_keys)} 未展开"
            if omitted_list_keys
            else ""
        )
        action_hint = (
            f"需要未展示的条目请再次调用 {tool_name} 并用 keyword 收窄"
            if supports_keyword
            else f"需要完整列表请再次调用 {tool_name} 查看"
        )
        note = (
            f"\n（共 {total} 条，本次上下文仅保留前 {kept_count} 条"
            f"{extra_hint}；{action_hint}）"
        )
        return payload, note

    # 预留尾部说明的最坏长度（kept_count=1 时最长），避免"裁剪完才发现说明超限"。
    note_reserve = len(_render([], 1)[1]) + 8
    budget = max_chars - note_reserve
    kept: list[str] = []
    used = 0
    for item in items:
        chunk = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        cost = len(chunk) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(chunk)
        used += cost

    # 实际长度以渲染结果为准；超出就继续丢尾部条目（保证正文不超配额）。
    while kept:
        payload, note = _render(kept, len(kept))
        if len(payload) + len(note) <= max_chars:
            return payload + note
        kept.pop()
    return truncate_for_context(raw, max_len=max_chars)


def _join_tool_result_lines(
    lines: list[str],
    *,
    max_total_chars: int,
) -> str:
    """按整行拼装多条工具结果，避免把任何一行正文拦腰截断。

    旧实现是 ``"\\n".join(lines)[:max_total_chars]``：整段硬切会在任意位置断开，
    恰好落在 JSON 中时模型就拿到一份"看起来完整"的残缺清单。现在改为整行淘汰：
    已保留至少一条时，放不下的后续行直接整行省略并准确标明「另有 N 条未保留」；
    仅当首行自身就超过总预算时才对首行做软截断。
    """
    if not lines:
        return ""
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        cost = len(line) + (1 if kept else 0)
        if used + cost <= max_total_chars:
            kept.append(line)
            used += cost
            continue
        remaining = len(lines) - index
        if kept:
            return "\n".join(kept) + f"\n… [另有 {remaining} 条工具结果因上下文长度未保留]"
        others = remaining - 1
        suffix = (
            f"\n… [输出已截断，另有 {others} 条工具结果未保留]"
            if others > 0
            else "\n… [输出已截断]"
        )
        return line[:max_total_chars].rstrip() + suffix
    return "\n".join(kept)


def build_final_tool_result_context(
    meta: dict[str, Any] | None,
    *,
    max_total_chars: int = 4000,
) -> str:
    """只把已收到最终成功状态的工具结果编成跨轮模型上下文。

    ``tool_outputs`` 也包含流式中间文本，因此必须以
    ``tool_result_states`` 的最终状态为准；缺少最终状态的条目默认丢弃。
    """

    if not isinstance(meta, dict):
        return ""
    tool_names = meta.get("tool_names") or {}
    tool_args_text = meta.get("tool_args_text") or {}
    tool_outputs = meta.get("tool_outputs") or {}
    tool_data = meta.get("tool_data") or {}
    result_states = meta.get("tool_result_states") or {}
    if not isinstance(tool_names, dict):
        return ""

    # 单条清单预算不得超过总预算，否则总预算较小时单条清单也会被二次硬切。
    catalog_max_chars = (
        max(120, min(CATALOG_TOOL_OUTPUT_MAX_CHARS, max_total_chars - 120))
        if max_total_chars > 240
        else max(1, max_total_chars)
    )

    catalog_lines: list[str] = []
    plain_lines: list[str] = []
    for tool_id, tool_name in tool_names.items():
        if not is_tool_result_success_state(result_states.get(tool_id)):
            continue
        try:
            name = str(tool_name or "").strip()
            if not name:
                continue
            arg_preview = str(tool_args_text.get(tool_id) or "{}")
            output = str(tool_outputs.get(tool_id) or "")
            data_blocks = tool_data.get(tool_id) or []
        except Exception:
            continue
        result_for_validation: Any = output
        if data_blocks:
            result_for_validation = {"text": output, "data_blocks": data_blocks}
        envelope = build_tool_result_envelope(
            call_id=str(tool_id),
            producer=name,
            result=result_for_validation,
            result_state=result_states.get(tool_id),
            evidence_policy="allow_empty_success",
        )
        if not envelope.evidence_eligible:
            continue
        block_note = f" (data_blocks={len(data_blocks)})" if data_blocks else ""
        if name in CATALOG_TOOL_NAMES:
            rendered = _escape_tool_context_text(
                _clip_catalog_payload(
                    output,
                    tool_name=name,
                    max_chars=catalog_max_chars,
                )
            )
            catalog_lines.append(f"{name}: {arg_preview} -> {rendered}{block_note}")
        else:
            rendered = _escape_tool_context_text(
                truncate_for_context(output, max_len=800)
            )
            plain_lines.append(f"{name}: {arg_preview} -> {rendered}{block_note}")

    # 清单型结果优先入上下文：它们是"对象是否存在"的唯一依据，一旦被普通结果挤掉，
    # 模型就只能把"没看到"当成"不存在"。
    return _join_tool_result_lines(
        catalog_lines + plain_lines,
        max_total_chars=max_total_chars,
    )


def _escape_tool_context_text(value: str) -> str:
    """防止不可信工具文本伪造内部上下文结束标签。"""

    return str(value or "").replace("</backend_tool_result_context>", "<\\/backend_tool_result_context>")


def _tool_output_text(output: Any) -> str:
    if isinstance(output, dict) and "text" in output:
        return str(output.get("text") or "")
    return str(output or "")


def is_tool_result_error(
    tool_name: str,
    output: Any,
    *,
    result_state: Any = None,
    domain_error: bool = False,
) -> bool:
    """判断工具是否失败，优先使用工具运行时的最终状态。

    AgentScope 的 ``ToolResultEndEvent.state`` 是执行结果的权威来源。
    文本标记只为旧事件或业务层错误保留兼容回退，避免成功输出中出现
    ``Error`` 单词时被误判。
    """

    if domain_error:
        return True

    state = normalize_tool_result_state(result_state)
    if state in _SUCCESS_STATES:
        return False
    if state in _FAILURE_STATES:
        return True

    text = _tool_output_text(output)
    if str(tool_name or "").strip().lower() == "bash":
        return bool(_BASH_FAILURE_MARKER_RE.search(text))
    return bool(_LEGACY_FAILURE_MARKER_RE.search(text))


def extract_tool_result_error_reason(
    tool_name: str,
    output: Any,
    *,
    result_state: Any = None,
    domain_error: bool = False,
    max_length: int = 300,
) -> str:
    """从失败结果提取一条脱敏摘要，供用户时间线展示。"""

    if not is_tool_result_error(
        tool_name,
        output,
        result_state=result_state,
        domain_error=domain_error,
    ):
        return ""

    # 中断不是工具自己报的错：AgentScope 在 reply 被取消时会给还没跑完的调用补一条
    # `<system-reminder>The tool call has been interrupted by the user.</system-reminder>`。
    # 直接展示既是一串 XML，又把「取消」一律说成用户点的停止（断流、看门狗、上游中断
    # 也会走到这里），所以统一换成一句中性说明。
    if normalize_tool_result_state(result_state) == "interrupted":
        return _INTERRUPTED_TOOL_REASON

    text = _INTERNAL_MARKUP_RE.sub("", _tool_output_text(output))
    lines = [line.strip() for line in text.splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        return "工具执行失败"

    candidate = ""
    command_failed = False
    for index, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith("command timed out"):
            candidate = "命令执行超时"
            break
        if lowered.startswith("command failed"):
            command_failed = True
        if lowered.startswith("stderr:"):
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                candidate = remainder
                break
            candidate = next(
                (
                    next_line
                    for next_line in lines[index + 1 :]
                    if next_line and next_line.lower() not in {"stdout:", "stderr:"}
                ),
                "",
            )
            if candidate:
                break
        if lowered.startswith("error:"):
            candidate = line.split(":", 1)[1].strip() or line
            break

    if not candidate:
        if command_failed:
            candidate = "命令执行失败（退出码非 0）"
        else:
            candidate = next(
                (
                    line
                    for line in non_empty
                    if "timed out" in line.lower()
                    or line.lower().startswith("permission denied")
                    or line.lower().startswith("permissiondenied")
                ),
                non_empty[0],
            )

    return sanitize_error_text(candidate, max_length=max_length)

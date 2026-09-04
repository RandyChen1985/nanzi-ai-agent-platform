"""工具促发（Tool Nudge）：由「本轮实际绑定的工具 + 其描述」驱动。

当用户问题与某个已绑定工具的能力（name + description）明显相关、且本轮属于
「应当用工具拿真实结果」的意图时，在 System Prompt 顶部注入一条强约束便签，
提高模型主动调用工具的概率。

设计要点：
- **不写死类别/工具名**：候选完全来自运行时 `tools` 的 name + description，
  因此平台内置工具、通用 API 工具、MCP 工具都能被自动促发。
- **不新增 LLM 调用**：相关度用字符级 bigram 重叠（无第三方分词依赖，中英通用）。
- **门禁过滤**：问候 / 元操作 / 过短问题不促发；记忆类有专门的 memory_search 便签。
- **命中至多一条**：取相关度最高的工具，避免 prompt 噪声与误触发。
- **计划预检**：运行时存在 `todo_write` 时，结构化多步骤请求首轮优先写入任务清单，之后恢复正常工具选择。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any, List, Mapping, Optional, Sequence, Set

from app.services.ai.request_decision import (
    RequestCapability,
    RequestDecision,
    RequestSource,
    resolve_request_decision,
)
from app.services.ai.chatbi_qualification import ChatBIMode
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.tool_policy import ToolMetadata, resolve_tool_metadata
from app.services.ai.grounding.models import EvidenceType

logger = logging.getLogger(__name__)

# 不主动促发的工具（写入/管理/记忆维护类）：避免推动模型产生副作用或与专门机制重复。
_NUDGE_EXCLUDED_TOOLS = frozenset({
    "update_user_preference",
    "delete_user_preference",
    "fetch_user_long_term_memory",
    "memory_search",
    "create_skills",
    "update_dashboard_context",
})

_OFFICE_TOOL_NAMES = frozenset({
    "word_document_read",
    "word_document_write",
    "excel_document_read",
    "excel_document_write",
})
_OFFICE_EXPLICIT_TOOL_NAMES = (
    "word_document_read",
    "word_document_write",
    "excel_document_read",
    "excel_document_write",
)
_OFFICE_WORD_TERMS = ("word", "docx", "word文档", "文字文档")
_OFFICE_EXCEL_TERMS = ("excel", "xlsx", "excel表格", "电子表格", "工作簿")
_OFFICE_READ_TERMS = (
    "读取", "查看", "看看", "打开", "解析", "检查结构", "查看内容",
    "读取段落", "读取单元格", "查看工作表", "读一下", "读下", "读读",
    "读一读", "帮我读", "读这个",
)
_OFFICE_WRITE_TERMS = (
    "生成", "创建", "制作", "保存", "导出", "转成", "转换为",
    "修改", "替换", "追加", "整理成", "编辑",
)
_OFFICE_DOWNLOAD_TERMS = ("给我下载地址", "提供下载", "下载链接", "下载地址")
_OFFICE_NEGATION_TERMS = (
    "不要调用", "别调用", "不用调用", "不调用", "无需调用", "请勿调用",
    "不必调用", "不需要调用", "不要使用", "禁止调用", "无需使用",
    "请勿使用", "不必使用", "不需要使用",
)
_OFFICE_WRITE_NEGATION_TERMS = (
    "不要保存", "别保存", "不用保存", "无需保存", "不必保存", "不需要保存",
    "不要生成", "别生成", "无需生成", "不必生成", "不需要生成",
    "不要创建", "别创建", "无需创建", "不必创建", "不需要创建",
    "不要导出", "别导出", "无需导出", "不必导出", "不需要导出",
    "不要修改", "别修改", "无需修改", "不必修改", "不需要修改",
    "不要写入", "别写入", "无需写入", "不必写入", "不需要写入",
)
_OFFICE_INVOCATION_TERMS = ("调用", "使用", "执行", "运行", "触发")
_OFFICE_EXPLANATION_TERMS = (
    "是什么", "什么工具", "有什么区别", "区别", "支持", "能做什么",
    "功能", "作用", "说明", "介绍", "怎么用",
)

# 用户明确要求进入“你问我答/逐步引导”流程时，不应再等待模型自行判断
# 当前任务是否已经阻塞。该信号只负责提升 ask_user_question，否定表达优先排除。
_EXPLICIT_USER_QUESTION_REQUEST_TERMS = (
    "随便问我",
    "问我几个",
    "问我一些",
    "你问我答",
    "考考我",
    "测测我",
    "考察我",
    "测试我",
    "测试一下我",
    "给我做个小测验",
    "给我做个测验",
    "我不知道怎么提问",
    "不知道怎么提问",
    "不知道怎么问",
    "你引导我",
    "引导我提问",
    "一步一步问我",
    "一步步问我",
    "逐个问我",
    "一个一个问我",
    "请你问我",
    "你来问我",
    "问我吧",
    "向我提问",
    "请向我提问",
    "提问我",
    "对我提问",
    "请开始提问",
    "开始提问",
    "出题考我",
    "问答互动",
    "采访我",
    "访谈我",
    "让我做个小测验",
    "让我做个测验",
    "先问我",
    "通过提问了解我",
    "通过提问了解一下我的",
    "先了解一下我的需求",
    "先收集我的需求",
    "需求访谈",
    "让我回答",
    "askmeafewquestions",
    "askmequestions",
    "quizme",
    "interviewme",
    "youaskianswer",
)

_EXPLICIT_USER_QUESTION_NEGATIONS = (
    "不要问我",
    "不用问我",
    "别问我",
    "不要提问",
    "不用提问",
    "无需提问",
    "不需要提问",
    "直接回答",
    "直接给我答案",
)

_NON_INTERACTIVE_CONTEXT_MARKERS = (
    "【自动化指令",
    "quick_suggestions_forbidden=true",
    "后台自动任务",
    "定时任务",
    "订阅任务",
    "taskcenter自动任务",
)

# 计算相关度时剔除的高频泛化片段（出现在问题里但无区分度）。
_STOP_FRAGMENTS = frozenset({
    "帮我", "帮忙", "一下", "一个", "请问", "可以", "怎么", "如何", "什么", "哪些",
    "有没有", "能否", "现在", "目前", "我想", "我要", "你能", "麻烦", "看下", "看看",
    "the", "and", "for", "with", "this", "that", "what", "how", "please", "help",
})

_TOOL_META_QUERY_TERMS = (
    "支持", "能否", "可以吗", "能不能", "有没有", "有哪些", "什么工具", "工具有哪些",
    "怎么用", "如何用", "怎么查", "如何查询", "需要什么参数", "参数怎么填", "有什么接口", "有没有接口",
)
_TOOL_META_QUESTION_RE = re.compile(r"(?:支持|能|可以|是否).{0,24}(?:吗|么|\?|？)$")
_TOOL_META_OBJECT_TERMS = ("工具", "接口", "能力", "功能", "参数")
_TOOL_EXECUTION_TERMS = (
    "查询", "查", "查看", "搜索", "检索", "获取", "帮我找", "分析",
    "调用", "使用", "执行", "运行", "给我", "请提供", "告诉我",
)
_TOOL_CONCRETE_TARGET_RE = re.compile(
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}:\d{2}|\d{2,}|"
    r"[A-Za-z]{2,}\d+|今天|明天|后天|本周|本月|实时|上海|北京|广州|深圳|杭州)",
    re.IGNORECASE,
)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _looks_like_tool_meta_query(query: str) -> bool:
    """识别仅询问工具能力/用法的元问题，不把真实任务误判成元问题。"""
    normalized = _normalize(query)
    if not normalized or not (
        _contains_any(normalized, _TOOL_META_QUERY_TERMS)
        or _TOOL_META_QUESTION_RE.search(normalized)
    ):
        return False
    if "有没有" in normalized and not _contains_any(normalized, _TOOL_META_OBJECT_TERMS):
        return False
    # “支持查询上海明天天气吗”同时包含能力询问和真实目标，仍应走正常取证链路；
    # 仅有“支持查天气吗”这类没有具体目标的询问才属于元问题。
    if (
        _contains_any(normalized, _TOOL_EXECUTION_TERMS)
        and _TOOL_CONCRETE_TARGET_RE.search(normalized)
    ):
        return False
    return True


def is_tool_meta_query(query: str) -> bool:
    """判断用户是否只在咨询工具能力，而非请求执行具体任务。"""
    return _looks_like_tool_meta_query(query)


def looks_like_explicit_user_question_request(user_query: str) -> bool:
    """识别用户是否明确要求进入交互式提问流程。

    这是确定性工具促发信号，不替代模型对问题内容和选项的生成。
    “列出几个问题”属于内容生成，不等于“问我几个问题”，因此不在正向词表中。
    """
    query = _normalize(user_query)
    if not query or "【用户回答】" in query:
        return False
    if _contains_any(query, _NON_INTERACTIVE_CONTEXT_MARKERS):
        return False
    if _contains_any(query, _EXPLICIT_USER_QUESTION_NEGATIONS):
        return False
    if _contains_any(query, _EXPLICIT_USER_QUESTION_REQUEST_TERMS):
        return True
    return (
        "提问" in query
        and _contains_any(query, ("我", "用户"))
        and _contains_any(query, ("先", "逐个", "一步", "引导", "通过"))
    )


def is_automatic_delivery_context(
    user_info: Optional[Mapping[str, Any]] = None,
    debug_options: Optional[Mapping[str, Any]] = None,
) -> bool:
    """识别无人值守交付上下文，避免主动提问卡等待不存在的用户。"""
    def enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    for context in (user_info, debug_options):
        if not isinstance(context, Mapping):
            continue
        if any(
            enabled(context.get(key))
            for key in (
                "quick_suggestions_forbidden",
                "is_scheduled_task",
                "is_subscription_task",
            )
        ):
            return True
    return False


def _query_signals(query: str) -> Set[str]:
    """从用户问题提取匹配信号：中文字符 bigram + 英文/数字词（去停用片段）。"""
    raw = (query or "").lower()
    signals: Set[str] = set()

    for run in _CJK_RUN.findall(raw):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram in _STOP_FRAGMENTS:
                continue
            signals.add(bigram)

    for token in _ALNUM_TOKEN.findall(raw):
        if token in _STOP_FRAGMENTS:
            continue
        signals.add(token)

    return signals


def _tool_text(tool: Any) -> str:
    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    return _normalize(f"{name} {description}")


def relevance_score(query_signals: Set[str], tool_text: str) -> float:
    """问题信号在工具 name+description 文本中的覆盖比例。"""
    if not query_signals or not tool_text:
        return 0.0
    matched = sum(1 for sig in query_signals if sig in tool_text)
    return matched / len(query_signals)


def should_consider_tool_nudge(user_query: str) -> bool:
    """门禁：过滤问候 / 元操作 / 过短问题。"""
    query = (user_query or "").strip()
    if len(query) < 4:
        return False

    from app.services.ai.intent_service import looks_like_greeting, looks_like_meta_action

    if looks_like_greeting(query) or looks_like_meta_action(query):
        return False
    return True


_MULTI_STEP_SEQUENCE_MARKERS = (
    "首先", "先", "第一步", "然后", "再", "最后", "接着", "随后", "之后", "完成后",
)
_MULTI_STEP_CONNECTORS = ("并", "同时", "以及", "分别")
_MULTI_STEP_OUTPUT_MARKERS = (
    "生成", "导出", "保存", "报告", "文件", "excel", "word", "pdf", "markdown",
)


def _looks_like_multi_step_request(query: str) -> bool:
    """识别带有多个动作的请求，不判断业务意图或具体工具。"""
    normalized = _normalize(query)
    sequence_count = sum(marker in normalized for marker in _MULTI_STEP_SEQUENCE_MARKERS)
    if sequence_count >= 2:
        return True

    clauses = [part for part in re.split(r"[，,；;。.!！？?\n]+", normalized) if part]
    if len(clauses) >= 3 and _contains_any(normalized, _MULTI_STEP_SEQUENCE_MARKERS):
        return True

    return (
        _contains_any(normalized, _MULTI_STEP_CONNECTORS)
        and _contains_any(normalized, _MULTI_STEP_OUTPUT_MARKERS)
    )


# 相关度 ≥ 该阈值时，hard 模式可直接强制调用「该具体工具」；介于 min 与此之间则强制
# 「必须调某工具（required）」由模型自行在已绑定工具中挑选。
STRONG_FORCE_SCORE = 0.5


@dataclass(frozen=True)
class ToolNudge:
    tool_name: str
    score: float
    message: str
    force_first_call: bool = False
    metadata: Optional[ToolMetadata] = None

    def recommended_force_mode(self) -> str:
        """hard 模式下推荐 of ToolChoice.mode：高相关度锁定具体工具，否则 required。"""
        if self.metadata is not None and self.metadata.nudge_mode == "evidence":
            return self.tool_name
        if self.score >= STRONG_FORCE_SCORE:
            return self.tool_name
        return "required"

    @property
    def should_force_first_call(self) -> bool:
        return self.force_first_call


@dataclass(frozen=True)
class EvidenceContract:
    tool_name: str
    required_evidence_types: frozenset[EvidenceType]
    freshness: str = "current_turn"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "required_evidence_types": sorted(
                str(getattr(evidence_type, "value", evidence_type))
                for evidence_type in self.required_evidence_types
            ),
            "freshness": self.freshness,
        }


@dataclass(frozen=True)
class ToolNudgePlan:
    nudges: tuple[ToolNudge, ...]
    evidence_contracts: tuple[EvidenceContract, ...]

    @property
    def primary(self) -> ToolNudge:
        return self.nudges[0]

    @property
    def message(self) -> str:
        ordered_tools = "、".join(contract.tool_name for contract in self.evidence_contracts)
        return (
            f"{self.primary.message}\n"
            f"【多工具取证计划】请按顺序完成以下独立工具调用：{ordered_tools}。"
            "每个工具都必须取得当轮成功结果后，才能在最终回答中使用对应事实；"
            "不要用一个工具的结果替代另一个工具的证据。"
        )


def _resolve_explicit_user_question_nudge(
    query: str,
    tools: List[Any],
) -> Optional[ToolNudge]:
    """为用户明确要求的互动提问生成首步强促发。"""
    if not looks_like_explicit_user_question_request(query):
        return None
    question_tool = next(
        (
            tool
            for tool in (tools or [])
            if str(getattr(tool, "name", "") or "").strip()
            == "ask_user_question"
        ),
        None,
    )
    if question_tool is None:
        return None
    return ToolNudge(
        tool_name="ask_user_question",
        score=1.0,
        message=(
            "【本轮互动提问】用户明确要求互动式提问，用于互动、引导或测验。"
            "必须优先调用 ask_user_question，生成一个具体问题和 2-12 个清晰选项；"
            "本轮调用后立即停止，等待用户回答。不要把问题列表作为普通文字直接输出。"
        ),
        force_first_call=True,
        metadata=resolve_tool_metadata(question_tool),
    )


def _build_office_tool_nudge(
    tool_name: str,
    tool: Any,
    *,
    score: float,
    explicit: bool,
    metadata_by_name: Optional[Mapping[str, ToolMetadata]] = None,
) -> ToolNudge:
    is_write = tool_name.endswith("_write")
    family = "Word" if tool_name.startswith("word_") else "Excel"
    if explicit:
        prefix = f"用户明确指定调用「{tool_name}」"
    elif is_write:
        prefix = f"用户明确提出{family}文件生成、保存、导出或修改请求"
    else:
        prefix = f"用户明确提出{family}文件读取或查看请求"

    if is_write:
        suffix = (
            "写入工具仍需遵循现有权限确认；只有工具成功返回 artifact.download_url 后，"
            "才能声称文件已生成并提供下载地址。"
        )
    else:
        suffix = "必须以工具返回的真实文件内容为准，不要只输出未执行的文字承诺。"
    return ToolNudge(
        tool_name=tool_name,
        score=score,
        message=(
            f"【Office 工具优先】{prefix}。本轮必须优先调用已绑定工具「{tool_name}」；"
            + suffix
        ),
        force_first_call=True,
        metadata=resolve_tool_metadata(tool, metadata_by_name=metadata_by_name),
    )


def _contains_office_type(normalized_query: str, terms: Sequence[str]) -> bool:
    for term in terms:
        if term in {"word", "docx", "excel", "xlsx"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized_query):
                return True
        elif term in normalized_query:
            return True
    return False


def _has_office_reference(normalized_query: str) -> bool:
    return (
        any(name in normalized_query for name in _OFFICE_EXPLICIT_TOOL_NAMES)
        or _contains_office_type(normalized_query, _OFFICE_WORD_TERMS)
        or _contains_office_type(normalized_query, _OFFICE_EXCEL_TERMS)
    )


def _resolve_office_tool_nudge(
    query: str,
    tools: List[Any],
    *,
    metadata_by_name: Optional[Mapping[str, ToolMetadata]] = None,
    explicit_only: bool = False,
) -> Optional[ToolNudge]:
    """按已绑定的四个 Office 工具解析显式工具名和中文读写意图。"""
    normalized = _normalize(query)
    available = {
        str(getattr(tool, "name", "") or "").strip(): tool
        for tool in tools or []
        if str(getattr(tool, "name", "") or "").strip() in _OFFICE_TOOL_NAMES
    }
    if not available:
        return None
    if _contains_any(normalized, _OFFICE_NEGATION_TERMS):
        return None

    mentioned_explicit_names = [
        name
        for name in _OFFICE_EXPLICIT_TOOL_NAMES
        if name in normalized
    ]
    if len(mentioned_explicit_names) > 1:
        return None
    explicit_name = (
        mentioned_explicit_names[0]
        if mentioned_explicit_names and mentioned_explicit_names[0] in available
        else None
    )
    if explicit_name is not None:
        if _contains_any(normalized, _OFFICE_INVOCATION_TERMS):
            return _build_office_tool_nudge(
                explicit_name,
                available[explicit_name],
                score=1.0,
                explicit=True,
                metadata_by_name=metadata_by_name,
            )
        if _contains_any(normalized, _OFFICE_EXPLANATION_TERMS):
            return None
    if explicit_only:
        return None

    has_word_type = _contains_office_type(normalized, _OFFICE_WORD_TERMS)
    has_excel_type = _contains_office_type(normalized, _OFFICE_EXCEL_TERMS)
    if has_word_type == has_excel_type:
        return None

    family = "word" if has_word_type else "excel"
    has_read = any(term in normalized for term in _OFFICE_READ_TERMS)
    has_write = any(term in normalized for term in _OFFICE_WRITE_TERMS)
    has_download = any(term in normalized for term in _OFFICE_DOWNLOAD_TERMS)
    if _contains_any(normalized, _OFFICE_WRITE_NEGATION_TERMS):
        has_write = False

    # 单独索要已有文件下载地址不是 Office 写入请求；需要生成/保存/导出/修改
    # 等动作词时，才允许选择 *_write。
    if has_download and not has_write:
        return None
    # 同时要求读取和写入时，首步顺序不能由 Office 单工具规则猜测；有
    # todo_write 时前面的多步骤规则已经先行接管。
    if has_read and has_write:
        return None
    if not has_read and not has_write:
        return None

    operation = "write" if has_write else "read"
    tool_name = f"{family}_document_{operation}"
    tool = available.get(tool_name)
    if tool is None:
        return None
    return _build_office_tool_nudge(
        tool_name,
        tool,
        score=1.0,
        explicit=False,
        metadata_by_name=metadata_by_name,
    )


def _attach_tool_metadata(
    nudge: Optional[ToolNudge],
    tools: List[Any],
    metadata_by_name: Optional[Mapping[str, ToolMetadata]],
) -> Optional[ToolNudge]:
    """Attach descriptive metadata without changing the nudge decision."""
    if nudge is None or nudge.metadata is not None:
        return nudge
    tool = next(
        (
            item
            for item in tools or []
            if str(getattr(item, "name", item) or "").strip() == nudge.tool_name
        ),
        nudge.tool_name,
    )
    return replace(
        nudge,
        metadata=resolve_tool_metadata(tool, metadata_by_name=metadata_by_name),
    )


def _short_capability(tool: Any) -> str:
    description = str(getattr(tool, "description", "") or "").strip()
    if not description:
        return ""
    # 取首句作为能力摘要，避免把整段 docstring 灌进 prompt。
    first = re.split(r"[。\n]", description, maxsplit=1)[0].strip()
    return first[:80]


def _build_message(tool_name: str, capability: str) -> str:
    cap = f"（{capability}）" if capability else ""
    return (
        f"【本轮工具优先】已绑定工具「{tool_name}」{cap}很可能可直接获取用户需要的真实结果。"
        f"请先调用它（或其他更合适的已绑定工具）拿到结果再回答；"
        f"在获得工具返回之前，不要凭记忆或常识直接给出具体数值、路径、状态或结论；"
        f"若工具返回为空或失败，如实说明，不要编造。"
    )


_PLATFORM_DOC_TOOL_ALIASES = (
    ("Grep", "search_text"),
    ("Glob", "glob_files"),
    ("Read", "read_file"),
)


def _find_platform_doc_tool(tools: List[Any]) -> Any:
    available = {
        str(getattr(tool, "name", "") or "").strip(): tool
        for tool in (tools or [])
    }
    for names in _PLATFORM_DOC_TOOL_ALIASES:
        for name in names:
            if name in available:
                return available[name]
    return None


def _resolve_platform_docs_nudge(tools: List[Any]) -> Optional[ToolNudge]:
    tool = _find_platform_doc_tool(tools)
    if tool is None:
        return None

    tool_name = str(getattr(tool, "name", "") or "").strip()
    return ToolNudge(
        tool_name=tool_name,
        score=0.95,
        message=(
            "【平台公共文档优先】本轮问题涉及智能体平台自身的功能、配置或开关说明。"
            "请优先通过宿主侧文件工具检索公共 docs/*.md（先用 Grep/Glob 定位，"
            "再用 Read 读取命中文档）后回答；Docker 沙箱 Bash 不挂载公共 docs，"
            "禁止通过 Bash 访问或臆造 /workspace/docs、/app/data/docs 路径。"
            "公共 docs 没有命中时，再按目录清单使用宿主工具读取服务根目录一级 /app/*.md（本地开发为项目根 *.md）帮助文档；"
            "禁止递归扫描 /app 或改为调用 sub_agent_call 搜索企业知识库。"
        ),
        force_first_call=True,
        metadata=resolve_tool_metadata(tool),
    )


_NOTIFICATION_ACTION_TERMS = (
    "发送", "推送", "通知", "发到", "发给", "发一下", "send", "push", "notify",
)

_NOTIFICATION_CHANNELS = (
    (
        "send_portal_notification",
        ("站内", "站内信", "站内消息", "铃铛", "inbox", "门户消息", "消息中心"),
        "站内消息（门户铃铛）",
    ),
    (
        "send_dingtalk_message",
        ("钉钉", "dingtalk"),
        "钉钉群机器人",
    ),
    (
        "send_wechat_work_message",
        ("企微", "企业微信", "wechat work", "wecom"),
        "企业微信群机器人",
    ),
    (
        "send_email",
        ("邮件", "邮箱", "email", "mail"),
        "邮件",
    ),
)


_EXPLICIT_SUB_AGENT_ACTION_TERMS = (
    "调用", "委派", "委派给", "交给", "让", "使用", "用",
    "call", "delegate", "ask",
)

_BATCH_DELEGATION_TERMS = (
    "并行", "同时", "分别", "批量", "一起", "concurrent", "batch", "parallel",
)

_SELF_AGENT_NAMES = frozenset({
    "main",
    "assistant",
    "主助手",
    "主智能体",
    "通用助手",
    "通用智能体",
})


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term and term in text for term in terms)


def _sub_agent_aliases(name: str) -> Set[str]:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return set()
    return {
        normalized,
        normalized.replace("_", "-"),
        normalized.replace("-", "_"),
    }


def _contains_sub_agent_alias(query: str, alias: str) -> bool:
    if not alias:
        return False
    if re.search(r"[a-z0-9_-]", alias):
        pattern = rf"(?<![a-z0-9_-]){re.escape(alias)}(?![a-z0-9_-])"
        return re.search(pattern, query) is not None
    return alias in query


def _resolve_explicit_sub_agent_targets(
    query: str,
    available_sub_agent_names: Optional[Set[str]],
) -> List[str]:
    """用户点名一个或多个可用子代理时，按其在 query 中的出现顺序返回规范名称列表。"""
    if not available_sub_agent_names:
        return []

    normalized_query = (query or "").strip().lower()
    if not _contains_any(normalized_query, _EXPLICIT_SUB_AGENT_ACTION_TERMS):
        return []

    query_variants = {
        normalized_query,
        normalized_query.replace("_", "-"),
        normalized_query.replace("-", "_"),
    }

    matched_targets: List[tuple[int, str]] = []
    seen: Set[str] = set()

    for candidate in sorted(available_sub_agent_names, key=lambda item: len(str(item)), reverse=True):
        canonical = str(candidate or "").strip()
        if not canonical or canonical in seen:
            continue
        if canonical.lower() in _SELF_AGENT_NAMES:
            continue
        aliases = _sub_agent_aliases(canonical)

        min_pos = -1
        for query_variant in query_variants:
            for alias in aliases:
                if _contains_sub_agent_alias(query_variant, alias):
                    pos = query_variant.find(alias)
                    if pos != -1 and (min_pos == -1 or pos < min_pos):
                        min_pos = pos
        if min_pos != -1:
            matched_targets.append((min_pos, canonical))
            seen.add(canonical)

    matched_targets.sort(key=lambda x: x[0])
    return [target for _, target in matched_targets]


def _resolve_explicit_sub_agent_target(
    query: str,
    available_sub_agent_names: Optional[Set[str]],
) -> Optional[str]:
    """用户点名某个可用子代理时，返回其规范名称（兼容单目标）。"""
    targets = _resolve_explicit_sub_agent_targets(query, available_sub_agent_names)
    return targets[0] if targets else None


def _build_explicit_sub_agent_message(target_agent_name: str) -> str:
    return (
        f"【本轮工具优先】用户明确要求调用子代理 '{target_agent_name}'。"
        f"你必须优先调用 sub_agent_call(agent_name='{target_agent_name}', query='用户的问题') "
        f"委派给该子代理处理，拿到结果后再回答；"
        f"不要改派给其他子代理，也不要在未调用工具前自行完成该任务；"
        f"若工具返回为空或失败，如实说明。"
    )


def _build_explicit_sub_agent_batch_message(targets: Sequence[str]) -> str:
    target_names = "、".join(f"'{name}'" for name in targets)
    calls_example = ", ".join(f'{{"agent_name": "{name}", "query": "..."}}' for name in targets) if targets else '{"agent_name": "...", "query": "..."}'
    return (
        f"【本轮工具优先】用户明确要求并行/批量调用子智能体 {target_names}。"
        f"你必须优先调用 sub_agent_batch_call(calls=[{calls_example}]) "
        f"并行委派给这些子智能体处理，按顺序收集结果后再回答；"
        f"不要串行逐个调用，也不要在未调用工具前自行完成该任务；"
        f"若部分或全部子智能体返回为空或失败，如实说明。"
    )


def _build_todo_write_message() -> str:
    return (
        "【本轮工具优先】用户请求包含多个连续步骤。"
        "请先调用 todo_write 写入完整、可执行的任务清单，再继续调用后续工具或子代理完成清单中的步骤；"
        "todo_write 返回后必须继续执行，不要只输出计划，也不要把任务清单当作最终答案。"
    )


def _normalize_capability_candidates(
    raw: Any,
) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        name = raw.strip()
        return [name] if name else []
    if isinstance(raw, Sequence):
        names: List[str] = []
        for item in raw:
            name = str(item or "").strip()
            if name and name not in names:
                names.append(name)
        return names
    name = str(raw or "").strip()
    return [name] if name else []


def build_semantic_sub_agent_nudge_message(
    *,
    capability: str,
    candidates: Sequence[str],
    intent_label: str,
) -> str:
    candidate_hint = "、".join(f"`{name}`" for name in candidates)
    return (
        f"【本轮工具优先】本轮用户请求涉及{intent_label}。"
        "主助手没有直接完成该任务的专用能力，必须先调用 sub_agent_call。"
        f"agent_name 必须从可委派子智能体清单中、具备 `{capability}` 能力的候选里按语义选择"
        "（对照 name / 中文名 / Description / Capabilities，与自动路由一致）；"
        f"当前候选包括：{candidate_hint}。"
        "严禁编造结果；若工具返回为空或失败，如实说明，不要编造。"
    )


def _build_semantic_sub_agent_message(
    *,
    capability: str,
    candidates: Sequence[str],
    intent_label: str,
) -> str:
    return build_semantic_sub_agent_nudge_message(
        capability=capability,
        candidates=candidates,
        intent_label=intent_label,
    )


def _resolve_resource_catalog_nudge(query: str, tools: List[Any]) -> Optional[ToolNudge]:
    """权限内数据集/知识库目录清单：优先促发 list 工具，禁止误推 search/sub_agent。"""
    from app.services.ai.intent_service import looks_like_accessible_resource_catalog_query

    if not looks_like_accessible_resource_catalog_query(query):
        return None

    available = {
        str(getattr(tool, "name", "") or ""): tool
        for tool in (tools or [])
        if getattr(tool, "name", None)
    }
    q = (query or "").lower()
    prefers_kb = any(token in q for token in ("知识库", "knowledge"))
    prefers_ds = any(token in q for token in ("数据集", "dataset"))

    if prefers_kb and "list_accessible_knowledge_bases" in available:
        tool_name = "list_accessible_knowledge_bases"
        label = "当前用户有权限的知识库目录"
    elif prefers_ds and "list_accessible_datasets" in available:
        tool_name = "list_accessible_datasets"
        label = "当前用户有权限的数据集目录"
    elif "list_accessible_knowledge_bases" in available and not prefers_ds:
        tool_name = "list_accessible_knowledge_bases"
        label = "当前用户有权限的知识库目录"
    elif "list_accessible_datasets" in available:
        tool_name = "list_accessible_datasets"
        label = "当前用户有权限的数据集目录"
    else:
        return None

    return ToolNudge(
        tool_name=tool_name,
        score=0.98,
        message=(
            f"【本轮工具优先】用户在询问{label}。"
            f"必须先调用 {tool_name} 获取权限内轻量目录（id/名称/备注），"
            "严禁调用 search_knowledge_base 做正文检索，也严禁编造权限清单。"
            "若工具返回为空，如实说明当前无可访问资源。"
        ),
        force_first_call=True,
    )


def _resolve_current_user_profile_nudge(query: str, tools: List[Any]) -> Optional[ToolNudge]:
    """优先读取当前用户资料，避免被 DATA_QUERY 委派规则抢先锁定。"""
    from app.services.ai.intent_service import looks_like_current_user_profile_query

    if not looks_like_current_user_profile_query(query):
        return None

    available_tool_names = {
        str(getattr(tool, "name", "") or "")
        for tool in (tools or [])
        if getattr(tool, "name", None)
    }
    if "get_myinfo" not in available_tool_names:
        return None

    return ToolNudge(
        tool_name="get_myinfo",
        score=0.99,
        message=(
            "【本轮工具优先】用户正在查询当前登录用户本人的资料。"
            "必须先调用 get_myinfo 获取本人基本信息、扩展信息、角色和权限；"
            "禁止调用 sub_agent_call 查询其他用户或业务数据。"
        ),
        force_first_call=True,
    )


def _resolve_notification_nudge(query: str, tools: List[Any]) -> Optional[ToolNudge]:
    normalized_query = query.lower()
    if not _contains_any(normalized_query, _NOTIFICATION_ACTION_TERMS):
        return None

    available_tool_names = {
        str(getattr(tool, "name", "") or "")
        for tool in (tools or [])
        if getattr(tool, "name", None)
    }
    for tool_name, channel_terms, channel_label in _NOTIFICATION_CHANNELS:
        if tool_name not in available_tool_names:
            continue
        if not _contains_any(normalized_query, channel_terms):
            continue
        return ToolNudge(
            tool_name=tool_name,
            score=1.0,
            message=(
                f"【本轮工具优先】用户明确要求发送或推送到{channel_label}。"
                f"请调用已绑定工具「{tool_name}」完成发送；"
                + (
                    "该工具会写入当前用户的门户站内信箱（铃铛），无需 Webhook 配置。"
                    if tool_name == "send_portal_notification"
                    else (
                        "该工具会自动读取当前用户在个人中心 -> 消息通知里的配置，"
                        "无需用户在本轮提供 webhook、token 或服务器配置。"
                    )
                )
                + "只有工具返回失败时，才向用户说明失败原因；不要在未调用工具前声称未配置或已发送。"
            ),
        )
    return None


def resolve_tool_nudge(
    user_query: str,
    tools: List[Any],
    *,
    min_score: float = 0.25,
    exclude_tools: Optional[Set[str]] = None,
    available_sub_agent_names: Optional[Set[str]] = None,
    sub_agent_candidates_by_capability: Optional[Mapping[str, Any]] = None,
    sub_agent_targets_by_capability: Optional[Mapping[str, Any]] = None,
    semantic_intent: Any = None,
    semantic_confidence: Any = None,
    turn_intent: Any = None,
    request_decision: Optional[RequestDecision] = None,
    turn_decision: Optional[TurnDecision] = None,
    tool_metadata: Optional[Mapping[str, ToolMetadata]] = None,
    allow_explicit_question: bool = True,
) -> Optional[ToolNudge]:
    """解析本轮是否需要工具促发；返回相关度最高的一条便签或 None。

    相关度完全由 ``tools`` 的 name + description 与问题的字符重叠决定，
    不依赖任何写死的工具名或类别。

    强 ChatBI/强知识库委派：强制调用 sub_agent_call，但 agent_name 由模型按
    通讯录语义选择（与自动路由对齐），不再按 sort_order 点名唯一目标。
    ``sub_agent_targets_by_capability`` 仅为兼容旧调用方，等价于单元素候选。
    """
    query = (user_query or "").strip()
    if not query:
        return None

    if allow_explicit_question:
        explicit_question_nudge = _resolve_explicit_user_question_nudge(query, tools)
        if explicit_question_nudge is not None:
            return explicit_question_nudge

    # 明确写出 Office 原始工具名时，不能被通用元操作门禁拦截；仍只接受
    # 当前运行时实际已绑定的目标工具。
    explicit_office_nudge = _resolve_office_tool_nudge(
        query,
        tools,
        metadata_by_name=tool_metadata,
        explicit_only=True,
    )
    if explicit_office_nudge is not None:
        return explicit_office_nudge

    if _looks_like_tool_meta_query(query):
        return None

    if not should_consider_tool_nudge(query):
        return None

    if request_decision is None and turn_decision is not None:
        request_decision = turn_decision.to_request_decision()
    if request_decision is None:
        request_decision = resolve_request_decision(
            query,
            semantic_intent=semantic_intent,
            semantic_confidence=semantic_confidence,
            turn_intent=turn_intent,
            semantic_intent_blocks_followup=True,
        )

    capability_candidates = dict(sub_agent_candidates_by_capability or {})
    if sub_agent_targets_by_capability:
        for capability, raw in sub_agent_targets_by_capability.items():
            capability_candidates.setdefault(str(capability), raw)

    # 特殊规则：对于显式点名子代理或多子代理批量委派
    sub_agent_tool = next((t for t in (tools or []) if getattr(t, "name", "") == "sub_agent_call"), None)
    batch_sub_agent_tool = next((t for t in (tools or []) if getattr(t, "name", "") == "sub_agent_batch_call"), None)
    if sub_agent_tool or batch_sub_agent_tool:
        explicit_sub_agents = _resolve_explicit_sub_agent_targets(query, available_sub_agent_names)
        has_batch_terms = _contains_any((query or "").strip().lower(), _BATCH_DELEGATION_TERMS)
        if (len(explicit_sub_agents) >= 2 or (len(explicit_sub_agents) >= 1 and has_batch_terms)) and batch_sub_agent_tool:
            return ToolNudge(
                tool_name="sub_agent_batch_call",
                score=1.0,
                message=_build_explicit_sub_agent_batch_message(explicit_sub_agents),
                force_first_call=True,
                metadata=resolve_tool_metadata(
                    batch_sub_agent_tool,
                    metadata_by_name=tool_metadata,
                ),
            )
        elif explicit_sub_agents and sub_agent_tool:
            return ToolNudge(
                tool_name="sub_agent_call",
                score=1.0,
                message=_build_explicit_sub_agent_message(explicit_sub_agents[0]),
                force_first_call=True,
                metadata=resolve_tool_metadata(
                    sub_agent_tool,
                    metadata_by_name=tool_metadata,
                ),
            )

    todo_tool = next((t for t in (tools or []) if getattr(t, "name", "") == "todo_write"), None)
    if todo_tool is not None and _looks_like_multi_step_request(query):
        return ToolNudge(
            tool_name="todo_write",
            score=1.0,
            message=_build_todo_write_message(),
            force_first_call=True,
            metadata=resolve_tool_metadata(
                todo_tool,
                metadata_by_name=tool_metadata,
            ),
        )

    current_user_profile_nudge = _resolve_current_user_profile_nudge(query, tools)
    if current_user_profile_nudge is not None:
        return _attach_tool_metadata(current_user_profile_nudge, tools, tool_metadata)

    from app.services.ai.intent_service import looks_like_current_model_query

    if (
        request_decision.source == RequestSource.PLATFORM_SELF_HELP
        and not looks_like_current_model_query(query)
    ):
        platform_docs_nudge = _resolve_platform_docs_nudge(tools)
        if platform_docs_nudge is not None:
            return _attach_tool_metadata(platform_docs_nudge, tools, tool_metadata)

    # 目录未给出高置信匹配时，允许主助手做一次低成本直接检索兜底；
    # 该路径必须先于知识库 sub_agent nudge，避免候选信号升级为委派。
    if request_decision.knowledge_fallback_allowed:
        direct_knowledge_tool = next(
            (
                tool
                for tool in (tools or [])
                if str(getattr(tool, "name", "") or "").strip()
                == "search_knowledge_base"
            ),
            None,
        )
        if direct_knowledge_tool is not None:
            return ToolNudge(
                tool_name="search_knowledge_base",
                score=0.9,
                message=(
                    "【知识库低置信兜底】授权目录没有确认本问题对应的知识库，"
                    "本轮只允许直接调用一次 search_knowledge_base 试探；"
                    "不要调用 sub_agent_call，也不要在本次结果后重复检索。"
                ),
                force_first_call=True,
                metadata=resolve_tool_metadata(
                    direct_knowledge_tool,
                    metadata_by_name=tool_metadata,
                ),
            )

    # data_query is a ChatBI capability in this platform, not a generic
    # "anything that looks like data" capability.  Only allow it when the
    # canonical source decision explicitly permits the ChatBI route.  In
    # particular, runtime diagnostics must never be sent to a ChatBI agent
    # just because a lower-level turn classifier said DATA_QUERY.
    chatbi_route_allowed = (
        request_decision.should_delegate
        and request_decision.delegate_capability == "data_query"
        and request_decision.allows_data_route
        and request_decision.chatbi_mode in {None, ChatBIMode.DIRECT.value}
        and request_decision.source in {
            RequestSource.INTERNAL_STRUCTURED_DATA,
            RequestSource.CONVERSATION_CONTEXT,
        }
    )

    if sub_agent_tool and request_decision.source != RequestSource.PLATFORM_SELF_HELP:
        def _sub_agent_available(name: str) -> bool:
            if available_sub_agent_names is None:
                return True
            aliases = {name, name.replace("_", "-"), name.replace("-", "_")}
            return bool(aliases & available_sub_agent_names)

        def _available_candidates_for(capability: str) -> List[str]:
            raw = capability_candidates.get(capability)
            return [
                name
                for name in _normalize_capability_candidates(raw)
                if _sub_agent_available(name)
            ]

        # 优先判断更具体的知识库检索意图
        if (
            request_decision.should_delegate
            and request_decision.delegate_capability == "knowledge_base"
        ):
            candidates = _available_candidates_for("knowledge_base")
            if not candidates:
                return None
            return ToolNudge(
                tool_name="sub_agent_call",
                score=0.95,
                message=_build_semantic_sub_agent_message(
                    capability="knowledge_base",
                    candidates=candidates,
                    intent_label="内部制度、SOP或操作规程查询",
                ),
                force_first_call=True,
                metadata=resolve_tool_metadata(
                    sub_agent_tool,
                    metadata_by_name=tool_metadata,
                ),
            )
        elif chatbi_route_allowed:
            candidates = _available_candidates_for("data_query")
            if not candidates:
                return None
            return ToolNudge(
                tool_name="sub_agent_call",
                score=0.95,
                message=_build_semantic_sub_agent_message(
                    capability="data_query",
                    candidates=candidates,
                    intent_label="内部数据、指标或资产查询",
                ),
                force_first_call=True,
                metadata=resolve_tool_metadata(
                    sub_agent_tool,
                    metadata_by_name=tool_metadata,
                ),
            )

    notification_nudge = _resolve_notification_nudge(query, tools)
    if notification_nudge is not None:
        return _attach_tool_metadata(notification_nudge, tools, tool_metadata)

    catalog_nudge = _resolve_resource_catalog_nudge(query, tools)
    if catalog_nudge is not None:
        return _attach_tool_metadata(catalog_nudge, tools, tool_metadata)

    office_nudge = _resolve_office_tool_nudge(
        query,
        tools,
        metadata_by_name=tool_metadata,
    )
    if office_nudge is not None:
        return office_nudge

    office_reference_in_query = _has_office_reference(_normalize(query))
    signals = _query_signals(query)
    if len(signals) < 2:
        return None

    excluded = set(_NUDGE_EXCLUDED_TOOLS)
    excluded.add("sub_agent_call")
    excluded.add("sub_agent_batch_call")
    if exclude_tools:
        excluded |= {str(name) for name in exclude_tools}

    best_tool: Any = None
    best_score = 0.0
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "")
        if not name or name in excluded:
            continue
        if office_reference_in_query and name in _OFFICE_TOOL_NAMES:
            continue
        score = relevance_score(signals, _tool_text(tool))
        if score > best_score:
            best_score = score
            best_tool = tool

    effective_min_score = (
        0.2
        if request_decision.capability == RequestCapability.WEB_SEARCH
        or request_decision.source == RequestSource.PUBLIC_WEB
        else min_score
    )
    if best_tool is None or best_score < effective_min_score:
        return None

    tool_name = str(getattr(best_tool, "name", "") or "")
    metadata = resolve_tool_metadata(
        best_tool,
        metadata_by_name=tool_metadata,
    )
    evidence_types = frozenset(getattr(best_tool, "evidence_types", None) or ())
    return ToolNudge(
        tool_name=tool_name,
        score=round(best_score, 3),
        message=_build_message(tool_name, _short_capability(best_tool)),
        force_first_call=bool(
            evidence_types and metadata.nudge_mode == "evidence"
        ),
        metadata=metadata,
    )


def resolve_evidence_tool_fallback_nudge(
    user_query: str,
    tools: List[Any],
    *,
    min_score: float = 0.35,
) -> Optional[ToolNudge]:
    """在常规预检异常时，为证据型只读工具提供 fail-closed 兜底。

    该路径只看运行时显式声明为 ``read`` 且带证据类型的工具，避免预检自身
    出错时又依赖同一条可能失败的复杂路由分支。它不处理写工具、委派工具或
    记忆复用工具；命中后始终锁定具体工具，不能退化为任意 ``required``。
    """
    query = (user_query or "").strip()
    if _looks_like_tool_meta_query(query) or not should_consider_tool_nudge(query):
        return None
    signals = _query_signals(query)
    if len(signals) < 2:
        return None

    candidates: list[tuple[float, Any, ToolMetadata]] = []
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "").strip()
        permission_scope = str(getattr(tool, "permission_scope", "") or "").strip().lower()
        evidence_types = frozenset(getattr(tool, "evidence_types", None) or ())
        if (
            not name
            or name in _NUDGE_EXCLUDED_TOOLS
            or name in {"sub_agent_call", "sub_agent_batch_call"}
            or permission_scope != "read"
            or not evidence_types
        ):
            continue
        try:
            metadata = resolve_tool_metadata(tool)
        except Exception as exc:
            logger.warning(
                "[ToolPreflight] Metadata resolution failed for tool=%s; "
                "using runtime safety metadata: %s",
                name,
                type(exc).__name__,
            )
            # 即使元数据解析器本身就是故障源，显式的 runtime read+evidence
            # 声明仍足以建立最小安全合同。
            source_type = str(getattr(tool, "source_type", "") or "").strip()
            metadata = ToolMetadata(
                capability=(
                    "external_tool"
                    if source_type in {"mcp", "generic_api"}
                    else "unknown"
                ),
                source=source_type or "unknown",
                freshness="dynamic",
                side_effect="read",
                confirmation="none",
                idempotent="yes",
                nudge_mode="evidence",
            )
        if metadata.nudge_mode != "evidence":
            continue
        score = relevance_score(signals, _tool_text(tool))
        if score <= 0:
            continue
        candidates.append((score, tool, metadata))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return None
    best_score, best_tool, best_metadata = candidates[0]
    if best_score < min_score:
        return None
    if len(candidates) > 1 and best_score - candidates[1][0] < 0.10:
        return None
    tool_name = str(getattr(best_tool, "name", "") or "").strip()
    return ToolNudge(
        tool_name=tool_name,
        score=round(best_score, 3),
        message=_build_message(tool_name, _short_capability(best_tool)),
        force_first_call=True,
        metadata=best_metadata,
    )


def _split_evidence_intent_clauses(query: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(r"(?:并且|同时|以及|另外|顺便|并)", _normalize(query))
        if clause.strip()
    ]


def resolve_tool_nudge_plan(
    user_query: str,
    tools: List[Any],
    *,
    min_score: float = 0.35,
    min_gap: float = 0.10,
) -> Optional[ToolNudgePlan]:
    """仅为明确的多意图只读查询生成独立证据合同计划。"""
    query = (user_query or "").strip()
    if (
        _looks_like_tool_meta_query(query)
        or not should_consider_tool_nudge(query)
    ):
        return None
    if (
        any(str(getattr(tool, "name", "") or "") == "todo_write" for tool in tools or [])
        and _looks_like_multi_step_request(query)
    ):
        return None
    clauses = _split_evidence_intent_clauses(query)
    if len(clauses) < 2:
        return None

    resolved: list[tuple[str, Any, float, ToolMetadata, frozenset[EvidenceType]]] = []
    for clause in clauses:
        signals = _query_signals(clause)
        if len(signals) < 2:
            return None
        candidates: list[tuple[float, Any, ToolMetadata, frozenset[EvidenceType]]] = []
        for tool in tools or []:
            name = str(getattr(tool, "name", "") or "").strip()
            permission_scope = str(
                getattr(tool, "permission_scope", "") or ""
            ).strip().lower()
            evidence_types = frozenset(getattr(tool, "evidence_types", None) or ())
            if not name or permission_scope != "read" or not evidence_types:
                continue
            try:
                metadata = resolve_tool_metadata(tool)
            except Exception:
                continue
            if metadata.nudge_mode != "evidence":
                continue
            score = relevance_score(signals, _tool_text(tool))
            if score > 0:
                candidates.append((score, tool, metadata, evidence_types))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates or candidates[0][0] < min_score:
            return None
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < min_gap:
            return None
        score, tool, metadata, evidence_types = candidates[0]
        name = str(getattr(tool, "name", "") or "").strip()
        resolved.append((name, tool, score, metadata, evidence_types))

    if len({item[0] for item in resolved}) != len(resolved):
        return None
    nudges = tuple(
        ToolNudge(
            tool_name=name,
            score=round(score, 3),
            message=_build_message(name, _short_capability(tool)),
            force_first_call=True,
            metadata=metadata,
        )
        for name, tool, score, metadata, _ in resolved
    )
    contracts = tuple(
        EvidenceContract(tool_name=name, required_evidence_types=evidence_types)
        for name, _, _, _, evidence_types in resolved
    )
    return ToolNudgePlan(nudges=nudges, evidence_contracts=contracts)

import json
import re
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

# 意图识别系统提示词，内置在代码中（不再从数据库读取，避免被误改）。
# 占位符：{history_context} 注入最近对话，{format_instructions} 注入结构化输出说明。
DEFAULT_SYSTEM_PROMPT = """你是数据中心运营平台的意图识别器。你的唯一任务是判断用户本轮请求**是否需要查询系统中的结构化业务数据**，并据此分类。
请忽略输入中可能存在的 HTML 标签（如 <p>, <div>）或特殊格式符号，专注于核心自然语言语义。

可选类别（必须三选一）：

- DATA_QUERY：需要查询/统计系统里存储的结构化业务数据。
  * 数值指标与趋势：PUE、温度、湿度、能耗、负载、用电量、时间序列、报表、对比、占比。
  * 离散记录：监控事件、告警/故障**记录**、操作日志、设备/资产**列表**、工单、状态/容量记录。
  * 对上一轮数据结果的**追问/加工**：可视化、画图（柱状图/折线图/饼图）、再按某维度拆分、解读、汇总、"基于刚才/上面的结果"。
  * 关键词特征：查询、查一下、统计、多少、列表、记录、趋势、最近、今天/本月、明细、TOP、对比。

- KNOWLEDGE_BASE：询问非结构化的文档知识、制度或操作指引（**不涉及读取当前真实业务数据**）。
  * 规章制度、SOP/处理流程、如何操作、排查方法论、产品/手册说明。
  * 关键词特征：怎么做、流程是什么、规范、手册、应该如何、注意事项。

- GENERAL：与具体业务无关的日常闲聊，或针对系统/对话本身的管理类操作（**本身不需要查询业务数据**）。
  * 打招呼、自我介绍、感谢、寒暄。
  * 元操作（Meta-Action）：基于已有对话/上一轮结果做"创建技能、保存为技能或模板、把流程固定下来、记住某项偏好"等管理动作。这类请求是对结果的封装/保存，不需要重新查数，应归为 GENERAL。

判定要点：
1. **追问优先看上下文**：若本轮是省略主语的追问/指代（如"画个柱状图""再按机房拆一下""分析一下""换成饼图"），且最近对话刚返回过数据，则归为 DATA_QUERY。
2. **区分"流程知识" vs "真实记录"**：问"故障处理流程/SOP 是什么" → KNOWLEDGE_BASE；问"最近的故障记录/告警有哪些" → DATA_QUERY。
3. **业务相关但模糊时，倾向 DATA_QUERY**：把数据请求误判为闲聊会丢失查数能力并可能编造答案；把边缘闲聊判成数据查询代价较小。
4. 带明确"查询/查看/统计/列出"等业务动词时，不得归类为 GENERAL。

示例：
用户：上海机房上个月的 PUE 趋势
输出：{"intent": "DATA_QUERY", "confidence": 0.97, "reasoning": "请求时间序列指标数据", "entities": ["上海机房", "PUE", "上个月"]}

用户：最近有哪些告警记录
输出：{"intent": "DATA_QUERY", "confidence": 0.95, "reasoning": "查询离散告警记录", "entities": ["告警记录"]}

用户：把刚才的结果画成柱状图
输出：{"intent": "DATA_QUERY", "confidence": 0.93, "reasoning": "对上一轮数据结果的可视化追问", "entities": ["柱状图"]}

用户：机房高温告警的标准处理流程是什么
输出：{"intent": "KNOWLEDGE_BASE", "confidence": 0.9, "reasoning": "询问 SOP 处理流程，非真实记录", "entities": ["高温告警", "处理流程"]}

用户：你好，你是谁
输出：{"intent": "GENERAL", "confidence": 0.98, "reasoning": "日常打招呼", "entities": []}

{history_context}

{format_instructions}

必须严格返回 JSON 格式，且只包含 JSON 内容。"""


# 追问/指代类关键词（用于 dispatcher 的廉价短路与意图器的上下文判断）。
_FOLLOWUP_KEYWORDS = [
    "可视化", "图表", "画图", "画个图", "柱状图", "折线图", "饼图", "分析一下", "总结一下",
    "解读一下", "基于上", "基于刚才", "根据上", "根据刚才", "上面的", "刚才的", "这个结果",
    "这些数据", "上一轮", "前面的", "按这个结果", "对这些",
    "格式", "规范", "渲染", "排版", "不符合", "不符", "重新输出", "重新展示", "重新生成", "重新排版", "重新渲染", "重新格式化",
    "visual", "chart", "plot", "graph", "analyze", "summarize", "format", "render",
]
_NEW_QUERY_KEYWORDS = [
    "重新查", "再查", "查询", "查一下", "查下", "统计", "列出", "列表", "筛选", "过滤", "最近",
    "今天", "昨天", "本周", "上周", "本月", "上月", "新增条件", "换成条件",
    "获取", "拉取", "看看", "展示", "显示", "查",
    "select ", "where ", "group by",
]
_COMPOUND_NEW_QUERY_KEYWORDS = [
    keyword for keyword in _NEW_QUERY_KEYWORDS
    if keyword not in {"看看", "展示", "显示"}
]
# 同句「查数 + 可视化/分析」时的可视化侧信号（与 _FOLLOWUP_KEYWORDS 部分重叠，用于识别复合新数据查询）。
_COMPOUND_VIZ_KEYWORDS = [
    "可视化", "图表", "画图", "画个图", "柱状图", "折线图", "饼图", "分析",
    "visual", "chart", "plot", "graph", "analyze",
]
_RESULT_FORMATTING_PATTERNS = [
    re.compile(r"(日期|时间|创建|更新时间|创建时间).{0,12}(yyyy|mm-dd|yyyy-mm-dd|格式|显示)", re.I),
    re.compile(r"(yyyy|mm-dd|yyyy-mm-dd).{0,12}(显示|格式|输出)", re.I),
    re.compile(r"(按|改成|转成|显示为).{0,12}(格式|yyyy|mm-dd|百分比|小数|千分位)", re.I),
    re.compile(r"(保留|四舍五入).{0,8}(小数|位)", re.I),
]


def looks_like_result_formatting_followup(user_question: str) -> bool:
    """仅调整上一轮结果展示格式，不应触发重新查数。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_context_action(user_question):
        return False
    return any(pattern.search(q) for pattern in _RESULT_FORMATTING_PATTERNS)


def looks_like_compound_query_with_viz(user_question: str) -> bool:
    """同一句里既要查数又要可视化/分析 → 新数据查询，不是复用上一轮结果。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    has_viz = any(keyword in q for keyword in _COMPOUND_VIZ_KEYWORDS)
    has_query = any(keyword in q for keyword in _COMPOUND_NEW_QUERY_KEYWORDS)
    return has_viz and has_query


def looks_like_pure_result_followup(user_question: str) -> bool:
    """仅对已有结果做可视化/分析/总结，同句不含新的查数诉求。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_context_action(user_question):
        return False
    if looks_like_result_formatting_followup(user_question):
        return True
    if looks_like_compound_query_with_viz(user_question):
        return False
    if not any(keyword in q for keyword in _FOLLOWUP_KEYWORDS):
        return False
    return not any(keyword in q for keyword in _COMPOUND_NEW_QUERY_KEYWORDS)


def looks_like_data_followup(user_question: str) -> bool:
    """轻量启发式：判断本轮是否为对上一轮数据结果的纯加工追问。

    与 DataQueryExecutor 保持一致，供 dispatcher 在确实存在可复用数据结果时
    做"跳过意图识别 LLM"的短路。同句含查数+可视化的复合请求不算复用上一轮结果。
    """
    return looks_like_pure_result_followup(user_question)


# 知识库/SOP 类问法（与 DATA_QUERY 的「查记录/统计」区分）
_KNOWLEDGE_SIGNALS = [
    "流程是什么", "怎么处理", "如何处理", "怎么做", "如何做", "怎样处理",
    "规范", "手册", "sop", "注意事项", "制度", "指引", "文档说明",
    "操作步骤", "处理流程", "标准流程", "预案", "是什么意思",
]
_DATA_QUERY_SIGNALS = [
    "查询", "查一下", "查下", "统计", "多少", "列表", "记录", "趋势",
    "最近", "今天", "本月", "上月", "top", "明细", "汇总", "对比",
]


def looks_like_knowledge_query(user_question: str) -> bool:
    """轻量启发式：用户是否在问 SOP/制度/操作指引（非结构化业务数据）。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    formatting_correction_signals = [
        "markdown", "格式", "渲染", "排版", "不符合", "不符",
        "重新输出", "重新展示", "重新生成", "重新排版", "重新渲染", "重新格式化",
        "format", "render",
    ]
    if any(sig in q for sig in formatting_correction_signals):
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if looks_like_compound_query_with_viz(user_question):
        return False
    if any(sig in q for sig in _DATA_QUERY_SIGNALS):
        return False
    return any(sig in q for sig in _KNOWLEDGE_SIGNALS)


# 元操作（Meta-Action）：对已有对话/结果做封装、保存、记忆等管理动作，本身不查询业务数据。
# 命中后 dispatcher 直接走 AssistantExecutor，避免被数据查询执行器的“先查库”护栏拖入冗余流程。
_META_ACTION_PATTERNS = [
    re.compile(r"(创建|新建|建个|建一个|做个|做一个|生成|封装|做成|保存|存为|存成|固化|固定).{0,4}(技能|skill)", re.I),
    re.compile(r"(技能|skill).{0,6}(固定|保存|存下来|记下来|沉淀)", re.I),
    re.compile(r"create\s+(a\s+)?skill", re.I),
    re.compile(r"save\s+(it\s+|this\s+)?as\s+(a\s+)?skill", re.I),
]


def looks_like_meta_action(user_question: str) -> bool:
    """轻量启发式：判断本轮是否为“元操作”（如基于上文创建/保存技能），而非数据查询。"""
    q = (user_question or "").strip()
    if not q:
        return False
    return any(p.search(q) for p in _META_ACTION_PATTERNS)


# 显式要求使用/执行某个已创建技能（与“创建技能”元操作区分）。
_SKILL_EXEC_PATTERNS = [
    re.compile(r"使用.{0,24}技能", re.I),
    re.compile(r"用.{0,24}技能", re.I),
    re.compile(r"(执行|运行|加载|触发|套用|按照).{0,16}技能", re.I),
    re.compile(r"use\s+.{0,24}skill", re.I),
    re.compile(r"run\s+.{0,24}skill", re.I),
]


def looks_like_skill_execution(user_question: str) -> bool:
    """轻量启发式：用户是否在要求使用/执行某个技能（而非创建新技能）。"""
    q = (user_question or "").strip()
    if not q:
        return False
    if looks_like_meta_action(q):
        return False
    return any(p.search(q) for p in _SKILL_EXEC_PATTERNS)


# 上下文动作（Context-Action）：对“已有对话/上一轮结果”执行保存、导出、发送、记忆等动作。
# 与“元操作”相比覆盖面更广（不只限于技能），用于 DataQueryExecutor 判定本轮“是否需要重新查数”：
# 命中即视为上下文动作（对已有结果做动作），不强制走 查Schema -> 执行SQL 的护栏。
_CONTEXT_ACTION_VERBS = [
    "保存", "存为", "存成", "存下来", "存到", "导出", "下载", "写入", "写到", "写成",
    "发送", "发给", "推送", "分享", "记住", "记录", "固化", "固定", "做成", "封装", "沉淀",
    "save", "export", "download", "send", "remember",
]
# 指代“已有上下文/上一轮结果”的引用词（刻意不含过于宽泛的“数据/结果”裸词，避免误伤新查询）。
_CONTEXT_REF_KEYWORDS = [
    "这个", "这些", "这份", "这张", "这条", "上面", "上述", "前面", "刚才", "刚刚",
    "之前", "上一", "前述", "该结果", "这次结果",
    "this", "above", "previous",
]


def looks_like_context_action(user_question: str) -> bool:
    """轻量启发式：判断本轮是否为“对已有上下文/上一轮结果执行动作”（保存/导出/发送/记忆/创建技能等）。

    判定规则：
    - 命中元操作（创建/保存技能）→ 直接为真；
    - 否则需同时出现“动作动词”+“指代已有上下文的引用词”，才视为上下文动作，
      以避免把“保存最近一个月订单”这类仍需新查数的请求误判。
    """
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_meta_action(q):
        return True
    has_action = any(v in q for v in _CONTEXT_ACTION_VERBS)
    has_ref = any(r in q for r in _CONTEXT_REF_KEYWORDS)
    return has_action and has_ref


def _build_history_context(history: Optional[List[Dict[str, str]]]) -> str:
    """把最近几轮对话压缩成简短上下文块，帮助识别省略主语的追问。"""
    if not history:
        return ""
    recent = [m for m in history if m.get("role") in ("user", "assistant")][-6:]
    if not recent:
        return ""
    lines = []
    for m in recent:
        role = "用户" if m.get("role") == "user" else "助手"
        content = re.sub(r"<[^>]+>", " ", str(m.get("content") or "")).strip()
        content = re.sub(r"\s+", " ", content)
        if len(content) > 200:
            content = content[:200] + "…"
        if content:
            lines.append(f"{role}：{content}")
    if not lines:
        return ""
    return "【最近对话（用于判断本轮是否为追问/指代）】\n" + "\n".join(lines)


class IntentType(str, Enum):
    DATA_QUERY = "DATA_QUERY"       # 自然语言查数、指标统计、趋势分析
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE" # 查阅手册、SOP、知识库问答
    GENERAL = "GENERAL"             # 闲聊、通用助手功能、简单的打招呼
    UNKNOWN = "UNKNOWN"             # 无法识别的意图

class IntentResponse(BaseModel):
    """Structured response for intent recognition"""
    intent: IntentType = Field(description="The identified intent category")
    confidence: float = Field(description="Confidence score from 0.0 to 1.0")
    reasoning: str = Field(description="Brief explanation of why this intent was chosen")
    entities: List[str] = Field(default_factory=list, description="Extracted key entities (e.g., room name, metric)")

class IntentService:
    """
    Service for identifying user intent using the AgentScope runtime adapter.
    """

    DEFAULT_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

    def __init__(self):
        self._llm = None

    @staticmethod
    def _format_instructions() -> str:
        return (
            "返回一个 JSON 对象，字段为："
            "intent（DATA_QUERY/KNOWLEDGE_BASE/GENERAL/UNKNOWN）、"
            "confidence（0 到 1 的数字）、"
            "reasoning（简短原因）、"
            "entities（字符串数组）。"
        )

    @staticmethod
    def _parse_response(raw: str) -> IntentResponse:
        text = (raw or "").strip()
        try:
            return IntentResponse.model_validate_json(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise
            data = json.loads(match.group())
            return IntentResponse.model_validate(data)

    async def identify_intent(
        self,
        user_input: str,
        llm=None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResponse:
        """
        Analyzes user input and returns a structured IntentResponse.
        :param llm: Optional configured LLM instance. If None, falls back to default.
        :param history: Optional recent conversation turns to disambiguate follow-ups.
        """
        # Use provided LLM or fallback to lazy-loaded default
        if llm:
            active_llm = llm
        else:
            if self._llm is None:
                from app.core.llm.client import get_llm_async
                self._llm = await get_llm_async(streaming=False)
            active_llm = self._llm
        
        if not active_llm:
            import logging
            logging.error("No LLM instance available for intent recognition.")
            return IntentResponse(
                intent=IntentType.UNKNOWN,
                confidence=0.0,
                reasoning="LLM service unavailable (configuration missing)",
                entities=[]
            )

        # 系统提示词内置在代码中，并用 .replace 注入占位符——避免 few-shot 中的 JSON
        # 花括号被 ChatPromptTemplate 当成模板变量解析。
        format_instructions = self._format_instructions()
        system_content = self.DEFAULT_SYSTEM_PROMPT.replace(
            "{history_context}", _build_history_context(history)
        ).replace("{format_instructions}", format_instructions)

        messages = [
            RuntimeMessage(
                role="system",
                content=[RuntimeContentBlock(type="text", text=system_content)],
            ),
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text=user_input)],
            ),
        ]

        try:
            chat_client = chat_client_from_handle(active_llm)
            response = await chat_client.generate_text(messages)
            return self._parse_response(response)
        except Exception as e:
            # Fallback for parsing errors or LLM failures
            import logging
            logging.error(f"Intent recognition failed: {e}")
            return IntentResponse(
                intent=IntentType.UNKNOWN,
                confidence=0.0,
                reasoning=f"Error occurred during recognition: {str(e)}",
                entities=[]
            )

# Singleton instance
intent_service = IntentService()

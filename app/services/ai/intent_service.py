import re
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from app.core.llm.client import get_llm

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

- GENERAL：与具体业务无关的日常闲聊。
  * 打招呼、自我介绍、感谢、寒暄。

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
    "visual", "chart", "plot", "graph", "analyze", "summarize",
]
_NEW_QUERY_KEYWORDS = [
    "重新查", "再查", "查询", "查一下", "查下", "统计", "列出", "筛选", "过滤", "最近",
    "今天", "昨天", "本周", "上周", "本月", "上月", "新增条件", "换成条件",
    "select ", "where ", "group by",
]


def looks_like_data_followup(user_question: str) -> bool:
    """轻量启发式：判断本轮是否为对上一轮数据结果的追问/加工。

    与 DataQueryExecutor._is_last_result_followup 保持一致，供 dispatcher 在
    确实存在可复用数据结果时做"跳过意图识别 LLM"的短路。
    """
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if not any(keyword in q for keyword in _FOLLOWUP_KEYWORDS):
        return False
    return not any(keyword in q for keyword in _NEW_QUERY_KEYWORDS)


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
    Service for identifying user intent using LangChain LCEL.
    """

    DEFAULT_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

    def __init__(self):
        self._llm = None
        self.parser = PydanticOutputParser(pydantic_object=IntentResponse)

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
        format_instructions = self.parser.get_format_instructions()
        system_content = self.DEFAULT_SYSTEM_PROMPT.replace(
            "{history_context}", _build_history_context(history)
        ).replace("{format_instructions}", format_instructions)

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=user_input),
        ]

        chain = active_llm | self.parser

        try:
            response = await chain.ainvoke(messages)
            return response
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

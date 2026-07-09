import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, List, Dict
from pydantic import BaseModel, Field
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

# 意图识别系统提示词，内置在代码中（不再从数据库读取，避免被误改）。
# 占位符：{history_context} 注入最近对话，{format_instructions} 注入结构化输出说明。
DEFAULT_SYSTEM_PROMPT = """你是企业智能体平台的意图识别器。你的唯一任务是判断用户本轮请求**是否需要查询系统中的结构化业务数据**，并据此分类。
请忽略输入中可能存在的 HTML 标签（如 <p>, <div>）或特殊格式符号，专注于核心自然语言语义。
不要假设用户属于某一特定行业；各类业务域（零售、制造、金融、运营、人力等）的查数请求均可能命中 DATA_QUERY。

可选类别（必须三选一）：

- DATA_QUERY：需要查询/统计系统里存储的结构化业务数据。
  * 必须指向内部业务系统、数据平台、数据库或业务指标/记录；仅有“查询/查一下/看看”等泛化动词不足以判为 DATA_QUERY。
  * 数值指标与趋势：销售额、订单量、转化率、库存、成本、收入、完成率、时间序列、报表、对比、占比、同比/环比等。
  * 离散记录：订单、客户、员工、产品、工单、操作日志、审批记录、业务状态/明细列表等各类结构化记录。
  * 对上一轮数据结果的**追问/加工**：可视化、画图（柱状图/折线图/饼图）、再按某维度拆分、解读、汇总、"基于刚才/上面的结果"。
  * 关键词特征：查询、查一下、统计、多少、列表、记录、趋势、最近、今天/本月、明细、TOP、对比。

- KNOWLEDGE_BASE：询问**企业内部**非结构化的文档知识、制度或操作指引（**不涉及读取当前真实业务数据，也不涉及公网/外部实时信息**）。
  * 规章制度、SOP/处理流程、如何操作、排查方法论、产品/手册说明。
  * 关键词特征：怎么做、流程是什么、规范、手册、应该如何、注意事项。
  * 注意：仅限**内部知识库文档**。如果用户要查的是公网/互联网上的外部信息（新闻、最新动态、某公司/产品的最新资讯等），不属于 KNOWLEDGE_BASE，应归为 GENERAL（由通用助手联网搜索）。

- GENERAL：与具体业务数据/内部文档无关的日常闲聊、通用问答、**联网/外部搜索**，或针对系统/对话本身的管理类操作（**本身不需要查询内部业务数据或内部知识库**）。
  * 打招呼、自我介绍、感谢、寒暄。
  * 公共常识/生活信息/编程与代码/API 用法/文本改写或文本分析，即使带有“查询/查一下/看看”，也应归为 GENERAL。
  * 联网/外部搜索：要求在互联网/公网上检索某事物的最新信息、新闻、动态、官网内容等（如"搜索 X 的最新消息""联网查一下 Y"）。这类请求由通用助手调用联网搜索工具完成，归为 GENERAL。
  * 元操作（Meta-Action）：基于已有对话/上一轮结果做"创建技能、保存为技能或模板、把流程固定下来、记住某项偏好"等管理动作。这类请求是对结果的封装/保存，不需要重新查数，应归为 GENERAL。

判定要点：
1. **追问优先看上下文**：若本轮是省略主语的追问/指代（如"画个柱状图""再按区域拆一下""分析一下""换成饼图"），且最近对话刚返回过数据，则归为 DATA_QUERY。
2. **区分"流程知识" vs "真实记录"**：问"退款处理流程/SOP 是什么" → KNOWLEDGE_BASE；问"最近有哪些待处理工单" → DATA_QUERY。
3. **内部业务数据才归 DATA_QUERY**：若只是公共信息、编程/API 用法、文本处理、系统使用帮助，不能因为出现"查询/查看/查一下"就归为 DATA_QUERY。
4. **流程/规范优先知识库**：询问内部 SOP、流程、规范、手册、怎么操作时，即使带有"查询/查一下"，也优先归为 KNOWLEDGE_BASE；只有查询真实记录、列表、数量、趋势时才归 DATA_QUERY。
5. **难以区分时优先 GENERAL**：如果无法确认用户是在查内部业务数据、查内部知识库，还是普通问答，请归为 GENERAL，让通用助手继续澄清；不要为了安全感强行归为 DATA_QUERY。

示例：
用户：各区域上个月的销售额趋势
输出：{"intent": "DATA_QUERY", "confidence": 0.97, "reasoning": "请求时间序列指标数据", "entities": ["各区域", "销售额", "上个月"]}

用户：最近有哪些待处理工单
输出：{"intent": "DATA_QUERY", "confidence": 0.95, "reasoning": "查询离散业务记录", "entities": ["待处理工单"]}

用户：把刚才的结果画成柱状图
输出：{"intent": "DATA_QUERY", "confidence": 0.93, "reasoning": "对上一轮数据结果的可视化追问", "entities": ["柱状图"]}

用户：订单退款的标准处理流程是什么
输出：{"intent": "KNOWLEDGE_BASE", "confidence": 0.9, "reasoning": "询问 SOP 处理流程，非真实记录", "entities": ["订单退款", "处理流程"]}

用户：联网搜索一下"某品牌"的最新信息
输出：{"intent": "GENERAL", "confidence": 0.93, "reasoning": "请求联网检索外部公网最新信息，非内部知识库，由通用助手联网搜索", "entities": ["某品牌"]}

用户：你好，你是谁
输出：{"intent": "GENERAL", "confidence": 0.98, "reasoning": "日常打招呼", "entities": []}

{history_context}

{format_instructions}

必须严格返回 JSON 格式，且只包含 JSON 内容。"""


# 追问/指代类关键词（用于 dispatcher 的廉价短路与意图器的上下文判断）。
_FOLLOWUP_KEYWORDS = [
    "可视化", "图表", "画图", "画个图", "柱状图", "折线图", "饼图", "分析一下", "总结一下",
    "解读一下", "基于上", "基于刚才", "根据上", "根据刚才", "上面的", "刚才的", "这个结果",
    "这些数据", "这个数据", "该数据", "当前数据", "上一轮", "前面的", "按这个结果", "对这些",
    "这列", "那列", "该列",
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
    re.compile(r"(那列|这列|该列|日期|时间).{0,12}(短日期|短时间|年月日)", re.I),
    re.compile(r"(改成|转成|显示为).{0,12}(短日期|短时间|年月日)", re.I),
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
    "怎么操作", "如何操作", "操作指引", "运维规范", "故障排查", "应急预案",
    "知识库", "文档里", "说明书", "用户手册", "政策", "条例", "合规",
    "wiki", "playbook", "runbook",
]
_PLATFORM_SELF_SERVICE_SUBJECTS = (
    "技能", "skill", "agent", "智能体", "工具", "tool", "模型", "model",
    "插件", "plugin", "mcp", "提示词", "prompt", "上下文", "配置",
)
_PLATFORM_SELF_SERVICE_ACTIONS = (
    "安装", "挂载", "加载", "启用", "配置", "设置", "创建", "管理", "使用",
    "触发", "执行", "怎么", "如何", "哪里", "在哪", "用法", "说明",
)
_DATA_QUERY_SIGNALS = [
    "查询", "查一下", "查下", "统计", "多少", "列表", "记录", "趋势",
    "最近", "今天", "本月", "上月", "top", "明细", "汇总", "对比",
]
_DATA_QUERY_GENERIC_VERBS = ["查询", "查一下", "查下", "查", "看看", "查看"]
_DATA_QUERY_STRONG_SIGNALS = [
    "统计", "多少", "列表", "记录", "趋势", "top", "明细", "汇总", "对比",
    "数量", "报表", "指标", "数据集", "数据库", "sql", "工单", "告警记录",
    "故障记录", "操作日志", "设备清单", "资产列表",
    "count", "list", "record", "records", "table", "metric", "metrics", "report",
]

_RUNTIME_LOCAL_CONTEXT_SIGNALS = [
    "当前系统", "当前服务器", "当前机器", "当前这台", "当前平台", "当前环境",
    "本机", "本地", "我机器", "我的机器", "这台机器", "这台服务器", "这台主机",
    "运行环境", "系统运行",
]
_RUNTIME_METRIC_SIGNALS = [
    "cpu", "内存", "memory", "磁盘", "disk", "负载", "load", "进程", "端口",
    "网络连通", "服务状态", "运行状态", "资源使用", "使用情况",
]
_RUNTIME_COMMAND_SIGNALS = [
    "执行命令", "shell", "bash", "uptime", "top ", "ps ", "df -h", "free -h",
]



def looks_like_strong_business_data_request(user_question: str) -> bool:
    """低置信度兜底用：只有明确业务数据对象/指标/记录信号才算强查数。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    return any(sig in q for sig in _DATA_QUERY_STRONG_SIGNALS)


def looks_like_runtime_diagnostic_query(user_question: str) -> bool:
    """当前运行环境诊断：需要工具拿真实状态，但不属于 ChatBI 业务库查数。"""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if any(sig in q for sig in _RUNTIME_COMMAND_SIGNALS):
        return True
    has_runtime_metric = any(sig in q for sig in _RUNTIME_METRIC_SIGNALS)
    if not has_runtime_metric:
        return False
    return any(sig in q for sig in _RUNTIME_LOCAL_CONTEXT_SIGNALS)


def looks_like_platform_self_service_query(user_question: str) -> bool:
    """Whether the user is asking about the platform/agent system itself."""
    q = (user_question or "").strip().lower()
    if not q:
        return False
    has_subject = any(sig in q for sig in _PLATFORM_SELF_SERVICE_SUBJECTS)
    if not has_subject:
        return False
    return any(sig in q for sig in _PLATFORM_SELF_SERVICE_ACTIONS)


def looks_like_public_profile_lookup(user_question: str) -> bool:
    """用户在查公开主体资料，而不是内部业务库记录。

    这类问题常见形态是“查一下 X 公司信息 / 了解某品牌资料”。它们可能没有
    “联网/新闻/官网”等显式公网词，但语义来源仍更接近公开搜索，不应仅因
    “查一下”或意图模型误判而进入 data_query。
    """
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_strong_business_data_request(q):
        return False
    lookup_verbs = (*_DATA_QUERY_GENERIC_VERBS, "了解", "搜一下", "搜索", "search")
    profile_subjects = ("公司", "企业", "机构", "品牌", "人物", "company", "brand")
    profile_fields = ("信息", "资料", "简介", "介绍", "背景", "官网", "新闻", "动态", "profile", "info")
    has_lookup = any(term in q for term in lookup_verbs)
    if not has_lookup:
        return False
    return any(subject in q for subject in profile_subjects) and any(field in q for field in profile_fields)


def looks_like_short_field_or_continuation_followup(user_question: str) -> bool:
    """极其简短的字段/属性追问（如：姓名呢、那手机号呢、还有创建时间呢、销售额呢）。"""
    q = (user_question or "").strip()
    if not q or len(q) > 15:
        return False

    has_prefix = any(q.startswith(p) for p in ("那", "还有", "加上", "补充", "增加"))
    has_suffix = q.endswith(("呢", "吗", "?", "？"))

    if not (has_prefix or has_suffix):
        return False

    # 去除前缀和后缀，提取核心内容
    core = q
    for p in ("那", "还有", "加上", "补充", "增加"):
        if core.startswith(p):
            core = core[len(p):]
            break
    for s in ("呢", "吗", "?", "？"):
        if core.endswith(s):
            core = core[:-len(s)]
    core = core.strip("?？ \t")

    if not core or len(core) > 10:
        return False

    # 排除纯人称代词和无意义单字
    if core in {"你", "我", "他", "它", "谁", "这", "那"}:
        return False
    # 排除一些明显的通用疑问词
    if core in {"什么", "怎么", "哪里", "哪个", "如何", "为什么", "怎么做"}:
        return False

    return True


def should_inherit_data_agent_session(user_question: str) -> bool:
    """上一轮为 data_query 智能体时，本轮是否仍应沿用其会话粘性。

    正向判定：仅当本轮是「对已有查数结果的加工追问」，或仍含明确内部业务库查数信号。
    仅有「看看/查一下/情况」等弱探询、但无内部业务对象时返回 False，避免机械沿用 ChatBI。
    """
    q = (user_question or "").strip()
    if not q:
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if looks_like_data_followup(q) or looks_like_pure_result_followup(q):
        return True
    if looks_like_short_field_or_continuation_followup(q):
        return True
    return looks_like_strong_business_data_request(q)


_GREETING_CORE_PHRASES = frozenset({
    "你好", "您好", "你好呀", "你好啊", "您好呀", "你好吗",
    "hi", "hello", "hey",
    "早上好", "下午好", "晚上好", "中午好",
    "你是谁", "你是哪位", "你能做什么", "你能干嘛",
    "介绍一下自己", "介绍一下你自己",
    "谢谢", "感谢", "多谢", "辛苦了", "thanks",
})
# 问候短路时若同时出现这些词，视为复合业务请求而非纯寒暄
_GREETING_COMPOUND_BLOCKERS = [
    *_DATA_QUERY_SIGNALS,
    "搜索", "搜一下", "流程", "规范", "手册", "怎么办", "如何", "怎么",
    "联网", "新闻", "资讯", "订单", "客户", "数据", "报表", "查询",
]


def looks_like_greeting(user_question: str) -> bool:
    """纯问候/寒暄/自我介绍/致谢，无业务诉求。用于路由与意图 LLM 短路。"""
    q = (user_question or "").strip()
    if not q or len(q) > 32:
        return False
    q_lower = q.lower()
    if any(sig in q_lower for sig in _GREETING_COMPOUND_BLOCKERS):
        return False
    q_core = re.sub(r"[\s!！?？。．,，~～]+", "", q_lower)
    if q_core in _GREETING_CORE_PHRASES:
        return True
    if re.fullmatch(r"(你好|您好)(吗|呀|啊)?", q_core):
        return True
    if re.fullmatch(r"(hi|hello|hey)", q_core):
        return True
    return False


# 联网/外部搜索信号：明确指向公网/实时外部信息，应走通用助手 + web_search 工具，
# 而非内部知识库（KNOWLEDGE_BASE）。与“内部文档/制度/SOP”严格区分。
# 注意：这里刻意只保留强外部语义的词，避免与查数语句（如“查一下目前的库存”）混淆。
_WEB_SEARCH_EXPLICIT_SIGNALS = [
    "联网", "网上", "网络上", "互联网", "上网查", "上网搜", "在线搜", "网络搜索",
    "百度", "谷歌", "google", "bing", "必应", "搜索引擎",
    "官网", "官方网站", "新闻", "资讯", "舆情",
    "最新消息", "最新动态", "最新资讯", "最新新闻", "最新进展",
]
# 仅“显式搜索动词”，不含“查一下/查查”等与查数高度重叠的泛化动词
_WEB_SEARCH_VERBS = [
    "搜索", "搜一下", "搜一搜", "搜下", "search", "look up",
]
# 与显式搜索动词组合时才生效的时效词（强外部色彩）
_WEB_FRESHNESS_SIGNALS = [
    "最新", "近期", "近况", "latest", "recent", "news",
]


def looks_like_web_search_query(user_question: str) -> bool:
    """轻量启发式：用户是否在请求“联网/外部公网搜索”（非内部知识库/非结构化业务数据）。

    判定（任一成立）：
    1. 命中明确的联网/公网/新闻/官网/“最新消息”等强外部关键词；
    2. “显式搜索动词 + 时效词”组合（如“搜索 X 的最新信息”）。

    刻意排除“查一下/查查”等泛化动词与“目前/现在”等弱时效词，避免误伤查数语句。
    命中元操作/上下文动作/技能执行时不算。
    """
    q = (user_question or "").strip().lower()
    if not q:
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if any(sig in q for sig in _WEB_SEARCH_EXPLICIT_SIGNALS):
        return True
    has_search_verb = any(sig in q for sig in _WEB_SEARCH_VERBS)
    has_freshness = any(sig in q for sig in _WEB_FRESHNESS_SIGNALS)
    return has_search_verb and has_freshness


_GENERAL_BOUNDARY_PATTERNS = [
    re.compile(r"(天气|气温|空气质量|下雨|降雨|台风|天气预报)", re.I),
    re.compile(r"(python|java|javascript|typescript|vue|react|sqlalchemy|fastapi|api|接口|代码|函数|报错|bug).{0,24}(用法|怎么写|怎么用|解释|示例|区别|原因)", re.I),
    re.compile(r"(查询|查一下|查下|看看).{0,24}(python|java|javascript|typescript|vue|react|api|接口|代码).{0,24}(用法|文档|示例|区别)", re.I),
    re.compile(r"(分析|总结|润色|改写|翻译|解释).{0,12}(这段话|这段文字|这段文本|下面这段|这句话|文本)", re.I),
]


def looks_like_general_query(user_question: str) -> bool:
    """强通用边界：生活信息、编程/API、文本处理等，不应因泛化查词走 DATA_QUERY。"""
    q = (user_question or "").strip()
    if not q:
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if looks_like_web_search_query(q):
        return True
    return any(pattern.search(q) for pattern in _GENERAL_BOUNDARY_PATTERNS)


def looks_like_knowledge_query(user_question: str) -> bool:
    """轻量启发式：用户是否在问 SOP/制度/操作指引（非结构化业务数据，且非联网搜索）。"""
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
    if looks_like_platform_self_service_query(q):
        return False
    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    # 联网/外部搜索优先于内部知识库判定，避免“搜索最新新闻”被当成知识库问答
    if looks_like_web_search_query(user_question):
        return False
    if looks_like_compound_query_with_viz(user_question):
        return False
    has_knowledge_signal = any(sig in q for sig in _KNOWLEDGE_SIGNALS)
    if not has_knowledge_signal:
        return False
    has_data_signal = any(sig in q for sig in _DATA_QUERY_SIGNALS)
    if not has_data_signal:
        return True
    has_strong_data_signal = any(sig in q for sig in _DATA_QUERY_STRONG_SIGNALS)
    only_generic_query_verb = any(sig in q for sig in _DATA_QUERY_GENERIC_VERBS) and not has_strong_data_signal
    if only_generic_query_verb:
        return True
    if has_strong_data_signal:
        return False
    return True


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


class IntentSource(str, Enum):
    INTERNAL_STRUCTURED_DATA = "internal_structured_data"
    PUBLIC_WEB = "public_web"
    INTERNAL_DOCS = "internal_docs"
    RUNTIME_DIAGNOSTIC = "runtime_diagnostic"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentSourceFrame:
    source: IntentSource
    confidence: float
    reasoning: str


def _intent_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


def _coerce_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def resolve_intent_source(
    query: str,
    *,
    semantic_intent: Any = None,
    semantic_confidence: Any = None,
    turn_intent: Any = None,
) -> IntentSourceFrame:
    """在将请求路由至专属智能体/工具前，仲裁本轮请求的来源类型。

    语义模型输出的 DATA_QUERY 标签只是证据，不能直接等同于"来自内部业务库"：
    - "查一下 X 公司信息"等公开主体查询可能被误判为 DATA_QUERY，但来源实为公网搜索，
      需要通过 looks_like_public_profile_lookup 提前拦截；
    - 当前系统 / 本机 / 服务器 CPU 内存等运行环境诊断需要执行类工具，不属于 ChatBI；
    - 强业务数据词（列表/记录/统计/明细等）或数据追问信号则直接采信为内部结构化数据；
    - 其余 DATA_QUERY 只作为弱证据保留，不直接触发 ChatBI / sub_agent_call。
    """
    query = (query or "").strip()
    if looks_like_platform_self_service_query(query):
        return IntentSourceFrame(
            source=IntentSource.UNKNOWN,
            confidence=0.9,
            reasoning="platform self-service or skills/tools configuration query",
        )

    if looks_like_web_search_query(query):
        return IntentSourceFrame(
            source=IntentSource.PUBLIC_WEB,
            confidence=0.95,
            reasoning="explicit public web/search signal",
        )

    if looks_like_knowledge_query(query):
        return IntentSourceFrame(
            source=IntentSource.INTERNAL_DOCS,
            confidence=0.85,
            reasoning="internal documentation or SOP signal",
        )

    if looks_like_runtime_diagnostic_query(query):
        return IntentSourceFrame(
            source=IntentSource.RUNTIME_DIAGNOSTIC,
            confidence=0.9,
            reasoning="current runtime/system diagnostic signal",
        )

    if looks_like_public_profile_lookup(query):
        return IntentSourceFrame(
            source=IntentSource.UNKNOWN,
            confidence=0.8,
            reasoning="public profile lookup without structured-data signal",
        )

    has_structured_signal = looks_like_strong_business_data_request(query)
    has_data_followup_signal = (
        looks_like_data_followup(query)
        or looks_like_pure_result_followup(query)
        or looks_like_short_field_or_continuation_followup(query)
    )
    if has_structured_signal or has_data_followup_signal:
        return IntentSourceFrame(
            source=IntentSource.INTERNAL_STRUCTURED_DATA,
            confidence=0.9 if has_data_followup_signal else 0.72,
            reasoning=(
                "data-result follow-up signal"
                if has_data_followup_signal
                else "strong internal structured data signal"
            ),
        )

    semantic_name = _intent_name(semantic_intent)
    semantic_score = _coerce_confidence(semantic_confidence)
    turn_name = _intent_name(turn_intent)

    if semantic_name in {IntentType.GENERAL.value, IntentType.KNOWLEDGE_BASE.value, IntentType.UNKNOWN.value}:
        return IntentSourceFrame(
            source=IntentSource.UNKNOWN,
            confidence=semantic_score,
            reasoning=f"router semantic intent is {semantic_name}",
        )
    if semantic_name == IntentType.DATA_QUERY.value:
        return IntentSourceFrame(
            source=IntentSource.UNKNOWN,
            confidence=semantic_score,
            reasoning="router semantic intent is DATA_QUERY without internal structured-data signal",
        )
    if turn_name == IntentType.DATA_QUERY.value:
        return IntentSourceFrame(
            source=IntentSource.UNKNOWN,
            confidence=0.6,
            reasoning="turn classification intent is DATA_QUERY without internal structured-data signal",
        )

    return IntentSourceFrame(
        source=IntentSource.UNKNOWN,
        confidence=0.0,
        reasoning="no reliable source signal",
    )

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
    def _intent_from_tool_call_payload(data: Any) -> Optional[IntentResponse]:
        """意图模型偶发直接返回工具调用 JSON，而非 IntentResponse 结构。"""
        if not isinstance(data, dict):
            return None
        tool_name = str(data.get("tool") or data.get("name") or "").strip().lower()
        if "search_knowledge" in tool_name:
            return IntentResponse(
                intent=IntentType.KNOWLEDGE_BASE,
                confidence=0.9,
                reasoning="模型返回 search_knowledge_base 工具调用，归类为知识库问答",
                entities=[],
            )
        return None

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
            tool_intent = IntentService._intent_from_tool_call_payload(data)
            if tool_intent is not None:
                return tool_intent
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

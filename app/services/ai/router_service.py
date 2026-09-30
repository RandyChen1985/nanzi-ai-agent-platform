import asyncio
import json
import logging
import time
from typing import List, Optional

from app.core.llm.client import get_llm_async
from app.services.ai.intent_service import (
    IntentResponse,
    IntentSource,
    IntentSourceFrame,
    IntentType,
    looks_like_strong_business_data_request,
)
from app.services.ai.request_decision import (
    RequestDecision,
    RequestSource,
    apply_chatbi_qualification,
    resolve_request_decision,
)
from app.services.ai.chatbi_qualification import (
    ChatBIQualification,
    ChatBIMode,
    qualify_chatbi_request,
    resolve_authorized_dataset_candidates,
)
from app.services.ai.accessible_resource_catalog import (
    build_accessible_resource_catalog,
)
from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    load_authorized_knowledge_catalog,
)
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.route_progress import RouteProgressCallback, emit_route_stage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

AMBIGUOUS_INTENT_CONFIDENCE_THRESHOLD = 0.65
ROUTER_MAX_OUTPUT_TOKENS = 512
DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS = 15.0

class LLMRouterResponse(BaseModel):
    """Internal structure for LLM output. thought 放最后，降低截断重伤概率。"""
    agent_name: str
    confidence: float
    secondary_agents: List[str] = []
    turn_labels: List[str] = []
    relation_to_previous: str = "unknown"
    user_action_type: str = "unknown"
    intent: Optional[str] = None
    domain: Optional[str] = None
    operation: Optional[str] = None
    fact_kind: Optional[str] = None
    freshness_requirement: Optional[str] = None
    time_scope: Optional[str] = None
    reference_mode: Optional[str] = None
    needs_fresh_data: Optional[bool] = None
    intent_confidence: Optional[float] = None
    intent_reasoning: Optional[str] = None
    thought: str = ""

class RouterService:
    # 兜底通用助手 slug，按优先级匹配 DB 中 ai_agents.name（首个命中即用）
    FALLBACK_AGENT_NAMES = ("assistant", "main", "general-chat")

    DEFAULT_SYSTEM_PROMPT = """# Role: 南孜智能体平台 · 智能路由助手 (Smart Router V8 · 清单驱动 · 意图合并)

你是智能体平台的"分诊台"。任务：依据【可用智能体清单】+【对话历史与上一轮路由】+【用户最新输入】，选出最合适的智能体，并同时给出来源意图。
你只输出路由决策 JSON，绝不回答业务问题本身。

## 1. 可用智能体清单 (唯一可选范围)
{agents_context}

## 2. 当前用户可访问的内部资源摘要
{accessible_resources_context}

该摘要只用于辅助判断用户问题是否可能属于内部知识库或结构化数据；
不能据此直接回答，也不能代替工具调用和服务端权限校验。
- 若问题与某个知识库名称或摘要有明确语义关联，且问题属于手册、流程、规范或怎么操作，优先选择知识库智能体并标记 `internal_docs`。
- 只有弱关键词重合、或问题明确询问公网/外部系统时，不能因为摘要存在就强行走内部资源。

## 3. 对话历史与上一轮路由
{history_context}

## 4. 决策步骤 (Reasoning Steps)

### Step 1 指代消解 (Coreference Resolution)
- 若输入含"它/这个/那个/刚才/上面/列表里第一个/继续/再/还有/也"等指代或省略主语，先结合历史还原其真实意图，再判断。
- 示例：上文在说"华东区域"，用户问"它有多少订单？"，应识别出"它"=华东区域。

### Step 2 会话连续性优先 (Session Continuity) — 重要
- 若本轮是对上一轮的追问/补充/指代（如"可视化一下""再查下""那它呢""展开讲讲"），且【没有】出现明确属于其它智能体的新领域意图，则【优先沿用上一轮处理的智能体】。
- 仅当用户明确转向另一个领域时，才切换到别的智能体。
- 【禁止机械沿用】：若上一轮由 data_query 智能体处理，但本轮【不再】表现为内部业务库查数或数据结果追问（例如仅有「看看/情况/查一下」等弱探询、或已切换到与平台业务库无关的新话题），必须重新分诊，不得仅因会话连续性继续路由到 data_query 智能体。

### Step 3 语义匹配 (Semantic Matching)
- 逐一对照清单中每个智能体的 name / 中文名 / Description / Capabilities，选出职责与用户真实意图最吻合的那一个。
- 你对"领域/职责"的判断必须来自上面清单里的描述，【禁止】脑补清单之外的智能体或领域。
- 区分"ChatBI 业务数据指标查询"与"当前运行环境诊断"：
  - 用户询问当前系统/本机/这台机器/服务器运行状态、负载、CPU、内存、磁盘、进程、端口、网络连通性、服务状态、日志、或要求执行命令时，这是平台运行环境诊断/工具执行意图，应选择 {fallback_agent_name}。
  - 用户是通过 ChatBI 查询、统计系统数据库中存储的结构化业务数据（如：销售额、订单量、转化率、库存、成本、完成率等历史/业务指标，以及客户/员工/产品/工单/审批等业务记录或数据列表）时，才按清单匹配 ChatBI 数据查询类智能体。
  - 不要因为出现"负载/利用率/CPU/内存"等词就直接判为数据查询；必须先判断用户是在看"当前这台机器"还是查"业务/监控数据指标"。
- 区分"泛化查询词"与"内部业务查数"：
  - 天气、公共常识、外部事实、代码/API/编程问题、文本分析/改写/翻译、系统使用帮助等属于通用助手，即使带有「查询/查一下」也应选择 {fallback_agent_name}。
  - 凡未明确指向平台已接入业务库的结构化数据（记录/列表/指标/报表/SQL 明细），而更像第三方平台指标、公网资讯、个人项目/外部系统状态时，应选择 {fallback_agent_name}，不要路由到 ChatBI 的 data_query 智能体。
  - 内部 SOP/流程/规范/手册/怎么操作/处理步骤属于知识库或文档类智能体，即使带有「查询/查一下」也不要路由到数据查询类智能体。
  - 只有用户明确要通过 ChatBI 查询内部业务数据的真实记录、列表、数量、指标、趋势、报表、SQL 明细时，才选择具备 data_query 能力的智能体；data_query 在本平台专指 ChatBI，不包括机器运行状态、CPU、内存、进程、端口或日志。
  - 当通用、知识库、数据查询三者难以区分时，优先选择 {fallback_agent_name}，由通用助手澄清用户到底要查数据、查知识库，还是普通问答。

### Step 4 复合意图判定 (Multi-Intent) — 保守
- 仅当用户问题明确跨越两个不同智能体的职责、且可并行处理时，才填写 secondary_agents；否则 secondary_agents 必须为空（如无必要，勿增实体）。

### Step 5 通用会话标签 (Generic Turn Hints) — 仅作为提示
- 基于当前输入和历史，输出通用标签，供后续 executor 作为 hint。注意：这些标签不是任何具体智能体内部业务分类的最终结论。
- turn_labels 可选值：
  - new_business_request：新的业务请求或新的业务目标。
  - continuation_followup：依赖上一轮上下文的继续追问。
  - topic_switch：切换话题或切换业务领域。
  - context_action：对已有上下文执行保存、导出、发送、记住等动作。
  - meta_action：对智能体、技能、会话等系统对象做管理动作。
  - business_related：和业务数据、业务知识或业务流程相关。
  - same_topic：与上一轮保持同一主题。
  - multi_intent：明确跨多个智能体职责。
  - general_chat：闲聊或通用问答。
  - ambiguous：上下文不足或标签不确定。
- relation_to_previous 只能是：new_topic、followup、topic_switch、standalone、unknown。
- user_action_type 只能是：ask_business_data_or_task、ask_knowledge、transform_context、save_or_export_context、manage_agent_or_skill、chat、unknown。
- 如果不确定，请使用 ambiguous / unknown，不要编造标签。

### Step 6 来源意图 (Source Intent) — 与选人同时输出
- 必须同时给出 intent / domain，供后续资格校验。domain 是来源边界，优先于「统计/查询/画图」等动作词。
- intent 只能是：DATA_QUERY、KNOWLEDGE_BASE、GENERAL、UNKNOWN。
- domain 只能是：chatbi_business_data（平台已接入的内部业务数据/指标/记录）、conversation_context（基于上一轮 ChatBI 结果继续加工）、runtime_environment（当前机器/服务器/进程/资源/服务状态）、local_file（本机文件或目录）、public_web（公网/外部系统）、internal_docs（内部文档/SOP）、general（通用问答/编程/文本处理）、unknown。
- operation 只能是：lookup、aggregate、visualize、transform、export、explain、unknown。
- 「统计一下我机器的文件数」→ domain=local_file；「看看服务器 CPU」→ domain=runtime_environment；「查一下有孚网络公司信息」→ domain=public_web。这三类即使误选了数据智能体，也必须标对 domain。
- 「统计客户订单 / 机房列表」→ intent=DATA_QUERY, domain=chatbi_business_data。
- 难以区分时 intent=GENERAL、domain=general，不要为了安全感强行标 DATA_QUERY。
- intent_confidence / intent_reasoning 只描述来源意图，禁止复用主智能体选择的 confidence / thought。

## 5. 硬性约束 (Hard Constraints)
- agent_name 与 secondary_agents 中的每个值，【必须】与清单中某个智能体的 name 字段完全一致（英文 slug，如 chat-bi）。严禁使用中文名、领域名或清单里不存在的名称。
- 当没有任何业务智能体明显匹配（纯打招呼/闲聊/无法归类）时，选择兜底智能体 {fallback_agent_name}。
- confidence 表示你对"主智能体选择"的把握：能明确匹配某业务智能体时应 >= 0.7；只有完全无法归类时才走 {fallback_agent_name} 并给较低分。

## 6. 输出格式 (Output Format)
必须返回纯 JSON，严禁包含 Markdown 标记或额外文字。
【关键】字段顺序必须如下，且 thought 不超过 40 个汉字，避免输出被截断：
{
  "agent_name": "清单中的某个 name",
  "confidence": 0.95,
  "secondary_agents": [],
  "turn_labels": ["continuation_followup", "business_related", "same_topic"],
  "relation_to_previous": "followup",
  "user_action_type": "transform_context",
  "intent": "DATA_QUERY",
  "domain": "chatbi_business_data",
  "operation": "lookup",
  "fact_kind": "business_metric",
  "freshness_requirement": "historical",
  "time_scope": null,
  "reference_mode": "new_query",
  "needs_fresh_data": true,
  "intent_confidence": 0.82,
  "intent_reasoning": "内部业务记录查询",
  "thought": "一句话理由"
}

## 7. 示例 (名称以"清单"为准，下例仅示意格式)
- 用户："你好" -> {"agent_name": "{fallback_agent_name}", "confidence": 0.9, "secondary_agents": [], "turn_labels": ["general_chat"], "relation_to_previous": "standalone", "user_action_type": "chat", "intent": "GENERAL", "domain": "general", "operation": "explain", "thought": "纯打招呼"}
- 上一轮由 data-agent 处理，用户："那再画个柱状图" -> {"agent_name": "data-agent", "confidence": 0.92, "secondary_agents": [], "turn_labels": ["continuation_followup", "business_related", "same_topic"], "relation_to_previous": "followup", "user_action_type": "transform_context", "intent": "DATA_QUERY", "domain": "conversation_context", "operation": "visualize", "thought": "数据结果追问，沿用上一轮"}
- 用户："我有哪些数据集/知识库" -> {"agent_name": "{fallback_agent_name}", "confidence": 0.93, "secondary_agents": [], "turn_labels": ["meta_action", "general_chat"], "relation_to_previous": "standalone", "user_action_type": "manage_agent_or_skill", "intent": "GENERAL", "domain": "general", "operation": "explain", "thought": "权限内资源目录，走通用助手工具"}
- 用户："统计一下我机器的文件数" -> {"agent_name": "{fallback_agent_name}", "confidence": 0.94, "secondary_agents": [], "turn_labels": ["general_chat"], "relation_to_previous": "standalone", "user_action_type": "chat", "intent": "GENERAL", "domain": "local_file", "operation": "aggregate", "fact_kind": "file_count", "thought": "本机文件统计，不是业务查数"}"""

    COMPACT_SYSTEM_PROMPT = """# Role: 南孜智能体平台 · 智能路由助手

你只负责选择目标智能体和判断来源意图，绝不回答业务问题本身。
只能从【可用智能体清单】中选择，必须返回纯 JSON。

## 可用智能体清单（唯一可选范围）
{agents_context}

## 当前用户可访问的内部资源摘要
{accessible_resources_context}

资源摘要只用于判断是否可能属于内部知识库或结构化数据，不能代替工具调用和服务端权限校验。
若问题与知识库名称或摘要有明确语义关联，且属于内部 SOP/流程/规范/手册/怎么操作，优先标记
`intent=KNOWLEDGE_BASE, domain=internal_docs`；弱关键词重合时不要强行路由到知识库。

## 对话历史与上一轮路由
{history_context}

## 选择规则
1. 先结合历史处理“它/这个/刚才/继续/再/还有/也”等指代和省略主语。
2. 对上一轮的明确追问、补充或上下文动作，优先沿用上一轮智能体；出现明确新领域时必须重新选择。
3. 逐一对照候选的 name、中文名、Description 和 Capabilities，不得脑补清单之外的专家。
4. 当前系统/本机/这台机器/服务器运行状态、CPU、内存、磁盘、进程、端口、网络、日志和服务状态，
   应选择 {fallback_agent_name}。不要因为出现"负载/利用率/CPU/内存"等词就直接判为数据查询。
5. ChatBI 仅处理平台已接入的内部业务数据，例如销售额、订单量、客户/员工/产品/工单/审批等业务记录或数据列表。
   天气、公共常识、外部事实、代码/API/编程、文本改写/翻译、系统使用帮助和公网信息，选择 {fallback_agent_name}。
6. 只有明确跨越多个专家职责且可并行处理时才填写 secondary_agents，否则必须为空。
7. intent 只能是 DATA_QUERY、KNOWLEDGE_BASE、GENERAL、UNKNOWN；
   domain 只能是 chatbi_business_data、conversation_context、runtime_environment、local_file、public_web、
   internal_docs、general、unknown；operation 只能是 lookup、aggregate、visualize、transform、export、explain、unknown。
8. 难以区分时选择 {fallback_agent_name}，不要为了安全感强行选择 ChatBI 或知识库专家。

## 输出格式
agent_name 和 secondary_agents 中的值必须与清单中的英文 name 完全一致。
confidence 是目标智能体选择置信度；intent_confidence 是来源意图置信度，二者不能混用。
thought 不超过 40 个汉字。只返回下列 JSON，不要 Markdown 或额外文字：
{
  "agent_name": "清单中的 name",
  "confidence": 0.95,
  "secondary_agents": [],
  "turn_labels": [],
  "relation_to_previous": "standalone",
  "user_action_type": "unknown",
  "intent": "GENERAL",
  "domain": "general",
  "operation": "explain",
  "fact_kind": "unknown",
  "freshness_requirement": "unknown",
  "time_scope": null,
  "reference_mode": "new_query",
  "needs_fresh_data": false,
  "intent_confidence": 0.0,
  "intent_reasoning": "",
  "thought": "一句话理由"
}"""

    ALLOWED_TURN_LABELS = {
        "new_business_request",
        "continuation_followup",
        "topic_switch",
        "context_action",
        "meta_action",
        "business_related",
        "same_topic",
        "multi_intent",
        "general_chat",
        "ambiguous",
    }
    ALLOWED_RELATIONS = {"new_topic", "followup", "topic_switch", "standalone", "unknown"}
    ALLOWED_ACTION_TYPES = {
        "ask_business_data_or_task",
        "ask_knowledge",
        "transform_context",
        "save_or_export_context",
        "manage_agent_or_skill",
        "chat",
        "unknown",
    }

    _INTENT_VALUES = {item.value for item in IntentType}
    _PLACEHOLDER_INTENTS = {"", "UNKNOWN", "GENERAL"}
    _PLACEHOLDER_DOMAINS = {"", "unknown"}
    def __init__(self):
        self._agents_cache: List[dict] = []
        self._last_cache_time: float = 0.0
        self._cache_ttl: int = 60 # Seconds

    def invalidate_cache(self):
        """Force clear the agents cache to ensure immediate updates."""
        self._agents_cache = []
        self._last_cache_time = 0.0
        logger.info("RouterService cache invalidated.")

    async def route_query(
        self, 
        user_input: str, 
        history: Optional[List[dict]] = None,
        enable_multi_agent: bool = True,
        user_id: Optional[int] = None,
        is_admin: bool = False,
        last_agent_name: Optional[str] = None,
        user_name: Optional[str] = None,
        early_context: Optional[str] = None,
        on_progress: Optional[RouteProgressCallback] = None,
    ) -> Optional[TurnDecision]:
        """
        Use LLM to select the most appropriate agent(s) for the user query.

        last_agent_name: 上一轮处理本会话的智能体 name(slug)，用于会话粘性，
        让追问/指代类输入优先沿用上一轮智能体，降低多轮误路由。
        early_context: 跨轮持久化的早期轮次压缩摘录（早于当前窗口被压缩溢出的历史要点），
        随路由上下文一并交给路由模型，使多轮后路由判断仍基于完整会话背景。
        """
        current_time = time.time()

        # 1. Fetch Agents (with Caching)
        candidate_started = time.perf_counter()
        await emit_route_stage(
            on_progress,
            "candidate_catalog",
            "获取可用专家",
            status="pending",
        )
        try:
            if self._agents_cache and (current_time - self._last_cache_time < self._cache_ttl):
                agents_metadata = self._agents_cache
            else:
                agents_metadata = await self._fetch_agents_from_db()
                if agents_metadata:
                    self._agents_cache = agents_metadata
                    self._last_cache_time = current_time

            if not agents_metadata:
                logger.warning("No agents available for routing.")
                await emit_route_stage(
                    on_progress,
                    "candidate_catalog",
                    "获取可用专家",
                    status="error",
                    details="当前没有可用的路由专家",
                    execution_time_ms=(time.perf_counter() - candidate_started) * 1000,
                )
                return None

            agents_metadata = await self._filter_agents_for_user(
                agents_metadata,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception:
            await emit_route_stage(
                on_progress,
                "candidate_catalog",
                "获取可用专家",
                status="error",
                details="获取可用专家失败",
                    execution_time_ms=(time.perf_counter() - candidate_started) * 1000,
            )
            raise

        await emit_route_stage(
            on_progress,
            "candidate_catalog",
            "获取可用专家",
            status="success",
            details="已完成可路由专家授权检查",
            execution_time_ms=(time.perf_counter() - candidate_started) * 1000,
        )
        if not agents_metadata:
            logger.warning("No routeable agents available for user %s.", user_id)
            return None

        from app.services.ai.intent_service import looks_like_knowledge_query

        knowledge_catalog: Optional[AuthorizedKnowledgeCatalog] = None
        if looks_like_knowledge_query(user_input):
            knowledge_started = time.perf_counter()
            await emit_route_stage(
                on_progress,
                "knowledge_catalog",
                "准备知识资源范围",
                status="pending",
            )
            try:
                knowledge_catalog = await load_authorized_knowledge_catalog(
                    user_id=user_id,
                    user_name=user_name,
                    is_admin=is_admin,
                )
            except Exception:
                await emit_route_stage(
                    on_progress,
                    "knowledge_catalog",
                    "准备知识资源范围",
                    status="error",
                    details="知识资源范围准备失败",
                    execution_time_ms=(time.perf_counter() - knowledge_started) * 1000,
                )
                raise
            await emit_route_stage(
                on_progress,
                "knowledge_catalog",
                "准备知识资源范围",
                status="success",
                details="已完成授权知识资源检查",
                execution_time_ms=(time.perf_counter() - knowledge_started) * 1000,
            )

        preflight_request_decision = self._request_decision_from_intent(
            user_input,
            None,
            False,
            knowledge_catalog=knowledge_catalog,
        )

        # 结构短路：权限过滤后仅剩 1 个候选，无需意图/路由 LLM。
        if len(agents_metadata) == 1:
            sole = agents_metadata[0]
            if (
                self._is_knowledge_agent(agents_metadata, sole.get("name"))
                and preflight_request_decision.knowledge_catalog_status
                and not preflight_request_decision.knowledge_fallback_allowed
                and preflight_request_decision.source == RequestSource.GENERAL
            ):
                fallback_agent = self._find_fallback_agent(agents_metadata)
                if fallback_agent and fallback_agent.get("id") != sole.get("id"):
                    return self._route_to_sole_candidate(
                        fallback_agent,
                        reasoning="授权知识库目录未高置信匹配，避免把请求短路到知识库智能体",
                        request_decision=preflight_request_decision,
                    )
            logger.info(
                "Sole candidate shortcut (post-permission): routing to %s without LLM",
                sole.get("name"),
            )
            return self._route_to_sole_candidate(
                sole,
                reasoning="权限过滤后仅剩唯一可路由智能体，结构短路跳过路由 LLM",
                request_decision=preflight_request_decision,
            )

        from app.services.ai.intent_service import (
            DataSessionAffinity,
            looks_like_accessible_resource_catalog_query,
            looks_like_data_followup,
            looks_like_greeting,
            looks_like_web_search_query,
            resolve_data_agent_session_affinity,
            should_inherit_data_agent_session,
        )

        if looks_like_greeting(user_input):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Greeting shortcut: routing to %s without LLM",
                    fallback_agent["name"],
                )
                return TurnDecision.from_router_components(
                    agent_id=fallback_agent["id"],
                    agent_name=fallback_agent.get("name"),
                    confidence=0.95,
                    reasoning="纯问候/寒暄，启发式短路至通用助手（跳过路由 LLM）",
                    turn_labels=["general_chat"],
                    relation_to_previous="standalone",
                    user_action_type="chat",
                )

        if looks_like_accessible_resource_catalog_query(user_input):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Resource catalog shortcut: routing to %s without LLM",
                    fallback_agent["name"],
                )
                return TurnDecision.from_router_components(
                    agent_id=fallback_agent["id"],
                    agent_name=fallback_agent.get("name"),
                    confidence=0.94,
                    reasoning="权限内数据集/知识库资源目录询问，启发式短路至通用助手（跳过路由 LLM）",
                    turn_labels=["meta_action", "general_chat"],
                    relation_to_previous="standalone",
                    user_action_type="manage_agent_or_skill",
                )

        if looks_like_web_search_query(user_input):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Web search shortcut: routing to %s without LLM",
                    fallback_agent["name"],
                )
                return TurnDecision.from_router_components(
                    agent_id=fallback_agent["id"],
                    agent_name=fallback_agent.get("name"),
                    confidence=0.92,
                    reasoning="联网/外部公网检索请求，启发式短路至通用助手",
                    turn_labels=["topic_switch", "general_chat"],
                    relation_to_previous="topic_switch",
                    user_action_type="chat",
                )

        # 业务确认回执（确定/取消）：协议固定前缀，会话粘性沿用上一轮智能体，跳过路由 LLM。
        from app.services.ai.business_confirmation import is_business_confirmation_receipt_message

        if last_agent_name and is_business_confirmation_receipt_message(user_input):
            sticky_agent = self._match_agent(last_agent_name, agents_metadata)
            if sticky_agent:
                logger.info(
                    "Business confirmation receipt shortcut: sticky route to %s without LLM",
                    sticky_agent["name"],
                )
                return TurnDecision.from_router_components(
                    agent_id=sticky_agent["id"],
                    agent_name=sticky_agent.get("name"),
                    confidence=0.99,
                    reasoning=(
                        "业务确认回执（确定/取消），会话粘性沿用上一轮智能体（跳过路由 LLM）"
                    ),
                    turn_labels=["business_confirmation_receipt", "follow_up"],
                    relation_to_previous="follow_up",
                    user_action_type="chat",
                )

        from app.services.ai.user_question import is_user_question_receipt_message

        if last_agent_name and is_user_question_receipt_message(user_input):
            sticky_agent = self._match_agent(last_agent_name, agents_metadata)
            if sticky_agent:
                logger.info(
                    "User question receipt shortcut: sticky route to %s without LLM",
                    sticky_agent["name"],
                )
                return TurnDecision.from_router_components(
                    agent_id=sticky_agent["id"],
                    agent_name=sticky_agent.get("name"),
                    confidence=0.99,
                    reasoning="AI 提问回执沿用上一轮智能体（跳过路由 LLM）",
                    turn_labels=["user_question_receipt", "follow_up"],
                    relation_to_previous="follow_up",
                    user_action_type="chat",
                )

        if (
            last_agent_name
            and self._is_data_query_agent(agents_metadata, last_agent_name)
            and resolve_data_agent_session_affinity(user_input) == DataSessionAffinity.BREAK
        ):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Data-agent session break: routing to %s without LLM (last=%s)",
                    fallback_agent["name"],
                    last_agent_name,
                )
                return TurnDecision.from_router_components(
                    agent_id=fallback_agent["id"],
                    agent_name=fallback_agent.get("name"),
                    confidence=0.9,
                    reasoning=(
                        "上一轮虽由数据智能体处理，但本轮不再具备内部业务库查数或数据追问信号，"
                        "按话题切换重新分诊至通用助手"
                    ),
                    turn_labels=["topic_switch", "general_chat"],
                    relation_to_previous="topic_switch",
                    user_action_type="chat",
                )

        # 纯结果追问不需要重新做“意图识别 + 路由选择”：上一轮 Agent 已经
        # 通过权限过滤，且本轮只复用已有 ChatBI 结果，不会引入新的数据范围。
        # 新查数请求不会命中 looks_like_data_followup，仍保留完整资格校验。
        if last_agent_name and looks_like_data_followup(user_input):
            sticky_agent = self._match_agent(last_agent_name, agents_metadata)
            if sticky_agent and self._is_data_query_agent(agents_metadata, last_agent_name):
                request_decision = resolve_request_decision(
                    user_input,
                    has_last_data_result=True,
                )
                if request_decision.allows_data_route:
                    request_decision = apply_chatbi_qualification(
                        request_decision,
                        qualify_chatbi_request(
                            domain="conversation_context",
                            operation="transform",
                            dataset_candidates=[],
                            previous_chatbi_result=True,
                        ),
                    )
                    logger.info(
                        "Data-result follow-up shortcut: sticky route to %s without intent/router LLM",
                        sticky_agent.get("name"),
                    )
                    return self._route_to_sole_candidate(
                        sticky_agent,
                        reasoning="上一轮 ChatBI 结果追问，沿用已授权智能体并跳过意图/路由 LLM",
                        request_decision=request_decision,
                        turn_labels=["continuation_followup", "business_related", "same_topic"],
                        relation_to_previous="followup",
                        user_action_type="transform_context",
                    )

        # 通用追问粘性短路（Fix 2）：上一轮非数据智能体 + 本轮为高置信延续追问
        # → 直接粘性沿用，跳过路由 LLM。
        # 设计保守：looks_like_general_followup 仅覆盖极短的指代/延续句，
        # 含任何业务数据信号时自动阻断，不影响需要切换智能体的新话题。
        from app.services.ai.intent_service import looks_like_general_followup

        if (
            last_agent_name
            and not self._is_data_query_agent(agents_metadata, last_agent_name)
            and looks_like_general_followup(user_input)
        ):
            sticky_agent = self._match_agent(last_agent_name, agents_metadata)
            if sticky_agent:
                logger.info(
                    "General followup shortcut: sticky route to %s without LLM (last=%s)",
                    sticky_agent["name"],
                    last_agent_name,
                )
                return TurnDecision.from_router_components(
                    agent_id=sticky_agent["id"],
                    agent_name=sticky_agent.get("name"),
                    confidence=0.93,
                    reasoning="通用追问/指代延续，启发式短路粘性沿用上一轮智能体（跳过路由 LLM）",
                    turn_labels=["continuation_followup", "same_topic"],
                    relation_to_previous="followup",
                    user_action_type="chat",
                )

        # 不再单独打意图 LLM：启发式先开闸，意图字段由同一次路由 JSON 带回后再校验。
        intent_info = None
        previous_chatbi_result = bool(
            last_agent_name
            and self._is_data_query_agent(agents_metadata, last_agent_name)
            and should_inherit_data_agent_session(user_input)
        )
        request_decision = self._request_decision_from_intent(
            user_input,
            intent_info,
            previous_chatbi_result,
            knowledge_catalog=knowledge_catalog,
        )
        source_frame = self._source_frame_from_request_decision(request_decision)
        data_route_allowed = self._data_route_allowed(
            request_decision,
            last_agent_name=last_agent_name,
            agents_metadata=agents_metadata,
            user_input=user_input,
        )
        routing_agents = self._constrain_candidates_by_intent(
            agents_metadata,
            intent_info,
            source_frame=source_frame,
            data_route_allowed=bool(data_route_allowed),
            request_decision=request_decision,
        )

        # 结构短路：候选收缩后仅剩 1 个，跳过路由 LLM。
        if len(routing_agents) == 1:
            sole = routing_agents[0]
            logger.info(
                "Sole candidate shortcut (post-constrain): routing to %s without route LLM",
                sole.get("name"),
            )
            return self._route_to_sole_candidate(
                sole,
                reasoning="意图约束后仅剩唯一可路由智能体，结构短路跳过路由 LLM",
                intent_info=intent_info,
                request_decision=request_decision,
            )

        # 2. Unified LLM Routing
        # 路由提示词内置在代码中（COMPACT_SYSTEM_PROMPT），不再从数据库配置读取，
        # 避免运营在配置页误改导致路由失准。
        system_prompt = self.COMPACT_SYSTEM_PROMPT
        agents_str = self._build_agents_context(routing_agents)
        if knowledge_catalog is None:
            # 目录摘要本来就在该路径加载；保留结构化快照，避免 LLM 返回
            # KNOWLEDGE_BASE 后再次读取权限目录。
            knowledge_started = time.perf_counter()
            await emit_route_stage(
                on_progress,
                "knowledge_catalog",
                "准备知识资源范围",
                status="pending",
            )
            try:
                knowledge_catalog = await load_authorized_knowledge_catalog(
                    user_id=user_id,
                    user_name=user_name,
                    is_admin=is_admin,
                )
            except Exception:
                await emit_route_stage(
                    on_progress,
                    "knowledge_catalog",
                    "准备知识资源范围",
                    status="error",
                    details="知识资源范围准备失败",
                    execution_time_ms=(time.perf_counter() - knowledge_started) * 1000,
                )
                raise
            await emit_route_stage(
                on_progress,
                "knowledge_catalog",
                "准备知识资源范围",
                status="success",
                details="已完成授权知识资源检查",
                execution_time_ms=(time.perf_counter() - knowledge_started) * 1000,
            )
        catalog_kwargs = {
            "user_id": user_id,
            "user_name": user_name,
            "is_admin": is_admin,
        }
        if knowledge_catalog is not None:
            catalog_kwargs["knowledge_catalog"] = knowledge_catalog
        accessible_resources_context = await build_accessible_resource_catalog(
            **catalog_kwargs,
        )
        history_str = self._build_history_context(
            history,
            last_agent_name,
            user_input=user_input,
            agents_metadata=routing_agents,
            early_context=early_context,
        )
        
        fallback_agent_name = self._resolve_fallback_agent_name(routing_agents)
        formatted_prompt = (
            system_prompt
            .replace("{agents_context}", agents_str)
            .replace(
                "{accessible_resources_context}",
                accessible_resources_context or "（当前没有可展示的资源摘要）",
            )
            .replace("{history_context}", history_str)
            .replace("{fallback_agent_name}", fallback_agent_name)
        )
        messages = [
            RuntimeMessage(
                role="system",
                content=[RuntimeContentBlock(type="text", text=formatted_prompt)],
            ),
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text=f"Latest User Query: {user_input}")],
            ),
        ]

        # 3. Invoke LLM with one retry to avoid silently dropping a valid
        # business query to general-chat on transient errors / malformed JSON.
        router_model_started = time.perf_counter()
        await emit_route_stage(
            on_progress,
            "router_model",
            "匹配目标专家",
            status="pending",
        )
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                llm = await get_llm_async(
                    temperature=0.0,
                    max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
                    ignore_session_reasoning_overrides=True,
                    source="router",
                )  # Use deterministic output
                chat_client = chat_client_from_handle(llm)
                attempt_messages = messages
                if attempt > 0:
                    # 第二次用极简提示，强制先写 agent_name，降低截断重伤概率。
                    # Fix 3: 同时注入 last_agent_name，避免 retry 时丢失粘性上下文。
                    last_agent_hint = (
                        f"\n上一轮由智能体 `{last_agent_name}` 处理，若本轮为追问/指代请优先沿用。"
                        if last_agent_name
                        else ""
                    )
                    compact_prompt = (
                        "你是路由助手。只返回一行纯 JSON，字段顺序固定为："
                        '{"agent_name":"...","confidence":0.9,"secondary_agents":[],'
                        '"turn_labels":["ambiguous"],"relation_to_previous":"unknown",'
                        '"user_action_type":"unknown","intent":"...","domain":"...",'
                        '"intent_confidence":0.0,"intent_reasoning":"...","thought":"短理由"}。'
                        f"agent_name 必须是清单中的英文 name；不确定时用 {fallback_agent_name}。"
                        "必须根据当前问题重新判断 intent 与 domain，禁止照抄 GENERAL/unknown 占位。"
                        "intent 为 DATA_QUERY/KNOWLEDGE_BASE/GENERAL/UNKNOWN；"
                        "domain 为 chatbi_business_data/local_file/public_web/runtime_environment/"
                        "internal_docs/general/conversation_context/unknown。"
                        "intent_confidence 是来源意图把握，不是 agent_name 的 confidence。"
                        f"{last_agent_hint}"
                        f"\n可用智能体：\n{agents_str}"
                        f"\n当前用户可访问的内部资源摘要：\n"
                        f"{accessible_resources_context or '（当前没有可展示的资源摘要）'}\n"
                        "如果问题明确匹配知识库名称或摘要，优先标记 internal_docs；"
                        "弱关键词重合时不要强行改路由。"
                    )
                    attempt_messages = [
                        RuntimeMessage(
                            role="system",
                            content=[RuntimeContentBlock(type="text", text=compact_prompt)],
                        ),
                        RuntimeMessage(
                            role="user",
                            content=[
                                RuntimeContentBlock(
                                    type="text",
                                    text=f"Latest User Query: {user_input}",
                                )
                            ],
                        ),
                    ]
                try:
                    result_json = await asyncio.wait_for(
                        chat_client.generate_structured_dict(
                            attempt_messages,
                            LLMRouterResponse,
                        ),
                        timeout=DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[Router] LLM structured routing call timed out after %ss (attempt %s)",
                        DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS,
                        attempt + 1,
                    )
                    raise TimeoutError(f"Router LLM generate_structured_dict timed out after {DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS}s")

                content = ""
                if result_json is None:
                    structured_status = getattr(
                        chat_client,
                        "last_structured_output_status",
                        None,
                    )
                    if structured_status in {"error", "invalid"}:
                        raise ValueError(
                            f"Structured router output failed ({structured_status})"
                        )
                    # Legacy/custom clients without native structured output keep
                    # one compatibility text call; native structured clients never
                    # pay for a second request after a structured-output failure.
                    try:
                        content = (await asyncio.wait_for(
                            chat_client.generate_text(attempt_messages),
                            timeout=max(5.0, DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS / 2),
                        )).strip()
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[Router] LLM text routing call timed out (attempt %s)",
                            attempt + 1,
                        )
                        raise TimeoutError("Router LLM generate_text timed out")
                    result_json = self._parse_router_json(content)
                else:
                    content = json.dumps(result_json, ensure_ascii=False)
                    logger.info(
                        "LLM Routing used AgentScope structured_output (attempt %s)",
                        attempt + 1,
                    )
                if result_json is None:
                    raise ValueError(f"Unparseable router response: {content[:200]!r}")

                logger.info(f"LLM Routing Response (attempt {attempt + 1}): {content}")
                (
                    intent_info,
                    request_decision,
                    source_frame,
                    data_route_allowed,
                ) = await self._apply_semantic_evidence_from_router(
                    user_input=user_input,
                    result_json=result_json,
                    last_agent_name=last_agent_name,
                    agents_metadata=agents_metadata,
                    previous_chatbi_result=previous_chatbi_result,
                    user_id=user_id,
                    user_name=user_name,
                    is_admin=is_admin,
                    knowledge_catalog=knowledge_catalog,
                )
                decision = self._build_turn_decision(
                    result_json,
                    routing_agents,
                    enable_multi_agent,
                    fallback_agents_metadata=agents_metadata,
                    intent_info=intent_info,
                    source_frame=source_frame,
                    request_decision=request_decision,
                    data_route_allowed=bool(data_route_allowed),
                )
                if decision is not None:
                    await emit_route_stage(
                        on_progress,
                        "router_model",
                        "匹配目标专家",
                        status="success",
                        details="已完成目标专家匹配",
                        execution_time_ms=(time.perf_counter() - router_model_started) * 1000,
                    )
                    return decision.model_copy(
                        update={"accessible_resources": accessible_resources_context or None}
                    )
                fallback_decision = self._fallback_to_general(
                    agents_metadata,
                    "Router returned no eligible candidate",
                    intent_info=intent_info,
                    request_decision=request_decision,
                )
                if fallback_decision is not None:
                    await emit_route_stage(
                        on_progress,
                        "router_model",
                        "匹配目标专家",
                        status="success",
                        details="已使用安全路由兜底",
                        execution_time_ms=(time.perf_counter() - router_model_started) * 1000,
                    )
                    return fallback_decision.model_copy(
                        update={"accessible_resources": accessible_resources_context or None}
                    )
                await emit_route_stage(
                    on_progress,
                    "router_model",
                    "匹配目标专家",
                    status="error",
                    details="未找到可用目标专家",
                    execution_time_ms=(time.perf_counter() - router_model_started) * 1000,
                )
                return None
            except Exception as e:  # noqa: BLE001 - retry then fall back
                last_error = e
                logger.warning(f"Routing attempt {attempt + 1} failed: {e}")

        logger.error(f"Routing failed after retries: {last_error}. Falling back to General Chat.")
        fallback_decision = self._fallback_to_general(
            agents_metadata,
            f"Routing exception ({str(last_error)})",
            intent_info=intent_info,
            request_decision=request_decision,
        )
        if fallback_decision is not None:
            await emit_route_stage(
                on_progress,
                "router_model",
                "匹配目标专家",
                status="success",
                details="路由模型重试后使用安全兜底",
                execution_time_ms=(time.perf_counter() - router_model_started) * 1000,
            )
            return fallback_decision.model_copy(
                update={"accessible_resources": accessible_resources_context or None}
            )
        await emit_route_stage(
            on_progress,
            "router_model",
            "匹配目标专家",
            status="error",
            details="目标专家匹配失败",
            execution_time_ms=(time.perf_counter() - router_model_started) * 1000,
        )
        return None

    @staticmethod
    def _request_decision_from_intent(
        user_input: str,
        intent_info: Optional[IntentResponse],
        previous_chatbi_result: bool,
        knowledge_catalog: Optional[AuthorizedKnowledgeCatalog] = None,
    ) -> RequestDecision:
        return resolve_request_decision(
            user_input,
            semantic_intent=getattr(intent_info, "intent", None),
            semantic_confidence=getattr(intent_info, "confidence", None),
            semantic_domain=getattr(intent_info, "domain", None),
            semantic_operation=getattr(intent_info, "operation", None),
            fact_kind=getattr(intent_info, "fact_kind", None),
            freshness_requirement=getattr(intent_info, "freshness_requirement", None),
            time_scope=getattr(intent_info, "time_scope", None),
            reference_mode=getattr(intent_info, "reference_mode", None),
            needs_fresh_data=getattr(intent_info, "needs_fresh_data", None),
            has_last_data_result=previous_chatbi_result,
            knowledge_catalog=knowledge_catalog,
        )

    def _data_route_allowed(
        self,
        request_decision: RequestDecision,
        *,
        last_agent_name: Optional[str],
        agents_metadata: List[dict],
        user_input: str,
    ) -> bool:
        from app.services.ai.intent_service import should_inherit_data_agent_session

        return bool(
            request_decision.allows_data_route
            or (
                last_agent_name
                and self._is_data_query_agent(agents_metadata, last_agent_name)
                and should_inherit_data_agent_session(user_input)
            )
        )

    @classmethod
    def _intent_from_router_payload(cls, result_json: dict) -> Optional[IntentResponse]:
        """Parse intent/domain fields from the combined router JSON.

        Routing ``confidence`` / ``thought`` are agent-selection scores and must
        not be reused as intent evidence. Placeholder GENERAL/unknown values
        from the compact retry example are ignored.
        """
        if not isinstance(result_json, dict):
            return None
        raw_intent = str(result_json.get("intent") or "").strip().upper()
        domain = str(result_json.get("domain") or "").strip().lower() or "unknown"
        operation = str(result_json.get("operation") or "").strip().lower() or "unknown"
        intent_confidence_raw = result_json.get("intent_confidence")
        has_intent_confidence = (
            intent_confidence_raw is not None
            and str(intent_confidence_raw).strip() != ""
        )
        if not raw_intent and domain == "unknown" and operation == "unknown":
            return None
        if (
            raw_intent in cls._PLACEHOLDER_INTENTS
            and domain in cls._PLACEHOLDER_DOMAINS
            and operation in {"", "unknown"}
            and not has_intent_confidence
        ):
            return None
        intent = (
            IntentType(raw_intent)
            if raw_intent in cls._INTENT_VALUES
            else IntentType.UNKNOWN
        )
        confidence = 0.0
        if has_intent_confidence:
            try:
                confidence = float(intent_confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
        reasoning = str(result_json.get("intent_reasoning") or "").strip()
        needs_fresh = result_json.get("needs_fresh_data")
        if isinstance(needs_fresh, str):
            needs_fresh = needs_fresh.strip().lower() in {"true", "1", "yes"}
        elif needs_fresh is not None:
            needs_fresh = bool(needs_fresh)
        time_scope = result_json.get("time_scope")
        if time_scope is not None:
            time_scope = str(time_scope).strip() or None
        return IntentResponse(
            intent=intent,
            confidence=confidence,
            reasoning=reasoning,
            entities=[],
            domain=domain,
            operation=operation,
            fact_kind=str(result_json.get("fact_kind") or "unknown").strip() or "unknown",
            freshness_requirement=(
                str(result_json.get("freshness_requirement") or "unknown").strip()
                or "unknown"
            ),
            time_scope=time_scope,
            reference_mode=(
                str(result_json.get("reference_mode") or "unknown").strip() or "unknown"
            ),
            needs_fresh_data=needs_fresh,
        )

    async def _apply_semantic_evidence_from_router(
        self,
        *,
        user_input: str,
        result_json: dict,
        last_agent_name: Optional[str],
        agents_metadata: List[dict],
        previous_chatbi_result: bool,
        user_id: Optional[int],
        user_name: Optional[str],
        is_admin: bool,
        knowledge_catalog: Optional[AuthorizedKnowledgeCatalog] = None,
    ) -> tuple:
        intent_info = self._intent_from_router_payload(result_json)
        if (
            knowledge_catalog is None
            and intent_info is not None
            and (
                intent_info.intent == IntentType.KNOWLEDGE_BASE
                or str(getattr(intent_info, "domain", "") or "").strip().lower()
                == "internal_docs"
            )
        ):
            knowledge_catalog = await load_authorized_knowledge_catalog(
                user_id=user_id,
                user_name=user_name,
                is_admin=is_admin,
            )
        request_decision = self._request_decision_from_intent(
            user_input,
            intent_info,
            previous_chatbi_result,
            knowledge_catalog=knowledge_catalog,
        )
        if intent_info is None:
            source_frame = self._source_frame_from_request_decision(request_decision)
            data_route_allowed = self._data_route_allowed(
                request_decision,
                last_agent_name=last_agent_name,
                agents_metadata=agents_metadata,
                user_input=user_input,
            )
            # 占位/未知语义必须失败关闭。只有现有会话继承、统一决策已明确放行，
            # 或 UNKNOWN 请求具备明确业务数据对象信号时，才允许保留 ChatBI 路由。
            if (
                not data_route_allowed
                and request_decision.source == RequestSource.UNKNOWN
            ):
                data_route_allowed = looks_like_strong_business_data_request(user_input)
            return intent_info, request_decision, source_frame, data_route_allowed

        chatbi_qualification = await self._resolve_chatbi_qualification(
            user_input,
            intent_info,
            request_decision,
            previous_chatbi_result=previous_chatbi_result,
            user_id=user_id,
            is_admin=is_admin,
        )
        request_decision = apply_chatbi_qualification(
            request_decision,
            chatbi_qualification,
        )
        source_frame = self._source_frame_from_request_decision(request_decision)
        data_route_allowed = self._data_route_allowed(
            request_decision,
            last_agent_name=last_agent_name,
            agents_metadata=agents_metadata,
            user_input=user_input,
        )
        return intent_info, request_decision, source_frame, data_route_allowed

    @staticmethod
    async def _resolve_chatbi_qualification(
        user_input: str,
        intent_info: Optional[IntentResponse],
        request_decision: RequestDecision,
        *,
        previous_chatbi_result: bool,
        user_id: Optional[int],
        is_admin: bool,
    ) -> ChatBIQualification:
        """Resolve source + authorized metadata evidence before ChatBI routing."""
        domain = str(getattr(intent_info, "domain", "unknown") or "unknown").strip().lower()
        operation = str(getattr(intent_info, "operation", "unknown") or "unknown").strip().lower()

        # Preserve the stronger deterministic boundaries even if the semantic
        # model returns an over-broad domain.
        if request_decision.source == RequestSource.RUNTIME_DIAGNOSTIC:
            domain = "runtime_environment"
        elif request_decision.source == RequestSource.PUBLIC_WEB:
            domain = "public_web"
        elif request_decision.source == RequestSource.INTERNAL_DOCS:
            domain = "internal_docs"

        # Existing embedders may construct IntentResponse without the new
        # domain field.  Keep those callers compatible; parsed legacy model
        # responses are marked ``unqualified_data_intent`` and remain closed.
        if (
            domain == "unknown"
            and getattr(intent_info, "intent", None) == IntentType.DATA_QUERY
            and request_decision.source == RequestSource.INTERNAL_STRUCTURED_DATA
        ):
            domain = "chatbi_business_data"

        candidates = []
        if domain == "chatbi_business_data":
            candidates = await resolve_authorized_dataset_candidates(
                user_input,
                user_id=user_id,
                is_admin=is_admin,
                top_k=3,
            )

        return qualify_chatbi_request(
            domain=domain,
            operation=operation,
            dataset_candidates=candidates,
            previous_chatbi_result=previous_chatbi_result,
            explicit_dataset=bool(getattr(intent_info, "explicit_dataset", False)),
        )

    def _should_constrain_to_data_agents(
        self,
        intent_info: Optional[IntentResponse],
        source_frame: Optional[IntentSourceFrame],
        data_route_allowed: bool,
        request_decision: Optional[RequestDecision] = None,
    ) -> bool:
        """是否满足将候选收缩到数据查询智能体的全部条件。

        须同时满足：
        1. 语义意图为 DATA_QUERY 且置信度超过模糊阈值（排除低置信误判）；
        2. 意图来源确认为内部结构化数据（排除公网查询 / 公司信息等误判）；
        3. 路由层允许进入数据查询路径（排除会话粘性以外的追问被强制推进）。
        """
        if not intent_info or intent_info.intent != IntentType.DATA_QUERY:
            return False
        if intent_info.confidence < AMBIGUOUS_INTENT_CONFIDENCE_THRESHOLD:
            return False
        if source_frame is None or source_frame.source != IntentSource.INTERNAL_STRUCTURED_DATA:
            return False
        if request_decision is not None and request_decision.chatbi_mode not in {
            None,
            ChatBIMode.DIRECT.value,
        }:
            return False
        return data_route_allowed

    def _constrain_candidates_by_intent(
        self,
        agents: List[dict],
        intent_info: Optional[IntentResponse],
        *,
        source_frame: Optional[IntentSourceFrame] = None,
        data_route_allowed: bool = False,
        request_decision: Optional[RequestDecision] = None,
    ) -> List[dict]:
        """Constrain to data agents only when the request source is truly data."""
        if not self._should_constrain_to_data_agents(
            intent_info,
            source_frame,
            data_route_allowed,
            request_decision,
        ):
            return agents

        data_agents = [
            agent
            for agent in agents
            if self._is_data_query_agent(agents, agent.get("name"))
        ]
        return data_agents or agents

    def _build_history_context(
        self,
        history: Optional[List[dict]],
        last_agent_name: Optional[str],
        *,
        user_input: str = "",
        agents_metadata: Optional[List[dict]] = None,
        early_context: Optional[str] = None,
    ) -> str:
        """Build a denoised, truncated history context plus session-affinity hint.

        - 注入"上一轮处理者"，让追问/指代类输入可沿用上一轮智能体。
        - 若上一轮是 data_query 但本轮不再满足粘性条件，显式提示勿机械沿用。
        - 历史逐条截断并剥离表格/图表/代码块，避免大段业务输出淹没路由信号。
        - early_context: 更早轮次（已压缩溢出）的跨轮摘录，置于历史之前的背景区。
        """
        parts: List[str] = []

        if early_context:
            parts.append(
                "### 更早轮次要点 (Earlier Background)\n"
                f"以下是本会话更早轮次被压缩的要点摘录，供你把握整体会话背景"
                f"（这是历史背景，不是用户当前本轮的问题，请勿逐条复述）：\n{early_context}"
            )

        if last_agent_name:
            from app.services.ai.intent_service import should_inherit_data_agent_session

            break_data_affinity = (
                agents_metadata is not None
                and self._is_data_query_agent(agents_metadata, last_agent_name)
                and user_input
                and not should_inherit_data_agent_session(user_input)
            )
            if break_data_affinity:
                parts.append(
                    "### 上一轮路由 (Previous Turn)\n"
                    f"上一轮由智能体 `{last_agent_name}` 处理。\n"
                    "但本轮输入**未表现出**内部业务库查数或数据结果追问信号；"
                    f"**禁止**仅因上一轮是数据智能体就机械沿用，应重新分诊最匹配的智能体。"
                )
            else:
                parts.append(
                    "### 上一轮路由 (Previous Turn)\n"
                    f"上一轮由智能体 `{last_agent_name}` 处理。\n"
                    f"若本轮为追问/指代/省略主语且无明确的新领域意图，应优先沿用 `{last_agent_name}`。"
                )

        condensed = self._condense_history(history)
        if condensed:
            parts.append(
                "Conversation History (recent rounds):\n"
                + "\n".join(condensed)
                + "\n\nAnalyze the user's latest query in the context of this conversation."
            )

        return "\n\n".join(parts)

    def _condense_history(self, history: Optional[List[dict]], max_rounds: int = 6, max_chars: int = 200) -> List[str]:
        if not history:
            return []
        recent = history[-max_rounds:] if len(history) > max_rounds else history
        lines: List[str] = []
        for msg in recent:
            role = msg.get("role", "unknown")
            if role == "system":
                continue
            content = self._strip_noise(msg.get("content", "") or "")
            if not content:
                continue
            if len(content) > max_chars:
                content = content[:max_chars] + "…(已截断)"
            label = role.capitalize()
            if role == "assistant" and msg.get("agent_name"):
                label = f"Assistant[{msg['agent_name']}]"
            lines.append(f"- {label}: {content}")
        return lines

    @staticmethod
    def _strip_noise(text: str) -> str:
        """Remove fenced blocks (chart/json/code) and markdown tables that drown routing signal."""
        import re
        text = re.sub(r"```.*?```", "[代码/图表块已省略]", text, flags=re.DOTALL)
        kept = []
        for ln in text.splitlines():
            stripped = ln.strip()
            if not stripped:
                continue
            # Skip markdown table rows and separator lines.
            if re.match(r"^\|.*\|$", stripped) or re.match(r"^\|?\s*:?-{3,}", stripped):
                continue
            kept.append(stripped)
        return " ".join(kept).strip()

    @staticmethod
    def _parse_router_json(content: str) -> Optional[dict]:
        if not content:
            return None
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                content = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            content = content.strip()
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            import re
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
            # 截断场景：优先捞 agent_name / confidence，避免整轮重试白跑。
            name_match = re.search(r'"agent_name"\s*:\s*"([^"\\]+)"', content)
            if not name_match:
                return None
            recovered: dict = {
                "agent_name": name_match.group(1).strip(),
                "thought": "recovered from truncated router JSON",
                "secondary_agents": [],
                "turn_labels": ["ambiguous"],
                "relation_to_previous": "unknown",
                "user_action_type": "unknown",
            }
            conf_match = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', content)
            if conf_match:
                try:
                    recovered["confidence"] = float(conf_match.group(1))
                except ValueError:
                    recovered["confidence"] = 0.6
            else:
                recovered["confidence"] = 0.6
            return recovered
        return None

    def _match_agent(self, name: Optional[str], agents_metadata: List[dict]) -> Optional[dict]:
        """Resolve an agent name (slug) back to its metadata; fall back to display_name match."""
        if not name:
            return None
        name_l = str(name).lower().strip()
        for a in agents_metadata:
            if str(a.get("name", "")).lower() == name_l:
                return a
        for a in agents_metadata:
            if str(a.get("display_name", "")).lower() == name_l:
                return a
        return None

    _DATA_QUERY_CAPABILITY_KEYS = frozenset({
        "data_query",
        "sql_generation",
        "text-to-sql",
        "reporting",
    })

    def _is_data_query_agent(self, agents_metadata: List[dict], agent_name: Optional[str]) -> bool:
        agent = self._match_agent(agent_name, agents_metadata)
        if not agent:
            return False
        caps = agent.get("capabilities") or []
        if isinstance(caps, str):
            caps = [caps]
        cap_set = {str(cap).strip().lower() for cap in caps}
        return bool(cap_set & self._DATA_QUERY_CAPABILITY_KEYS)

    def _is_knowledge_agent(self, agents_metadata: List[dict], agent_name: Optional[str]) -> bool:
        """识别具备知识库执行能力的候选，用于目录未匹配时避免误短路。"""
        agent = self._match_agent(agent_name, agents_metadata)
        if not agent:
            return False
        capabilities = agent.get("capabilities") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        normalized = {str(value).strip().lower() for value in capabilities}
        return bool(normalized & {"knowledge_base", "knowledge_search", "rag"})

    @staticmethod
    def _source_frame_from_request_decision(decision: RequestDecision) -> IntentSourceFrame:
        source_map = {
            RequestSource.INTERNAL_STRUCTURED_DATA: IntentSource.INTERNAL_STRUCTURED_DATA,
            RequestSource.INTERNAL_DOCS: IntentSource.INTERNAL_DOCS,
            RequestSource.PUBLIC_WEB: IntentSource.PUBLIC_WEB,
            RequestSource.RUNTIME_DIAGNOSTIC: IntentSource.RUNTIME_DIAGNOSTIC,
        }
        return IntentSourceFrame(
            source=source_map.get(decision.source, IntentSource.UNKNOWN),
            confidence=decision.confidence,
            reasoning=decision.reasoning,
        )

    def _build_turn_decision(
        self,
        result_json: dict,
        agents_metadata: List[dict],
        enable_multi_agent: bool,
        *,
        fallback_agents_metadata: Optional[List[dict]] = None,
        intent_info: Optional[IntentResponse] = None,
        source_frame: Optional[IntentSourceFrame] = None,
        request_decision: Optional[RequestDecision] = None,
        data_route_allowed: bool = True,
    ) -> Optional[TurnDecision]:
        confidence = result_json.get("confidence", 0.5)
        target_name = result_json.get("agent_name")
        secondary_names = result_json.get("secondary_agents", []) or []
        reasoning = result_json.get("thought") or result_json.get("reasoning", "")
        turn_labels = self._normalize_turn_labels(result_json.get("turn_labels"))
        relation_to_previous = self._normalize_choice(
            result_json.get("relation_to_previous"),
            self.ALLOWED_RELATIONS,
            "unknown",
        )
        user_action_type = self._normalize_choice(
            result_json.get("user_action_type"),
            self.ALLOWED_ACTION_TYPES,
            "unknown",
        )

        # --- Confidence & Fallback Mechanism ---
        if confidence < 0.6:
            logger.info(f"Low confidence ({confidence}) for '{target_name}'. Falling back to General Chat.")
            return self._fallback_to_general(
                agents_metadata,
                f"Low confidence ({confidence}) for agent selection. Reason: {reasoning}",
                intent_info=intent_info,
                request_decision=request_decision,
            )

        target_agent = self._match_agent(target_name, agents_metadata)
        if not target_agent:
            logger.warning(f"Router suggested unknown agent: {target_name}. Falling back to General Chat.")
            return self._fallback_to_general(
                fallback_agents_metadata or agents_metadata,
                f"Router returned unknown agent: {target_name}",
                intent_info=intent_info,
                request_decision=request_decision,
            )

        if (
            request_decision is not None
            and self._is_knowledge_agent(agents_metadata, target_agent.get("name"))
            and request_decision.knowledge_catalog_status
            and request_decision.source == RequestSource.GENERAL
        ):
            fallback_agents = fallback_agents_metadata or agents_metadata
            if self._find_fallback_agent(fallback_agents):
                logger.info(
                    "Router selected knowledge agent %s without catalog evidence; falling back to Main.",
                    target_agent.get("name"),
                )
                return self._fallback_to_general(
                    fallback_agents,
                    "授权知识库目录未提供高置信匹配，保留一次直接检索兜底但不进入知识库智能体",
                    intent_info=intent_info,
                    request_decision=request_decision,
                )

        if self._is_data_query_agent(agents_metadata, target_agent.get("name")) and not data_route_allowed:
            source_reason = getattr(source_frame, "reasoning", "unknown source")
            fallback_agents = fallback_agents_metadata or agents_metadata
            if self._find_fallback_agent(fallback_agents):
                logger.info(
                    "Router selected data agent %s without internal structured-data source (%s); falling back to Main.",
                    target_agent.get("name"),
                    source_reason,
                )
                return self._fallback_to_general(
                    fallback_agents,
                    f"Router selected data agent without internal structured-data source: {source_reason}",
                    intent_info=intent_info,
                    request_decision=request_decision,
                )
            logger.info(
                "Router selected data agent %s without internal structured-data source (%s), "
                "but no fallback Main agent is available; keeping router result.",
                target_agent.get("name"),
                source_reason,
            )

        resolved_secondaries: List[str] = []
        if enable_multi_agent and secondary_names:
            for s_name in secondary_names:
                s_agent = self._match_agent(s_name, agents_metadata)
                if s_agent and s_agent["id"] != target_agent["id"]:
                    if (
                        self._is_data_query_agent(agents_metadata, s_agent.get("name"))
                        and not data_route_allowed
                    ):
                        logger.info(
                            "Filtered data secondary agent %s without internal structured-data source",
                            s_agent.get("name"),
                        )
                        continue
                    resolved_secondaries.append(s_agent["id"])

        return TurnDecision.from_router_components(
            agent_id=target_agent["id"],
            agent_name=target_agent.get("name"),
            secondary_agents=resolved_secondaries,
            confidence=confidence,
            reasoning=reasoning,
            intent_info=intent_info,
            request_decision=request_decision,
            turn_labels=turn_labels,
            relation_to_previous=relation_to_previous,
            user_action_type=user_action_type,
            turn_kind=(
                "data_query"
                if self._is_data_query_agent(agents_metadata, target_agent.get("name"))
                and data_route_allowed
                else None
            ),
        )

    def _normalize_turn_labels(self, value) -> List[str]:
        if not isinstance(value, list):
            return []
        labels: List[str] = []
        for item in value:
            label = str(item or "").strip().lower()
            if label in self.ALLOWED_TURN_LABELS and label not in labels:
                labels.append(label)
        return labels

    @staticmethod
    def _normalize_choice(value, allowed: set, default: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in allowed else default

    async def _fetch_agents_from_db(self) -> List[dict]:
        from app.core.orm import AsyncSessionLocal
        from app.models.agent import AIAgent
        from sqlalchemy import select
        
        agents_metadata = []
        async with AsyncSessionLocal() as session:
            # Only select enabled agents AND system agents for routing
            result = await session.execute(
                select(AIAgent).where(AIAgent.is_enabled == True, AIAgent.is_system == True)
            )
            agents = result.scalars().all()
            for agent in agents:
                agents_metadata.append({
                    "id": agent.id,
                    "name": agent.name,
                    "display_name": agent.display_name or agent.name,  # 中文显示名，用于路由匹配兜底
                    "description": agent.description or "No description provided.",
                    "capabilities": agent.capabilities or []
                })
        return agents_metadata

    async def _filter_agents_for_user(
        self,
        agents: List[dict],
        *,
        user_id: Optional[int],
        is_admin: bool = False,
    ) -> List[dict]:
        if is_admin or not user_id:
            return agents

        try:
            from app.core.orm import AsyncSessionLocal
            from app.services.permission_service import PermissionService

            async with AsyncSessionLocal() as session:
                perms = await PermissionService(session).get_user_permissions(int(user_id))
                if "admin" in getattr(perms, "roles", []):
                    return agents
                allowed_agent_ids = set(getattr(perms.permissions, "agents", []) or [])
        except Exception as e:
            logger.warning("Failed to filter route agents for user %s: %s", user_id, e)
            return agents

        return [agent for agent in agents if agent.get("id") in allowed_agent_ids]

    def _build_agents_context(self, agents: List[dict]) -> str:
        agents_str = ""
        for agent in agents:
            display = agent.get("display_name", "")
            # 若有独立的中文显示名，在 Prompt 里一并提供，让 LLM 能用中文名路由
            name_info = f"{agent['name']}"
            if display and display != agent["name"]:
                name_info += f" (中文名: {display})"
            agents_str += f"- ID: {name_info} (UUID: {agent['id']})\n"
            agents_str += f"  Description: {agent['description']}\n"
            agents_str += f"  Capabilities: {agent['capabilities']}\n\n"
        return agents_str

    @classmethod
    def _find_fallback_agent(cls, agents: List[dict]) -> Optional[dict]:
        by_name = {str(a.get("name", "")).lower(): a for a in agents}
        for fallback_name in cls.FALLBACK_AGENT_NAMES:
            agent = by_name.get(fallback_name)
            if agent:
                return agent
        return None

    def _resolve_fallback_agent_name(self, agents: List[dict]) -> str:
        agent = self._find_fallback_agent(agents)
        if agent:
            return str(agent["name"])
        return self.FALLBACK_AGENT_NAMES[-1]

    def _route_to_sole_candidate(
        self,
        agent: dict,
        *,
        reasoning: str,
        intent_info: Optional[IntentResponse] = None,
        request_decision: Optional[RequestDecision] = None,
        turn_labels: Optional[List[str]] = None,
        relation_to_previous: str = "unknown",
        user_action_type: str = "chat",
    ) -> TurnDecision:
        """Route directly when structural filtering left exactly one agent."""
        return TurnDecision.from_router_components(
            agent_id=str(agent["id"]),
            agent_name=agent.get("name"),
            confidence=0.98,
            reasoning=reasoning,
            intent_info=intent_info,
            request_decision=request_decision,
            turn_labels=turn_labels or ["sole_candidate"],
            relation_to_previous=relation_to_previous,
            user_action_type=user_action_type,
        )

    def _fallback_to_general(
        self,
        agents: List[dict],
        reason: str,
        *,
        intent_info: Optional[IntentResponse] = None,
        request_decision: Optional[RequestDecision] = None,
    ) -> Optional[TurnDecision]:
        fallback_agent = self._find_fallback_agent(agents)
        if fallback_agent:
            return TurnDecision.from_router_components(
                agent_id=fallback_agent['id'],
                agent_name=fallback_agent.get("name"),
                confidence=0.1,
                reasoning=f"Fallback: {reason}",
                intent_info=intent_info,
                request_decision=request_decision,
                turn_labels=["ambiguous"],
                relation_to_previous="unknown",
                user_action_type="unknown",
                turn_kind="general",
            )
        return None

router_service = RouterService()

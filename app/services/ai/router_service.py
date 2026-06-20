from typing import List, Optional
import json
import logging
from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class RouteResult(BaseModel):
    agent_id: str
    secondary_agents: List[str] = []
    confidence: float
    reasoning: str
    turn_labels: List[str] = []
    relation_to_previous: str = "unknown"
    user_action_type: str = "unknown"

class LLMRouterResponse(BaseModel):
    """Internal structure for LLM output."""
    thought: str  # Reasoning/Coreference resolution
    agent_name: str
    secondary_agents: List[str] = []
    confidence: float
    turn_labels: List[str] = []
    relation_to_previous: str = "unknown"
    user_action_type: str = "unknown"

class RouterService:
    # 兜底通用助手 slug，按优先级匹配 DB 中 ai_agents.name（首个命中即用）
    FALLBACK_AGENT_NAMES = ("assistant", "main", "general-chat")

    DEFAULT_SYSTEM_PROMPT = """# Role: 云枢智能体平台 · 智能路由助手 (Smart Router V7 · 清单驱动)

你是智能体平台的"分诊台"。任务：依据【可用智能体清单】+【对话历史与上一轮路由】+【用户最新输入】，选出最合适的智能体。
你只输出路由决策 JSON，绝不回答业务问题本身。

## 1. 可用智能体清单 (唯一可选范围)
{agents_context}

## 2. 对话历史与上一轮路由
{history_context}

## 3. 决策步骤 (Reasoning Steps)

### Step 1 指代消解 (Coreference Resolution)
- 若输入含"它/这个/那个/刚才/上面/列表里第一个/继续/再/还有/也"等指代或省略主语，先结合历史还原其真实意图，再判断。
- 示例：上文在说"上海机房"，用户问"它有多少服务器？"，应识别出"它"=上海机房。

### Step 2 会话连续性优先 (Session Continuity) — 重要
- 若本轮是对上一轮的追问/补充/指代（如"可视化一下""再查下""那它呢""展开讲讲"），且【没有】出现明确属于其它智能体的新领域意图，则【优先沿用上一轮处理的智能体】。
- 仅当用户明确转向另一个领域时，才切换到别的智能体。
- 【禁止机械沿用】：若上一轮由 data_query 智能体处理，但本轮【不再】表现为内部业务库查数或数据结果追问（例如仅有「看看/情况/查一下」等弱探询、或已切换到与平台业务库无关的新话题），必须重新分诊，不得仅因会话连续性继续路由到 data_query 智能体。

### Step 3 语义匹配 (Semantic Matching)
- 逐一对照清单中每个智能体的 name / 中文名 / Description / Capabilities，选出职责与用户真实意图最吻合的那一个。
- 你对"领域/职责"的判断必须来自上面清单里的描述，【禁止】脑补清单之外的智能体或领域。
- 区分"业务数据指标查询"与"当前运行环境诊断"：
  - 用户询问当前系统/本机/这台机器/服务器运行状态、负载、CPU、内存、磁盘、进程、端口、网络连通性、服务状态、日志、或要求执行命令时，这是平台运行环境诊断/工具执行意图，应选择 {fallback_agent_name}。
  - 用户是查询、统计系统数据库中存储的结构化业务数据（如：PUE、温度、能耗、负载率等历史/业务指标，以及机房列表、设备清单、工单记录、告警/故障历史、操作日志等离散记录或数据列表）时，才按清单匹配数据查询类智能体。
  - 不要因为出现"负载/利用率/CPU/内存"等词就直接判为数据查询；必须先判断用户是在看"当前这台机器"还是查"业务/监控数据指标"。
- 区分"泛化查询词"与"内部业务查数"：
  - 天气、公共常识、外部事实、代码/API/编程问题、文本分析/改写/翻译、系统使用帮助等属于通用助手，即使带有「查询/查一下」也应选择 {fallback_agent_name}。
  - 凡未明确指向平台已接入业务库的结构化数据（记录/列表/指标/报表/SQL 明细），而更像第三方平台指标、公网资讯、个人项目/外部系统状态时，应选择 {fallback_agent_name}，不要路由到 data_query 智能体。
  - 内部 SOP/流程/规范/手册/怎么操作/处理步骤属于知识库或文档类智能体，即使带有「查询/查一下」也不要路由到数据查询类智能体。
  - 只有用户明确要内部业务数据的真实记录、列表、数量、指标、趋势、报表、SQL 明细时，才选择具备 data_query 能力的智能体。
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

## 4. 硬性约束 (Hard Constraints)
- agent_name 与 secondary_agents 中的每个值，【必须】与清单中某个智能体的 name 字段完全一致（英文 slug，如 chat-bi）。严禁使用中文名、领域名或清单里不存在的名称。
- 当没有任何业务智能体明显匹配（纯打招呼/闲聊/无法归类）时，选择兜底智能体 {fallback_agent_name}。
- confidence 表示你对"主智能体选择"的把握：能明确匹配某业务智能体时应 >= 0.7；只有完全无法归类时才走 {fallback_agent_name} 并给较低分。

## 5. 输出格式 (Output Format)
必须返回纯 JSON，严禁包含 Markdown 标记或额外文字。
{
  "thought": "1.指代消解结果 2.是否沿用上一轮及理由 3.命中清单里哪个/哪些 name 及理由",
  "agent_name": "清单中的某个 name",
  "secondary_agents": [],
  "confidence": 0.95,
  "turn_labels": ["continuation_followup", "business_related", "same_topic"],
  "relation_to_previous": "followup",
  "user_action_type": "transform_context"
}

## 6. 示例 (名称以"清单"为准，下例仅示意格式)
- 用户："你好" -> {"thought": "纯打招呼，无业务意图。", "agent_name": "{fallback_agent_name}", "secondary_agents": [], "confidence": 0.9, "turn_labels": ["general_chat"], "relation_to_previous": "standalone", "user_action_type": "chat"}
- 上一轮由 data-agent 处理，用户："那再画个柱状图" -> {"thought": "追问且无新领域意图，沿用上一轮 data-agent。", "agent_name": "data-agent", "secondary_agents": [], "confidence": 0.92, "turn_labels": ["continuation_followup", "business_related", "same_topic"], "relation_to_previous": "followup", "user_action_type": "transform_context"}"""

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
    ) -> Optional[RouteResult]:
        """
        Use LLM to select the most appropriate agent(s) for the user query.

        last_agent_name: 上一轮处理本会话的智能体 name(slug)，用于会话粘性，
        让追问/指代类输入优先沿用上一轮智能体，降低多轮误路由。
        """
        import time

        current_time = time.time()
        
        # 1. Fetch Agents (with Caching)
        if self._agents_cache and (current_time - self._last_cache_time < self._cache_ttl):
            agents_metadata = self._agents_cache
        else:
            agents_metadata = await self._fetch_agents_from_db()
            if agents_metadata:
                self._agents_cache = agents_metadata
                self._last_cache_time = current_time
        
        if not agents_metadata:
            logger.warning("No agents available for routing.")
            return None

        agents_metadata = await self._filter_agents_for_user(
            agents_metadata,
            user_id=user_id,
            is_admin=is_admin,
        )
        if not agents_metadata:
            logger.warning("No routeable agents available for user %s.", user_id)
            return None

        from app.services.ai.intent_service import (
            looks_like_greeting,
            looks_like_web_search_query,
            should_inherit_data_agent_session,
        )

        if looks_like_greeting(user_input):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Greeting shortcut: routing to %s without LLM",
                    fallback_agent["name"],
                )
                return RouteResult(
                    agent_id=fallback_agent["id"],
                    confidence=0.95,
                    reasoning="纯问候/寒暄，启发式短路至通用助手（跳过路由 LLM）",
                    turn_labels=["general_chat"],
                    relation_to_previous="standalone",
                    user_action_type="chat",
                )

        if looks_like_web_search_query(user_input):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Web search shortcut: routing to %s without LLM",
                    fallback_agent["name"],
                )
                return RouteResult(
                    agent_id=fallback_agent["id"],
                    confidence=0.92,
                    reasoning="联网/外部公网检索请求，启发式短路至通用助手",
                    turn_labels=["topic_switch", "general_chat"],
                    relation_to_previous="topic_switch",
                    user_action_type="chat",
                )

        if (
            last_agent_name
            and self._is_data_query_agent(agents_metadata, last_agent_name)
            and not should_inherit_data_agent_session(user_input)
        ):
            fallback_agent = self._find_fallback_agent(agents_metadata)
            if fallback_agent:
                logger.info(
                    "Data-agent session break: routing to %s without LLM (last=%s)",
                    fallback_agent["name"],
                    last_agent_name,
                )
                return RouteResult(
                    agent_id=fallback_agent["id"],
                    confidence=0.9,
                    reasoning=(
                        "上一轮虽由数据智能体处理，但本轮不再具备内部业务库查数或数据追问信号，"
                        "按话题切换重新分诊至通用助手"
                    ),
                    turn_labels=["topic_switch", "general_chat"],
                    relation_to_previous="topic_switch",
                    user_action_type="chat",
                )

        # 2. Unified LLM Routing
        # 路由提示词内置在代码中（DEFAULT_SYSTEM_PROMPT），不再从数据库配置读取，
        # 避免运营在配置页误改导致路由失准。
        system_prompt = self.DEFAULT_SYSTEM_PROMPT
        agents_str = self._build_agents_context(agents_metadata)
        history_str = self._build_history_context(
            history,
            last_agent_name,
            user_input=user_input,
            agents_metadata=agents_metadata,
        )
        
        fallback_agent_name = self._resolve_fallback_agent_name(agents_metadata)
        formatted_prompt = (
            system_prompt
            .replace("{agents_context}", agents_str)
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
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                llm = await get_llm_async(temperature=0.0)  # Use deterministic output
                chat_client = chat_client_from_handle(llm)
                content = (await chat_client.generate_text(messages)).strip()
                result_json = self._parse_router_json(content)
                if result_json is None:
                    raise ValueError(f"Unparseable router response: {content[:200]!r}")

                logger.info(f"LLM Routing Response (attempt {attempt + 1}): {content}")
                return self._build_route_result(result_json, agents_metadata, enable_multi_agent)
            except Exception as e:  # noqa: BLE001 - retry then fall back
                last_error = e
                logger.warning(f"Routing attempt {attempt + 1} failed: {e}")

        logger.error(f"Routing failed after retries: {last_error}. Falling back to General Chat.")
        return self._fallback_to_general(agents_metadata, f"Routing exception ({str(last_error)})")

    def _build_history_context(
        self,
        history: Optional[List[dict]],
        last_agent_name: Optional[str],
        *,
        user_input: str = "",
        agents_metadata: Optional[List[dict]] = None,
    ) -> str:
        """Build a denoised, truncated history context plus session-affinity hint.

        - 注入"上一轮处理者"，让追问/指代类输入可沿用上一轮智能体。
        - 若上一轮是 data_query 但本轮不再满足粘性条件，显式提示勿机械沿用。
        - 历史逐条截断并剥离表格/图表/代码块，避免大段业务输出淹没路由信号。
        """
        parts: List[str] = []

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
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    return None
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

    def _build_route_result(
        self,
        result_json: dict,
        agents_metadata: List[dict],
        enable_multi_agent: bool,
    ) -> Optional[RouteResult]:
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
            )

        target_agent = self._match_agent(target_name, agents_metadata)
        if not target_agent:
            logger.warning(f"Router suggested unknown agent: {target_name}. Falling back to General Chat.")
            return self._fallback_to_general(agents_metadata, f"Router returned unknown agent: {target_name}")

        resolved_secondaries: List[str] = []
        if enable_multi_agent and secondary_names:
            for s_name in secondary_names:
                s_agent = self._match_agent(s_name, agents_metadata)
                if s_agent and s_agent["id"] != target_agent["id"]:
                    resolved_secondaries.append(s_agent["id"])

        return RouteResult(
            agent_id=target_agent["id"],
            secondary_agents=resolved_secondaries,
            confidence=confidence,
            reasoning=reasoning,
            turn_labels=turn_labels,
            relation_to_previous=relation_to_previous,
            user_action_type=user_action_type,
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

    def _fallback_to_general(self, agents: List[dict], reason: str) -> Optional[RouteResult]:
        fallback_agent = self._find_fallback_agent(agents)
        if fallback_agent:
            return RouteResult(
                agent_id=fallback_agent['id'],
                confidence=0.1,
                reasoning=f"Fallback: {reason}",
                turn_labels=["ambiguous"],
                relation_to_previous="unknown",
                user_action_type="unknown",
            )
        return None

router_service = RouterService()

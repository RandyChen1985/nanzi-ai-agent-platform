"""KnowledgeAgentRunner：知识库问答专用执行链路。"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.common import (
    convert_history_to_messages,
    normalize_messages_for_llm,
    tools_include_named,
)
from app.services.ai.executors.prompts import KnowledgeChatPrompts
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import GroundingRiskLevel
from app.services.ai.grounding.service import GroundingService
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_display
from app.services.metadata_rag_service import MetadataRagService
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage, AIMessage
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
    runtime_tool_spec_from_legacy_tool,
)
from app.services.ai.runtime.agentscope.tool_result import build_tool_result_envelope
from app.services.ai.tool_capability import RegistryToolProvider, resolve_tool_capabilities
from app.services.ai.knowledge_scope_gate import (
    SCOPE_PICK,
    ScopeGate,
    build_gate_question,
    evaluate_scope_gate,
    parse_scope_choice,
    resolve_dataset_ids_for_choice,
    resolve_scope_gate_settings,
    scope_mode_for_choice,
)
from app.services.conversation_resource_service import ConversationResourceService
from app.services.ai.knowledge_utils import (
    NO_KNOWLEDGE_DATASET_MESSAGE,
    KNOWLEDGE_BASE_DISABLED_USER_MESSAGE,
    collect_citation_ids_from_payload,
    filter_invalid_citation_markers,
    format_dataset_ids_for_tool,
    has_knowledge_citation_markers,
    is_knowledge_base_enabled,
    knowledge_prefetch_had_citations,
    resolve_knowledge_dataset_ids,
    text_has_valid_citation_markers,
)
from app.services.ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 已确认范围的存活时间：一次工作会话的尺度。
# 太短会让用户连续追问时反复被问，太长则把范围钉死到换话题之后。
CONFIRMED_SCOPE_TTL_SECONDS = 2 * 60 * 60

class KnowledgeAgentRunner(AssistantAgentRunner):
    """知识库问答 Runner：自动检索 + AgentScope ReAct，可扩展挂载业务工具。"""

    _REUSABLE_RESULT_BLOCKING_REASONS = frozenset(
        {
            "freshness_requested",
            "selected_result_missing",
            "selected_result_invalid",
            "selected_result_incompatible_type",
            "incompatible_result_type",
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._valid_citation_ids: Set[str] = set()
        self._rag_empty = False
        self._knowledge_retrieval_succeeded = False

    def _ensure_knowledge_grounding_ledger(self) -> EvidenceLedger:
        """为知识库预检与后续 AgentScope 工具调用共享同一份账本。"""
        from app.core.context import get_current_agent_context, set_agent_context

        context = get_current_agent_context() or self._ensure_agent_context()
        ledger = getattr(context, "grounding_evidence_ledger", None)
        if (
            not isinstance(ledger, EvidenceLedger)
            or ledger.user_id != self._runtime_user_id()
            or ledger.conversation_id != self.conversation_id
        ):
            ledger = EvidenceLedger(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
            )
            context.grounding_evidence_ledger = ledger
            set_agent_context(context)
        self._evidence_ledger = ledger
        return ledger

    @staticmethod
    def _is_knowledge_reusable_result(result: Any) -> bool:
        """仅允许明确标记为知识结果的快照进入知识库免检索短路。"""
        if not isinstance(result, dict):
            return False
        result_type = str(result.get("result_type") or "").strip().lower()
        if result_type:
            return result_type == "knowledge"
        return str(result.get("source_type") or "").strip().lower() == "knowledge"

    @staticmethod
    def _sanitize_knowledge_tool_binding_notice(
        text: str,
        *,
        retrieval_succeeded: bool,
    ) -> str:
        """Remove a model-generated unbound-tool notice after cited retrieval succeeded."""
        if not retrieval_succeeded or not text:
            return text

        lines = text.splitlines()
        kept_lines = []
        removed = False
        for line in lines:
            if (
                "search_knowledge_base" in line
                and any(marker in line for marker in ("未绑定", "没有绑定", "不可用"))
            ):
                removed = True
                continue
            kept_lines.append(line)

        if not removed:
            return text
        return "\n".join(kept_lines).strip()

    def _build_tool_observation(self, **kwargs: Any) -> Dict[str, Any]:
        result = super()._build_tool_observation(**kwargs)
        if (
            kwargs.get("tool_name") == "search_knowledge_base"
            and result.get("citation")
        ):
            self._knowledge_retrieval_succeeded = True
        return result

    def _is_hallucinated_knowledge_reply(self, text: str) -> bool:
        text_clean = text.strip()
        if not text_clean:
            return False
        # 1. 包含表格结构视为事实性回答
        if "|" in text_clean and "---" in text_clean:
            return True
        # 2. 较长且包含列表项标记，视为详细步骤脑补
        if len(text_clean) > 120 and ("-" in text_clean or "1." in text_clean):
            return True
        # 3. 较长且无任何拒绝/无法找到相关词汇的陈述，视为编造
        refusal_keywords = ["未找到", "没有找到", "暂无", "无法", "抱歉", "没有", "换个关键词", "换关键词", "不具备", "不能"]
        has_refusal = any(kw in text_clean for kw in refusal_keywords)
        if len(text_clean) > 100 and not has_refusal:
            return True
        return False

    def _is_hallucinated_with_rag_reply(self, text: str) -> bool:
        text_clean = text.strip()
        if not text_clean:
            return False
        # 1. 检查是否存在引用标记（规范 [ID:n] 或旧式 [n]）
        if self._valid_citation_ids:
            if text_has_valid_citation_markers(text_clean, self._valid_citation_ids):
                return False
        elif has_knowledge_citation_markers(text_clean):
            return False
        # 2. 无引用标记时：仅当回答表现出"具体事实断言"结构（表格 / 较长编号列表）且无拒绝措辞，
        #    才保守地认为可能编造。仅凭文本长度（len>120）不判定为幻觉——RAG 已召回时未编号的
        #    长段落仍可能忠实，避免把正确长答案误判为幻觉并触发强制重写。
        refusal_keywords = ["未找到", "没有找到", "暂无", "无法", "抱歉", "没有", "换个关键词", "换关键词", "不具备", "不能", "未提及", "未在"]
        has_refusal = any(kw in text_clean for kw in refusal_keywords)

        is_structured_factual = ("|" in text_clean and "---" in text_clean) or (
            len(text_clean) > 100 and ("-" in text_clean or "1." in text_clean)
        )
        if is_structured_factual and not has_refusal:
            return True
        return False
    def _looks_like_rag_followup(self, query: str, history: List[Dict[str, str]]) -> bool:
        """识别对上一轮检索结果的追问、翻译、总结、解释等上下文操作，以跳过本轮不必要的知识库重复检索。"""
        if not history or len(history) < 2:
            return False
        q = (query or "").strip().lower()
        if not q:
            return False
        
        # 1. 指代词特征
        vague_refs = ["刚才", "刚刚", "上面", "上轮", "上一轮", "这个", "这些", "它", "刚才说的", "前一个", "翻译结果", "上一个回答"]
        # 2. 格式化/翻译/总结/追问等上下文动作特征
        followup_actions = ["翻译", "英文", "english", "总结", "概括", "简短", "口语化", "解释一下第", "详细说明第", "写得通俗", "翻译成"]
        
        is_ref_match = any(ref in q for ref in vague_refs)
        is_action_match = any(act in q for act in followup_actions)
        return is_ref_match or is_action_match or (len(q) < 8 and is_action_match)

    def _should_skip_knowledge_prefetch(
        self,
        *,
        is_catalog_query: bool,
        reusable_decision: Any,
        reusable_knowledge_result: bool,
        user_question: str,
        history: List[Dict[str, str]],
    ) -> bool:
        """判断是否可以跳过知识库预检索，避免复用失败时误走历史追问短路。"""
        if is_catalog_query:
            return False
        # 用户回答回执必须走预检：它带着范围选择，一旦被当成「追问上一轮结果」而短路，
        # 门禁恢复就会静默失效，用户会看到复用的旧引用而不是新范围内的检索结果。
        from app.services.ai.user_question import USER_QUESTION_MESSAGE_PREFIX

        if str(user_question or "").strip().startswith(USER_QUESTION_MESSAGE_PREFIX):
            return False
        if reusable_knowledge_result:
            return True
        if reusable_decision.mode == "reuse":
            return False
        if reusable_decision.reason in self._REUSABLE_RESULT_BLOCKING_REASONS:
            return False
        return self._looks_like_rag_followup(user_question, history)

    def _build_synthesis_user_message(self, user_query: str, execution_review: str) -> str:
        return KnowledgeChatPrompts.synthesis_user_message(user_query, execution_review)

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or "KnowledgeAgent"

    @staticmethod
    def _resolve_explicit_question_nudge(
        user_question: str,
        tools: List[RuntimeToolSpec],
        *,
        allow_interactive_question: bool = True,
    ):
        """解析知识库路径的主动互动提问信号。"""
        from app.services.ai.tool_nudge_policy import (
            looks_like_explicit_user_question_request,
            resolve_tool_nudge,
        )

        if (
            not allow_interactive_question
            or not looks_like_explicit_user_question_request(user_question)
        ):
            return None
        nudge = resolve_tool_nudge(
            user_question,
            tools,
            allow_explicit_question=allow_interactive_question,
        )
        if nudge is None or nudge.tool_name != "ask_user_question":
            return None
        return nudge

    async def _resolve_knowledge_tools(self) -> List[RuntimeToolSpec]:
        configured_tools = self.config.tools or []
        system_tools = ToolRegistry.get_system_implicit_tools()
        resolved = await resolve_tool_capabilities(
            configured_tools,
            implicit_tools=system_tools,
            provider=RegistryToolProvider(
                registry=ToolRegistry,
                legacy_converter=runtime_tool_spec_from_legacy_tool,
                evidence_attacher=ToolRegistry._attach_evidence_metadata,
            ),
        )
        self._last_tool_resolution = resolved
        from app.services.ai.runtime.agentscope.tool_timeout import (
            apply_configured_agent_tool_timeout,
        )

        return await apply_configured_agent_tool_timeout(
            resolved.specs,
            agent_timeout=getattr(self.config, "toolcall_timeout_seconds", None),
        )

    async def _maybe_open_scope_gate(
        self,
        *,
        query: str,
        ctx: Any,
    ) -> Optional[ScopeGate]:
        """检索前判断是否需要让用户确认范围。

        任何异常都返回 None（放行现有全量行为）——门禁是优化项，不能比现状更差。
        """
        try:
            # 委派链没有交互通道：卡片事件会被委派工具的流白名单静默丢弃，
            # 子代理还跑在 child_session_{run_id} 上，回执校验必然失败。
            # 此时弹卡等于把「正常回答」换成「子代理无输出」，必须让路给自动收窄。
            if self._has_no_interactive_user():
                return None
            conversation_id = str(self.conversation_id or "").strip()
            user_id = self._runtime_user_id()
            # 没有会话或用户身份，就无法登记待回答状态、也无法在回执时还原范围：
            # 这种情况下弹卡只会弹出一张「点了必然失败」的卡，直接放行全量行为。
            if not conversation_id or not user_id:
                return None
            conversation_scope = await ConversationResourceService.get(user_id, conversation_id)
            conversation_scope = await self._drop_scope_if_agent_changed(
                scope=conversation_scope,
                agent_id=self._scope_agent_id(ctx),
                user_id=user_id,
                conversation_id=conversation_id,
            )
            conversation_scope = await self._drop_expired_scope(
                scope=conversation_scope,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            enabled, threshold, ranked_limit = await resolve_scope_gate_settings(
                getattr(ctx, "engine_config", None)
            )
            if not enabled:
                return None

            from app.services.ai.knowledge_catalog import load_authorized_knowledge_catalog

            catalog = await load_authorized_knowledge_catalog(
                user_id=user_id,
                user_name=(self.user_info or {}).get("user_name"),
                is_admin=bool(getattr(ctx, "is_admin", False)),
            )
            gate = await evaluate_scope_gate(
                query=query,
                effective_ids=list(getattr(ctx, "dataset_ids", []) or []),
                bound_ids=list(getattr(ctx, "agent_dataset_ids", []) or []),
                explicit_request_ids=list(getattr(ctx, "knowledge_dataset_ids", []) or []),
                conversation_scope=conversation_scope,
                catalog=catalog,
                enabled=enabled,
                threshold=threshold,
                ranked_limit=ranked_limit,
            )
            if gate is not None and conversation_id:
                await ConversationResourceService.patch_scope(
                    user_id,
                    conversation_id,
                    pending_scope_gate=gate.to_pending_payload(),
                )
            return gate
        except Exception as exc:  # noqa: BLE001 - 门禁失败绝不阻塞提问
            logger.warning("[KnowledgeAgentRunner] Scope gate skipped: %s", exc)
            return None

    def _has_no_interactive_user(self) -> bool:
        """本轮是否有在线用户等待交互（委派、定时任务、订阅任务都没有）。

        直接复用 tool_nudge_policy 的既有判定：它已经覆盖定时/订阅/禁止建议等无人值守
        场景，把「能否弹卡」的判断收敛到一处，避免两边口径不一致。
        """
        from app.services.ai.tool_nudge_policy import is_automatic_delivery_context

        return is_automatic_delivery_context(self.user_info, self.execution_options)

    def _is_delegated(self) -> bool:
        """本轮是否由其他智能体委派而来。

        与 ``_has_no_interactive_user`` 分开判断：定时/订阅任务同样不能弹卡，
        但它们不该被「静默收窄」——报表把 100 个库自动砍到 3 个且结果里没有任何提示，
        属于悄悄降低召回，漏数据比慢更糟。只有委派场景（母智能体在场、问题明确）
        才做自动收窄。
        """
        from app.services.ai.tool_nudge_policy import is_automatic_delivery_context

        if not is_automatic_delivery_context(self.user_info, self.execution_options):
            return False
        return any(
            str((context or {}).get("delegated_sub_agent") or "").strip().lower()
            in {"1", "true", "yes", "on"}
            for context in (self.user_info, self.execution_options)
            if isinstance(context, dict)
        )

    def _scope_agent_id(self, ctx: Any = None) -> str:
        """当前轮次的智能体标识，用于判断会话里存的范围是否还属于它。

        写入与比较必须同源：``ctx.agent_id`` 优先，缺省才退回配置里的 agent_id。
        两处用不同来源时，委派或配置复用场景会出现「每轮都判定智能体变了 → 清空 →
        再弹卡」的循环。
        """
        if ctx is None:
            from app.core.context import get_current_agent_context

            ctx = get_current_agent_context()
        runtime_agent_id = str(getattr(ctx, "agent_id", "") or "") if ctx else ""
        return runtime_agent_id or str(
            getattr(getattr(self, "config", None), "agent_id", "") or ""
        )

    async def _drop_expired_scope(
        self,
        *,
        scope: Any,
        user_id: Any,
        conversation_id: str,
    ) -> Any:
        """超过 TTL 的已确认范围作废。

        范围一旦确认，会话在 Redis 里会保留 30 天；不设 TTL 就等于把一个话题的
        检索范围钉死一个月，用户后面换话题问到别的库时会静默搜不到——比他重新
        确认一次要糟得多。
        """
        if not isinstance(scope, dict):
            return scope
        state = scope.get("knowledge_scope")
        if not isinstance(state, dict) or not self._scope_expired(state):
            return scope
        logger.info(
            "[KnowledgeAgentRunner] Confirmed scope expired (confirmed_at=%s), clearing",
            state.get("confirmed_at"),
        )
        try:
            await ConversationResourceService.patch_scope(
                user_id,
                conversation_id,
                knowledge_scope=None,
            )
        except Exception as exc:  # noqa: BLE001 - 清理失败也按过期处理
            logger.warning("[KnowledgeAgentRunner] Clear expired scope failed: %s", exc)
        cleared = dict(scope)
        cleared["knowledge_scope"] = None
        return cleared

    @staticmethod
    def _scope_expired(state: Any) -> bool:
        if not isinstance(state, dict):
            return False
        raw = str(state.get("confirmed_at") or "").strip()
        if not raw:
            # 早期写入没有时间戳，按仍在有效期处理：宁可多生效一次，
            # 也不要因为字段缺失把用户刚确认的范围立刻作废。
            return False
        confirmed = None
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                confirmed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if confirmed is None:
            # 认不出格式就按仍在有效期处理：宁可多生效一次，也不要因为
            # 一个格式差异把用户刚确认的范围当场作废。
            return False
        if confirmed.tzinfo is None:
            confirmed = confirmed.astimezone()
        return (datetime.now(confirmed.tzinfo) - confirmed).total_seconds() > CONFIRMED_SCOPE_TTL_SECONDS

    async def _drop_scope_if_agent_changed(
        self,
        *,
        scope: Any,
        agent_id: str,
        user_id: Any,
        conversation_id: str,
    ) -> Any:
        """切换智能体后旧范围必须清空重判：不同智能体的知识范围本就不同，
        沿用旧范围会检索到该智能体根本不该搜的库。
        """
        if not isinstance(scope, dict):
            return scope
        stored_agent = str((scope.get("knowledge_scope") or {}).get("agent_id") or "").strip()
        if not stored_agent or not agent_id or stored_agent == agent_id:
            return scope
        logger.info(
            "[KnowledgeAgentRunner] Agent changed (%s -> %s), clearing confirmed scope",
            stored_agent,
            agent_id,
        )
        try:
            await ConversationResourceService.patch_scope(
                user_id,
                conversation_id,
                knowledge_scope=None,
                pending_scope_gate=None,
            )
        except Exception as exc:  # noqa: BLE001 - 清理失败也不阻塞，内存里按空处理
            logger.warning("[KnowledgeAgentRunner] Clear stale scope failed: %s", exc)
        cleared = dict(scope)
        cleared["knowledge_scope"] = None
        cleared["pending_scope_gate"] = None
        return cleared

    async def _narrow_scope_without_asking(
        self,
        *,
        query: str,
        dataset_ids: List[str],
        ctx: Any,
    ) -> List[str]:
        """委派场景的降级策略：问不了用户，就按最相关的几个库自动收窄。

        委派意味着用户已经把「怎么做」交给主智能体，此时打断他去选范围并不合适；
        但直接把几十上百个库全量联合检索也没有意义。按问题与库名称/描述的匹配度
        取前 N 个，既保留了收敛收益，也不用任何交互。
        """
        try:
            # 只有委派场景收窄；定时/订阅任务保持全量（见 _is_delegated 说明）。
            if not self._is_delegated() or not dataset_ids:
                return dataset_ids
            enabled, threshold, ranked_limit = await resolve_scope_gate_settings(
                getattr(ctx, "engine_config", None)
            )
            if not enabled or len(dataset_ids) <= max(int(threshold), 1):
                return dataset_ids

            from app.services.ai.knowledge_catalog import (
                load_authorized_knowledge_catalog,
                rank_knowledge_catalog,
            )

            catalog = await load_authorized_knowledge_catalog(
                user_id=self._runtime_user_id(),
                user_name=(self.user_info or {}).get("user_name"),
                is_admin=bool(getattr(ctx, "is_admin", False)),
            )
            allowed = set(dataset_ids)
            ranked = [
                dataset_id
                for dataset_id, _score in rank_knowledge_catalog(
                    query, catalog, limit=ranked_limit
                )
                if dataset_id in allowed
            ]
            if not ranked:
                # 一个都没匹配上就别猜：保留原范围，宁可多检索，也不要因猜错而漏掉答案。
                return dataset_ids
            logger.info(
                "[KnowledgeAgentRunner] Delegated scope auto-narrowed: %d -> %d",
                len(dataset_ids),
                len(ranked),
            )
            return ranked
        except Exception as exc:  # noqa: BLE001 - 收窄失败按原范围检索
            logger.warning("[KnowledgeAgentRunner] Delegated auto-narrow skipped: %s", exc)
            return dataset_ids

    @staticmethod
    def _apply_scope_to_context(dataset_ids: List[str]) -> None:
        """把本轮最终范围写回上下文，让工具层的二次检索使用同一份范围。

        ``resolve_bound_dataset_ids`` 读的是 ``ctx.dataset_ids``。只把收窄结果传给
        平台预检索，模型自己再调 ``search_knowledge_base`` 时仍是全量——
        「只在这 3 个库里检索」的用户意图就只兑现了一半，召回稀释问题原样回来。
        """
        if not dataset_ids:
            return
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        if ctx is None:
            return
        if list(ctx.dataset_ids or []) == list(dataset_ids):
            return
        ctx.dataset_ids = list(dataset_ids)
        logger.info(
            "[KnowledgeAgentRunner] Scope applied to context: %d datasets",
            len(dataset_ids),
        )

    async def _apply_confirmed_scope(self, resolved_ids: List[str]) -> List[str]:
        """把会话里已确认的检索范围应用到本轮，而不只是靠它「不再弹卡」。

        门禁确认过的范围存在会话里；如果只在弹卡判定时读它、检索时不用它，
        用户就会看到「不再提示」但检索范围又悄悄变回全量——那等于这个功能没生效。
        """
        conversation_id = str(self.conversation_id or "").strip()
        user_id = self._runtime_user_id()
        if not conversation_id or not user_id or not resolved_ids:
            return resolved_ids
        try:
            scope = await ConversationResourceService.get(user_id, conversation_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就按原范围检索
            logger.warning("[KnowledgeAgentRunner] Read confirmed scope failed: %s", exc)
            return resolved_ids
        state = scope.get("knowledge_scope")
        if not isinstance(state, dict):
            return resolved_ids
        scope = await self._drop_scope_if_agent_changed(
            scope=scope,
            agent_id=self._scope_agent_id(),
            user_id=user_id,
            conversation_id=conversation_id,
        )
        scope = await self._drop_expired_scope(
            scope=scope,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        state = scope.get("knowledge_scope")
        if not isinstance(state, dict):
            return resolved_ids
        confirmed = [str(item).strip() for item in (state.get("dataset_ids") or []) if str(item).strip()]
        if not confirmed:
            return resolved_ids
        # 库被删除或权限被收回后，旧范围可能包含已不可检索的 ID：只保留仍在可检索集合内的，
        # 全部失效时退回本轮解析出的范围，避免用空集合搜出一片零结果。
        allowed = set(resolved_ids)
        still_valid = [dataset_id for dataset_id in confirmed if dataset_id in allowed]
        if not still_valid:
            logger.info(
                "[KnowledgeAgentRunner] Confirmed scope fully stale, falling back to resolved ids"
            )
            return resolved_ids
        logger.info(
            "[KnowledgeAgentRunner] Apply confirmed scope: mode=%s kept=%d of resolved=%d",
            state.get("mode"),
            len(still_valid),
            len(resolved_ids),
        )
        return still_valid

    @staticmethod
    def _original_query_before_receipt(history: List[Dict[str, str]]) -> str:
        """取触发门禁的那条原始提问，跳过【用户回答】回执本身。

        回执轮次的 ``current_user_query`` 是整段回执文本（问题、所选选项、question_id...），
        拿它当检索词会搜回一堆无关内容，所以恢复范围时要把检索词换回原问题。
        """
        from app.services.ai.user_question import USER_QUESTION_MESSAGE_PREFIX

        for message in reversed(history or []):
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "").lower() != "user":
                continue
            content = str(message.get("content") or "").strip()
            if content and not content.startswith(USER_QUESTION_MESSAGE_PREFIX):
                return content
        return ""

    async def _build_scope_gate_chunk(self, *, gate: ScopeGate) -> Optional[Dict[str, Any]]:
        """把门禁快照转成与 ``ask_user_question`` 同款的 ``user_question`` 事件。

        发卡前必须先在 ``UserQuestionStore`` 登记待回答状态，否则用户提交回执时会被
        preflight 判为「问题不存在或不属于当前会话」——卡片看得见、点了必然失败。
        登记失败时返回 None，由调用方退回原有的全量检索行为。

        ``__scope_bound__`` 在智能体未绑库时被标记为 disabled：现在选项支持禁用态了，
        直接透传给前端置灰——比整项删掉好，用户能看见「有这一项，但当前用不了」以及原因。
        """
        from app.services.ai.user_question import persist_user_question_event

        event = {
            "type": "user_question",
            "question_id": f"uq_{uuid.uuid4().hex[:16]}",
            "question": build_gate_question(gate),
            "options": [dict(option) for option in gate.options],
            "is_multi_select": False,
            "allow_custom_input": True,
            "context": "范围较大时先确认，可以避免在无关知识库中检索，提高命中率与速度。",
            "purpose": "knowledge_scope_gate",
        }
        try:
            await persist_user_question_event(
                event=event,
                user_id=self._runtime_user_id(),
                conversation_id=str(self.conversation_id or "").strip(),
            )
        except Exception as exc:  # noqa: BLE001 - 登记不上就不要发这张卡
            logger.warning(
                "[KnowledgeAgentRunner] Scope gate chunk dropped, persist failed: %s", exc
            )
            return None
        return event

    async def _restore_scope_from_reply(self) -> List[str]:
        """用户回答门禁卡后，用会话里的 pending 快照还原具体 dataset_id（通道 B）。

        前端已把选中库写进 ``knowledge_dataset_ids`` 时（通道 A）也能命中，
        这里覆盖的是不实现通道 A 的第三方客户端。
        """
        try:
            choice = parse_scope_choice(self.current_user_query or "")
            if not choice:
                return []
            conversation_id = str(self.conversation_id or "").strip()
            if not conversation_id:
                return []
            user_id = self._runtime_user_id()
            if choice == SCOPE_PICK:
                # 用户希望自己指定：不猜范围，交回原有解析（通常是全量），
                # 卡片文案已引导他改用知识库附件精确指定。
                # 顺手清掉待回答快照，避免它被后续轮次误用。
                await ConversationResourceService.patch_scope(
                    user_id,
                    conversation_id,
                    pending_scope_gate=None,
                )
                return []
            scope = await ConversationResourceService.get(user_id, conversation_id)
            pending = scope.get("pending_scope_gate")
            dataset_ids = resolve_dataset_ids_for_choice(choice, pending)
            if not dataset_ids:
                return []
            await ConversationResourceService.patch_scope(
                user_id,
                conversation_id,
                knowledge_scope={
                    "mode": scope_mode_for_choice(choice),
                    "dataset_ids": dataset_ids,
                    "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "agent_id": self._scope_agent_id(),
                },
                pending_scope_gate=None,
            )
            logger.info(
                "[KnowledgeAgentRunner] Scope confirmed from reply: mode=%s datasets=%d",
                scope_mode_for_choice(choice),
                len(dataset_ids),
            )
            return dataset_ids
        except Exception as exc:  # noqa: BLE001 - 还原失败则不收窄，走原有解析
            logger.warning("[KnowledgeAgentRunner] Restore scope from reply failed: %s", exc)
            return []

    async def _auto_invoke_search_knowledge_base(
        self,
        *,
        query: str,
        tools: List[RuntimeToolSpec],
        dataset_ids: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """ReAct 开始前平台侧自动执行 search_knowledge_base。"""
        kb_spec = next((tool for tool in tools if tool.name == "search_knowledge_base"), None)
        if kb_spec is None:
            logger.warning("[KnowledgeAgentRunner] search_knowledge_base tool missing; skip auto prefetch")
            return

        tool_id = f"kb_prefetch_{uuid.uuid4().hex[:8]}"
        started_at = time.time()
        yield {
            "type": "log",
            "id": tool_id,
            "title": "自动检索知识库",
            "details": (
                f"平台自动调用 search_knowledge_base("
                f"query={query or 'None'}, dataset_ids={dataset_ids or 'agent-default'})"
            ),
            "status": "pending",
            "category": "tool",
            "started_at": int(started_at * 1000),
        }

        output = ""
        try:
            tool_kwargs: Dict[str, Any] = {"query": query or ""}
            if dataset_ids:
                tool_kwargs["dataset_ids"] = dataset_ids
            # 预检也是一次真实的知识库工具调用，必须走 RuntimeToolSpec 的
            # 最终收口，以便统一生成 ToolResultEnvelope 并进入共享账本。
            result = await kb_spec.invoke(tool_kwargs, call_id=tool_id)
            output = str(result or "")
            self._valid_citation_ids = collect_citation_ids_from_payload(output)
        except Exception as exc:
            logger.error("[KnowledgeAgentRunner] Auto search_knowledge_base failed: %s", exc)
            err_msg = str(exc)
            if MetadataRagService._is_service_unavailable(exc):
                output = MetadataRagService.knowledge_unavailable_hint(err_msg)
            else:
                output = f"[TOOL_ERROR] 自动检索知识库失败: {exc}"

        # 混合检索自适应召回退避逻辑 (特性 6)
        is_empty = False
        max_score = 0.0
        citations = []
        parsed = None
        try:
            import json
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                is_empty = (parsed.get("status") == "empty")
                citations = parsed.get("citations", [])
                if citations:
                    scores = [float(c.get("similarity", 0.0)) for c in citations if "similarity" in c]
                    if scores:
                        max_score = max(scores)
        except Exception:
            pass

        similarity_threshold = 0.45
        try:
            from app.services.config_service import ConfigService
            sys_threshold = await ConfigService.get("knowledge_ragflow_similarity_threshold")
            if sys_threshold is not None:
                similarity_threshold = float(sys_threshold)
        except Exception:
            pass

        service_unavailable = self._is_knowledge_service_unavailable(output)
        parsed_ok = isinstance(parsed, dict)
        if parsed_ok and not service_unavailable and (is_empty or max_score < similarity_threshold):
            logger.info(f"[KnowledgeAgentRunner] Knowledge recall weak (max_score={max_score} < threshold={similarity_threshold}). Activating web search backoff...")
            web_tool_id = f"web_prefetch_{uuid.uuid4().hex[:8]}"
            web_started = time.time()
            yield {
                "type": "log",
                "id": web_tool_id,
                "title": "触发联网辅助检索",
                "details": f"知识库检索无高置信度结果（相似度: {max_score:.3f}，阈值: {similarity_threshold}），自适应触发百度联网检索辅助推理。",
                "status": "pending",
                "category": "tool",
                "started_at": int(web_started * 1000),
            }
            try:
                from app.services.ai.tools.advanced_auxiliary_tools import web_search_baidu_raw
                web_results = await web_search_baidu_raw(query=query, max_results=3)
                web_envelope = build_tool_result_envelope(
                    call_id=web_tool_id,
                    producer="web_search_baidu_raw",
                    result={"items": web_results},
                    evidence_policy="non_empty",
                    result_state="success",
                )
                self._ensure_knowledge_grounding_ledger().record_envelope(
                    web_envelope,
                    evidence_types={EvidenceType.PUBLIC_WEB},
                    policy="non_empty",
                )
                web_duration = (time.time() - web_started) * 1000
                yield {
                    "type": "log",
                    "id": web_tool_id,
                    "title": "联网辅助检索完成",
                    "details": f"成功融合 {len(web_results)} 个网页的最新参考事实作为推理支撑 (耗时 {web_duration:.0f}ms)",
                    "status": "success",
                    "category": "tool",
                    "execution_time_ms": web_duration,
                }
                
                kb_context = ""
                if isinstance(parsed, dict):
                    kb_context = parsed.get("content", "")
                else:
                    kb_context = output

                web_context_pieces = []
                new_citations = []
                
                # 给原有的知识库 citations 加上 source_type
                for c in citations:
                    c["source_type"] = "knowledge"
                    new_citations.append(c)
                    
                for idx, item in enumerate(web_results, 1):
                    title = item.get("title", "")
                    link = item.get("real_url") or item.get("link", "")
                    abstract = item.get("abstract", "")
                    content = item.get("extracted_content") or abstract
                    
                    web_context_pieces.append(f"文献 {idx} [标题: {title} | 来源: {link}]:\n{content}")
                    
                    web_chunk_id = f"web_{idx}_{uuid.uuid4().hex[:6]}"
                    new_citations.append({
                        "chunk_id": web_chunk_id,
                        "doc_name": f"网页: {title}",
                        "content": content,
                        "similarity": 1.0,
                        "source_type": "web",
                        "link": link
                    })
                    
                web_context = "\n\n【互联网参考事实文献】\n" + "\n\n".join(web_context_pieces) if web_context_pieces else ""
                combined_context = f"{kb_context}\n{web_context}"
                
                import json
                output = json.dumps({
                    "content": combined_context,
                    "citations": new_citations
                })
                self._valid_citation_ids = {
                    str(idx) for idx in range(1, len(new_citations) + 1)
                }
                
            except Exception as web_exc:
                logger.error(f"[KnowledgeAgentRunner] Web search backoff failed: {web_exc}", exc_info=True)
                yield {
                    "type": "log",
                    "id": web_tool_id,
                    "title": "联网辅助检索失败",
                    "details": f"发生异常: {web_exc}",
                    "status": "error",
                    "category": "tool",
                }

        duration_ms = (time.time() - started_at) * 1000
        self._increment_step()
        observation = self._build_tool_observation(
            tool_id=tool_id,
            tool_name="search_knowledge_base",
            tool_args={"query": query, "dataset_ids": dataset_ids},
            tool_output=output,
            duration_tool=duration_ms,
            target_tool=kb_spec,
            tool_index=0,
        )
        if observation.get("trace"):
            self.trace_buffer.append(observation["trace"])
        service_unavailable = self._is_knowledge_service_unavailable(output)
        log_event = {
            "type": "log",
            "id": tool_id,
            "title": "工具完成: search_knowledge_base",
            "details": observation.get("log", {}).get("details", output[:500]),
            "status": "error" if service_unavailable else "success",
            "category": "tool",
            "execution_time_ms": duration_ms,
        }
        display_args = observation.get("log", {}).get("tool_args")
        if display_args:
            log_event["tool_args"] = display_args
        yield log_event
        if observation.get("citation"):
            yield observation["citation"]
        yield {
            "__knowledge_output__": observation.get("final_tool_message_content") or output,
            "__citations_raw__": observation.get("citation", {}).get("data") if observation.get("citation") else None,
            "__knowledge_service_unavailable__": service_unavailable,
        }

    @staticmethod
    def _is_knowledge_service_unavailable(tool_output: Any) -> bool:
        """仅识别工具显式返回的错误标记，勿对检索正文做子串匹配（正文可能含 503/timeout 等）。"""
        text = str(tool_output or "")
        if "[知识库服务不可用]" in text:
            return True
        normalized = text.lstrip()
        if normalized.startswith("[Tool Error]") or normalized.startswith("[TOOL_ERROR]"):
            return MetadataRagService._is_service_unavailable(text)
        return False

    async def _yield_knowledge_no_dataset_abort(
        self,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "type": "log",
            "id": f"kb_no_dataset_{uuid.uuid4().hex[:8]}",
            "title": "未指定知识库",
            "details": NO_KNOWLEDGE_DATASET_MESSAGE,
            "status": "error",
            "category": "knowledge",
        }
        yield {
            "content": KnowledgeChatPrompts.KNOWLEDGE_NO_DATASET_CONTENT,
            "status": "error",
        }

    async def _yield_knowledge_fatal_abort(
        self,
        details: Any = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "type": "log",
            "id": f"kb_fatal_{uuid.uuid4().hex[:8]}",
            "title": "知识库服务不可用",
            "details": truncate_for_display(str(details or ""), max_len=1000) or "知识库服务不可用",
            "status": "error",
        }
        yield {
            "content": KnowledgeChatPrompts.KNOWLEDGE_SERVICE_UNAVAILABLE_CONTENT,
            "status": "error",
        }

    def _knowledge_synthesis_event(self, status: str, details: str) -> Dict[str, Any]:
        """知识库问答的「检索完成，正在组织回答」过程项。

        发出（pending）与收尾（success）必须共用同一 id 与 title：前端按 id 归并，
        title 不一致会让两个状态各成一条。故集中在此，勿在调用处手写字面量。
        """
        return {
            "type": "log",
            "id": "knowledge_synthesis",
            "title": "检索完成，正在组织回答",
            "details": details,
            "status": status,
            "category": "knowledge",
        }

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="KnowledgeAgentRunner",
        ):
            async for chunk in self._execute_raw(history):
                yield chunk

    async def _execute_raw(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.reusable_result import (
            CLICKED_REPLY_MARKER,
            prepare_reusable_route_input,
        )

        raw_current_question = str(self.current_user_query or "")
        raw_history_question = next(
            (
                str(message.get("content") or "")
                for message in reversed(history or [])
                if isinstance(message, dict)
                and str(message.get("role") or "").lower() == "user"
            ),
            "",
        )
        route_source_question = (
            raw_history_question
            if CLICKED_REPLY_MARKER.lower() in raw_history_question.lower()
            else raw_current_question or raw_history_question
        )
        history, cleaned_question = prepare_reusable_route_input(
            history,
            route_source_question,
        )
        if CLICKED_REPLY_MARKER.lower() in raw_current_question.lower():
            self.current_user_query = cleaned_question
        from app.services.ai.multimodal_support import (
            resolve_runtime_model_name,
            run_multimodal_gate,
        )
        from app.services.config_service import ConfigService

        self._knowledge_retrieval_succeeded = False
        self._ensure_knowledge_grounding_ledger()

        if not await is_knowledge_base_enabled():
            yield {
                "type": "error",
                "status": "error",
                "content": KNOWLEDGE_BASE_DISABLED_USER_MESSAGE,
            }
            return

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        async for chunk in run_multimodal_gate(
            history,
            model_name,
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
        ):
            yield chunk
            if chunk.get("status") == "error" and not chunk.get("type"):
                return

        tools = await self._resolve_knowledge_tools()
        for event in self._tool_resolution_log_events():
            yield event

        raw_user_question = self._current_user_query(history).strip()
        from app.services.ai.reusable_result import (
            extract_reusable_action_query,
            resolve_reusable_result,
        )
        from app.services.ai.session_tool_artifact import (
            build_session_tool_artifact_context_message,
            filter_tools_for_reusable_result,
            insert_session_tool_artifact_context,
            load_session_tool_artifact,
        )

        preferred_reusable_result_id = str(
            getattr(self.turn_decision, "reusable_result_id", None) or ""
        ).strip() or None
        reusable_artifact = await load_session_tool_artifact(
            self._runtime_user_id(),
            self.conversation_id,
            preferred_result_id=preferred_reusable_result_id,
        )
        reusable_decision = resolve_reusable_result(
            raw_user_question,
            current=reusable_artifact,
            stack=[],
            preferred_result_id=preferred_reusable_result_id,
            allowed_result_types={"knowledge"},
        )
        user_question = (
            extract_reusable_action_query(raw_user_question)
            if "【被点击的 AI 回复】" in raw_user_question
            else raw_user_question
        )
        reusable_knowledge_result = (
            reusable_decision.mode == "reuse"
            and self._is_knowledge_reusable_result(reusable_decision.result)
        )
        if not tools_include_named(tools, "search_knowledge_base") and not reusable_knowledge_result:
            yield {
                "type": "error",
                "status": "error",
                "content": "知识库工具 search_knowledge_base 不可用，无法执行知识库问答。",
            }
            return
        # 从 execution_options 读取会话级反幻觉开关（与模型覆盖等参数同通道）
        hallucination_check_enabled = bool(self.execution_options.get("hallucination_check", False))
        grounding_enabled = self._grounding_enabled()

        system_content = self.config.system_prompt or ""
        use_cache_layout = await self._using_cache_layout()
        decision_context = AgentServicePrompts.turn_decision_context(
            self.turn_decision
        )
        if use_cache_layout:
            # enabled 灰度桶：动态块后置，稳定（系统提示）在前。
            dynamic_appends = []
            if KnowledgeChatPrompts.TURN_SYSTEM_HINT not in system_content:
                dynamic_appends.append(KnowledgeChatPrompts.TURN_SYSTEM_HINT)
            if (
                decision_context
                and decision_context not in system_content
                and "本轮执行上下文（平台路由快照）" not in system_content
            ):
                dynamic_appends.append(decision_context)
            if dynamic_appends:
                system_content = f"{system_content}\n\n" + "\n\n".join(dynamic_appends)
        else:
            # legacy/observe：还原传统前置顺序（TURN_SYSTEM_HINT -> decision_context），
            # 确保默认路径不被灰度改动。
            if KnowledgeChatPrompts.TURN_SYSTEM_HINT not in system_content:
                system_content = f"{KnowledgeChatPrompts.TURN_SYSTEM_HINT}\n\n{system_content}"
            if decision_context:
                system_content = f"{decision_context}\n\n{system_content}"
        if reusable_knowledge_result and reusable_decision.result:
            session_artifact_context = build_session_tool_artifact_context_message(
                reusable_decision.result,
                user_question=raw_user_question,
                force_reuse=preferred_reusable_result_id is not None,
            )
            tools = filter_tools_for_reusable_result(
                tools,
                user_question=raw_user_question,
                artifact=reusable_decision.result,
                force_reuse=preferred_reusable_result_id is not None,
            )
        else:
            session_artifact_context = None

        # RAG 分类与复用决策
        from app.services.ai.intent_service import looks_like_accessible_resource_catalog_query

        is_catalog_query = looks_like_accessible_resource_catalog_query(user_question)
        is_followup = self._should_skip_knowledge_prefetch(
            is_catalog_query=is_catalog_query,
            reusable_decision=reusable_decision,
            reusable_knowledge_result=reusable_knowledge_result,
            user_question=user_question,
            history=history,
        )
        prefetched_knowledge_output: str | None = None
        prefetched_citations_raw: list | None = None
        knowledge_service_unavailable = False
        prefetch_had_citations = False
        # 预检索后发出、首个正文 chunk 到达时收尾的状态项；跨重试轮只发/收一次。
        knowledge_synthesis_details = ""
        synthesis_pending = False
        knowledge_state_prompt = ""

        if is_catalog_query:
            yield {
                "type": "log",
                "id": f"kb_catalog_{uuid.uuid4().hex[:8]}",
                "title": "知识库目录清单",
                "details": (
                    "检测到权限/目录清单询问，跳过 search_knowledge_base 自动检索；"
                    "请使用 list_accessible_knowledge_bases 返回可访问知识库。"
                ),
                "status": "success",
                "category": "intent",
            }
            system_content += (
                "\n\n【本轮为知识库权限/目录清单】"
                "必须调用 list_accessible_knowledge_bases 列出当前用户可访问知识库；"
                "禁止调用 search_knowledge_base 检索文档正文，禁止编造权限清单。"
            )
        elif is_followup:
            yield {
                "type": "log",
                "id": f"kb_followup_{uuid.uuid4().hex[:8]}",
                "title": "自动复用知识库",
                "details": "检测到对上一轮回答的追问/操作（翻译/总结等），自动复用历史上下文，跳过知识库重新检索。",
                "status": "success",
                "category": "intent",
            }
            # 提取上一轮被成功引用的 ID，以便在追问中继续复用，通过防幻觉校验
            last_assistant_content = ""
            for msg in reversed(history):
                if msg.get("role") == "assistant":
                    last_assistant_content = str(msg.get("content") or "")
                    break
            if last_assistant_content:
                import re
                last_citations = re.findall(r"\[ID:\s*(\d+)\]", last_assistant_content)
                self._valid_citation_ids = set(last_citations)
            knowledge_state_prompt = KnowledgeChatPrompts.knowledge_state_block(
                search_status="REUSED",
                result_status="SUFFICIENT" if self._valid_citation_ids else "UNKNOWN",
                citation_count=len(self._valid_citation_ids),
                supplement_allowed=False,
            )
        else:
            # 范围门禁：先看上一轮用户是否刚回答了范围卡（通道 B 还原），
            # 再判断本轮的检索范围是否需要用户先确认。
            restored_dataset_ids = await self._restore_scope_from_reply()
            if restored_dataset_ids:
                # 卡片弹出到用户点下去之间库可能已被删除或收回，这里要跟正常路径
                # 一样做存活校验，否则会拿着死 ID 去打 RAGFlow。
                from app.services.ai.knowledge_utils import filter_alive_knowledge_dataset_ids

                resolved_dataset_ids = await filter_alive_knowledge_dataset_ids(
                    list(restored_dataset_ids)
                )
                dataset_error = (
                    None
                    if resolved_dataset_ids
                    else "⚠️ 已确认的知识库在 RAGFlow 侧已失联或不存在，本次知识库问答已终止。"
                )
                # 回执轮的检索词不能是【用户回答】全文，换回触发门禁的原始提问。
                user_question = self._original_query_before_receipt(history) or user_question
            else:
                from app.core.context import get_current_agent_context

                receipt_choice = parse_scope_choice(self.current_user_query or "")
                scope_gate = None
                if receipt_choice != SCOPE_PICK:
                    # 选「我自己指定」时不重复弹卡：否则用户点完这张卡下一行又弹同一张。
                    scope_gate = await self._maybe_open_scope_gate(
                        query=user_question,
                        ctx=get_current_agent_context() or self._ensure_agent_context(),
                    )
                if scope_gate is not None:
                    gate_chunk = await self._build_scope_gate_chunk(gate=scope_gate)
                    if gate_chunk is not None:
                        yield gate_chunk
                        return
                if receipt_choice == SCOPE_PICK:
                    # 回执轮的检索词同样要换回原问题，而不是整段【用户回答】。
                    user_question = self._original_query_before_receipt(history) or user_question
                resolved_dataset_ids, dataset_error = await resolve_knowledge_dataset_ids(
                    query=user_question,
                )
                # 会话里确认过范围就按它检索（本轮没显式选库时才应用，显式选库优先）。
                if not getattr(
                    get_current_agent_context() or self._ensure_agent_context(),
                    "knowledge_dataset_ids",
                    None,
                ):
                    resolved_dataset_ids = await self._apply_confirmed_scope(
                        list(resolved_dataset_ids or [])
                    )
                    # 委派场景问不了用户，改用「最相关 N 个库」自动收窄。
                    resolved_dataset_ids = await self._narrow_scope_without_asking(
                        query=user_question,
                        dataset_ids=list(resolved_dataset_ids or []),
                        ctx=get_current_agent_context() or self._ensure_agent_context(),
                    )
                # 最终范围必须写回上下文：模型随后自己调用 search_knowledge_base 时
                # 走 resolve_bound_dataset_ids(ctx.dataset_ids)，不写回就变成
                # 「预检索收窄了、二次检索仍全量」，用户确认的范围只兑现一半。
                self._apply_scope_to_context(list(resolved_dataset_ids or []))
            if dataset_error and not resolved_dataset_ids:
                async for chunk in self._yield_knowledge_no_dataset_abort():
                    yield chunk
                return

            prefetch_dataset_ids = format_dataset_ids_for_tool(resolved_dataset_ids)
            async for chunk in self._auto_invoke_search_knowledge_base(
                query=user_question,
                tools=tools,
                dataset_ids=prefetch_dataset_ids,
            ):
                if chunk.get("__knowledge_service_unavailable__"):
                    knowledge_service_unavailable = True
                if chunk.get("__knowledge_output__") is not None:
                    prefetched_knowledge_output = str(chunk["__knowledge_output__"])
                    prefetched_citations_raw = chunk.get("__citations_raw__")
                    prefetch_had_citations = knowledge_prefetch_had_citations(prefetched_knowledge_output)
                    continue
                yield chunk

            if knowledge_service_unavailable:
                async for chunk in self._yield_knowledge_fatal_abort(prefetched_knowledge_output):
                    yield chunk
                return

            knowledge_state_prompt = KnowledgeChatPrompts.knowledge_state_block(
                search_status="PREFETCHED",
                result_status="SUFFICIENT" if prefetch_had_citations else "INSUFFICIENT",
                citation_count=len(self._valid_citation_ids),
                supplement_allowed=not prefetch_had_citations,
            )
            self._knowledge_retrieval_succeeded = prefetch_had_citations

            # 0 条引用时不得谎报「已获取 0 条资料」。文案在发出时定稿、收尾复用同一份：
            # 模型若在 ReAct 中二次检索会更新 _valid_citation_ids，收尾时重算会让
            # 同一条目前后文案不一致。
            knowledge_synthesis_details = (
                f"已获取 {len(self._valid_citation_ids)} 条资料，正在生成回答…"
                if prefetch_had_citations
                else "未检索到相关资料，正在组织回复…"
            )
            yield self._knowledge_synthesis_event(status="pending", details=knowledge_synthesis_details)
            synthesis_pending = True

        self._rag_empty = False
        if knowledge_state_prompt:
            system_content += "\n\n" + knowledge_state_prompt
        if prefetched_knowledge_output:
            try:
                import json
                parsed = json.loads(prefetched_knowledge_output)
                if isinstance(parsed, dict) and parsed.get("status") == "empty":
                    self._rag_empty = True
            except Exception:
                pass
            system_content += (
                "\n\n"
                + KnowledgeChatPrompts.prefetched_knowledge_context(
                    user_question,
                    prefetched_knowledge_output,
                )
            )

        from app.services.ai.runtime.agentscope.workspace import (
            append_session_workspace_sandbox_to_system_prompt,
        )

        system_content = await append_session_workspace_sandbox_to_system_prompt(
            system_content,
            user_id=self._runtime_user_id(),
            user_name=self._runtime_user_name(),
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            tools=tools,
        )

        from app.services.ai.time_anchor import (
            append_time_anchor_for_user_question,
            filter_redundant_time_tools,
        )

        system_content = append_time_anchor_for_user_question(
            system_content,
            user_question,
            prepend=not use_cache_layout,
        )
        tools = filter_redundant_time_tools(tools, system_content)

        # AgentService 已按最终模型的 history_budget 完成窗口选择和摘录；
        # 知识库 runner 不能再次按固定条数截断，避免丢掉平台摘要。
        runtime_messages = [SystemMessage(content=system_content)]
        runtime_messages.extend(convert_history_to_messages(history, strip_thought=True))
        if session_artifact_context:
            runtime_messages = insert_session_tool_artifact_context(
                runtime_messages,
                HumanMessage(content=session_artifact_context),
            )
        runtime_messages = normalize_messages_for_llm(runtime_messages)

        max_steps_str = await ConfigService.get("agent_max_iterations")
        max_steps = int(max_steps_str) if max_steps_str else 5

        prev = system_content
        if is_followup:
            followup_hint = (
                "【平台已复用上一轮上下文】检测到本轮为针对上一次知识库回答的追问、翻译或格式调整动作，"
                "系统已自动复用上一轮检索到的事实文献上下文，跳过了本次 search_knowledge_base 重复检索。"
                "请直接在多轮对话历史中结合上一轮的回答来响应用户，无需再次检索。"
            )
            if use_cache_layout:
                native_system_content = f"{prev}\n\n{followup_hint}"
            else:
                native_system_content = f"{followup_hint}\n\n{prev}"
        elif prefetch_had_citations:
            _corr = KnowledgeChatPrompts.PREFETCH_DONE_CORRECTION_MSG
            native_system_content = (
                f"{prev}\n\n{_corr}" if use_cache_layout else f"{_corr}\n\n{prev}"
            )
        else:
            _corr = KnowledgeChatPrompts.SEARCH_CORRECTION_MSG
            native_system_content = (
                f"{prev}\n\n{_corr}" if use_cache_layout else f"{_corr}\n\n{prev}"
            )

        # 预检索成功后仍保留 search_knowledge_base 注册，避免模型受用户附件/系统提示
        # 强制调用约束时触发 AgentScope ToolNotFoundError。
        react_tools = tools

        initial_tool_choice = None
        from app.services.ai.tool_nudge_policy import is_automatic_delivery_context

        question_nudge = self._resolve_explicit_question_nudge(
            user_question,
            react_tools,
            allow_interactive_question=not is_automatic_delivery_context(
                self.user_info,
                self.execution_options,
            ),
        )
        if question_nudge is not None:
            if use_cache_layout:
                native_system_content = (
                    f"{native_system_content}\n\n{question_nudge.message}"
                )
            else:
                native_system_content = (
                    f"{question_nudge.message}\n\n{native_system_content}"
                )
            initial_tool_choice = self._build_preflight_tool_choice(
                question_nudge.recommended_force_mode()
            )
            yield {
                "type": "log",
                "id": f"tool_preflight_{uuid.uuid4().hex[:8]}",
                "title": "工具预检：本轮优先调用工具",
                "details": (
                    "用户明确要求互动式提问，已要求模型首步调用 ask_user_question。"
                ),
                "status": "success",
                "category": "tool_preflight",
                "tool_name": "ask_user_question",
                "force_first_call": initial_tool_choice is not None,
            }

        if not all(isinstance(tool, RuntimeToolSpec) for tool in react_tools):
            yield {
                "type": "error",
                "status": "error",
                "content": "Knowledge 工具链必须使用 AgentScope RuntimeToolSpec。",
            }
            return

        native_model_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        self._record_effective_temperature(native_model_handle)
        native_model = getattr(native_model_handle, "native_model", None)
        if native_model is None:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前模型适配器未提供 AgentScope native_model，无法执行知识库 AgentScope 原生工具链。",
            }
            return

        # 幻觉检测自反思循环 (Reflection Loop) (特性 3)
        from app.services.ai.hallucination_evaluator import HallucinationEvaluator

        max_retries = 2
        retry_count = 0
        passed_guard = False
        last_guard_reason = "回答中存在尚未完全核实的知识库事实"
        
        current_system_content = native_system_content
        current_messages = list(runtime_messages)

        while retry_count <= max_retries:
            chunks_buffer = []
            full_text = ""
            # 终止信号标志位：error chunk 不再进缓冲区，无法靠扫缓冲区识别。
            saw_error = False
            
            execute_kwargs = {
                "native_model": native_model,
                "tools": react_tools,
                "system_content": current_system_content,
                "runtime_messages": current_messages,
                "max_steps": max_steps,
            }
            if initial_tool_choice is not None:
                execute_kwargs["initial_tool_choice"] = initial_tool_choice

            async for chunk in self._execute_with_agentscope_native_agent(**execute_kwargs):
                # 错误事件可能是安全拦截，且**携带 content**；若只在不带 content 的
                # 分支里识别，这类错误会被漏判而进入幻觉评估，把拦截误判成幻觉。
                if chunk.get("type") == "error":
                    saw_error = True
                if "content" in chunk:
                    content = str(chunk.get("content") or "")
                    if self._valid_citation_ids:
                        content = filter_invalid_citation_markers(content, self._valid_citation_ids)
                    full_text += content
                    chunk = dict(chunk)
                    chunk["content"] = content
                    chunks_buffer.append(chunk)
                    if synthesis_pending:
                        # 首个正文 chunk 到达即收尾：此时 model_call 的 pending 项已经
                        # 实时转发出来，卡片能无缝接上「模型调用: <模型名> · 进行中」；
                        # 若等 flush 才收尾，中间会出现「状态项已完成 + 模型调用已完成
                        # + 正文未到达」的空档，卡片会再次停住。
                        synthesis_pending = False
                        yield self._knowledge_synthesis_event(
                            status="success",
                            details=knowledge_synthesis_details,
                        )
                else:
                    # 过程性事件（model_call / log / reasoning_content / meta 等）实时转发。
                    # 此前它们与正文一起进缓冲区，用户在模型生成期间收不到任何事件，
                    # 思考卡片停在「工具完成: search_knowledge_base」上，看起来像卡死。
                    yield chunk

            if saw_error:
                # 错误事件是终止信号，不能进入知识库事实评估/反思循环，
                # 否则会把安全拦截误判成幻觉并覆盖原始错误。
                for chunk in chunks_buffer:
                    yield chunk
                return

            if self._knowledge_retrieval_succeeded:
                sanitized_full_text = self._sanitize_knowledge_tool_binding_notice(
                    full_text,
                    retrieval_succeeded=True,
                )
                if sanitized_full_text != full_text:
                    content_chunks = [
                        chunk for chunk in chunks_buffer if "content" in chunk
                    ]
                    if content_chunks:
                        content_chunks[0]["content"] = sanitized_full_text
                        for chunk in content_chunks[1:]:
                            chunk["content"] = ""
                    full_text = sanitized_full_text

            if not grounding_enabled:
                passed_guard = True
                break

            # 评估是否含有幻觉（受会话开关控制）
            evaluation = await HallucinationEvaluator.evaluate(
                query=user_question,
                context=prefetched_knowledge_output or "",
                response=full_text,
                enabled=hallucination_check_enabled,
            )

            is_hallucinated = evaluation.get("is_hallucinated", False)
            if not is_hallucinated:
                # 兜底原有的规则：如果 RAG 为空但模型编造了答案，或召回非空但缺少引用
                if self._rag_empty:
                    is_hallucinated = self._is_hallucinated_knowledge_reply(full_text)
                else:
                    is_hallucinated = self._is_hallucinated_with_rag_reply(full_text)

            if not is_hallucinated:
                passed_guard = True
                break

            retry_count += 1
            reason = evaluation.get("reason") or "回答中存在无依据的幻觉陈述或缺少必要引用"
            last_guard_reason = reason
            logger.warning(
                f"[KnowledgeAgentRunner] Hallucination detected! Attempt {retry_count} failed. "
                f"Reason: {reason}. Model output: {full_text[:150]}..."
            )

            if retry_count <= max_retries:
                # 发送拦截 log 事件告知前端
                yield {
                    "type": "log",
                    "id": f"kb_reflection_{uuid.uuid4().hex[:8]}",
                    "title": f"检测到无依据表述 (反思修正中 {retry_count}/{max_retries})",
                    "details": f"回答中发现与事实文献不符的描述（原因: {reason}），安全网关已拦截并命令其重写纠正。",
                    "status": "warning",
                }
                
                # 重新追加 messages 进行反思自纠偏
                current_messages.append(AIMessage(content=full_text))
                reflection_prompt = (
                    f"【网关安全警报】\n"
                    f"你刚才生成的回答被事实一致性网关拦截退回。原因：{reason}。\n"
                    f"请务必吸取教训，重新审查问题和提供的事实文献，绝对不允许包含文献中没有明确说明的外部假设、新参数或隐藏规则。\n"
                    f"请重新进行一次高度客观且无任何幻觉的严谨回答："
                )
                current_messages.append(HumanMessage(content=reflection_prompt))
            else:
                break

        # 引用量统计与「回答是否通过事实一致性网关」解耦。
        #
        # 检索量由知识库工具在检索时记录，本就不受网关门控；若引用量只在 passed_guard
        # 为真时才记，被判定幻觉、重写后仍未通过的回答就会出现「有检索、零引用」，引用率
        # 被系统性压低甚至恒为 0。网关判的是「有没有无依据表述」，不是「有没有引用」——
        # 模型是否写下 [ID:n] 是回答文本里的客观事实，两者必须分开统计。
        if prefetched_citations_raw and full_text:
            try:
                import re
                from app.core.context import get_current_agent_context
                from app.services.knowledge_metrics_service import KnowledgeMetricsService

                citation_ids = set(re.findall(r"\[ID:\s*(\d+)\]", full_text))
                # 引用量优先按台账的整轮全局编号统计（可覆盖本轮全部检索，含 ReAct
                # 中的二次检索）；台账缺失时退回按预取列表的位置编号，仅覆盖本次预取。
                ctx = get_current_agent_context()
                ledger = getattr(ctx, "knowledge_citation_ledger", None) if ctx else None
                entries = getattr(ledger, "entries", None)
                if entries:
                    await KnowledgeMetricsService.record_citation_hits_for_refs(
                        entries,
                        citation_ids,
                    )
                else:
                    await KnowledgeMetricsService.record_citation_hits(
                        prefetched_citations_raw,
                        citation_ids,
                    )
            except Exception as metric_err:
                logger.warning(f"[KnowledgeAgentRunner] Redis metrics recording failed: {metric_err}")

        if passed_guard:
            for chunk in chunks_buffer:
                yield chunk
        else:
            logger.warning(
                f"[KnowledgeAgentRunner] Hallucination check failed after {max_retries} retries. "
                f"Retaining the final answer with a grounding risk warning."
            )
            for chunk in chunks_buffer:
                yield chunk
            yield {
                "type": "log",
                "id": f"kb_guard_{uuid.uuid4().hex[:8]}",
                "title": "事实来源风险提示已追加",
                "details": last_guard_reason,
                "status": "warning",
                "category": "grounding",
            }
            yield GroundingService.warning_chunk(
                risk_level=GroundingRiskLevel.HIGH,
                reason=last_guard_reason,
            )

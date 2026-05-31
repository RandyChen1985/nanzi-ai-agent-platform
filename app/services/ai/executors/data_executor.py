import time
import uuid
import json
import logging
import asyncio
import re
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

from langchain_core.messages import (
    HumanMessage, 
    AIMessage, 
    SystemMessage, 
    BaseMessage,
    ToolMessage
)
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.config import AgentConfigProvider

from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.common import (
    convert_history_to_messages,
    extract_tokens_from_message,
    parse_xml_tool_calls,
)
from app.services.ai.executors.prompts import DataQueryPrompts, SharedPrompts
from app.services.ai.intent_service import (
    looks_like_context_action,
    looks_like_skill_execution,
    looks_like_pure_result_followup,
)

logger = logging.getLogger(__name__)

# 数据查询 ReAct 轮次上限（与全局 agent_max_iterations 取较小值，避免长时间空转）
DATA_QUERY_MAX_STEPS_CAP = 10

class DataQueryExecutor(BaseExecutor):
    def __init__(self, config: ChatConfig, trace_id: str, trace_buffer: List[AgentExecutionStep], debug_options: Dict[str, Any] = None, user_info: Optional[Dict[str, Any]] = None, conversation_id: Optional[str] = None):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        self.intent_info = None
        self._sql_plan_enforcement_added = False
        self._ratio_anomaly_feedback_sent = False
        self._schema_fetched_ok = False
        self._schema_no_authorized = False
        self._metadata_unavailable = False
        self._sql_attempted = False
        self._sql_succeeded = False
        self._sql_after_schema_nudge_sent = False
        self._current_user_question = ""
        self._no_tool_call_streak = 0
        self._non_data_tool_called = False
        # 本轮是否“需要重新查数”（K1）。对已有结果做保存/导出/发送/记忆/建技能等动作（K3）时为 False，
        # 届时关闭“必须先查库”的强制护栏，允许直接基于上下文调用工具或作答。
        self._requires_fresh_data = True
        self._skip_few_shot = False
        self._skill_matched = False
        self._skill_ready = False
        self._needs_skill_prep = False
        self._sql_plan_block_used = False

    # 数据查询类工具：调用这些以外的工具（如 create_skills）视为“元操作/外部动作”，
    # 不应再被“必须先查库”的护栏阻断。
    DATA_TOOL_NAMES = {"get_dataset_schema", "execute_sql_query", "update_dashboard_context"}
    SKILL_TOOL_NAMES = {"list_available_skills", "read_skill_instruction"}

    def _active_skills_in_system_prompt(self, system_prompt: str) -> bool:
        return "[Active Skills Loaded]" in (system_prompt or "")

    def _increment_step(self):
        self.step_counter += 1

    def _has_sql_plan(self, text: str) -> bool:
        if not text:
            return False
        # Require an explicit tag to avoid accidental matches.
        return re.search(r"<sql_plan>\s*\{[\s\S]*?\}\s*</sql_plan>", text, flags=re.IGNORECASE) is not None

    def _should_require_sql_plan(self, user_question: str) -> bool:
        """
        Only enforce sql_plan for high-risk queries to avoid blocking simple list/detail queries.
        Heuristics: ratio/percent metrics, comparisons, trends, ranking, grouping/segmentation.
        """
        q = (user_question or "").strip().lower()
        if not q:
            return False
        # Chinese and English mixed keywords
        high_risk_keywords = [
            "率", "占比", "比例", "比率", "负载", "利用率", "pue", "成功率", "转化率", "人均", "单价",
            "同比", "环比", "趋势", "变化", "增长", "下降",
            "top", "排名", "排行", "分组", "维度", "group", "join",
            "p95", "p90", "分位", "中位", "median", "percentile",
        ]
        if any(k in q for k in high_risk_keywords):
            return True
        # 「按X分组/统计」才算高风险；单独的「按」过于宽泛，会误拦简单明细查询。
        return re.search(r"按.{0,12}(组|类|类型|维度|机房|区域|部门|用户|状态)", q) is not None

    def _pending_sql_tool_calls_from_history(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        """找出历史中已声明但未落库执行的 execute_sql_query 调用（常见于计划护栏拦截后空转）。"""
        executed_ids = {
            m.tool_call_id for m in messages if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)
        }
        for msg in reversed(messages):
            if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
                continue
            pending = [
                tc for tc in msg.tool_calls
                if tc.get("name") == "execute_sql_query" and tc.get("id") not in executed_ids
            ]
            if pending:
                return pending
        return []

    def _try_parse_json_output(self, tool_output: Any) -> Any:
        """
        Best-effort parse tool output into structured JSON for preview/analysis.
        The external SQL API usually returns JSON string (list/dict).
        """
        if isinstance(tool_output, (list, dict)):
            return tool_output
        if not isinstance(tool_output, str):
            return tool_output
        s = tool_output.strip()
        if not s or s[0] not in "[{":
            return tool_output
        try:
            return json.loads(s)
        except Exception:
            return tool_output

    def _detect_ratio_anomaly(self, parsed: Any) -> Optional[str]:
        """
        Heuristic checks for 'partially correct' ratio outputs:
        - many rows with 0/100/extreme values
        - ratio columns outside expected range
        Returns a short reason string if anomaly is detected.
        """
        if not isinstance(parsed, list) or len(parsed) < 3:
            return None
        # Only handle list[dict] to keep this safe and generic.
        if not all(isinstance(r, dict) for r in parsed[: min(len(parsed), 10)]):
            return None

        # Find candidate ratio columns by name.
        sample_keys = set()
        for r in parsed[: min(len(parsed), 20)]:
            sample_keys.update(r.keys())
        ratio_cols = [k for k in sample_keys if re.search(r"(rate|ratio|pct|percent|percentage|load_rate|utilization)", str(k), re.IGNORECASE)]
        if not ratio_cols:
            return None

        def to_float(v):
            try:
                if v is None:
                    return None
                if isinstance(v, bool):
                    return float(v)
                if isinstance(v, (int, float)):
                    return float(v)
                sv = str(v).strip().replace("%", "")
                if sv == "":
                    return None
                return float(sv)
            except Exception:
                return None

        checks = []
        for col in ratio_cols[:3]:  # cap
            vals = []
            for r in parsed:
                fv = to_float(r.get(col))
                if fv is not None:
                    vals.append(fv)
            if len(vals) < 3:
                continue

            # If values look like 0-1, treat as ratio; else 0-100 as percent.
            v_min, v_max = min(vals), max(vals)
            is_ratio_01 = v_max <= 1.5
            # Count extremes
            if is_ratio_01:
                extreme = sum(1 for v in vals if v <= 0.001 or v >= 0.999)
                out_of_range = sum(1 for v in vals if v < -0.01 or v > 1.01)
            else:
                extreme = sum(1 for v in vals if v <= 0.1 or v >= 99.9)
                out_of_range = sum(1 for v in vals if v < -1 or v > 101)

            extreme_ratio = extreme / max(len(vals), 1)
            out_ratio = out_of_range / max(len(vals), 1)
            # Heuristic: many extremes OR any out-of-range indicates likely grain/join/denom issues.
            if out_ratio > 0:
                checks.append(f"列 {col} 存在超出合理区间的值")
            elif extreme_ratio >= 0.6 and (v_max - v_min) > (0.5 if is_ratio_01 else 50):
                checks.append(f"列 {col} 出现大量接近 0/100 的极端分布")

        if not checks:
            return None
        return "；".join(checks[:2])

    def _is_no_authorized_schema(self, tool_output: Any) -> bool:
        s = str(tool_output or "")
        return ("No authorized datasets found" in s) or ("未找到相关的授权数据集" in s)

    def _convert_history(self, history: List[Dict[str, str]]) -> List[BaseMessage]:
        return convert_history_to_messages(history)

    def _current_user_id(self) -> Optional[int]:
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _is_last_result_followup(self, user_question: str) -> bool:
        """K2 纯加工追问：复用上一轮结构化结果，同句不含新的查数诉求。"""
        return looks_like_pure_result_followup(user_question)

    async def _load_last_data_result_for_followup(self, user_question: str) -> Optional[Dict[str, Any]]:
        if not self.conversation_id or not self._is_last_result_followup(user_question):
            return None
        return await self._load_last_data_result()

    async def _load_last_data_result(self) -> Optional[Dict[str, Any]]:
        """无条件加载本会话上一轮结构化查询结果（用于 K3 动作类请求注入上下文）。"""
        if not self.conversation_id:
            return None
        user_id = self._current_user_id()
        if not user_id:
            return None
        try:
            from app.services.ai.memory_service import memory_service
            return await memory_service.get_last_data_result(user_id, self.conversation_id)
        except Exception as e:
            logger.warning(f"[DataExecutor] Failed to load last data result: {e}")
            return None

    async def _save_last_data_result_for_followups(self, tool_call: Dict[str, Any], parsed_tool_output: Any) -> None:
        if not self.conversation_id or tool_call.get("name") != "execute_sql_query":
            return
        if not isinstance(parsed_tool_output, (list, dict)):
            return
        user_id = self._current_user_id()
        if not user_id:
            return

        args = tool_call.get("args") or {}
        payload = {
            "sql": args.get("sql") or args.get("query"),
            "data_source": args.get("data_source"),
            "dataset_name": args.get("dataset_name"),
            "rows": parsed_tool_output,
            "saved_at": datetime.now().isoformat(),
            "trace_id": self.trace_id,
        }
        try:
            from app.services.ai.memory_service import memory_service
            await memory_service.set_last_data_result(user_id, self.conversation_id, payload)
        except Exception as e:
            logger.warning(f"[DataExecutor] Failed to save last data result: {e}")

    async def _synthesize_from_last_data_result(
        self,
        langchain_messages: List[BaseMessage],
        system_prompt: str,
        user_question: str,
        last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"reuse_{uuid.uuid4().hex[:8]}",
            "title": "复用上一轮查询结果",
            "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
            "status": "success",
        }
        yield {"type": "thinking", "status": "continuing"}

        prompt_without_menu = (system_prompt or "").replace("{dataset_menu}", DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER)
        result_json = json.dumps(last_result, ensure_ascii=False, indent=2)
        if len(result_json) > 30000:
            result_json = result_json[:30000] + "\n... [上一轮结果过长已截断]"

        synthesis_messages: List[BaseMessage] = [SystemMessage(content=prompt_without_menu)]
        for msg in langchain_messages[-6:-1]:
            if isinstance(msg, (HumanMessage, AIMessage)) and getattr(msg, "content", None):
                synthesis_messages.append(msg)
        synthesis_messages.append(HumanMessage(content=DataQueryPrompts.followup_synthesis_user_message(user_question, result_json)))

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        accumulated_msg = None
        try:
            async for chunk in final_llm.astream(synthesis_messages):
                if accumulated_msg is None:
                    accumulated_msg = chunk
                else:
                    accumulated_msg += chunk
                if chunk.content:
                    if not content_emitted:
                        generation_start = time.time()
                        yield {"type": "log", "id": gen_log_id, "title": "✨ 开始生成回复", "status": "pending", "started_at": int(generation_start * 1000)}
                    content_emitted = True
                    full_synthesis_content += chunk.content
                    yield {"content": chunk.content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error(f"[DataExecutor] Follow-up synthesis failed: {syn_err}")
            fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
            full_synthesis_content = fallback
            yield {"type": "log", "id": f"syn_err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 总结生成失败", "details": str(syn_err), "status": "error"}
            yield {"content": fallback}

        tokens = extract_tokens_from_message(accumulated_msg)

        self._increment_step()
        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter,
            event_type="synthesis",
            agent_name=self.config.agent_name,
            model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
            temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
            tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
            raw_log=full_synthesis_content,
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            timestamp=datetime.fromtimestamp(start_synthesis),
        ))

    async def _emit_direct_answer(self, final_text: str, start_thought: float) -> AsyncGenerator[Dict[str, Any], None]:
        """K3 非新查数轮：模型已直接基于上下文作答，将其内容作为最终回答输出并记录 trace。"""
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        yield {"type": "log", "id": gen_log_id, "title": "✨ 开始生成回复", "status": "pending", "started_at": int(time.time() * 1000)}
        yield {"content": final_text}
        yield {
            "type": "log",
            "id": gen_log_id,
            "title": "✨ 生成回复完成",
            "status": "success",
            "execution_time_ms": (time.time() - start_thought) * 1000,
        }
        self._increment_step()
        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter,
            event_type="synthesis",
            agent_name=self.config.agent_name,
            model=str(self.config.model_name),
            temperature=float(self.config.temperature or 0),
            tool_output={"content": final_text, "context_action_direct_answer": True},
            raw_log=final_text,
            execution_time_ms=(time.time() - start_thought) * 1000,
            timestamp=datetime.fromtimestamp(start_thought),
        ))

    async def execute(self, history: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.multimodal_support import (
            ensure_multimodal_compatible,
            resolve_runtime_model_name,
        )

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        TOOL_LABEL_MAP = {"get_dataset_schema": "检索数据集定义", "execute_sql_query": "执行 SQL 查询", "update_dashboard_context": "更新看板关联状态"}
        import json
        import re

        # 1. Build Messages
        langchain_messages = self._convert_history(history)
        system_prompt = self.config.system_prompt
        self._current_user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "")

        turn_cls = getattr(self, "turn_classification", None)
        if turn_cls is not None:
            self._requires_fresh_data = turn_cls.requires_fresh_data
            self._skip_few_shot = not turn_cls.requires_few_shot
        else:
            self._requires_fresh_data = not looks_like_context_action(self._current_user_question)
            self._skip_few_shot = not self._requires_fresh_data

        if self._is_last_result_followup(self._current_user_question):
            last_result = await self._load_last_data_result_for_followup(self._current_user_question)
            if last_result:
                async for chunk in self._synthesize_from_last_data_result(
                    langchain_messages,
                    system_prompt,
                    self._current_user_question,
                    last_result,
                ):
                    yield chunk
            else:
                yield {
                    "type": "log",
                    "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
                    "title": "缺少可复用查询结果",
                    "details": "检测到本轮是基于上一轮结果的分析/可视化请求，但当前会话没有保存的结构化查询结果。",
                    "status": "error",
                }
                yield {"content": DataQueryPrompts.NO_REUSABLE_RESULT}
            return

        # 2. Prepare Tools

        prep_tools_start = time.time()
        prep_tools_log_id = f"prep_tools_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": prep_tools_log_id,
            "title": "准备工具",
            "details": "正在加载本轮可用工具...",
            "status": "pending",
            "started_at": int(prep_tools_start * 1000),
        }
        configured_tools = self.config.tools or ["get_dataset_schema", "execute_sql_query", "update_dashboard_context"]
        # Hard requirement: DataQueryExecutor must be able to execute SQL.
        if "execute_sql_query" not in configured_tools:
            configured_tools = list(configured_tools) + ["execute_sql_query"]
        tools = await ToolRegistry.get_tools(configured_tools)
        if not tools: tools = await ToolRegistry.get_tools(ToolRegistry.DEFAULT_TOOL_SET)
        # 补全系统隐式工具（create_skills / 记忆 / 任务 / web 等），使本执行器在“非新查数”场景
        # （如保存结果、创建技能、记住偏好）也能直接调用对应工具，而不必再被拖入查数流程。
        try:
            system_tools = ToolRegistry.get_system_implicit_tools()
        except Exception as _ste:
            system_tools = []
            logger.warning(f"[DataExecutor] Failed to load system implicit tools: {_ste}")
        if system_tools:
            existing_names = {getattr(t, "name", None) for t in tools}
            for st in system_tools:
                if getattr(st, "name", None) not in existing_names:
                    tools.append(st)
        tool_names = {getattr(t, "name", None) for t in tools}
        tool_names.discard(None)
        yield {
            "type": "log",
            "id": prep_tools_log_id,
            "title": "准备工具完成",
            "details": f"已加载工具: {', '.join(sorted(str(name) for name in tool_names)) if tool_names else '无'}",
            "status": "success",
            "execution_time_ms": (time.time() - prep_tools_start) * 1000,
        }

        # Reset per-execution state to avoid leaking across multiple execute() calls
        # (unit tests may reuse the same executor instance).
        self._ratio_anomaly_feedback_sent = False
        self._schema_fetched_ok = False
        self._schema_no_authorized = False
        self._metadata_unavailable = False
        self._sql_attempted = False
        self._sql_succeeded = False
        self._sql_after_schema_nudge_sent = False
        self._no_tool_call_streak = 0
        self._non_data_tool_called = False
        self._sql_plan_block_used = False
        # 注：_requires_fresh_data / _skill_ready / _needs_skill_prep 在本轮入口或下方按最终 system_prompt 计算。
        
        # [经验库] Few-Shot 检索与注入（K1 / 技能执行等需要新 SQL 的轮次才检索）
        _few_shot_reminder = ""
        _schema_reminder_injected = False
        fewshot_start = time.time()
        fewshot_log_id = f"fewshot_search_{uuid.uuid4().hex[:8]}"
        if self._skip_few_shot:
            yield {
                "type": "log",
                "id": fewshot_log_id,
                "title": "跳过经验库检索",
                "details": "本轮无需新 SQL 生成，已跳过经验库检索以节省延迟。",
                "status": "success",
                "execution_time_ms": 0,
            }
        else:
            yield {
                "type": "log",
                "id": fewshot_log_id,
                "title": "检索经验库",
                "details": "正在查找可复用的历史优质 SQL 案例...",
                "status": "pending",
                "started_at": int(fewshot_start * 1000),
            }
            try:
                from app.services.chatbi_example_service import ExampleService
                user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "")
                if user_question:
                    examples = await ExampleService.search_examples(
                        user_question, 
                        dataset_id=None, 
                        top_k=5,
                        history=langchain_messages
                    )
                    if examples:
                        max_sim = max([ex.get('similarity', 0) for ex in examples])
                        hit_titles = [f"#{ex.get('id', '?')} 「{ex['question'][:15]}...」 (相似度: {ex.get('similarity', 0):.2f})" for ex in examples]
                        sim_status = "匹配度极高" if max_sim >= 0.80 else "匹配度一般"
                        yield {
                            "type": "log", 
                            "id": f"fewshot_{uuid.uuid4().hex[:6]}", 
                            "title": f"✨ 命中经验库案例 ({len(examples)}条, {sim_status})", 
                            "details": f"已匹配到历史优质 SQL 案例：\n" + "\n".join(hit_titles) + f"\n\n当前最高相似度: {max_sim:.2f}。\n这些案例将作为强制性参考引导模型生成 SQL，以减少冗余迭代。",
                            "status": "success"
                        }
                        
                        few_shot_block = ExampleService.build_few_shot_prompt(examples)
                        if few_shot_block:
                            system_prompt = few_shot_block + "\n\n---\n\n" + system_prompt
                            logger.info(f"[FewShot] Injected {len(examples)} examples at HEAD of system prompt for trace_id: {self.trace_id}")
                        
                        _few_shot_reminder = ExampleService.build_few_shot_reminder(examples)
                        
                        try:
                            example_ids = [ex["id"] for ex in examples if ex.get("id")]
                            similarities = [ex.get("similarity", 0) for ex in examples if ex.get("id")]
                            if example_ids:
                                asyncio.create_task(ExampleService.record_usage(example_ids, self.trace_id, similarities=similarities))
                        except Exception as ree:
                            logger.error(f"[FewShot] Failed to record usage statistic: {ree}")
            except Exception as fe:
                logger.warning(f"[FewShot] Failed to search/inject examples: {fe}")
            yield {
                "type": "log",
                "id": fewshot_log_id,
                "title": "检索经验库完成",
                "details": "经验库检索完成，继续构建本轮提示上下文。",
                "status": "success",
                "execution_time_ms": (time.time() - fewshot_start) * 1000,
            }

        if "{dataset_menu}" in system_prompt:
            # 注入用户权限信息
            user_id = self.user_info.get("user_id") if self.user_info else None
            is_admin = self.user_info.get("role") == "admin" if self.user_info else False
            
            dataset_menu_start = time.time()
            dataset_menu_log_id = f"dataset_menu_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": dataset_menu_log_id,
                "title": "加载数据集菜单",
                "details": "正在按当前用户权限加载可见数据集菜单...",
                "status": "pending",
                "started_at": int(dataset_menu_start * 1000),
            }
            dataset_menu = await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)
            system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
            yield {
                "type": "log",
                "id": dataset_menu_log_id,
                "title": "加载数据集菜单完成",
                "details": "已完成数据集菜单注入。",
                "status": "success",
                "execution_time_ms": (time.time() - dataset_menu_start) * 1000,
            }
        langchain_messages.insert(0, SystemMessage(content=system_prompt))

        # --- [Accuracy Upgrade] Force Plan -> SQL workflow (generic) ---
        # We add a strict instruction once per executor instance to reduce grain/join errors.
        if not self._sql_plan_enforcement_added:
            langchain_messages.append(SystemMessage(content=DataQueryPrompts.SQL_PLAN_ENFORCEMENT))
            langchain_messages.append(SystemMessage(content=DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT))
            self._sql_plan_enforcement_added = True

        # 技能：摘要已注入 [Active Skills Loaded] 时，全文须 read_skill_instruction 后才算就绪。
        self._skill_matched = self._active_skills_in_system_prompt(system_prompt)
        self._skill_ready = False
        self._needs_skill_prep = self._skill_matched or looks_like_skill_execution(
            self._current_user_question
        )
        if self._skill_matched and not self._skill_ready:
            langchain_messages.append(SystemMessage(content=DataQueryPrompts.MUST_READ_MATCHED_SKILLS))

        # K3（对已有结果做动作/可直接基于上下文作答）：注入上一轮结构化结果并放宽“先查库”约束，
        # 让模型可以直接调用工具（保存/导出/记忆/创建技能等）或基于上下文作答，而非机械重查。
        if not self._requires_fresh_data:
            last_result = await self._load_last_data_result()
            result_block = ""
            if last_result:
                result_json = json.dumps(last_result, ensure_ascii=False)
                if len(result_json) > 20000:
                    result_json = result_json[:20000] + "\n... [上一轮结果过长已截断]"
                result_block = result_json
            langchain_messages.append(SystemMessage(content=DataQueryPrompts.context_action_guide(result_block)))

        # 3. Model Setup
        # Use streaming for orchestration to allow potential turn-1 short-circuit
        model_setup_start = time.time()
        model_setup_log_id = f"model_setup_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": model_setup_log_id,
            "title": "准备模型",
            "details": "正在初始化模型并绑定工具...",
            "status": "pending",
            "started_at": int(model_setup_start * 1000),
        }
        model_with_tools = await AgentConfigProvider.get_configured_llm(streaming=True, config=self.config)
        model_with_tools = model_with_tools.bind_tools(tools)
        yield {
            "type": "log",
            "id": model_setup_log_id,
            "title": "准备模型完成",
            "details": "模型和工具绑定完成，准备进入首轮决策。",
            "status": "success",
            "execution_time_ms": (time.time() - model_setup_start) * 1000,
        }

        from app.services.config_service import ConfigService
        max_steps_str = await ConfigService.get("agent_max_iterations")
        raw_max = int(max_steps_str) if max_steps_str else 6
        MAX_STEPS = min(raw_max, DATA_QUERY_MAX_STEPS_CAP)
        step = 0
        has_critical_error = False

        while step < MAX_STEPS:
            step += 1
            self._increment_step()
            yield {"type": "thinking", "status": "continuing"}
            
            start_thought = time.time()
            thought_log_id = f"thought_{self.step_counter}_{uuid.uuid4().hex[:6]}"
            yield {
                "type": "log",
                "id": thought_log_id,
                "title": f"模型决策: 第 {step} 轮",
                "details": "模型正在判断下一步动作和工具参数...",
                "status": "pending",
                "started_at": int(start_thought * 1000),
                "category": "tool",
            }
            accumulated_content = ""
            accumulated_msg = None
            has_tool_call_indicator = False

            try:
                async for chunk in model_with_tools.astream(langchain_messages):
                    if accumulated_msg is None: accumulated_msg = chunk
                    else: accumulated_msg += chunk
                    
                    if chunk.content:
                        if "<function_calls" in (accumulated_content + chunk.content):
                            has_tool_call_indicator = True
                        accumulated_content += chunk.content
            except Exception as stream_err:
                logger.error(f"[DataExecutor] Stream error during thought at step {step}: {stream_err}")
                yield {"type": "log", "id": f"err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 模型响应异常", "details": f"流式输出中断: {str(stream_err)}", "status": "error"}
                break

            response = accumulated_msg
            tokens = extract_tokens_from_message(response)
            if response is None:
                if step == 1 and not accumulated_content:
                    return # No response from model
                # Fallback to an empty message if we have content but no message object
                response = AIMessage(content=accumulated_content)

            if not response.tool_calls and "<function_calls>" in accumulated_content:
                response.tool_calls = parse_xml_tool_calls(accumulated_content)

            thought_elapsed_ms = (time.time() - start_thought) * 1000
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter, event_type="thought", agent_name=self.config.agent_name,
                model=str(self.config.model_name), temperature=float(self.config.temperature or 0),
                tool_output={"content": accumulated_content, "tool_calls": [tc['name'] for tc in response.tool_calls]},
                raw_log=accumulated_content, execution_time_ms=thought_elapsed_ms,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_thought)
            ))
            yield {
                "type": "log",
                "id": thought_log_id,
                "title": f"模型决策完成: 第 {step} 轮",
                "details": (
                    "本轮模型已完成内部决策。"
                    f"\n工具调用: {', '.join(tc['name'] for tc in response.tool_calls) if response.tool_calls else '无'}"
                ),
                "status": "success",
                "category": "tool",
                "execution_time_ms": thought_elapsed_ms,
            }

            if not response.tool_calls:
                # If SQL has already succeeded, don't prolong the loop on empty tool calls.
                # Break to final synthesis to reduce latency.
                if self._sql_succeeded:
                    langchain_messages.append(response)
                    break

                # 元操作收尾：本轮已成功执行非数据类工具（如 create_skills）且未做数据查询，
                # 说明这是对已有上下文的封装/保存类动作，不应再被“先查库”护栏逼回查询流程。
                if self._non_data_tool_called and not self._sql_attempted:
                    langchain_messages.append(response)
                    break

                # K3（非新查数轮）且未查库：模型选择直接基于上下文作答而非调用工具。
                # 不强制其去查 Schema/SQL，直接把本轮内容作为最终回答收尾。
                if not self._requires_fresh_data and not self._sql_attempted:
                    langchain_messages.append(response)
                    final_text = (accumulated_content or "").strip()
                    if final_text:
                        async for ch in self._emit_direct_answer(final_text, start_thought):
                            yield ch
                        return
                    break

                # Schema 已就绪但上一轮 SQL 工具调用被计划护栏拦截未执行：代为执行，避免空转。
                pending_sql = self._pending_sql_tool_calls_from_history(langchain_messages)
                if (
                    self._requires_fresh_data
                    and self._schema_fetched_ok
                    and not self._schema_no_authorized
                    and not self._sql_attempted
                    and pending_sql
                ):
                    yield {
                        "type": "log",
                        "id": f"pending_sql_{uuid.uuid4().hex[:8]}",
                        "title": "执行待处理 SQL",
                        "details": (
                            "检测到 Schema 已就绪，但先前轮次的 execute_sql_query 尚未真正执行。"
                            "系统将代为执行该 SQL，跳过一次空转决策。"
                        ),
                        "status": "success",
                        "execution_time_ms": 0,
                    }
                    response = AIMessage(content="", tool_calls=pending_sql)
                else:
                    self._no_tool_call_streak += 1
                    if self._no_tool_call_streak in (2, 4):
                        yield {
                            "type": "log",
                            "id": f"diag_{uuid.uuid4().hex[:8]}",
                            "title": "🧭 调用工具诊断",
                            "details": (
                                f"检测到模型连续 {self._no_tool_call_streak} 次未产生工具调用（tool_calls 为空）。\n"
                                "为确保数据准确性，系统将继续强制引导调用 get_dataset_schema / execute_sql_query。\n"
                                "若仍无法触发，请检查：模型是否支持工具调用、输出是否包含 <function_calls>、以及 agent 是否配置了 data_query 能力。"
                            ),
                            "status": "success",
                            "execution_time_ms": (time.time() - start_thought) * 1000,
                        }
                    # Keep backward-compatible procrastination nudge (Turn 1, chatty, no tools)
                    # Some unit tests and UI flows rely on this exact wording.
                    if step == 1 and len(accumulated_content or "") > 50:
                        langchain_messages.append(response)
                        langchain_messages.append(SystemMessage(content=DataQueryPrompts.NUDGE_DESCRIBE_PLAN_NO_TOOL))
                        continue
                    # If required tools are not available, don't spin in enforcement loops.
                    # This happens in some unit tests (only schema tool is provided).
                    if self._schema_fetched_ok and "execute_sql_query" not in tool_names:
                        langchain_messages.append(response)
                        break
                    # 用户要求使用技能但尚未加载技能指令：优先引导 list/read_skill，而非直接逼查 Schema。
                    if self._needs_skill_prep and not self._skill_ready:
                        langchain_messages.append(response)
                        langchain_messages.append(SystemMessage(content=DataQueryPrompts.MUST_LOAD_SKILL_FIRST))
                        continue
                    # Hard rule: DataQueryExecutor is not allowed to answer without querying data.
                    if not self._schema_fetched_ok:
                        must_msg = DataQueryPrompts.MUST_FETCH_SCHEMA
                    elif not self._schema_no_authorized and not self._sql_attempted:
                        must_msg = DataQueryPrompts.MUST_EXECUTE_SQL
                    elif not self._schema_no_authorized and self._sql_attempted and not self._sql_succeeded:
                        must_msg = DataQueryPrompts.MUST_FIX_SQL
                    else:
                        must_msg = DataQueryPrompts.CONTINUE_OR_SUMMARIZE
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=must_msg))
                    continue

            self._no_tool_call_streak = 0

            # --- [Plan Policy] Enforce plan only for high-risk queries; otherwise just nudge ---
            plan_blocked = False
            if any(tc.get("name") == "execute_sql_query" for tc in response.tool_calls) and not self._has_sql_plan(accumulated_content):
                require_plan = self._should_require_sql_plan(self._current_user_question)
                if require_plan and not self._sql_plan_block_used:
                    # 仅阻断一次；若模型仍不补计划，下一轮直接执行 SQL，避免「有 tool_calls 却无查询完成」的空转。
                    self._sql_plan_block_used = True
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=DataQueryPrompts.HIGH_RISK_REQUIRE_PLAN))
                    plan_blocked = True
                elif not require_plan:
                    langchain_messages.append(SystemMessage(content=DataQueryPrompts.PLAN_NUDGE_NON_BLOCKING))
            if plan_blocked:
                continue

            # Hard rule: after schema is fetched, you must execute SQL before doing anything else.
            # （K3 非新查数轮不强制；技能尚未就绪时也不强制，应先 read_skill_instruction。）
            if (
                self._requires_fresh_data
                and (self._skill_ready or not self._needs_skill_prep)
                and self._schema_fetched_ok
                and not self._schema_no_authorized
                and not self._sql_attempted
            ):
                has_sql_call = any(tc.get("name") == "execute_sql_query" for tc in response.tool_calls)
                if not has_sql_call:
                    langchain_messages.append(response)
                    langchain_messages.append(SystemMessage(content=DataQueryPrompts.MUST_EXECUTE_SQL_AFTER_SCHEMA))
                    continue

            # Process Tool Calls
            langchain_messages.append(response)
            parallel_tasks = []
            for tool_call in response.tool_calls:
                if not tool_call.get("id"): tool_call["id"] = f"call_{uuid.uuid4().hex[:8]}"
                tool_display = TOOL_LABEL_MAP.get(tool_call['name'], tool_call['name'])
                
                yield {
                    "type": "log",
                    "id": tool_call['id'],
                    "title": f"调用工具: {tool_display}",
                    "details": f"参数: {json.dumps(tool_call['args'], ensure_ascii=False)}",
                    "status": "pending",
                    "started_at": int(time.time() * 1000),
                }
                parallel_tasks.append(self._dispatch_tool_safe(tool_call, tools))
            # Parallel Execute
            execution_task = asyncio.ensure_future(asyncio.gather(*parallel_tasks))
            waited_seconds = 0
            while not execution_task.done():
                done, _ = await asyncio.wait([execution_task], timeout=1.5)
                if execution_task in done: break
                waited_seconds += 1.5
                for tc in response.tool_calls:
                     yield {
                         "type": "log",
                         "id": tc['id'],
                         "title": f"正在查询: {TOOL_LABEL_MAP.get(tc['name'], tc['name'])}",
                         "status": "pending",
                         "elapsed_time_ms": waited_seconds * 1000,
                     }

            results = execution_task.result()
            needs_followup_after_sql = False
            for tool_call, tool_output, duration_tool in results:
                # [Data Permission Tracing]
                # Merge trace logs into the tool's details instead of a separate card
                from app.core.context import get_current_agent_context
                ctx = get_current_agent_context()
                
                tool_display = TOOL_LABEL_MAP.get(tool_call['name'], tool_call['name'])

                # Try to parse tool output for better preview and downstream anomaly detection
                parsed_tool_output = self._try_parse_json_output(tool_output)
                final_details = str(tool_output)[:5000]

                # --- [修复] 先分析工具状态 ---
                tool_status, error_msg, is_sys_err = self._analyze_result(tool_output)
                if is_sys_err: 
                    has_critical_error = True

                # 记录是否调用了“非数据类工具”（如 create_skills 等元操作/外部动作），
                # 用于放松“必须先查库”的护栏：本轮若是元操作则允许在未查库时收尾。
                if tool_status == "success" and tool_call['name'] not in self.DATA_TOOL_NAMES:
                    self._non_data_tool_called = True
                if tool_status == "success" and tool_call['name'] == "read_skill_instruction":
                    self._skill_ready = True
                    self._needs_skill_prep = False

                # --- [优化] SQL 执行日志显示增强 ---
                # 强制所有 execute_sql_query 都显示SQL语句，不管状态如何
                if tool_call['name'] == "execute_sql_query":
                    self._sql_attempted = True
                    executed_sql = tool_call['args'].get('sql', '未知 SQL')
                    # 显示 SQL 和 结果摘要
                    results_preview = ""
                    if tool_status == "success":
                        self._sql_succeeded = True
                        await self._save_last_data_result_for_followups(tool_call, parsed_tool_output)
                        if isinstance(parsed_tool_output, list):
                            results_preview = f"\n\n✅ [查询成功]: 命中 {len(parsed_tool_output)} 行数据。"
                            if len(parsed_tool_output) > 0:
                                preview_rows = parsed_tool_output[:3]
                                results_preview += f"\n预览前{len(preview_rows)}行:\n" + json.dumps(preview_rows, ensure_ascii=False, indent=2)
                        elif isinstance(parsed_tool_output, dict):
                            results_preview = f"\n\n✅ [查询成功]: 返回对象结构。"
                            results_preview += "\n预览:\n" + json.dumps(parsed_tool_output, ensure_ascii=False, indent=2)[:800]
                        else:
                            results_preview = f"\n\n✅ [查询成功]: {str(tool_output)[:500]}"
                    elif tool_status == "error":
                        results_preview = f"\n\n❌ [执行失败]: {error_msg}"

                    final_details = f"📊 [执行 SQL]:\n```sql\n{executed_sql}\n```" + results_preview
                    logger.info(f"[DEBUG] SQL display with results: {executed_sql[:50]}...")

                    # --- [Accuracy Feedback] Detect ratio anomalies and request one-time self-correction ---
                    if tool_status == "success" and not self._ratio_anomaly_feedback_sent:
                        anomaly_reason = self._detect_ratio_anomaly(parsed_tool_output)
                        if anomaly_reason:
                            self._ratio_anomaly_feedback_sent = True
                            needs_followup_after_sql = True
                            langchain_messages.append(SystemMessage(content=DataQueryPrompts.ratio_anomaly_recheck(anomaly_reason)))

                # --- [新增] 针对元数据检索日志的精简与增强逻辑 ---
                if tool_call['name'] == "get_dataset_schema":
                    # 元数据服务不可用（如 RAGFlow 502）：禁止继续臆造 SQL，必须硬终止本次查询。
                    schema_out_str = tool_output if isinstance(tool_output, str) else json.dumps(tool_output, ensure_ascii=False)
                    if "[元数据服务不可用]" in schema_out_str:
                        self._metadata_unavailable = True
                        tool_status = "error"
                    if tool_status == "success" and not self._metadata_unavailable:
                        self._schema_fetched_ok = True
                        if self._is_no_authorized_schema(tool_output):
                            self._schema_no_authorized = True
                        # After schema is fetched, force an explicit, tool-call-only nudge once.
                        if (not self._schema_no_authorized) and (not self._sql_attempted) and (not self._sql_after_schema_nudge_sent):
                            self._sql_after_schema_nudge_sent = True
                            langchain_messages.append(SystemMessage(content=DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA))
                    keywords = tool_call['args'].get('keywords', '未指定')
                    # 在日志详情顶部增加检索词
                    header = f"🔍 [检索关键词]: {keywords}\n" + "-"*30 + "\n"
                    
                    # 确保 [置信度: 0.XX] 的显示（如果 data_api 没加上，这里做兼容）
                    content = tool_output
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False)

                    if "[置信度:" not in content:
                        # 尝试从内容中匹配置信度并提升到头部
                        content = re.sub(r"IsRelevant:\s*([\d\.]+)", r"[置信度: \1]", content)
                    
                    # 精简显示：仅移除详情中的 columns 列表，防止吞掉后续数据集
                    # 改进正则：匹配 columns: 到下一个主要标记（置信度、Source、Dataset、Relationships或字符串结尾），
                    # 使用更严谨的边界判断，避免在 Local Mode 或大块 RAG 结果中过量匹配。
                    # 注意：YAML 中的 columns 通常是缩进的，这里暂用多行匹配并排除后续 Dataset/Source/Relationships 标记。
                    simplified_content = re.sub(r"columns:.*?(\nrelationships:|\n\[置信度:|\n--- Source:|\n--- Dataset:|\Z)", r"columns: [已隐藏具体字段定义，仅 AI 可见]\1", content, flags=re.DOTALL)

                    
                    # 如果精简后完全没变化且内容很长，说明可能没匹配到 columns 标记，直接使用 content
                    # [优化] 增加安全截断，防止极端情况下（如上百个数据集）发送超大负载
                    final_details = header + (simplified_content if simplified_content else content)
                    if len(final_details) > 15000:
                        final_details = final_details[:15000] + "\n\n... [内容过长已截断，请点击右侧详情图标查看完整 Trace]"
                    
                    # 兜底：如果 final_details 为空（不应该发生），确保至少有 header
                    if not final_details.strip():
                        final_details = header + "[检索完成，未发现匹配的数据集定义]"
                
                if tool_call['name'] == "execute_sql_query" and ctx and hasattr(ctx, "trace_logs") and ctx.trace_logs:
                    # Prepend rewrite info to details
                    trace_summary = "\n".join(ctx.trace_logs)
                    final_details = f"🔒 动态数据权限重写记录:\n{trace_summary}\n\n--- 原始查询结果 ---\n{final_details}"
                    tool_display = f"🔒 [权限重写] {tool_display}"
                    # Clear for next tool in same step if any
                    ctx.trace_logs = []
                
                target_inst = next((t for t in tools if t.name == tool_call["name"]), None)
                runtime_cfg = getattr(target_inst, "_runtime_config", None)
                t_model = getattr(runtime_cfg, "model_name", self.config.model_name)
                t_temp = getattr(runtime_cfg, "temperature", self.config.temperature)
                
                # Ensure types for AgentExecutionStep validation (especially when using Mocks in tests)
                if not isinstance(t_model, str): t_model = str(self.config.model_name)
                if not isinstance(t_temp, (int, float)): t_temp = float(self.config.temperature or 0)

                # Store structured tool output when possible (improves synthesis accuracy)
                tool_output_for_trace = parsed_tool_output
                self.trace_buffer.append(AgentExecutionStep(
                    step_number=self.step_counter, event_type="tool_call", agent_name=self.config.agent_name,
                    model=t_model, temperature=t_temp, tool_name=tool_call["name"], tool_input=tool_call["args"],
                    tool_output=tool_output_for_trace if isinstance(tool_output_for_trace, (dict, list)) else {"raw": tool_output},
                    execution_time_ms=duration_tool, status=tool_status, error_message=error_msg, timestamp=datetime.now()
                ))

                yield {
                    "type": "log",
                    "id": tool_call['id'],
                    "title": f"查询完成: {tool_display}",
                    "details": final_details,
                    "status": tool_status,
                    "execution_time_ms": duration_tool,
                }
                
                feedback = tool_output
                if tool_status == "error": feedback = self._get_self_healing_feedback(tool_call["name"], tool_output)
                if tool_call["name"] == "update_dashboard_context" and tool_status == "success":
                    yield {"type": "context_update", "data": tool_call["args"]}
                langchain_messages.append(ToolMessage(content=str(feedback), tool_call_id=tool_call["id"]))
                if tool_status == "success" and tool_call["name"] == "read_skill_instruction":
                    langchain_messages.append(SystemMessage(content=DataQueryPrompts.SKILL_EXECUTION_GUIDE))

                # 元数据服务不可用：硬终止，直接给出明确失败结论，绝不进入 execute_sql_query 臆造查询。
                if self._metadata_unavailable:
                    yield {
                        "type": "log",
                        "id": f"abort_{uuid.uuid4().hex[:8]}",
                        "title": "⛔ 终止：元数据服务不可用",
                        "details": "元数据检索服务（RAGFlow）不可用，已终止本次数据查询，避免编造表结构或 SQL。",
                        "status": "error",
                        "execution_time_ms": 0,
                    }
                    yield {"content": DataQueryPrompts.METADATA_UNAVAILABLE}
                    return

                # [优化2] get_dataset_schema 成功返回后，立即注入经验库案例二次提醒
                # 让模型在看完实时 Schema 数据后马上重新聚焦到参考案例的 SQL 逻辑
                if (tool_call["name"] == "get_dataset_schema" and tool_status == "success"
                        and _few_shot_reminder and not _schema_reminder_injected):
                    langchain_messages.append(SystemMessage(content=_few_shot_reminder))
                    _schema_reminder_injected = True
                    logger.info(f"[FewShot] Injected schema-reminder SystemMessage after get_dataset_schema")

            if has_critical_error:
                break

            if self._sql_succeeded and not needs_followup_after_sql:
                yield {
                    "type": "log",
                    "id": f"fast_path_{uuid.uuid4().hex[:8]}",
                    "title": "跳过额外决策",
                    "details": "SQL 已成功返回且未触发异常复核，直接进入汇总阶段。",
                    "status": "success",
                    "execution_time_ms": 0,
                }
                break

        # 5. Final Synthesis
        tool_msgs = [m for m in langchain_messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            # Should not happen in DataQueryExecutor, but keep safe.
            return

        # Hard gate: never synthesize a final answer without executing SQL
        # （例外：本轮非新查数(K3)、无授权数据集，或已完成的元操作/外部动作如 create_skills）。
        if self._requires_fresh_data and not self._sql_attempted and not self._schema_no_authorized and not self._non_data_tool_called:
            yield {"type": "log", "id": f"gate_{uuid.uuid4().hex[:8]}", "title": "⚠️ 缺少 SQL 查询", "details": DataQueryPrompts.GATE_NO_SQL_LOG_DETAILS, "status": "error"}
            yield {"content": DataQueryPrompts.GATE_NO_SQL_CONTENT}
            return

        start_synthesis = time.time()
        syn_log_id = f"syn_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": syn_log_id,
            "title": "📝 汇总数据",
            "details": "正在分析查询结果...",
            "status": "pending",
            "started_at": int(start_synthesis * 1000),
        }
        yield {"type": "thinking", "status": "continuing"}

        # --- [STRATEGY B+: Context-Aware Synthesis] ---
        synthesis_messages = []
        # 1. System
        synthesis_messages.append(langchain_messages[0])
        # 2. Clean History (Skip current last human message)
        for msg in langchain_messages[1:-1]:
            if isinstance(msg, HumanMessage):
                synthesis_messages.append(msg)
            elif isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
                synthesis_messages.append(msg)
        
        # 3. Current Question + Data
        user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "无原始问题")
        
        # [IMPROVED] Use structured trace instead of just raw tool outputs
        execution_review = self._format_trace_for_synthesis(self.trace_buffer)
        
        synthesis_messages.append(HumanMessage(content=DataQueryPrompts.synthesis_user_message(user_question, execution_review)))

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        content_emitted = False
        full_synthesis_content = ""
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        accumulated_msg = None
        try:
            async for chunk in final_llm.astream(synthesis_messages):
                if accumulated_msg is None:
                    accumulated_msg = chunk
                else:
                    accumulated_msg += chunk
                if chunk.content:
                    if not content_emitted:
                         generation_start = time.time()
                         yield {
                             "type": "log",
                             "id": syn_log_id,
                             "title": "📝 汇总数据完成",
                             "details": "已完成查询结果分析，开始生成最终回复。",
                             "status": "success",
                             "execution_time_ms": (time.time() - start_synthesis) * 1000,
                         }
                         yield {"type": "log", "id": gen_log_id, "title": "✨ 开始生成回复", "status": "pending", "started_at": int(generation_start * 1000)}
                    content_emitted = True
                    full_synthesis_content += chunk.content
                    yield {"content": chunk.content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error(f"[DataExecutor] Stream error during synthesis: {syn_err}")
            yield {"type": "log", "id": f"syn_err_{uuid.uuid4().hex[:6]}", "title": "⚠️ 总结生成失败", "details": f"模型在总结阶段返回异常: {str(syn_err)}", "status": "error"}
            # 即使合成失败，也尝试将累积的内容保存或返回
            if not full_synthesis_content:
                full_synthesis_content = DataQueryPrompts.SYNTHESIS_FAILED_FALLBACK
                yield {"content": full_synthesis_content}
        
        self._increment_step()
        s_model = getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)
        s_temp = self.config.synthesis_temperature or self.config.temperature
        
        if not isinstance(s_model, str): s_model = str(self.config.model_name)
        if not isinstance(s_temp, (int, float)): s_temp = float(self.config.temperature or 0.7)

        tokens = extract_tokens_from_message(accumulated_msg)

        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter, event_type="synthesis", agent_name=self.config.agent_name,
            model=s_model,
            temperature=s_temp,
            tool_output={"content": full_synthesis_content}, raw_log=full_synthesis_content,
            execution_time_ms=(time.time() - start_synthesis) * 1000, 
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            timestamp=datetime.fromtimestamp(start_synthesis)
        ))

    def _format_trace_for_synthesis(self, traces: List[AgentExecutionStep]) -> str:
        """
        Formats the execution trace into a readable summary for the synthesis model.
        """
        lines = ["【执行过程回顾】"]
        
        # Filter only relevant steps (thoughts and tool calls) from the current session
        # We assume traces are ordered.
        
        current_step_num = -1
        
        for trace in traces:
            # Skip router or init steps if any
            if trace.event_type not in ["thought", "tool_call"]:
                continue
            
            if trace.step_number != current_step_num:
                lines.append(f"\nStep {trace.step_number}:")
                current_step_num = trace.step_number
            
            if trace.event_type == "thought":
                # Extract a summary of thought if possible, or just raw
                thought_content = trace.raw_log or ""
                # Try to clean up XML tags if present
                thought_content = re.sub(r'<function_calls>.*?</function_calls>', '', thought_content, flags=re.DOTALL)
                thought_content = thought_content.strip()
                if thought_content:
                    lines.append(f"  [思考] {thought_content}")
            
            elif trace.event_type == "tool_call":
                status_icon = "✅" if trace.status == "success" else "❌"
                output_str = str(trace.tool_output)
                # Truncate very long outputs for context window
                if len(output_str) > 2000:
                    output_str = output_str[:2000] + "... (truncated)"
                
                lines.append(f"  [操作] {trace.tool_name}({json.dumps(trace.tool_input, ensure_ascii=False)})")
                lines.append(f"  [结果] {status_icon} {output_str}")

        return "\n".join(lines)

    async def _dispatch_tool_safe(self, tool_call: Dict[str, Any], tools: List[Any]) -> tuple:
        start_tool = time.time()
        try:
            name = tool_call["name"]; args = tool_call["args"]
            # Keep tool input backward-compatible:
            # - old tools/tests expect {"query": "..."} for execute_sql_query
            # - newer prompts may use {"sql": "..."}
            invoke_args = args
            if isinstance(args, dict) and name == "execute_sql_query":
                invoke_args = dict(args)
                # Trim strings to reduce silly mismatches
                if isinstance(invoke_args.get("sql"), str):
                    invoke_args["sql"] = invoke_args["sql"].strip()
                if isinstance(invoke_args.get("query"), str):
                    invoke_args["query"] = invoke_args["query"].strip()
                # If only sql is provided, also provide query for legacy tools
                if "query" not in invoke_args and "sql" in invoke_args:
                    invoke_args["query"] = invoke_args.get("sql")
                # If only query is provided, do not inject sql (keeps unit tests strict)

            if self.debug_options.get("dry_run") and name == "execute_sql_query":
                output = f"[DRY RUN MODE] SQL skipped: {invoke_args.get('sql') or invoke_args.get('query')}"
            else:
                target_inst = next((t for t in tools if t.name == name), None)
                if not target_inst:
                    output = f"[TOOL_ERROR] Unknown tool: {name}"
                else:
                    from app.services.config_service import ConfigService

                    timeout_str = await ConfigService.get("agent_tool_timeout_seconds")
                    try:
                        timeout = float(timeout_str) if timeout_str else 60.0
                    except (TypeError, ValueError):
                        timeout = 60.0
                    output = await asyncio.wait_for(target_inst.ainvoke(invoke_args), timeout=timeout)
        except asyncio.TimeoutError:
            output = f"[TOOL_ERROR] Tool {tool_call.get('name')} timed out during execution."
        except Exception as e:
            output = f"[TOOL_ERROR] Execution failed: {str(e)}"
        return tool_call, output, (time.time() - start_tool) * 1000

    def _analyze_result(self, output: Any):
        """
        Heuristic tool status analyzer.

        NOTE: Avoid treating arbitrary JSON fields containing 'error' as a failure.
        Prefer explicit tool-error markers and common failure phrases.
        """
        if isinstance(output, (list, dict)):
            return "success", None, False

        out_str = str(output or "")
        low = out_str.lower().strip()
        critical_signals = (
            "critical",
            "authentication failed",
            "unauthorized",
            "forbidden",
            "connection refused",
            "timed out",
            "timeout",
        )

        # Explicit executor-side tool error marker
        if low.startswith("[tool_error]"):
            return "error", out_str[:200], any(s in low for s in critical_signals)

        # Application/business error prefixes returned by sql_query_execution_service / data_api
        # (e.g. "Error: Dataset 'xxx' not found", "[Validation Failed]", "[Permission Denied]")
        if re.match(
            r"^\s*("
            r"error:|错误[：:]|"
            r"\[(validation failed|permission denied|security error|tool_error|tool error|"
            r"rag error|system config error|rag connection error)\]"
            r")",
            low,
            flags=re.IGNORECASE,
        ):
            return "error", out_str[:200], any(s in low for s in critical_signals)

        # Strong error signals
        strong_signals = (
            "traceback",
            "exception",
            "sql error",
            "syntax error",
            "sql validation failed",
            "validation failed",
            "permission denied",
            "access denied",
            "unauthorized",
            "forbidden",
            "timeout",
            "timed out",
            "connection refused",
            "connection error",
            "failed",
            "not found",
            "parse failed",
            "are not allowed",
            "are prohibited",
            "empty sql query",
            "only read-only queries",
            "dangerous keyword",
            "external api error",
            "error from api",
            "本地执行 sql 失败",
            "执行超时",
        )
        if any(s in low for s in strong_signals):
            return "error", out_str[:200], any(s in low for s in critical_signals)

        # Best-effort: if it looks like JSON and can be parsed, treat as success.
        # (Many successful SQL APIs return JSON strings.)
        s = out_str.strip()
        if s[:1] in "[{":
            try:
                json.loads(s)
                return "success", None, False
            except Exception:
                pass

        return "success", None, False

    def _get_self_healing_feedback(self, tool_name: str, error_output: Any) -> str:
        err_str = str(error_output)
        if tool_name == "execute_sql_query":
            if "Unknown column" in err_str or "no such column" in err_str:
                return f"[规划修正] 字段名错误。工具 {tool_name} 报错: {err_str}。请先用 get_dataset_schema 确认表结构再重新生成 SQL。"
        return f"工具 {tool_name} 执行出错: {err_str}"

    def _parse_xml_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        return parse_xml_tool_calls(content)

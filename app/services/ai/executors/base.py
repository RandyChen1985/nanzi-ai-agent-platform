from abc import ABC, abstractmethod
import logging
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.temperature import resolve_config_temperature

logger = logging.getLogger(__name__)

class BaseExecutor(ABC):
    """
    Base class for all Agent Executors.
    Defines the contract for streaming execution.
    """

    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        execution_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Dict[str, Any] = None,
    ):
        self.config = config
        self.trace_id = trace_id
        self.trace_buffer = trace_buffer
        self.execution_options = execution_options or {}
        self.permission_options = permission_options or {}
        self.user_info = user_info
        self.conversation_id = conversation_id
        self.step_counter = 0
        self._last_tool_resolution = None

    def _tool_resolution_log_events(self) -> list[Dict[str, Any]]:
        """Return safe UI events for the latest runtime-tool resolution."""
        if self._last_tool_resolution is None:
            return []
        from app.services.ai.tool_capability import build_tool_resolution_log_events

        return build_tool_resolution_log_events(self._last_tool_resolution)

    def _grounding_enabled(self) -> bool:
        """Return whether this request explicitly enabled grounding audits."""
        return self.execution_options.get("grounding_enabled") is True

    async def _using_cache_layout(self) -> bool:
        """是否启用 prompt-cache 稳定前缀布局（enabled 灰度桶）。

        legacy/observe 一律返回 False，保持传统前后置行为；只有进入
        enabled 灰度桶时，runner 才把动态内容放到稳定段之后。
        任何配置读取异常一律失败回退到传统布局，绝不误开新布局。
        """
        try:
            from app.services.ai.prompt_assembler import (
                resolve_prompt_layout_config,
                should_use_prompt_cache_layout,
            )

            cfg = await resolve_prompt_layout_config()
            return should_use_prompt_cache_layout(
                cfg.mode,
                cfg.rollout_percent,
                self.conversation_id,
            )
        except Exception:
            return False

    @abstractmethod
    async def execute(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Main execution loop. Must yield dicts compatible with SSE.
        """
        pass

    def _user_identity_from_info(self) -> Dict[str, Any]:
        user_dims: Dict[str, Any] = {}
        u_id_val = None
        is_admin_val = False
        if not self.user_info:
            return {
                "u_id_val": None,
                "is_admin_val": False,
                "user_dims": user_dims,
            }

        raw_uid = self.user_info.get("user_id", self.user_info.get("id"))
        if raw_uid:
            u_id_val = int(raw_uid)
        is_admin_val = self.user_info.get("role") == "admin"
        user_dims = {
            "id": u_id_val,
            "user_name": self.user_info.get("user_name"),
            "real_name": self.user_info.get("real_name"),
            "role": self.user_info.get("role"),
            "dept_code": self.user_info.get("dept_code"),
            "org_path": self.user_info.get("org_path"),
        }
        extra_data = self.user_info.get("extra_data")
        if extra_data:
            try:
                import json
                if isinstance(extra_data, str):
                    extra_dict = json.loads(extra_data)
                else:
                    extra_dict = extra_data
                if isinstance(extra_dict, dict):
                    for k, v in extra_dict.items():
                        if k not in user_dims:
                            user_dims[k] = v
            except Exception:
                pass
        user_dims["extra_data"] = extra_data
        return {
            "u_id_val": u_id_val,
            "is_admin_val": is_admin_val,
            "user_dims": user_dims,
        }

    def _apply_user_identity_to_context(self, ctx: Any, identity: Dict[str, Any]) -> None:
        ctx.trace_id = self.trace_id
        u_id_val = identity.get("u_id_val")
        # api_key 是「身份缺失时反查 user_id」的唯一通道，必须在提前返回之前写入，
        # 否则一个没有 user_id 的上下文连兜底查询都做不了。
        api_key_val = self.user_info.get("api_key") if self.user_info else None
        if api_key_val:
            ctx.api_key = api_key_val
        if u_id_val is None:
            # 本次调用方给了 user_info，但里面没有可信 user_id：不能静默沿用 ctx 上
            # 残留的旧身份（那会以别人的身份执行工具，缓存作用域与行级权限一起串），
            # 显式清空并留痕，交由下游 fail-closed 拦截。
            if self.user_info:
                ctx.user_id = None
                ctx.is_admin = False
                ctx.user_dimensions = {}
            logger.warning(
                "[Agent] 本次执行未取得可信 user_id，已清空上下文残留身份 "
                "(trace_id=%s, conversation_id=%s, has_user_info=%s, has_api_key=%s)",
                self.trace_id,
                self.conversation_id,
                bool(self.user_info),
                bool(api_key_val),
            )
            return
        ctx.user_id = u_id_val
        ctx.is_admin = bool(identity.get("is_admin_val"))
        ctx.user_dimensions = dict(identity.get("user_dims") or {})
        if self.conversation_id:
            ctx.conversation_id = self.conversation_id
        browser_session_id = self.execution_options.get("browser_session_id")
        if browser_session_id:
            ctx.browser_session_id = str(browser_session_id)
        ctx.permission_options = dict(self.permission_options or {})
        if self.trace_buffer is not None:
            ctx.trace_buffer = self.trace_buffer

    def _ensure_agent_context(self) -> Any:
        from app.core.context import get_current_agent_context, set_agent_context, AgentContext
        ctx = get_current_agent_context()
        identity = self._user_identity_from_info()
        if ctx is not None:
            self._apply_user_identity_to_context(ctx, identity)
            ctx.skills_custom = bool(getattr(self.config, "skills_custom", False))
            ctx.skills = list(getattr(self.config, "skills", None) or [])
            return ctx

        from app.services.ai.knowledge_utils import merge_dataset_id_sources
        engine_config = self.config.engine_config or {}
        agent_dataset_ids = merge_dataset_id_sources(engine_config.get("dataset_ids"))
        u_id_val = identity.get("u_id_val")
        is_admin_val = identity.get("is_admin_val", False)
        user_dims = identity.get("user_dims") or {}

        ctx = AgentContext(
            agent_id=self.config.agent_id,
            agent_name=self.config.agent_name,
            dataset_ids=agent_dataset_ids,
            knowledge_dataset_ids=[],
            agent_dataset_ids=agent_dataset_ids,
            require_explicit_dataset=False,
            engine_type=self.config.engine_type or "LOCAL",
            engine_config=engine_config,
            user_id=u_id_val,
            conversation_id=self.conversation_id,
            browser_session_id=self.execution_options.get("browser_session_id"),
            is_admin=is_admin_val,
            api_key=self.user_info.get("api_key") if self.user_info else None,
            user_dimensions=user_dims,
            trace_id=self.trace_id,
            trace_buffer=self.trace_buffer or [],
            delegation_depth=0,
            permission_options=dict(self.permission_options or {}),
            skills_custom=bool(getattr(self.config, "skills_custom", False)),
            skills=list(getattr(self.config, "skills", None) or []),
        )
        set_agent_context(ctx)
        return ctx

    def _increment_step(self) -> int:
        """分配下一个 trace 步骤号。

        委派的子代理与主代理共用同一个 trace_buffer，但各自是独立的执行器实例。
        若只按实例自增，子代理会从 1 重新编号，与主代理步骤撞号：同一 trace_id
        下出现重复 step_number，审计页按 step_number 排序就会乱序。这里用共享
        buffer 的当前长度作为下限，保证同一 trace 内编号单调。

        测试替身可能只构造了部分属性，因此一律用 getattr 探测，缺省按原逻辑自增。
        """
        trace_buffer = getattr(self, "trace_buffer", None)
        if trace_buffer is not None:
            try:
                self.step_counter = max(self.step_counter, len(trace_buffer))
            except TypeError:
                pass
        self.step_counter += 1
        return self.step_counter

    def record_llm_token_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        event_type: str = "model_call",
        model: str | None = None,
        tool_name: str | None = None,
        execution_time_ms: float | None = None,
    ) -> None:
        """Record one LLM API call's token usage into trace_buffer for audit/history."""
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type=event_type,
                agent_name=self.config.agent_name,
                model=model,
                temperature=resolve_config_temperature(self.config),
                tool_name=tool_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                execution_time_ms=execution_time_ms,
                timestamp=datetime.now(),
            )
        )

    def _record_agent_scope_model_call(
        self,
        event: Any,
        *,
        state: Dict[str, Any],
        native_model: Any,
    ) -> None:
        reply_id = str(getattr(event, "reply_id", "") or "")
        started_at = state.get("model_call_started_at", {}).get(reply_id, time.time())
        self.record_llm_token_usage(
            prompt_tokens=int(getattr(event, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(event, "output_tokens", 0) or 0),
            event_type="model_call",
            model=str(
                getattr(event, "model_name", "")
                or getattr(native_model, "model", self.config.model_name)
                or ""
            ),
            tool_name="agentscope_model_call",
            execution_time_ms=(time.time() - started_at) * 1000,
        )

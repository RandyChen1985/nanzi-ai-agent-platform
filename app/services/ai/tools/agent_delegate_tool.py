import logging
import asyncio
import re
import uuid
import inspect
from typing import Optional, Dict, Any, List
from app.services.ai.tools.tool_compat import tool
from app.core.context import get_current_agent_context, AgentContext, set_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.agent_manager import AgentManagerService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


def clean_sub_agent_output(text: str) -> str:
    """滤除 <sql_plan>...</sql_plan> 标签以防上下文污染，支持多行匹配。"""
    if not text:
        return ""
    cleaned = re.sub(r"<sql_plan>.*?</sql_plan>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _extract_delegation_text(chunk: Dict[str, Any]) -> str:
    """从子 Executor chunk 中提取可交付给主助手的文本（不含 log 进度）。"""
    content = chunk.get("content")
    if content:
        return str(content)
    for key in ("text", "message"):
        value = chunk.get(key)
        if value:
            return str(value)
    return ""


@tool
async def sub_agent_call(agent_name: str, query: str) -> str:
    """委派其他专有子智能体执行特定任务（如查数、查手册）。禁止未调用本工具就编造数据或流程。

    Args:
        agent_name: 目标子智能体的英文名称标识（如 data-agent，knowledge-base）
        query: 委派的具体任务指令或查询词
    """
    main_ctx = get_current_agent_context()
    if not main_ctx:
        return "错误：无法获取当前执行上下文，委派失败。"

    # 1. 嵌套深度检查 (Depth Check)
    if main_ctx.delegation_depth >= 1:
        return f"错误：检测到多级智能体嵌套委派调用（当前深度 {main_ctx.delegation_depth}），拒绝执行以防死循环。"

    # 2. 校验目标智能体是否存在并加载配置
    target_config = None
    async with AsyncSessionLocal() as session:
        from app.models.agent import AIAgent
        from sqlalchemy import select
        # 强制只查询启用的系统内置智能体 (is_system = True)
        stmt = select(AIAgent).where(AIAgent.is_enabled == True, AIAgent.is_system == True)
        all_active_system = (await session.execute(stmt)).scalars().all()
        
        matched_agent = None
        clean_name = lambda s: s.lower().replace('-', '_').strip() if s else ""
        target_clean = clean_name(agent_name)
        
        for a in all_active_system:
            # 规则1：英文标识忽略大小写和中划线/下划线比对
            if a.name and clean_name(a.name) == target_clean:
                matched_agent = a
                break
            # 规则2：中文/英文展示名比对
            if a.display_name and a.display_name.strip() == agent_name.strip():
                matched_agent = a
                break
            if a.display_name and a.display_name.lower().strip() == agent_name.lower().strip():
                matched_agent = a
                break
        
        if matched_agent:
            # 使用匹配到的正确的英文标识名重新加载配置
            target_config = await AgentManagerService.get_active_agent_config(session, agent_name=matched_agent.name)
            
            # [CR Fix] 阻止自委派 (matched_agent.id == main_ctx.agent_id)
            if target_config and str(target_config.agent_id) == str(main_ctx.agent_id):
                return "错误：主智能体无法委派调用自身。"
        
        if not target_config:
            # 无论如何都找不到，列出当前系统已启用的内置系统智能体列表，供模型自我纠错
            candidates = [
                f"`{a.name}` ({a.display_name or a.name})" 
                for a in all_active_system 
                if str(a.id) != str(main_ctx.agent_id)
            ]
            candidates_str = ", ".join(candidates)
            return (
                f"错误：未找到名为 '{agent_name}' 的启用系统智能体。请重新反思问题，并只能从以下当前已启用的系统内置候选智能体列表中选择正确的英文标识 (agent_name) 进行 `sub_agent_call` 调用：{candidates_str}"
            )

        # 3. 权限校验
        if main_ctx.user_id and not main_ctx.is_admin:
            perm_service = PermissionService(session)
            has_perm = await perm_service.check_permission(int(main_ctx.user_id), "agent", str(target_config.agent_id))
            if not has_perm:
                return f"错误：当前用户无权访问子智能体 '{target_config.agent_display_name or agent_name}'。"

    # 4. 构造子代理独立上下文 (Sandbox Isolation)
    sub_history = [{"role": "user", "content": query}]

    # [CR Fix] 继承主上下文已生效的知识库 ID，并与子智能体引擎自身配置的 IDs 合并
    effective_dataset_ids = list(set(main_ctx.dataset_ids or []))
    if target_config.engine_config and target_config.engine_config.get("dataset_ids"):
        from app.services.ai.knowledge_utils import merge_dataset_id_sources
        effective_dataset_ids = merge_dataset_id_sources(
            effective_dataset_ids,
            target_config.engine_config.get("dataset_ids")
        )

    sub_engine_config = dict(target_config.engine_config or {})
    sub_engine_config["dataset_ids"] = effective_dataset_ids

    # 创建一个专属子上下文，隔离历史，但保留用户信息和 API Key 供子工具鉴权
    sub_ctx = AgentContext(
        agent_id=str(target_config.agent_id),
        agent_name=target_config.agent_name,
        dataset_ids=effective_dataset_ids,
        knowledge_dataset_ids=list(main_ctx.knowledge_dataset_ids or []),
        require_explicit_dataset=False,
        engine_type=target_config.engine_type or "LOCAL",
        engine_config=sub_engine_config,
        user_id=main_ctx.user_id,
        conversation_id=main_ctx.conversation_id,
        is_admin=main_ctx.is_admin,
        api_key=main_ctx.api_key,
        user_dimensions=main_ctx.user_dimensions,
        delegation_depth=main_ctx.delegation_depth + 1,  # 深度加 1
        trace_buffer=main_ctx.trace_buffer,  # 共用 trace 收集物理步骤
        event_queue=main_ctx.event_queue,  # 传递 event_queue 用于流式穿透
    )

    # [CR Fix] 从 main_ctx 还原 user_info 并传给 dispatch，避免 session lock 和维度缺失
    user_info = {
        "user_id": main_ctx.user_id,
        "role": "admin" if main_ctx.is_admin else "user",
        "api_key": main_ctx.api_key,
        "user_name": main_ctx.user_dimensions.get("user_name") if main_ctx.user_dimensions else None,
        "real_name": main_ctx.user_dimensions.get("real_name") if main_ctx.user_dimensions else None,
        "dept_code": main_ctx.user_dimensions.get("dept_code") if main_ctx.user_dimensions else None,
        "org_path": main_ctx.user_dimensions.get("org_path") if main_ctx.user_dimensions else None,
        "extra_data": main_ctx.user_dimensions.get("extra_data") if main_ctx.user_dimensions else None,
    } if main_ctx else None

    # 5. 实例化子执行器 (Dispatch Sub Executor)
    from app.services.ai.dispatcher import AgentDispatcher
    sub_executor = await AgentDispatcher.dispatch(
        target_config,
        query,
        sub_history,
        trace_id=f"sub_{uuid.uuid4().hex[:8]}",
        trace_buffer=main_ctx.trace_buffer,
        debug_options=None,
        permission_options=None,
        user_info=user_info,
        conversation_id=main_ctx.conversation_id,
    )

    # 临时切换到子 Context 运行
    original_ctx = get_current_agent_context()
    set_agent_context(sub_ctx)

    full_output = ""
    sub_display_name = target_config.agent_display_name or target_config.agent_name or agent_name
    sub_stream = None

    try:
        # 6. 超时控制 (60 Seconds Timeout)
        sub_stream = sub_executor.execute(sub_history)

        async def consume_stream():
            nonlocal full_output
            async for chunk in sub_stream:
                text = _extract_delegation_text(chunk)
                if text:
                    full_output += text
                # 7. 日志实时穿透 (SSE log forwarding)
                elif chunk.get("type") == "log" and main_ctx.event_queue:
                    title = chunk.get("title", "")
                    chunk["title"] = f"[{sub_display_name}] {title}"
                    await main_ctx.event_queue.put(chunk)

        await asyncio.wait_for(consume_stream(), timeout=60.0)

    except asyncio.TimeoutError:
        logger.warning(f"[Delegation] Sub-agent '{agent_name}' timed out after 60 seconds.")
        if main_ctx.event_queue:
            await main_ctx.event_queue.put({
                "type": "log",
                "title": f"[{sub_display_name}] 调用超时",
                "details": "子智能体未能在 60 秒内返回数据，强制中断并释放资源。",
                "status": "error"
            })
        return f"错误：调用子智能体 '{sub_display_name}' 响应超时（已达 60 秒限制）。"
    except Exception as e:
        logger.error(f"[Delegation] Error executing sub-agent '{agent_name}': {e}", exc_info=True)
        return f"错误：调用子智能体 '{sub_display_name}' 时发生异常：{str(e)}"
    finally:
        if sub_stream and inspect.isasyncgen(sub_stream):
            try:
                await sub_stream.aclose()
            except Exception as close_err:
                logger.warning(f"Failed to close sub-agent generator stream: {close_err}")
        set_agent_context(original_ctx)

    # 8. 过滤去噪与截断 (Sanitization & Truncation)
    cleaned_output = clean_sub_agent_output(full_output)
    
    max_chars = 2000
    if len(cleaned_output) > max_chars:
        cleaned_output = cleaned_output[:max_chars] + "\n\n...[因数据量过大，子代理回复已被系统自动截断]"

    return cleaned_output

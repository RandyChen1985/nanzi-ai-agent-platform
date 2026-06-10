import re
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import text
from app.core.orm import AsyncSessionLocal
from app.services.config_service import ConfigService
from app.services.ai.agent_manager import AgentManagerService
from app.schemas.prompt import PromptMetadata, PromptSource, PromptVersionSummary, PromptDetail, PromptTestResponse
from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

logger = logging.getLogger(__name__)

# 定义系统级 Prompt 的映射注册表
# 注意：路由相关的两层系统提示词均已内置到代码，不再通过数据库配置管理
# （避免运营在配置页误改导致路由/意图识别失准）：
#   - router_system_prompt   -> RouterService.DEFAULT_SYSTEM_PROMPT（第一层智能体路由）
#   - intent_recognition_prompt -> IntentService.DEFAULT_SYSTEM_PROMPT（第二层意图识别）
SYSTEM_PROMPT_REGISTRY = {}

class PromptService:
    @staticmethod
    def extract_variables(text: str) -> List[str]:
        """从提示词中提取 {variable} 格式的占位符"""
        return list(set(re.findall(r'\{([^{}]+)\}', text)))

    @staticmethod
    async def get_all_prompts() -> List[PromptMetadata]:
        prompts = []
        
        # 1. 获取系统级 Prompts
        for key, info in SYSTEM_PROMPT_REGISTRY.items():
            val = await ConfigService.get(key)
            if val is not None:
                # 获取历史记录以计算版本
                history = await ConfigService.get_config_history(key)
                
                # 构建版本列表
                versions = []
                total_versions = len(history) + 1
                
                # 最新版本 (当前值)
                latest_update = history[0]["created_at"] if history else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                versions.append(PromptVersionSummary(
                    version_number=total_versions,
                    status="PUBLISHED",
                    comment="当前活跃版本",
                    updated_at=latest_update
                ))
                
                # 历史版本
                for i, h in enumerate(history):
                    v_num = total_versions - (i + 1)
                    if v_num < 1: break
                    versions.append(PromptVersionSummary(
                        version_number=v_num,
                        status="ARCHIVED",
                        comment=h["description"] or "历史快照",
                        updated_at=h["created_at"]
                    ))

                prompts.append(PromptMetadata(
                    id=key,
                    name=info["name"],
                    source=PromptSource.SYSTEM_CONFIG,
                    category=info["category"],
                    description=info["description"],
                    target_key=key,
                    versions=versions
                ))

        # 2. 获取智能体级 Prompts
        async with AsyncSessionLocal() as session:
            # 获取所有活跃智能体
            result = await session.execute(text("SELECT id, name, display_name, description, created_by, is_system FROM ai_agents"))
            agents = result.fetchall()
            
            for agent in agents:
                agent_id, name, display_name, desc, created_by, is_system = agent
                
                # 获取该智能体的版本列表
                v_res = await session.execute(
                    text("SELECT version_number, status, comment, created_at FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC"),
                    {"agent_id": agent_id}
                )
                v_rows = v_res.fetchall()
                versions = [
                    PromptVersionSummary(
                        version_number=r[0],
                        status=r[1],
                        comment=r[2],
                        updated_at=r[3].strftime("%Y-%m-%d %H:%M:%S") if r[3] else ""
                    ) for r in v_rows
                ]
                
                prompts.append(PromptMetadata(
                    id=f"agent_{agent_id}",
                    name=name,
                    display_name=display_name,
                    source=PromptSource.AGENT,
                    category="Agent",
                    description=desc,
                    agent_id=agent_id,
                    versions=versions,
                    created_by=created_by,
                    is_system=is_system or False
                ))
                    
        return prompts

    @staticmethod
    async def get_prompt_detail(source: PromptSource, target_id: str, version: Optional[int] = None) -> PromptDetail:
        content = ""
        v_num = version
        note = ""

        if source == PromptSource.SYSTEM_CONFIG:
            history = await ConfigService.get_config_history(target_id)
            total_versions = len(history) + 1
            
            if version:
                if version == total_versions:
                    content = await ConfigService.get(target_id, "")
                    # Try to get latest note from most recent history if available
                    if history:
                         note = history[0].get("description", "")
                else:
                    # 获取对应版本的旧值
                    idx = total_versions - version - 1
                    if 0 <= idx < len(history):
                        content = history[idx]["old_value"]
                        note = history[idx].get("description", "")
                    else:
                        content = ""
            else:
                content = await ConfigService.get(target_id, "")
                v_num = total_versions
                if history:
                     note = history[0].get("description", "")
                     
        elif source == PromptSource.AGENT:
            agent_id = target_id.replace("agent_", "")
            async with AsyncSessionLocal() as session:
                if version:
                    result = await session.execute(
                        text("SELECT system_prompt, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND version_number = :version"),
                        {"agent_id": agent_id, "version": version}
                    )
                else:
                    # Fetch Published (Latest Active)
                    result = await session.execute(
                        text("SELECT system_prompt, version_number, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'PUBLISHED' LIMIT 1"),
                        {"agent_id": agent_id}
                    )
                    # Fallback: If no published, get latest Draft
                    if not result.rowcount:
                         result = await session.execute(
                            text("SELECT system_prompt, version_number, comment FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC LIMIT 1"),
                            {"agent_id": agent_id}
                        )
                        
                row = result.fetchone()
                if row:
                    content = row[0]
                    if version:
                        note = row[1]
                    else:
                        v_num = row[1]
                        note = row[2]
        
        return PromptDetail(
            id=target_id,
            source=source,
            content=content,
            version_number=v_num,
            version_note=note,
            variables=PromptService.extract_variables(content)
        )

    @staticmethod
    async def test_prompt(content: str, variables: Dict[str, Any], user_input: Optional[str] = None, model_name: Optional[str] = None) -> PromptTestResponse:
        start_time = time.time()
        
        # 1. 插值 (Interpolation)
        # Using .replace() instead of .format() to avoid KeyError when prompt contains JSON braces
        interpolated = content
        for k, v in variables.items():
            interpolated = interpolated.replace(f"{{{k}}}", str(v))
        
        # 2. 调用 LLM
        try:
            llm = await get_llm_async(model=model_name)
            if not llm:
                raise Exception("LLM not configured properly")
            chat_client = chat_client_from_handle(llm)

            # Use user_input as user message if provided
            if user_input:
                messages = [
                    RuntimeMessage(
                        role="system",
                        content=[RuntimeContentBlock(type="text", text=interpolated)],
                    ),
                    RuntimeMessage(
                        role="user",
                        content=[RuntimeContentBlock(type="text", text=user_input)],
                    ),
                ]
            else:
                messages = [
                    RuntimeMessage(
                        role="user",
                        content=[RuntimeContentBlock(type="text", text=interpolated)],
                    )
                ]
            raw_output = await chat_client.generate_text(messages)
            
            latency = (time.time() - start_time) * 1000
            
            return PromptTestResponse(
                raw_output=raw_output,
                interpolated_prompt=interpolated,
                latency_ms=latency
            )
        except Exception as e:
            logger.error(f"LLM invocation failed: {str(e)}", exc_info=True)
            # 返回一个模拟的响应，而不是抛出异常
            return PromptTestResponse(
                raw_output=f"[LLM Error] {str(e)}\n\nInterpolated Prompt:\n{interpolated}",
                interpolated_prompt=interpolated,
                latency_ms=(time.time() - start_time) * 1000
            )

    @staticmethod
    async def save_prompt(source: PromptSource, target_id: str, content: str, version_note: str = "", user_name: str = "system") -> bool:
        if source == PromptSource.SYSTEM_CONFIG:
            return await ConfigService.set_config(
                target_id, 
                content, 
                description=None, 
                changed_by=user_name,
                change_reason=version_note
            )
        elif source == PromptSource.AGENT:
            agent_id = target_id.replace("agent_", "")
            # 创建新版本或更新草稿？
            # 策略：如果当前有 DRAFT，更新它；否则创建一个新的版本号
            async with AsyncSessionLocal() as session:
                try:
                    # 检查是否有 DRAFT
                    result = await session.execute(
                        text("SELECT id, version_number, system_prompt, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'DRAFT' LIMIT 1"),
                        {"agent_id": agent_id}
                    )
                    draft = result.fetchone()
                    
                    if draft:
                        # Check if content actually changed
                        if draft[2] == content and draft[3] == version_note:
                            return False # No change

                        # Update existing DRAFT
                        await session.execute(
                            text("UPDATE ai_agent_versions SET system_prompt = :content, comment = :note WHERE id = :id"),
                            {"content": content, "note": version_note, "id": draft[0]}
                        )
                        await session.commit()
                        return True
                    else:
                        # 获取最大版本号
                        res_max = await session.execute(
                            text("SELECT MAX(version_number) FROM ai_agent_versions WHERE agent_id = :agent_id"),
                            {"agent_id": agent_id}
                        )
                        max_v = res_max.fetchone()
                        next_v = (max_v[0] or 0) + 1
                        
                        # 获取已发布版本的模型配置作为模板
                        res_tmpl = await session.execute(
                            text("SELECT model_name, tools, synthesis_model_name, synthesis_temperature FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'PUBLISHED' LIMIT 1"),
                            {"agent_id": agent_id}
                        )
                        template = res_tmpl.fetchone()
                        model_name = template[0] if template else "gpt-3.5-turbo"
                        tools = template[1] if template else "[]"
                        s_model = template[2] if template else None
                        s_temp = template[3] if template else None
                        
                        # 插入新 DRAFT
                        import uuid
                        await session.execute(
                            text("INSERT INTO ai_agent_versions (id, agent_id, version_number, model_name, synthesis_model_name, synthesis_temperature, system_prompt, tools, status, comment) VALUES (:uuid, :agent_id, :next_v, :model_name, :s_model, :s_temp, :content, :tools, 'DRAFT', :note)"),
                            {
                                "uuid": str(uuid.uuid4()), 
                                "agent_id": agent_id, 
                                "next_v": next_v, 
                                "model_name": model_name, 
                                "s_model": s_model,
                                "s_temp": s_temp,
                                "content": content, 
                                "tools": tools, 
                                "note": version_note
                            }
                        )
                    await session.commit()
                    return True
                except Exception:
                    await session.rollback()
                    raise

    @staticmethod
    async def optimize_prompt(content: str) -> Dict[str, Any]:
        """利用 LLM 生成 5 个优化后的提示词版本"""
        system_prompt = """你是一个专业的提示词工程专家 (Prompt Engineer)。
你的任务是根据用户提供的原始提示词，生成 5 个不同侧重点的优化版本。

优化维度建议（但不限于）：
1. **结构化增强**：使用 Markdown 标题、列表、XML 标签等增强语义结构。
2. **少样本学习 (Few-Shot)**：增加具有代表性的示例以引导输出。
3. **角色设定 (Roleplay)**：强化其专家身份和响应风格。
4. **思维链 (CoT)**：引导提示词按逻辑步骤思考。
5. **清晰约束**：明确负面约束和输出格式。

输出格式要求：
请仅返回一个 JSON 对象，结构如下：
{
  "suggestions": [
    {
      "title": "版本标题 (如：结构化增强版)",
      "content": "完全优化后的提示词内容",
      "reason": "推荐理由：为什么这么修改，解决了什么问题。"
    },
    ... (共5个)
  ]
}
不要包含任何额外的 Markdown 标记或解释。"""

        try:
            llm = await get_llm_async()
            if not llm:
                raise Exception("LLM not configured")

            import json

            chat_client = chat_client_from_handle(llm)
            messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=system_prompt)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=f"原始提示词如下：\n\n{content}")],
                ),
            ]
            raw_text = await chat_client.generate_text(messages)
            
            # 清理 Markdown 代码块包裹
            clean_text = raw_text.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```json"):
                    clean_text = "\n".join(lines[1:-1])
                elif lines[0].startswith("```"):
                    clean_text = "\n".join(lines[1:-1])
            
            data = json.loads(clean_text)
            return data
        except Exception as e:
            logger.error(f"Prompt optimization failed: {str(e)}", exc_info=True)
            raise e

    @staticmethod
    async def get_agent_prompt_history(agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体 Prompt 的变更历史，格式化为类似系统审计日志的结构"""
        target_id = agent_id.replace("agent_", "")
        
        async with AsyncSessionLocal() as session:
            # Fetch all versions ordered by number DESC
            result = await session.execute(
                text("SELECT id, version_number, system_prompt, comment, status, created_at FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC"),
                {"agent_id": target_id}
            )
            versions = result.fetchall()
            
            history = []
            # Calculate diffs by looking at the next item (which is the previous version)
            for i, v in enumerate(versions):
                current_prompt = v[2]
                prev_prompt = ""
                
                # Try to find previous version
                if i + 1 < len(versions):
                    prev_prompt = versions[i+1][2]
                
                history.append({
                    "id": v[0],
                    "change_type": "PUBLISH" if v[4] in ["PUBLISHED", "ARCHIVED"] else "DRAFT",
                    "changed_by": "System", # Agent versions don't track user yet
                    "created_at": v[5].strftime("%Y-%m-%d %H:%M:%S") if v[5] else "",
                    "description": v[3], # The version note/comment
                    "new_value": current_prompt,
                    "old_value": prev_prompt
                })
                
            return history

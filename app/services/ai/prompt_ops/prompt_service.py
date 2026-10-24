import json
import re
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, text
from app.core.orm import AsyncSessionLocal
from app.services.config_service import ConfigService
from app.services.ai.agent_manager import AgentManagerService
from app.schemas.prompt import PromptMetadata, PromptSource, PromptVersionSummary, PromptDetail, PromptTestResponse
from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

logger = logging.getLogger(__name__)

_OPTIMIZE_XML_ITEM_RE = re.compile(
    r"<item>\s*"
    r"<title>(?P<title>.*?)</title>\s*"
    r"<reason>(?P<reason>.*?)</reason>\s*"
    r"<content>\s*(?:<!\[CDATA\[(?P<cdata>.*?)\]\]>|(?P<plain>.*?))\s*</content>\s*"
    r"</item>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_markdown_fence(text: str) -> str:
    clean = str(text or "").strip()
    if not clean.startswith("```"):
        return clean
    lines = clean.splitlines()
    if not lines:
        return clean
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _relax_json_text(text: str) -> str:
    """Common LLM JSON cleanups that do not alter string semantics."""
    cleaned = str(text or "").strip().lstrip("\ufeff")
    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _escape_control_chars_in_json_strings(text: str) -> str:
    """Escape raw newlines/tabs inside JSON string literals."""
    out: List[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def _extract_json_object(text: str) -> Optional[str]:
    cleaned = _strip_markdown_fence(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    return cleaned[start : end + 1]


def _normalize_optimize_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("优化结果不是 JSON 对象")
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("优化结果缺少 suggestions")
    suggestions: List[Dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not content:
            continue
        suggestions.append(
            {
                "title": title or f"优化方案 {len(suggestions) + 1}",
                "content": content,
                "reason": reason or "未提供推荐理由",
            }
        )
    if not suggestions:
        raise ValueError("优化结果没有可用方案")
    return {"suggestions": suggestions}


def parse_optimize_prompt_response(raw_text: str) -> Dict[str, Any]:
    """
    Parse LLM optimize output.

    Prefer XML+CDATA (avoids quote escaping issues in long prompts).
    Fall back to JSON with light repairs for trailing commas / raw newlines.
    """
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("模型返回为空")

    xml_items = list(_OPTIMIZE_XML_ITEM_RE.finditer(text))
    if xml_items:
        suggestions = []
        for match in xml_items:
            content = (match.group("cdata") if match.group("cdata") is not None else match.group("plain") or "")
            content = content.strip()
            if not content:
                continue
            suggestions.append(
                {
                    "title": (match.group("title") or "").strip() or f"优化方案 {len(suggestions) + 1}",
                    "content": content,
                    "reason": (match.group("reason") or "").strip() or "未提供推荐理由",
                }
            )
        if suggestions:
            return {"suggestions": suggestions}

    blob = _extract_json_object(text)
    if not blob:
        raise ValueError("无法从模型输出中提取结构化结果")

    candidates = [
        blob,
        _relax_json_text(blob),
        _escape_control_chars_in_json_strings(_relax_json_text(blob)),
    ]
    last_err: Optional[Exception] = None
    for candidate in candidates:
        try:
            return _normalize_optimize_payload(json.loads(candidate))
        except Exception as exc:  # noqa: BLE001 - try next repair strategy
            last_err = exc
            continue
    raise ValueError(f"模型返回的 JSON 无法解析: {last_err}")

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
    async def _visible_agent_ids(session, user: Any) -> Optional[set[str]]:
        """当前用户在提示词工坊里能看到哪些智能体。

        ``None`` 表示不限制（管理员）。口径与智能体中心一致：
        自己创建的 + 系统级且被授权的。没这条限制时，任何登录账号都能读到
        全公司所有智能体的系统提示词，包括生产基线。
        """
        is_admin = bool(user and (
            (user.get("role") == "admin") if isinstance(user, dict)
            else getattr(user, "role", None) == "admin"
        ))
        if is_admin:
            return None

        username = user.get("user_name") if isinstance(user, dict) else getattr(user, "user_name", None)
        user_id = user.get("user_id") or user.get("id") if isinstance(user, dict) else getattr(user, "id", None)

        from app.models.agent import AIAgent
        from app.services.permission_service import PermissionService

        allowed_ids: set[str] = set()
        try:
            perms = await PermissionService(session).get_user_permissions(int(user_id))
            allowed_ids = set(perms.permissions.agents or [])
        except (TypeError, ValueError):
            allowed_ids = set()

        rows = await session.execute(
            select(AIAgent.id, AIAgent.created_by, AIAgent.is_system)
        )
        visible: set[str] = set()
        for agent_id, created_by, is_system in rows.all():
            if username and created_by == username:
                visible.add(str(agent_id))
            elif is_system and str(agent_id) in allowed_ids:
                visible.add(str(agent_id))
        return visible

    @staticmethod
    async def get_all_prompts(user: Any = None) -> List[PromptMetadata]:
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

            visible_ids = await PromptService._visible_agent_ids(session, user)
            if visible_ids is not None:
                agents = [row for row in agents if str(row[0]) in visible_ids]

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
    async def get_prompt_detail(
        source: PromptSource,
        target_id: str,
        version: Optional[int] = None,
        user: Any = None,
    ) -> PromptDetail:
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
                # 看得到才给看：否则任何人拿到 agent_id 就能读别人的提示词全文
                visible_ids = await PromptService._visible_agent_ids(session, user)
                if visible_ids is not None and agent_id not in visible_ids:
                    raise PermissionError("无权查看该智能体的提示词")

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
    async def save_prompt(
        source: PromptSource,
        target_id: str,
        content: str,
        version_note: str = "",
        user_name: str = "system",
        user: Any = None,
    ) -> Dict[str, Any]:
        """保存提示词，返回 ``{"changed": bool, "version_id": Optional[str]}``。

        带上 version_id 是为了让前端能发布**刚保存的那一版**：原先前端靠
        ``find(v => v.status === 'DRAFT')`` 取列表里第一个草稿，多草稿时会发错版本。
        """
        if source == PromptSource.SYSTEM_CONFIG:
            changed = await ConfigService.set_config(
                target_id, 
                content, 
                description=None, 
                changed_by=user_name,
                change_reason=version_note
            )
            return {"changed": bool(changed), "version_id": None}
        elif source == PromptSource.AGENT:
            agent_id = target_id.replace("agent_", "")
            # 必须走 AgentManagerService：它带所有权校验、DRAFT 校验，
            # 并且新建版本时会复制现有字段（temperature / 工具 / 技能……），
            # 而不是让它们落回数据库默认值。
            from app.core.orm import AsyncSessionLocal as _Session
            from app.services.ai.agent_manager import AgentManagerService

            async with _Session() as session:
                try:
                    version_id = await AgentManagerService.save_agent_prompt(
                        session,
                        agent_id,
                        content,
                        user=user,
                        note=version_note or None,
                    )
                    await session.commit()
                    return {"changed": version_id is not None, "version_id": version_id}
                except Exception:
                    await session.rollback()
                    raise

    @staticmethod
    async def optimize_prompt(content: str) -> Dict[str, Any]:
        """利用 LLM 生成 8 个优化后的提示词版本（含高级范式）。"""
        system_prompt = """你是一个专业的提示词工程专家 (Prompt Engineer)。
你的任务是根据用户提供的原始提示词，生成恰好 8 个不同侧重点的优化版本。
请尽量保留原始提示词中的业务意图与领域约束，在其基础上增强，而不是改写成无关内容。

必须覆盖以下 8 个维度（每个维度 1 个方案，title 需体现维度名称）：
1. **结构化增强**：使用 Markdown 标题、列表、XML 标签等增强语义结构。
2. **少样本学习 (Few-Shot)**：增加具有代表性的示例以引导输出。
3. **角色设定 (Roleplay)**：强化其专家身份和响应风格。
4. **思维链 (CoT)**：引导提示词按逻辑步骤思考。
5. **清晰约束**：明确负面约束和输出格式；必要时补充拒答/脱敏等安全边界。
6. **ReAct / 工具调用规范**：明确何时调用工具、如何解读工具结果、失败时如何降级与说明。
7. **反幻觉 / 取证门禁**：无证据不编造；要求引用/取证；不确定时如实说明。
8. **输出契约 (Schema)**：固定章节/字段/表格结构，便于 ChatBI、知识库或结构化交付。

输出格式要求（优先使用 XML，避免提示词正文中的引号/换行破坏结构）：
请仅返回如下 XML，不要 Markdown 代码块，不要额外解释：
<suggestions>
  <item>
    <title>版本标题 (如：结构化增强版)</title>
    <reason>推荐理由</reason>
    <content><![CDATA[
完全优化后的提示词内容（可含任意引号、换行、Markdown）
]]></content>
  </item>
  <!-- 必须恰好 8 个 item -->
</suggestions>

若无法输出 XML，也可退回 JSON（content 内双引号必须写成 \\"，换行写成 \\n）：
{"suggestions":[{"title":"...","content":"...","reason":"..."}]}"""

        return await PromptService._generate_optimize_suggestions(
            system_prompt, f"原始提示词如下：\n\n{content}"
        )

    @staticmethod
    async def optimize_task_instruction(content: str) -> Dict[str, Any]:
        """利用 LLM 为定时任务的执行指令生成 3 个优化版本。"""
        system_prompt = """你是一个 AI 任务指令优化专家。用户会给你一段「定时任务执行指令」，该指令将定期发送给 AI 智能体自动执行（智能体可查询数据库、检索知识库、生成报告等），执行结果会推送给用户。
你的任务是生成恰好 3 个不同侧重点的优化版本。

优化原则：
- 严格保留用户的业务意图，不要虚构用户没有提到的数据范围、指标或阈值。
- 如果关键信息缺失（如时间范围、统计对象、阈值），用占位符「____」标注并提示用户补充，不要自行编造。
- 指令面向自动化执行场景：应明确任务目标、数据/时间范围、输出格式，避免开放式闲聊措辞。

必须提供以下 3 个方案（每个方案 1 个，title 需体现方案名称）：
1. **结构化完整版**：按「任务目标 / 数据范围 / 输出要求 / 其他说明」骨架组织，信息完整、边界清晰。
2. **精简直达版**：保持简短（几行以内），一句话说清目标与范围，适合简单任务。
3. **输出契约版**：重点固定输出结构（如固定章节、指标表格、结论先行），让每次推送的结果格式稳定、便于阅读对比。

输出格式要求（优先使用 XML，避免正文中的引号/换行破坏结构）：
请仅返回如下 XML，不要 Markdown 代码块，不要额外解释：
<suggestions>
  <item>
    <title>方案标题 (如：结构化完整版)</title>
    <reason>推荐理由</reason>
    <content><![CDATA[
优化后的执行指令内容
]]></content>
  </item>
  <!-- 必须恰好 3 个 item -->
</suggestions>

若无法输出 XML，也可退回 JSON（content 内双引号必须写成 \\"，换行写成 \\n）：
{"suggestions":[{"title":"...","content":"...","reason":"..."}]}"""

        return await PromptService._generate_optimize_suggestions(
            system_prompt, f"原始执行指令如下：\n\n{content}"
        )

    @staticmethod
    async def _generate_optimize_suggestions(system_prompt: str, user_text: str) -> Dict[str, Any]:
        """调用 LLM 生成优化建议，解析失败时做一次修复重试。"""
        try:
            llm = await get_llm_async()
            if not llm:
                raise Exception("LLM not configured")

            chat_client = chat_client_from_handle(llm)
            messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=system_prompt)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=user_text)],
                ),
            ]
            raw_text = await chat_client.generate_text(messages)

            try:
                return parse_optimize_prompt_response(raw_text)
            except ValueError as first_err:
                # 一次纠错重试：专治 content 含未转义引号导致的 JSON 损坏
                logger.warning(
                    "Prompt optimize parse failed, retrying with repair prompt: %s",
                    first_err,
                )
                repair_messages = [
                    RuntimeMessage(
                        role="system",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=(
                                    "你是 JSON/XML 修复器。用户会给你一段损坏的提示词优化结果。"
                                    "请改写为合法 XML（推荐）或合法 JSON，保留全部方案的 title/reason/content。"
                                    "content 优先放在 CDATA 中。只输出修复后的结果，不要解释。"
                                ),
                            )
                        ],
                    ),
                    RuntimeMessage(
                        role="user",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=(
                                    f"解析错误：{first_err}\n\n"
                                    f"待修复内容：\n{raw_text}"
                                ),
                            )
                        ],
                    ),
                ]
                repaired = await chat_client.generate_text(repair_messages)
                return parse_optimize_prompt_response(repaired)
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

import json
import logging
import asyncio
import re
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, update, select
from datetime import datetime

from app.models.chatbi_example import ChatBIExample, ChatBIExampleUsage
from app.models.audit import AgentExecutionTrace, AgentExecutionHistory
from app.models.metadata import MetaDataset
from app.core.orm import AsyncSessionLocal
from app.services.config_service import ConfigService
from app.services.ai.ragflow_client import RagFlowClient

logger = logging.getLogger(__name__)

class ExampleService:
    @staticmethod
    async def create_from_feedback(db: AsyncSession, trace_id: str, feedback_type: str, user_id: str = None) -> Optional[ChatBIExample]:
        """
        根据反馈，从 trace 中提取成功的 SQL 并创建经验记录。
        """
        try:
            # 1. ORM 方式查找 Trace
            # 优化：不强制要求 tool_name 必须是 execute_sql_query，只要包含 sql 关键字或 input 中有 SQL 即可
            import re
            stmt = select(AgentExecutionTrace).where(
                AgentExecutionTrace.trace_id == trace_id
            ).order_by(AgentExecutionTrace.step_number.desc())
            
            result = await db.execute(stmt)
            traces = result.scalars().all()
            
            trace = None
            sql_text = None
            dataset_name = None

            for t in traces:
                # 寻找包含 sql 的工具调用，或者 event_type 包含关键信息
                t_name = (t.tool_name or "").lower()
                t_input_raw = t.tool_input
                
                if "sql" in t_name or (t.event_type == "tool" and "sql" in str(t_input_raw).lower()):
                    trace = t
                    try:
                        # 增加空值检查，防止 json.loads 抛出 Expecting value 错误
                        if not t_input_raw:
                            continue
                            
                        # 尝试解析 JSON
                        if isinstance(t_input_raw, dict):
                            input_data = t_input_raw
                        elif isinstance(t_input_raw, str) and t_input_raw.strip():
                            try:
                                input_data = json.loads(t_input_raw)
                            except json.JSONDecodeError:
                                # 如果不是标准 JSON，作为字符串处理
                                input_data = t_input_raw
                        else:
                            input_data = t_input_raw

                        if isinstance(input_data, dict):
                            sql_text = input_data.get("sql") or input_data.get("query")
                            dataset_name = input_data.get("dataset_name")
                        else:
                            # 如果是字符串或解析后的非字典，正则提取
                            sql_match = re.search(r"(SELECT\s+.*)", str(input_data), re.IGNORECASE | re.DOTALL)
                            if sql_match:
                                sql_text = sql_match.group(1).strip()
                    except Exception as parse_err:
                        logger.warning(f"[ExampleService] Skip one trace due to parse error: {parse_err}")
                        continue
                    
                    if sql_text:
                        break

            if not sql_text:
                logger.info(f"[ExampleService] No extractable SQL found in traces for trace_id: {trace_id}")
                return None
        except Exception as e:
            logger.error(f"[ExampleService] Error during SQL extraction from trace {trace_id}: {e}")
            return None

        try:
            # 2. ORM 方式查找 Execution History
            stmt_hist = select(AgentExecutionHistory).where(
                AgentExecutionHistory.trace_id == trace_id
            )
            result_hist = await db.execute(stmt_hist)
            history = result_hist.scalars().first()

            if not history:
                logger.error(f"[ExampleService] Execution history not found for trace_id: {trace_id}")
                return None

            # 3. 解析 Dataset ID
            dataset_id = 0
            if dataset_name:
                stmt_ds = select(MetaDataset.id).where(MetaDataset.name == dataset_name)
                res_ds = await db.execute(stmt_ds)
                dataset_id = res_ds.scalar() or 0

            # 4. 幂等创建或更新 (ORM 模式)
            stmt_ex = select(ChatBIExample).where(ChatBIExample.trace_id == trace_id)
            result_ex = await db.execute(stmt_ex)
            example = result_ex.scalars().first()

            if not example:
                example = ChatBIExample(
                    trace_id=trace_id,
                    agent_id=history.agent_id,
                    dataset_id=dataset_id,
                    user_query=history.query,
                    sql_text=sql_text,
                    ai_answer=history.summary,
                    feedback_type=feedback_type,
                    status="pending",
                    user_id=user_id or history.user_id
                )
                db.add(example)
            else:
                example.feedback_type = feedback_type
                example.sql_text = sql_text
                example.ai_answer = history.summary
                # 不论点赞还是点踩，统一重置为待审核状态，由管理员决定最终状态
                example.status = "pending"
                # 重置同步状态，待审核通过后再次触发同步
                example.rag_sync_status = "pending"
                
            await db.commit()
            await db.refresh(example)

            # 5. 异步触发 AI 增强 (意图还原、背景总结、SQL特征)
            asyncio.create_task(ExampleService._enhance_example_with_llm(example.id))
            
            return example

        except Exception as e:
            await db.rollback()
            logger.error(f"[ExampleService] Error creating from feedback: {str(e)}", exc_info=True)
            return None

    @staticmethod
    async def _enhance_example_with_llm(example_id: int):
        """
        异步调用 LLM 对经验进行“意图增强”和“背景总结”。
        """
        from app.services.ai.config import AgentConfigProvider
        from app.services.ai.runtime.agentscope.compat import SystemMessage

        async with AsyncSessionLocal() as db:
            try:
                # 1. 获取基础数据
                stmt = select(ChatBIExample).where(ChatBIExample.id == example_id)
                res = await db.execute(stmt)
                example = res.scalars().first()
                if not example: return

                # 标记正在增强
                example.enhance_status = "pending"
                await db.commit()

                # 2. 获取对话历史 (最近 3 轮)
                stmt_hist = select(AgentExecutionHistory).where(
                    AgentExecutionHistory.agent_id == example.agent_id,
                    AgentExecutionHistory.user_id == example.user_id
                ).order_by(AgentExecutionHistory.created_at.desc()).limit(5)
                res_hist = await db.execute(stmt_hist)
                histories = res_hist.scalars().all()
                histories.reverse() # 转为正序

                context_text = ""
                for h in histories:
                    context_text += f"User: {h.query}\nAI: {h.summary[:200]}...\n---\n"

                # 3. 构建 Prompt
                prompt = (
                    "你是一个数据仓库专家和提示词工程师。请根据提供的【对话背景】和最终生成的【SQL】，对该案例进行增强处理。\n\n"
                    "【对话背景】:\n"
                    f"{context_text}\n"
                    "【当前碎片化提问】: " + example.user_query + "\n"
                    "【执行成功的 SQL】: \n" + example.sql_text + "\n\n"
                    "请输出 JSON 格式，包含以下字段：\n"
                    "1. refined_query: 将碎片化提问改写为【独立且完整】的自然语言问题。例如：将“那去年的呢”改写为“查询2025年全年的销售额总额”。\n"
                    "2. context_summary: 简述产生该 SQL 的业务背景和对话脉络（100字以内）。\n"
                    "3. sql_metadata: 一个对象，包含 tables (涉及的物理表名列表), query_type (聚合/同比/TopN等), dimensions (核心维度)。\n"
                )

                llm = await AgentConfigProvider.get_configured_llm(streaming=False)
                response = await llm.ainvoke([SystemMessage(content=prompt)])
                
                # 4. 解析结果
                import re
                content = response.content
                json_match = re.search(r"({.*})", content, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    example.refined_query = data.get("refined_query")
                    example.context_summary = data.get("context_summary")
                    example.sql_metadata = data.get("sql_metadata")
                    example.enhance_status = "success"
                    
                    await db.commit()
                    logger.info(f"[ExampleEnhance] Successfully enhanced example {example_id}")
                else:
                    raise Exception("LLM output is not a valid JSON block")
            except Exception as e:
                logger.error(f"[ExampleEnhance] AI enhancement failed: {e}")
                try:
                    # 重新获取 example 对象进行状态更新
                    stmt_err = select(ChatBIExample).where(ChatBIExample.id == example_id)
                    res_err = await db.execute(stmt_err)
                    ex_err = res_err.scalars().first()
                    if ex_err:
                        ex_err.enhance_status = "failed"
                        await db.commit()
                except: pass

    @staticmethod
    async def ensure_chatbi_sample_kb_id() -> str:
        """
        确保 chatbi-example-meta 知识库在 RAGFlow 中存在，并将其 ID 写入 system_configs 中。
        如果不存在或失效，则自动创建并写入。
        """
        target_kb_id = await ConfigService.get("chatbi_sample_knowledge_base")
        client = RagFlowClient()
        kb_name = "chatbi-example-meta"
        
        # 1. 探针校验已有的 ID
        if target_kb_id:
            try:
                await client.list_documents(target_kb_id, page_size=1, page=1)
                return target_kb_id
            except Exception as e:
                err = str(e).lower()
                if "not found" in err or "doesn't exist" in err or "you don't own" in err:
                    logger.warning(f"[Example KB] Existing KB {target_kb_id} is invalid/deleted. Will re-fetch or re-create.")
                    target_kb_id = None
                else:
                    logger.warning(f"[Example KB] Probe failed for {target_kb_id}: {e}. Assuming valid for now.")
                    return target_kb_id

        # 2. 如果无有效 ID，先检索同名库
        if not target_kb_id:
            try:
                existing_kbs = await client.list_datasets(name=kb_name)
                match = next((k for k in existing_kbs if k['name'] == kb_name), None)
                if match:
                    target_kb_id = match['id']
                    logger.info(f"[Example KB] Found existing KB in RAGFlow: {target_kb_id}")
                else:
                    logger.info(f"[Example KB] Creating new KB in RAGFlow: {kb_name}")
                    new_kb = await client.create_dataset(
                        name=kb_name,
                        description="ChatBI 优质案例样本库",
                        chunk_method="one"
                    )
                    target_kb_id = new_kb['id']
                
                # 3. 将新获取的 ID 更新回配置中
                if target_kb_id:
                    await ConfigService.update_config_value(
                        key="chatbi_sample_knowledge_base",
                        value=target_kb_id,
                        changed_by="system",
                        change_reason="Auto initialized/corrected ChatBI sample KB"
                    )
            except Exception as e:
                logger.error(f"[Example KB] Failed to ensure KB '{kb_name}': {e}", exc_info=True)
                raise e

        if not target_kb_id:
            raise Exception("Failed to retrieve or create RAGFlow dataset for ChatBI examples")
            
        return target_kb_id

    @staticmethod
    async def sync_to_ragflow(example_id: int):
        """
        同步经验到 RAGFlow。使用增强后的 Markdown 模板。
        """
        async with AsyncSessionLocal() as db:
            client = RagFlowClient()
            try:
                # 1. 获取最新数据
                stmt = select(ChatBIExample).where(ChatBIExample.id == example_id)
                result = await db.execute(stmt)
                example = result.scalars().first()
                if not example or example.rag_sync_status == "syncing":
                    return

                example.rag_sync_status = "syncing"
                await db.commit()

                # 2. 环境检查
                if example.status not in ["approved", "deprecated"]:
                    example.rag_sync_status = "pending"
                    await db.commit()
                    return

                target_kb_id = await ExampleService.ensure_chatbi_sample_kb_id()

                file_name = f"chatbi_{example.trace_id}.md"

                # 3. 清理旧文档
                if example.rag_doc_id:
                    try: await client.delete_documents(target_kb_id, [example.rag_doc_id])
                    except: pass
                
                if example.status == "deprecated" or example.feedback_type == "down":
                    example.rag_doc_id = None
                    example.rag_sync_status = "removed"
                    await db.commit()
                    return

                # 4. 准备增强型 Markdown 模板
                dataset_name = "通用数据集"
                try:
                    stmt_ds = select(MetaDataset.display_name).where(MetaDataset.id == example.dataset_id)
                    res_ds = await db.execute(stmt_ds)
                    dataset_name = res_ds.scalar() or "通用数据集"
                except: pass

                # 核心：Markdown 内容用于 RAG 检索意图
                title = example.refined_query or example.user_query
                content_lines = [
                    f"# ChatBI 优质案例: {title}\n",
                    "## 🎯 核心意图 (Refined Query)",
                    f"{example.refined_query or '尚未自动改写'}\n",
                    "## 🌐 业务背景 (Context Summary)",
                    f"{example.context_summary or '无前置上下文'}\n",
                    "## 🛠 验证通过的 SQL",
                    "```sql",
                    f"{example.sql_text}",
                    "```\n",
                    "---",
                    "## 🤖 结构化数据 (仅供系统解析，请勿删除)",
                    "```json"
                ]

                # 将所有核心数据打包成 JSON 存放在末尾，确保解析稳定性
                payload = {
                    "id": example.id, # 增加 ID 方便排查
                    "question": example.refined_query or example.user_query,
                    "raw_query": example.user_query,
                    "context_summary": example.context_summary,
                    "sql": example.sql_text,
                    "dataset_name": dataset_name,
                    "metadata": {
                        "trace_id": example.trace_id,
                        "dataset_id": example.dataset_id,
                        "agent_id": example.agent_id,
                        "sql_metadata": example.sql_metadata
                    }
                }
                content_lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
                content_lines.append("```")

                content = "\n".join(content_lines)
                
                # 5. 上传
                blob = content.encode('utf-8')
                new_doc = await client.upload_document(target_kb_id, file_name, blob)
                
                if new_doc and 'id' in new_doc:
                    example.rag_doc_id = new_doc['id']
                    example.rag_sync_status = "synced"
                    example.rag_synced_at = datetime.now()
                    await client.parse_documents(target_kb_id, [new_doc['id']])
                else:
                    raise Exception("RAGFlow upload returned invalid response")
                
                await db.commit()
            except Exception as e:
                logger.error(f"[ExampleSync] Failed: {e}")
                example.rag_sync_status = "failed"
                example.rag_sync_error = str(e)
                await db.commit()

            except Exception as e:
                logger.error(f"[ExampleSync] Sync failed for example {example_id}: {str(e)}")
                # 失败回退
                try:
                    # 重新获取 session 更新状态
                    stmt_err = update(ChatBIExample).where(ChatBIExample.id == example_id).values(
                        rag_sync_status="failed",
                        rag_sync_error=str(e)
                    )
                    await db.execute(stmt_err)
                    await db.commit()
                except: pass

    @staticmethod
    async def _cleanup_remote_by_name(client: RagFlowClient, kb_id: str, file_name: str):
        """
        已合并入 sync_to_ragflow，仅保留作为兼容或后续扩展使用。
        """
        try:
            docs = await client.list_documents(kb_id, name=file_name)
            to_delete = [d['id'] for d in docs if d['name'] == file_name]
            if to_delete:
                await client.delete_documents(kb_id, to_delete)
        except Exception as e:
            logger.warning(f"[ExampleSync] Cleanup failed: {e}")

    @staticmethod
    async def search_examples(query: str, dataset_id: int = None, top_k: int = 5, history: List[Any] = None) -> List[Dict[str, Any]]:
        """
        从经验库中检索相似案例。支持意图改写（De-contextualization）。
        """
        try:
            logger.info(f"[ExampleSearch] >>> Start searching examples for query: '{query}'")
            
            try:
                target_kb_id = await ExampleService.ensure_chatbi_sample_kb_id()
            except Exception as e:
                logger.warning(f"[ExampleSearch] Skip: Failed to ensure 'chatbi_sample_knowledge_base': {e}")
                return []

            # 1. 意图改写：如果 query 太短或包含代词，尝试根据 history 进行改写
            search_query = query
            if history and (len(query) < 8 or any(p in query for p in ["那", "它", "这个", "之前", "上一个", "刚才", "统计结果"])):
                try:
                    search_query = await ExampleService._rewrite_query_for_search(query, history)
                    logger.info(f"[ExampleSearch] Intent Rewritten: '{query}' -> '{search_query}'")
                except Exception as ree:
                    logger.warning(f"[ExampleSearch] Intent rewrite failed: {ree}")

            # 2. 动态获取检索配置
            threshold_str = await ConfigService.get("chatbi_sample_similarity_threshold")
            weight_str = await ConfigService.get("chatbi_sample_vector_similarity_weight")
            
            similarity_threshold = float(threshold_str) if threshold_str else 0.4
            vector_weight = float(weight_str) if weight_str else 0.5

            logger.info(f"[ExampleSearch] RAGFlow Params: KB_ID={target_kb_id}, Query='{search_query}', Threshold={similarity_threshold}, VectorWeight={vector_weight}, TopK={top_k}")

            client = RagFlowClient()
            # 语义检索
            results = await client.retrieve(
                search_query, 
                [target_kb_id], 
                top_k=top_k, 
                similarity_threshold=similarity_threshold,
                vector_similarity_weight=vector_weight
            )
            
            logger.info(f"[ExampleSearch] RAGFlow returned {len(results)} raw chunks.")
            
            examples = []
            for i, res in enumerate(results):
                content = res.get("content", "")
                similarity = res.get("similarity", 0)

                # 优先尝试解析末尾的结构化 JSON 块
                json_match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        examples.append({
                            "id": data.get("id"),
                            "question": data.get("question") or data.get("raw_query"),
                            "sql": data.get("sql"),
                            "context_summary": data.get("context_summary"),
                            "dataset_name": data.get("dataset_name"),
                            "trace_id": data.get("metadata", {}).get("trace_id"),
                            "sql_metadata": data.get("metadata", {}).get("sql_metadata"),
                            "similarity": similarity
                        })
                        continue
                    except Exception as je:
                        logger.warning(f"[ExampleSearch] Failed to parse JSON block in chunk {i+1}: {je}")

                # 兜底方案：正则匹配
                q_match = re.search(r"## (?:🎯 )?核心意图.*?\n+(.*?)\n+##", content, re.DOTALL | re.IGNORECASE)
                sql_match = re.search(r"```sql\n(.*?)\n```", content, re.DOTALL | re.IGNORECASE)
                
                if sql_match and q_match:
                    examples.append({
                        "question": q_match.group(1).strip(),
                        "sql": sql_match.group(1).strip(),
                        "similarity": similarity
                    })
            
            logger.info(f"[ExampleSearch] Final Matched Examples: {len(examples)} (After parsing)")
            return examples
        except Exception as e:
            logger.error(f"[ExampleSearch] Search execution exception: {e}", exc_info=True)
            return []

    @staticmethod
    async def _rewrite_query_for_search(query: str, history: List[Any]) -> str:
        """
        利用 LLM 进行搜索意图改写，去除对上下文的依赖。
        """
        from app.services.ai.config import AgentConfigProvider
        from app.services.ai.runtime.agentscope.compat import SystemMessage
        
        # 仅取最近 3 轮历史
        recent_history = history[-6:] if len(history) > 6 else history
        hist_text = ""
        for m in recent_history:
            role = "User" if m.__class__.__name__ == "HumanMessage" else "Assistant"
            content = m.content if hasattr(m, "content") else str(m)
            hist_text += f"{role}: {content[:100]}\n"

        prompt = (
            "你是一个搜索优化助手。请根据对话历史，将用户的【最新提问】改写为一个【独立、完整、且包含具体名词】的查询语句，以便于在知识库中进行语义检索。\n\n"
            "要求：\n"
            "1. 补全代词（如：那这个、它、之前那个、那个人的等）。\n"
            "2. 提取核心业务实体和指标。\n"
            "3. 只输出改写后的句子，不要任何解释。\n\n"
            "【历史记录】:\n" + hist_text + "\n"
            "【最新提问】: " + query + "\n\n"
            "【改写后查询词】:"
        )
        
        llm = await AgentConfigProvider.get_configured_llm(streaming=False)
        response = await llm.ainvoke([SystemMessage(content=prompt)])
        return response.content.strip().strip('"').strip("'")
    def build_few_shot_prompt(examples: List[Dict[str, Any]]) -> str:
        """
        将检索到的案例构建为 Few-Shot Prompt 块。
        【优化】：
        - 阈值从 0.80 降至 0.65，让强力引导在实际场景中更容易触发
        - 明确"必须复用 / 允许适配 / 禁止改动"三层边界，避免模型走极端
        - 相似度用语义标签代替数字，减少无意义信息
        - 第一个（最高相似度）案例加⭐标记，明确优先级
        """
        if not examples:
            return ""

        max_sim = max([ex.get('similarity', 0) for ex in examples]) if examples else 0

        # 相似度 → 语义标签（给模型看，不是给人看）
        def _sim_label(sim: float) -> str:
            if sim >= 0.65: return "【极高匹配】"
            if sim >= 0.50: return "【较高匹配】"
            return "【供参考】"

        prompt_lines = [
            "\n## 💡 核心参考案例库 (Matched Examples Library)\n",
            "系统已从【ChatBI 经验库】中检索到以下经过人工验证的历史优质 SQL 案例。\n"
        ]

        # 分级核心指令：阈值降为 0.65
        if max_sim >= 0.65:
            prompt_lines.append(
                "> **【强制参考指令】** 发现与用户问题高度匹配的历史案例。请严格遵守以下三层规则：\n"
                "> \n"
                "> ✅ **【必须复用】**：物理表名（FROM/JOIN 中的表）、多表 JOIN 连接关系、核心聚合函数（SUM/COUNT/AVG/窗口函数等）\n"
                "> 🔧 **【允许适配】**：WHERE 条件中的时间范围、筛选值、SELECT 字段的别名，根据用户当前问题灵活调整\n"
                "> ❌ **【禁止改动】**：除非 Schema 明确显示该表不存在，否则不得替换案例中的核心表名\n"
            )
        else:
            prompt_lines.append(
                "> **【参考准则】** 以下案例提供选表逻辑参考：\n"
                "> \n"
                "> ✅ **【必须复用】**：物理表名（FROM/JOIN）、已验证的 JOIN 关系\n"
                "> 🔧 **【允许适配】**：WHERE 条件、时间范围、SELECT 字段\n"
                "> ❌ **【禁止随意替换】**：不要用其他未经验证的表替代案例中的核心表\n"
            )

        for i, ex in enumerate(examples, 1):
            sim = ex.get('similarity', 0)
            label = _sim_label(sim)
            ds_info = f" [数据集: {ex['dataset_name']}]" if ex.get('dataset_name') else ""

            # 第一个案例（最高相似度）加⭐优先标记
            if i == 1 and max_sim >= 0.50:
                prompt_lines.append(f"### ⭐ 案例 1 {label}{ds_info}（最优先参考）")
            else:
                prompt_lines.append(f"### 案例 {i} {label}{ds_info}")

            prompt_lines.append(f"- **历史问题**: {ex['question']}")

            if ex.get('context_summary'):
                prompt_lines.append(f"- **业务背景**: {ex['context_summary']}")

            if ex.get('sql_metadata') and isinstance(ex['sql_metadata'], dict):
                meta = ex['sql_metadata']
                logic_parts = []
                if meta.get('query_type'): logic_parts.append(f"查询类型: {meta['query_type']}")
                if meta.get('dimensions'): logic_parts.append(f"核心维度: {', '.join(meta['dimensions'])}")
                if logic_parts:
                    prompt_lines.append(f"- **专家策略**: {' | '.join(logic_parts)}")

            prompt_lines.append("- **验证通过的 SQL（核心表和 JOIN 逻辑必须复用）**:")
            prompt_lines.append(f"```sql\n{ex['sql']}\n```\n")

        prompt_lines.append(
            "---\n"
            "**最终提醒**：\n"
            "1. 优先使用案例中的物理表，不要「幻觉」出新表\n"
            "2. 案例的 JOIN/聚合逻辑已经过业务验证，请直接复用结构，仅调整 WHERE/SELECT 细节\n"
            "3. 若案例表名在当前 Schema 中确实不存在，才可改用其他表，并需在思考中说明原因\n"
        )
        return "\n".join(prompt_lines)

    @staticmethod

    @staticmethod
    def build_few_shot_reminder(examples: List[Dict[str, Any]]) -> str:
        """
        构建精简版"二次提醒"，在 get_dataset_schema 返回后注入。
        目的：让模型在看完实时 Schema 后马上重新聚焦到案例 SQL 逻辑，
        而不是完全被新鲜的 Schema 数据带偏。
        内容刻意精简，不重复完整 SQL，避免过度占用 context window。
        """
        if not examples:
            return ""

        lines = [
            "⚠️ **【经验库二次提醒】** 你已获取到表结构数据。在生成 SQL 之前，请务必回顾以下经过人工验证的历史案例核心逻辑：\n"
        ]

        for i, ex in enumerate(examples, 1):
            question = ex.get("question", "")
            sql = ex.get("sql", "")
            sql_meta = ex.get("sql_metadata") or {}
            context = ex.get("context_summary", "")
            sim = ex.get("similarity", 0)

            # 从 sql_metadata 提取关键信息
            tables = sql_meta.get("tables", [])
            query_type = sql_meta.get("query_type", "")
            dimensions = sql_meta.get("dimensions", [])

            # 若无 sql_metadata，从 SQL 里简单提取表名
            if not tables and sql:
                import re as _re
                tables = _re.findall(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', sql, _re.IGNORECASE)
                tables = [t for pair in tables for t in pair if t]

            case_lines = [f"**案例 {i}** (相似度: {sim:.2f}): {question}"]
            if context:
                case_lines.append(f"  - 业务背景: {context}")
            if tables:
                case_lines.append(f"  - 📌 核心表: `{'`, `'.join(tables)}`（请优先从这些表查询）")
            if query_type:
                case_lines.append(f"  - 查询类型: {query_type}" + (f" | 核心维度: {', '.join(dimensions)}" if dimensions else ""))
            # 仅展示前3行 SQL 作为线索，不重复整段
            if sql:
                sql_preview = "\n".join(sql.strip().splitlines()[:3])
                if len(sql.strip().splitlines()) > 3:
                    sql_preview += "\n  ..."
                case_lines.append(f"  - SQL 逻辑线索:\n```sql\n{sql_preview}\n```")

            lines.append("\n".join(case_lines))

        lines.append(
            "\n**执行要求**：请基于以上案例的表选择和 SQL 结构，结合你刚查到的表字段，生成最终 SQL。"
            "若案例中的表名在当前 Schema 中存在，必须优先使用。"
        )
        return "\n\n".join(lines)

    @staticmethod
    async def record_usage(example_ids: List[int], trace_id: str, similarities: List[float] = None):
        """
        记录经验库案例的引用情况。
        """
        if not example_ids:
            return
            
        async with AsyncSessionLocal() as db:
            try:
                # 1. 更新主表引用计数
                stmt = update(ChatBIExample).where(
                    ChatBIExample.id.in_(example_ids)
                ).values(use_count=ChatBIExample.use_count + 1)
                await db.execute(stmt)

                # 2. 插入详细引用流水
                for i, eid in enumerate(example_ids):
                    sim = similarities[i] if similarities and i < len(similarities) else None
                    usage = ChatBIExampleUsage(
                        example_id=eid,
                        trace_id=trace_id,
                        similarity=sim
                    )
                    db.add(usage)
                
                await db.commit()
                logger.info(f"[ExampleUsage] Recorded usage for {example_ids} in trace {trace_id}")
            except Exception as e:
                logger.error(f"[ExampleUsage] Failed to record usage: {e}")

    @staticmethod
    async def audit_example(db: AsyncSession, example_id: int, status: str) -> bool:
        """
        审核经验记录 (支持联动删除 RAGFlow)。
        """
        stmt = select(ChatBIExample).where(ChatBIExample.id == example_id)
        result = await db.execute(stmt)
        example = result.scalars().first()
        if not example:
            return False
        
        # 联动删除逻辑增强：不仅是 deprecated，如果是 rejected 也需要同步清理远程数据
        if status in ["deprecated", "rejected"] and example.rag_doc_id:
            try:
                target_kb_id = await ExampleService.ensure_chatbi_sample_kb_id()
                client = RagFlowClient()
                await client.delete_documents(target_kb_id, [example.rag_doc_id])
                example.rag_doc_id = None
                example.rag_sync_status = "removed"
            except Exception as e:
                logger.error(f"[ExampleAudit] Failed to delete from RAGFlow during audit: {e}")

        example.status = status
        await db.commit()
        return True

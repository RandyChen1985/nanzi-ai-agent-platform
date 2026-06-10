from typing import Dict, Any, List
import logging
import json
from pydantic import BaseModel, Field
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.config import AgentConfigProvider
from app.core.orm import AsyncSessionLocal

logger = logging.getLogger(__name__)

class ColumnMetadata(BaseModel):
    physical_name: str = Field(description="数据库物理列名")
    term: str = Field(description="业务术语，如 '机房名称' 而非 'room_name'")
    type: str = Field(description="字段数据类型")
    description: str = Field(description="详细的业务含义描述")
    enums: List[Dict[str, Any]] = Field(default=[], description="枚举值列表，如 [{'value': 0, 'label': '正常'}]")
    synonyms: List[str] = Field(default=[], description="该字段的同义词，用于增强检索")

class TableMetadata(BaseModel):
    physical_name: str = Field(description="数据库物理表名")
    term: str = Field(description="业务术语，如 '资产配置表'")
    description: str = Field(description="该表存储的数据内容概要")
    synonyms: List[str] = Field(default=[], description="表的同义词")
    columns: List[ColumnMetadata] = Field(description="字段列表")

class MetricMetadata(BaseModel):
    name: str = Field(description="指标物理名")
    display_name: str = Field(description="指标显示名")
    description: str = Field(description="指标逻辑描述")
    calculation_logic: str = Field(description="具体的 SQL 计算逻辑")
    unit: str = Field(default="", description="单位 (e.g. 'kWh', '%')")

class RelationshipMetadata(BaseModel):
    source_table: str = Field(description="源表物理名")
    target_table: str = Field(description="目标表物理名")
    type: str = Field(description="关联类型: one_to_one, one_to_many, many_to_one")
    condition: str = Field(description="关联条件 (e.g. 't1.id = t2.t1_id')")
    description: str = Field(description="关系描述")

class ImportResult(BaseModel):
    tables: List[TableMetadata] = Field(description="识别出的所有表结构")
    metrics: List[MetricMetadata] = Field(default=[], description="识别出的业务指标")
    relationships: List[RelationshipMetadata] = Field(default=[], description="识别出的表关联关系")

class MetricRecommendationResult(BaseModel):
    metrics: List[MetricMetadata] = Field(description="推荐的高价值业务指标列表")

class DatasetEnhanceResult(BaseModel):
    description: str = Field(description="数据集的业务背景描述，100字以内")
    tags: List[str] = Field(description="数据集的标签列表，如 ['财务', '生产', '核心数据']")

class MetadataGeneratorService:
    @staticmethod
    def _format_instructions(model_cls: type[BaseModel]) -> str:
        schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
        return (
            "必须只返回一个 JSON 对象，不要 Markdown，不要解释文字。"
            f"JSON Schema:\n{schema}"
        )

    @staticmethod
    def _extract_json(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except Exception:
            import re

            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise
            return json.loads(match.group())

    @staticmethod
    async def _invoke_json(
        llm: Any,
        result_model: type[BaseModel],
        system_prompt_template: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        system_prompt = system_prompt_template.replace(
            "{format_instructions}",
            MetadataGeneratorService._format_instructions(result_model),
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        raw = getattr(response, "content", "") or str(response)
        data = MetadataGeneratorService._extract_json(raw)
        return result_model.model_validate(data).model_dump()

    @staticmethod
    async def _save_trace_log(trace_id: str, step: int, event: str, output: Any, error: str = None, execution_time: float = 0):
        """Helper to save a single trace log entry"""
        try:
            from app.core.orm import AsyncSessionLocal
            from app.models.audit import AgentExecutionTrace
            import json
            from datetime import datetime
            
            async with AsyncSessionLocal() as session:
                log = AgentExecutionTrace(
                    trace_id=trace_id,
                    step_number=step,
                    event_type=event,
                    agent_name="MetadataGenerator",
                    tool_name="LLM",
                    tool_input={}, # Can be populated if needed
                    tool_output=output if isinstance(output, (dict, list)) else {"raw": str(output)},
                    execution_time_ms=execution_time,
                    status="error" if error else "success",
                    error_message=error,
                    created_at=datetime.now()
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save trace log: {e}", exc_info=True)

    @staticmethod
    async def generate_from_ddl(content: str) -> Dict[str, Any]:
        """
        利用 LLM 对输入内容进行深度语义分析，提取并推断业务元数据。
        支持 DDL、Markdown 表格或自然语言描述。
        """
        from app.services.config_service import ConfigService
        import uuid
        import time
        
        trace_id = f"import-{str(uuid.uuid4())}"
        start_total = time.time()
        
        try:
            # Step 1: Initialization
            await MetadataGeneratorService._save_trace_log(trace_id, 1, "start", {"input_length": len(content), "preview": content[:200]})

            # 1. 获取 LLM (适配 Model Management)
            from app.services.ai.config import AgentConfigProvider
            from app.schemas.agent import ChatConfig
            
            # 3. 获取智能体配置 (Metadata Specialist)
            async with AsyncSessionLocal() as session:
                agent_config = await AgentManagerService.get_active_agent_config(
                    session, agent_name='metadata-specialist'
                )
            
            # Prepare overrides if agent_config exists
            # Prepare overrides if agent_config exists
            chat_config = agent_config
            
            if not chat_config:
                logger.warning("Metadata Specialist config not found in DB, using system default model.")

            # Get configured LLM (automatically handles ai_models lookup or system default)
            llm = await AgentConfigProvider.get_configured_llm(streaming=False, config=chat_config)

            # 4. Resolve System Prompt
            if not agent_config or not agent_config.system_prompt:
                system_prompt_template = (
                    "你是一个资深的业务分析师和数据库建模专家。\n"
                    "请分析用户提供的数据库 DDL、Markdown 表格或自然语言描述，提取出精确的元数据结构。\n"
                    "确保推断出合理的业务术语(term)和详细的字段描述。\n"
                    "{format_instructions}"
                )
            else:
                system_prompt_template = agent_config.system_prompt
                # Ensure format_instructions is present if not already in DB prompt
                if "{format_instructions}" not in system_prompt_template:
                    system_prompt_template += "\n\n{format_instructions}"

            logger.info(f"Generating metadata for content (first 100 chars): {content[:100]}...")
            
            
            # 3. 调用 LLM
            start_llm = time.time()
            result = await MetadataGeneratorService._invoke_json(
                llm,
                ImportResult,
                system_prompt_template,
                f"请分析以下内容并生成元数据：\n\n{content}",
            )
            
            duration_llm = (time.time() - start_llm) * 1000
            
            logger.info(f"LLM Result: {json.dumps(result, ensure_ascii=False)[:200]}...")
            
            table_name = result.get('physical_name') or (result.get('tables')[0].get('physical_name') if result.get('tables') else 'unknown')
            logger.info(f"Successfully generated metadata for table: {table_name}")
            
            await MetadataGeneratorService._save_trace_log(trace_id, 4, "llm_success", result, execution_time=duration_llm)
            
            # Return result AND trace_id
            if isinstance(result, dict):
                result["_trace_id"] = trace_id
            
            return result
        except Exception as e:
            logger.error(f"Error generating metadata: {str(e)}", exc_info=True)
            duration_error = (time.time() - start_total) * 1000
            await MetadataGeneratorService._save_trace_log(trace_id, 5, "error", {"error_str": str(e)}, error=str(e), execution_time=duration_error)
            
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"智能解析失败，请检查日志。Trace ID: {trace_id}. Error: {str(e)}")

    @staticmethod
    async def recommend_metrics(dataset_id: int, schema_context: str) -> Dict[str, Any]:
        """
        根据数据集 Schema 推荐业务指标
        """
        import uuid
        import time
        from app.services.config_service import ConfigService
        from app.services.ai.agent_manager import AgentManagerService
        from app.schemas.agent import ChatConfig
        
        trace_id = f"metric-rec-{str(uuid.uuid4())}"
        start_total = time.time()
        
        try:
            # 1. Log Start
            await MetadataGeneratorService._save_trace_log(trace_id, 1, "start_recommendation", {"dataset_id": dataset_id, "schema_len": len(schema_context)})

            # 2. Get Agent Config (Reusable logic, could be extracted)
            async with AsyncSessionLocal() as session:
                agent_config = await AgentManagerService.get_active_agent_config(
                    session, agent_name='metadata-specialist'
                )
            
            chat_config = agent_config
            if not chat_config:
                 logger.warning("Metadata Specialist config not found, using default.")

            # Get configured LLM (automatically handles ai_models lookup or system default)
            llm = await AgentConfigProvider.get_configured_llm(streaming=False, config=chat_config)

            # 3. Prompt
            system_prompt = (
                "你是一个精通数据分析的 BI 专家。\n"
                "请分析给定的数据库 Schema（包含表结构、字段含义），推荐 5-10 个**最有业务价值**的分析指标。\n"
                "指标类型可以是：\n"
                "1. **聚合型 (KPI)**: 如总数、平均值、比率 (e.g., 'PUE均值', '机房总数')。\n"
                "2. **维度分布 (Dimension)**: 如按类别分组统计 (e.g., '各区域机房分布', '设备类型占比')。\n"
                "3. **常用视图 (Data View)**: 常用查询字段组合 (e.g., '机房详细列表: 名称, 编码, 地址')。\n\n"
                "对于 SQL (calculation_logic 字段)：\n"
                "- 必须是合法的 ClickHouse SQL 表达式或完整 Query。\n"
                "- 对于分布/视图类，请写出完整的 `SELECT ... FROM ... [GROUP BY ...]` 语句。\n"
                "- 禁止使用中文别名。\n"
                "{format_instructions}"
            )
            
            # 4. Invoke
            start_llm = time.time()
            result = await MetadataGeneratorService._invoke_json(
                llm,
                MetricRecommendationResult,
                system_prompt,
                f"Schema 定义如下：\n\n{schema_context}",
            )
            duration_llm = (time.time() - start_llm) * 1000
            
            # 5. Log Success
            await MetadataGeneratorService._save_trace_log(trace_id, 4, "llm_success", result, execution_time=duration_llm)
            
            if isinstance(result, dict):
                result["_trace_id"] = trace_id
                
            return result

        except Exception as e:
            logger.error(f"Error recommending metrics: {e}", exc_info=True)
            await MetadataGeneratorService._save_trace_log(trace_id, 5, "error", {"error": str(e)})
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"指标推荐失败: {str(e)}")

    @staticmethod
    async def enhance_dataset_metadata(dataset_id: int, tables_summary: str) -> Dict[str, Any]:
        """
        AI 辅助: 根据表信息动态生成数据集的【描述】和【标签】
        """
        import uuid
        import time
        from app.services.ai.agent_manager import AgentManagerService
        
        trace_id = f"ds-enhance-{str(uuid.uuid4())}"
        start_total = time.time()
        
        try:
            # 1. Log Start
            await MetadataGeneratorService._save_trace_log(trace_id, 1, "start_dataset_enhance", {"dataset_id": dataset_id, "tables_summary": tables_summary})

            # 2. Get Agent Config
            async with AsyncSessionLocal() as session:
                agent_config = await AgentManagerService.get_active_agent_config(
                    session, agent_name='metadata-specialist'
                )
            
            # Get configured LLM
            llm = await AgentConfigProvider.get_configured_llm(streaming=False, config=agent_config)

            # 3. Prompt
            system_prompt = (
                "你是一个精通元数据管理的业务架构师。\n"
                "请分析给定的数据集包含的【表信息】（物理名及业务术语），为该数据集生成专业的【业务描述】和【分类标签】。\n\n"
                "要求：\n"
                "1. description: 100字以内的业务背景描述，说明该数据集主要用于解决什么业务问题。\n"
                "2. tags: 3-5个简短的标签，如 ['财务', '生产', '核心', '监控'] 等。\n"
                "{format_instructions}"
            )
            
            # 4. Invoke
            start_llm = time.time()
            result = await MetadataGeneratorService._invoke_json(
                llm,
                DatasetEnhanceResult,
                system_prompt,
                f"该数据集包含以下表信息：\n\n{tables_summary}",
            )
            duration_llm = (time.time() - start_llm) * 1000
            
            # 5. Log Success
            await MetadataGeneratorService._save_trace_log(trace_id, 4, "llm_success", result, execution_time=duration_llm)
            
            if isinstance(result, dict):
                result["_trace_id"] = trace_id
                
            return result

        except Exception as e:
            logger.error(f"Error enhancing dataset metadata: {e}", exc_info=True)
            await MetadataGeneratorService._save_trace_log(trace_id, 5, "error", {"error": str(e)})
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"AI 辅助生成元数据失败: {str(e)}")

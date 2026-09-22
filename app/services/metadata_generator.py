from typing import Awaitable, Callable, Dict, Any, List, Optional
import logging
import json
import re
from pydantic import BaseModel, Field
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.config import AgentConfigProvider
from app.core.orm import AsyncSessionLocal
from app.services.metadata_relationship_candidate_service import (
    MetadataRelationshipCandidateService,
)
from app.services.metadata_relationship_probe_service import (
    MetadataRelationshipProbeService,
)

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]

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
    partition_fields: List[str] = Field(default=[], description="数据库分区字段物理名列表；必须从 DDL 的 PARTITION 定义提取")
    index_fields: List[str] = Field(default=[], description="数据库索引字段物理名列表；从 PRIMARY KEY、UNIQUE/KEY/INDEX 定义提取并去重")
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

class RelationshipRecommendation(BaseModel):
    source_table: str = Field(description="源表物理名（必须是 Schema 中实际存在的物理表名）")
    target_table: str = Field(description="目标表物理名（必须是 Schema 中实际存在的物理表名）")
    condition: str = Field(description="关联条件 JOIN 表达式，如 't1.id = t2.t1_id'，使用物理表/字段名")
    relation_type: str = Field(description="关联类型: one_to_one, one_to_many, many_to_one")
    description: str = Field(description="该关联关系的业务含义描述")
    confidence: float = Field(
        description="置信度打分，0~1 之间的小数，值越高表示模型对这条关联关系越有把握",
        ge=0,
        le=1,
    )
    source: str = Field(default="AI", description="推荐来源标识，默认 'AI'，可填 'FK'/'NAMING'/'AI' 等")

class RelationshipRecommendationResult(BaseModel):
    relationships: List[RelationshipRecommendation] = Field(description="推荐的表关联关系列表")

class RelationshipDescriptionItem(BaseModel):
    pair_id: int = Field(description="对应输入的 pair_id")
    description: str = Field(description="关系业务含义描述，不超过 80 个中文字符")

class RelationshipDescriptionResult(BaseModel):
    descriptions: List[RelationshipDescriptionItem] = Field(
        description="每条已确认关系的业务描述，必须覆盖全部输入 pair_id"
    )

RELATIONSHIP_DESCRIPTION_SYSTEM_PROMPT = (
    "你是一个数据建模专家。输入中的关系已通过数据库外键约束确认，"
    "请结合表名、字段名和探测原因，为每条关系输出简洁的中文业务含义描述。\n"
    "严格规则：\n"
    "1. 只能为输入给出的 pair_id 输出描述，不得新增或删除。\n"
    "2. description 不超过 80 个中文字符，不要复述 SQL。\n"
    "3. 只返回一个 JSON 对象。\n"
    "{format_instructions}"
)

class MetricRecommendationResult(BaseModel):
    metrics: List[MetricMetadata] = Field(description="推荐的高价值业务指标列表")

class DatasetEnhanceResult(BaseModel):
    description: str = Field(description="数据集的业务背景描述，100字以内")
    tags: List[str] = Field(description="数据集的标签列表，如 ['财务', '生产', '核心数据']")

# 元数据智能导入实际生效的系统提示词。
#
# 为什么不直接用 DB 里 metadata-specialist 智能体的提示词：该智能体在两个库的种子里都被
# 显式禁用（db-prod/V20-add_agent_enabled_status.sql 置 is_enabled=0，
# db-prod-pg/V0-baseline.sql 置 FALSE），此后没有任何迁移再启用它；而
# AgentManagerService.get_active_agent_config() 在 is_enabled 为假时直接返回 None，
# 于是 generate_from_ddl() 会稳定落到本模板。
#
# 也就是说，按发行种子的状态，下面这段就是线上真正使用的元数据解析提示词，而不是一层
# "以防万一"的占位符——它必须承载完整的解析要求，否则 enums/synonyms/metrics/relationships
# 这些能力会静默退化。修改时请同步：
#   architech/prompts/meta/metadata_generator.md
#   architech/prompts/system_agents/metadata/metadata_specialist.md
# 字段清单以 ImportResult 的 JSON Schema 为准，此处的 {format_instructions} 占位符
# 由 _invoke_json() 注入，不要删除。
DEFAULT_METADATA_SYSTEM_PROMPT = (
    "你是一个资深的业务分析师和数据库建模专家。\n"
    "用户将提供关于数据库表结构的描述信息，其格式可能是：\n"
    "1. SQL DDL 语句 (如 CREATE TABLE...)\n"
    "2. Markdown 表格 (包含字段、类型、描述等)\n"
    "3. 业务口径描述或自然语言定义的表结构\n"
    "\n"
    "你的任务是将用户提供的 DDL 或表格描述转化为标准化的元数据 JSON。\n"
    "特别要求：\n"
    "- 提取业务术语（term）：提供最准确的中文业务名称。如果输入缺失备注/注释，请根据物理名（Physical Name）进行智能推断。\n"
    "- 识别描述（description）：提供详细的业务含义描述。如果原始信息缺失且字段名具有代表性，请结合行业知识生成建议的业务解释。\n"
    "- 识别物理名（physical_name）：如果是 DDL 请严格保留；如果是自然语言请按下划线命名法推断（如 '机房ID' -> 'room_id'）。\n"
    "- 识别枚举值（enums）：从描述文本中提取可能的取值范围。\n"
    "- 生成同义词（synonyms）：为表和核心字段提供 2-3 个业务同义词，帮助 AI 检索。\n"
    "- 提取指标（metrics）：如果输入包含计算逻辑或统计需求，提取为指标。\n"
    "- 提取关系（relationships）：从 JOIN 语句或外键约束中提取表关联。\n"
    "- 支持多表：如果输入包含多个 CREATE TABLE 语句，请在 tables 数组中返回所有表，不要只返回第一张。\n"
    "- 识别查询优化字段：partition_fields 是分区字段物理名列表，从 DDL 的 PARTITION 定义提取；"
    "index_fields 是索引字段物理名列表，从 PRIMARY KEY、UNIQUE/KEY/INDEX 定义提取并去重。"
    "两者都必须填写真实存在的物理字段名，不得编造；没有则返回空数组。\n"
    "\n"
    "{format_instructions}"
)

class MetadataGeneratorService:
    @staticmethod
    async def _emit_progress(
        progress_callback: ProgressCallback,
        **payload: Any,
    ) -> None:
        """发送生成进度；进度通道异常不能打断主推荐任务。"""
        if progress_callback is None:
            return
        try:
            await progress_callback(payload)
        except Exception as exc:
            logger.warning(
                "元数据 AI 进度回调失败: phase=%s, error=%s",
                payload.get("phase"),
                exc,
                exc_info=True,
            )

    @staticmethod
    def _extract_schema_table_names(schema_context: str) -> List[str]:
        """从导出的 Schema 文本中提取物理表名，用于驱动逐表关系扫描。"""
        text = str(schema_context or "")
        names: List[str] = []
        seen = set()
        patterns = [
            r"---\s*\[Schema:\d+\][^\n]*\btype=table\b[^\n]*\btable=([^\s]+)",
            r"^table_name:\s*([^\s]+)\s*$",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
                name = str(match.group(1) or "").strip().strip("'\"")
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return names

    @staticmethod
    def _relationship_pair_key(rel: Dict[str, Any]) -> Optional[tuple[str, str, str]]:
        """构建关系去重键，源/目标表无序，condition 参与区分不同连接条件。"""
        src = (rel.get("source_table") or "").strip().lower()
        tgt = (rel.get("target_table") or "").strip().lower()
        cond = (rel.get("condition") or "").strip().lower()
        if not src or not tgt:
            return None
        return tuple(sorted([src, tgt])) + (cond,)

    @staticmethod
    def _extract_condition_pair_keys(condition: str) -> List[tuple[str, str]]:
        """从 JOIN 条件里解析参与比较的物理表对键，用于去重前置匹配。"""
        keys: List[tuple[str, str]] = []
        for match in re.finditer(
            r"([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)\s*=\s*"
            r"([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)",
            str(condition or ""),
        ):
            left_table = str(match.group(1) or "").strip().lower()
            right_table = str(match.group(3) or "").strip().lower()
            if left_table and right_table:
                keys.append(tuple(sorted((left_table, right_table))))
        return list(dict.fromkeys(keys))

    @staticmethod
    def _format_instructions(model_cls: type[BaseModel]) -> str:
        schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
        return (
            "必须只返回一个 JSON 对象，不要 Markdown，不要解释文字。"
            f"JSON Schema:\n{schema}"
        )

    @staticmethod
    def _extract_json(raw: str) -> Dict[str, Any]:
        """从模型输出中提取完整的 JSON 对象，兼容说明文字和 Markdown 代码块。"""
        text = (raw or "").strip()
        if not text:
            raise ValueError("模型返回内容为空，无法解析 JSON")

        # 代码块优先解析，避免说明文字中出现 JSON 示例时误取外层文本。
        candidates = [text]
        fenced_match = re.search(
            r"```(?:json)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced_match:
            candidates.insert(0, fenced_match.group(1).strip())

        decoder = json.JSONDecoder()
        last_error: Optional[Exception] = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
                last_error = ValueError("模型返回的 JSON 顶层结构不是对象")
            except json.JSONDecodeError as exc:
                last_error = exc

            # 使用 JSONDecoder 扫描每个对象起点，正确处理字符串中的花括号，
            # 同时避免原贪婪正则把多个对象或截断文本拼成一个无效 JSON。
            valid_objects = []
            stripped_candidate = candidate.lstrip()
            if stripped_candidate.startswith("{"):
                # 输出以对象开头时只尝试外层对象，避免外层截断后误返回嵌套对象。
                scan_starts = [candidate.find("{")]
            else:
                # 输出包含前置说明时，扫描所有对象起点以兼容说明文字。
                scan_starts = [
                    start for start, char in enumerate(candidate) if char == "{"
                ]

            for start in scan_starts:
                try:
                    parsed, end = decoder.raw_decode(candidate[start:])
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue
                if isinstance(parsed, dict):
                    valid_objects.append((end, parsed))

            if valid_objects:
                # 外层对象通常覆盖范围最大，优先返回它而不是字符串中的嵌套对象。
                _, parsed = max(valid_objects, key=lambda item: item[0])
                logger.info(
                    "从模型输出中提取嵌入式 JSON 对象成功: raw_length=%s",
                    len(candidate),
                )
                return parsed

        logger.error(
            "模型 JSON 输出解析失败: raw_length=%s, error=%s",
            len(text),
            last_error,
        )
        if last_error is not None:
            raise last_error
        raise ValueError("模型返回内容无法解析为 JSON 对象")

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
        try:
            data = MetadataGeneratorService._extract_json(raw)
            return result_model.model_validate(data).model_dump()
        except (json.JSONDecodeError, ValueError) as first_error:
            # 截断或轻微格式漂移时重试一次，避免把一次模型输出异常直接暴露给接口。
            logger.warning(
                "模型结构化输出首次解析失败，准备重试: model=%s, raw_length=%s, error=%s",
                getattr(llm, "model", "unknown"),
                len(raw),
                first_error,
            )
            retry_system_prompt = (
                f"{system_prompt}\n\n"
                "【结构化输出重试要求】上一次输出未通过 JSON 或 Schema 校验。"
                "请重新生成完整结果：只返回一个可被 json.loads 解析的 JSON 对象，"
                "不要 Markdown、不要解释文字；保持描述简洁、避免重复关系，确保所有字符串、"
                "数组及对象都完整闭合。"
            )
            retry_response = await llm.ainvoke(
                [
                    SystemMessage(content=retry_system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            retry_raw = getattr(retry_response, "content", "") or str(retry_response)
            try:
                retry_data = MetadataGeneratorService._extract_json(retry_raw)
                return result_model.model_validate(retry_data).model_dump()
            except (json.JSONDecodeError, ValueError) as retry_error:
                logger.error(
                    "模型结构化输出重试仍然失败: model=%s, first_raw_length=%s, "
                    "retry_raw_length=%s, error=%s",
                    getattr(llm, "model", "unknown"),
                    len(raw),
                    len(retry_raw),
                    retry_error,
                    exc_info=True,
                )
                raise retry_error from first_error

    @staticmethod
    async def _save_trace_log(trace_id: str, step: int, event: str, output: Any, error: str = None, execution_time: float = 0):
        """Helper to save a single trace log entry"""
        try:
            from app.core.orm import AsyncSessionLocal
            from app.models.audit import AgentExecutionTrace
            from app.services.ai.audit_payload import bound_audit_payload
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
                    tool_output=bound_audit_payload(
                        output if isinstance(output, (dict, list)) else {"raw": str(output)}
                    ),
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
    async def generate_from_ddl(
        content: str,
        data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            await MetadataGeneratorService._save_trace_log(
                trace_id,
                1,
                "start",
                {
                    "input_length": len(content),
                    "preview": content[:200],
                    "data_source": data_source or "default_clickhouse",
                },
            )

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
            # metadata-specialist 智能体默认处于禁用状态（见 DEFAULT_METADATA_SYSTEM_PROMPT
            # 的说明），因此这里的兜底分支是常态路径而非异常兜底，模板必须保持完整。
            if not agent_config or not agent_config.system_prompt:
                system_prompt_template = DEFAULT_METADATA_SYSTEM_PROMPT
            else:
                system_prompt_template = agent_config.system_prompt
                # Ensure format_instructions is present if not already in DB prompt
                if "{format_instructions}" not in system_prompt_template:
                    system_prompt_template += "\n\n{format_instructions}"

            from app.services.sql_query_execution_service import dialect_from_data_source

            sql_dialect = dialect_from_data_source(data_source)
            dialect_instruction = (
                "PostgreSQL 标准 SQL（日期转换使用 CAST、DATE_TRUNC、EXTRACT，禁止使用 toDate、"
                "parseDateTimeBestEffort、toDateTime、toYYYYMM 等 ClickHouse 函数）"
                if sql_dialect == "postgres"
                else f"{sql_dialect} 标准 SQL（禁止使用其他数据库的专有函数）"
            )
            # 将方言约束放入系统提示，避免数据库专属提示词覆盖用户消息中的兼容要求。
            system_prompt_template = (
                f"{system_prompt_template.rstrip()}\n\n"
                f"【业务指标 SQL 方言要求】calculation_logic 必须使用{dialect_instruction}。"
            )
            logger.info(
                "开始从 DDL 生成元数据: data_source=%s, sql_dialect=%s",
                data_source or "default_clickhouse",
                sql_dialect,
            )

            logger.info(f"Generating metadata for content (first 100 chars): {content[:100]}...")
            
            
            # 3. 调用 LLM
            start_llm = time.time()
            result = await MetadataGeneratorService._invoke_json(
                llm,
                ImportResult,
                system_prompt_template,
                f"请分析以下内容并生成元数据。业务指标 calculation_logic 必须使用{dialect_instruction}。\n\n{content}",
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
    async def recommend_metrics(
        dataset_id: int,
        schema_context: str,
        user_prompt: Optional[str] = None,
        existing_metrics: Optional[List[Any]] = None,
        data_source: Optional[str] = None,
        progress_callback: ProgressCallback = None,
    ) -> Dict[str, Any]:
        """
        根据数据集 Schema 推荐业务指标，支持用户自定义需求与10分钟内防重复推荐
        """
        import uuid
        import time
        from app.core.redis import get_redis
        from app.services.config_service import ConfigService
        from app.services.ai.agent_manager import AgentManagerService
        from app.schemas.agent import ChatConfig
        
        trace_id = f"metric-rec-{str(uuid.uuid4())}"
        start_total = time.time()
        
        try:
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="initializing",
                message="正在初始化业务指标推荐任务",
                percent=5,
                trace_id=trace_id,
                completed_units=0,
                total_units=5,
                remaining_units=5,
                unit_label="阶段",
            )
            # 1. Log Start
            await MetadataGeneratorService._save_trace_log(
                trace_id, 1, "start_recommendation",
                {"dataset_id": dataset_id, "schema_len": len(schema_context), "has_user_prompt": bool(user_prompt)}
            )

            # 2. 读取 10 分钟内近期已推荐指标 (Redis) 和数据库已有指标
            redis_client = await get_redis()
            recent_key = f"metadata:metric_rec:recent:{dataset_id}"
            recent_names: set[str] = set()
            if redis_client:
                try:
                    cached_items = await redis_client.smembers(recent_key)
                    if cached_items:
                        for item in cached_items:
                            val = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                            if val.strip():
                                recent_names.add(val.strip())
                except Exception as ex:
                    logger.warning(f"Failed to read recent recommended metrics from Redis: {ex}")

            db_existing_names: set[str] = set()
            if existing_metrics:
                for m in existing_metrics:
                    name = getattr(m, "name", None) or (m.get("name") if isinstance(m, dict) else "")
                    display_name = getattr(m, "display_name", None) or (m.get("display_name") if isinstance(m, dict) else "")
                    if name and str(name).strip():
                        db_existing_names.add(str(name).strip())
                    if display_name and str(display_name).strip():
                        db_existing_names.add(str(display_name).strip())

            all_excluded_names = db_existing_names.union(recent_names)
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="deduplicating",
                message=f"已加载 {len(all_excluded_names)} 个已有或近期指标用于去重",
                percent=20,
                trace_id=trace_id,
                completed_units=1,
                total_units=5,
                remaining_units=4,
                unit_label="阶段",
            )

            # 3. Get Agent Config
            async with AsyncSessionLocal() as session:
                agent_config = await AgentManagerService.get_active_agent_config(
                    session, agent_name='metadata-specialist'
                )
            
            chat_config = agent_config
            if not chat_config:
                 logger.warning("Metadata Specialist config not found, using default.")

            # Get configured LLM
            llm = await AgentConfigProvider.get_configured_llm(streaming=False, config=chat_config)
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="model_ready",
                message="AI 模型已就绪，正在准备指标生成提示词",
                percent=35,
                trace_id=trace_id,
                completed_units=2,
                total_units=5,
                remaining_units=3,
                unit_label="阶段",
            )

            # 4. 根据数据集绑定的数据源生成方言约束，避免 PostgreSQL 误用 ClickHouse 函数。
            from app.services.sql_query_execution_service import dialect_from_data_source

            sql_dialect = dialect_from_data_source(data_source)
            dialect_instructions = {
                "postgres": (
                    "目标数据源为 PostgreSQL。calculation_logic 必须使用 PostgreSQL 标准 SQL；"
                    "日期转换使用 CAST(field AS DATE/TIMESTAMP)、DATE_TRUNC、EXTRACT，"
                    "禁止使用 ClickHouse 的 toDate、parseDateTimeBestEffort、toDateTime、toYYYYMM 等函数。"
                ),
                "mysql": (
                    "目标数据源为 MySQL。calculation_logic 必须使用 MySQL 标准 SQL；"
                    "日期转换使用 DATE(field)、DATE_FORMAT 等函数，禁止使用 ClickHouse 专有函数。"
                ),
                "oracle": (
                    "目标数据源为 Oracle。calculation_logic 必须使用 Oracle 标准 SQL；"
                    "日期转换使用 TRUNC、TO_DATE、TO_CHAR 等函数，禁止使用 ClickHouse 专有函数。"
                ),
                "tsql": (
                    "目标数据源为 SQL Server。calculation_logic 必须使用 SQL Server 标准 SQL；"
                    "日期转换使用 CAST/CONVERT、DATEPART 等函数，禁止使用 ClickHouse 专有函数。"
                ),
            }
            dialect_instruction = dialect_instructions.get(
                sql_dialect,
                "目标数据源为 ClickHouse。calculation_logic 可以使用 ClickHouse 标准 SQL 日期函数。",
            )
            logger.info(
                "开始推荐业务指标: dataset_id=%s, data_source=%s, sql_dialect=%s",
                dataset_id,
                data_source or "未指定",
                sql_dialect,
            )

            # 5. 组装 Prompt
            prompt_segments = [
                "你是一个精通数据分析的 BI 专家。",
                "请分析给定的数据库 Schema（包含表结构、字段含义），推荐 5-10 个**最有业务价值**的分析指标。",
                "指标类型可以是：",
                "1. **聚合型 (KPI)**: 如总数、平均值、比率 (e.g., 'PUE均值', '机房总数')。",
                "2. **维度分布 (Dimension)**: 如按类别分组统计 (e.g., '各区域机房分布', '设备类型占比')。",
                "3. **常用视图 (Data View)**: 常用查询字段组合 (e.g., '机房详细列表: 名称, 编码, 地址')。\n",
                "对于 SQL (calculation_logic 字段)：",
                f"- {dialect_instruction}",
                "- 对于分布/视图类，请写出完整的 `SELECT ... FROM ... [GROUP BY ...]` 语句。",
                "- 禁止使用中文别名。"
            ]

            if user_prompt and user_prompt.strip():
                prompt_segments.append(
                    f"\n【用户特定业务需求与偏好】：\n{user_prompt.strip()}\n请务必重点贴合上述用户需求来设计和发掘指标。"
                )

            if all_excluded_names:
                excluded_list_str = "、".join(sorted(list(all_excluded_names))[:40])
                prompt_segments.append(
                    f"\n【去重与排除约束（严禁重复推荐）】：\n以下指标已在系统中存在或近期10分钟内刚刚推荐过，请务必不要推荐名称或业务含义与下列重复/雷同的指标：\n{excluded_list_str}\n请发掘其他未被覆盖的高价值业务分析维度或指标。"
                )

            prompt_segments.append("{format_instructions}")
            system_prompt = "\n".join(prompt_segments)
            
            # 6. Invoke LLM
            start_llm = time.time()
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="generating",
                message="AI 正在分析字段语义并生成指标与 SQL",
                percent=45,
                trace_id=trace_id,
                completed_units=2,
                total_units=5,
                remaining_units=3,
                unit_label="阶段",
            )
            result = await MetadataGeneratorService._invoke_json(
                llm,
                MetricRecommendationResult,
                system_prompt,
                f"Schema 定义如下：\n\n{schema_context}",
            )
            duration_llm = (time.time() - start_llm) * 1000

            # 7. 后置去重过滤与写入 Redis 10分钟缓存
            raw_metrics = result.get("metrics", []) if isinstance(result, dict) else (getattr(result, "metrics", []) if hasattr(result, "metrics") else [])
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="validating",
                message=f"模型已返回 {len(raw_metrics)} 个候选指标，正在校验并去重",
                percent=85,
                trace_id=trace_id,
                completed_units=4,
                total_units=5,
                remaining_units=1,
                unit_label="阶段",
                result_count=len(raw_metrics),
            )
            filtered_metrics = []
            new_metric_names = []

            for m in raw_metrics:
                m_dict = m if isinstance(m, dict) else (m.model_dump() if hasattr(m, "model_dump") else dict(m))
                name = (m_dict.get("name") or "").strip()
                display_name = (m_dict.get("display_name") or "").strip()

                # 后置过滤与已有名称完全冲突的项
                if name in all_excluded_names or display_name in all_excluded_names:
                    logger.info(f"Filtered out duplicate recommended metric: {name} / {display_name}")
                    continue

                filtered_metrics.append(m_dict)
                if name:
                    new_metric_names.append(name)
                if display_name:
                    new_metric_names.append(display_name)

            # 更新结果列表（若全被过滤则保留原始结果，避免返回空）
            if isinstance(result, dict):
                result["metrics"] = filtered_metrics if filtered_metrics else raw_metrics
                result["_trace_id"] = trace_id

            # 写入 Redis 缓存，TTL 600 秒
            if redis_client and new_metric_names:
                try:
                    await redis_client.sadd(recent_key, *new_metric_names)
                    await redis_client.expire(recent_key, 600)
                except Exception as ex:
                    logger.warning(f"Failed to save recommended metrics to Redis: {ex}")
            
            # 8. Log Success
            await MetadataGeneratorService._save_trace_log(trace_id, 4, "llm_success", result, execution_time=duration_llm)
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="completed",
                message=f"业务指标推荐完成，共生成 {len(result.get('metrics', []))} 个指标",
                percent=100,
                trace_id=trace_id,
                completed_units=5,
                total_units=5,
                remaining_units=0,
                unit_label="阶段",
                result_count=len(result.get("metrics", [])),
            )
                
            return result

        except Exception as e:
            logger.error(f"Error recommending metrics: {e}", exc_info=True)
            await MetadataGeneratorService._save_trace_log(trace_id, 5, "error", {"error": str(e)})
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"指标推荐失败: {str(e)}")

    @staticmethod
    async def recommend_relationships(
        dataset_id: int,
        schema_context: str,
        user_prompt: Optional[str] = None,
        existing_relationships: Optional[List[str]] = None,
        data_source: Optional[str] = None,
        strategy: str = "strict",
        progress_callback: ProgressCallback = None,
    ) -> Dict[str, Any]:
        """
        根据数据集 Schema 智能推荐实体（表）之间的关联关系。
        支持严格/智能推断策略、10 分钟内 Redis 历史去重、DB 已有关系排除以及用户自定义 Prompt 偏好注入。
        仅输出建议，不自动入库，供用户人工判断确认。
        """
        import uuid
        import time
        import json
        from app.core.redis import get_redis
        from app.services.ai.agent_manager import AgentManagerService

        strategy = strategy if strategy in {"strict", "smart"} else "strict"
        smart_candidate_pair_limit = 120
        trace_id = f"rel-rec-{str(uuid.uuid4())}"
        await MetadataGeneratorService._emit_progress(
            progress_callback,
            phase="initializing",
            message="正在初始化实体关系推荐任务",
            percent=3,
            trace_id=trace_id,
            completed_units=0,
            total_units=0,
            remaining_units=0,
            unit_label="候选组",
            batch_count=0,
            result_count=0,
        )

        # 1. 查询近 10 分钟已推荐的关联关系缓存（避免短期内频繁推荐相同关联）
        recent_recommended_keys = set()
        recent_recommended_items = []
        redis_client = None
        redis_key = f"metadata:rel_rec:recent:{dataset_id}"
        try:
            redis_client = await get_redis()
            cached_data = await redis_client.get(redis_key)
            if cached_data:
                recent_list = json.loads(cached_data)
                for item in recent_list:
                    src = item.get("source_table", "").strip().lower()
                    tgt = item.get("target_table", "").strip().lower()
                    cond = item.get("condition", "").strip().lower()
                    if src and tgt:
                        pair_key = tuple(sorted([src, tgt])) + (cond,)
                        recent_recommended_keys.add(pair_key)
                        recent_recommended_items.append(item)
        except Exception as e:
            logger.warning(f"Failed to read recent relationship recommendations from Redis: {e}")

        await MetadataGeneratorService._emit_progress(
            progress_callback,
            phase="deduplicating",
            message=f"已加载 {len(recent_recommended_keys)} 条近期关系用于去重",
            percent=7,
            trace_id=trace_id,
            completed_units=0,
            total_units=0,
            remaining_units=0,
            unit_label="候选组",
            batch_count=0,
            result_count=0,
        )

        # 整理负向排除清单（既有关系 + 近期推荐关系）
        exclude_descriptions = []
        existing_relationship_keys = set()
        if existing_relationships:
            for r in existing_relationships:
                exclude_descriptions.append(f"- 已存在关系: {r}")
                existing_match = re.match(
                    r"^\s*(.+?)\s*<->\s*(.+?)\s*\((.*)\)\s*$",
                    str(r),
                )
                if existing_match:
                    source_name = existing_match.group(1).strip().lower()
                    target_name = existing_match.group(2).strip().lower()
                    condition = existing_match.group(3).strip().lower()
                    existing_relationship_keys.add(
                        tuple(sorted((source_name, target_name))) + (condition,)
                    )
        for item in recent_recommended_items[:20]:
            exclude_descriptions.append(
                f"- 近期已推荐关系: {item.get('source_table')} -> "
                f"{item.get('target_table')} ({item.get('condition')})"
            )

        try:
            # 1. Log Start
            await MetadataGeneratorService._save_trace_log(
                trace_id, 1, "start_recommendation",
                {
                    "dataset_id": dataset_id,
                    "schema_len": len(schema_context),
                    "user_prompt": user_prompt,
                    "recent_cached_count": len(recent_recommended_keys),
                    "existing_rels_count": len(existing_relationships or [])
                },
            )

            # 2. 构建紧凑 Schema 和候选表对；候选范围由后端确定，不再依赖模型翻页。
            schema_table_names, relationship_table_index = (
                MetadataRelationshipCandidateService.parse_schema(schema_context)
            )
            focused_table_names = MetadataRelationshipCandidateService.parse_focused_table_names(
                user_prompt,
                schema_table_names,
            )
            candidate_build = MetadataRelationshipCandidateService.build_candidate_pairs_with_stats(
                schema_table_names,
                relationship_table_index,
                focused_table_names=focused_table_names,
                strategy=strategy,
                max_candidate_pairs=smart_candidate_pair_limit,
            )
            candidate_pairs = list(candidate_build.pairs)
            possible_pair_count = (
                len(schema_table_names) * (len(schema_table_names) - 1) // 2
            )
            logger.warning(
                "关系推荐候选准备完成: trace_id=%s, dataset_id=%s, schema_len=%s, "
                "table_count=%s, possible_pairs=%s, candidate_pairs=%s, "
                "custom_prompt=%s",
                trace_id,
                dataset_id,
                len(schema_context),
                len(schema_table_names),
                possible_pair_count,
                len(candidate_pairs),
                bool(user_prompt and user_prompt.strip()),
            )
            logger.info(
                "关系推荐策略完成: trace_id=%s, dataset_id=%s, strategy=%s, "
                "smart_candidate_pairs=%s, truncated_pairs=%s, limit=%s",
                trace_id,
                dataset_id,
                strategy,
                candidate_build.smart_candidate_pair_count,
                candidate_build.truncated_pair_count,
                smart_candidate_pair_limit if strategy == "smart" else None,
            )

            # 2.1 去重前置：既有关系与近期推荐命中的表对在探测前剔除，
            # 避免数据库探测和 AI 兜底重复处理已经确认过的关系。
            existing_condition_pair_keys = set()
            for src_name, tgt_name, condition in list(existing_relationship_keys) + list(recent_recommended_keys):
                condition_pairs = MetadataGeneratorService._extract_condition_pair_keys(condition)
                if condition_pairs:
                    existing_condition_pair_keys.update(condition_pairs)
                else:
                    existing_condition_pair_keys.add(
                        tuple(sorted((src_name, tgt_name)))
                    )
            deduped_candidate_pairs = []
            excluded_pair_count = 0
            for pair in candidate_pairs:
                if pair.unordered_key in existing_condition_pair_keys:
                    excluded_pair_count += 1
                    continue
                deduped_candidate_pairs.append(pair)
            if excluded_pair_count:
                logger.warning(
                    "关系推荐候选去重前置完成: trace_id=%s, dataset_id=%s, "
                    "before=%s, excluded=%s, remaining=%s",
                    trace_id,
                    dataset_id,
                    len(candidate_pairs),
                    excluded_pair_count,
                    len(deduped_candidate_pairs),
                )
            candidate_pairs = deduped_candidate_pairs

            # 3. 数据库元数据探测优先；禁止读取业务行，未确认候选交给模型兜底。
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="database_probe",
                message="正在读取数据库外键约束",
                percent=12,
                trace_id=trace_id,
                completed_units=0,
                total_units=len(candidate_pairs),
                remaining_units=len(candidate_pairs),
                unit_label="候选表对",
                batch_count=0,
                result_count=0,
                candidate_pair_count=len(candidate_pairs),
                completed_pair_count=0,
                remaining_pair_count=len(candidate_pairs),
            )
            foreign_keys, probe_unavailable_reason = (
                await MetadataRelationshipProbeService.load_foreign_keys(data_source)
            )
            probe_relationships = MetadataRelationshipProbeService.find_fk_relationships(
                foreign_keys,
                candidate_pairs,
            )
            fk_pair_keys = {
                tuple(sorted((
                    (
                        getattr(relationship, "left_table", None)
                        or relationship.get("left_table", "")
                    ).lower(),
                    (
                        getattr(relationship, "right_table", None)
                        or relationship.get("right_table", "")
                    ).lower(),
                )))
                for relationship in probe_relationships
            }
            unprobed_pairs = [
                pair for pair in candidate_pairs
                if pair.unordered_key not in fk_pair_keys
            ]
            sampled_relationships = []
            probe_stats = {
                "probed_pair_count": 0,
                "confirmed_pair_count": len(probe_relationships),
                "rejected_pair_count": 0,
                "rejected_reasons": {},
                "unverified_pair_count": 0,
                "probe_duration_ms": 0,
                "probe_unavailable_reason": (
                    probe_unavailable_reason or "business_row_sampling_disabled"
                ),
            }
            logger.info(
                "关系推荐跳过业务行抽样，未确认候选交给模型: trace_id=%s, "
                "dataset_id=%s, unprobed_pair_count=%s",
                trace_id,
                dataset_id,
                len(unprobed_pairs),
            )

            collected_keys = set(recent_recommended_keys).union(
                existing_relationship_keys
            )
            confirmed_relationships = []
            confirmed_duplicate_count = 0
            for item in probe_relationships + sampled_relationships:
                # 兼容数据类与测试注入的字典形态，字段语义一致。
                left_table = getattr(item, "left_table", None)
                right_table = getattr(item, "right_table", None)
                left_column = getattr(item, "left_column", None)
                right_column = getattr(item, "right_column", None)
                if left_table is None:
                    left_table = item.get("left_table")
                    right_table = item.get("right_table")
                    left_column = item.get("left_column")
                    right_column = item.get("right_column")
                column_pairs = getattr(item, "column_pairs", None)
                if column_pairs is None and isinstance(item, dict):
                    column_pairs = item.get("column_pairs")
                if not column_pairs and left_column and right_column:
                    column_pairs = ((left_column, right_column),)
                column_pairs = tuple(
                    (str(left), str(right))
                    for left, right in (column_pairs or ())
                    if left and right
                )
                if not column_pairs:
                    continue
                relationship = {
                    "source_table": left_table,
                    "target_table": right_table,
                    "condition": " AND ".join(
                        f"{left_table}.{left} = {right_table}.{right}"
                        for left, right in column_pairs
                    ),
                    "relation_type": getattr(item, "relation_type", None)
                    or (item.get("relation_type") if isinstance(item, dict) else None),
                    "description": getattr(item, "reason", None)
                    or (item.get("reason") if isinstance(item, dict) else None),
                    "confidence": getattr(item, "confidence", None)
                    or (item.get("confidence") if isinstance(item, dict) else None),
                    "source": getattr(item, "source", None)
                    or (item.get("source", "FK") if isinstance(item, dict) else "FK"),
                }
                # 探测结论同样执行去重键校验，避免外键与近期缓存重复输出。
                relationship_key = (
                    MetadataGeneratorService._relationship_pair_key(relationship)
                )
                if relationship_key and relationship_key in collected_keys:
                    confirmed_duplicate_count += 1
                    continue
                confirmed_relationships.append(relationship)
            if confirmed_duplicate_count:
                logger.warning(
                    "关系探测结果去重完成: trace_id=%s, dataset_id=%s, "
                    "excluded=%s, kept=%s",
                    trace_id,
                    dataset_id,
                    confirmed_duplicate_count,
                    len(confirmed_relationships),
                )

            confirmed_pair_keys = set(fk_pair_keys)
            for item in sampled_relationships:
                # 与上面的确认关系转换保持一致，兼容数据类与字典形态。
                left_table = getattr(item, "left_table", None)
                right_table = getattr(item, "right_table", None)
                if left_table is None:
                    left_table = item.get("left_table")
                    right_table = item.get("right_table")
                confirmed_pair_keys.add(tuple(sorted((
                    left_table.lower(),
                    right_table.lower(),
                ))))
            fallback_pairs = [
                pair for pair in candidate_pairs
                if pair.unordered_key not in confirmed_pair_keys
            ]
            candidate_groups = MetadataRelationshipCandidateService.group_candidate_pairs(
                fallback_pairs,
                max_pairs_per_group=8,
                max_tables_per_group=12,
            )
            total_groups = len(candidate_groups)

            confirmed_pair_summaries = [
                {
                    "pair_id": index + 1,
                    "source_table": rel["source_table"],
                    "target_table": rel["target_table"],
                    "condition": rel["condition"],
                    "probe_reason": rel["description"],
                }
                for index, rel in enumerate(confirmed_relationships)
            ]
            llm = None
            needs_llm = bool(candidate_groups or confirmed_pair_summaries)
            if needs_llm:
                async with AsyncSessionLocal() as session:
                    agent_config = await AgentManagerService.get_active_agent_config(
                        session,
                        agent_name="metadata-specialist",
                    )
                if not agent_config:
                    logger.warning("元数据专家配置不存在，关系推荐使用默认模型配置")
                llm = await AgentConfigProvider.get_configured_llm(
                    streaming=False,
                    config=agent_config,
                )
                logger.warning(
                    "关系推荐模型初始化完成: trace_id=%s, dataset_id=%s, model=%s",
                    trace_id,
                    dataset_id,
                    getattr(llm, "model", "unknown"),
                )
            else:
                logger.info(
                    "关系推荐无候选组，跳过模型初始化: trace_id=%s, dataset_id=%s",
                    trace_id,
                    dataset_id,
                )

            # 3.1 已确认关系只差业务描述，合并为单次模型调用以降低总耗时。
            confirmed_description_updated = 0
            if confirmed_pair_summaries:
                description_started_at = time.time()
                try:
                    description_prompt = (
                        "以下为数据库探测已确认的表关系，请逐条生成业务含义描述。\n\n"
                        f"{json.dumps(confirmed_pair_summaries, ensure_ascii=False)}"
                    )
                    description_result = await MetadataGeneratorService._invoke_json(
                        llm,
                        RelationshipDescriptionResult,
                        RELATIONSHIP_DESCRIPTION_SYSTEM_PROMPT,
                        description_prompt,
                    )
                    descriptions_by_id = {
                        item.get("pair_id"): str(item.get("description") or "").strip()
                        for item in description_result.get("descriptions", [])
                        if isinstance(item, dict)
                    }
                    for index, rel in enumerate(confirmed_relationships):
                        text = descriptions_by_id.get(index + 1, "")
                        if text:
                            rel["description"] = text
                            confirmed_description_updated += 1
                    logger.warning(
                        "关系探测描述生成完成: trace_id=%s, dataset_id=%s, "
                        "pair_count=%s, updated=%s, duration_ms=%.2f",
                        trace_id,
                        dataset_id,
                        len(confirmed_pair_summaries),
                        confirmed_description_updated,
                        (time.time() - description_started_at) * 1000,
                    )
                except Exception as exc:
                    logger.warning(
                        "关系探测描述生成失败，保留探测原因描述: trace_id=%s, "
                        "dataset_id=%s, error=%s",
                        trace_id,
                        dataset_id,
                        exc,
                        exc_info=True,
                    )

            # 4. 每个候选表对只在所属分组中调用一次 AI，模型只负责最终语义判断。
            system_prompt = (
                "你是一个精通数据建模的 DBA/数据架构师。\n"
                "输入只包含后端预筛选的候选表对和相关字段，请逐对判断是否存在真实、"
                "可执行且有业务价值的等值 JOIN 关系。\n\n"
                "严格规则：\n"
                "1. 只能推荐 candidate_pairs 中明确列出的表对，不得扩展到其它表对。\n"
                "2. source_table、target_table 和 condition 必须使用输入中的物理表名和物理字段名。\n"
                "3. condition 只能使用 '物理表.字段 = 物理表.字段' 的简单等值连接。\n"
                "4. 只有具备明确字段命名、类型和业务语义依据时才输出；"
                "禁止仅因业务领域相近而臆造关系。\n"
                "5. confidence 必须客观评分，只输出 confidence >= 0.70 的关系。\n"
                "6. 必须检查本组全部候选表对；没有可靠关系的表对无需输出。\n"
                "7. 不设置最终关系总数上限，但不得输出重复关系。\n"
                "8. relation_type 只能是 one_to_one、one_to_many、many_to_one。\n"
                "9. description 使用不超过 80 个中文字符说明关系含义。\n"
            )
            if strategy == "smart":
                system_prompt += (
                    "10. 本次使用智能推断策略，候选可能没有数据库主外键约束；"
                    "只有字段语义、类型和表业务含义共同支持时才确认，不能把候选本身当作事实。\n"
                )
            if exclude_descriptions:
                system_prompt += (
                    "\n【已存在或近期已推荐关系，严禁重复】\n"
                    + "\n".join(exclude_descriptions[:40])
                    + "\n"
                )
            if user_prompt and user_prompt.strip():
                system_prompt += (
                    "\n【用户关注方向】\n"
                    f"{user_prompt.strip()}\n"
                )
            system_prompt += "\n{format_instructions}"

            start_llm = time.time()
            batch_number = 0
            successful_group_count = 0
            failed_group_count = 0
            completed_pair_count = len(confirmed_relationships)
            collected_relationships: List[Dict[str, Any]] = list(
                confirmed_relationships
            )
            new_cache_items: List[Dict[str, Any]] = []
            debug_groups: List[Dict[str, Any]] = [
                {
                    "group": 0,
                    "pair_count": len(confirmed_relationships),
                    "status": "completed",
                    "source": "database_probe",
                }
            ]
            total_prompt_chars = 0
            total_scoped_schema_chars = 0
            last_group_error: Optional[Exception] = None

            for group_index, group in enumerate(candidate_groups, start=1):
                batch_number += 1
                group_schema = MetadataRelationshipCandidateService.render_group_schema(
                    group,
                    relationship_table_index,
                )
                group_prompt = (
                    f"这是候选关系组 {group_index}/{total_groups}。\n"
                    "请完整判断 candidate_pairs 中的每个表对，并返回本组所有可靠关系。\n\n"
                    f"{group_schema}"
                )
                total_prompt_chars += len(group_prompt)
                total_scoped_schema_chars += len(group_schema)
                compression_percent = max(
                    0,
                    int(
                        100
                        * (1 - len(group_schema) / max(len(schema_context), 1))
                    ),
                )
                progress_percent = 10 + int(
                    85 * successful_group_count / max(total_groups, 1)
                )
                await MetadataGeneratorService._emit_progress(
                    progress_callback,
                    phase="scanning",
                    message=(
                        f"正在推导候选关系组 {group_index}/{total_groups}，"
                        f"本组 {len(group.pairs)} 个表对"
                    ),
                    percent=progress_percent,
                    trace_id=trace_id,
                    completed_units=successful_group_count,
                    total_units=total_groups,
                    remaining_units=total_groups - successful_group_count,
                    unit_label="候选组",
                    current_item=f"候选组 {group_index}/{total_groups}",
                    current_page=0,
                    batch_count=batch_number,
                    result_count=len(collected_relationships),
                    candidate_pair_count=len(candidate_pairs),
                    completed_pair_count=completed_pair_count,
                    remaining_pair_count=len(candidate_pairs) - completed_pair_count,
                )
                logger.warning(
                    "关系推荐开始请求候选组: trace_id=%s, dataset_id=%s, "
                    "group=%s/%s, pair_count=%s, table_count=%s, "
                    "full_schema_len=%s, scoped_schema_len=%s, "
                    "compression_percent=%s, prompt_len=%s, collected=%s",
                    trace_id,
                    dataset_id,
                    group_index,
                    total_groups,
                    len(group.pairs),
                    len(group.table_names),
                    len(schema_context),
                    len(group_schema),
                    compression_percent,
                    len(group_prompt),
                    len(collected_relationships),
                )

                group_started_at = time.time()
                try:
                    group_result = await MetadataGeneratorService._invoke_json(
                        llm,
                        RelationshipRecommendationResult,
                        system_prompt,
                        group_prompt,
                    )
                except Exception as exc:
                    failed_group_count += 1
                    last_group_error = exc
                    debug_groups.append({
                        "group": group_index,
                        "pair_count": len(group.pairs),
                        "status": "error",
                        "error": str(exc),
                    })
                    logger.warning(
                        "关系推荐候选组失败，继续后续分组: trace_id=%s, "
                        "dataset_id=%s, group=%s/%s, pair_count=%s, error=%s",
                        trace_id,
                        dataset_id,
                        group_index,
                        total_groups,
                        len(group.pairs),
                        exc,
                        exc_info=True,
                    )
                    await MetadataGeneratorService._emit_progress(
                        progress_callback,
                        phase="group_failed",
                        message=(
                            f"候选关系组 {group_index}/{total_groups} 失败，"
                            "已记录并继续后续分组"
                        ),
                        percent=10 + int(
                            85
                            * successful_group_count
                            / max(total_groups, 1)
                        ),
                        trace_id=trace_id,
                        completed_units=successful_group_count,
                        total_units=total_groups,
                        remaining_units=total_groups - successful_group_count,
                        unit_label="候选组",
                        current_item=f"候选组 {group_index}/{total_groups}",
                        current_page=0,
                        batch_count=batch_number,
                        result_count=len(collected_relationships),
                        candidate_pair_count=len(candidate_pairs),
                        completed_pair_count=completed_pair_count,
                        remaining_pair_count=(
                            len(candidate_pairs) - completed_pair_count
                        ),
                        stop_reason="partial_group_error",
                    )
                    continue

                group_duration_ms = (time.time() - group_started_at) * 1000
                successful_group_count += 1
                completed_pair_count += len(group.pairs)
                raw_relationships = (
                    group_result.get("relationships", [])
                    if isinstance(group_result, dict)
                    else []
                )
                group_new_count = 0
                group_duplicate_count = 0
                rejection_reasons: List[str] = []
                for relationship in raw_relationships:
                    is_valid, rejection_reason = (
                        MetadataRelationshipCandidateService.validate_relationship(
                            relationship,
                            group,
                            relationship_table_index,
                        )
                    )
                    if not is_valid:
                        rejection_reasons.append(rejection_reason)
                        continue
                    relationship_key = MetadataGeneratorService._relationship_pair_key(
                        relationship
                    )
                    if relationship_key is None:
                        rejection_reasons.append("invalid_relationship_key")
                        continue
                    if relationship_key in collected_keys:
                        group_duplicate_count += 1
                        continue
                    collected_keys.add(relationship_key)
                    collected_relationships.append(relationship)
                    new_cache_items.append({
                        "source_table": relationship.get("source_table"),
                        "target_table": relationship.get("target_table"),
                        "condition": relationship.get("condition"),
                        "ts": time.time(),
                    })
                    group_new_count += 1

                rejection_counts = (
                    MetadataRelationshipCandidateService.count_rejection_reasons(
                        rejection_reasons
                    )
                )
                debug_groups.append({
                    "group": group_index,
                    "pair_count": len(group.pairs),
                    "table_count": len(group.table_names),
                    "raw_count": len(raw_relationships),
                    "new_count": group_new_count,
                    "duplicate_count": group_duplicate_count,
                    "rejection_counts": rejection_counts,
                    "scoped_schema_len": len(group_schema),
                    "compression_percent": compression_percent,
                    "duration_ms": round(group_duration_ms, 2),
                    "status": "completed",
                })
                logger.warning(
                    "关系推荐候选组完成: trace_id=%s, dataset_id=%s, "
                    "group=%s/%s, pair_count=%s, raw_count=%s, new_count=%s, "
                    "duplicate_count=%s, rejected=%s, rejection_counts=%s, "
                    "total=%s, duration_ms=%.2f",
                    trace_id,
                    dataset_id,
                    group_index,
                    total_groups,
                    len(group.pairs),
                    len(raw_relationships),
                    group_new_count,
                    group_duplicate_count,
                    len(rejection_reasons),
                    rejection_counts,
                    len(collected_relationships),
                    group_duration_ms,
                )

                remaining_groups = total_groups - successful_group_count
                elapsed_seconds = max(time.time() - start_llm, 0.001)
                estimated_remaining_seconds = int(
                    elapsed_seconds
                    / max(batch_number, 1)
                    * remaining_groups
                )
                await MetadataGeneratorService._emit_progress(
                    progress_callback,
                    phase="group_completed",
                    message=(
                        f"候选关系组 {group_index}/{total_groups} 完成，"
                        f"新增 {group_new_count} 条，剩余 {remaining_groups} 组"
                    ),
                    percent=10 + int(
                        85 * successful_group_count / max(total_groups, 1)
                    ),
                    trace_id=trace_id,
                    completed_units=successful_group_count,
                    total_units=total_groups,
                    remaining_units=remaining_groups,
                    unit_label="候选组",
                    current_item=f"候选组 {group_index}/{total_groups}",
                    current_page=0,
                    batch_count=batch_number,
                    result_count=len(collected_relationships),
                    estimated_remaining_seconds=estimated_remaining_seconds,
                    candidate_pair_count=len(candidate_pairs),
                    completed_pair_count=completed_pair_count,
                    remaining_pair_count=len(candidate_pairs) - completed_pair_count,
                )

            if total_groups and successful_group_count == 0 and last_group_error:
                raise RuntimeError(
                    f"全部 {total_groups} 个候选关系组均推导失败: {last_group_error}"
                ) from last_group_error

            stop_reason = (
                "partial_group_error"
                if failed_group_count
                else "all_candidate_groups_scanned"
            )
            duration_llm = (time.time() - start_llm) * 1000
            result = {
                "relationships": collected_relationships,
                "_trace_id": trace_id,
                "_batch_count": batch_number,
                "_stop_reason": stop_reason,
                "_debug": {
                    "schema_len": len(schema_context),
                    "schema_table_count": len(schema_table_names),
                    "schema_table_names_preview": schema_table_names[:30],
                    "strategy": strategy,
                    "possible_pair_count": possible_pair_count,
                    "candidate_pair_count": len(candidate_pairs),
                    "filtered_pair_count": possible_pair_count - len(candidate_pairs),
                    "smart_candidate_pair_count": candidate_build.smart_candidate_pair_count,
                    "candidate_pair_limit": (
                        smart_candidate_pair_limit if strategy == "smart" else None
                    ),
                    "truncated_pair_count": candidate_build.truncated_pair_count,
                    "candidate_group_count": total_groups,
                    "fk_relationship_count": len(probe_relationships),
                    "probed_pair_count": probe_stats.get("probed_pair_count"),
                    "confirmed_pair_count": probe_stats.get("confirmed_pair_count"),
                    "rejected_reasons": probe_stats.get("rejected_reasons"),
                    "unverified_pair_count": probe_stats.get("unverified_pair_count"),
                    "confirmed_duplicate_count": confirmed_duplicate_count,
                    "confirmed_description_updated": confirmed_description_updated,
                    "probe_duration_ms": probe_stats.get("probe_duration_ms"),
                    "probe_unavailable_reason": probe_stats.get(
                        "probe_unavailable_reason"
                    ),
                    "completed_group_count": successful_group_count,
                    "failed_group_count": failed_group_count,
                    "remaining_group_count": failed_group_count,
                    "completed_pair_count": completed_pair_count,
                    "remaining_pair_count": len(candidate_pairs) - completed_pair_count,
                    "total_prompt_chars": total_prompt_chars,
                    "total_scoped_schema_chars": total_scoped_schema_chars,
                    "stop_reason": stop_reason,
                    "groups": debug_groups[-200:],
                },
            }

            if redis_client and new_cache_items:
                try:
                    await redis_client.set(
                        redis_key,
                        json.dumps(new_cache_items, ensure_ascii=False),
                        ex=600,
                    )
                except Exception as ex:
                    logger.warning(
                        "关系推荐近期结果缓存失败: dataset_id=%s, error=%s",
                        dataset_id,
                        ex,
                    )

            logger.warning(
                "关系推荐完成: trace_id=%s, dataset_id=%s, total=%s, "
                "possible_pairs=%s, candidate_pairs=%s, groups=%s, "
                "successful_groups=%s, failed_groups=%s, total_prompt_chars=%s, "
                "stop_reason=%s, execution_time_ms=%.2f",
                trace_id,
                dataset_id,
                len(collected_relationships),
                possible_pair_count,
                len(candidate_pairs),
                total_groups,
                successful_group_count,
                failed_group_count,
                total_prompt_chars,
                stop_reason,
                duration_llm,
            )
            await MetadataGeneratorService._save_trace_log(
                trace_id,
                4,
                "llm_success",
                result,
                execution_time=duration_llm,
            )
            partial_interruption = stop_reason == "partial_group_error"
            await MetadataGeneratorService._emit_progress(
                progress_callback,
                phase="interrupted" if partial_interruption else "completed",
                message=(
                    f"实体关系推荐部分中断，已保留 {len(collected_relationships)} 条关系"
                    if partial_interruption
                    else f"实体关系推荐完成，共生成 {len(collected_relationships)} 条关系"
                ),
                percent=(
                    int(95 * successful_group_count / max(total_groups, 1))
                    if partial_interruption
                    else 100
                ),
                trace_id=trace_id,
                completed_units=(
                    successful_group_count
                    if partial_interruption
                    else total_groups
                ),
                total_units=total_groups,
                remaining_units=(failed_group_count if partial_interruption else 0),
                unit_label="候选组",
                batch_count=batch_number,
                result_count=len(collected_relationships),
                estimated_remaining_seconds=(None if partial_interruption else 0),
                stop_reason=stop_reason,
                candidate_pair_count=len(candidate_pairs),
                completed_pair_count=completed_pair_count,
                remaining_pair_count=(
                    len(candidate_pairs) - completed_pair_count
                    if partial_interruption
                    else 0
                ),
            )

            return result

        except Exception as e:
            logger.error(f"Error recommending relationships: {e}", exc_info=True)
            await MetadataGeneratorService._save_trace_log(trace_id, 5, "error", {"error": str(e)})
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"关系推荐失败: {str(e)}")

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

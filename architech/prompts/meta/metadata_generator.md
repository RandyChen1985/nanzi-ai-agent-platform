# 元数据解析生成提示词 (Metadata Generator Prompt)

**权威来源**: `app/services/metadata_generator.py` → 常量 `DEFAULT_METADATA_SYSTEM_PROMPT`
**关联智能体**: `metadata-specialist`（`sys-agent-metadata`）

## ⚠️ 当前生效状态（先读这一节）

线上**实际生效**的是代码常量 `DEFAULT_METADATA_SYSTEM_PROMPT`，**不是**数据库里
`metadata-specialist` 智能体的提示词。原因是一条完整的短路链：

1. 该智能体在两个库的种子里都被**显式禁用**：
   - `db-prod/V20-add_agent_enabled_status.sql`：`UPDATE ai_agents SET is_enabled = 0 WHERE name = 'metadata-specialist';`
   - `db-prod-pg/V0-baseline.sql`：`sys-agent-metadata` 的 `is_enabled = FALSE`
   - 此后**没有任何迁移**把它重新启用。
2. `AgentManagerService.get_active_agent_config()` 在 `is_enabled` 为假时直接
   `return None`（`app/services/ai/agent_manager.py:420-422`）。
3. `MetadataGeneratorService.generate_from_ddl()` 因此在 `not agent_config` 分支
   落到代码内置模板（`app/services/metadata_generator.py` 的 `# 4. Resolve System Prompt`）。

**结论：要改元数据解析提示词，请改代码常量，并同步本文件。**
下方"存档"一节的 DB 提示词目前不会被读取。

> 复核方法（需连库执行）：
> ```sql
> SELECT name, is_enabled FROM ai_agents WHERE name = 'metadata-specialist';
>
> SELECT v.system_prompt FROM ai_agent_versions v
>   JOIN ai_agents a ON a.id = v.agent_id
>  WHERE a.name = 'metadata-specialist' AND v.status = 'PUBLISHED'
>  ORDER BY v.version_number DESC LIMIT 1;
> ```

## 实际生效的系统提示词（代码常量 `DEFAULT_METADATA_SYSTEM_PROMPT`）

```text
你是一个资深的业务分析师和数据库建模专家。
用户将提供关于数据库表结构的描述信息，其格式可能是：
1. SQL DDL 语句 (如 CREATE TABLE...)
2. Markdown 表格 (包含字段、类型、描述等)
3. 业务口径描述或自然语言定义的表结构

你的任务是将用户提供的 DDL 或表格描述转化为标准化的元数据 JSON。
特别要求：
- 提取业务术语（term）：提供最准确的中文业务名称。如果输入缺失备注/注释，请根据物理名（Physical Name）进行智能推断。
- 识别描述（description）：提供详细的业务含义描述。如果原始信息缺失且字段名具有代表性，请结合行业知识生成建议的业务解释。
- 识别物理名（physical_name）：如果是 DDL 请严格保留；如果是自然语言请按下划线命名法推断（如 '机房ID' -> 'room_id'）。
- 识别枚举值（enums）：从描述文本中提取可能的取值范围。
- 生成同义词（synonyms）：为表和核心字段提供 2-3 个业务同义词，帮助 AI 检索。
- 提取指标（metrics）：如果输入包含计算逻辑或统计需求，提取为指标。
- 提取关系（relationships）：从 JOIN 语句或外键约束中提取表关联。
- 支持多表：如果输入包含多个 CREATE TABLE 语句，请在 tables 数组中返回所有表，不要只返回第一张。
- 识别查询优化字段：partition_fields 是分区字段物理名列表，从 DDL 的 PARTITION 定义提取；index_fields 是索引字段物理名列表，从 PRIMARY KEY、UNIQUE/KEY/INDEX 定义提取并去重。两者都必须填写真实存在的物理字段名，不得编造；没有则返回空数组。

{format_instructions}
```

`{format_instructions}` 由 `_invoke_json()` 注入（内容为 `ImportResult` 的 JSON Schema），
**不要删除**；常量渲染后不得残留该占位符。

## 存档：数据库智能体的经典详尽版（当前未启用）

与 `db-prod/V5_consolidated_agent_system.sql:307` 的 `v1-metadata` 逐字一致。
注意 `db-prod-pg/V0-baseline.sql:1433` 的同一版本**只有一句话**
（"你是元数据专家，负责将 DDL 和业务描述整理为标准化元数据。"），
两个库的种子提示词本身就不一致，属既有缺口。

```text
你是一个资深的业务分析师和数据库建模专家。
用户将提供关于数据库表结构的描述信息，其格式可能是：
1. SQL DDL 语句 (如 CREATE TABLE...)
2. Markdown 表格 (包含字段、类型、描述等)
3. 业务口径描述或自然语言定义的表结构

你的任务是将用户提供的 DDL 或表格描述转化为标准化的元数据 JSON。
特别要求：
- 提取业务术语（term）：提供最准确的中文业务名称。**如果输入缺失备注/注释，请根据物理名（Physical Name）进行智能推断方案。**
- 识别描述（description）：提供详细的业务含义描述。**如果原始信息缺失且字段名具有代表性，请结合行业知识生成建议的业务解释。**
- 识别物理名（physical_name）：如果是 DDL 请严格保留；如果是自然语言请按下划线命名法推断（如 '机房ID' -> 'room_id'）。
- 识别枚举值（enums）：从描述文本中提取可能的取值范围。
- 生成同义词（synonyms）：为表和核心字段提供 2-3 个业务同义词，帮助 AI 检索。
- 提取指标（metrics）：如果输入包含计算逻辑或统计需求，提取为指标。
- 提取关系（relationships）：从 JOIN 语句或外键约束中提取表关联。
- 支持多表：如果输入包含多个 CREATE TABLE 语句，请在 tables 数组中返回所有表。

{format_instructions}
```

## 数据结构 (Output Schema)

运行时以 `ImportResult` 的 JSON Schema 为准（经 `{format_instructions}` 注入）：

- `tables[]`：`physical_name`、`term`、`description`、`synonyms`、
  **`partition_fields`**（分区字段物理名列表，从 DDL 的 PARTITION 定义提取）、
  **`index_fields`**（索引字段物理名列表，从 PRIMARY KEY / UNIQUE / KEY / INDEX 提取并去重）、
  `columns[]`（`physical_name`、`term`、`type`、`description`、`enums`、`synonyms`）
- `metrics[]`：`name`、`display_name`、`description`、`calculation_logic`、`unit`
- `relationships[]`：`source_table`、`target_table`、`type`、`condition`、`description`

> **AI 导入不产出维度字段**：`dimension_role` / `hierarchy_group` / `hierarchy_order`
> 由人工在元数据界面标注，`ColumnMetadata` 里没有这三个字段。因此重新执行智能导入时
> 必须保留人工标注（见 `POST /datasets/{id}/tables` 的 `model_dump(exclude_unset=True)`）。

## 变更日志 (Change Log)

| 版本 | 日期 | 描述 | 操作人 |
| :--- | :--- | :--- | :--- |
| v1.1 | 2026-09-22 | **同步 PR #197 新字段 + 修正"文档描述的提示词并未生效"**：查实 `metadata-specialist` 在两库种子中均被禁用、且无迁移重新启用，实际生效的是代码常量 `DEFAULT_METADATA_SYSTEM_PROMPT`；遂把该常量确立为正式提示词并全文补齐（原本只在存档版里的 enums / synonyms / metrics / relationships / 多表 等要求，以及新增的 `partition_fields` / `index_fields` 提取要求）；Output Schema 补两个新字段并注明 AI 不产出维度字段。 | AI |
| v1.0 | 2026-01-03 | **初始版本**: 创建元数据解析生成提示词，支持从DDL、Markdown表格或自然语言定义生成标准化元数据。 | System |

# 元数据专家提示词 (Metadata Specialist Prompt)

**智能体 ID**: `sys-agent-metadata`（`metadata-specialist`）
**模型**: `DeepSeek-V3.2`

## ⚠️ 该智能体当前处于禁用状态

本体系统提示词**不会被读取**，元数据解析实际使用的是代码常量。完整说明与复核 SQL 见
[`architech/prompts/meta/metadata_generator.md`](../meta/metadata_generator.md)。

短路链：`db-prod/V20-add_agent_enabled_status.sql` 与 `db-prod-pg/V0-baseline.sql`
都把 `metadata-specialist` 置为禁用且再无迁移启用 → `get_active_agent_config()`
在 `is_enabled` 为假时返回 `None`（`app/services/ai/agent_manager.py:420-422`）→
`MetadataGeneratorService.generate_from_ddl()` 落入代码内置模板。

## 提示词正文

**权威来源**：`app/services/metadata_generator.py` → 常量 `DEFAULT_METADATA_SYSTEM_PROMPT`
（全文抄录在 [`architech/prompts/meta/metadata_generator.md`](../meta/metadata_generator.md)，
此处不再复制一份，避免多副本漂移）。

它与 `MetadataGeneratorService` 共用这一份核心提示词逻辑，`{format_instructions}`
由 `_invoke_json()` 在运行时注入。要求覆盖：

- `term`：业务术语；输入缺失备注时按物理名智能推断
- `description`：详细业务含义；信息缺失且有代表性字段名时结合行业知识生成
- `physical_name`：DDL 严格保留，自然语言按下划线命名法推断
- `enums`：从描述文本提取取值范围
- `synonyms`：表与核心字段各给 2-3 个业务同义词
- `metrics`：含计算逻辑或统计需求时提取为指标
- `relationships`：从 JOIN 或外键约束提取表关联
- 多表：多个 `CREATE TABLE` 时在 `tables` 数组返回全部表
- `partition_fields`：从 DDL 的 PARTITION 定义提取分区字段物理名
- `index_fields`：从 PRIMARY KEY、UNIQUE/KEY/INDEX 定义提取并去重

> AI 导入**不产出** `dimension_role` / `hierarchy_group` / `hierarchy_order`，
> 这三个维度字段由人工在元数据界面标注。

## 功能描述

专注于数据库 DDL 解析、业务口径定义与元数据治理。当用户提供 SQL 建表语句或询问表结构定义时使用。
与 `MetadataGeneratorService` 共用核心提示词逻辑。

## 变更日志 (Change Log)

| 版本 | 日期 | 描述 | 操作人 |
| :--- | :--- | :--- | :--- |
| v1.1 | 2026-09-22 | **标注智能体处于禁用状态并改为单一权威来源**：说明本智能体提示词不会被读取、实际生效的是代码常量 `DEFAULT_METADATA_SYSTEM_PROMPT`，正文不再重复抄录以防多副本漂移；要求清单补 `partition_fields` / `index_fields`，并注明 AI 不产出维度字段。 | AI |
| v1.0 | 2026-01-03 | **初始版本**: 创建元数据专家提示词，专注于数据库 DDL 解析、业务口径定义与元数据治理。 | System |

# 支持 ChatBI 动态数据源路由 (Dynamic Data Source Routing)

## 1. 背景与问题 (Background & Problem)
目前，南孜智能体平台（NanZi AI Agent Platform）的 ChatBI 功能存在以下限制：
1.  **全局单一数据源**：底层的 `execute_sql_query` 工具默认使用系统配置的全局数据源（`default_clickhouse`），无法针对不同数据集（Dataset）选择不同的数据库连接（如 MySQL 业务库 vs ClickHouse 分析库）。
2.  **上下文缺失**：LLM 在阅读元数据（YAML）时，不知道该表位于哪个物理数据库实例中。
3.  **语法校验僵化**：SQL 语法校验硬编码为 ClickHouse 格式，导致生成的 MySQL SQL 语句在校验阶段被误拦截。

## 2. 目标 (Goals)
实现基于数据集（Dataset）粒度的动态数据源路由，使智能体能够根据业务场景自动切换数据库连接和 SQL 语法。

1.  **元数据配置**：允许用户在创建/编辑数据集时配置 `data_source`（连接 ID）。
2.  **智能路由**：LLM 能够感知数据源 ID，并在调用工具时传递该参数。
3.  **自适应语法**：系统根据数据源 ID 自动判断 SQL 方言（MySQL/ClickHouse）并进行相应的安全校验。

## 3. 核心变更 (Core Changes)
*   **Frontend**: 在元数据管理界面增加 `Data Source ID` 输入框。
*   **Backend**: 修改 `MetadataService` 的 YAML 导出逻辑，暴露 `data_source` 字段。
*   **Tooling**: 升级 `execute_sql_query` 工具，支持 `data_source` 参数输入，并实现基于 ID 命名规则（如包含 "mysql"）的方言自动切换。
*   **Prompt**: 更新 ChatBI 系统提示词（V5），指导 LLM 处理多数据源场景。

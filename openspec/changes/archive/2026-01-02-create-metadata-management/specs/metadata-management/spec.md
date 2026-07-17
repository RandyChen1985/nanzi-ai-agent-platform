## ADDED Requirements

### Requirement: 元数据存储 (Metadata Storage)
系统 MUST 将语义元数据持久化存储在关系型数据库中，以增强 Text-to-SQL 能力。

#### Scenario: 存储表定义
- **WHEN** 管理员创建一个指向物理表 "res_room" 的新表定义 "机房表"
- **THEN** 该定义被保存到 `meta_tables` 表中，字段 `term` 为 "机房表"，`physical_name` 为 "res_room"。

#### Scenario: 配置数据源
- **WHEN** 管理员创建数据集 "南孜资源"
- **THEN** 系统默认将其 `data_source` 设为 "clickhouse"，但也允许指定为 "mysql" 或其他支持的源。这确保了后端执行查询时能路由到正确的数据库连接。

### Requirement: 元数据管理接口 (Metadata CRUD API)
系统 MUST 提供 RESTful API 以管理元数据。

#### Scenario: 列出数据集
- **WHEN** 用户请求 `GET /api/portal/metadata/datasets`
- **THEN** 系统返回已配置的元数据域（Datasets）列表。

#### Scenario: 导出数据集 YAML (View as YAML)
- **WHEN** 用户请求 `GET /api/portal/metadata/datasets/{id}/yaml`
- **THEN** 系统将该数据集下的所有表和字段聚合，动态生成并返回完整的 YAML 格式字符串。

### Requirement: 元数据生成助手 (Metadata Generation Assistant)
系统 MUST 提供一个 AI 驱动的助手，从原始 DDL 自动生成初始元数据。

#### Scenario: 从 DDL 生成
- **WHEN** 用户提交有效的 SQL DDL "CREATE TABLE rooms (id int, name varchar...)"
- **THEN** 系统返回一个 JSON 结构，包含建议的 "term" (业务名), "description" (描述), 以及提取出的 "enums" (枚举值)，供用户审查。

# metadata-management Specification

## Purpose
TBD - created by archiving change create-metadata-management. Update Purpose after archive.
## Requirements
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

### Requirement: 数据集配置 (Dataset Configuration)
系统 **MUST** 支持在元数据管理界面配置数据集的 Data Source ID，并将其包含在导出的 YAML 上下文中。

#### Scenario: 配置 MySQL 数据源
- **WHEN** 管理员在前端创建或编辑数据集时
- **THEN** 界面提供 "Data Source ID" 输入框。
- **AND** 管理员输入如 `mysql_crm_01` 后，系统保存该字段到数据库。

#### Scenario: YAML 导出包含数据源
- **WHEN** 系统 (Tool) 调用 `export_dataset_yaml` 导出元数据时
- **THEN** 返回的 YAML 必须包含顶级字段 `data_source`。
- **AND** 如果数据库中该字段为空，则自动填充系统默认的 `external_sql_data_source` 值。

### Requirement: 兼容性 (Compatibility)
系统 **MUST** 确保在引入动态数据源后，未配置新字段的历史数据集仍能正常工作。

#### Scenario: 旧数据集处理
- **WHEN** 访问未配置 `data_source` 的旧数据集
- **THEN** 系统默认将其视为 ClickHouse 数据源，保证现有业务不受影响。

### Requirement: 数据集状态控制 (Dataset Status Control)
系统 **MUST** 支持对数据集进行启用或禁用操作。

#### Scenario: 数据库字段扩展
- **WHEN** 执行数据库迁移脚本
- **THEN** `meta_datasets` 表新增 `status` 字段，类型为 TINYINT，默认值为 1 (启用)。

#### Scenario: 智能体检索过滤
- **WHEN** 智能体调用 `get_dataset_schema` 搜索元数据
- **THEN** 系统仅返回 `status=1` 的数据集。
- **AND** 已禁用 (`status=0`) 的数据集即使关键词匹配也不得出现在结果中。

#### Scenario: 前端状态切换
- **WHEN** 管理员在数据集列表页面点击卡片上的状态开关（药丸形状）
- **THEN** 系统立即调用 API 更新该数据集的状态。
- **AND** 界面实时反映最新的启用/禁用状态。


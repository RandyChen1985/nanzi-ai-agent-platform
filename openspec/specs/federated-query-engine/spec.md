# federated-query-engine Specification

## Purpose
TBD - created by archiving change federated-query-trace-canvas. Update Purpose after archive.
## Requirements
### Requirement: ChatBI SqlQueryBinding 统一执行期元数据
系统 MUST 在 Agent 路径将 `get_dataset_schema` 输出解析为表→数据集→列 binding，并在 `execute_sql_query` 前与当前 SQL 合并为 `SqlQueryBinding`；Gate 预检与 `execute_sql_query_core` MUST 复用同一份 binding 做字段、表归属与权限校验。

#### Scenario: Gate 预检通过后 Core 不重复字段校验
- **WHEN** Agent 路径 `resolve_sql_schema_preflight_with_binding` 通过并设 `preflight_validated=True`
- **THEN** 同轮 `execute_sql_query_core` 跳过重复字段预检，仍执行表归属、权限与 sandbox 校验

#### Scenario: HTTP 直连无 binding 时回退查库
- **WHEN** `/api/v1/chatbi/*` 或门户黄金 SQL 直连调用 `execute_sql_query_core` 且未传入 binding
- **THEN** 系统回退 MetaTable/MetaColumn 查库逻辑，行为与改造前兼容

### Requirement: 元数据 YAML 导出结构隔离
系统 MUST 支持以数据集为边界隔离导出 YAML 元数据。导出时，表和字段必须明确归属于各自的 `dataset_name`，以供大模型感知表所在的数据集上下文。

#### Scenario: YAML 导出表的数据集归属验证
- **WHEN** 触发 `MetadataService.export_dataset_yaml`
- **THEN** 导出的 YAML 文本以 `dataset_name` 作为根节点组织表结构，保证表名、字段物理名与对应的数据集映射清晰、明确

### Requirement: 跨数据集联合意图分类与联邦 XML 计划生成
系统 MUST 在分类层及 `DataQueryExecutor` 识别出涉及跨数据集的联合查询，并引导大模型生成结构化的 `<multi_dataset_plan>` XML 执行计划，其中每一个 `<sub_query>` 节点明确指定其所属的 `dataset_id` 或 `dataset_name`。

平台 MUST 在生成联邦计划前，基于失败 SQL 与 Schema 构造 `SqlQueryBinding`（表→数据集→列），将表→dataset 映射作为硬约束注入 plan prompt，并在解析 XML 后按 binding 校验或修正 subquery 的 `dataset_name`；禁止仅依赖 LLM 从 Schema 文本自行推断而无平台侧绑定。

#### Scenario: 联邦查询执行计划的推演生成
- **WHEN** 用户提问“查找实时能耗超标（数据集A）的机柜维保人员（数据集B）”
- **THEN** 大模型没有直接生成一条拼凑的 SQL，而是输出包含 `<sub_query>`（显式包含属性 `dataset_id` 或 `dataset_name`）和 `<memory_join>` 的 XML 执行计划文本

#### Scenario: 跨库 SQL 升级时 subquery dataset_name 与表归属一致
- **WHEN** 单源 `execute_sql_query` 因引用多 dataset 表失败并升级为联邦查询
- **THEN** 系统从 SQL 与 MetaTable 反查得到 `VIEW_AI_VISIT_LOG → crm_ds`、`HRMRESOURCE → HR_ds` 等映射，注入 plan prompt，且解析后的 subquery `dataset_name` 与各 subquery SQL 中的表所属 dataset 一致

### Requirement: 跨数据集权限校验门禁拦截 (IDOR 防护)
系统 MUST 在联邦执行器执行任何子查询前，依次校验当前用户身份对计划中出现的每一个 `dataset_id` 是否拥有合法的读取权限，实现严格的数据安全隔离阻断。

#### Scenario: 联邦执行中的未授权数据集拦截
- **WHEN** 执行计划中包含 `dataset_id=15` 的子查询，但当前用户的权限角色无权访问该数据集
- **THEN** 系统立即熔断并退出，丢弃后续执行步骤，返回友好错误“您无权访问相关数据集”

### Requirement: 基于 DuckDB 内存表的数据联合 Join
系统 MUST 实现 `FederatedQueryExecutor`。其根据计划中的子查询 `dataset_id` 寻址到对应的数据源连接，执行物理查询获取 Pandas DataFrame，再将其注册为 DuckDB 内存数据库中的临时表进行 Join。

#### Scenario: 联邦计划的分步执行与内存 Join 联结
- **WHEN** 联邦执行器顺次运行基于不同数据集连接的 SQL 查询，将结果集加载到 DuckDB
- **THEN** 执行器在 DuckDB 内存中完成 INNER JOIN，并以标准 JSON table 格式返回给前端画布渲染


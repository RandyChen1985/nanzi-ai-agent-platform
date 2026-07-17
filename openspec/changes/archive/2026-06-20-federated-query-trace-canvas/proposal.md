## Why

随着南孜智能体平台在企业级智能运营场景的深化，多 Agent 意图路由、ReAct 循环、自愈 SQL 和外部 MCP 调用的嵌套调用导致系统复杂度急剧上升。当前平台面临以下核心痛点：
1. **链路可观测性不足**：复杂对话场景下，慢调用和错误难以准确定位，缺乏步骤耗时与父子 Span 嵌套的可视化 Trace 甘特图。
2. **查数交互成本高**：ChatBI 产出图表后，用户对图表样式的微调（如折线转柱状）需要大模型重写 SQL 执行，响应慢且浪费 Token，缺乏前端数据透视和智能图表本地重绘能力。
3. **数据孤岛限制**：现有的 ChatBI 限制在单数据集内执行查询，而企业多源异构数据（如 ClickHouse 的实时能耗和 Oracle 的设备资产）物理上隔离在不同数据集中，不支持跨数据集的多源联邦联合查询。

为了提升系统的可观测性、交互响应时效并打破数据集隔离壁垒，平台有必要在底层与展现层升级这些核心查数与监控功能。

## What Changes

本变更方案将引入以下具体新特性与架构升级：
- **新增 Span 嵌套 Trace 监控**：在 Trace 表及 ORM 层增加 span 级父子关系和耗时记录，提供树形 Trace APM 接口与甘特图交互面板。
- **新增本地透视表与样式拦截**：前端画布集成轻量行列透视（Pivot）算法，并智能匹配/拦截 ECharts 渲染指令，实现毫秒级本地图表重绘。
- **新增跨数据集联邦查询引擎**：保持现有“数据集绑定单数据源，表绑定单数据集”的原则。在分类层识别跨数据集查询，由大模型推演包含数据集映射的 XML 子查询计划，由后端在内存 DuckDB 临时表中进行联合 Join 并实施数据集级的读权限隔离校验。

## Capabilities

### New Capabilities
- `agent-observability-trace`: 收集智能体全链路的嵌套 Span 耗时与上下文元数据，提供树形追溯甘特图与 Trace 调试卡片。
- `pivot-data-canvas`: 在轻量化画布中支持本地行列透视和筛选，并支持 ECharts 样式的快捷无感微调拦截与本地渲染。
- `federated-query-engine`: 识别跨数据集的联合查询，基于大模型生成的 XML 联邦计划（基于数据集 ID 分发），在内存 DuckDB 库中顺次调度各子数据集并执行 Join 关联，包含 RBAC 数据集权限拦截校验；后续可在 Trace 上下文与资源配额完善后扩展为并发调度。

### Modified Capabilities
<!-- 本轮没有修改已有 specs 的行为需求，全部为新功能的补充拓展 -->

## Impact

1. **数据库层**：`ai_agent_execution_traces` 物理表结构升级，增加版本迁移脚本（不需要改动元数据表结构）。
2. **后端服务**：
   - 核心 Runner 和 Executor 嵌入嵌套 TraceSpan 上下文。
   - 分类器 `data_query_turn_classifier.py` 引入联邦查询与渲染微调指令短路识别。
   - 新增 `FederatedQueryExecutor`，根据计划中的数据集 ID 加载对应数据源连接，使用 DuckDB 进行内存级别数据 Join 与数据集权限校验。
3. **前端控制台**：
   - `AgentDebug.vue` 与 `EmbedChat.vue` 嵌入全景甘特 Trace 可视化组件。
   - 画布 `ChatCanvas.vue` 升级，添加 PivotTable 面板与快捷样式切换工具栏。

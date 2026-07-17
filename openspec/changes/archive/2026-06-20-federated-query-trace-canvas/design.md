## Context

当前南孜智能体平台在面对复杂场景时，面临三个主要的交互与架构痛点：
1. **链路黑盒**：复杂的 Agent 意图识别与 ReAct 执行流（包含自愈重试和多源 API 抓取）缺乏层级化的 Span 可观测性。
2. **图表样式微调高延迟**：简单的图表形式或显示切换会误触发完整的 Text-to-SQL 重算，Token 浪费大。
3. **数据集墙（Data Silo）**：受制于“单一数据集仅能绑定单数据源”的设计规范，ChatBI 无法执行多数据源的跨库联邦联合查询。

为了在不打破“单一数据集单数据源”核心物理边界的前提下实现跨数据集查数，同时解决链路排查和交互延迟痛点，我们需要统一设计这三大功能模块的软硬件结合方案。

## Goals / Non-Goals

**Goals:**
- 实现跨协程安全的 `TraceSpan` 收集机制与可视化时延甘特图。
- 研发画布数据透视表组件，以及对样式微调类指令的“前端 setOption 本地重绘 + 后端格式纠错短路缓存”双通道优化。
- 在分类层识别跨数据集查询，由大模型推演基于数据集（Dataset）映射的 XML 子查询计划，由后端在内存 DuckDB 临时表中进行多源 Join。
- 联邦查询执行前实施严格的数据集权限校验（防 IDOR 越权）。

**Non-Goals:**
- 允许数据集内的表跨越不同数据源。表必须遵循原有的“表绑定唯一数据集，数据集绑定唯一数据源”的规范。
- 搭建或引入外部重度分布式查询引擎（如 Trino / Presto）。

## Decisions

### 1. 联邦 Join 底座：为什么选择 DuckDB 内存库而非 Trino 或 Pandas In-Memory？
- **对比分析**：
  - *Trino*：架构重，需要独立的物理集群和复杂的 JDBC/元数据映射维护，不适合轻量级独立部署。
  - *Pandas*：适合小规模数据，但使用 Python 代码手动进行 Dataframe Join 缺乏灵活性，且无法支持模型生成的标准 SQL 语法。
  - *DuckDB*：单文件进程内数据库，C++ 编写，性能极高，天然支持标准的 ANSI SQL JOIN 语法，对 Pandas DataFrame 有原生的零拷贝注册支持，是做内存联邦 Join 的最佳底座。
- **决定**：采用 `duckdb` 作为后端 `FederatedQueryExecutor` 的内存 Join 执行底座。

### 2. 跨数据集路由与调度设计
- **设计**：我们保持表只绑定当前数据集。子查询计划不针对物理“数据源”编写，而是针对“数据集”编写。
- **运行流**：
  1. 大模型生成的 XML 计划中，`<sub_query>` 节点指定 `dataset_id` 或 `dataset_name`。
  2. 后端执行器通过 `dataset_id` 寻址，找到该数据集绑定的 `DbConnectionConfig`。
  3. 执行器根据该连接配置获取 Adapter 实例，执行对应的 SQL。
  4. 这种方式既保持了现存元数据关系不变，又自然融入了数据集级别的权限门禁校验逻辑。

### 3. 链路追踪与前端展现设计
- **技术选择**：采用 `ContextVar` 机制在 `trace_context.py` 中自主封装轻量级 `TraceSpan` 上下文拦截器。
- **展示决策**：
  - **权限**：Trace 链路**所有人可见**，降低系统调试与用户排错门槛。
  - **交互**：在 AI 消息底部的动作工具栏，紧跟在“重新生成”按钮后增加微缩 Trace 交互按钮。
  - **排版适配**：在移动端（窄屏）下自动对 Trace 按钮进行隐藏（使用 Tailwind 的 `hidden md:inline-flex`），防止按钮过多导致 UI 溢出和重叠。

### 4. 图表样式微调：为什么采用前端拦截与后端短路缓存的双通道优化？
- **对比分析**：
  - 如果完全依赖前端正则拦截，复杂的修饰要求（如“将负载排名前 3 的机柜背景加粗并在折线图上画一条 80% 的红色水平警示线”）容易匹配失败。
  - 如果完全发回后端走大模型，Text-to-SQL 耗时太长，且可能误引发数据库重查。
- **决定**：双通道机制。纯图标点击切换由前端 `setOption` 本地处理；输入框输入的复杂排版文本，经由后端分类器拦截为 `FORMAT_CORRECTION` 状态，免去物理库查询，仅调阅 Redis 缓存的上轮数据，通过极轻量的 LLM 排版 Prompt 修正 echarts 样式并输出。

## Risks / Trade-offs

### [Risk 1] 联邦子查询结果集过大导致服务器内存 OOM
- **Mitigation**：在 `FederatedQueryExecutor` 调度子查询时，限制每个子查询注册到 DuckDB 的 DataFrame 行数最大为 1000 行，并限制最终 Join 输出最大为 1000 行。若中间结果超出限制，执行器会截断并在日志中提示用户增加过滤条件。

### [Risk 2] 跨数据集查询带来水平越权风险 (IDOR)
- **Mitigation**：大模型推演的 XML 计划中包含了各子查询 of `dataset_id`。后端在执行具体 Adapter 查询前，必须调用 `PermissionService` 的底层方法，针对当前操作人的 `user_id` 强校验其对每一个 `dataset_id` 的读权限（通过角色菜单表/行级过滤配置的联合校验）。只要有一个校验失败，立即抛出权限异常，熔断当前执行。

### [Risk 3] DuckDB 内存 Join 执行 LLM 生成 SQL 带来外部访问风险
- **Mitigation**：DuckDB 连接必须使用 `enable_external_access=false`，并在执行 `memory_join` 前做只读校验，禁止 `COPY`、`INSTALL/LOAD`、`ATTACH`、`read_csv/read_parquet/read_json` 等外部文件或扩展访问能力。

# 🎉 NanZi AI Agent Platform v1.0.14.0 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.14.0 版本是一次以 **AI 编排责任链管道化重构、分布式锁看门狗自动续约、候选正文实时流式直出、首句工作区并发预热、多工具并发异步调度、工具证据分级门禁、全功能平台 MCP 服务台与双向生态闭环** 为核心，并全面推进 **反幻觉凭据按需注册与 EmbedChat 浮标交互、元数据推荐引擎与实体关系探测优先、定时任务 Cron 表达式生成加固、模型级采样温度配置与文件上传安全隔离** 的里程碑式大版本。

在本次更新中，平台核心 `AgentService` 从单体流转逻辑系统性重构成清晰高内聚的责任链流水线（Preflight → Context → Route → Assemble → Execution → Finalize），大幅降低系统复杂度并杜绝状态泄漏；创新实现首字候选正文毫秒级直出与首句工作区目录并发预热（TTFT 显著降低），配合工具触发平滑回撤及思考卡片防过早折叠，带来丝滑流畅的对话体感；引入 `DistributedLockWatchdog` 自动续约与会话长推理静默心跳，彻底终结长时间推理与多工具循环时的锁悬挂与 409 会话忙；全面开放「平台 MCP 服务台」，平台不仅可作为标准 MCP Server 提供全套 Agent、数据、元数据与知识库工具，还支持 OAuth 授权、Token 细粒度有效期与 Scope 版本控制、Client 全员共享与出站调用独立审计；同时落地多工具并发异步执行、工具证据分级响应门禁、元数据大表分批推荐与数据库探测优先、模型级 Temperature 覆盖等多项企业级硬核能力。

本次变更范围自 `2a4072f2d10ed51ba86284834d4614b04a360f82`（不含，为 v1.0.13 末尾提交）至 `933a5f465ca3476d592b3b53301559ad2e6f8cbf`（含），共 **125 个提交**（其中非 Merge 提交 106 个），涉及 416 个文件、约 55,678 行新增代码与 5,171 行删除。

---

## 🚀 Key Features

### 1. 🏗️ AI 编排责任链管道化重构与工程解耦 (Pipeline Architecture for AgentService)
*   **责任链流水线设计**：彻底重构 `AgentService` 原有的单体复杂流转逻辑，将其解耦为 6 个高内聚、职责单一的独立流水线步骤：
    1.  `PreflightStep`：请求鉴权、用户身份校验、空会话归属判定与会话级分布式锁预检；
    2.  `ContextStep`：多轮历史记忆加载、上下文压缩策略判定、可复用结果检测与系统提示词注入；
    3.  `RouteStep`：意图识别、路由大模型超时保护与智能体动态分流；
    4.  `AssembleStep`：智能体版本配置解析、工具集装配、凭证注入与**工作区并发预热**；
    5.  `ExecutionStep`：驱动 AgentScope 运行时、工具调度执行、流式生成与心跳保活；
    6.  `FinalizeStep`：终态响应封包、记忆与会话审计持久化、可复用结果归档与看门狗资源回收。
*   **死代码清理与模块边界清晰化**：全面清理历史遗留冗余分支与未引用的死代码，模块之间通过强类型的 `PipelineContext` 传递数据，大幅提升单测覆盖率、故障隔离能力与后续扩展性。

### 2. ⚡ 会话并发吞吐、分布式锁看门狗与长推理心跳保活 (Distributed Lock Watchdog & Long-Inference Heartbeat)
*   **分布式锁看门狗自动续约**：引入后台 `DistributedLockWatchdog` 守护协程。在用户发起复杂任务、长时间深度思考或多轮工具调用时，看门狗以安全周期自动续约 Redis 分布式会话锁，彻底解决因长推理超时导致锁被意外释放而引发的并发穿透与 409 Conflict 异常；任务正常结束或异常熔断时，看门狗立即停止并主动释放锁。
*   **长推理静默心跳保活**：在模型深度思考（Thinking）、工具挂起（Pending）或网络等待期间，`session_run_lane` 主动向前端推送轻量级 `heartbeat` 心跳事件，防止反向代理（Nginx、Traefik、云厂商网关）因长时间无 SSE 数据流而强行切断连接。
*   **流式生命周期终态流转与去重收敛**：加固 AI 聊天流生命周期终态状态机，确保在成功、中断、超时或异常等各类边界场景下，流式终态信号 `stream_end` 均能精准触发，彻底杜绝前端一直处于 loading 假死状态。

### 3. 🚀 候选正文实时流式直出、前置回撤与首句工作区并发预热 (Candidate Streaming, Retraction & Workspace Prewarm)
*   **候选正文毫秒级流式直出 (Candidate Streaming)**：在模型尚未决定是调用工具还是纯文本回答之前，首段生成的文字内容以 `answer_delta` 候选正文形式实时流式推送到前端，显著降低首字呈现延迟（TTFT，Time to First Token），使用户秒级感知模型响应。
*   **协议级前置回撤机制 (Retraction)**：当模型在吐出首段候选文本后决定发起工具调用时，系统通过标准化的 `retraction` 机制将已输出的候选文字自动转译为工具调用的引导说明，或在前端干净平滑撤回，确保最终正文不与工具执行结果产生文本拼接冲突。
*   **首句工作区并发预热 (Workspace Prewarm)**：针对涉及文件读写、代码执行或沙箱操作的会话，将原本串行阻塞在工具执行前的物理工作区目录创建、权限检测及 Docker 隔离环境初始化，提前至 `AssembleStep` 阶段与模型首轮推理**异步并发并行执行**，将工具执行前的冷启动延迟降低至接近 0ms。
*   **后台 Producer 挂死看门狗**：为流式生产消费队列加入 Producer 挂死超时监控看门狗，当生产端异常挂死或协程僵死时自动介入熔断并通知前端。
*   **思考卡片防过早折叠修复**：全面修复在模型输出推理内容后、工具调用处于 pending 阶段时，前端因误判流状态而过早折叠思考卡片的偶发体验缺陷，保持思考与执行全过程的视觉完整性。

### 4. 🔄 多工具并发异步调度与安全限流门禁 (Concurrent Multi-Tool Dispatcher & Safe Throttling)
*   **多工具并发异步并行执行**：升级 AgentScope 运行时核心调度器，当大模型单轮次规划并发出多个无依赖关系的工具调用（如同时查询多个数据源、并发检索两份不同知识库文档）时，系统自动启动并发异步任务组（`asyncio.gather`）并行执行，总耗时由串行累加缩减为最慢工具的单次耗时。
*   **多工具安全限流与配额保护**：引入会话级与系统级工具并发配额限制，防止模型失控产生巨量工具调用风暴；结合工具调用全局上限（`agent_tool_loop_global_limit`）与单次超时（`agent_max_toolcall_timeout`），保障系统稳定性。
*   **并发工具确认与流式去重**：完善多工具并发执行时的状态回调与去重防抖，保证多工具执行卡片在界面上顺序渲染、状态独立更新。

### 5. 🛡️ 工具证据分级响应门禁、可信溯源与结果凭据隔离 (Tool Evidence Grounding & Credential Isolation)
*   **工具证据分级响应门禁 (Evidence Grounding)**：建立工具执行结果与模型回答的置信度门禁体系（Level 1 强事实证据 / Level 2 弱推导依据 / Level 3 无证据防御性回答）。当工具执行空结果、查询失败或数据受限时，强制模型进行事实防御，杜绝基于空数据胡编乱造。
*   **多级事实追溯与元查询 API**：提供工具元查询与证据追溯接口，用户或审计人员可由 AI 生成的每一条核心结论，向上追溯至具体执行的工具名称、时间戳、原始入参与原始返回数据快照。
*   **跨轮上下文净化与截断保护**：对工具执行产生的大体积结果进行结构化提纯与凭据隔离，防止未经脱敏的大数据量或敏感认证凭据污染后续多轮对话的历史上下文。
*   **查数快捷追问路由优化**：在 ChatBI 数据查询结果卡片上提供上下文感知的一键快捷追问，自动继承当前数据源与数据集范围，省去重复选择与配置成本。

### 6. 🌐 全功能平台 MCP 服务台与双向生态闭环 (Platform MCP Desk, Inbound OAuth & Outbound Audit)
*   **平台开放为标准 MCP Server (Inbound)**：NanZi 平台自身全面升级为标准 MCP 服务端，外部生态工具（Claude Desktop、Cursor、VSCode、自定义 MCP Client）只需配置 NanZi 的 MCP 端点，即可无缝调用平台内的通用智能体、ChatBI 数据查询、知识库语义检索、元数据目录等丰富工具。
*   **企业级 OAuth 2.0 授权与 Token 治理**：
    *   构建完整的 MCP OAuth 授权认证体系（`V137` / `V138` / `V38` / `V39`），支持客户端注册、Client ID / Secret 凭据生成与换取 Access Token；
    *   **Scope 细粒度权限白名单与版本控制**（`V139` / `V40`）：支持按资源与能力配置 Scope 白名单，引入 `scope_version`，一旦管理员收缩或变更 Client 的权限范围，旧 Token 自动失效，杜绝越权访问；
    *   **Token 自定义有效期与重生成提示**：提供 7 天、30 天、90 天、1 年及永不过期等灵活有效期配置；
    *   **Client 全员共享模式**（`V143` / `V44`）：支持团队公用 MCP Client 与个人私有 Client 灵活切换。
*   **调用第三方 MCP 的用户身份上下文透传 (User Context Assertion 核心特性)**：
    *   **破解企业权限盲区**：过去智能体调用第三方/企业自研 MCP 服务时，通常仅能配置全局统一的静态 Token。第三方系统无法获知是哪位具体员工在操作，无法落实**数据行级鉴权、部门级权限隔离或精准审计**；若将用户信息放在 Prompt 参数中，极易被模型篡改伪造。
    *   **双轨分离认证协议**：平台支持将请求来源认证（`Authorization: Bearer <Token>`）与当前用户身份断言（`X-Nanzi-User-Assertion: <JWS>`）解耦分离，支持独立开关配置，两者互不耦合、各自独立生效。
    *   **Ed25519 非对称签名与密钥隔离**：平台在后端为每个开启的第三方 MCP 独立生成非对称签名私钥（Ed25519/EdDSA）与专属 Audience，私钥在数据库强加密存储且绝不回显前端；第三方业务 MCP 仅需读取公钥验证签名。
    *   **丰富结构化 Payload 规范**：JWS 签名载荷包含标准主体 `sub`（如 `nanzi:user:123`）、`user_context`（真实用户 ID、用户名、姓名、部门代码、组织路径）、安全过滤后的扩展属性、发起调用的智能体信息（ID、版本、名称）以及防重放攻击的 `jti` 与 60 秒极短有效期（`exp`）。
    *   **标准 JWKS 公钥发现与一键生成对接代码**：平台对外暴露标准 JWKS 公钥发现端点（`/.well-known/nanzi/mcp/{mcp_server_id}/jwks.json`）；管理页面提供 Python 与 Java 双语言的一键生成验签中间件示例代码，业务方开箱即用。
    *   **内置 Echo 诊断测试服务**：平台提供一键创建 Echo 测试 MCP 实例，可全链路模拟并回显 Bearer Token 与 UserContext 验签结果（`user_assertion_received=true`、`user_assertion_valid=true`），方便开发者零成本排查 DNS rebinding、Header 传递与验签全流程。
*   **MCP 外部出站工具调用独立审计 (Outbound Audit)**：
    *   新增 `sys_mcp_outbound_audit_logs` 独立出站审计表（`V145` / `V46`），在服务台全局「审计日志」Tab 中集中展示智能体发起的所有外部 MCP 工具调用记录；
    *   记录发起智能体、所属用户、目标 Server、工具名称、耗时（毫秒）、执行状态、入参快照、出参结果及异常堆栈；
    *   支持 Trace ID 端到端全链路穿透排查。
*   **稳定性与 Direct HTTP 架构加固**：
    *   修复 MCP 工具集 P0/P1/P2 缺陷，加固 Direct HTTP 并发稳定性与错误回显；
    *   平台对外 MCP 工具名统一规范为下划线命名风格（如 `metadata_search`、`agent_invoke`）。

### 7. 🎯 反幻觉凭据按需注册与 EmbedChat 浮标交互体验 (Anti-Hallucination On-Demand & EmbedChat UX)
*   **反幻觉凭据按需加载**：重构反幻觉验证凭据的注册机制，避免全量会话无差别加载大体积校验上下文，仅在业务指定或高敏感场景下按需激活，提升普通会话吞吐与启动性能。
*   **EmbedChat 反幻觉开关默认关闭**：针对嵌入式网页聊天组件（EmbedChat），将反幻觉开关默认调整为关闭，提供更快速轻巧的交互响应；管理员或嵌入页面可按需通过参数开启。
*   **输入框内交互浮标与状态指示**：优化 EmbedChat 输入框交互，在输入框内部直接集成悬浮状态指示与交互浮标，减少界面空间占用，移动端与窄屏体验大幅升级。
*   **正文防刷屏熔断优化**：将流式正文块防刷屏熔断周期安全调整为 15 周期，兼顾长文本连续生成与模型死循环拦截。

### 8. 📊 元数据推荐引擎升级与实体关系探测优先 (Metadata Recommendation & Entity Relationship Discovery)
*   **大表长任务分批生成与防截断**：全面重构智能实体关系发现，支持大表、多表场景下的分批生成与分批诊断日志，修复长响应下 JSON 截断引发的解析异常；取消死板的关系推荐数量上限，支持任意规模企业级数据字典。
*   **物理探测优先 + AI 深度融合**：关系推荐算法优先通过数据库真实外键、同名字段与主谓索引进行物理探测；无法直接判定的复杂关系再交由 AI 语义模型批量合并推导，极大节约大模型 Token 成本并成倍提升发现速度。
*   **实时进度与同步日志流**：元数据管理新增实时日志流与 AI 推荐实时进度条，任务执行状态透明可见，避免长任务被前端误判超时。

### 9. ⏰ 定时任务调度 Cron 表达式生成与容错加固 (Cron Expression Governance & Stability)
*   **历史非法 Cron 容错自愈**：修复因部分历史定时任务存在不合规 Cron 表达式而导致的任务中心列表接口 500 崩溃问题，增加底层容错转换与安全兜底。
*   **Cron 生成器与双向校验**：统一任务调度中心前端可视化 Cron 表达式生成器与后端校验规则，支持秒级/分级常用周期快捷切换、实时生成与下次触发时间预测。

### 10. 🌡️ 模型级采样温度配置与多租户/文件安全防线 (Model Temperature Configuration & File Security)
*   **模型级 Temperature 独立配置**：`ai_models` 表新增 `temperature` 字段（`V144` / `V45`），允许为不同模型单独配置 0~2 范围的采样温度（如代码模型设为 0.1、创意写作模型设为 0.7）；未单独配置时平滑回退至全局默认值。
*   **统一 Agent 单次超时基线**：统一全局 `agent_max_toolcall_timeout` 默认值为 60s（`V136` / `V36`），杜绝因超时时间过长引发的任务挂死。
*   **文件上传防覆盖与路径安全**：统一上传文件名的唯一安全生成机制，彻底修复同名文件并发上传时的覆盖缺陷；加固生成文件下载链接的时效性签名校验与发布目录安全隔离。
*   **Office 工具访问范围放宽**：在确保租户安全隔离的前提下，放宽 Office 自动化工具对用户私有工作区内合法文件的访问权限。

### 11. 🎨 前端交互与视觉打磨体验提升 (Frontend UI/UX Polish)
*   **图标库统一与类型加固**：重构前端菜单与资源图标，消除 TypeScript 编译告警与类型索引潜在风险。
*   **消息操作栏与卡片微调**：优化消息气泡底部操作按钮与资源引用卡片排版，修复窄屏模式下操作栏换行错位与工作空间图标重合问题。
*   **移动端 Excel 导出按钮修复**：解决移动端查看数据表时 Excel 导出按钮偶发重复渲染的缺陷。
*   **网页预览自适应缩放**：优化内置网页预览组件的高分辨率自适应缩放与内边距。
*   **在线用户统计修复**：修复在线用户多标签页打开导致的计数重复统计缺陷。

### 12. 🧪 全面质量保证与自动化测试矩阵加固 (Comprehensive Quality Assurance & Test Matrix)
*   **全链路自动化测试覆盖**：新增超 30+ 组全流程测试用例，覆盖责任链管道各步骤契约、分布式锁看门狗自动续约、候选正文实时流式直出与前置撤回、多工具并发调度与限流、MCP OAuth 与出站审计等。
*   **测试规范与清单持续同步**：按平台规范同步更新 `tests/CHECKLIST.md`，前端类型检查 `vue-tsc --noEmit` 全量通过。

---

## 🐛 Bug Fixes

### AI 编排 / 聊天流 / 会话并发
*   **会话锁悬挂修复**：引入看门狗后台自动续约机制，解决长时间推理或工具复杂调用时分布式锁被误释放导致的 409 会话忙。
*   **思考卡片过早折叠修复**：修复模型推理输出后工具处于 pending 状态时，前端误判流结束而提前折叠思考卡片的偶发体验缺陷。
*   **AI 聊天流终态释放**：修复异常或中断场景下 stream_end 未正常触发生命周期收敛的问题。
*   **新空会话归属回退**：优化新创建空会话在多租户上下文中的归属判定回退逻辑。
*   **防刷屏熔断微调**：将流式正文块防刷屏熔断阈值优化调整为 15 周期，避免正常长输出被意外误拦截。

### MCP 服务台 / 权限 / Direct HTTP
*   **Direct HTTP 稳定性**：修复 MCP Direct HTTP 传输模式下的并发调用卡顿与错误回显不全问题。
*   **工具命名统一规范**：将平台对外部暴露的 MCP 工具名统一规范为下划线命名风格，避免在 Claude Desktop 等客户端中识别异常。
*   **Client 资源白名单与时区**：修复 MCP Client 资源白名单配置丢失与 Token 创建时间时区偏差问题。
*   **服务禁用状态覆盖**：修复在 MCP 服务管理中禁用某服务后，前端界面未及时同步禁用状态以及执行端未拦截已停用工具调用的缺陷。
*   **认证信息脱敏与回显**：完善 MCP 服务台认证配置回显与重复提示清理。

### 元数据 / 实体关系发现
*   **JSON 截断解析异常**：修复大表关系推荐时因模型输出长响应截断导致的 JSON 解析失败。
*   **大任务超时误判**：修复长耗时元数据推导任务被前端请求超时机制意外中断的问题。
*   **逐表扫描容错**：优化实体关系推荐的逐表扫描算法，遇到数据库迁移遗留字段缺失时平滑降级。

### 任务调度 / 定时任务
*   **非法 Cron 表达式容错**：彻底解决因历史脏数据或非法 Cron 表达式导致任务调度列表接口 500 崩溃的重大缺陷。
*   **Cron 校验与生成**：统一前后端 Cron 表达式的生成逻辑与合法性校验规则。

### 系统配置 / 数据库迁移 / 文件
*   **V71 分区迁移幂等性加固**：加固 `V71-add_audit_log_partitions.sql` 在重复执行或已有分区时的幂等安全性，修复 `apply-sql-native.sh` 执行异常。
*   **上传文件同名碰撞**：修复同名文件上传时的文件覆盖冲突，统一路径生成规则。
*   **下载链接安全发布**：修复生成文件下载链接缺乏时效校验的安全隐患。
*   **在线用户重复统计**：修复用户在多个浏览器标签页打开时在线计数被重复累加的问题。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.13.0 升级至 v1.0.14.0 时，请特别注意以下变更：

| 项目 | 变更说明与迁移指导 |
| :--- | :--- |
| **平台 MCP 服务台与 OAuth 体系** | 引入全新的平台级 MCP OAuth 架构，需执行 MySQL `V137~V139`、`V141~V143`、`V145` 或 PG `V38~V40`、`V42~V44`、`V46`。如需使用平台对外开放的 MCP 功能，需在「MCP 服务台」重新创建 Client 并分配 Scope。 |
| **MCP 工具名称风格统一** | 平台对外暴露的 MCP 工具名称已统一调整为标准下划线风格（如 `metadata_search`、`agent_invoke`、`metadata_get_schema` 等）。若已有三方客户端对接了旧名称，请同步调整工具调用名称。 |
| **单次工具调用超时基准调整** | 全局默认单次工具调用超时从 120s 统一调整为 60s（通过 `V136` / `V36` 迁移）。若存在特定耗时较长的定制工具，可在智能体版本配置中单独配置 `toolcall_timeout_seconds`。 |
| **分布式锁看门狗与长推理心跳** | 后端会话并发引入后台协程看门狗与长推理心跳机制。请确保生产环境 Redis 运行正常且网络延迟可控，推荐 Redis 7.x 搭配 Redis Stack。 |
| **模型级 Temperature 字段** | 模型配置表新增 `temperature` 字段（`V144` / `V45`），允许单独覆盖全局采样温度，旧模型数据默认继承全局配置，无破坏性影响。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

### MySQL（`db-prod/`）

从 v1.0.13.0 升级至 v1.0.14.0，MySQL 主库引入 **10 个**增量升级脚本，并对部分历史脚本进行了幂等加固：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V110-add_mcp_user_context_auth.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V110-add_mcp_user_context_auth.sql)** | `sys_mcp_servers` 表新增固定客户端凭据与签名 UserContext 认证模式字段。 |
| **[V136-update-agent-max-toolcall-timeout-default.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V136-update-agent-max-toolcall-timeout-default.sql)** | 统一全局智能体单次工具调用超时配置 `agent_max_toolcall_timeout` 默认值为 60 秒。 |
| **[V137-create_platform_mcp_oauth.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V137-create_platform_mcp_oauth.sql)** | 创建平台 MCP OAuth 核心支持表（`sys_mcp_platform_config`、`sys_mcp_oauth_clients`、`sys_mcp_oauth_access_tokens`）。 |
| **[V138-add_mcp_user_access_token_permission.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V138-add_mcp_user_access_token_permission.sql)** | 增加 MCP 用户访问令牌管理权限点（`element:mcp:token` 等）。 |
| **[V139-add_mcp_scope_version.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V139-add_mcp_scope_version.sql)** | `sys_mcp_oauth_clients` 与 Token 表新增 `scope_version` 字段，实现权限变更自动失效。 |
| **[V141-mcp-oauth-security-audit.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V141-mcp-oauth-security-audit.sql)** | 创建 `sys_mcp_oauth_security_audit_logs` 表，记录 MCP 认证安全审计与生命周期日志。 |
| **[V142-add_mcp_rate_limit_config.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V142-add_mcp_rate_limit_config.sql)** | `sys_mcp_platform_config` 表新增 Client 级与 User 级调用频率限流配置。 |
| **[V143-add_mcp_client_is_shared.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V143-add_mcp_client_is_shared.sql)** | `sys_mcp_oauth_clients` 表新增 `is_shared` 字段，支持全员共享 Client。 |
| **[V144-add_ai_model_temperature.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V144-add_ai_model_temperature.sql)** | `ai_models` 表新增模型级采样温度 `temperature` 字段（0~2 浮点数，为空继承全局）。 |
| **[V145-add_mcp_outbound_audit_logs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V145-add_mcp_outbound_audit_logs.sql)** | 创建 `sys_mcp_outbound_audit_logs` 表，记录平台向外部 MCP 发起调用的出站审计全量数据。 |
| **[V71-add_audit_log_partitions.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V71-add_audit_log_partitions.sql)** | *(脚本维护)* 加固审计日志分区迁移的幂等安全性，避免重复执行抛错。 |

### PostgreSQL（`db-prod-pg/`）

PostgreSQL 对应的 10 个增量升级脚本如下：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V36-update-agent-max-toolcall-timeout-default.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V36-update-agent-max-toolcall-timeout-default.sql)** | 统一全局单次工具调用超时配置默认值为 60 秒。 |
| **[V37-add_mcp_user_context_auth.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V37-add_mcp_user_context_auth.sql)** | `sys_mcp_servers` 表新增固定客户端凭据与签名 UserContext 认证字段。 |
| **[V38-create_platform_mcp_oauth.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V38-create_platform_mcp_oauth.sql)** | 创建平台 MCP OAuth 核心支持表。 |
| **[V39-add_mcp_user_access_token_permission.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V39-add_mcp_user_access_token_permission.sql)** | 增加 MCP 用户访问令牌管理权限点。 |
| **[V40-add_mcp_scope_version.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V40-add_mcp_scope_version.sql)** | 新增 `scope_version` 字段支持 Scope 变更失效机制。 |
| **[V42-mcp-oauth-security-audit.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V42-mcp-oauth-security-audit.sql)** | 创建 `sys_mcp_oauth_security_audit_logs` 表。 |
| **[V43-add_mcp_rate_limit_config.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V43-add_mcp_rate_limit_config.sql)** | 新增 Client 级与 User 级调用频率限流配置。 |
| **[V44-add_mcp_client_is_shared.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V44-add_mcp_client_is_shared.sql)** | 新增 `is_shared` 字段支持全员共享 Client。 |
| **[V45-add_ai_model_temperature.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V45-add_ai_model_temperature.sql)** | `ai_models` 表新增模型级采样温度 `temperature` 字段。 |
| **[V46-add_mcp_outbound_audit_logs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod-pg/V46-add_mcp_outbound_audit_logs.sql)** | 创建 `sys_mcp_outbound_audit_logs` 外部出站审计日志表。 |

---

## 🛠️ Upgrade Guide (升级指南)

### 方式一：源码直接升级（本地 / 虚机部署）

#### 1. MySQL 主库

```bash
# 1. 拉取最新代码
git fetch origin && git checkout main && git pull origin main

# 2. 执行数据库增量升级 (自动执行包含 V136~V145 及 V110 等增量迁移)
./db-prod/apply-sql-native.sh

# 3. 启动/重启服务（dev.sh 会自动准备 uv/Python 3.11 环境、按需安装前后端依赖）
./dev.sh
```

#### 2. PostgreSQL 主库

```bash
# 配置 DATABASE_TYPE=postgresql 并执行迁移
./db-prod-pg/apply-sql.sh
```

---

### 方式二：Docker 容器化升级（生产环境 / 容器集群）

#### 1. 场景 A：下载官方 Release 镜像归档（推荐生产/离线环境）

从 [GitHub Releases v1.0.14.0](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.14.0) 下载对应架构的 Docker 镜像归档包：

```bash
# 1. 执行数据库迁移（使用宿主机或临时容器）
./db-prod/apply-sql-native.sh  # PG: ./db-prod-pg/apply-sql.sh

# 2. 导入 Docker 镜像归档（按服务器架构选择对应文件）
# x86_64 服务器
docker load -i nanzi-ai-agent_1.0.14.0_linux-amd64_*.tar

# ARM64 服务器（鲲鹏 / Ampere / Apple Silicon 等）
docker load -i nanzi-ai-agent_1.0.14.0_linux-arm64_*.tar

# 3. 检查镜像加载状态
docker images | grep nanzi-ai-agent

# 4. 启动 / 重启容器服务（默认挂载宿主机 docker.sock 支持 DooD 沙箱）
cd docker && ./start-nanzi-ai-agent.sh
# 或使用 compose 重启：docker-compose -f docker-compose.ai-agent.yml up -d --force-recreate
```

#### 2. 场景 B：本地 / 服务器自主构建镜像

```bash
# 1. 拉取最新代码并执行数据库迁移
git fetch origin && git checkout main && git pull origin main
./db-prod/apply-sql-native.sh  # PG: ./db-prod-pg/apply-sql.sh

# 2. 进入 docker 目录构建 v1.0.14.0 镜像
cd docker

# x86_64 Linux 服务器
./build_linux_x86.sh 1.0.14.0

# ARM64 Linux 服务器（鲲鹏 / Ampere / M 芯片）
./build_linux_arm.sh 1.0.14.0

# 3. 启动 / 重启容器服务
./start-nanzi-ai-agent.sh
```

---

## 💾 Downloads / Assets

本项目 v1.0.14.0 发布版本关联的源码、Docker 镜像资产归档包及配置文件如下：

* 📦 **Source Code (zip)**: `nanzi-ai-agent-platform-1.0.14.0.zip`
* 📦 **Source Code (tar.gz)**: `nanzi-ai-agent-platform-1.0.14.0.tar.gz`
* 🐳 **Docker Image for Linux amd64 (x86_64)**: `nanzi-ai-agent_1.0.14.0_linux-amd64_*.tar`
* 🐳 **Docker Image for Linux arm64 (aarch64)**: `nanzi-ai-agent_1.0.14.0_linux-arm64_*.tar`
* ⚙️ **Docker Compose YAML file**: `docker-compose.ai-agent.yml` / `docker-compose.yml`

🔗 **下载地址**: [GitHub Releases v1.0.14.0](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.14.0)

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **AI 责任链流水线流转**：Preflight → Context → Route → Assemble → Execution → Finalize 各环节正常执行，无死锁与跨会话变量泄露。
- [ ] **会话并发吞吐与锁看门狗**：发起复杂耗时任务，分布式锁在长推理期间自动续约；结束时锁正常释放；无 409 会话忙报错。
- [ ] **长推理静默心跳**：模型深度思考与工具 pending 期间，前端控制台可见周期性推送的 heartbeat 心跳事件，SSE 连接不断连。
- [ ] **候选正文实时流式直出与回撤**：模型吐出正文首段时秒级流式呈现在前端（TTFT 显著降低）；若触发工具调用，候选正文平滑转为过程引导，最终回答干净工整。
- [ ] **首句工作区并发预热**：涉及文件/沙箱操作的任务，工作区在 Assemble 阶段并发完成目录初始化，工具首次执行无多余等待。
- [ ] **思考卡片展示连贯性**：模型深度思考及工具 pending 阶段，思考卡片持续展开，无过早折叠问题。
- [ ] **多工具并发异步调度**：触发同时调用多个独立工具时，后台并发并行异步执行，总耗时大幅缩短，前端卡片有序渲染。
- [ ] **工具证据分级响应门禁**：故意查询不存在的数据或错误工具，模型输出合规的事实防御性回答，无幻觉胡编。
- [ ] **平台 MCP 服务台与 OAuth**：在 MCP 服务台创建 Client、配置 Scope 白名单并生成 Token；使用外部标准 MCP Client（如 Claude Desktop 或 Cursor）调用平台 `agent_invoke`、`metadata_search` 等工具正常响应。
- [ ] **MCP 出站独立审计**：智能体调用外部 MCP 工具后，在服务台「审计日志」Tab 中可查看出站调用的入参、出参、耗时与 Trace ID。
- [ ] **EmbedChat 浮标与反幻觉**：嵌入模式下输入框内浮标正常交互，反幻觉开关默认关闭且可按需开启。
- [ ] **元数据推荐与实体关系**：触发大表实体关系发现，任务分批生成不截断，数据库物理探测优先执行，实时进度条正常推进。
- [ ] **定时任务 Cron 校验**：进入任务调度中心，列表加载正常无 500；使用可视化 Cron 生成器创建定时任务成功。
- [ ] **模型 Temperature 配置**：在模型管理中配置模型级 temperature，模型测试与新建智能体优先采用该温度。
- [ ] **自动化测试全量回归**：运行 `PYTHONPATH=. pytest tests/`，前端契约测试 `pytest --confcutdir=tests/frontend`，前端类型检查 `vue-tsc --noEmit`，确保全量通过。

完整测试清单见 [tests/CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md)。

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `933a5f46` | perf(chat): 支持首句工作区并发预热与后台 Producer 挂死超时看门狗 |
| `eff6f446` | fix(chat): 优化新创建空会话归属判定回退逻辑 |
| `e89e9fc3` | feat(chat): 支持候选正文实时流式并修复思考卡片过早折叠 |
| `32eeffee` | Merge pull request #163 from RandyChen1985/dev-agentscope |
| `3e96cc8d` | docs(tests): 同步更新自动化测试清单 tests/CHECKLIST.md |
| `3725dc3c` | fix(ai): 引入分布式锁看门狗自动续约、权威摘要覆盖与恢复流收敛 |
| `78a79329` | fix(ai): 优化会话并发锁吞吐、长推理心跳保活与流式正文累积 |
| `a5f247fa` | fix(ai): 修复 AI 聊天流生命周期终态、工具并发确认及流式去重 |
| `204d484b` | refactor(ai): 实现 AgentService 责任链管道化重构与死代码清理 |
| `9f332f1c` | feat(ai): 实现多工具并发异步调用执行与安全限流调度 |
| `f3f2db95` | feat(chat): 优化反幻觉凭据注册并支持EmbedChat反幻觉开关默认关闭与输入框内浮标交互 |
| `7095ef61` | perf(ai): AI聊天流链路系统性优化与流式结束状态释放修复 |
| `18996b21` | Merge pull request #162 from RandyChen1985/dev-agentscope |
| `df02af36` | feat: 优化查数结果快捷追问路由及工具结果凭据隔离 |
| `7872eda9` | test: 补充工具凭证封装、跨轮上下文净化及截断保护相关自动化测试 |
| `cd571508` | fix: 将正文块防刷屏熔断阈值调整为15周期 |
| `91baeca8` | Merge pull request #161 from RandyChen1985/dev-agentscope |
| `b8f117c3` | docs: 更新自动化测试清单(tests/CHECKLIST.md) |
| `30d12b6e` | feat(ai): 实现工具证据分级响应门禁与追溯校验策略 |
| `35eb0a9b` | feat(frontend): EmbedChat 支持配置实时流式输出与失败撤回模式 |
| `47b8ba52` | docs: 添加工具预检编排与证据链溯源的设计规范与实施计划文档 |
| `30968a57` | test(ai): 补充工具元查询API、恢复合同与无证据门禁测试用例 |
| `3b17929c` | test: 补充工具预检、证据追溯与前端流式撤回等测试用例及测试清单 |
| `be92aa1d` | fix(mcp): 修复服务禁用前端状态覆盖缺陷并增加执行端停用拦截 |
| `5b5f220a` | Merge pull request #160 from RandyChen1985/dev-agentscope |
| `210109fe` | feat(mcp): 实现MCP外部出站调用独立审计、全局审计日志Tab与页面顶部紧凑化布局 |
| `4a564e7f` | feat: 调整 MCP 调用概览展示顺序 |
| `494524ef` | 修复：历史非法 cron 表达式导致任务列表 500 崩溃 |
| `2797fbad` | fix: 修复任务调度台 cron 表达式生成与校验问题 |
| `db7d6172` | docs: 在 PR 模板中将表结构变更清单拆分为 MySQL 与 PG 子章节 |
| `b0b67880` | Merge pull request #159 from RandyChen1985/dev-agentscope |
| `8d869ba0` | fix: 修复 temperatureGuidance 类型索引安全访问 |
| `4cd87f4d` | docs: 同步更新自动化测试清单 |
| `2a1fbfb3` | feat: 完善模型温度配置与参考说明 |
| `e461c50b` | fix: 删除 MCP 认证重复提示 |
| `3b87da18` | feat: 完善 MCP 认证与资源权限管理 |
| `bad0ea4b` | fix: 统一前端确认弹窗交互 |
| `1b4b1357` | fix: 修复 MCP Client 资源白名单与 Token 时区问题 |
| `65f73e63` | feat: 增加 Client 使用统计与界面优化 |
| `55dc3108` | refactor(mcp): 规范化 Platform MCP 对外工具名为下划线风格 |
| `d4d05fb8` | fix(mcp): 修复 MCP 工具集 P0/P1/P2 缺陷与 Direct HTTP 并发稳定性加固 |
| `851dbde6` | fix: 优化 V71 迁移脚本幂等性与 native 导入脚本执行 |
| `2e0dfbaa` | Merge pull request #158 from RandyChen1985/dev-agentscope |
| `1a46647f` | docs: 在 PR 模板中增加表结构变更清单规范 |
| `c1e6b742` | fix: 完善 MCP Playground Token 提示 |
| `83765baf` | style: 优化 MCP Client 卡片状态与详情入口 |
| `fb3c66a2` | feat: 完善 MCP 服务台 Client Token 管理 |
| `5416e85c` | fix: 修复 MCP 服务台探针与 Client 管理问题 |
| `26ed4454` | fix: 优化 MCP 服务台能力与 Scope 移动端展示 |
| `a85ce2ab` | fix: 回显 MCP 服务认证配置 |
| `db0ada45` | fix: 优化数据库迁移幂等执行与导入提示 |
| `0ed07270` | Merge pull request #157 from RandyChen1985/dev-agentscope |
| `43984e96` | docs: 更新 MCP 服务台测试清单 |
| `0de85a64` | fix: 完善 Client Token 重新生成提示 |
| `13fc8703` | feat: 优化 MCP Client 管理体验 |
| `29a8671b` | feat: 完善 MCP 服务台安全审计与 Token 信息 |
| `1ba46cfc` | feat: 完善 MCP 服务台与入站能力 |
| `bfb5d546` | fix: 完善平台 MCP 与管理页面体验 |
| `26880908` | Merge pull request #156 from RandyChen1985/dev-agentscope |
| `d4a55ef6` | docs: 更新 MCP 测试清单 |
| `a1647948` | fix: 补充 MCP Token 有效期选项 |
| `b8ad7d82` | feat: 增加 MCP Scope 版本与 Token 重生成提示 |
| `a09275ed` | fix: 优化 MCP 服务台权限与 Token 操作 |
| `b112001b` | feat(mcp): 完成平台 MCP OAuth 数据库迁移、API 与前端实现 |
| `e91be183` | fix: 优化网页预览自适应缩放 |
| `bf6238d8` | docs: 提交平台 MCP 与功能设计文档 |
| `4aff6d55` | test: 补充平台 MCP 与浏览器能力测试 |
| `78b81aab` | docs: 设计 AI 消息 URL 右侧打开方案 |
| `902555ce` | Merge pull request #155 from RandyChen1985/dev-agentscope |
| `8bd79346` | docs: 更新实体关系发现验收清单 |
| `6202462f` | feat: 优化实体关系发现策略 |
| `cc796c8a` | Merge pull request #153 from CandyACE/fix/metadata-json-parse |
| `2fb14138` | fix: 补充 Echo 公网地址配置与故障文档 |
| `a6683f91` | Merge pull request #154 from RandyChen1985/dev-agentscope |
| `cbafad40` | fix: 修复 Redis 关闭与会话摘要读取 |
| `703199ac` | fix: 解耦 MCP 用户身份透传与接口认证 |
| `818a9277` | feat: 完善 MCP 用户身份透传与 Echo 测试服务 |
| `bad6412e` | docs: 统一 MCP Bearer Token 术语 |
| `a39bcdc2` | docs: 补充 MCP 认证架构图 |
| `549065cf` | Merge pull request #152 from RandyChen1985/dev-agentscope |
| `35dc2dd0` | docs: 补充 MCP 身份透传测试清单 |
| `c2b84a1f` | feat: 增加 MCP 用户身份透传认证 |
| `768dc9bc` | 同步上游 main 到元数据解析修复分支 |
| `7f014654` | Merge pull request #151 from RandyChen1985/dev-agentscope |
| `b88fc6f4` | docs: 更新在线用户统计测试清单 |
| `86800350` | fix: 修复在线用户重复统计 |
| `84179db8` | fix: 完善流式重复检测与系统配置 |
| `9ae13f7d` | feat: 实体关系发现增加数据库探测优先与 AI 调用合并 |
| `e68d005f` | feat: 完善智能体排序与系统配置 |
| `28cee545` | Merge pull request #150 from RandyChen1985/dev-agentscope |
| `db5ba10c` | feat: 完善平台配置与权限交互 |
| `3b71b126` | feat: 优化知识库与元数据交互体验 |
| `2ba6a7cd` | feat: 增加元数据同步实时日志 |
| `1eaeb26d` | docs: 制定元数据同步实时日志开发计划 |
| `aad8ff80` | docs: 设计元数据同步实时日志方案 |
| `e9c78115` | fix: 放宽 Office 工具用户工作区文件访问范围 |
| `5f6cefeb` | fix: 优化上传目录入口文案 |
| `1b47a239` | fix: 修复移动端 Excel 导出按钮重复显示 |
| `41ea478d` | fix: 修复窄屏消息操作栏和工作空间图标体验 |
| `669e2bbc` | Merge pull request #148 from RandyChen1985/dev-agentscope |
| `38f6c874` | fix: 修复上传文件碰撞覆盖并保护超时默认迁移 |
| `127cc5fd` | fix: 统一上传文件名生成与路径安全处理 |
| `8df09936` | fix: 统一 Agent 超时默认值并修复 Python 3.11 启动错误 |
| `6ec893da` | docs: 制定统一 Agent 超时配置实现计划 |
| `6b2c4719` | docs: 记录统一 Agent 超时配置设计 |
| `548d0f28` | 合并上游主分支最新代码 |
| `1b3f5b48` | 优化元数据关系推荐候选分组 |
| `831504ef` | Merge pull request #147 from RandyChen1985/dev-agentscope |
| `8e3b0f25` | docs: 补充下载链接加固测试清单 |
| `924a5ae2` | fix: 加固生成文件下载链接校验与发布路径 |
| `16fa3db8` | 澄清关系推荐AI推导批次文案 |
| `31b0ce7f` | fix: 统一元数据与消息菜单图标 |
| `2b500bfd` | fix: 隐藏智能体列表拖动提示 |
| `8d914c01` | Merge pull request #146 from RandyChen1985/dev-agentscope |
| `03d199f7` | docs: 更新前端视觉优化验收清单 |
| `ee07af22` | style: 优化消息操作栏与资源卡片布局 |
| `2ea9ef3f` | 记录元数据推荐容器验收结果 |
| `9db848d4` | refactor: 统一前端图标并修复构建类型错误 |
| `5aa9a39b` | 增加元数据AI推荐实时进度 |
| `02a8a429` | 修复关系推荐长任务被前端误判超时 |
| `230ad259` | 修复关系推荐逐表扫描与迁移缺字段降级 |
| `2af41386` | 增强关系推荐分批诊断日志 |
| `117a7a67` | 修复关系推荐长响应截断并支持分批生成 |
| `6de24e84` | 取消关系推荐数量上限 |
| `ae19de8a` | 修复元数据关系推荐 JSON 截断解析异常 |

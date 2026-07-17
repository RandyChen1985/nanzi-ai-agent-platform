# 🎉 NanZi AI Agent Platform v1.0.4 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.4 版本是一次**跨数据集联邦查数、数据门户 2.0 与 ChatBI 运行时全面进化**的大版本更新。在本次更新中，平台全新上线跨数据集联邦查询引擎与链路追踪 Trace Span 画布；数据门户升级为「一数据集一场景」、支持钉住、黄金 SQL 报表暂存与表级推荐提问；ChatBI 引入物理 SQL 智能改写与安全沙箱网关、空结果筛选诊断与自动修正重试、结构化语义意图帧，并将 `DataAgentRunner` 拆分为 26 个 `chatbi/` 域模块；EmbedChat 新增 Mermaid / HTML / PDF / 图片 Canvas 在线预览与物理路径自愈代理；同时强化用户画像与 LTM 全轮次注入、澄清交互与智能路由边界识别。

本次变更范围自 `528d43c`（含）至 `3534d54`，共 **104 个提交**，涉及 255 个文件、约 33,000 行新增代码。

![v1.0.4 发布概览：联邦查数 · 数据门户 2.0 · ChatBI 运行时进化](./image.png)

---

## 🚀 Key Features

### 1. 🔗 跨数据集联邦查询引擎（Federated Query）
* **联邦查询执行器**：全新 `FederatedExecutor` 支持跨数据集 SQL 计划分解、逐节点执行与 synthesis 汇总；输出中文表头映射，完善联邦 synthesis 格式规范。
* **自适应升级与优雅降级**：单数据集 SQL 跨表引用时自动拦截并引导升级联邦查询；跨数据集子查询误判时升级为整计划 repair，子查询错误可优雅降级。
* **逐节点 SQL Repair**：联邦 SQL repair 增强 Schema 检索、EXPLAIN 与 Oracle 错误指引；强化跨数据集 IN/EXISTS 子查询禁止约束并补充正反例。
* **维度补全服务**：新增 `DimensionEnrichmentService`，在跨数据集场景下自动补全关联维度字段。
* **相对时间范围门禁**：新增相对时间表达式静态校验，防范联邦与单源查询中的时间范围幻觉。
* **链路追踪 Trace Span**：`ai_agent_execution_traces` 新增 `span_id` / `parent_span_id` / `meta_info` 字段，前端 Trace 画布支持嵌套步骤父子层级展示与计时审计。

### 2. 🏠 数据门户 2.0（Data Portal）
* **一数据集一场景**：按业务场景合并门户卡片，消除重复展示；支持「我常问」删除、多数据集折叠、卡片置顶与折叠计数。
* **指令升级 `/dataset_portal`**：数据门户系统指令由 `/dataset_menu` 统一为 `/dataset_portal`（旧指令仍兼容）；统一 `useDatasetPortal` composable 与移动端抽屉体验。
* **门户钉住与缓存**：支持门户面板钉住、缓存失效提示、换一批卡片级 4 秒冷却；追问换一批减少 LLM 重复调用；无数据集时跳过 LLM 直接展示权限空态。
* **表级推荐提问 API**：新增表级推荐提问接口；支持推荐问题一键编辑后提问、搜索区域默认折叠隐藏。
* **指标快捷入口**：门户卡片新增指标快捷入口、换一批失败友好提示；调试页抽屉与门户 UI 统一。
* **移动端全面优化**：修复 iframe 内顶栏裁切与滚动、钉住状态异常；快捷指令与专家模式收进顶部栏。

### 3. 🛡️ ChatBI SQL 安全沙箱与智能修复
* **物理 SQL 智能改写与安全沙箱网关**：新增 `SqlSandboxGate`，支持笛卡尔积拦截、明细行数上限 500、Explain 行数熔断；SQL 权限改写加固与业务友好失败提示。
* **空结果筛选诊断与自动重试**：新增 `EmptyResultFilterDiagnostic`，空结果时自动诊断筛选条件并修正重试，业务 SQL 自动重试提升至 3 次。
* **结构化语义意图帧**：引入 `DataQuerySemanticIntent`，增强 Schema 检索精准度、空结果复核与列表意图窄化，防止全量列表过度联想脑补。
* **SQL 预检机制优化**：放宽单表字段校验避免方言误杀；支持 Oracle 伪列 / ROWNUM / dual 豁免与无参内置函数；屏蔽字面量与注释防止误判；阻止 Schema 未收录表的 SQL 执行。
* **Schema 检索治理**：治理未命中时重试词污染；增加 Emoji 过滤与 UI 停用词阻断；时区改读本机系统时区。
* **查询计划卡片**：前端新增 `SqlPlanCard`，将 `sql_plan` 渲染为结构化查询计划卡片，便于用户理解多步查数逻辑。

### 4. 📊 黄金 SQL 报表暂存与极速运行
* **报表暂存 API**：新增 `saved_reports` 端点，支持黄金 SQL 报表暂存、列表管理与免模型极速直连运行。
* **直连与分析闭环**：黄金报表直连成功后新增「手动分析」快捷按钮，支持数据结果缓存复用；修复暂存弹窗、Executed 前缀清洗与标题自动提取。
* **SQL 计划拦截**：数据智能体 SQL 计划拦截相关函数整理归位，完善计划阶段门禁。

### 5. 🖼️ EmbedChat Canvas 多媒体画布
* **Mermaid 架构图**：支持平移缩放交互，在 Canvas 中流畅浏览复杂架构图。
* **HTML 双态预览**：HTML 代码块支持预览 / 代码 Tab 切换；通用服务器物理路径自愈代理，规避绝对路径裂图。
* **多格式在线预览**：消息中超链接与附件区支持 PDF / CSV / 图片（jpg/png/webp 等）双向拦截，一键 Canvas 预览。
* **LTM 偏好提醒气泡**：长期记忆偏好以气泡形式提醒；Canvas 物理路径安全识别与 Markdown 富文本预览。
* **思考过程体验**：流式扫光与计时优化；设置菜单支持思考过程折叠及面板滚动限制；回答完成后思考步骤保持浅色弱化展示。

### 6. 👤 用户画像、LTM 与澄清交互
* **全轮次用户画像注入**：每轮对话注入只读用户画像与 LTM，过滤客户端伪造身份；重构至系统提示词装配流统一注入，修复多 System 消息丢失与长对话截断失效。
* **友好称谓规范**：优先使用真实姓名进行日常称呼指代；澄清提示词注入友好指代称谓。
* **澄清模块架构优化**：按场景路由（`vague_query` / `intent_calibration` / `non_data` / `missing_reuse` / `missing_context`）；代码组装原因说明与 quick 按钮，LLM 仅生成引导语；`non_data` 等场景跳过 LLM 提速；修复澄清正文 `await` 遗漏导致 SSE 序列化失败的 Bug。
* **智能体切换引导**：非数据查询与闲聊场景澄清引导切换专家智能体；实现澄清交互指令拦截。

### 7. 🎯 智能路由与主助手增强
* **会话粘性与简短追问**：ChatBI 会话粘性支持极短属性追问（如「姓名呢」）；泛化路由避免非查数误粘 ChatBI。
* **主助手工具预检**：工具预检与技能自动扫描，收紧反查数护栏误拦；查数请求提示切换专家智能体。
* **手动终止与会话锁**：手动终止生成时释放会话锁；会话运行取消与 Session Lane 进一步完善。

### 8. ⚙️ ChatBI 架构重构与工程化
* **DataAgentRunner 模块化拆分**：将 5,200+ 行单体 `DataAgentRunner` 拆分为 26 个 `chatbi/` 域模块（`clarification` / `sql_gates` / `synthesis` / `schema_prefetch` / `react_stream` 等），主 Runner 精简至约 685 行。
* **AgentService 重构拆分**：`AgentService` 与 `prompts` 模块重构，提升可维护性与测试覆盖。
* **Pydantic V2 迁移**：全局 `dict()` 升级至 `model_dump()`；Redis `setex` 统一改用 `set(key, value, ex=ttl)`。
* **向量同步策略调整**：取消启动时 Redis 向量全量同步，local 模式增加一键重构入口。
* **V1 API 权限兼容**：统一 V1 外部 API 权限 ID 格式并兼容旧数据；可分配资源改为静态列表。

### 9. 🎨 管理端与体验优化
* **元数据管理**：DDL 导入识别已存在表并支持筛选；变更日志 `resource_id` 扩展至 270 字符。
* **个人中心**：会话记忆支持一键清除全部；智能体中心与任务调度台改为全宽布局。
* **系统配置**：修复 Redis 未定义导致配置保存 500；TokenStats 看板默认时段改为 7 天。
* **欢迎页**：能力卡片可点击并优化文案；修复编辑用户弹窗 tab 裁切等问题。

### 10. 📄 文档与开源资产
* **README 与架构图**：替换 README banner，新增系统架构与概览图（16:9 / 9:16 多尺寸）。
* **设计文档同步**：同步 AgentScope Runtime、ChatBI Guards、联邦查询 OpenSpec 等设计文档至当前实现。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.3 升级至 v1.0.4 时，请特别注意以下变更：

| 项目 | 说明 |
| :--- | :--- |
| **门户指令变更** | 数据门户主指令为 **`/dataset_portal`**，旧指令 `/dataset_menu` 仍兼容。前端常量与 composable 已统一迁移。 |
| **数据库变更** | 升级前须执行 `V79` / `V80` / `V81` 三个增量脚本（见下方数据库升级说明）。 |
| **SQL 沙箱更严格** | 物理 SQL 安全沙箱网关上线后，笛卡尔积、超大明细、Explain 超行数等将被拦截；已移除自动注入 LIMIT 的逻辑，改由模型与沙箱协同控制。 |
| **向量同步策略** | 启动时不再自动全量同步 Redis 向量索引；local 模式需手动触发一键重构或按需同步。 |
| **V1 API 权限 ID** | 外部 API 权限 ID 格式统一，旧格式数据已做兼容处理；可分配资源列表改为静态配置。 |
| **查数超时** | SQL 执行超时由 30s 调整为 60s（`V80` 脚本同步系统配置）。 |
| **Pydantic V2** | 内部 API 已全部迁移 `model_dump()`；第三方集成若直接调用内部 Schema 序列化需注意。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

从 v1.0.3 升级至 v1.0.4 期间，平台数据库共引入了 **3 个**增量 SQL 升级脚本（存放于 [db-prod/](https://github.com/RandyChen1985/nanzi-ai-agent-platform/tree/main/db-prod) 目录下）：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V79-expand_meta_changelog_resource_id.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V79-expand_meta_changelog_resource_id.sql)** | 扩展 `meta_changelog.resource_id` 至 VARCHAR(270)，容纳 `{dataset_id}:{physical_name}` 表级变更日志格式。 |
| **[V80-update_data_api_timeout_seconds.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V80-update_data_api_timeout_seconds.sql)** | 将 `data_api_timeout_seconds` 从 30s 更新为 60s，与代码默认值对齐。 |
| **[V81-add_span_columns_to_traces.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V81-add_span_columns_to_traces.sql)** | 为 `ai_agent_execution_traces` 新增 `span_id` / `parent_span_id` / `meta_info` 字段及索引，支持链路追踪 Span 树形检索。 |

> [!WARNING]
> 请在升级后通过执行 `./db-prod/apply-sql-native.sh` 脚本，将增量 SQL 自动、安全地导入到目标 MySQL 数据库中。

---

## 📦 Upgrade Guide

### 从 v1.0.3 升级

```bash
# 1. 拉取最新代码
git fetch origin && git checkout main && git pull origin main

# 2. 更新依赖
source .venv/bin/activate
pip install -r requirements.txt

# 3. 执行数据库迁移（V79 / V80 / V81）
./db-prod/apply-sql-native.sh

# 4. 重新编译前端并启动
cd frontend && npm install && npm run build && cd ..
./dev.sh
```

> [!TIP]
> 升级后建议在 local 模式下执行一次向量索引一键重构；对核心数据集重新同步元数据以确保 Schema 检索与联邦查询命中正常。

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **联邦查询**：跨数据集提问（如关联两表），验证自动升级联邦、逐节点 repair、synthesis 中文表头与 Trace Span 画布展示。
- [ ] **数据门户**：`/dataset_portal` 指令、场景卡片换一批（4 秒冷却）、钉住、表级推荐提问编辑后提问、无权限空态。
- [ ] **黄金报表**：暂存 SQL 报表、免模型极速运行、直连后手动分析、缓存复用。
- [ ] **SQL 沙箱**：构造笛卡尔积 / 超大明细 SQL，确认拦截与友好提示；空结果场景验证自动诊断重试。
- [ ] **澄清交互**：发送「你好，你是谁」「可视化分析一下」，验证澄清正文完整输出（非仅思考面板）与 quick 按钮。
- [ ] **Canvas 预览**：Mermaid 图、HTML 预览、PDF/图片超链接与附件区点击预览。
- [ ] **用户画像**：验证真实姓名称谓、LTM 偏好气泡、非查数场景引导切换智能体。
- [ ] **简短追问**：查数后发送「姓名呢」等极短追问，验证会话粘性与正确复用上下文。
- [ ] **回归测试**：运行 `pytest tests/`，确保 ChatBI / 联邦 / 门户相关用例通过。

完整测试清单见 [tests/CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md)。

---

## 💾 Downloads / Assets

本项目 v1.0.4 发布版本关联的源码、Docker 镜像资产归档包及配置文件如下：

* 📦 **Source Code (zip)**: `nanzi-ai-agent-platform-1.0.4.zip`
* 📦 **Source Code (tar.gz)**: `nanzi-ai-agent-platform-1.0.4.tar.gz`
* 🐳 **Docker Image for Linux amd64 (x86_64)**: `nanzi-ai-agent_1.0.4_linux-amd64_*.tar`
* 🐳 **Docker Image for Linux arm64 (aarch64)**: `nanzi-ai-agent_1.0.4_linux-arm64_*.tar`
* ⚙️ **Docker Compose YAML file**: `docker-compose.yml`

🔗 **下载地址**: [GitHub Releases v1.0.4](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.4)

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `3534d54` | refactor(chatbi): 优化澄清模块架构并修复澄清正文未输出的 await 遗漏 |
| `666e5dc` | fix(test): 修复 ChatBI 既有测试并简化 V1 API 可分配资源为静态列表 |
| `901bb5a` | refactor(chatbi): 拆分 DataAgentRunner 为 chatbi 域模块并同步周边改动 |
| `72b376f` | fix(auth): 统一 V1 外部 API 权限 ID 格式并兼容旧数据 |
| `2540fa9` | refactor(chatbi): 泛化路由/意图 Prompt，时区改读本机，移除 SELECT * 静态门禁 |
| `13f7ffc` | fix(chatbi): 治理 schema 检索未命中时的重试词污染，增加 Emoji 过滤与 UI 停用词阻断 |
| `66905e7` | fix: 修复数据门户 DatasetNavigationPrompts 未定义异常与排版渲染 Bug |
| `0978418` | fix: 优化 ChatBI 结构化意图分析中全量列表的过度联想脑补与清洗误杀逻辑 |
| `030fab4` | fix: keep ChatBI list intent narrow |
| `d7e3755` | fix: block ChatBI SQL tables missing from schema |
| `d3e92aa` | fix: clarify ChatBI intent frame schema boundary |
| `0cfb8ca` | optimize(chatbi): 优化联邦查询前置静态升级条件，防范单数据集关联误判 |
| `1519c98` | feat(refactor): 系统功能优化与 prompts 和 AgentService 重构拆分 |
| `a74a2fe` | 优化联邦查询 Prompt：强化跨数据集 IN/EXISTS 子查询禁止约束，补充正反例对比 |
| `3cd25d1` | feat: ChatBI 自动 SQL 引用与知识库 [ID:n] 引用校验修复 |
| `58ee8ef` | feat(chatbi): 引入结构化语义意图帧，增强 Schema 检索与空结果复核 |
| `1d65069` | fix: harden ChatBI SQL permission rewrites |
| `57705d9` | feat(chatbi): 空结果自动重试业务 SQL 提升至 3 次 |
| `071ed57` | feat(chatbi): 空结果筛选诊断与平台自动修正重试 |
| `4a8b43c` | feat(chatbi): 联邦查询 synthesis 输出中文表头映射 |
| `db7920d` | refactor: 取消启动时 Redis 向量全量同步，local 模式增加一键重构 |
| `f0a6e32` | fix(chatbi): 联邦子查询跨数据集引表时升级为整计划 repair |
| `bf66e5d` | feat(chatbi): 联邦 SQL repair 增强 Schema 检索、EXPLAIN 与 ORA 错误指引 |
| `488ee03` | feat(chatbi): 联邦查询改为逐节点 SQL repair 并完善 synthesis 格式 |
| `424032f` | feat(chatbi): 新增相对时间范围门禁并完善联邦 synthesis 输出规范 |
| `eb15b0a` | feat(chatbi): 加固联邦与单源查询的门禁、修复与缓存安全 |
| `2a35be2` | feat(ai): 优化联邦查询执行器子查询错误修复机制，新增单元测试并更新文档 |
| `4469fe0` | docs: 替换 README banner 并新增系统架构与概览图，同时优化追问排版约束 |
| `2a05c96` | feat(ai): 优化 ChatBI 会话粘性，支持极其简短的属性或字段追问（如'姓名呢'） |
| `b3073a8` | refactor: 移除 SQL 安全沙箱网关中自动注入 limit 的相关逻辑 |
| `239e29f` | feat: 实现 ChatBI 物理 SQL 智能改写与安全沙箱网关，支持笛卡尔积拦截、限制 500 明细及 Explain 行数熔断，并优化 EmbedChat 思考过程展开与偏好单次提示控制 |
| `b2a4921` | feat: 实现ChatBI跨数据集拦截自适应升级为联邦查询与联邦子查询优雅降级 |
| `4495047` | feat: 优化单数据集SQL跨数据集表引用拦截提示，引导走联邦查询与维度补全流程，并更新对应单测 |
| `f308115` | fix(frontend): restore build and improve portal refresh UX |
| `b43b512` | feat(dataset): avoid duplicate portal refresh questions |
| `ab95f40` | feat: 实现跨数据集联邦查询与链路追踪 Trace 画布，并归档相关任务 |
| `09e5c95` | fix: 自动纠正大模型生成绝对路径时的工作空间名前缀幻觉拼写 |
| `0ddf724` | feat: 实现LTM偏好提醒气泡与Canvas物理路径安全识别及MD富文本预览 |
| `b7d32ad` | fix(embed-chat): 支持在消息气泡附件区直接点击图片调起 Canvas 预览，并对下载超链接进行物理路径重映射 |
| `15e9b11` | fix(embed-chat): 修复 code 类型的 html 代码块下，绝对路径图片仍然裂图的漏洞 |
| `50a94eb` | fix(embed-chat): 修复 HTML 预览模式下绝对路径资源无法渲染的问题，将 srcdoc 绑定修改为重映射后的 resolvedContent |
| `aa9ec18` | feat(embed-chat): 支持 HTML 画布的预览与代码双态 Tab 切换，并实现通用的服务器物理路径自愈代理，规避裂图问题 |
| `ec90541` | feat(embed-chat): 支持在消息中拦截以 jpg/png/webp 等常见图片格式结尾的超链接以在 Canvas 中预览 |
| `d4052fa` | feat(embed-chat): 支持 Mermaid 架构图平移缩放与 PDF/CSV/图片双向拦截在线预览，并清理冗余 Canvas 状态声明 |
| `14169ab` | feat(embed-chat): 优化智能体思考流式扫光与计时，并在设置菜单中支持思考过程折叠及面板滚动限制 |
| `f365f9f` | docs: 同步设计文档与 README 至当前实现 |
| `fdfaaac` | fix: 泛化智能路由 ChatBI 会话粘性，避免非内部查数误路由 |
| `be5db4d` | feat: ChatBI 前端渲染 sql_plan 为结构化查询计划卡片 |
| `34f3a2d` | feat: 主助手工具预检与技能自动扫描，收紧反查数护栏误拦 |
| `f5b5ecd` | fix: 优化智能路由边界识别 |
| `9b01e25` | fix: 查数请求提示切换专家智能体 |
| `316ad9c` | feat: 优化 SQL 物理表提取逻辑，支持豁免各数据库方言的内置系统 Schema 校验 |
| `9992923` | refactor: 优化 SQL 预检机制，放宽单表字段校验以避免方言误杀，支持 Oracle 伪列与无参内置函数，并屏蔽字面量与注释防止误判 |
| `607a331` | feat: 支持数据门户推荐问题一键编辑后提问 & 搜索区域默认折叠隐藏 |
| `21f86e8` | feat(portal): 数据门户卡片置顶、我常问折叠计数及移动端UI优化 |
| `cc56604` | fix(config): 修复系统配置保存时由于 redis 未定义导致的 500 报错，添加局部 redis 初始化 |
| `f844f8e` | feat: 黄金报表直连成功后新增手动分析快捷按钮并支持数据结果缓存复用 |
| `34c7ed2` | fix: 修复暂存按钮绑定的局部变量以使标题自动生成逻辑生效，并补充 SQL 表名匹配兜底提取标题功能 |
| `d2342b9` | chore: 整理提交数据智能体 SQL 计划拦截相关细节处理函数 |
| `c040f08` | chore: 整理提交数据智能体 SQL 计划拦截相关遗漏函数 |
| `5121337` | fix: 修复黄金报表暂存弹窗不自动关闭及无提示bug，新增列表小刷新按钮，清洗SQL语句的Executed前缀以修复直连400报错 |
| `3d6e73f` | feat: 支持黄金 SQL 报表暂存与免模型极速运行，优化移动端输入框聚焦及延长数据门户缓存 |
| `c96503f` | fix(chatbi): 优化门禁分类与调试体验 |
| `f3ee304` | feat(embedchat): 优化移动端布局，快捷指令与专家模式收进顶部栏 |
| `219de78` | fix: 补全 metrics Schema 字段，移动端隐藏 EmbedChat 全屏按钮 |
| `1eb371d` | fix(frontend): 修复编辑用户弹窗 tab 文字被裁切的问题 |
| `e482518` | refactor(chatbi): 补充 SQL 报错修复指引，引导模型使用 toDateTimeOrNull 容错 ClickHouse 脏日期 |
| `3730c62` | refactor: 修复 Redis setex 过时警告，统一改用 set(key, value, ex=ttl) 并适配单元测试 |
| `e850cd9` | refactor(chatbi): 移除 SQL 静态网关的 LIMIT 拦截规则，清理 V2 过时 Pydantic 警告并将 TokenStats 看板默认时段更改为 7 天 |
| `f3d02bd` | refactor(pydantic): 全局升级废弃的 dict 方法至 model_dump，并修复已有 RAGFlow 测试用例 |
| `75e7d0e` | fix(chatbi): 优化 SQL 静态安全门禁以兼容 ROWNUM 并防范常见 SQL 误杀 |
| `544cdf7` | feat: 实现智能体澄清交互指令拦截与澄清提示词友好指代称谓注入 |
| `2d470ff` | feat: 优化用户画像称谓规范，优先使用真实姓名进行友好日常称呼指代 |
| `4ac2332` | feat: 优化非数据查询与闲聊的澄清机制，引导切换智能体并新增相关测试 |
| `5b0b432` | feat: 将用户画像重构至系统提示词装配流统一注入，修复多System消息丢失与长对话截断失效的缺陷 |
| `95fa7d5` | feat(chat): 全轮次注入只读用户画像与 LTM，并过滤客户端伪造身份 |
| `283d7a3` | fix(ui): 智能体中心与任务调度台改为全宽布局 |
| `08d347f` | feat(memory): 个人中心会话记忆支持一键清除全部 |
| `83ed6ce` | feat(embed): 欢迎页能力卡片可点击并优化文案 |
| `9fc7a10` | fix(chat): 手动终止生成时释放会话锁并优化门户钉住图标 |
| `00068ae` | fix(portal): 修复 iframe 内移动端门户顶栏裁切与无法滚动 |
| `7fe9a96` | fix(chatbi): 优化 SQL 权限与校验失败的用户可见提示 |
| `d2ae62e` | fix(portal): 修复移动端数据门户钉住状态与滚动问题 |
| `8b612e4` | refactor(portal): 数据门户指令改为 /dataset_portal 并清理重复逻辑 |
| `f9601d8` | fix(ux): 优化模型失败提示与对话错误感知 |
| `b4b3c06` | fix(chatbi): 修复 SQL 空结果 Guard 误判导致有数据仍被阻断 |
| `8be31b6` | fix(llm): 补全 system+user 消息以兼容 Qwen ds-api 网关 |
| `759c97f` | fix(portal): 无数据集时跳过 LLM 并直接展示权限空态 |
| `a696342` | feat(metadata): 数据集 DDL 导入识别已存在表并支持筛选 |
| `95e2472` | fix(chat): 回答完成后思考步骤保持浅色弱化展示 |
| `54b4b48` | fix(chatbi): 强化 SQL 失败修复与重复调用防护 |
| `6ee7ef3` | 优化数据门户：统一 composable、移动端抽屉与缓存状态展示 |
| `b3b7cc0` | feat(portal,chat): 门户钉住与思考步骤体验优化，SQL 超时调至 60s |
| `7ef3e1d` | feat(portal): 换一批增加卡片级 4 秒冷却，减少 LLM 重复调用 |
| `1b77fd9` | fix(chatbi): Oracle dual 豁免与分页语法系统级防护，优化门户卡片标题布局 |
| `5cae587` | feat(portal): 指标快捷入口、换一批失败提示与调试页抽屉统一 |
| `8a684fc` | feat(portal): 门户缓存失效、追问换一批与多数据集折叠体验优化 |
| `071962f` | fix(metadata): 扩展变更日志 resource_id 长度并隔离写入失败 |
| `50ee320` | feat(portal): 一数据集一场景、我常问删除与门户移动端交互优化 |
| `9a46e40` | feat(chatbi): 优化澄清回复，围绕用户原问生成建议并说明触发原因 |
| `d848b13` | 优化 ChatBI 澄清回复：围绕用户原问生成建议并说明触发原因 |
| `4c48297` | feat(portal): 表级推荐提问 API 与门户交互优化，统一开发平台菜单命名 |
| `56cf6a0` | feat(chatbi): 数据门户体验升级与可视化分析闭环修复 |
| `528d43c` | fix(dataset): 按业务场景合并数据门户卡片，消除重复展示 |

---

## 🤝 Contributors

感谢所有参与 v1.0.4 版本发布的开发者！

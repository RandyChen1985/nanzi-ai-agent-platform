# 🎉 NanZi AI Agent Platform v1.0.7 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.7 版本是一次**品牌统一为南孜 / NanZi、ChatBI 可信分析与黄金报表订阅、Grounding 事实门禁、个人技能与场景工作台**的重大功能迭代版本。在本次更新中，平台完成「云枢 / Yunshu」→「南孜 / NanZi」全站品牌与仓库更名；ChatBI 补齐非数据分流、连续分析、可信依据与黄金报表订阅/简报/告警推送；引入并强化 Evidence Grounding 门禁与 MCP 会话恢复；上线个人技能隔离与创建-编辑-挂载闭环、场景模板交付与个人业务工作台；并增强数据源库表摸排画像、技能压缩包导入与智能体版本配置向导体验。

本次变更范围自 `cda13f9993abe6357b8cd8c64df1c389d1bb4618`（含）至 `3aee21bb5bedf765d8aa0ede8e6c0f5f4ff7c4d4`，共 **101 个提交**，涉及 535 个文件、约 47,974 行新增代码。

---

## 🚀 Key Features

### 1. 🏷️ 名称统一：南孜 / NanZi
*   **全站文案更名**：中文名「云枢」统一为「南孜」，英文与技术标识 Yunshu 统一为 NanZi。
*   **仓库与文档对齐**：GitHub 仓库更名为 `nanzi-ai-agent-platform`，README / 设计文档 / 宣传图与项目声明同步更新。

### 2. 📊 ChatBI：分诊、连续分析与可信交付
*   **非数据请求分流**：实现 ChatBI 非数据意图分流与结构化澄清，放宽软性澄清拦截，禁止将 dataset 当作数据库名。
*   **连续分析与分诊升级**：升级分诊与连续分析交付能力，DATA_QUERY 意图无条件委派，并增强澄清改写示例。
*   **可信依据与追问**：实现可信数据依据与追问分析，前端画布事件与黄金报表点击交互优化。
*   **业务简报落库**：新增 `chatbi_briefs`，支持证据化业务简报沉淀与下载物关联。

### 3. 📈 黄金报表：订阅、运行历史、通知与告警
*   **订阅与运行历史**：黄金报表支持订阅调度、运行历史快照与通知中心 / 信箱。
*   **智能简报推送**：支持 AI 智能分析简报，经钉钉 / 企微 / 邮件 / 站内信多渠道投递并审计。
*   **告警条件**：订阅支持版本化告警条件与触发证据，完善智能订阅通知体验。

### 4. 🛡️ Grounding 事实门禁与 MCP 稳定性
*   **Evidence Grounding Gate**：引入 GroundingService，统一各 Runner 决策与审计；支持 Intent-First、Session 级门控、Final SQL 显式角色。
*   **软风险提示**：未接地事实支持 `PASS_WITH_WARNING`；动态只读 MCP 的 external_tool 事实凭证校验。
*   **MCP 恢复增强**：Direct HTTP 模式正确解析 SSE；会话失效自动恢复与 SSE 重连。

### 5. 🧩 个人技能与 Skills 工作台
*   **个人技能隔离**：个人技能独立管理、作用域修复、名称/时间排序、启用/禁用开关。
*   **创建-编辑-挂载闭环**：个人中心「我的技能」、创建后引导横幅、个人 SKILL.md 预加载。
*   **压缩包导入与编辑器**：技能压缩包上传导入；编辑器浅色主题、预览默认、近 30 天调用趋势。
*   **自定义 Skills 白名单**：智能体版本可自定义公共技能白名单，运行时与选择框统一过滤。
*   **新会话预载**：所有智能体新会话首轮强制预载 `using-superpowers`。

### 6. 🧰 场景模板、个人工作台与助手体验
*   **场景模板交付闭环**：场景包市场、资源绑定与实例落库（`ai_agent_scenario_instances`）。
*   **个人业务工作台**：新增个人工作台首页，完善场景包与助手跳转；修复工作台进会话被旧 `conversation_id` 钉回。
*   **智能体卡片增强**：展示能力与资源计数；寒暄导语按真实智能体名由 LLM 生成。

### 7. 🗄️ 数据源摸排画像与元数据导入
*   **库表摸排**：数据源摸排任务支持中断、分页、增量/全量；大库状态自动校正。
*   **画像探索器**：三栏布局与数据预览；置信度评分、临时表判定与忽略过滤。
*   **直导元数据**：画像直导元数据导入，优化导入预览与数据源绑定流程。

### 8. 🎨 管理端 / 聊天体验与平台加固
*   **版本配置向导**：智能体版本配置改为全屏抽屉分步向导，MCP 分组折叠与一键全选。
*   **聊天共享能力**：EmbedChat / AgentDebug 共享能力重构；对话历史 Markdown 渲染与源码切换。
*   **个人门户与中心**：完善个人数据门户与个人中心；记忆页与 API Key 隐藏交互优化。
*   **安全与运维**：管理员可为用户设置登录密码；工具/命令黑名单拦截；RAGFlow 超时可配置；HTTP 环境 API Key 复制兼容。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.6 升级至 v1.0.7 时，请特别注意以下变更：

| 项目 | 说明 |
| :--- | :--- |
| **数据库变更** | 升级前须执行 `V94` ~ `V102` 九个增量脚本（见下方数据库升级说明）。注意场景实例脚本文件名为 **V102**（勿与告警条件 V100 混淆）。 |
| **品牌与仓库** | 对外文档、嵌入页、镜像名若仍使用「云枢 / Yunshu」需同步替换为「南孜 / NanZi」；仓库路径为 `nanzi-ai-agent-platform`。 |
| **Grounding 策略** | 默认启用事实门禁与软风险提示；未知来源请求策略已放宽，但 ChatBI Final SQL / MCP 凭证校验路径行为有变，请回归关键对话。 |
| **个人技能作用域** | 个人技能与公共技能隔离；禁用技能不再自动加载。依赖旧「全局可见个人技能」行为的流程需调整。 |
| **智能体 Skills 白名单** | 开启 `skills_custom` 后仅挂载勾选的公共技能，未配置白名单的旧版本保持兼容默认行为。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

从 v1.0.6 升级至 v1.0.7 期间，平台数据库共引入了 **9 个**增量 SQL 升级脚本（存放于 [db-prod/](https://github.com/RandyChen1985/nanzi-ai-agent-platform/tree/main/db-prod) 目录下）：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V94-create_db_table_profiles.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V94-create_db_table_profiles.sql)** | 创建库表摸排任务与表/视图画像草稿表。 |
| **[V95-add_db_table_profile_confidence.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V95-add_db_table_profile_confidence.sql)** | 画像增加置信度、临时表与忽略标记。 |
| **[V96-create-portal-saved-report-runs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V96-create-portal-saved-report-runs.sql)** | 创建黄金报表运行历史表。 |
| **[V97-create-saved-report-subscriptions-and-inbox.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V97-create-saved-report-subscriptions-and-inbox.sql)** | 创建黄金报表订阅与通知信箱相关表。 |
| **[V98-create-saved-report-digest-deliveries.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V98-create-saved-report-digest-deliveries.sql)** | 创建智能简报推送审计表。 |
| **[V99-agent-version-custom-skills.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V99-agent-version-custom-skills.sql)** | 智能体版本增加自定义 Skills 白名单字段。 |
| **[V100-add-saved-report-alert-conditions.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V100-add-saved-report-alert-conditions.sql)** | 订阅告警条件与触发证据字段。 |
| **[V101-create-chatbi-briefs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V101-create-chatbi-briefs.sql)** | 创建 ChatBI 证据化业务简报表。 |
| **[V102-create-scenario-template-instances.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V102-create-scenario-template-instances.sql)** | 创建场景模板交付实例表。 |

> [!WARNING]
> 请在升级后通过执行 `./db-prod/apply-sql-native.sh` 脚本，将增量 SQL 自动、安全地导入到目标 MySQL 数据库中。

---

## 📦 Upgrade Guide

### 从 v1.0.6 升级

```bash
# 1. 拉取最新代码
git fetch origin && git checkout main && git pull origin main

# 2. 更新依赖
source .venv/bin/activate
pip install -r requirements.txt

# 3. 执行数据库迁移（V94 ~ V102）
./db-prod/apply-sql-native.sh

# 4. 重新编译前端并启动
cd frontend && npm install && npm run build && cd ..
./dev.sh
```

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **品牌**：登录页 / 侧栏 / README 展示为南孜 · NanZi，无残留「云枢 / Yunshu」。
- [ ] **ChatBI**：非数据分流与澄清、连续分析、可信依据追问、业务简报生成。
- [ ] **黄金报表**：订阅创建、定时运行、信箱通知、外部渠道简报、告警条件触发。
- [ ] **Grounding / MCP**：事实门禁软风险提示、MCP SSE/断线恢复、工具空结果不被误拦。
- [ ] **个人技能**：创建 / 编辑 / 挂载 / 预加载 / 启用禁用；创建后引导跳转个人中心。
- [ ] **工作台与场景包**：场景模板交付、工作台进会话不钉回旧 conversation_id。
- [ ] **库表摸排**：摸排任务、画像预览、置信度过滤、直导元数据导入。
- [ ] **回归测试**：运行 `pytest tests/`，确保全部测试用例通过。

完整测试清单见 [tests/CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md)。

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `7a6b3eae` | feat: 打通个人技能创建-编辑-挂载闭环，并优化个人中心体验 |
| `9d8aafd3` | fix: 将场景模板实例迁移重编号为 V102，消除与告警条件的 V100 冲突 |
| `4d6866d5` | fix: 优化多管理页移动端工具栏，并放开元数据菜单入口 |
| `24bb2ab9` | docs: 对齐 ChatBI 文档与亲和性三态及连续分析架构 |
| `3207bb6e` | fix: 修复用户更多菜单先清空再打开导致设置密码无响应 |
| `38633b91` | feat: 升级 ChatBI 分诊与连续分析交付，并优化黄金报表点击交互 |
| `42d35935` | fix: 对齐系统概览与个人工作台视觉体验 |
| `1dfabe2c` | fix: 优化元数据列表工具栏与卡片密度，修复用户更多菜单遮挡 |
| `629abc43` | feat: 所有智能体新会话首轮强制预载 using-superpowers |
| `f4698142` | fix: 修复从工作台进入会话后新会话被旧 conversation_id 钉回 |
| `d1f1f390` | feat: 寒暄导语按真实智能体名由 LLM 生成并丰富文案 |
| `f2acbfe6` | fix: 消除工作台静默刷新时的页面抖动 |
| `dcb43aef` | feat: 新增个人业务工作台并完善场景包与助手跳转 |
| `31f8376f` | feat: 智能体卡片展示能力与资源计数，并优化场景包市场页 |
| `f50a17f8` | feat(agent): 完善场景模板交付闭环 |
| `c2d89db2` | fix: 移动端智能体卡片恢复显示版本管理入口 |
| `1b374611` | fix(chatbi): 放宽软性澄清拦截，并禁止将 dataset 当作数据库名 |
| `bbc40abd` | refactor: 聊天日志改为左右分栏，支持 Markdown 渲染与折叠执行链路 |
| `4f88aa9d` | refactor: 抽取智能体对话历史为独立组件，支持 Markdown 渲染与源码切换 |
| `7107d7d9` | feat: 智能体支持自定义 Skills 白名单，运行时与选择框/工具统一过滤 |
| `f6c65503` | 优化多管理页顶栏布局与操作层级，并完善 AgentDebug 全屏引导。 |
| `7b438bf6` | docs: README 品牌名称统一为 NanZi |
| `4a4fd668` | fix: 修复个人技能作用域问题并新增名称/时间排序 |
| `604c2b31` | feat: 技能启用/禁用开关，禁用后不再自动加载 |
| `46ab6e5b` | feat: 个人技能隔离管理，品牌名称统一为 NanZi·智能体平台 |
| `226aef29` | feat: 技能YAML块标量解析增强，README新增微信交流群二维码 |
| `9c81917b` | docs: 补充南孜项目声明并更新 README 宣传图 |
| `37ee926c` | chore: GitHub 仓库更名为 nanzi-ai-agent-platform |
| `31db0eb3` | chore: 英文品牌与技术标识 Yunshu 统一更名为 NanZi |
| `6904ca42` | chore: 全站品牌文案「云枢」更名为「南孜」 |
| `b6f133bc` | feat: 用户管理支持管理员为用户设置登录密码 |
| `85f880b1` | fix: DATA_QUERY 意图无条件委派，并增强澄清改写示例 |
| `4dac99ce` | fix: 修复回复开始时思考面板未折叠，并允许无数据集时刷新门户 |
| `553a8efe` | fix: 优化 EmbedChat 思考摘要与 ChatBI 诊断门禁体验，并修复上传图片预览 |
| `96d582a2` | feat: 聊天双页面共享能力重构及多项体验优化 |
| `286913e8` | fix: 更新个人数据门户标签文案 |
| `6653d66d` | feat: 完善个人数据门户与个人中心体验 |
| `c557daee` | docs: 设计数据门户首页重组方案 |
| `93905855` | feat: 完善黄金报表智能订阅与通知 |
| `35852379` | feat: 实现黄金报表订阅、运行历史、通知中心与信箱功能，并添加相关测试 |
| `0449c6ce` | feat: 实现 ChatBI 可信数据依据与追问分析功能并优化前端画布事件 |
| `e8ef7e1e` | fix: 修复智能体调试中点击打开文件画布无反应问题并优化相关配置 |
| `47484b97` | feat: 修复 AgentScope 待确认快照序列化时协程泄漏问题并补充测试 |
| `18e11834` | fix: 调大 RAGFlow 检索超时时间至 20 秒，支持从系统配置动态加载 |
| `0d4cf4d0` | fix(frontend): 修复在HTTP部署环境下API Key复制无反应的兼容性Bug |
| `d49c9d75` | fix(db): 修复非 bash 环境下运行 apply-sql.sh 时的语法兼容性错误 |
| `08b39b16` | fix(mcp): 优化 MCP 会话失效自动恢复与 SSE 重连机制并补充单元测试 |
| `4ed0c111` | feat: 实现 ChatBI 非数据请求分流与结构化澄清，包含相关后端模块及测试用例修改 |
| `8491486e` | docs(chatbi): design non-data routing split |
| `5224dbd9` | feat: 模型注册表支持多模态图标渲染，放宽多界面模型加载并美化输入框模型选择器 |
| `d3ff2864` | style(frontend): 优化调试页输入框宽度、智能体选择卡片及侧边栏布局冲突 |
| `e78cb4fd` | feat(grounding): 实现 Intent-First 优化与 Session 级 Grounding 门控开关 |
| `c566577d` | feat(grounding): 引入 GroundingService 并重构各 Runner 的 Grounding 决策与审计流程 |
| `1fde42f7` | feat(grounding): 强化 Grounding 准入网关关联校验，支持 ChatBI 显式 Final SQL 角色并完善测试清单与文档 |
| `17507ab0` | feat(mcp): 修复 MCP 客户端在 Direct HTTP 模式下无法解析 SSE 格式响应的问题；优化智能体运行状态及凭证校验逻辑 |
| `b4d88e70` | feat(grounding): 实现动态只读 MCP 的 external_tool 事实凭证与校验逻辑，更新相关测试 |
| `fee89578` | feat(grounding): 实现未接地事实的软风险提示（PASS_WITH_WARNING）机制，更新测试与文档 |
| `292acb57` | style: 优化提示词工坊左侧列表为高阶卡片样式，修复全屏按钮图标形变并优化默认展示模式 |
| `228401ed` | feat: 技能详情默认进入预览，并展示近 30 天调用趋势 |
| `ea541dbe` | feat: 重构智能体版本配置为全屏抽屉分步向导，优化工具选择与 Prompt 编辑体验 |
| `9b55d68a` | feat: 技能编辑器切换全浅色主题，智能体版本配置 MCP 分组支持折叠 |
| `6c239551` | style: 重构技能文件编辑器 UI，优化工具栏布局与 Markdown 白色预览主题 |
| `6e64f4db` | style: 优化智能体版本编辑中 MCP 工具排版并集成一键全选功能 |
| `26bd6fd4` | feat: 智能体技能压缩包上传导入与工作台 IDE 级 UI/UX 重构 |
| `a64dd48b` | refactor(grounding): 关闭未知来源请求的严格事实核验，放宽 grounding 通过策略 |
| `901166e5` | feat: 增强摸排表探索器体验并对齐 api-data-platform 能力 |
| `e8320209` | feat: 画像探索器三栏布局并支持数据预览 |
| `d2ebcbb6` | feat: 紧凑化导入弹窗布局并优化数据源验证流程 |
| `f57237fa` | feat: 优化智能导入预览页布局与可编辑区域提示 |
| `e32fd376` | feat: 智能导入绑定数据源并优化画像展示与数据集筛选 |
| `3121aa56` | feat: 画像列表支持按可信度排序，优化数据源操作区 |
| `088affab` | feat: 摸排画像直导元数据导入，优化导入弹窗计数与展示 |
| `6debab5c` | fix: 大库摸排主任务状态自动校正，支持进行中查看部分画像 |
| `872b2132` | feat: 库表摸排支持中断、分页与增量/全量，优化数据源操作区 UI |
| `82cea06c` | style: 将查看表资产画像按钮重构为淡雅的靛蓝空心按钮，去除突兀实心背景 |
| `b94bd628` | style: 优化数据源智能摸排排版布局，合并表资产画像按钮至操作列表 |
| `c62d87f2` | feat: 新增数据表摸排置信度评分、临时表判定、忽略过滤机制及设计文档更新 |
| `6e692fd2` | feat: 新增数据源与库表画像功能及相关接口与前端管理页面 |
| `382ce278` | fix: auto recover missing SQL plans |
| `195ab1ff` | feat: strengthen platform grounding gate |
| `b980bab9` | fix: 修复工具返回空结果被门禁误拦截的问题 |
| `c5096e81` | feat: 新增反幻觉事实取证门禁（Evidence Grounding Gate） |
| `13b2e624` | feat: 智能体用户及角色禁用工具与运行时命令黑名单拦截加固，支持自定义与 MCP 扩展工具动态拉取禁用，优化红色拦截卡片 UI 反馈 |
| `8d022810` | deploy: 优化 docker-compose 容器数据卷挂载 |
| `244422f0` | style: 将技能统计弹窗优化为双 Tab 标签页并修复 ECharts 渲染空白问题 |
| `cda13f99` | feat: 优化智能体工具心智并新增技能调用统计功能 |

---

## 🤝 Contributors

感谢所有参与 v1.0.7 版本发布的开发者！

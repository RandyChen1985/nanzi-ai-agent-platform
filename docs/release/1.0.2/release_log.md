# 🎉 NanZi AI Agent Platform v1.0.2 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.2 版本是一次**性能与稳定性里程碑升级**。在本次更新中，平台不仅引入了艾宾浩斯长时记忆遗忘与凌晨固化归并机制，实现了基于本地 Redis (RediSearch) HNSW 向量库的本地化语义检索，而且彻底解耦了常规知识库与元数据 RAG 检索参数，同时对 ChatBI 执行流程进行了彻底的 Bug 修复（引入 ReAct 熔断机制防止空结果死循环，并简化 SQL 计划输出以提升性能）。

本次变更合并了自基础版本（从 `7f04baf` 至今）共 **48 个提交**，涉及大量核心后端逻辑与前端交互重构。

---

## 🚀 Key Features

### 1. 🧠 艾宾浩斯长时记忆（LTM）遗忘与固化管道
* **艾宾浩斯对数遗忘重排**：引入艾宾浩斯记忆模型打分算法，结合 KNN 与 Fallback 检索策略，对用户会话记忆进行智能时间敏感重排。
* **凌晨定时记忆归并降噪**：新增凌晨 3:00 后台定时清理任务，基于 Cosine 相似度连通图聚类对碎片记忆进行大模型自动合并归纳（压缩至 50 字以内），继承引用次数并物理清扫旧片段。
* **记忆管理前端控制台**：个人中心新增“我的记忆”面板，重构了会话摘要时间线展示、记忆索引可视化管理、每日摘要卡片式交互、快速清除与搜索过滤功能。

### 2. ⚡ 基于 RediSearch 的本地 HNSW 向量检索
* **本地表结构及字段向量化**：支持在 local 模式下，系统调用本地 Embedding 服务计算向量，并通过本地 Redis 数据库构建高效的 HNSW 索引，进行超轻量级、无 RAGFlow 依赖 of 元数据语义搜索。
* **ChatBI 案例库本地向量搜索**：同步实现了案例库（ChatBI Examples）本地 Redis HNSW 向量索引与增量同步更新；在 Redis 发生故障时，系统会自动且无感地降级为 MySQL 关键字 LIKE 模糊匹配案例检索。

### 3. 📚 知识库与元数据 RAGFlow 配置独立解耦
* **连接与检索参数彻底隔离**：将常规知识库问答检索参数（`knowledge_ragflow_`）与元数据检索（`ragflow_`）彻底分离开，避免由于 `metadata_provider = local` 本地模式导致常规知识库配置连接断开或混淆的问题。
* **UI 交互自适应隐藏与提示**：当 `metadata_provider` 选择为 `local` 时，页面会自动隐藏原本多余的 `ragflow_api_url` 和 `ragflow_api_key` 输入框，并在下拉框下方动态展示漂亮的蓝黄双色模式差异对比警告。
* **物理清除废弃配置**：在 V77 SQL 迁移脚本中，追加了对已废弃的旧全局配置键 `ragflow_dataset_ids` 进行物理删除的逻辑，防止界面混淆。
* **统一指标命名**：将所有出现 `当前 RAGFlow 引擎：` 的地方统一改为更规范明晰的 **`当前 知识库引擎(RAGFlow)：`**。

### 4. 🛠️ 查数流核心优化（熔断死循环与计划流精简）
* **ReAct 死循环熔断器（Halt ReAct）**：引入 `halt_current_react` 熔断机制，一旦查数遭遇 SQL 报错（`sql_error`）、空结果（`empty_sql_result`）或安全拦截等，系统会立即跳出（`break`）当前的 ReAct 迭代循环，并直接由前端呈现明确的平台门禁拦截指令，**彻底修复了大模型因为空结果而在循环中重复尝试直至崩溃的 Bug**，节约大量 Token。
* **简化计划输出**：关闭了之前强行限制模型必须输出 `<sql_plan>` 后才能执行工具的阻断流程，简化提示词，允许模型以原生 tools 直接查数，显著缩短查数响应耗时。
* **大模型调用指标明细看板**：在 EmbedChat 挂件中支持点击 Token 数量直接弹出大模型调用明细的 Modal 看板。

### 5. 🛡️ 查数安全与防幻觉拦截门禁
* **非查数请求短路澄清**：在分类层增加打招呼、无关扯淡及上下文模糊问题的快速短路识别，直接跳过数据库和经验检索以极速返回澄清话术。
* **JOIN 安全网关明细限制**：新增 SQL 门禁明细 JOIN 无 LIMIT 拦截防护，防范数据膨胀。
* **防脑补防幻觉拦截**：通用助手与知识库助手在无工具调用或 RAG 检索召回中没有有效引用时，触发平台强行拦截，阻止脱离事实的幻觉回答。

### 6. 🎨 界面灵动交互与微动效
* **思考过程卡片时间轴化**：重构了 `EmbedChat` 和 `AgentDebug` 的思考过程卡片，采用轻量级、高密度的动态时间轴（Timeline）设计。
* **滑动选择条支持**：将大模型温度（`llm_temperature`）以及相似度阈值、向量权重全面升级为 Range Slider。并针对温度在滑块刻度 `0.0` 旁标注 `(更严谨/精准)`，在 `1.0` 旁标注 `(更随机/发散)`。
* **输入区域灵动动效**：AI 生成过程中，聊天输入框增加三点跳动、呼吸边框与背景微蓝等灵动微动效。
* **聚焦自愈**：新会话确认重置后、以及 AI 回复完后，输入框会自动获得焦点，大幅提升键盘操作友好度。

### 7. 🐳 Docker 镜像与 Playwright Linux 兼容
* **内嵌浏览器内核支持**：将 Playwright Chromium 浏览器内核直接打包打入 Docker 镜像（支持联网预检索）。
* **root 用户崩溃修复**：为 playwright 增加 `--no-sandbox` 和 `--disable-dev-shm-usage` 参数，彻底修复了其在 Linux/Docker 环境下 root 用户运行崩溃的问题。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.1 升级至 v1.0.2 时，请特别注意以下变更：

| 项目 | 说明 |
| :--- | :--- |
| **数据库变更** | 升级前必须导入执行 `db-prod/` 目录下的增量升级脚本，旧版 `ragflow_dataset_ids` 字段会被自动清洗删除。 |
| **Redis 重建向量索引** | 升级后若开启了 Redis 本地向量化，需在参数配置页点击**【一键重建本地向量索引】**，自动重建 HNSW 元数据与经验案例向量缓存。 |
| **大模型提示词 V8** | 系统提示词全面升级至 V8，以对齐 DataAgentRunner 的门控分工，请确保配置库已完成更新。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

从 v1.0.1 升级至 v1.0.2 期间，平台数据库共引入了 5 个增量 SQL 升级脚本（存放于 [db-prod/](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/) 目录下）：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V73-update_chatbi_prompt_v8.sql](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/V73-update_chatbi_prompt_v8.sql)** | 更新 ChatBI 助手系统提示词为 V8 版本，精简了查数与门控交互，完美适配 DataAgentRunner。 |
| **[V74-add_ebbinghaus_memory_configs.sql](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/V74-add_ebbinghaus_memory_configs.sql)** | 引入艾宾浩斯长时记忆算法的基础半衰期（`memory_base_half_life`）与凌晨降噪相似度聚类阈值（`memory_consolidation_threshold`）等系统配置参数。 |
| **[V75-add_global_embed_configs.sql](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/V75-add_global_embed_configs.sql)** | 插入全局 Embedding 服务参数配置（API 地址、API Key、模型名与维度），用于本地向量化索引计算。 |
| **[V76-add_chatbi_sample_top_k.sql](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/V76-add_chatbi_sample_top_k.sql)** | 新增配置项 `chatbi_sample_top_k`（默认值 5），以控制查数过程中的优质案例最大召回条数。 |
| **[V77-add_knowledge_ragflow_configs.sql](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/db-prod/V77-add_knowledge_ragflow_configs.sql)** | 独立解耦常规知识库问答专属 RAGFlow 检索配置，并自动迁移老数据，同时物理清理删除已废弃的 `ragflow_dataset_ids` 字段。 |

> [!WARNING]
> 本次数据库升级涉及旧配置字段的物理删除及新独立字段的同步复制，请在升级后务必在项目根目录下通过执行 `./db-prod/apply-sql-native.sh` 脚本，将上面的所有增量 SQL 自动、安全地导入到目标 MySQL 数据库中。

---

## 📦 Upgrade Guide

### 从 v1.0.1 升级

```bash
# 1. 拉取最新代码
git fetch origin && git checkout dev && git pull origin dev

# 2. 确认 Redis 版本及组件
# 本地向量检索功能依赖 RediSearch 模块，推荐使用 redis-stack 镜像：
# docker pull redis/redis-stack-server:latest

# 3. 更新依赖
source venv/bin/activate
pip install -r requirements.txt

# 4. 执行数据库迁移（引入新版 RAGFlow 解耦参数，清洗删除旧配置）
./db-prod/apply-sql-native.sh

# 5. 重新编译前端并启动
cd frontend && npm install && npm run build && cd ..
./dev.sh
```

> [!TIP]
> 升级后若启用了本地 Redis 向量化模式，建议在【系统配置】参数配置页点击 **【一键重建本地向量索引】**，以快速生成元数据与经验案例的本地 HNSW 向量缓存。

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **艾宾浩斯长时记忆**：聊天中提到带有时间或偏好的信息（如“我只关心临港地区的数据”），后续多轮提问中确认偏好能否被自动识别改写；个人中心“我的记忆”能够展示、查询并允许清理。
- [ ] **本地 Redis 向量检索**：配置中将 `metadata_provider` 设置为 `local`，点击测试连通性，验证元数据和 ChatBI 案例库检索是否正常；验证在 Redis 停止或发生异常时，系统能否平滑降级为 MySQL 模糊检索。
- [ ] **常规知识库解耦**：在参数配置页将知识库引擎（RAGFlow）URL 和 API Key 配置为正确参数，选择本地元数据后，验证原本多余的 `ragflow_` 配置项是否正确隐藏；测试知识库智能体对话检索，确保能正常调用 RAGFlow 进行检索回答。
- [ ] **熔断机制测试**：故意输入错误的 SQL 查询语法（或构造会报错的 ClickHouse 查询），查看大模型是否在 1-2 轮自愈失败或门禁拦截后立刻提前终止（通过 `halt_current_react` 熔断），不再发生死循环空转。
- [ ] **安全与防幻觉门禁**：对智能助手和知识库助手进行超出已知范围或空响应测试，确认系统能识别并触发防幻觉安全拦截。
- [ ] **UI 灵动交互与 Slider 验证**：在配置页验证 LLM 温度配置、权重配置是否已变更为 Range Slider 滑动条，验证呼吸灯、输入框微蓝渐变动效。
- [ ] **回归测试**：运行自动化测试套件 `pytest tests/`，确保整体功能无回归。

完整测试清单见 [tests/CHECKLIST.md](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/tests/CHECKLIST.md)。

---

## 💾 Downloads / Assets

本项目 v1.0.2 发布版本关联的源码、Docker 镜像资产归档包及配置文件如下：

* 📦 **Source Code (zip)**: `nanzi-ai-agent-platform-1.0.2.zip`
* 📦 **Source Code (tar.gz)**: `nanzi-ai-agent-platform-1.0.2.tar.gz`
* 🐳 **Docker Image for Linux amd64 (x86_64)**: `nanzi-ai-agent_1.0.2_linux-amd64_*.tar`
* 🐳 **Docker Image for Linux arm64 (aarch64)**: `nanzi-ai-agent_1.0.2_linux-arm64_*.tar`
* ⚙️ **Docker Compose YAML file**: `docker-compose.yml`

🔗 **下载地址**: [GitHub Releases v1.0.2](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.2)

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `fa87458` | fix: 引入 halt_current_react 熔断机制以修复 SQL 报错或空结果时 ReAct 的死循环空转问题 |
| `642ffba` | feat: 关闭查数前强制输出 sql_plan 的流程并简化提示词文案 |
| `1cd6a6b` | feat: 知识库 RAGFlow 配置独立解耦及前端交互和状态文案优化 |
| `f95a8ad` | feat: 支持 ChatBI 经验案例本地 Redis HNSW 向量化搜索，联动增量同步并适配前端 local 模式 UI 与禁用 |
| `33af45e` | feat: 优化 RAGFlow 检索超时时间为 3.0s 并调整重试次数为 2 次，实现快速失败降级 |
| `a773b69` | feat: 实现基于 RediSearch 的本地元数据向量检索与前置权限隔离及优雅降级 |
| `812986a` | feat: 升级元数据检索测试模拟器支持临时检索参数与大弹窗；错误Banner全量支持手动关闭并优化同步禁用控制 |
| `70e8a2a` | feat(chatbi): 新增非查数请求短路澄清机制与SQL网关JOIN限制校验 |
| `3fc3184` | fix: 优化数据运行器致命错误判定逻辑，将 [Validation Failed] 移出致命错误以允许 SQL 语法错触发大模型自我修复重试 |
| `24b0560` | feat: 新增通用会话与知识库防幻觉拦截门禁系统，完善相关测试用例与Checklist |
| `37fc841` | feat(memory): 引入艾宾浩斯时间敏感重排与定时记忆固化降噪管道，更新前端配置并归档 OpenSpec |
| `73dc646` | fix(chatbi): 修复检索案例改写中因缺少 user 消息导致的大模型网关 400 错误 |
| `cc72697` | feat(prompt): 强化全局与 ChatBI 的建议按钮输出规则为 MUST 强制约束 |
| `29f6aeb` | fix(chatbi): 修复重复 SQL 拦截门控未更新完成状态导致的死循环问题 |
| `59e47e7` | feat(chatbi): 对齐 DataAgentRunner 门控分工，升级 ChatBI 系统提示词至 V8 版本 |
| `2c1289e` | UI: 重构 EmbedChat 思考过程卡片，采用高密度轻量级时间轴设计 |
| `d074f6b` | UI: 重构 AgentDebug 思考过程卡片，对齐 EmbedChat 采用轻量级时间轴设计 |
| `1370b05` | feat: 调整快捷指令列表，将新会话(/new)提升至第一位 |
| `c3d02bf` | chore: 更新主助手(main)的显示名称为主助手(Main) |
| `4dfa30e` | feat: ChatBI门禁优化及前端版本号显示 |
| `3d8d410` | feat(chatbi): 优化意图纠正与上下文改写逻辑，新增无权限等致命SQL错误极速熔断 |
| `504efc6` | build(docker): 优化 Playwright 安装方式，使用 --with-deps 自动补全系统库 |
| `b36cd1a` | docs: 修复和更新架构设计文档中与代码现状不符的过期内容 |
| `f46b39f` | fix: 为 playwright 增加 --no-sandbox 和 --disable-dev-shm-usage 参数，修复在 Linux/Docker 环境下 root 用户运行崩溃的问题 |
| `db0b6af` | fix(chatbi): 修复智能体历史状态污染与多轮对话 Token 泄漏问题 |
| `01f722e` | build(docker): 将 Playwright Chromium 浏览器内核打入镜像以支持网页检索工具 |
| `714dd17` | style(chat-input): AI 生成中输入框增加三点跳动、呼吸边框与背景微蓝动效 |
| `141c7b0` | fix(chatbi): 将 ChatBI ReAct 硬上限从 10 提升至 20 与全局配置对齐 |
| `626531f` | doc(readme): 优化README主视觉图容器自适应并升级Dockerfile构建缓存 |
| `91b543c` | style(embedchat): 新会话确认重置后自动聚焦输入框并同步更新清单 |
| `2de94d5` | refactor(chatbi): 拦截诊断SQL洗白漏洞、实现Schema Miss短路优化与RAG容错重试 |
| `fa63a9d` | feat(chatbi): 升级大模型指标看板展示深度思考与工具参数并修复KeyError异常 |
| `be66236` | fix(ai): 修复 ChatBI 元数据未命中时门禁穿透漏洞，并优化 SQL 拦截为精准去重以支持多表维度查询 |
| `41eaa29` | refactor(ai): 优化 ChatBI 查数门禁修复逻辑，集成大模型调用时间戳及指标明细看板 |
| `86204e9` | refine: 历史消息裁剪策略优化 |
| `3ecb922` | feat: 工具权限与意图分类优化 |
| `af3ff5f` | perf(ai): 优化 ChatBI 首轮分类延迟与大结果集安全截取 |
| `87a3058` | refactor(ai): 优化 AgentScope 锁超时、指纹重建通知与 RAG 连接异常判定 |
| `9068344` | fix(ai): get_current_time 设为只读工具免审批 |
| `b41135f` | feat(ai): Data Agent 注入时间锚点并挂载系统隐式工具 |
| `e7c67d9` | feat(ai): ChatBI Schema 就绪后首轮 Agent 强制 execute_sql_query |
| `dbc9650` | fix(ai): 工具失败不再中断 Agent 流，确保 synthesis 兜底输出正文 |
| `c597668` | feat(ui): 输入框聚焦高亮与移动端批准弹层可读性优化 |
| `a3b9e90` | feat(ui): 优化工具批准方式选择器交互与视觉样式 |
| `efbfd14` | fix(ai): 知识库多轮继承 dataset_ids 并修正轮次误分类为通用对话 |
| `f7e7007` | fix(ai): 预检索保留知识库工具并优化检索日志与引用体验 |
| `f0f2774` | feat(ai): 知识库检索结构化传参、RAG 参数贯通与无 dataset 阻断 |
| `aafa3fe` | feat(ai): 优化知识库引用 Popover 与 citation 提示词 |

---

## 🤝 Contributors

感谢所有参与 v1.0.2 版本发布的开发者！

# 🎉 NanZi AI Agent Platform v1.0.6 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.6 版本是一次**知识库开发与运营看板中心、Token 额度与预算控制、工作空间文件浏览器全面升级、个人多渠道消息通知**的重大功能迭代版本。在本次更新中，平台围绕“知识库开发与管理”进行了全链路深度优化，上线知识库运营统计看板与原档高亮定位；全新推出 Token 额度控制体系（包含策略配置、调用拦截、用量展示及快捷查询）；全面升级工作空间文件管理器（支持回收站、最近文件、多层隔离目录及画布预览）；并重构个人中心通知配置，支持钉钉/企业微信/邮件多端通知绑定与连通性测试。

本次变更范围自 `d429e7fa97b29edff8d06fa830a53d54e42be78b`（含）至 `64e40cf7c5cc150821c97a8ecde9b4d830bca2d8`，共 **87 个提交**，涉及 161 个文件、约 25,364 行新增代码。

---

## 🚀 Key Features

### 1. 📂 知识库开发与 RAG 管理体系深度加固
*   **文档虚拟文件夹与卡片折叠**：知识库支持文档虚拟文件夹（目录）分类管理，文件拖拽移动落库，卡片支持独立折叠与展示高级配置，排版完美对齐数据门户。
*   **反向成员授权**：知识库管理页新增“分配授权成员”与反向角色/用户授权分配功能，限制只有拥有管理权限的用户才能访问敏感数据集，并补全单元测试。
*   **AI 元数据分析与智能回填**：编辑元数据时集成 LLM，基于前 100 个文件名分析智能提取中英文标签、描述与备注信息，并在前端一键填充回填。
*   **双栏置顶提问推荐**：卡片支持 1~3 个人位置顶问题，在门户抽屉双栏呈现置顶问题与 LLM 预测问题，左键可直接发送，右键可进行小铅笔二次编辑。
*   **追问检索优化与 Mermaid 流程图**：当问题属于总结或指代代词追问时短路跳过 RAG 检索，复用上轮引用；优化步骤类问题的 Mermaid 图表生成 Prompt；前端折叠附加元数据提示词。
*   **错误提示友好翻译**：在 `RagFlowClient` 中拦截 RAGFlow 底层权限拒绝（User lacks permission）、未找到数据集等晦涩错误，自动映射并转化为友好可读的中文文案。
*   **高级检索调参**：侧边滑动抽屉支持参数即时调参，并内置反幻觉开关；过滤机制中剔除失联 `dataset_id` 确保连接稳定。

### 2. 📊 知识库运营统计看板与原档定位
*   **统计看板与趋势分析看板**：全新引入知识库运营看板，统计近期使用率、高频检索知识库排名并绘制趋势图，看板刷新时自动触发 Redis→DB 实时同步。
*   **原档定位滑出预览**：检索测试和引用溯源支持 RAG 原档物理页码直接高亮定位，侧栏滑出 PDF/Markdown 原档预览，移动端自适应隐藏。
*   **性能埋点与报错修复**：修复看板埋点永远拿不到 citations 数据的 Bug；修复刷新看板非 JSON 格式输出时触发误导性 ERROR 日志的问题。

### 3. 🗂️ 工作空间文件浏览全面升级 (Workspace 3.0)
*   **Redis 按用户隔离**：工作空间浏览偏好、最近打开文件改为 Redis按 `user_id` 物理隔离，防范多用户数据覆盖。
*   **私有 Sandbox 与路径隔离**：支持用户私有 `uploads/sandbox` 路径隔离，工作空间路径可读化，并深度加固文件浏览器读写安全性。
*   **最近文件与回收站**：文件浏览中心全面升级，新增“最近文件”面板、快捷导航以及支持软删除的“回收站”机制。
*   **抽屉式浏览与画布预览**：升级为右侧抽屉式文件浏览器，支持工作空间图片 blob 破图修复、画布预览与文件直接写入 API。

### 4. 💰 Token 额度控制与预算管理体系
*   **多维度限额拦截**：新增 Token 额度管理策略配置表，提供调用次数/Token用量预算拦截、策略管理与实时消耗监控能力。
*   **个人用量展示**：个人中心增加“我的 Token 消耗”展示 Tab，额度管理体系中数据统一以紧凑格式（K/M/B）展示。
*   **快捷指令查看**：对话框支持 `/quota` 快捷指令，支持用户在聊天中随时查询本月已用额度及剩余额度。

### 5. 🔔 个人中心消息通知多渠道绑定
*   **多通道连通性测试**：个人配置页支持配置并绑定钉钉群机器人、企业微信群机器人及 SMTP 邮件通知，支持密码打星脱敏与连通性测试。
*   **企微通知工具集成**：在工具层重构智能体消息通知工具，自动绑定个人配置，并新增企业微信通知工具，提升工作流集成效率。

### 6. 🎨 网页悬浮助手集成与品牌个性化
*   **悬浮挂件多模式集成**：网页悬浮助手优化引导页，支持以 Widget、嵌入网页等多模式集成；优化参数传递并修复前端偶发加载故障。
*   **智能助手自定义命名**：品牌个性化设置除了 Logo 与版权外，全面支持自定义主助手显示的系统名称。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.5 升级至 v1.0.6 时，请特别注意以下变更：

| 项目 | 说明 |
| :--- | :--- |
| **数据库变更** | 升级前须执行 `V88` ~ `V93` 六个增量脚本（见下方数据库升级说明）。 |
| **Token 额度限制** | 更新后系统默认将启用 Token 限额拦截逻辑。请管理员通过配置 `quota_policies` 表以配置相应用户或角色的策略，以防误伤。 |
| **消息通知密码脱敏** | 个人中心消息配置在保存时对明文密码自动进行加密传输与落库；历史配置的明文密码需在升级后重新保存。 |
| **工作空间结构** | 临时 Sandbox 与 Session 目录进行路径物理迁移隔离，脚本或插件在访问物理路径时严禁硬编码。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

从 v1.0.5 升级至 v1.0.6 期间，平台数据库共引入了 **6 个**增量 SQL 升级脚本（存放于 [db-prod/](https://github.com/RandyChen1985/nanzi-ai-agent-platform/tree/main/db-prod) 目录下）：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V88-create_quota_policies.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V88-create_quota_policies.sql)** | 创建 `quota_policies` 策略管理表，支持多维度 Token 用量控制。 |
| **[V89-quota-usage-index.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V89-quota-usage-index.sql)** | 为限额与消耗日志添加数据库性能索引。 |
| **[V90-create_user_notification_configs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V90-create_user_notification_configs.sql)** | 创建 `user_notification_configs` 个人中心消息通知渠道配置表。 |
| **[V91-register_wechat_work_tool.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V91-register_wechat_work_tool.sql)** | 注册企业微信机器人通知工具元数据。 |
| **[V92-update_notification_tool_descriptions.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V92-update_notification_tool_descriptions.sql)** | 动态更新系统通知工具描述，使之绑定个人中心自定义配置。 |
| **[V93-create_knowledge_base_metrics.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V93-create_knowledge_base_metrics.sql)** | 创建 `knowledge_base_metrics` 知识库运营度量表，记录检索调用率与看板分析元数据。 |

> [!WARNING]
> 请在升级后通过执行 `./db-prod/apply-sql-native.sh` 脚本，将增量 SQL 自动、安全地导入到目标 MySQL 数据库中。

---

## 📦 Upgrade Guide

### 从 v1.0.5 升级

```bash
# 1. 拉取最新代码
git fetch origin && git checkout main && git pull origin main

# 2. 更新依赖
source .venv/bin/activate
pip install -r requirements.txt

# 3. 执行数据库迁移（V88 ~ V93）
./db-prod/apply-sql-native.sh

# 4. 重新编译前端并启动
cd frontend && npm install && npm run build && cd ..
./dev.sh
```

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **知识库开发**：新建虚拟文件夹、编辑 AI 分析自动回填、卡片独立折叠、自建徽章标记、检索测试高级参数。
- [ ] **运营看板**：访问“知识库运营看板”、刷新看板并检查 Toast、在右侧原档定位滑出预览、移动端隐藏原档。
- [ ] **工作空间**：工作空间右侧抽屉式浏览、最近文件与软删除回收站、私有 `uploads/sandbox` 目录安全性。
- [ ] **Token 预算**：对话框输入 `/quota` 快捷指令、限额拦截拦截触发、个人 Token 消耗 Tab 展现。
- [ ] **消息通知**：在个人配置测试钉钉/企微/邮件通道、通知工具绑定及使用。
- [ ] **回归测试**：运行 `pytest tests/`，确保全部测试用例通过。

完整测试清单见 [tests/CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md)。

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `64e40cf` | fix(knowledge): 优化 RAGFlow 底层错误提示为友好中文转换并补齐单元测试 |
| `be1f357` | refactor(ai): 重构统一请求决策层并清理冗余计算与废弃死代码 |
| `27d97fe` | feat(knowledge): 优化知识库连接不可用时的 UI 提示与系统问候语技能引导 |
| `f1d9792` | feat: improve skill auto-loading flow visibility |
| `53fe0f1` | feat: 优化快捷指令与系统配置展示 |
| `81552f4` | feat: 优化知识库搜索工具绑定机制，仅在配置的工具列表中勾选时才挂载，防止误挂载 |
| `7b922af` | style: 修复虚拟文件夹和文档列表行重叠排版，增加虚拟文件夹文档数量计数徽章 |
| `00bd254` | feat: 优化知识库智能体追问检索逻辑，优化 Mermaid 流程图生成提示词，并在前端实现系统元数据提示词折叠 |
| `8095bf0` | style: 优化知识库卡片标签展示，最多显示3个并支持点击加号弹窗查看全部 |
| `c75fa3e` | feat: 知识库新增文档虚拟文件夹分类、卡片独立折叠、自建徽章标记、高级配置配色优化及双栏置顶提问推荐 |
| `4427a48` | feat: 知识库管理支持反向角色和用户授权分配，限权用户管理权限访问，并补全单元测试 |
| `a825bdd` | feat: 知识库门户抽屉新增搜索开关及按分类标签过滤功能，并更新测试清单 |
| `12e23c4` | feat: 知识库管理编辑元数据支持 AI 智能分析与自动回填，优化界面 UI 及同步体验，加固 404 容错 |
| `581c158` | docs(知识库): 补充知识库中心与聊天端到端架构设计文档 |
| `c19961d` | fix(知识库): 权限页标记失联库并禁止勾选，检索前剔除失联 dataset_id |
| `2713cdb` | feat(知识库门户): 优化推荐提问骨架屏，原档预览抽屉支持分区折叠 |
| `c81db4e` | fix(知识库运营): 补全知识库排行榜中文名称解析链路 |
| `3188f42` | fix(知识库运营): 修复趋势图与排行榜展示问题，移动端隐藏查看原档 |
| `5ea0ce0` | fix(知识库运营): 修复埋点永远拿不到 citations 数据的根本问题，改为直接使用 citation 事件的原始数据 |
| `bd645e5` | feat(知识库运营): 刷新看板时增加成功/失败 Toast 提示 |
| `f13b9c3` | fix(知识库运营): 修复非 JSON 格式 output 触发误导性 ERROR 日志的问题 |
| `ec5fbac` | feat(知识库运营): 刷新看板时自动触发 Redis→DB 数据实时同步 |
| `249ce4e` | feat(知识库中心): 文档预览权限收口、RAG 预览 Teleport 层级修复及代码清理 |
| `7c3aadd` | fix: keep rag preview above pinned drawers |
| `b539f10` | fix(知识库中心): 关闭抽屉切换自动路由模式后提示条不消失的问题 |
| `0c96947` | feat(知识库中心): 单文档专属推荐提问展开、文档数量徽章与若干修复 |
| `77bd4ac` | feat: 知识库中心卡片排版布局对齐数据门户，支持推荐问题双区交互并修复后端 LLM Bug |
| `aaaf18d` | feat: 实现侧边栏滑动式知识库中心及高级检索参数调参功能并集成反幻觉开关 |
| `77bafe6` | feat: 实现知识库运营统计分析看板与RAG原档物理页码定位滑出预览高亮功能 |
| `9501394` | fix(rag): 修复系统配置页点浏览知识库时未保存配置不能应用、掩码 Key 被覆盖等问题 |
| `ba6655d` | fix(rag): 修复知识库检索连接失败时门禁失效导致重复重试的 Bug |
| `64db0cb` | fix(frontend): 修复知识库上传或删除文件后左侧树节点计数不自动更新的问题 |
| `6a93bd4` | style(frontend): 优化知识库引擎地址显示为状态胶囊悬浮Tooltip并限制检索测试页连通性 |
| `b9b7fbc` | feat(widget,rag): 优化网页悬浮助手集成引导，支持多模式并修复前端故障 |
| `0f1406c` | fix(metadata): 修复变更日志Tab内容渲染块遗漏导致页面空白的故障 |
| `9eeecbe` | fix(metadata): 修复因从模块直接导入类静态方法导致的ImportError |
| `0b822fb` | fix(metadata,rag): 修复从数据集取消授权报400的Bug并同步返回主键ID |
| `5203eab` | feat(metadata,rag): 智能体数据集新增权限管理Tab及增删搜索功能 & 知识库删除同步清理授权关系 |
| `d7c1931` | fix(ragflow): 兼容 RAGFlow OpenAPI 的 page_size 最大 100 的限制，对超额请求自动进行分批次拉取并合并 |
| `5827d61` | fix(ragflow): 修复知识库管理模块实例化 RagFlowClient 时未指定 config_prefix 导致读取错误配置 Key 的 Bug |
| `bce2340` | style: 替换技能管理页面中的原生confirm弹窗为自定义ConfirmModal组件 |
| `984d590` | feat(router): 优化运行环境诊断意图识别及工具促发策略并更新测试与清单 |
| `ae825e2` | feat: 支持跨设备/无痕模式同步当前活动会话ID，Redis全局按用户维度存储且不设置过期时间 |
| `71c81da` | fix(ai): 修复智能体触发确认后丢失 AgentContext 导致自定义工具获取不到 user_id 的问题 |
| `3c53520` | feat: 系统配置中第三方账号同步配置项置为只读并进行可视化卡片重构 |
| `7a1b255` | feat(branding): 品牌个性化支持自定义智能助手名称 |
| `0f516b2` | refactor(ai): 清理工具促发策略代码，提升可读性 |
| `75197e9` | feat(ai): 优化工具促发策略 (Tool Nudge Policy)，支持识别语义和单轮意图 |
| `84c26a5` | fix(nudge): 优化智能体引导切换机制与数据幻觉防护逻辑 |
| `657cc0c` | fix(task): 优化定时任务相关指标配置及前端文件浏览器样式与任务中心交互 |
| `9c8aade` | fix(task): 修复定时任务执行历史不落库问题及优化右键菜单说明 |
| `c44cbca` | fix(frontend): 修复文件上传丢失 boundary 导致 400 报错的问题，新增 Logo 裁剪预览功能，并修复登录页隐藏 SSO 刷新后还原的问题 |
| `60b033c` | feat: 个人中心增加消息通知配置（钉钉/企微/邮件），支持连通性测试及重构 |
| `d5ad75c` | feat: 会话目录迁入 sessions 子目录并优化工作空间标识 |
| `e32ae68` | feat: 区分会话目录与 docs 文档目录，并增强工作空间浏览体验 |
| `3a56de2` | fix: 修复工作空间图片画布预览 blob URL 被误转换导致破图 |
| `c6d0ccf` | feat: 工作空间浏览偏好改为 Redis 按用户隔离存储 |
| `a235854` | feat: 工作空间最近文件改为 Redis 按用户隔离存储 |
| `a27dcd5` | fix: 浏览 uploads 目录不存在时自动初始化 |
| `99e1b66` | feat: 技能右侧抽屉与门户关闭恢复自动路由 |
| `9584fe2` | feat: 记忆选择改为右侧抽屉，支持钉住与清除记忆 |
| `4639545` | feat: 工作空间浏览全面升级（回收站、最近文件与快捷导航） |
| `7e4bd83` | feat: 工作空间浏览增强与用户私有 uploads/sandbox 路径 |
| `b063757` | feat: 工作空间右侧抽屉浏览、画布预览编辑与文件写入 API |
| `7282017` | fix: 工作空间浏览体验优化 |
| `09fe7cb` | feat: 工作空间路径可读化并加固文件浏览权限隔离 |
| `0be883c` | fix: 第三方用户同步字段/表选择下拉改用 Teleport 避免被裁切 |
| `68f8343` | feat: 对话快捷指令 /quota 查看本月 Token 额度 |
| `c32daae` | feat: Token 数量统一 K/M/B 紧凑展示 |
| `47326e4` | feat: Token 额度管理体系（策略配置、调用拦截与用量展示） |
| `deccf7e` | feat: 个人中心新增我的 Token 消耗 Tab |
| `21f311a` | feat: 数据权限过滤变量区分内置与 extra_data 扩展 |
| `5b1f11a` | fix: 第三方用户同步 extra_data 将 NULL 写入为空字符串 |

---

## 🤝 Contributors

感谢所有参与 v1.0.6 版本发布的开发者！

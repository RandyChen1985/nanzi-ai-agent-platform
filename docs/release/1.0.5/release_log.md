# 🎉 NanZi AI Agent Platform v1.0.5 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.5 版本是一次**ChatBI 联邦查数深度加固、数据门户黄金报表体系、SQL Server 数据源与第三方用户同步**的大版本更新。在本次更新中，平台全面增强联邦查询 WHERE 探查、空结果筛选重试、子查询门禁与平台重试预算；数据门户上线黄金 SQL 报表持久化、分享与个人偏好；新增 SQL Server 全链路支持与 sqlglot 方言修复；重构第三方用户同步为「用户名映射 + extra_data JSON 聚合」；同时引入 Office 文档工具、品牌个性化、知识库总开关、主助手动态专家清单、智能体中心拖拽排序与 Redis 诊断增强等能力。

本次变更范围自 `61681d9`（含）至 `099181d`，共 **49 个提交**，涉及 199 个文件、约 21,800 行新增代码。

---

## 🚀 Key Features

### 1. 🔗 ChatBI 联邦查询深度加固
* **SqlQueryBinding 统一绑定**：引入 `SqlQueryBinding` 统一 Schema→SQL→execute 元数据绑定，加强联邦 memory_join 列校验。
* **memory_join 计划修正**：联邦 `memory_join` 计划阶段自动修正与 preflight sqlglot 列校验；修复 repair 解析污染与降级列结构异常。
* **联邦 SQL Repair 增强**：优化联邦查询 SQL repair 与降级留空机制；子查询门禁、平台重试预算与 JOIN 异常拦截。
* **WHERE 探查与空结果重试**：联邦 WHERE 探查接入 Schema；空结果筛选诊断与自动修正重试；修复空结果半句话截断，联邦子查询接入 `empty_filter`。
* **Schema 预检增强**：WHERE 条件样例探查；放行 SELECT 别名与 Oracle 伪列；补全 sql_gates 预检与 gate 分类单元测试。
* **意图与路由优化**：改进意图提取与数据集前置路由策略；优化查数与联邦总结话术（去技术化、业务化）；移除占比/比率结果异常门禁；避免 COUNT+SUM 汇总 SQL 被误判为诊断查询。

### 2. 📊 数据门户黄金报表体系
* **Portal 保存报表**：新增 `portal_saved_reports` 持久化与分享 API；支持消息渲染表格视图切换。
* **个人偏好与放大浏览**：收藏、置顶、运行历史与个人偏好；报表放大浏览体验优化。
* **共享与权限状态**：完善黄金报表共享、权限状态展示与模型删除确认。
* **沉淀入口优化**：优化黄金报表沉淀入口与自动填充；强化查数前置说明；Embed 操作栏体验改进。

### 3. 🗄️ SQL Server 数据源全链路支持
* **连接与适配器**：新增 SQL Server 数据源（ODBC / aioodbc）；支持表列表、DDL 导入与本地只读物理查询。
* **Docker 镜像**：Docker 镜像安装 SQL Server ODBC 驱动（unixODBC + msodbcsql18）。
* **ChatBI 方言修复**：修复 sqlglot 方言映射（`sqlserver` → `tsql`），使 `execute_sql_query` 可正常解析 T-SQL。
* **行数限制**：增强 SQL Server `TOP N` 行数限制与本地执行兜底；ChatBI 分页语法 repair 提示补充 T-SQL 说明。

### 4. 👥 第三方用户同步（重构）
* **用户名映射**：以用户名为两边系统映射主键；本地不存在则新增（ID 自增、API Key 自动生成），已存在则更新真实姓名、备注与扩展 JSON。
* **extra_data 聚合**：支持将来源表多个字段映射为 JSON 写入 `extra_data`。
* **配置与 UI**：数据源 + 表 + 字段对比映射；用户表可搜索选择；功能说明弹层；预览/同步支持提交当前表单配置（无需先保存）。
* **定时同步**：支持 hourly / daily / weekly 定时任务。

### 5. 🤖 子代理委派与 Office 文档工具
* **子代理委派**：子代理委派链路优化，减少重复调用与空返回；知识库上下文优化与知识库助手外网工具隔离。
* **Word / Excel 工具**：新增 Word/Excel 文档工具及生成文件下载能力；归一化生成文件下载链接。

### 6. 🎯 智能路由与 ChatBI 行级权限
* **路由分流**：优化智能路由层高置信度数据查询分流；ChatBI 元数据检索致命错误在 ReAct 内快速熔断拦截。
* **行级权限重写**：完善 ChatBI 行级权限 SQL 重写与工具日志提示；优化行级权限配置下拉样式。

### 7. 🏠 数据门户与缓存
* **Redis 缓存优化**：数据门户 Redis 按 `user_id` 单 Key 存储，缓存 TTL 延长至 90 天。
* **智能体中心**：支持智能体中心拖拽排序。

### 8. ⚙️ 平台管理与运维
* **品牌个性化**：新增品牌个性化配置（产品名、Logo、登录页 Tab 顺序、版权文案等）。
* **知识库总开关**：知识库功能总开关；关闭后管理页、检索测试与 `search_knowledge_base` 工具不可用。
* **主助手动态清单**：主助手欢迎语专家清单改为运行时动态注入 `{agent_roster}`。
* **Redis 诊断**：系统诊断支持 Redis 选择性清理与 Key 详情查看；修复窄栏下按钮文字折行。
* **元数据向量检索**：修复本地元数据 Redis 向量检索与 FT.INFO / FT.SEARCH 解析（含 binary 客户端兼容）。
* **系统配置**：修复系统配置只读误判；统一会话 ID 生成；增强 Schema 检索诊断日志。

### 9. 📄 文档与工程化
* **OpenSpec 归档**：归档 sub-agent-delegation 变更；补充路由语义证据、Office 工具与联邦门禁 CHECKLIST 条目。
* **apply-sql-native.sh**：修复 SQL 切分脚本；补充 AGENTS.md 代码已合协作说明。

---

## ⚠️ Breaking Changes & Migration Notes

> 从 v1.0.4 升级至 v1.0.5 时，请特别注意以下变更：

| 项目 | 说明 |
| :--- | :--- |
| **数据库变更** | 升级前须执行 `V82` ~ `V87` 六个增量脚本（见下方数据库升级说明）。 |
| **第三方用户同步** | 同步逻辑改为以**用户名**为映射主键（不再写入第三方 ID）；旧配置中的 `field_map.id` 已废弃，需重新保存字段映射。 |
| **SQL Server ChatBI** | 使用 `sqlserver_*` / `mssql_*` 数据源时，sqlglot 方言已统一为 `tsql`；T-SQL 语法（如 `TOP N`）需在 Prompt 中正确引导。 |
| **知识库总开关** | 默认启用；关闭后所有知识库相关入口与工具不可用，升级后请确认 `knowledge_base_enabled` 配置。 |
| **主助手 Prompt** | 执行 `V86` 后，主助手能力列表改为运行时注入，勿在 Prompt 中硬编码专家清单。 |
| **黄金报表表结构** | 新增 `portal_saved_reports` 及分享/偏好关联表，旧版无报表持久化能力。 |
| **Docker 镜像** | 若使用 SQL Server 数据源，需使用含 ODBC 驱动的新镜像或自行安装 `msodbcsql18`。 |

---

## 🗄️ Database Incremental Upgrades (数据库增量升级说明)

从 v1.0.4 升级至 v1.0.5 期间，平台数据库共引入了 **6 个**增量 SQL 升级脚本（存放于 [db-prod/](https://github.com/RandyChen1985/nanzi-ai-agent-platform/tree/main/db-prod) 目录下）：

| 脚本文件 | 核心变更内容 |
| :--- | :--- |
| **[V82-create_portal_saved_reports.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V82-create_portal_saved_reports.sql)** | 创建 `portal_saved_reports` 及分享目标关联表，支持黄金 SQL 报表持久化与分享。 |
| **[V83-create_portal_saved_report_user_prefs.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V83-create_portal_saved_report_user_prefs.sql)** | 创建 `portal_saved_report_user_prefs`，支持收藏、置顶、浏览与运行历史。 |
| **[V84-branding-config.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V84-branding-config.sql)** | 插入品牌个性化相关 `system_configs` 配置项。 |
| **[V85-third-party-user-sync-config.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V85-third-party-user-sync-config.sql)** | 插入 `third_party_user_sync_config` 默认配置项。 |
| **[V86-main-agent-dynamic-roster.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V86-main-agent-dynamic-roster.sql)** | 主助手 Prompt 能力列表改为 `{agent_roster}` 运行时动态注入。 |
| **[V87-knowledge-base-enabled-switch.sql](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/db-prod/V87-knowledge-base-enabled-switch.sql)** | 插入 `knowledge_base_enabled` 知识库总开关配置项。 |

> [!WARNING]
> 请在升级后通过执行 `./db-prod/apply-sql-native.sh` 脚本，将增量 SQL 自动、安全地导入到目标 MySQL 数据库中。

---

## 📦 Upgrade Guide

### 从 v1.0.4 升级

```bash
# 1. 拉取最新代码
git fetch origin && git checkout main && git pull origin main

# 2. 更新依赖
source .venv/bin/activate
pip install -r requirements.txt

# 3. 执行数据库迁移（V82 ~ V87）
./db-prod/apply-sql-native.sh

# 4. 重新编译前端并启动
cd frontend && npm install && npm run build && cd ..
./dev.sh
```

> [!TIP]
> 升级后若使用 SQL Server 数据源，请确认 Docker 镜像已包含 ODBC 驱动；第三方用户同步需在「用户管理 → 同步第三方用户」中重新配置字段映射。

---

## ✅ Test Checklist

升级后建议验证以下核心场景：

- [ ] **联邦查询**：跨数据集提问、WHERE 探查 repair、空结果自动重试、子查询门禁与 Trace 展示。
- [ ] **黄金报表**：Portal 保存/分享/运行报表、个人偏好（收藏/置顶）、表格视图切换。
- [ ] **SQL Server**：配置 `sqlserver_*` 数据源、ChatBI `execute_sql_query` 执行 T-SQL（含 `TOP N`）。
- [ ] **第三方用户同步**：配置数据源与字段映射、预览、手动/定时同步、extra_data JSON 写入。
- [ ] **Office 工具**：Word/Excel 生成与下载链接归一化。
- [ ] **知识库开关**：关闭总开关后管理页与检索工具不可用。
- [ ] **品牌个性化**：登录页 Tab 顺序、Logo 与产品名展示。
- [ ] **Redis 诊断**：Key 详情查看与选择性清理。
- [ ] **回归测试**：运行 `pytest tests/`，确保 ChatBI / 联邦 / 门户相关用例通过。

完整测试清单见 [tests/CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md)。

---

## 💾 Downloads / Assets

本项目 v1.0.5 发布版本关联的源码、Docker 镜像资产归档包及配置文件如下：

* 📦 **Source Code (zip)**: `nanzi-ai-agent-platform-1.0.5.zip`
* 📦 **Source Code (tar.gz)**: `nanzi-ai-agent-platform-1.0.5.tar.gz`
* 🐳 **Docker Image for Linux amd64 (x86_64)**: `nanzi-ai-agent_1.0.5_linux-amd64_*.tar`
* 🐳 **Docker Image for Linux arm64 (aarch64)**: `nanzi-ai-agent_1.0.5_linux-arm64_*.tar`
* ⚙️ **Docker Compose YAML file**: `docker-compose.yml`

🔗 **下载地址**: [GitHub Releases v1.0.5](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.5)

---

## 📋 Commit Log

| Hash | 描述 |
| :--- | :--- |
| `099181d` | feat: 重构第三方用户同步并增强 SQL Server 查询限制 |
| `519d9a8` | fix: 修复 SQL Server 数据源 sqlglot 方言映射导致查询解析失败 |
| `187df07` | fix: 修复 binary Redis 客户端下 FT.SEARCH 字典响应解析失败 |
| `a628687` | fix: 修复本地元数据 Redis 向量检索与 FT.INFO 解析 |
| `a097288` | fix: Docker 镜像安装 SQL Server ODBC 驱动 |
| `9186e45` | feat: 增强 Schema 检索诊断日志并优化知识库开关交互 |
| `3d82cca` | fix: 修复系统配置只读误判并统一会话 ID 生成 |
| `e4735da` | feat: 知识库总开关、local 元数据同步修复与主助手动态专家清单 |
| `3ca6bfd` | feat: 新增第三方用户同步功能 |
| `a628f6b` | fix: 修复 apply-sql-native.sh SQL 切分并补充代码已合协作说明 |
| `b92b94f` | feat: 新增品牌个性化配置并优化登录页 Tab 顺序 |
| `f1eee32` | feat: 新增 SQL Server 数据源全链路支持 |
| `325181e` | feat: 数据门户 Redis 按 user_id 单 Key 并延长缓存 TTL 至 90 天 |
| `84fc2c4` | fix: 修复系统诊断 Redis 操作按钮在窄栏下文字折行 |
| `89e95cc` | feat: 系统诊断支持 Redis 选择性清理与 Key 详情查看 |
| `597766e` | feat: 支持智能体中心拖拽排序，并优化行级权限配置下拉样式 |
| `fe2900f` | feat: 完善 ChatBI 行级权限重写与工具日志提示，并优化 Embed 操作栏 |
| `25bac5a` | feat: 优化黄金报表沉淀入口与自动填充，并强化查数前置说明 |
| `668efe4` | feat: 优化黄金报表体验并新增个人偏好与放大浏览 |
| `c4bbf73` | feat: 完善黄金报表共享与权限状态 |
| `d345dc3` | feat(chatbi): 新增 Portal 保存报表与分享功能，并支持消息渲染的表格视图切换 |
| `b974a32` | feat: 完善黄金报表与模型删除确认 |
| `7efccf3` | feat: 优化智能路由层高置信度数据查询分流逻辑，并修复 ChatBI 元数据检索致命错误在 ReAct 内的快速熔断拦截 |
| `39cf90e` | docs: add routing semantic evidence design |
| `3b0b53c` | fix(ai): 归一化生成文件下载链接并补充 Office 工具验收项 |
| `643d64b` | docs(ai): specify generated file link normalization |
| `b45bb5d` | feat(ai): 新增 Word/Excel 文档工具及生成文件下载能力 |
| `1fe17aa` | docs(ai): plan office document tools |
| `3455231` | docs(ai): specify office document tools |
| `106a20d` | fix(ai): harden sub-agent delegation |
| `5dc3c79` | fix(ai): 优化子代理委派链路，减少重复调用与空返回 |
| `9f45a06` | fix(chatbi): 避免 COUNT+SUM 汇总 SQL 被误判为诊断查询 |
| `724bac9` | docs(openspec): 归档 sub-agent-delegation 变更并更新主需求规范 |
| `9560f5c` | feat(ai): 子代理委派、知识库上下文优化与知识库助手外网工具隔离 |
| `09cd860` | fix(chatbi): 移除占比/比率结果异常门禁 |
| `b2ca382` | docs(tests): 补全 ChatBI 联邦门禁与平台重试相关 CHECKLIST 条目 |
| `a77d3ec` | feat(chatbi): 联邦子查询门禁、平台重试预算与 join 异常拦截 |
| `963d1ec` | fix(chatbi): 修复空结果半句话截断，联邦子查询接入 empty_filter |
| `bc9f89e` | feat(chatbi): 联邦 WHERE 探查与空结果筛选重试策略优化 |
| `119df3e` | feat(chatbi): WHERE 探查接入 Schema 并增强自动修复 |
| `f0eeec0` | test(chatbi): 补全 sql_gates 预检与 gate 分类单元测试 |
| `290250c` | fix(chatbi): SQL 预检放行 SELECT 别名与 Oracle 伪列 |
| `48d38f7` | feat(chatbi): WHERE 条件样例探查与 Schema 预检增强 |
| `abb278c` | fix(chatbi): 修复联邦 memory_join repair 解析污染与降级列结构 |
| `78be784` | fix(chatbi): 优化联邦查询 SQL repair 与降级留空机制 |
| `ba15808` | refactor(chatbi): 改进意图提取与数据集前置路由策略 |
| `d0e771a` | style: 优化查数与联邦查询最终总结及报错提示词文案 |
| `c263e37` | feat(chatbi): 联邦 memory_join 计划阶段自动修正与 preflight sqlglot 列校验 |
| `61681d9` | feat(chatbi): 引入 SqlQueryBinding 并加强联邦 memory_join 列校验 |

---

## 🤝 Contributors

感谢所有参与 v1.0.5 版本发布的开发者！

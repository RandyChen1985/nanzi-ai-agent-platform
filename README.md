> **项目声明**  
> 本项目为**个人开源**，供自由学习交流，遵循 [MIT](LICENSE) 开源协议，可自由分发。  
> 原项目名称「云枢」与其他企业项目重名，为避免混淆，现更名为「NanZi」。  
> 「NanZi」来自我一直使用的网名（南孜），取「孜孜不倦」之意，寓意 AI 持续学习与进化。

# NanZi · 智能体平台 (NanZi AI Agent Platform)

**简体中文** | [English](README_EN.md)

> **企业级 AI 智能体编排与执行平台**  
> *Connect Data. Orchestrate Intelligence.*



[![Python](https://img.shields.io/badge/Python-3.11-blue.svg?logo=python&logoColor=white)](https://www.python.org/) [![AgentScope](https://img.shields.io/badge/AgentScope-2.x-7C3AED.svg)](https://github.com/agentscope-ai/agentscope) [![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/) [![TailwindCSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/) [![ClickHouse](https://img.shields.io/badge/ClickHouse-Ready-FFCC00.svg?logo=clickhouse&logoColor=black)](https://clickhouse.com/) [![Redis](https://img.shields.io/badge/Redis-Active-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/) [![MCP](https://img.shields.io/badge/MCP-Supported-orange.svg?logo=anthropic)](https://modelcontextprotocol.org/) [![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

> 📖 **实战连载**：[NanZi 开源智能体平台实战连载](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect)（架构 · 安装 · 智能体配置 · ChatBI · 工具箱 · MCP）

![Promo](docs/images/nanzi-platform-promo-16x9.png)
![Overview](docs/images/nanzi-platform-overview-16x9.png)

**NanZi 智能体平台** 是专为企业级复杂场景打造的 AI 智能中枢。

平台核心聚焦于以下能力矩阵：
*   💬 **深度交互式对话 (Dialogue & Co-Agent)**：极速流式响应，支持 **智能委派（默认进入 Main）** 与 **专家模式 / @提及直选**、多专家协同。内置 **工具预检** 促发模型主动调用工具；支持 `ask_user_question` 智能提问卡（单选/多选/输入）、**Todo 任务清单** 分步执行与常驻跟踪；主助手支持 **Skill 自动扫描** 与权限挂起恢复。
*   🛡️ **多策略安全沙箱与隔离执行 (Multi-Policy Sandbox)**：原生支持 **Local**（本机进程）、**Docker**（私有容器隔离）、**E2B**（云端安全沙箱）、**SSH**（远端安全通道）四大执行策略；Docker 容器与宿主机工作区**同绝对路径挂载**，支持在代码画布中直接打开预览并保存回宿主机物理文件；支持镜像预构建、空闲 30 分钟自动回收（Idle Reaper）、优雅停机清理及输入框浮标面板一键探测与**秒级运行时长监控**。
*   🌐 **持久化浏览器会话与实时接管面板 (Persistent Browser & Live Takeover)**：服务端持久化浏览器会话与全套自动化工具（网页访问、点击、输入、拟人滑块轨迹拖拽、按键、滚动、截图与多标签）；前端右侧提供**实时 Web 交互面板**，支持快照串流渲染与无缝人机协同交互接管。
*   📊 **原生企业级 ChatBI 与自愈分析 (ChatBI & Self-Healing)**：数据源与元数据管理、案例集 Few-Shot、SQL 自愈与 **sql_plan 结构化计划**；**我的数据门户**（`/dataset_portal`）个性化导航；支持直连物理 SQL 与黄金报表暂存订阅。
*   🧠 **长期记忆与跨会话回顾 (Memory & LTM)**：LTM 偏好注入 + 内置 `memory_search` 按需检索会话/每日摘要；记忆管理中心提供向量检索运维与数据治理；全链路 Redis 会话记忆与压缩日志 **TTL 全面升级为 30 天**。
*   📊 **上下文分项拆解观测与溢出智能压缩 (Context Observability & Compaction)**：精准实时拆解 System Prompt、Tools Schema、Memory/History 与 Current Turn 四大项 Token 占比；支持自动水位线告警与两阶段结构化压缩（`_structured_tool_block` 精准提炼、保留多模态附件标记）。
*   🧩 **代码画布与工作区执行 (Code Canvas & Workspace)**：支持 Python / Shell 代码的流式运行、停止、输出回传与私有工作区文件预览；支持 `publish_generated_file` 智能工件发布与有效期管理。
*   📚 **可视化知识库管理中心 (RAG & Knowledge Hub)**：非结构化文档树形管理、召回测试、语义合并；**Knowledge 执行器**在 ReAct 前自动检索并注入引用。
*   🔌 **开放插件生态 (MCP Integration)**：遵循 Anthropic Model Context Protocol 标准，无缝连接 Jira、Email、GitLab 等外部生产力系统。
*   🔌 **灵活的嵌入式 (Embed) 集成**：通过嵌入式 Chat SDK 快速集成至企业业务系统，对接现有鉴权体系，实现租户隔离、RBAC 权限与水印安全合规。
*   ⏰ **自动化任务中心与多通道推送 (Task Scheduler & Notifications)**：APScheduler + Redis 执行期锁，支持通过环境变量指定唯一调度节点，模拟智能体身份自主执行周期（Cron）、定时与间隔任务；支持 **多通道智能触达**（企业微信、钉钉、飞书、邮件、自定义 Webhook 及站内信通知中心）；内置自动剥离思考过程（纯净业务摘要推送）、超长截断保护与黄金报表异常阈值告警。
*   🛠️ **全链路 Debug 与 Trace**：决策链、工具调用、SQL 计划卡片可视化；结构化查数结果 CSV/Excel 导出。
*   ⚙️ **标准化 API 开放**：标准化 V1 API 接口，支持外部系统通过 API 直接调用智能体编排与执行能力。
*   🎯 **提示词工厂 (Prompt Factory)**：系统提示词版本管理与草稿（`architech/prompts/`），生产行为可控可审计。

---

## 🏛️ 系统架构 (Architecture)

![Architecture](docs/images/nanzi-platform-architecture-16x9.png)

![End-to-End Flow](docs/images/e2e.png)

```text
┌──────────────────────────────────────────────────────────┐
│                     NanZi 智能体平台                     │
└───────────────┬────────────────────────────┬─────────────┘
                │                            │
   [ 嵌入式聊天 SDK ]                [ 管理控制后台 ]
   (Embed Chat SDK)                 (Admin Console)
                │                            │
                └─────────────┬──────────────┘
                              │ SSE/HTTP
┌─────────────────────────────▼────────────────────────────┐
│                  核心网关 (Portal Gateway)               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ 统一鉴权 │  │ Main 委派 │  │ 任务调度 │  │ 审计回溯 │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└─────────────────────────────┬──────────────┬─────────────┘
                              │              │ (状态与队列)
                              │        ┌─────▼─────┐
                              │        │   Redis   │
                              │        └───────────┘
┌─────────────────────────────▼────────────────────────────┐
│                智能体专家集群 (Expert Pool)               │
│   ┌──────────────┐      ┌──────────────┐     ┌─────────┐  │
│   │  ChatBI 专家 │      │ RAG 知识专家  │     │ 插件助手│  │
│   └──────┬───────┘      └──────┬───────┘     └───┬─────┘  │
└──────────┼─────────────────────┼─────────────────┼────────┘
           │ (ReAct 循环)        │ (托管路由)      │ (工具链调用)
┌──────────▼─────────────────────▼─────────────────▼────────┐
│                智能体运行引擎 (Execution Engines)         │
│  ┌──────────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ AgentScope ReAct │  │ RAGFlow Agent│  │  OpenClaw🦞 │  │
│  │ (Loop & 自愈SQL) │  │  (托管智能体) │  │ (AUTH上下文) │  │
│  └────────┬─────────┘  └──────┬───────┘  └──────┬──────┘  │
└───────────┼───────────────────┼─────────────────┼─────────┘
            │                   │                 │
┌───────────▼───────┐ ┌─────────▼─────┐ ┌─────────▼────────┐
│ 企业多源数据仓/DB │ │ RAGFlow 知识库 │ │   MCP Server     │
│ (Oracle/CK/MySQL) │ │ (非结构化数据) │ │ (外部系统/API)    │
└───────────────────┘ └───────────────┘ └──────────────────┘
```

---

## 🖼️ 界面预览 (Interface Snapshots)

| 📊 仪表盘概览 (Overview Dashboard) | 💬 智能助手对话 (AI Chat) |
| :---: | :---: |
| ![仪表盘概览](docs/snapshot/overview.png) | ![智能助手](docs/snapshot/ai-chat.png) |
| **🧠 长期记忆与无感回忆 (Memory & LTM)** | **🔍 记忆管理控制台 (Memory Manage)** |
| ![记忆与偏好](docs/snapshot/chat-with-memory.png) | ![记忆管理控制台](docs/snapshot/memory-manage.png) |
| **🛠️ 决策链路调试 (Trace Timeline)** | **📚 知识库工作台 (Knowledge Hub)** |
| ![调试链路](docs/snapshot/chat-debug.png) | ![知识库](docs/snapshot/knowledge.png) |
| **🤖 智能体编排 (Agent Studio)** | **📝 提示词游乐场 (Prompt Playground)** |
| ![智能体](docs/snapshot/bot-list.png) | ![提示词](docs/snapshot/prompt_studio.png) |
| **🔌 直连物理数据源 (Data Sources)** | **📊 元数据智能构建 (Metadata)** |
| ![数据源](docs/snapshot/datasource.png) | ![元数据](docs/snapshot/meta-list.png) |
| **⚡ 智能体动态技能 (Agent Skills)** | **⚙️ 系统管理设置 (System Settings)** |
| ![技能](docs/snapshot/skills-manage.png) | ![系统配置](docs/snapshot/system.png) |




---

## 🌟 核心能力 (Core Capabilities)

![NanZi 核心能力矩阵](docs/images/core.png)

### 1. 🧠 多引擎与混合编排 (Multi-Engine & Hybrid Orchestration)
*   **智能委派**：未指定智能体时直接进入 `Main`，由 Main 根据任务判断直接回答，或通过 `sub_agent_call` / `sub_agent_batch_call` 按需委派已授权专家；不再增加外层语义路由 LLM。
*   **专家直选**：Embed 专家模式、`agent_id` 或 `@` 提及保持直达指定智能体；指定专家后仍可由该专家继续委派其他子代理。
*   **AgentScope ReAct**：Assistant / ChatBI / Knowledge 基于 AgentScope Agent + Toolkit，闭环调度本地工具，支持权限挂起与恢复。
*   **主助手增强**：工具预检（按绑定工具相关度促发调用）、Skill 自动扫描、反业务数据幻觉 Guard（可一键切换 ChatBI）。
*   **思考模型兼容层**：支持 DeepSeek-R1、Kimi 等思考模型展开与 6 级 `reasoning_effort` 调优，自动挂载 `tool_choice_for_model` 确保工具稳定触发。
*   **RAGFlow 托管 Agent**：对接 RAGFlow 在线托管智能体，复用其检索与流式对话能力。
*   **OpenClaw🦞 安全网关**：通过 `AUTH_CONTEXT` 透传用户身份、频道及可访问数据集，保障租户隔离。

### 2. 🛡️ 多策略安全沙箱与隔离执行 (Multi-Policy Sandbox & Isolation)
*   **四大沙箱策略**：原生支持 `Local`（本机安全隔离）、`Docker`（私有容器隔离）、`E2B`（云端安全沙箱）、`SSH`（远程服务器安全通道）。
*   **Docker 绝对路径同径挂载**：用户工作区直接挂载进容器同绝对路径，自动将 `/workspace/...` 容器内逻辑绝对路径转义映射至宿主机物理文件，支持右侧代码画布直接打开、预览并保存回物理磁盘。
*   **沙箱生命周期自动化**：支持后台 30 分钟空闲自动销毁（Idle Reaper）、服务重启优雅停机清理，确保无残留孤儿容器。
*   **输入框浮标控制台**：右上角上下文浮标无缝聚合 Docker 沙箱状态（🟢已运行/🟡启动中/🔴失败/⚪未启动）、当前分配容器 ID、**实时秒级运行时长（`5分20秒`）**以及手动「启动容器/重试启动/常驻手动刷新」控制。

### 3. 🌐 服务端持久化浏览器会话与实时接管 (Persistent Browser & Live Takeover)
*   **完整自动化套件**：支持打开网页、元素点击、表单输入、拟人轨迹滑块拖拽、智能等待、按键、全页滚动、文件上传、截图与多标签页管理。
*   **右侧 Web 交互面板**：前端支持持久化浏览器面板，快照串流实时呈现；支持用户随时点击接管人工输入或验证码交互，人机协同闭环。

### 4. 📊 上下文智能预算与溢出压缩 (Context Management & Observability)
*   **四维分项拆解与可视化**：精确估算并呈现 System Prompt、Tools Schema、Memory/History 与 Current Turn 的 Token 占比条形图。
*   **智能溢出压缩**：触发物理窗口阈值时自动触发两阶段结构化压缩，提炼工具调用产物（`_structured_tool_block`）并保留重要多模态图片标识。
*   **30 天长期记忆缓存**：全链路 Redis 记忆会话、压缩日志与生成工件下载链接全面升级为 30 天长效保存。

### 5. 📊 智能数仓分析 (ChatBI & Self-Healing)
*   **Text-to-SQL 闭环**：元数据注入 + Schema 门禁 + 多层 SQL 护栏，自然语言直查业务库。
*   **我的数据门户**：系统指令 `/dataset_portal`（兼容旧 `/dataset_menu`），按权限生成数据集导航与 quick 追问。
*   **案例集与 Few-Shot**：经验库审核入库、相似案例动态注入提示词头部，提升专有 SQL 准确率。
*   **自愈与计划推演**：SQL 报错自动修复轮次；可选 `enable_sql_plan` 要求高风险查询先输出结构化 `<sql_plan>`（前端卡片展示）。
*   **连续分析工作流**：非查数请求可本地回答或无感委派；结果栈支持引用、条件继承下钻、混合任务串行执行，并区分结果分析、呈现、动作和新查数。
*   **元数据业务导航**：字段与口径回答基于授权 Schema 生成指标、维度和可执行问题；澄清候选不使用模型臆造字段。
*   **分析交付闭环**：查询结果可一键生成证据化 Markdown/Word 业务简报，或转为黄金报表订阅，配置阈值、变化率、连续命中和无数据告警。
*   **数据源管理**：可视化管理 Oracle / ClickHouse / MySQL 等连接，支持 DDL 抓取与连接别名唯一校验；支持黄金报表暂存与直连物理 SQL 执行。

### 6. 🔌 开放插件生态 (MCP Integration)
*   **原生支持 MCP**：遵循 Anthropic 的 Model Context Protocol。
*   **无限扩展**：无需修改核心代码，即可通过 MCP 服务器连接 Jira、Email、GitLab 等外部生产力工具。

### 7. 📚 深度知识增强与集成 (RAG & Knowledge Hub)
*   **一站式知识库管理**：树形文档管理、切片预览、召回测试、语义合并与生命周期审计。
*   **Knowledge 执行器**：对话中自动 `search_knowledge_base` 预检索，ReAct 阶段注入引用卡片，空召回/无引用回答可拦截。
*   **RAGFlow 托管路径**：亦可一键对接 RAGFlow 托管知识智能体，复用外部检索与流式底座。

### 8. 🛠️ 企业级配套与安全审计 (Enterprise Toolkit & RBAC)
*   **自动化任务中心与多通道推送**：APScheduler + Redis 执行期锁，支持通过环境变量指定唯一调度节点，模拟智能体身份执行周期（Cron）与定时任务；支持**企微、钉钉、飞书、邮件、Webhook 与站内信 (Inbox)** 多通道智能触达，自带思考内容清洗（正文纯净推送）与超长截断保护。
*   **黄金报表智能告警**：支持 ChatBI 报表定时巡检，配置阈值告警、变化率偏离、连续命中与无数据告警并自动推送通知。
*   **多提供商模型管理**：内置 OpenAI, Azure, DeepSeek, Kimi, 智谱 AI, 硅基流动, 阿里云百炼, 火山引擎 (Ark/豆包), Ollama 等模型预设与 Endpoint 智能版本号规范化。
*   **平台时区配置**：系统调度和未单独指定时区的订阅按 `platform_timezone` 解释时间，默认 `Asia/Shanghai`。
*   **精细化 RBAC**：用户、角色、菜单与元素级权限，读写操作隔离。
*   **SSO 与脱敏**：SSO 登录可后台开关；审计日志自动脱敏密码、API Key 等敏感字段。
*   **安全审计水印**：Embed 窗口背景水印（用户名+时间戳或自定义文案），防截屏外泄。
*   **链路可视化与导出**：Trace 时间线调试；查数结果 CSV/Excel 导出（utf-8-sig）。

---

## 🔄 智能体工作流 (Execution Flow)

系统遵循 **「确定入口 → 委派/分发 → 执行 → 聚合」** 链路：

1.  **入口解析**：未传 `agent_id` 时直接加载默认 `Main`；传入 `agent_id`、`agent_name`、`version_id` 或使用 `@` / 专家模式时，直接加载指定专家。
2.  **智能委派**：Main（或当前指定的父专家）结合自身 Prompt、能力目录、工具与权限门禁，决定直接回答，或调用 `sub_agent_call` / `sub_agent_batch_call` 委派子任务。
3.  **执行分发 (Dispatcher)**：按最终智能体的引擎与能力选择 **Knowledge** / **ChatBI (DataQuery)** / **Assistant** / RAGFlow / OpenClaw 执行器；ChatBI 内部分诊新查数、结果分析/呈现/动作、元数据、非查数委派或澄清等。
4.  **动态执行 (ReAct)**：AgentScope「思考-行动-观察」循环，工具权限挂起、SQL 护栏、工具预检等按执行器生效。
5.  **结果合成 (Synthesis)**：多 Agent 场景由 Synthesizer 聚合；单 Agent 流式 SSE 返回正文、日志与引用。

详见 [architech/design/chat/CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) · [智能委派与专家直选设计](architech/design/AGENT_ROUTING_DESIGN.md)

---

## 📚 文档与架构 (Documentation)

| 文档 | 说明 |
|------|------|
| [微信实战连载](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect) | 架构分层、安装部署、智能体配置、ChatBI、工具箱、MCP |
| [HOW_TO_INSTALL.md](HOW_TO_INSTALL.md) | 安装部署与 FAQ |
| [architech/README.md](architech/README.md) | 架构文档索引 |
| [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) | 聊天端到端流程 |
| [PROMPT_LAYERS.md](architech/design/chat/PROMPT_LAYERS.md) | 提示词分层与注入 |
| [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md) | 智能委派与专家直选设计 |
| [mcp-business-integration-authentication-design.md](architech/design/mcp-business-integration-authentication-design.md) | MCP 业务集成认证总体架构、安全策略与调用流程 |
| [mcp-platform-inbound-service-design.md](architech/design/mcp-platform-inbound-service-design.md) | NanZi 平台级 MCP 对外服务、OAuth2 授权、管理页与元数据方法方案 |
| [api_integration_guide.md](docs/md/api_integration_guide.md) | Embed / V1 API 集成 |
| [mcp_user_context_integration_guide.md](docs/md/mcp_user_context_integration_guide.md) | 自有 MCP UserContext 接入、JWKS 验签及 Python / Java 示例 |
| [mcp_echo_test_server.md](docs/md/mcp_echo_test_server.md) | 平台级 MCP Echo 测试服务创建、智能体挂载与认证诊断 |
| [code_canvas_and_workspace_guide.md](docs/md/code_canvas_and_workspace_guide.md) | 代码画布、工作区文件与执行 API |
| [ai_agent_gating_contract.md](docs/md/ai_agent_gating_contract.md) | Agent 门控契约 |
| [tests/CHECKLIST.md](tests/CHECKLIST.md) | 自动化测试验收清单 |

---

## 📂 项目结构 (Structure)

```text
.
├── app/                  # 后端核心代码 (FastAPI)
│   ├── api/              # API 接口层 (Portal 运营端与 V1 客户端 API)
│   ├── services/         # 业务引擎服务 (Auth 鉴权、RAG 知识、MCP 插件服务)
│   │   └── ai/           # 🤖 AI 编排中心 (AgentScope Runner、OpenClaw 执行器与意图分发器)
│   └── models/           # SQLAlchemy 数据库 ORM 映射模型
├── frontend/             # 前端管理后台与内嵌聊天 SDK 工程 (Vue 3 + Tailwind)
├── .agent/               # Agent 专属自动化开发技能与工作流程配置 (opsx 等)
├── architech/            # 顶层架构设计规范与系统提示词 (Prompts) 管理控制
├── db-prod/              # MySQL 历史版本迁移与 SQL 升级脚本 (V0-VNN)
├── db-prod-pg/           # PostgreSQL 基线与幂等版本迁移脚本
├── docker/               # 容器化打包与一键 Docker-compose 部署方案
├── scripts/              # 运维辅助与工具脚本 (一键开发启动、数据同步、重部署工具)
├── tests/                # 自动化测试套件与测试验收清单 (CHECKLIST.md)
└── openspec/             # 接口规范变更与接口协议追踪 (OpenSpec)
```


---

## 🚀 快速开始

### 🐳 Docker 部署 (推荐)

**1. 配置环境**
```bash
cd docker
cp ../env.example .env   # 配置数据库、Redis、ENCRYPTION_KEY 等
```

平台主库默认使用 MySQL；如需使用 PostgreSQL，编辑 `docker/.env`：

```dotenv
DATABASE_TYPE=postgresql
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5432
POSTGRES_DB=nanzi_ai_agent_platform
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<password>
```

容器中的主库 Host 不能填写 `localhost` 或 `127.0.0.1`，应使用宿主机地址、
`host.docker.internal` 或 Docker 网络中的服务名。详细初始化步骤见
[HOW_TO_INSTALL.md](HOW_TO_INSTALL.md) 和 [db-prod-pg/README.md](db-prod-pg/README.md)。

**主库初始化**

MySQL 使用 `db-prod/apply-sql.sh`；PostgreSQL 使用独立的幂等迁移入口：

```bash
# 返回项目根目录执行
cd ..
./db-prod-pg/apply-sql.sh
```

该脚本会按 `db-prod-pg/` 中当前存在的 `V0`～`V14` 版本顺序执行 PostgreSQL 迁移，并在首次初始化后询问是否创建管理员。以后新增迁移时，以该目录实际文件为准，不要把 PostgreSQL 迁移和 `db-prod/` 的 MySQL 迁移混用。
PostgreSQL 不使用 MySQL 的 `INIT-USER-ADMIN.sql` 固定凭证；如需手动维护管理员，可执行：

```bash
./db-prod-pg/create-admin-user.sh
./db-prod-pg/create-admin-key.sh
./db-prod-pg/reset-admin-password.sh
```

**2. 构建镜像并导出 tar**

| 脚本 | 目标环境 |
| :--- | :--- |
| `./build_linux_x86.sh` | x86_64 Linux 服务器（最常见） |
| `./build_linux_arm.sh` | ARM64 Linux（鲲鹏 / Ampere 等） |
| `./build_native.sh` | 本机原生架构，仅用于本地试跑 |

```bash
# 若上一步返回了项目根目录，先进入 docker 目录
cd docker
# 生产环境（x86 服务器）— Mac 上打 x86 包也用此脚本
./build_linux_x86.sh
```

产物输出至 **`docker/release/`**，例如 `nanzi-ai-agent_linux-amd64_20250527.tar`。离线部署可在目标机执行 `docker load -i docker/release/xxx.tar`。

> Mac（Apple Silicon）部署到 x86 服务器时，务必使用 `build_linux_x86.sh`，不要用 `build_native.sh`。首次跨平台构建拉取基础镜像时可能较长时间无新日志，属正常现象。

**若提示 `docker buildx` 不可用**（Homebrew docker + Colima 常见，`cli-plugins` 仍指向已卸载的 Docker Desktop）：

```bash
cd docker
./install-buildx.sh
./build_linux_x86.sh
```

详见 [docker/README.md](docker/README.md) · [docker/README_EN.md](docker/README_EN.md)

**3. 启动服务**
```bash
./start-nanzi-ai-agent.sh
```

### 🛠️ 开发与部署工具

#### 1. 本地一键开发联调 (强烈推荐)
对于日常本地开发，推荐使用项目根目录下的一键集成脚本：
```bash
./dev.sh
```
该脚本首次运行会自动检测并安装 `uv`，准备 Python 3.11、创建 `.venv` 并安装后端依赖；后续仅在 `requirements.txt` 变化时更新依赖。随后脚本会停止旧的 `API_SERVICE_PORT` 进程（默认端口 `8001`）、编译前端（跳过类型检查以提速）并以前台 `reload` 模式拉起后端 FastAPI 服务，您可以在当前终端中实时查看联调日志与输出。

如需后台运行，可使用以下生命周期命令：

```bash
# 后台启动，PID 保存到 .dev-server.pid
./dev.sh -d

# 查看 PID、端口监听和 /health 健康状态
./dev.sh status

# 优雅停止后台服务，超时后自动强制停止
./dev.sh stop
```

`status` 和 `stop` 不会重新安装依赖或编译前端；端口监听探测优先使用 `lsof`，缺失时自动回退到 `ss`/`fuser`。脚本可识别以绝对/相对 `.venv` 路径或 `python3 -m uvicorn` 等形态启动的本项目 Uvicorn；若仍无法确认归属（或确实被其他进程占用），会列出监听 PID 及命令行并拒绝误杀，便于人工核对。

启动顶部还会打印 uv、Python 目标版本、虚拟环境、PyPI 镜像、`DATABASE_TYPE`、数据库地址和 Redis 地址等配置摘要；数据库和 Redis 密码不会单独打印。这些信息仅用于确认当前配置，不代表数据库或 Redis 连通性测试已经通过。

> **运行前的环境准备（`.env`）**：脚本首次运行前必须先编辑 `.env` 配置文件。项目根目录提供了模板 `env.example`，请先基于它创建 `.env` 并填写为你的实际环境：
> ```bash
> cp env.example .env
> ```
> 然后按需修改 `.env` 中的关键项：
> - **数据库**：`DATABASE_TYPE`（`mysql` 默认 / `postgresql`），以及对应的 `MYSQL_*` 或 `POSTGRES_*` 主机、端口、库名、账号与密码；
> - **Redis**：`REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`、`REDIS_PASSWORD`（无密码时留空）；
> - **任务调度**：`TASK_SCHEDULER_ENABLED`（默认 `true`）；多节点部署时只在一个节点开启，其他 API 节点设为 `false`；
> - **加密密钥**：`ENCRYPTION_KEY`（API Key 的对称加密密钥，可保留模板默认值，生产环境务必更换为独立的 Fernet Key）；
> - **可选集成**：SSO、RAGFlow、Jira 等信息按需填写。

一键脚本仍需要本机具备 Node.js/npm，并且需要提前准备 `.env`、数据库和 Redis；uv、Python 与 Python 依赖的首次下载需要网络。若 PyPI 镜像需要调整，可通过 `PYPI_INDEX_URL` 覆盖默认的清华镜像地址。

---

## 🤝 贡献指南

1.  **分支规范**: 基于 `main` 分支开发，功能分支命名格式 `feature/your-feature-name`。
2.  **提交信息**: 必须使用 **中文** 编写 Commit Message，清晰描述变更内容。
3.  **测试验收**: 新增功能时，请更新 `tests/CHECKLIST.md`。

---

## 💬 联系与交流

如果您在使用过程中有任何疑问、功能建议，或者想要获取更多技术资讯，欢迎扫码关注我们的微信公众号，或加入微信交流群；亦可阅读 [NanZi 开源智能体平台实战连载](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect)：

<table>
  <tr>
    <td align="center">
      <img src="docs/images/weixin.png" alt="微信公众号" width="200" /><br/>
      <sub>微信公众号</sub>
    </td>
    <td align="center">
      <img src="docs/images/weixin-group.png" alt="微信交流群" width="200" /><br/>
      <sub>微信交流群（7天内有效）</sub>
    </td>
  </tr>
</table>

扫码进群获取平台免费体验账号和地址。

---

## 📄 许可证

本项目采用 MIT 开源许可证，允许自由使用、复制、修改、合并、发布、分发、再许可及销售本软件副本。

---
Copyright © 2025-2026 Randy Chen <cexlong@gmail.com>. All Rights Reserved.

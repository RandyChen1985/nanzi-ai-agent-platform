# 南孜 · 智能体平台 (NanZi AI Agent Platform)

**简体中文** | [English](README_EN.md)

> **企业级 AI 智能体编排与执行平台**  
> *Connect Data. Orchestrate Intelligence.*



[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/) [![AgentScope](https://img.shields.io/badge/AgentScope-2.x-7C3AED.svg)](https://github.com/agentscope-ai/agentscope) [![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/) [![TailwindCSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/) [![ClickHouse](https://img.shields.io/badge/ClickHouse-Ready-FFCC00.svg?logo=clickhouse&logoColor=black)](https://clickhouse.com/) [![Redis](https://img.shields.io/badge/Redis-Active-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/) [![MCP](https://img.shields.io/badge/MCP-Supported-orange.svg?logo=anthropic)](https://modelcontextprotocol.org/) [![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)


![Promo](docs/images/nanzi-platform-promo-16x9.png)
![Overview](docs/images/nanzi-platform-overview-16x9.png)

**南孜智能体平台** 是专为企业级复杂场景打造的 AI 智能中枢。

平台核心聚焦于以下能力矩阵：
*   💬 **深度交互式对话 (Dialogue & Co-Agent)**：极速流式响应，支持自动路由与 **专家模式 / @提及直选**、多专家协同。内置 **工具预检** 促发模型主动调用已绑定工具；主助手支持 **Skill 自动扫描** 与权限挂起恢复；支持快捷指令、多模态附件与 Vision 识图。
*   🧠 **长期记忆与跨会话回顾**：LTM 偏好注入 + 内置 `memory_search` 按需检索会话/每日摘要；记忆管理中心提供向量检索运维与数据治理。
*   🔌 **灵活的嵌入式 (Embed) 集成**：通过嵌入式 Chat SDK 快速集成至企业业务系统，对接现有鉴权体系，实现租户隔离与安全合规。
*   📊 **原生企业级 ChatBI**：数据源与元数据管理、案例集 Few-Shot、SQL 自愈与 **sql_plan 结构化计划**；**我的数据门户**（`/dataset_portal`）个性化导航；支持直连物理 SQL 与黄金报表暂存。
*   🤝 **开箱即用的主流生态集成**：对接 **RAGFlow** 托管智能体与知识库；集成 **OpenClaw🦞** 大模型安全网关，透传用户身份与数据集权限上下文。
*   📚 **可视化知识库管理中心**：非结构化文档树形管理、召回测试、语义合并；**Knowledge 执行器**在 ReAct 前自动检索并注入引用。
*   🛠️ **全链路 Debug 与 Trace**：决策链、工具调用、SQL 计划卡片可视化；结构化查数结果 CSV/Excel 导出。
*   ⚙️ **API 与分布式调度**：标准化 V1 API；APScheduler + Redis 任务中心，模拟智能体身份执行周期任务。
*   🎯 **提示词工厂 (Prompt Factory)**：系统提示词版本管理与草稿（`architech/prompts/`），生产行为可控可审计。

---

## 🏛️ 系统架构 (Architecture)

![Architecture](docs/images/nanzi-platform-architecture-16x9.png)

```text
┌──────────────────────────────────────────────────────────┐
│                      南孜智能体平台                      │
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
│  │ 统一鉴权 │  │ 意图路由 │  │ 任务调度 │  │ 审计回溯 │  │
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

### 1. 🧠 多引擎与混合编排 (Multi-Engine & Hybrid Orchestration)
*   **智能路由**：未指定智能体时，先走问候/联网/ChatBI 会话粘性等启发式短路，再 LLM 语义路由；支持多意图并行与 Synthesizer 聚合。
*   **专家直选**：Embed 专家模式、`agent_id` 或 `@` 提及可跳过自动路由，直达指定智能体。
*   **AgentScope ReAct**：Assistant / ChatBI / Knowledge 基于 AgentScope Agent + Toolkit，闭环调度本地工具，支持权限挂起与恢复。
*   **主助手增强**：工具预检（按绑定工具相关度促发调用）、Skill 自动扫描、反业务数据幻觉 Guard（可一键切换 ChatBI）。
*   **RAGFlow 托管 Agent**：对接 RAGFlow 在线托管智能体，复用其检索与流式对话能力。
*   **OpenClaw🦞 安全网关**：通过 `AUTH_CONTEXT` 透传用户身份、频道及可访问数据集，保障租户隔离。


### 2. 📊 智能数仓分析 (ChatBI & Self-Healing)
*   **Text-to-SQL 闭环**：元数据注入 + Schema 门禁 + 多层 SQL 护栏，自然语言直查业务库。
*   **我的数据门户**：系统指令 `/dataset_portal`（兼容旧 `/dataset_menu`），按权限生成数据集导航与 quick 追问。
*   **案例集与 Few-Shot**：经验库审核入库、相似案例动态注入提示词头部，提升专有 SQL 准确率。
*   **自愈与计划推演**：SQL 报错自动修复轮次；可选 `enable_sql_plan` 要求高风险查询先输出结构化 `<sql_plan>`（前端卡片展示）。
*   **澄清短路**：非查数/寒暄类请求在分类层直接澄清，避免误触发查库。
*   **数据源管理**：可视化管理 Oracle / ClickHouse / MySQL 等连接，支持 DDL 抓取与连接别名唯一校验；支持黄金报表暂存与直连物理 SQL 执行。


### 3. 🔌 开放插件生态 (MCP Integration)
*   **原生支持 MCP**：遵循 Anthropic 的 Model Context Protocol。
*   **无限扩展**：无需修改核心代码，即可通过 MCP 服务器连接 Jira、Email、GitLab 等外部生产力工具。

### 4. 📚 深度知识增强与集成 (RAG & Knowledge Hub)
*   **一站式知识库管理**：树形文档管理、切片预览、召回测试、语义合并与生命周期审计。
*   **Knowledge 执行器**：对话中自动 `search_knowledge_base` 预检索，ReAct 阶段注入引用卡片，空召回/无引用回答可拦截。
*   **RAGFlow 托管路径**：亦可一键对接 RAGFlow 托管知识智能体，复用外部检索与流式底座。

### 5. 🛠️ 企业级配套与安全审计 (Enterprise Toolkit & RBAC)
*   **分布式任务中心**：APScheduler + Redis 调度，支持模拟智能体身份执行周期/单次任务。
*   **精细化 RBAC**：用户、角色、菜单与元素级权限，读写操作隔离。
*   **SSO 与脱敏**：SSO 登录可后台开关；审计日志自动脱敏密码、API Key 等敏感字段。
*   **安全审计水印**：Embed 窗口背景水印（用户名+时间戳或自定义文案），防截屏外泄。
*   **链路可视化与导出**：Trace 时间线调试；查数结果 CSV/Excel 导出（utf-8-sig）。

---

## 🔄 智能体工作流 (Execution Flow)

系统遵循 **「路由 → 分发 → 执行 → 聚合」** 链路：

1.  **意图路由 (Router)**：未传 `agent_id` 时，先尝试启发式短路（纯问候、联网搜索、ChatBI 会话非粘性话题切换 → 通用助手），否则 LLM 结合最近对话与智能体元数据选型；支持多专家并行 hint。
2.  **专家直选**：Embed 专家模式 / `agent_id` / `@提及` 跳过 Router，直接进入指定智能体。
3.  **执行分发 (Dispatcher)**：按引擎与能力选择 **Knowledge** / **ChatBI (DataQuery)** / **Assistant** / RAGFlow / OpenClaw 执行器；ChatBI 内部再判定新查数、复用结果、上下文动作等。
4.  **动态执行 (ReAct)**：AgentScope「思考-行动-观察」循环，工具权限挂起、SQL 护栏、工具预检等按执行器生效。
5.  **结果合成 (Synthesis)**：多 Agent 场景由 Synthesizer 聚合；单 Agent 流式 SSE 返回正文、日志与引用。

详见 [architech/design/chat/CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) · [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md)

---

## 📚 文档与架构 (Documentation)

| 文档 | 说明 |
|------|------|
| [HOW_TO_INSTALL.md](HOW_TO_INSTALL.md) | 安装部署与 FAQ |
| [architech/README.md](architech/README.md) | 架构文档索引 |
| [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) | 聊天端到端流程 |
| [PROMPT_LAYERS.md](architech/design/chat/PROMPT_LAYERS.md) | 提示词分层与注入 |
| [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md) | 智能体路由设计 |
| [api_integration_guide.md](docs/md/api_integration_guide.md) | Embed / V1 API 集成 |
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
├── db-prod/              # 数据库版本迁移与 SQL 升级脚本 (V0-VNN)
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

**2. 构建镜像并导出 tar**

| 脚本 | 目标环境 |
| :--- | :--- |
| `./build_linux_x86.sh` | x86_64 Linux 服务器（最常见） |
| `./build_linux_arm.sh` | ARM64 Linux（鲲鹏 / Ampere 等） |
| `./build_native.sh` | 本机原生架构，仅用于本地试跑 |

```bash
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
该脚本会自动停止旧的 8001 进程、编译前端（跳过类型检查以提速）并以前台 `reload` 模式拉起后端 FastAPI 服务，您可以在当前终端中实时查看联调日志与输出。

#### 2. 工具脚本对比
项目提供了以下三个工具以适应不同的开发和部署场景：

| 脚本名称 | 运行模式 | 前端编译方式 | 后端拉起方式 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- |
| `dev.sh` | **前台** 交互式 | 极速编译 (跳过类型检查) | 带 `--reload` 前台实时输出日志 | 本地日常功能联调与排错 |
| `scripts/redeploy-fast.sh` | **后台** 运行 | 极速编译 (跳过类型检查) | `nohup` 后台守护进程运行 | 开发测试环境快速后台热更新 |
| `scripts/redeploy.sh` | **后台** 运行 | 完整编译 (包含 `vue-tsc` 类型检查) | `nohup` 后台守护进程运行 | 生产/准生产环境规范发布与部署 |

#### 3. 传统分步手动启动
如果您需要分步微调前后端，也可以执行传统命令：
```bash
# 1. 准备环境
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 启动后端
uvicorn app.main:app --reload --port 8001

# 3. 启动前端
cd frontend && npm install && npm run dev
```

---

## 🤝 贡献指南

1.  **分支规范**: 基于 `main` 分支开发，功能分支命名格式 `feature/your-feature-name`。
2.  **提交信息**: 必须使用 **中文** 编写 Commit Message，清晰描述变更内容。
3.  **测试验收**: 新增功能时，请更新 `tests/CHECKLIST.md`。

---

## 💬 联系与交流

如果您在使用过程中有任何疑问、功能建议，或者想要获取更多技术资讯，欢迎扫码关注我们的微信公众号：

<img src="docs/images/weixin.png" alt="微信公众号" width="200" />

---

## 📄 许可证

本项目采用 MIT 开源许可证，允许自由使用、复制、修改、合并、发布、分发、再许可及销售本软件副本。

---
Copyright © 2025-2026 Randy Chen <cexlong@gmail.com>. All Rights Reserved.

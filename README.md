# 云枢 · 智能体平台 (Yunshu AI Agent Platform)

**简体中文** | [English](README_EN.md)

> **企业级 AI 智能体编排与执行平台**  
> *Connect Data. Orchestrate Intelligence.*



[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/) [![TailwindCSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/) [![ClickHouse](https://img.shields.io/badge/ClickHouse-Ready-FFCC00.svg?logo=clickhouse&logoColor=black)](https://clickhouse.com/) [![Redis](https://img.shields.io/badge/Redis-Active-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/) [![MCP](https://img.shields.io/badge/MCP-Supported-orange.svg?logo=anthropic)](https://modelcontextprotocol.org/) [![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)


<img src="docs/images/banner.png" alt="Banner" width="600" />

**云枢智能体平台** 是专为企业级复杂场景打造的 AI 智能中枢。

平台核心聚焦于以下能力矩阵：
*   💬 **深度交互式对话 (Dialogue & Co-Agent)**：提供极速流式响应，支持复杂意图理解与多专家协同。支持快捷指令、@Mention 智能体快速提及、多模态附件上传与 Vision 识图问答，并无缝直连知识检索与动态技能 (Skills) 链执行。
*   🔌 **灵活的嵌入式 (Embed) 集成**：支持通过嵌入式聊天 SDK 快速集成至企业内各业务系统，并无缝对接现有用户鉴权体系，实现统一的安全合规与租户隔离。
*   📊 **原生企业级 ChatBI**：内置完整的数据源管理、可视化元数据同步、以及核心的“案例数据集”机制，通过 Few-Shot 强化与 SQL 报错自愈，实现高准确率的自然语言转 SQL 查询。
*   🤝 **开箱即用的主流生态集成**：原生对接 **RAGFlow 托管智能体与知识库** 以实现非结构化数据的多维精准检索与专业溯源；深度集成 **OpenClaw🦞 大模型安全网关** 代理，实现大模型调用的安全网关鉴权与多维度审计。
*   📚 **可视化知识库管理中心 (Knowledge Base Center)**：支持企业级非结构化知识文档的一体化树形管理，提供召回检索测试、语义关联合并、生命周期维护及数据隔离防护。
*   🛠️ **全链路在线 Debug 与 Trace 分析**：可视化呈现 AI 决策链、工具调用详情及生成的 SQL 执行计划，支持在线开发调试与追踪，赋能开发人员快速定位和调优。
*   ⚙️ **API 接口与分布式调度**：暴露标准化、安全的服务级 API，集成基于 APScheduler + Redis 的任务调度系统，支持模拟智能体身份执行周期/单次自动化任务。
*   🎯 **提示词工厂 (Prompt Factory)**：提供系统提示词的版本控制与可视化管理（位于 `architech/prompts/`），确保模型行为在企业生产环境中的精确可控。

---

## 🏛️ 系统架构 (Architecture)

```text
┌──────────────────────────────────────────────────────────┐
│                      云枢智能体平台                      │
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
*   **多意图编排**：系统自动将复杂问题拆解为多个子任务（如：“查上周 PUE 并对比 SOP” -> 拆解为 ChatBI + RAG），支持专家 Agent 异步并行工作，由 Synthesizer 聚合生成逻辑一致的最终回复。
*   **AgentScope ReAct 引擎**：General / ChatBI 基于 AgentScope Agent + Toolkit，遵循“思考-行动-观察-反思”闭环，支持本地工具链自主调度与权限挂起恢复。
*   **RAGFlow 托管 Agent 驱动**：一键对接 RAGFlow 在线托管的知识智能体，直接利用其强大的并行检索和流式对话底座。
*   **OpenClaw🦞 大模型安全网关**：对接小龙虾 API 网关代理，利用 `AUTH_CONTEXT` (授权上下文) 在请求模型时透传当前用户的真实身份、频道及可访问的元数据/数据集列表，确保企业级数据隔离与安全。


### 2. 📊 智能数仓分析 (ChatBI & Self-Healing)
*   **Text-to-SQL 闭环**：通过元数据注入，实现自然语言直接查询业务数据库。
*   **案例集与 Few-Shot 增强**：**核心黑科技**。内置案例集（经验库）管理模块，支持用户反馈一键入库与人工审核，通过 RAGFlow 经验库进行相似度检索，并在生成 SQL 时将优秀案例作为 Few-Shot 动态注入 LLM 提示词头部，大幅提升业务专有 SQL 的生成准确率。
*   **自愈机制 (Self-Healing)**：**独家特性**。当 SQL 生成错误时，系统自动拦截报错并引导 LLM 根据 Schema 修正，大幅提升一次性查询成功率。
*   **独立数据源管理**：**新增特性**。提供可视化管理界面，支持多类型数据库（含 Oracle Thin/Thick 模式、ClickHouse、MySQL 等）连接配置的管理、测试与 DDL 智能读取，保障连接别名全局唯一。


### 3. 🔌 开放插件生态 (MCP Integration)
*   **原生支持 MCP**：遵循 Anthropic 的 Model Context Protocol。
*   **无限扩展**：无需修改核心代码，即可通过 MCP 服务器连接 Jira、Email、GitLab 等外部生产力工具。

### 4. 📚 深度知识增强与集成 (RAG & Knowledge Hub)
*   **一站式知识库管理**：平台内置强大的知识管理工作台，支持文件夹与文档的一体化树形管理，提供文件切片、召回检索测试、语义合并与版本控制能力。
*   **原生 Chat 无缝集成**：知识库能力原生嵌入 Chat 对话流中，AI 会话时自动并发检索并召回最相关的知识切片注入 Prompt 上下文；同时，在 UI 端以高颜值溯源卡片直观展示引用出处，保障回答的可信性与企业数据安全隔离。

### 5. 🛠️ 企业级配套与安全审计 (Enterprise Toolkit & RBAC)
*   **分布式任务中心**：**新增特性**。集成基于 APScheduler + Redis 的任务调度系统，支持模拟智能体身份执行周期性或单次自动触发任务（如定时审计、数据同步）。
*   **精细化 RBAC 权限与数据隔离**：内置细粒度的用户、角色及元素级权限管理系统，支持菜单权限只读、操作权限隔离，实现严苛的企业内安全合规管控。
*   **SSO 统一认证与数据脱敏**：原生对接 SSO 统一认证登录（支持通过后台胶囊开关一键启闭），支持用户数据的高效安全同步；大模型审计日志捕获时，对密码、API Key 等敏感字段实施自动深度脱敏。
*   **安全审计水印 (Watermark)**：**新增特性**。支持一键启闭嵌入式对话窗口背景水印，可按【用户名 + 时间戳】或【自定义文本】自动平铺渲染，强力防御截屏信息外泄。
*   **链路可视化与数据导出**：支持 AI 决策链路 Trace 可视化展示，并提供结构化数据查询结果一键导出（支持 CSV/Excel 格式，已处理中文乱码）。

---

## 🔄 智能体工作流 (Execution Flow)

系统遵循 **“路由 -> 分发 -> 执行 -> 聚合”** 的环形逻辑：

1.  **意图路由 (Router)**：基于 LLM 和指代消解技术，决定调用哪些专家。
2.  **动态执行 (ReAct)**：执行器采用“思考-行动-观察-反思”循环，自主决定工具调用路径。
3.  **结果合成 (Synthesis)**：消除冗余，将多源数据转化为人类易读的专业报告。

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

产物输出至 **`docker/release/`**，例如 `yunshu-ai-agent_linux-amd64_20250527.tar`。离线部署可在目标机执行 `docker load -i docker/release/xxx.tar`。

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
./start-yunshu-ai-agent.sh
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

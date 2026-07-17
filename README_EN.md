> **Project Notice**  
> This is a **personal open-source** project for learning and exchange, licensed under [MIT](LICENSE) and free to redistribute.  
> The original name “Yunshu (云枢)” conflicted with other enterprise projects; it has been renamed to “NanZi (南孜)” to avoid confusion.  
> “NanZi” comes from my long-used online handle, from the Chinese idiom “孜孜不倦” (diligent and tireless), reflecting continuous learning and evolution in AI.

# NanZi AI Agent Platform (南孜 · 智能体平台)

[简体中文](README.md) | **English**

> **Enterprise-grade AI Agent Orchestration and Execution Platform**  
> *Connect Data. Orchestrate Intelligence.*

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/) [![AgentScope](https://img.shields.io/badge/AgentScope-2.x-7C3AED.svg)](https://github.com/agentscope-ai/agentscope) [![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/) [![TailwindCSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/) [![ClickHouse](https://img.shields.io/badge/ClickHouse-Ready-FFCC00.svg?logo=clickhouse&logoColor=black)](https://clickhouse.com/) [![Redis](https://img.shields.io/badge/Redis-Active-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/) [![MCP](https://img.shields.io/badge/MCP-Supported-orange.svg?logo=anthropic)](https://modelcontextprotocol.org/) [![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

![Promo](docs/images/nanzi-platform-promo-16x9.png)
![Overview](docs/images/nanzi-platform-overview-16x9.png)

**NanZi AI Agent Platform** is an AI intelligence center purpose-built for complex enterprise scenarios.

The platform revolves around the following core capability matrix:
*   💬 **Deep Interactive Dialogue**: High-performance streaming chat with auto-routing, **expert mode / @mention direct selection**, and multi-agent synthesis. **Tool preflight** nudges the model to call bound tools; the main assistant supports **skill auto-scan** and permission suspend/resume; slash commands, multimodal attachments, and Vision Q&A.
*   🧠 **Long-Term & Cross-Session Memory**: LTM preference injection plus on-demand **`memory_search`** over session/daily summaries; Memory Management Console for vector ops and governance.
*   🔌 **Flexible Embedded Integration**: Embed Chat SDK for enterprise portals with existing auth, tenant isolation, and compliance.
*   📊 **Native Enterprise ChatBI**: Data sources, metadata sync, case-library Few-Shot, SQL self-healing, and optional **sql_plan** structured plans; **My Data Portal** via `/dataset_portal`; direct physical SQL and golden report stash.
*   🤝 **Ecosystem Integration**: **RAGFlow** managed agents & knowledge bases; **OpenClaw🦞** LLM security gateway with user identity and dataset context passthrough.
*   📚 **Knowledge Base Center**: Tree document management, recall testing, semantic merge; **Knowledge executor** auto-retrieves before ReAct with citation cards.
*   🛠️ **Debug & Trace**: Decision chains, tool calls, SQL plan cards; CSV/Excel export for structured query results.
*   ⚙️ **APIs & Scheduling**: Standard V1 APIs; APScheduler + Redis task center under agent identities.
*   🎯 **Prompt Factory**: System prompt versioning and drafts under `architech/prompts/`.

---

## 🏛️ Architecture

![Architecture](docs/images/nanzi-platform-architecture-16x9.png)

```text
┌──────────────────────────────────────────────────────────┐
│                 NanZi AI Agent Platform                 │
└───────────────┬────────────────────────────┬─────────────┘
                │                            │
      [ Embed Chat SDK ]              [ Admin Console ]
                │                            │
                └─────────────┬──────────────┘
                              │ SSE/HTTP
┌─────────────────────────────▼────────────────────────────┐
│                       Portal Gateway                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Auth/Perm│  │Intent Rtr│  │Task Sched│  │AuditTrace│  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└─────────────────────────────┬──────────────┬─────────────┘
                              │              │ (Status & Queue)
                              │        ┌─────▼─────┐
                              │        │   Redis   │
                              │        └───────────┘
┌─────────────────────────────▼────────────────────────────┐
│                        Expert Pool                       │
│   ┌──────────────┐      ┌──────────────┐     ┌─────────┐  │
│   │ChatBI Expert │      │  RAG Expert  │     │PluginAst│  │
│   └──────┬───────┘      └──────┬───────┘     └───┬─────┘  │
└──────────┼─────────────────────┼─────────────────┼────────┘
           │ (ReAct Loop)        │ (Managed Route) │ (Tool Chain)
┌──────────▼─────────────────────▼─────────────────▼────────┐
│                     Execution Engines                    │
│  ┌──────────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ AgentScope ReAct │  │RAGFlow Agent │  │  OpenClaw🦞 │  │
│  │ (Loop & SelfSQL) │  │(Managed Bot) │  │(AUTHContext)│  │
│  └────────┬─────────┘  └──────┬───────┘  └──────┬──────┘  │
└───────────┼───────────────────┼─────────────────┼─────────┘
            │                   │                 │
┌───────────▼───────┐ ┌─────────▼─────┐ ┌─────────▼────────┐
│ Enterprise DBs    │ │ RAGFlow KBs   │ │   MCP Server     │
│ (Oracle/CK/MySQL) │ │ (Unstructured)│ │ (Ext System/API) │
└───────────────────┘ └───────────────┘ └──────────────────┘
```

---

## 🖼️ Interface Snapshots

| 📊 Overview Dashboard | 💬 AI Chat |
| :---: | :---: |
| ![Dashboard Overview](docs/snapshot/overview.png) | ![AI Chat](docs/snapshot/ai-chat.png) |
| **🧠 Memory & LTM** | **🔍 Memory Control Console** |
| ![Memory & LTM](docs/snapshot/chat-with-memory.png) | ![Memory Management](docs/snapshot/memory-manage.png) |
| **🛠️ Trace Timeline** | **📚 Knowledge Base Workbench** |
| ![Trace Details](docs/snapshot/chat-debug.png) | ![Knowledge Hub](docs/snapshot/knowledge.png) |
| **🤖 Agent Studio** | **📝 Prompt Playground** |
| ![Agent Management](docs/snapshot/bot-list.png) | ![Prompt Studio](docs/snapshot/prompt_studio.png) |
| **🔌 Direct Data Sources** | **📊 Metadata Management** |
| ![Data Sources](docs/snapshot/datasource.png) | ![Metadata Management](docs/snapshot/meta-list.png) |
| **⚡ Dynamic Agent Skills** | **⚙️ System Settings** |
| ![Agent Skills](docs/snapshot/skills-manage.png) | ![System Config](docs/snapshot/system.png) |



---

## 🌟 Core Capabilities

### 1. 🧠 Multi-Engine & Hybrid Orchestration
*   **Smart routing**: When no agent is specified, heuristic shortcuts (greetings, web search, ChatBI session break) run before LLM semantic routing; multi-intent parallel execution with Synthesizer aggregation.
*   **Direct expert selection**: Embed expert mode, `agent_id`, or `@mention` skips auto-routing and loads the chosen agent.
*   **AgentScope ReAct**: Assistant / ChatBI / Knowledge run on AgentScope Agent + Toolkit with permission suspend/resume.
*   **Main assistant extras**: Tool preflight (relevance-based nudge), skill auto-scan, anti–business-data hallucination guard with one-click ChatBI switch.
*   **RAGFlow managed agents**: Connect to RAGFlow-hosted bots for retrieval and streaming dialogue.
*   **OpenClaw🦞 gateway**: Passes `AUTH_CONTEXT` (identity, channel, accessible datasets) for tenant isolation.

### 2. 📊 Intelligent Warehouse Analysis (ChatBI & Self-Healing)
*   **Text-to-SQL loop**: Metadata injection, schema gates, and layered SQL guards.
*   **My Data Portal**: Slash command `/dataset_portal` (legacy `/dataset_menu` still works) for permission-aware navigation and quick follow-ups.
*   **Case library & Few-Shot**: Audited experience base with dynamic head-of-prompt injection.
*   **Self-healing & sql_plan**: SQL error repair rounds; optional `enable_sql_plan` for high-risk queries with structured `<sql_plan>` cards in the UI.
*   **Clarification short-circuit**: Non-data chit-chat clarified at classification without forcing SQL.
*   **Data sources**: Visual Oracle / ClickHouse / MySQL management, DDL sync, golden report stash, and direct physical SQL execution.

### 3. 🔌 Open Plugin Ecosystem (MCP Integration)
*   **Native MCP Support**: Fully compliant with Anthropic's Model Context Protocol.
*   **Infinite Extensibility**: Seamlessly connect to external productivity tools like Jira, Email, GitLab, etc. via MCP servers without modifying core code.

### 4. 📚 Deep Knowledge Enhancement & Integration (RAG & Knowledge Hub)
*   **Knowledge workbench**: Tree document management, slice preview, recall testing, semantic merge, lifecycle audit.
*   **Knowledge executor**: Auto `search_knowledge_base` prefetch before ReAct; citation cards; blocks uncited factual answers when retrieval is empty.
*   **RAGFlow managed path**: Optionally connect RAGFlow-hosted knowledge agents instead.

### 5. 🛠️ Enterprise Security, Audit & Utilities
*   **Task center**: APScheduler + Redis for periodic/one-off jobs under agent identities.
*   **Granular RBAC**: User, role, menu, and element-level permissions.
*   **SSO & masking**: Toggleable SSO; audit logs mask passwords and API keys.
*   **Embed watermark**: Username + timestamp or custom overlay text against screenshot leaks.
*   **Trace & export**: Timeline debugging; CSV/Excel query exports (utf-8-sig).

---

## 🔄 Execution Flow

The system follows **Routing → Dispatch → Execution → Synthesis**:

1.  **Intent Router**: Without `agent_id`, heuristic shortcuts run first (greetings, web search, ChatBI session break → general assistant), then LLM routing with recent history and agent metadata; multi-agent hints supported.
2.  **Direct selection**: Embed expert mode, `agent_id`, or `@mention` bypasses the router.
3.  **Dispatcher**: Routes to **Knowledge** / **ChatBI (DataQuery)** / **Assistant** / RAGFlow / OpenClaw; ChatBI classifies new query vs reuse vs context action internally.
4.  **ReAct execution**: AgentScope reasoning-action loop with per-executor guards (SQL gates, tool preflight, permissions).
5.  **Synthesis**: Multi-agent answers aggregated by Synthesizer; single-agent streams SSE content, logs, and citations.

See [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) · [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md)

---

## 📚 Documentation

| Doc | Description |
|-----|-------------|
| [HOW_TO_INSTALL.md](HOW_TO_INSTALL.md) | Installation & FAQ |
| [architech/README.md](architech/README.md) | Architecture index |
| [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) | End-to-end chat flow |
| [PROMPT_LAYERS.md](architech/design/chat/PROMPT_LAYERS.md) | Prompt layering |
| [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md) | Agent routing |
| [api_integration_guide.md](docs/md/api_integration_guide.md) | Embed / V1 API integration |
| [ai_agent_gating_contract.md](docs/md/ai_agent_gating_contract.md) | Agent gating contract |
| [tests/CHECKLIST.md](tests/CHECKLIST.md) | Test checklist |

---

## 📂 Project Structure

```text
.
├── app/                  # Backend core code (FastAPI)
│   ├── api/              # API router layer (Portal admin & Client V1 APIs)
│   ├── services/         # Business service layer (Auth, RAG knowledge, MCP plugin services)
│   │   └── ai/           # 🤖 AI Orchestration Center (AgentScope Runners, OpenClaw execution & intent dispatch)
│   └── models/           # SQLAlchemy ORM models
├── frontend/             # Admin console and embedded Chat SDK project (Vue 3 + Tailwind)
├── .agent/               # Agent-specific dev skills & workflow configs (opsx, etc.)
├── architech/            # High-level architecture specs & System Prompt management
├── db-prod/              # Database migrations & SQL upgrade scripts (V0-VNN)
├── docker/               # Containerization & one-click Docker-compose deployment solutions
├── scripts/              # Devops auxiliary scripts (one-click run, data sync, redeployment)
├── tests/                # Automated test suites & verification checklists (CHECKLIST.md)
└── openspec/             # OpenSpec API specifications & protocol trace files
```

---

## 🚀 Quick Start

### 🐳 Docker Deployment (Recommended)

**1. Configure environment**
```bash
cd docker
cp ../env.example .env   # DB, Redis, ENCRYPTION_KEY, etc.
```

**2. Build image and export tar**

| Script | Target |
| :--- | :--- |
| `./build_linux_x86.sh` | x86_64 Linux servers (most common) |
| `./build_linux_arm.sh` | ARM64 Linux (Kunpeng / Ampere, etc.) |
| `./build_native.sh` | Host native arch — local testing only |

```bash
# Production (x86) — also use this on Mac when deploying to x86 servers
./build_linux_x86.sh
```

Artifacts are written to **`docker/release/`**, e.g. `nanzi-ai-agent_linux-amd64_20250527.tar`. On the target host: `docker load -i docker/release/xxx.tar`.

> On Apple Silicon Macs deploying to x86 servers, use `build_linux_x86.sh`, not `build_native.sh`. The first cross-platform build may take a long time with little console output while base images are pulled.

**If `docker buildx` is unavailable** (common with Homebrew `docker` + Colima when `~/.docker/cli-plugins/docker-buildx` still points at uninstalled Docker Desktop):

```bash
cd docker
./install-buildx.sh
./build_linux_x86.sh
```

More details: [docker/README.md](docker/README.md) (Chinese) · [docker/README_EN.md](docker/README_EN.md) (English).

**3. Start services**
```bash
./start-nanzi-ai-agent.sh
```

### 🛠️ Development & Deployment Tools

#### 1. One-Click Local Development (Highly Recommended)
For daily local development, it is highly recommended to use the integration script at the repository root:
```bash
./dev.sh
```
This script will automatically terminate any stale processes on port 8001, compile frontend assets (skipping type-checks for speed), and launch the FastAPI backend service in `reload` mode. You can monitor live logs directly in your active terminal.

#### 2. Utility Scripts Comparison
We provide three utility scripts tailored for different development and deployment environments:

| Script | Mode | Frontend Build Method | Backend Execution Method | Best Use Case |
| :--- | :--- | :--- | :--- | :--- |
| `dev.sh` | **Foreground** Interactive | Quick Build (skips type check) | Active logging with `--reload` | Local debugging & troubleshooting |
| `scripts/redeploy-fast.sh` | **Background** Daemon | Quick Build (skips type check) | Runs in background via `nohup` | Fast hot updates in dev/test setups |
| `scripts/redeploy.sh` | **Background** Daemon | Full Build (includes `vue-tsc` checks) | Runs in background via `nohup` | Standard releases in production environments |

#### 3. Traditional Step-by-Step Manual Run
If you need to tweak the frontend or backend separately, you can run:
```bash
# 1. Setup environment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Run backend
uvicorn app.main:app --reload --port 8001

# 3. Run frontend
cd frontend && npm install && npm run dev
```

---

## 🤝 Contributing

1.  **Branching Policy**: Develop based on `main`. Feature branches should be named `feature/your-feature-name`.
2.  **Commit Message**: Commit messages must be written in **Chinese**, clearly describing your changes.
3.  **Verification**: Update `tests/CHECKLIST.md` when introducing new features.

---

## 💬 Contact & Community

If you have any questions, feature suggestions, or need further technical updates, please scan the QR code to follow our WeChat Official Account:

<img src="docs/images/weixin.png" alt="WeChat QR Code" width="200" />

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---
Copyright © 2025-2026 Randy Chen <cexlong@gmail.com>. All Rights Reserved.

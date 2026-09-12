> **Project Notice**  
> This is a **personal open-source** project for learning and exchange, licensed under [MIT](LICENSE) and free to redistribute.  
> The original name “Yunshu (云枢)” conflicted with other enterprise projects; it has been renamed to “NanZi” to avoid confusion.  
> “NanZi” comes from my long-used online handle, from the Chinese idiom “孜孜不倦” (diligent and tireless), reflecting continuous learning and evolution in AI.

# NanZi AI Agent Platform (智能体平台)

[简体中文](README.md) | **English**

> **Enterprise-grade AI Agent Orchestration and Execution Platform**  
> *Connect Data. Orchestrate Intelligence.*

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg?logo=python&logoColor=white)](https://www.python.org/) [![AgentScope](https://img.shields.io/badge/AgentScope-2.x-7C3AED.svg)](https://github.com/agentscope-ai/agentscope) [![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?logo=vue.js&logoColor=white)](https://vuejs.org/) [![TailwindCSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/) [![ClickHouse](https://img.shields.io/badge/ClickHouse-Ready-FFCC00.svg?logo=clickhouse&logoColor=black)](https://clickhouse.com/) [![Redis](https://img.shields.io/badge/Redis-Active-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/) [![MCP](https://img.shields.io/badge/MCP-Supported-orange.svg?logo=anthropic)](https://modelcontextprotocol.org/) [![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

> 📖 **Hands-on series** (Chinese): [NanZi Open-Source Agent Platform Series](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect) — architecture · install · agent setup · ChatBI · toolbox · MCP

![Promo](docs/images/nanzi-platform-promo-16x9.png)
![Overview](docs/images/nanzi-platform-overview-16x9.png)

**NanZi AI Agent Platform** is an AI intelligence center purpose-built for complex enterprise scenarios.

The platform revolves around the following core capability matrix:
*   💬 **Deep Interactive Dialogue**: High-performance streaming chat with **intelligent delegation through the default Main agent**, **expert mode / @mention direct selection**, and multi-agent synthesis. **Tool preflight** nudges the model to call tools; integrated `ask_user_question` smart cards (single/multi-choice, text input), **Todo task lists** with step-by-step progress tracking, and skill auto-scan with permission suspend/resume.
*   🛡️ **Multi-Policy Sandbox & Isolation**: Native support for **Local** (host process), **Docker** (isolated private container), **K8s** (Kubernetes native Pod sandbox, recommended for enterprise cloud-native environments without Docker Socket), **E2B** (cloud sandbox), and **SSH** (remote secure channel) policies. Docker and K8s modes support user workspace **same-path/subPath mounting** for seamless canvas preview and edit persistence; idle auto-reaper (30m timeout), graceful shutdown cleanup, and chat popover control with **live second-by-second uptime tracking**.
*   🌐 **Persistent Browser & Live Takeover**: Server-side persistent browser sessions with complete automation toolsets (navigation, click, fill, human-like trajectory slider dragging, scroll, keys, snapshot, file upload, multi-tabs); frontend right-side **live Web interactive drawer** with stream rendering and human-in-the-loop takeover.
*   📊 **Native Enterprise ChatBI & Self-Healing**: Data sources and metadata management, **metadata consistency inspection & schema drift governance**, case-library Few-Shot, SQL self-healing, and optional **sql_plan** structured plans; **My Data Portal** via `/dataset_portal`; direct physical SQL and golden report stash.
*   🧠 **Long-Term & Cross-Session Memory**: LTM preference injection plus on-demand **`memory_search`** over session/daily summaries; Memory Management Console for vector ops and governance; full-lifecycle Redis memory and compaction logs **TTL extended to 30 days**.
*   📊 **Context Breakdown & Overflow Compaction**: Fine-grained Token breakdown across System Prompt, Tools Schema, Memory/History, and Current Turn; smart two-stage structured overflow compaction (`_structured_tool_block` with multimodal tag preservation).
*   🧩 **Code Canvas & Workspace Execution**: Stream, stop, and inspect Python / Shell runs inside a private user workspace; `publish_generated_file` for downloadable artifacts.
*   📚 **Knowledge Base Center (RAG & Knowledge Hub)**: Tree document management, recall testing, semantic merge; **Knowledge executor** auto-retrieves before ReAct with citation cards.
*   🔌 **Bi-Directional Enterprise MCP Ecosystem (Platform & Client MCP)**: Automatically forwards authenticated user context when calling outbound external MCP tools to guarantee multi-tenancy and data permission isolation; natively provides **NanZi Platform MCP** resource server (OAuth2 auth & Service Desk) allowing external clients like Cursor and Claude Desktop to invoke NanZi agents, chats, and metadata directly.
*   🔌 **Flexible Embedded Integration**: Embed Chat SDK for enterprise portals with existing auth, tenant isolation, granular RBAC, and watermark compliance.
*   ⏰ **Task Scheduler & Multi-Channel Notifications**: APScheduler with Redis execution locks and an environment-controlled scheduler node for Cron/periodic/one-off tasks under agent identities; multi-channel alerts (**WeCom, DingTalk, Feishu, Email, Webhook, and In-App Inbox**); auto-cleans thinking streams for clean deliveries with overflow protection and ChatBI golden report threshold alerts.
*   🛠️ **Debug & Trace**: Decision chains, tool calls, SQL plan cards; CSV/Excel export for structured query results.
*   ⚙️ **Open Standard APIs**: Standard V1 API suite for third-party systems to trigger agent workflows and queries programmatically.
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
│   │ ChatBI Expert│      │  RAG Expert  │     │ Plugins │  │
│   └──────┬───────┘      └──────┬───────┘     └───┬─────┘  │
└──────────┼─────────────────────┼─────────────────┼────────┘
           │ (ReAct Loop)        │ (Managed Route) │ (Tool Chain)
┌──────────▼─────────────────────▼─────────────────▼────────┐
│                     Execution Engines                     │
│  ┌──────────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ AgentScope ReAct │  │ RAGFlow Agent│  │  OpenClaw🦞 │  │
│  │(Loop/Self-Heal)  │  │(Managed Bot) │  │(AUTH Context│  │
│  └────────┬─────────┘  └──────┬───────┘  └──────┬──────┘  │
└───────────┼───────────────────┼─────────────────┼─────────┘
            │                   │                 │
┌───────────▼───────┐ ┌─────────▼─────┐ ┌─────────▼────────┐
│ Multi-Source DBs  │ │ RAGFlow KB    │ │   MCP Server     │
│ (Oracle/CK/MySQL) │ │(Unstructured) │ │(Ext System/API)  │
└───────────────────┘ └───────────────┘ └──────────────────┘
```

---

## 🖼️ Interface Snapshots

| 📊 Overview Dashboard | 💬 AI Chat |
| :---: | :---: |
| ![Overview Dashboard](docs/snapshot/overview.png) | ![AI Chat](docs/snapshot/ai-chat.png) |
| **🧠 Memory & LTM** | **🔍 Memory Management Console** |
| ![Memory & Preference](docs/snapshot/chat-with-memory.png) | ![Memory Console](docs/snapshot/memory-manage.png) |
| **🛠️ Trace Timeline Debug** | **📚 Knowledge Hub** |
| ![Trace Timeline](docs/snapshot/chat-debug.png) | ![Knowledge Hub](docs/snapshot/knowledge.png) |
| **🤖 Agent Studio** | **📝 Prompt Playground** |
| ![Agent Studio](docs/snapshot/bot-list.png) | ![Prompt Studio](docs/snapshot/prompt_studio.png) |
| **🔌 Physical Data Sources** | **📊 Metadata Builder** |
| ![Data Sources](docs/snapshot/datasource.png) | ![Metadata](docs/snapshot/meta-list.png) |
| **⚡ Dynamic Agent Skills** | **⚙️ System Settings** |
| ![Skills](docs/snapshot/skills-manage.png) | ![System Settings](docs/snapshot/system.png) |

---

## 🌟 Core Capabilities

![NanZi Core Capabilities Matrix](docs/images/core.png)

### 1. 🧠 Multi-Engine & Hybrid Orchestration
*   **Intelligent delegation**: When no agent is specified, the request goes directly to the default `Main`, which answers directly or delegates to authorized experts through `sub_agent_call` / `sub_agent_batch_call` when needed.
*   **Direct expert selection**: Embed expert mode, `agent_id`, or `@mention` directly loads the chosen agent; that expert can still delegate sub-agents.
*   **AgentScope ReAct**: Assistant / ChatBI / Knowledge run on AgentScope Agent + Toolkit with permission suspend/resume.
*   **Thinking model compatibility**: Built-in `tool_choice_for_model` support and 6-tier `reasoning_effort` tuning for DeepSeek-R1, Kimi, GLM, etc.
*   **Main assistant extras**: Tool preflight (relevance-based nudge), skill auto-scan, anti–business-data hallucination guard with one-click ChatBI switch.
*   **RAGFlow managed agents**: Connect to RAGFlow-hosted bots for retrieval and streaming dialogue.
*   **OpenClaw🦞 gateway**: Passes `AUTH_CONTEXT` (identity, channel, accessible datasets) for tenant isolation.

### 2. 🛡️ Multi-Policy Sandbox & Execution Isolation
*   **Five sandbox policies**: Native support for `Local` (host process), `Docker` (private container), `K8s` (Kubernetes native Pod sandbox), `E2B` (cloud sandbox), and `SSH` (remote secure host).
*   **Cloud-native K8s Pod sandbox**: Eliminates the need to mount host Docker Sockets (`/var/run/docker.sock`) in Kubernetes production clusters. Dynamically launches isolated Pods via K8s API with shared PVC `subPath` workspace mounting, meeting enterprise and financial security compliance.
*   **Same-path / subPath workspace mounting**: User workspaces are mounted seamlessly inside Docker containers or K8s Pods with real-time canvas preview and editing.
*   **Automated lifecycle management**: 30-minute idle reaper, graceful shutdown cleanup, and concurrency ref-count sharing.
*   **Chat input popover console**: Live status badge (🟢Running/🟡Starting/⚪Idle), assigned Pod/container ID, **second-by-second live runtime counter**, and manual restart/shutdown controls with confirmation dialogs.

### 3. 🌐 Persistent Browser & Live Takeover
*   **Comprehensive automation toolkit**: Navigation, element click, text input, human-like trajectory slider dragging, smart wait, keypress, full-page scroll, file upload, screenshots, and multi-tab management.
*   **Right-side Web interactive drawer**: Live stream snapshot rendering, allowing users to manually take over interaction or solve captchas at any time.

### 4. 📊 Context Management & Observability
*   **4-tier Token breakdown**: Visual breakdown across System Prompt, Tools Schema, Memory/History, and Current Turn.
*   **Two-stage structured compaction**: Automatic watermark trigger with `_structured_tool_block` extraction and multimodal tag preservation.
*   **30-Day long-term retention**: Redis session history, compaction logs, and artifact download links extended to 30 days.

### 5. 📊 Intelligent Warehouse Analysis (ChatBI & Self-Healing)
*   **Text-to-SQL loop**: Metadata injection, schema gates, and layered SQL guards.
*   **My Data Portal**: Slash command `/dataset_portal` (legacy `/dataset_menu` still works) for permission-aware navigation and quick follow-ups.
*   **Case library & Few-Shot**: Audited experience base with dynamic head-of-prompt injection.
*   **Self-healing & sql_plan**: SQL error repair rounds; optional `enable_sql_plan` for high-risk queries with structured `<sql_plan>` cards in the UI.
*   **Clarification short-circuit**: Non-data chit-chat clarified at classification without forcing SQL.
*   **Data sources**: Visual Oracle / ClickHouse / MySQL management, DDL sync, golden report stash, and direct physical SQL execution.
*   **Metadata inspection & schema drift governance**: Physical warehouse consistency health-checks (zero Token cost) and runtime error self-healing feedback; detects table/column drops and type mismatches with one-click human-in-the-loop calibration, Changelog diff audit, and multi-channel scheduled alerts.

### 6. 🔌 Bi-Directional Open Plugin Ecosystem (MCP Integration)
*   **Outbound calls with user context forwarding**: When agents invoke external MCP tools, the protocol layer **automatically passes through the authenticated user context** (identity, role, tenant, and data scopes) to enforce downstream data permissions and operational audit trails.
*   **NanZi Platform MCP inbound service**: Serves as a native MCP resource server with standard OAuth2 authentication and an administrative "MCP Service Desk", enabling external clients like Cursor, Claude Desktop, and third-party agents to discover and invoke NanZi-governed agents, conversations, and warehouse metadata directly.
*   **Infinite ecosystem connectivity**: Connect enterprise productivity tools and multi-agent ecosystems seamlessly without modifying platform core code.

### 7. 📚 Deep Knowledge Enhancement & Integration (RAG & Knowledge Hub)
*   **Knowledge workbench**: Tree document management, slice preview, recall testing, semantic merge, lifecycle audit.
*   **Knowledge executor**: Auto `search_knowledge_base` prefetch before ReAct; citation cards; blocks uncited factual answers when retrieval is empty.
*   **RAGFlow managed path**: Optionally connect RAGFlow-hosted knowledge agents instead.

### 8. 🛠️ Enterprise Security, Audit & Utilities
*   **Automated Task Center & Notifications**: APScheduler with Redis execution locks and an environment-controlled scheduler node for Cron/periodic/one-off tasks under agent identities; multi-channel alerts (**WeCom, DingTalk, Feishu, Email, Webhook, and In-App Inbox**) with thinking stream stripping and overflow protection.
*   **ChatBI Golden Report Alerts**: Scheduled report inspection with threshold-hit, deviation rate, consecutive hits, and no-data anomaly alerts.
*   **Multi-Provider Model Registry**: Built-in presets for OpenAI, Azure, DeepSeek, Kimi, Zhipu GLM, SiliconFlow, Alibaba DashScope, Volcengine Ark (Doubao), Ollama with smart endpoint normalization.
*   **Platform timezone**: System jobs and subscriptions without an explicit timezone use `platform_timezone` (default `Asia/Shanghai`).
*   **Granular RBAC**: User, role, menu, and element-level permissions.
*   **SSO & masking**: Toggleable SSO; audit logs mask passwords and API keys.
*   **Embed watermark**: Username + timestamp or custom overlay text against screenshot leaks.
*   **Trace & export**: Timeline debugging; CSV/Excel query exports (utf-8-sig).

---

## 🔄 Execution Flow

The system is powered by a **6-stage asynchronous pipeline (PipelineRunner)**, following the **Entry Resolution → Delegation/Dispatch → Execution → Delivery/Synthesis** flow:

1.  **Entry Resolution (Route)**:
    *   **Explicit Selection**: When `agent_id`, `agent_name`, `version_id`, `@mention`, or Expert Mode is specified, it directly loads the targeted expert;
    *   **Quick Follow-up**: Clicking quick-action capsules on ChatBI result cards automatically routes to the DataQuery expert;
    *   **Default Fallback**: Without explicit targets, it directly loads the default `Main` assistant (eliminating outer semantic router overhead).
2.  **Intelligent Delegation**:
    *   Only **enabled system agents (`is_system=True`)** are eligible for the delegation candidate pool (custom agents do not participate in automated delegation);
    *   Main evaluates the request to answer directly or delegate subtasks via `sub_agent_call` (serial) / `sub_agent_batch_call` (parallel), guarded by self-delegation, recursion depth, and permission policies.
3.  **Dispatcher**:
    *   Accurately routes to **Knowledge** / **ChatBI (DataQuery)** / **Assistant** / **RAGFlow** / **OpenClaw** executors based on engine type and capabilities;
    *   ChatBI handles new data query, result reuse, contextual actions, metadata inspection, and clarification triage internally.
4.  **ReAct Execution**:
    *   AgentScope "Think-Act-Observe" reasoning loop natively integrated with SQL syntax safety guards, HITL human-in-the-loop approvals, user question card interruptions, and browser session panel live events.
5.  **Delivery & Synthesis**:
    *   Dynamic delegation is consolidated and output by the parent assistant; multi-agent parallel collaboration is aggregated via a dedicated Synthesizer model; full SSE streaming delivers content, reasoning traces, logs, and citations.

See [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) · [Intelligent delegation and expert selection](architech/design/AGENT_ROUTING_DESIGN.md)

---

## 📚 Documentation

| Doc | Description |
|-----|-------------|
| [WeChat series (CN)](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect) | Architecture, install, agent setup, ChatBI, toolbox, MCP |
| [HOW_TO_INSTALL.md](HOW_TO_INSTALL.md) | Installation & FAQ |
| [architech/README.md](architech/README.md) | Architecture index |
| [CHAT_FLOW.md](architech/design/chat/CHAT_FLOW.md) | End-to-end chat flow |
| [PROMPT_LAYERS.md](architech/design/chat/PROMPT_LAYERS.md) | Prompt layering |
| [AGENT_ROUTING_DESIGN.md](architech/design/AGENT_ROUTING_DESIGN.md) | Intelligent delegation and expert selection |
| [api_integration_guide.md](docs/md/api_integration_guide.md) | Embed / V1 API integration |
| [code_canvas_and_workspace_guide.md](docs/md/code_canvas_and_workspace_guide.md) | Code Canvas, workspace files, and execution API |
| [ai_agent_gating_contract.md](docs/md/ai_agent_gating_contract.md) | Agent gating contract |
| [tests/CHECKLIST.md](tests/CHECKLIST.md) | Test checklist |

---

## 📂 Project Structure

```text
.
├── app/                  # Backend core code (FastAPI async architecture)
│   ├── api/              # API router layer (Portal admin & Client V1 REST/SSE APIs)
│   ├── core/             # Core infrastructure (Config, DB engine, security, lifecycle)
│   ├── models/           # SQLAlchemy 2.x ORM data models
│   ├── schemas/          # Pydantic schemas & DTO validation models
│   ├── services/         # Business services (Auth, Models, ChatBI, RAG, MCP dispatch)
│   │   └── ai/           # 🤖 AI Orchestration (AgentScope Runners, OpenClaw executor & dispatch)
│   └── utils/            # General utilities (Crypto, vector operators, helpers)
├── frontend/             # Frontend project (Vue 3 + Vite + TypeScript + Tailwind CSS)
│   └── src/
│       ├── api/          # Frontend API requests & backend contract mappings
│       ├── views/        # Page views (Dashboard, Agent Studio, ChatBI, KB, Skill Center)
│       ├── components/   # Shared UI components & streaming chat cards
│       ├── composables/  # Vue composable hooks (SSE stream sessions, auth)
│       └── router/       # Dynamic routing & permission control
├── .agent/               # Agent-specific dev skills & workflow configs (opsx, dev-skills)
├── architech/            # High-level architecture specs, schemas & System Prompts
├── data/                 # Platform persistent data (Workspaces, sandbox, skills, uploads)
├── db-prod/              # MySQL migrations & SQL upgrade scripts (V0-VNN)
├── db-prod-pg/           # PostgreSQL baseline & idempotent migrations (V0-VNN)
├── docker/               # Containerization & one-click Docker-compose deployment
├── docs/                 # Project documentation, Release Notes & brand design guidelines
├── html/                 # Standalone product landing page
├── k8s_deploy/           # Cloud-native Kubernetes manifests & sandbox cluster ops suite
├── openspec/             # OpenSpec API specifications & change tracking
├── scripts/              # Devops auxiliary scripts (one-click run, data sync, redeploy)
├── tests/                # Automated test suites (Pytest) & checklists (CHECKLIST.md)
├── dev.sh                # 🛠️ Local one-click dev bootstrap, live reload & service manager
├── prebuild-sandbox.sh   # 🐳 Docker security code-sandbox image prebuild script
├── env.example           # ⚙️ Baseline global environment & sensitive configuration template
├── requirements.txt      # 📦 Backend Python runtime dependency list (Python 3.11)
└── pytest.ini            # 🧪 Automated test suite runner & assertion configuration
```

#### 📌 Root Scripts & Core Configuration Files

| File | Type | Description |
| :--- | :--- | :--- |
| [`dev.sh`](dev.sh) | Shell Script | **Local Dev & Ops Manager**: Supports concurrent backend/frontend startup, hot reload, foreground/daemon modes, environment detection, and graceful process management. |
| [`prebuild-sandbox.sh`](prebuild-sandbox.sh) | Shell Script | **Docker Sandbox Prebuild**: Pulls and builds the isolated Python 3.11 security code-execution sandbox image in advance to accelerate Agent tool executions. |
| [`env.example`](env.example) | Config Template | **Global Environment Template**: Covers MySQL/PostgreSQL, Redis Stack, JWT/encryption keys, LLM API keys, and platform ports. |
| [`requirements.txt`](requirements.txt) | Dependencies | **Backend Python Dependencies**: Core runtime packages for Python 3.11 (FastAPI, AgentScope, SQLAlchemy, Redis, etc.). |
| [`pytest.ini`](pytest.ini) | Test Config | **Pytest Test Configuration**: Rules for test discovery, async test markers, logging, and test execution behavior. |


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
On the first run, this script automatically detects and installs `uv`, prepares Python 3.11, creates `.venv`, and installs backend dependencies. Later runs only refresh dependencies when `requirements.txt` changes. It then terminates stale processes on the `.env`-configured `API_SERVICE_PORT` (default `8001`), compiles frontend assets (skipping type-checks for speed), and launches the FastAPI backend service in `reload` mode. You can monitor live logs directly in your active terminal.

For background development, use the lifecycle commands below:

```bash
# Start in the background; the PID is saved to .dev-server.pid
./dev.sh -d

# Check the PID, listening port, and /health status
./dev.sh status

# Gracefully stop the background service, then force-stop after the timeout
./dev.sh stop
```

`status` and `stop` never reinstall dependencies or rebuild the frontend. Port probing prefers `lsof` and falls back to `ss`/`fuser` when `lsof` is absent. The script recognizes this project's Uvicorn started via an absolute or relative `.venv` path, or as `python3 -m uvicorn`; if ownership still cannot be confirmed (or the port is genuinely held by another process), it lists the listening PID and command line and refuses to kill it so the situation can be inspected manually.

The startup banner also prints uv, Python target version, virtual environment, PyPI mirror, `DATABASE_TYPE`, database address, and Redis address information. Database and Redis passwords are not printed as separate fields. These values only describe the active configuration; they do not verify database or Redis connectivity.

The one-click script still requires Node.js/npm, and `.env`, database, and Redis must be prepared separately. The first uv, Python, and Python dependency downloads require network access. Set `PYPI_INDEX_URL` to override the default Tsinghua PyPI mirror when needed.

---

## 🤝 Contributing

1.  **Branching Policy**: Develop based on `main`. Feature branches should be named `feature/your-feature-name`.
2.  **Commit Message**: Commit messages must be written in **Chinese**, clearly describing your changes.
3.  **Verification**: Update `tests/CHECKLIST.md` when introducing new features.

---

## 💬 Contact & Community

If you have any questions, feature suggestions, or need further technical updates, please scan the QR code to follow our WeChat Official Account, or join our WeChat community group. You can also read the [NanZi Open-Source Agent Platform Series](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzU3NzAwOTA0NA==&action=getalbum&album_id=4613921118301732865#wechat_redirect) (Chinese):

<table>
  <tr>
    <td align="center">
      <img src="docs/images/weixin.png" alt="WeChat Official Account" width="200" /><br/>
      <sub>WeChat Official Account</sub>
    </td>
    <td align="center">
      <img src="docs/images/weixin-group.png" alt="WeChat Community Group" width="200" /><br/>
      <sub>WeChat Community Group (valid for 7 days)</sub>
    </td>
  </tr>
</table>

Scan the group QR code to get a free platform trial account and access URL.

---

## 💖 Sponsor & Support

NanZi AI Agent Platform is fully open-source and continuously evolving. If this project helps you in your learning, work, or production deployment, feel free to buy the author a cup of coffee ☕!

Your generous support is the greatest encouragement to keep improving architecture, releasing new capabilities, and maintaining the open-source community. Thank you for supporting open source!

<div align="center">
  <img src="docs/images/donate.png" alt="Sponsor QR Code" width="220" /><br/>
  <sub>WeChat Sponsor (Randy Chen)</sub>
</div>

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---
Copyright © 2025-2026 Randy Chen <cexlong@gmail.com>. All Rights Reserved.

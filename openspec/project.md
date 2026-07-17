# Project Context

> **⚠️ CRITICAL INSTRUCTION (交互准则)**: 
> 1. **始终使用中文**与用户交流，无论用户输入的是什么语言。
> 2. **Always respond in Chinese**, unless explicitly asked to translate.

## Purpose
**南孜・智能体平台 (NanZi AI Agent Platform)**
这是一个独立的 AI 服务系统，旨在为南孜生态（数据中心智能运营）提供通用的 AI 能力。
核心功能包括：
- **ChatBI**: 自然语言查数、生成图表。
- **RAG**: 知识库问答。
- **Multi-Agent**: 多智能体意图识别与自动化工作流。
该平台不直接连接底层大宽表（ClickHouse/HBase），而是通过 **南孜・数据服务平台 (api_service)** 获取统一封装的数据，确保权限与逻辑的一致性。

## Tech Stack
### Backend
- **Language**: Python 3
- **Framework**: FastAPI (Async)
- **Server**: Uvicorn / Gunicorn
- **Data Access**: 
  - `asynch` / `clickhouse-driver` (ClickHouse)
  - `aiomysql` (MySQL)
  - `redis` (Cache/Queue)
- **Validation**: Pydantic v2
- **Testing**: Pytest, Pytest-Asyncio

### Frontend (Console)
- **Framework**: Vue 3 (Composition API)
- **Build Tool**: Vite
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **State Management**: Pinia
- **Routing**: Vue Router
- **Visualization**: ECharts, Vue-ECharts
- **HTTP Client**: Axios

### Infrastructure
- **Containerization**: Docker, Docker Compose
- **Database**: ClickHouse (OLAP), MySQL (Metadata), Redis

## Project Conventions

### Code Style
- **Python**: 遵循 PEP 8 标准。Type hinting (mypy) 是必须的。
- **Vue/TS**: 使用 Composition API (`<script setup lang="ts">`)。
- **Commits**: Git 提交信息 **必须使用中文**。
- **Docs**: 所有设计文档、Specs、Proposal **必须使用中文**。
- **Database/DDL**: 新增数据库 Schema 或 DDL 变更脚本时，必须在 SQL 文件最前面以注释形式清晰说明变更原因、对应需求背景、创建人及创建时间。且严禁自动运行，仅负责生成 SQL 并提醒用户手动应用。

### Architecture Patterns
- **Backend Layered Architecture**:
  - `api/`: 路由与控制器 (Endpoints)
  - `schemas/`: Pydantic 数据模型 (DTOs)
  - `services/`: 业务逻辑层
  - `services/data_adapter/`: 数据访问适配器 (ClickHouse/MySQL)
  - `core/`: 核心配置、中间件、异常处理
- **Frontend Architecture**:
  - `views/`: 页面级组件
  - `components/`: 通用 UI 组件
  - `composables/`: 组合式逻辑 (Hooks)
  - `stores/`: Pinia 状态管理
- **Integration**:
  - AI Platform 消费 `api_service` 的 REST API。
  - Frontend 消费 AI Platform 与 Backend Service 的 API。

### Testing Strategy
- 使用 `pytest` 进行单元测试和集成测试。
- 在实现新功能或接口时，必须自动更新 `tests/CHECKLIST.md`。

### Git Workflow
- **No Auto Commit**: 严禁自动提交代码，必须等待用户明确指令。
- **Branching**: 基于 `main` 分支开发（参考 GitFlow 或 GitHub Flow）。

## Domain Context
- **NanZi (南孜)**: 南孜智能体的数据中心智能运营品牌。
- **Data Foundation**: 底层数据采集与加工平台。
- **api_service (Data API Platform)**: 统一数据出口，屏蔽底层存储差异，提供统一鉴权。
- **ChatBI**: 用户通过自然语言提问（如“华东机房温度趋势”），系统解析意图并调用 `api_service` 获取数据，最终返回图表+结论。
- **NL2SQL**: 自然语言转 SQL (或 API 查询参数)。

## Important Constraints
- **Language**: 与用户的交流、Git Commit、文档编写必须使用 **中文**。
- **Safety**: 运行 GUI 应用前需先停止旧进程；修改代码后需自动编译、停止旧进程、启动新进程并展示过程。
- **Data Access**: AI 平台原则上不直接查询底层业务数据（ClickHouse/HBase），必须通过 `api_service`。自有元数据（Prompt、配置等）可存自有 MySQL。
- **UX**: 
  - 设置界面需具备 macOS 原生质感 (TabView, standard icons)。
  - Slash Commands 菜单需支持键盘导航。
  - 长消息需正确处理溢出和滚动。
  - 严禁使用浏览器原生的 `alert()` 或 `confirm()` 确认框，必须统一使用平台定制的高颜值自定义弹窗组件（如 `ConfirmModal` 等），以维护高品质的 Premium 视觉体验。

## External Dependencies
- **LLM Gateway**: 公司内部私有化部署的模型网关（兼容 OpenAI 协议）。
- **NanZi Data API Platform (`api_service`)**: 核心数据来源。

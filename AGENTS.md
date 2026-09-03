## 交流语言

- **始终使用中文交流**，包括中间过程中的推理与思考，以及所有回复、汇报与文档说明。所有说明请优先用中文。与用户沟通、解释、总结时一律使用中文。

## 项目技术栈与关键边界

- **运行时**：Python 3.11；Docker 基础镜像为 `python:3.11-slim`。禁止使用仅 Python 3.12+ 支持的语法。
- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.x 异步 ORM + Pydantic 2。
- **前端**：Vue 3 + TypeScript + Vite 7 + Tailwind CSS 3，按现有 Vue 工程实现，不引入 React 方向的方案。
- **AI 编排**：AgentScope 2.x 是主运行时；OpenClaw、RAGFlow、MCP 是独立集成边界。修改路由或执行链前，先追踪 router、dispatcher、runner、tool 和权限预检。
- **数据库**：平台主库默认 MySQL，也支持 PostgreSQL。MySQL 使用 `db-prod/`，PostgreSQL 使用 `db-prod-pg/`，两套迁移不可混用；平台主库与外部数据源支持必须分开处理。
- **Redis**：Redis Stack/RediSearch 是平台运行依赖。
- **权限**：`menu:*` 控制菜单显示，`element:*` 控制具体功能。平台技能管理和待审核使用 `element:skills:admin`。
- **测试**：后端使用 pytest；纯前端契约测试使用 `pytest --confcutdir=tests/frontend`，前端类型检查使用 `vue-tsc --noEmit`。
- **数据库变更**：只新增对应版本的迁移 SQL，不直接修改本地或线上数据库。
- **执行边界**：Agent 不主动执行 `./dev.sh`、部署脚本或生产数据库操作；服务启停和部署由用户在控制台执行。

## Git 协作：用户说「代码已合」

用户表示 PR/代码已合并后，**必须立即执行本地 git 同步**，不要只口头确认：

1. `git fetch origin`
2. `git checkout main && git pull origin main`
3. `git checkout dev-agentscope && git merge main`（或 `git rebase main`，以 `DEVELOPMENT.md` 为准）
4. `git push origin dev-agentscope`（若本地领先远程）
5. 汇报：`main` / `dev-agentscope` 最新 commit、是否与远程一致
6. 提醒用户自行在控制台执行 `./dev.sh`（Agent **不**代跑）

**不要**在用户未要求时擅自创建 PR 或 force push。

## Git 协作：创建 / 更新 Pull Request

用户要求创建或更新 PR 时，**必须**按仓库根目录 [`PULL_REQUEST_TEMPLATE.md`](./PULL_REQUEST_TEMPLATE.md) 填写标题与正文（概要、核心变更、表结构变更清单、Commit Log、测试覆盖、备注），不要使用默认的 Summary/Test plan 两段式。提交前同步更新 `tests/CHECKLIST.md`，并在 PR 中注明。

## 开发环境服务启停

**任何场景下**均严禁主动或自动运行 `./dev.sh` 等编译、部署与启动脚本（包括用户说「代码已合」时）。所有的服务编译、启停和重启测试均需交由用户在控制台手动操作。修改完毕或同步完成后，Agent 仅负责通知用户代码/分支已就绪，并提醒用户自行执行 `./dev.sh`。

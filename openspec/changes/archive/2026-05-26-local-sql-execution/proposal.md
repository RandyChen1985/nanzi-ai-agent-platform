## Why

当前南孜智能体平台的 ChatBI 模块和 SQL 执行工具，高度依赖远程外部 SQL 执行服务（`yovole-yunshu-api-data-platform`）来进行物理 SQL 执行与数据源在线 Debug。这不仅带来了额外的微服务跨网延迟和鉴权开销，还增加了系统部署与容器运维的复杂度。

为了实现系统去外部解耦、降低运维成本并提升 ChatBI 执行效能，我们需要在智能体平台内部直接集成这一套 SQL 执行器逻辑（包括 ClickHouse/MySQL/Oracle 适配器及连接池），并在保留远程接口模式的同时，通过配置开关支持一键平滑切换。

## What Changes

- **新增本地数据库连接池管理与适配器**：在服务内部直接集成 ClickHouse、MySQL 及 Oracle 物理数据源的异步连接池与只读安全过滤适配器，替代对外部系统的物理直连依赖。
- **引入模式切换开关**：支持通过系统环境变量 `SQL_EXECUTION_MODE` 和数据库系统配置项 `sql_execution_mode` 进行分流。环境变量为空或 `auto` 时读取数据库动态配置；环境变量为 `local` 或 `remote` 时强制锁定对应模式。
- **重构数据工具接口与调试**：改造 `call_external_sql_api` 内部实现，并整合数据源执行调试接口，使其自动适配 `local` 与 `remote` 模式下的数据执行与结果格式化，同时保持现有工具返回格式兼容。
- **补齐本地执行保护**：本地执行仅允许 `SELECT` 查询，并统一增加最大行数、超时、返回体大小和连接池生命周期保护。

## Capabilities

### New Capabilities

- `local-sql-executor`: 支持在智能体平台内直连并安全执行物理 SQL 数据库查询，具备 local/remote 开关分流和连接池自动常驻复用、心跳检测、配置变更失效回收与服务关闭释放。

### Modified Capabilities

无。

## Impact

- **工具层代码**：影响 `app/services/ai/tools/data_api.py` 的底层调用机制（由单一的网络请求变更为基于模式开关的本地/远程分流）。
- **进程 resource 占用**：本地直连模式下，系统会在主进程中直接建立和常驻对应目标数据源的 TCP 连接池（包括 aiomysql 连接池、asynch 连接池等）。
- **环境部署**：去除了对外部 `yovole-yunshu-api-data-platform` 容器的强依赖，简化了系统一键开发部署流程。

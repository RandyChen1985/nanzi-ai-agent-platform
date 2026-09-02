# Platform MCP 全部方法接入实施计划

> **执行方式：**本计划在当前任务中直接执行。所有数据库变更仅通过已有版本化迁移管理，本次实现不直接连接或修改数据库。

## 目标

将 `architech/design/mcp-platform-inbound-service-design.md` 中已经确定的 Platform MCP 方法全部接入真实业务链路，使服务台不再把以下方法显示为“待接入”：

- `agent.list_allowed`
- `agent.invoke`
- `conversation.continue`
- `knowledge.search`（保留现有实现并统一审计）
- `metadata.list_datasets`
- `metadata.search`
- `metadata.get_dataset`
- `metadata.get_schema`
- `metadata.get_metrics`

所有方法继续共用 NanZi OAuth2/OIDC 入站认证、方法 Scope、总开关、能力组开关、OAuth Client 资源白名单和 `McpInboundAuditLog` 审计；不把 OAuth Access Token 当作 NanZi API Key 传给智能体运行时。

## 约束与验收边界

- 用户身份只取自已验证的 `McpPrincipal.user_id` 对应的当前 NanZi 用户记录，不接受工具参数中的 `user_id`。
- 用户授权模式下，智能体/元数据权限分别复用现有 `AgentManagerService` 和 `MetadataService`；Client 白名单与用户权限取交集。
- Client Credentials 仅允许管理员配置的系统智能体和元数据白名单；不允许调用用户会话和需要用户身份的智能体执行。
- `conversation.continue` 必须验证会话归属，不能仅凭会话 ID 查询或继续。
- 元数据只返回数据集、表、字段、指标描述，不返回连接凭据、行级权限配置、内部数据源地址或实际业务数据。
- 所有工具失败都写入审计；审计写失败不能覆盖业务结果。
- 只运行纯单元/契约测试、类型检查和静态检查；不启动 `./dev.sh`，不执行迁移，不做真实 OAuth/数据库/Redis/LLM 验收。

## 实施步骤

### 1. 先补 RED 测试

新增 `tests/services/mcp/test_platform_mcp_methods.py`，覆盖：

1. 九个方法定义均为 `implemented=True`，名称、Scope、能力组与设计一致。
2. 分页游标只能由 NanZi 生成，篡改或错误格式被拒绝，且分页前已经完成权限过滤。
3. 用户上下文从数据库用户记录构造，扩展字段只保留安全 JSON；不从 OAuth claims 或请求参数信任角色和用户名。
4. 用户权限与 Client 的 `allowed_agent_ids` / `allowed_metadata_dataset_ids` 取交集；Client Credentials 没有显式白名单时无资源权限。
5. Agent、会话和元数据序列化结果不包含 API Key、密码、连接信息、行过滤配置等敏感字段。
6. 元数据搜索只在已授权数据集内匹配 dataset/table/column/metric，并遵守 limit 与 resource_types。

扩展 `tests/services/mcp/test_platform_mcp.py`，验证所有工具均已注册，并验证通用审计接收方法名、Client 请求 ID、Agent/会话/数据集上下文。

先运行：

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests \
  tests/services/mcp/test_platform_mcp_methods.py \
  tests/services/mcp/test_platform_mcp.py -q
```

预期先失败，证明测试确实锁定了待实现行为。

### 2. 抽取 Platform MCP 共享授权和输出支持

在 `app/services/mcp/platform_mcp.py` 或相邻的 Platform MCP 支持模块中补充：

- 当前 OAuth Client 加载及状态校验；
- 已认证用户加载和标准 `user_info` 构造；
- Agent 与元数据资源白名单/用户权限交集；
- 平台/能力组/Scope/用户身份统一校验；
- 安全分页游标签发与校验；
- 通用请求 ID、异常状态和审计字段处理。

保持现有 `knowledge.search` 的知识库范围交集逻辑，只把硬编码的方法名改为调用方传入的方法名。

### 3. 接入 Agent 方法

使用现有 `AgentManagerService.list_allowed_agents`、`_user_can_execute_agent` 和 `AgentService.chat_completion`：

- `agent.list_allowed` 返回当前主体可执行的已启用智能体和已发布版本信息；
- `agent.invoke` 校验 Agent、用户和会话归属后调用现有聊天执行链；
- 新会话生成 `mcp_<uuid>`，已有会话必须属于当前用户；
- `conversation.continue` 从当前用户拥有的历史记录恢复 Agent，再调用同一执行链；
- 返回 request_id、conversation_id、Agent ID、正文、引用和可取得的 token usage，不返回模型内部思考内容；
- 不把入站 OAuth Access Token 作为 `api_key` 传给 `AgentService`。

运行第 1、2 组测试，进入 GREEN。

### 4. 接入 Metadata 方法

复用 `MetadataService.list_accessible_dataset_options` 和数据集查询模型：

- `metadata.list_datasets` 返回授权数据集及安全统计信息；
- `metadata.search` 在授权数据集的 dataset/table/column/metric 范围内搜索；
- `metadata.get_dataset` 返回单个授权数据集摘要；
- `metadata.get_schema` 返回授权表和字段，按需返回同数据集内的关系；
- `metadata.get_metrics` 只返回授权数据集指标及业务口径；
- 对 Client Credentials 强制使用 Client 配置的数据集白名单。

运行全部 MCP 服务测试并修正序列化、异步查询和兼容性问题。

### 5. 更新方法状态和文档

- 将方法定义的 `implemented` 状态与真实注册结果保持一致；
- 检查服务台方法表无需额外前端硬编码即可显示“已启用/已关闭”；
- 更新 `architech/design/mcp-platform-inbound-service-design.md` 的当前实现状态、方法清单和验收项；
- 如测试契约需要，补充服务台方法状态契约测试。

### 6. 回归验证与代码审阅

按风险执行：

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests \
  tests/services/mcp tests/api/test_platform_mcp_contract.py \
  tests/api/test_mcp_service_desk_contract.py -q
./frontend/node_modules/.bin/vue-tsc --noEmit
git diff --check
```

最后核对 `git status --short` 和本次变更范围。当前任务不自动 stage、commit 或 push。

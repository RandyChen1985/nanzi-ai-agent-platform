# 会话级工具授权（本会话总是允许）设计

- 日期：2026-10-28
- 状态：已确认，进入实现
- 涉及面：AgentScope 运行时权限判定、工具确认接口、SSE 事件、对话卡片前端

## 1. 背景

工具确认卡片（`frontend/src/components/chat/ToolPermissionCard.vue`）目前只有两个动作：
「拒绝执行」与「本次允许执行」。后者只对单次调用生效，同一个会话里连续的探测类命令
（例如核对挂载与可写性的一串 Bash）会让用户重复确认十几次。

需要增加「本会话总是允许」：用户授权一次后，**本会话内同名工具不再逐条询问**。

## 2. 已确认的取舍

| 维度 | 决策 |
| --- | --- |
| 放行粒度 | 按工具（工具名），不是命令文本、也不是只读命令族 |
| 存储 | Redis Set，key 带 user_id + conversation_id，TTL 24h，命中不续期 |
| 撤销 | 卡片上提供「撤销」入口，走 HTTP 接口立即生效 |
| 留痕 | 自动放行的调用在时间线里带浅色徽标「本会话已授权 · 自动执行」 |
| 实现路径 | 在工具 `check_permissions` 的 ASK 出口判定，不在中间件层拦截 |

## 3. 安全边界（硬性）

1. **只免除 ASK，不免除 DENY**。禁用工具（`enforce_tool_forbidden`）、命令黑名单
   （`enforce_command_forbidden`）、`permission_scope == "dangerous"`、删除类
   `bypass_immune=True` 的决策，全部在 ASK 出口之前返回，会话授权无法覆盖。
   `approval_mode == "deny"` 的语义同样不变。
   原生工具自己返回的 **safety ASK**（`PermissionDecision.bypass_immune=True` 的 ASK，
   例如 `curl | bash` 这类必须让用户看见的危险操作）在 AgentScope 语义里连 allow
   规则都不能覆盖，因此判定时显式排除：`bypass_immune` 的 ASK 一律不降级。
2. **不做中间件层拦截**。引擎存在先于 `check_permissions` 的只读快速通道，在中间件层
   直接返回 ALLOW 会绕过工具自身的删除保护，必须在工具内、且在危险判定之后判定。
3. **fail-closed**。取不到 `conversation_id` 或 `user_id` 时不生效（宁可不放行）。
4. **不信任前端传参**。写入授权时使用的 `conversation_id` 取自服务端挂起快照
   （`pending.snapshot.conversation_id`），不使用请求体里的值；写入发生在恢复流的鉴权
   之后（`peek_async(..., user_id=current_user_id)` 与显式归属比对）。
5. 跨会话不共享；TTL 到期自动失效；Redis 写入失败时退化到进程内存兜底，行为与
   `pending_store` 一致。**Redis 可用时它是唯一权威来源**：内存兜底只在 Redis 取不到
   连接时参与判定，否则多实例下「另一实例撤销」会被本进程的内存记录挡住 24 小时。

## 4. 数据模型

新增模块 `app/services/ai/runtime/agentscope/session_permission_grants.py`：

- Key：`{memory_service.KEY_PREFIX}:{user_id}:session_grant:{conversation_id}`
- Value：Redis Set，成员为工具名（如 `Bash`）
- TTL：`SESSION_GRANT_TTL_SECONDS = 24 * 60 * 60`，写入时设置，命中不续期
- 接口：`grant` / `revoke` / `is_granted` / `list_grants`（全部 async，失败不抛给调用方）
- 内存兜底：带过期时间戳的 dict，Redis 不可用时使用

## 5. 判定点

`AgentScopeRuntimeTool.check_permissions`（`tools.py`）在返回 `ASK` 之前判定；
`AgentScopeNativeApprovalTool.check_permissions` 则在原生工具给出**非终态**决策时判定：

```python
if await session_permission_grants.is_granted(user_id, conversation_id, self.name):
    return PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message=f"Tool '{self.name}' is allowed for this session.",
        decision_reason=f"session_grant:{self.name}",
    )
```

**原生工具必须同时覆盖 `PASSTHROUGH`**：`Bash` 执行 `mkdir`、`Write` 写工作区文件这类
普通调用，工具自己返回的是 `PASSTHROUGH` 而不是 `ASK`；引擎 Step 4 只采纳
`ALLOW`/`DENY`，两种非终态都会继续落到 Step 6 的兜底 `ASK`。只认 `ASK` 会让授权在主
路径（Bash）上完全不生效——按钮点了、授权写了，下次照样弹框。因此判定条件取
`behavior in {ASK, PASSTHROUGH}`，并排除 `bypass_immune` 与 `DONT_ASK` 模式。

`conversation_id` 由 `build_toolkit` / `runtime_tool_from_spec` /
`runtime_tool_from_native` 一路显式传入（调用方已有 `self.conversation_id`），
缺失时回退 `AgentContext.conversation_id`，再缺失则不生效。

自动放行的标记走「判定点记账、结果侧出队」：`_session_grant_decision` 命中时把工具名
压入 `AgentContext.session_grant_auto_approved`，`on_tool_result_end` 消费一次并给出
`auto_approved`。刻意**不在结果侧反查 Redis**——反查只能看出「本会话授权过这个工具名」，
会把只读快通道自动放行的 `ls`、`approval_mode=allow` 的工具、以及刚被 DENY 的调用
一起误标成「自动执行」，还会给每次工具调用多加一次 Redis 往返。本次「显式确认」的那个
`tool_call_id` 同样排除。

## 6. 接口

- `ToolPermissionConfirmRequest` 增加 `scope: Literal["once", "session"] = "once"`。
  `scope="session"` 且 `confirmed=true` 时，在恢复执行前先写授权记录。
- 新增 `GET /api/v1/chat/permissions/grants/{conversation_id}`：列出本会话已放行工具。
  注意历史回放只重建时间线、不重建确认卡片，撤销入口目前只在本轮页面内可见（接口本身
  已经可以用，后续接线时不必再改后端）。
- 新增 `DELETE /api/v1/chat/permissions/grants/{conversation_id}/{tool_name}`：撤销。
- 权限校验：`require_api_key`，只允许操作自己的会话。

## 7. 事件与前端

- `permission` SSE 事件新增 `supports_session_grant: bool`：由工具 `permission_scope`
  决定（`read` / `dangerous` 不给该选项），前端据此决定是否渲染第三个按钮。
- 工具结果事件在自动放行时带 `auto_approved: true`，时间线展示浅色徽标；该字段随
  `finalize_process_timeline` 落库（白名单压缩不能把它洗掉），刷新/回放仍可见。
- 卡片新增状态 `approved_session`：转灰、折叠、显示「已允许（本会话内不再询问）」+「撤销」。
- 两个使用页面 `AgentDebug.vue`、`EmbedChat.vue` 的确认请求体增加 `scope`，
  并处理新状态与撤销调用。

## 8. 测试

后端 pytest：

- store：写入/命中/撤销/TTL 语义/用户与会话隔离/Redis 不可用时的内存兜底
- `check_permissions`：命中返回 ALLOW；黑名单、禁用工具、删除类 ASK 仍然优先；
  `approval_mode="deny"` 仍然 DENY；`conversation_id` 缺失不生效
- 接口：`scope="session"` 写入授权、`scope="once"` 不写入、撤销后重新回到 ASK

前端契约：

- 三按钮渲染与 `supports_session_grant` 开关
- 确认请求体带 `scope`
- `approved_session` 文案与撤销入口存在
- `vue-tsc --noEmit` 零报错

数据库：无变更（纯 Redis），不新增迁移。

## 9. 明确不做

- 「本会话内总是拒绝」
- 跨会话或按智能体的长期授权
- 按命令内容/路径的规则匹配（上游 `suggested_rules` 未实现完整，不在本次范围）

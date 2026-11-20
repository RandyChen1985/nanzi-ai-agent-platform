# 子智能体工具授权交回主会话（A + B1）设计

日期：2026-11-20
状态：设计已确认，进入实现
关联缺陷：团队模式下子智能体执行 Bash 需要确认时，主专家只能报错「当前委派模式无法在子流程中完成确认」

## 1. 背景与核实结论

用户反馈：团队模式下主专家委派的子智能体执行 Bash，需要用户确认工具权限，但确认弹窗无法在主会话弹出，只得到一条错误文案。

核实结果（代码 + 实测）：

1. 错误文案来自 `app/services/ai/tools/agent_delegate_tool.py`
   `DELEGATION_INTERRUPT_MESSAGES["permission_required"]`。委派层把 `permission_required`
   列入 `INTERRUPT_SSE_TYPES`，一旦子代理流出现该事件就终止委派并返回固定文案，
   **不转发确认卡**。
2. 委派链上只有 `ask_user_question` 存在「子代理 → 父会话」的转发通道，权限确认没有。
3. 实测 `runtime_tool_from_native(Bash(), approval_mode="allow").check_permissions()`：
   `approval_mode == "allow"` 时直接 ALLOW，Bash 不会产生确认事件。因此「自动批准」下
   本不该报这个错；报错必然发生在 `ask`（或权限选项丢失）的那一轮。
4. 错误文案给出的两条出路都不成立：子代理运行在内部会话 `child_session_{run_id}`，
   用户无法打开；系统配置里没有「工具自动执行策略」这个键。

因此问题不是偶发故障，而是**能力缺口**：委派链缺少把权限确认交回主会话的通道。

## 2. 范围

**A（文案与指引）**

- 委派层不再输出「当前委派模式无法在子流程中完成确认」，改为「子智能体请求执行 X 需要你授权，
  本次委派已暂停（N 秒内有效）。请在确认卡里允许或拒绝」。
- 删除「请直接打开对应子智能体对话」这条不存在的出路。

**B1（能力）**

- 子智能体请求权限 → 挂起 → 主会话弹出确认卡 → 用户允许/拒绝 → 子智能体**续聊**继续跑完
  → 父专家汇总。

**明确不做**

- B2：同一条 SSE 流内等待用户确认、当场继续执行（与「一轮一请求」模型冲突，风险高）。
- `dangerous` 作用域与命令黑名单：保持硬 DENY，不弹卡。
- 不新增数据库表、不改迁移。

## 3. 数据流

```
子代理 check_permissions 返回 ASK
  → AgentScope REQUIRE_USER_CONFIRM
  → 子代理 event_stream 生成 permission_required 事件，注册挂起快照
  → 委派层捕获该事件：
       · 把事件（含 permission_request_id、子代理元信息）转发进父会话事件队列
       · 本次委派以「等待授权」结束，返回暂停文案（不再当作 error）
  → 前端现有 permission_required 卡片渲染（标注「来自子智能体 X」）
  → 用户点「允许这一次 / 总是允许 / 拒绝」
  → POST /api/v1/chat/permissions/{id}/confirm
       · 识别快照带 delegation 元信息 → 走「子代理授权」分支
       · 允许这一次：写一次性令牌（Redis，TTL 120s，用完即删）
       · 总是允许：写会话授权，键含子代理维度
       · 拒绝：只记录拒绝事实，不落持久授权
       · 返回续聊指令（resume_directive）
  → 前端把续聊指令发到父会话：「【用户授权】…请用 sub_agent_call(resume=True) 继续」
  → 父专家 resume 子代理；子代理命中授权记录直接执行（或拿到拒绝事实换方案）
  → 子代理跑完 → 父专家汇总并回答用户
```

## 4. 组件改动

| 位置 | 改动 |
| --- | --- |
| `app/services/ai/runtime/agentscope/pending_store.py` | 快照新增可选字段 `delegation: dict \| None`，`register` 的 `asdict` 与 `_from_dict` 两处同步 |
| `app/services/ai/runtime/agentscope/event_stream.py` | `stream_pending_tool_interrupt` 注册快照时，若当前 `AgentContext` 有 `parent_conversation_id` / `delegation_run_id`，写入 `delegation` 元信息；`permission_required` 事件 payload 增加 `subagent` 字段 |
| `app/services/ai/tools/agent_delegate_tool.py` | `_consume_sub_agent_stream` 对 `permission_required` 改为「转发 + 暂停」；`DELEGATION_INTERRUPT_MESSAGES` 文案重写；挂起不再记 `error` 状态 |
| `app/services/ai/runtime/agentscope/tools.py` | 决策链在 `_session_grant_decision` 之前/之后接入一次性令牌消费；会话授权判定带上子代理维度 |
| `app/services/ai/runtime/agentscope/session_permission_grants.py` | 授权键支持 agent 维度（该子代理本会话），保持旧键兼容 |
| 新模块 `grant_once`（可挂在 session_permission_grants 内） | 一次性令牌：`grant_once` / `consume_once`，TTL 120 秒 |
| `app/services/ai/agent_service.py` + `app/api/v1/endpoints/chat.py` | confirm 端点识别子代理快照，走授权分支并返回 `resume_directive` |
| `frontend/src/views/EmbedChat.vue`、`frontend/src/utils/agentscopeSseHandlers.ts` | 卡片标注子智能体名；确认结果带 `resume_directive` 时自动向父会话发续聊消息 |

## 5. 授权记录

| 类型 | 存储 | 生效范围 | 生命周期 |
| --- | --- | --- | --- |
| 允许这一次 | Redis 一次性令牌，键含 `user + 父会话::sub::子代理 + 工具名 + 参数指纹` | 仅该次调用 | 120 秒，`GETDEL` 原子消费即删 |
| 总是允许 | `session_permission_grants`，键为 `父会话::sub::子代理` | 该子代理在本会话内的这类工具 | 与现有会话授权一致，可撤销 |
| 拒绝 | 仅作为续聊指令里的文本事实 | 该次 | 不落持久授权 |

三条实现约束：

1. **作用域只能从平台上下文取**：`check_permissions(tool_input, context)` 的 `context`
   是 AgentScope 的 `PermissionContext`（只有 `mode` / 规则 / 工作目录），没有父会话，
   委派身份必须走 `get_current_agent_context()`。
2. **一次性令牌与「本会话允许」共用一道安全门禁**：只把 `ASK` / `PASSTHROUGH` 降级为
   `ALLOW`，`bypass_immune` 的 safety ASK 与 `DONT_ASK`（无人值守）一律不放行、也不消费令牌。
3. **撤销要用写入时的作用域**：后端在 `permission_result.session_grant.conversation_id`
   回传该键，前端撤销优先用它，否则删的是主会话里一条不存在的记录。

「总是允许」只对该子代理生效：其他子代理、其他工具仍然逐次确认。

## 6. 边界与异常

- **600 秒未确认**：快照过期，父会话下一轮提示「授权请求已过期，请重新发起委派」，不静默失败。
- **关页面/断线**：快照在 Redis 中，前端「恢复流」逻辑可续上；超过 TTL 才作废。
- **并行多子代理同时挂起**：本期**不做排队**。前端只有一个 `pendingPermission` 槽位，
  后到的挂起会覆盖前一张卡片；被覆盖的挂起仍留在 Redis 里，600 秒内可通过恢复流找回，
  但界面上看不到。多子代理并发请求授权属于已知限制，排队与折叠提示留到后续版本。
- **拒绝后再次请求同一工具**：一次性令牌不写入，卡片会再弹一次（有意为之）。
- **父专家不遵守续聊指令**：子代理授权记录已写入，父专家若直接重跑任务，工具同样能过；不会退化成死循环。
- **续聊指令发不出去**：确认流收尾时父专家那一轮可能仍在输出，`sendMessage` 会被运行锁
  挡回。前端轮询等待运行态释放（最多 6 秒），仍等不到就把「发送『继续』」的提示写进正文，
  不静默丢弃指令。
- **无用户在线（定时/订阅）**：`unattended` 场景不弹卡；即使存在一次性令牌，`DONT_ASK`
  模式下也不放行，沿用现有拒答路径。

## 7. 测试

- 后端：
  - 委派层捕获 `permission_required` 后不终止、转发事件、返回暂停文案（不是 error）。
  - 快照 `delegation` 元信息写入与读回（Redis asdict / `_from_dict` 双向往返）。
  - confirm 三分支：一次性授权 / 会话授权 / 拒绝，各自写对存储。
  - 一次性令牌被消费后立即失效（第二次不再放行）。
  - 会话授权按 agent 维度隔离：A 子代理的授权不放行 B 子代理。
  - 文案不含「请直接打开对应子智能体对话」。
- 前端契约：
  - `permission_required` 带 `subagent` 时卡片显示子智能体名。
  - 确认结果带 `resume_directive` 时构造续聊消息发往父会话。
- 集成：团队模式 Bash 挂起 → 允许 → 子代理续聊跑完（fake runner 跑通）。

## 8. 风险与回滚

- 风险最高的改动是「挂起不再当 error」：若某处依赖 `interrupt_type == "permission_required"`
  判定失败态，会把失败显示成成功。测试必须覆盖 run_status 与子代理结果状态。
- 回滚方式：委派层转发分支与 confirm 子代理分支都是独立 `if`，删除该分支即可退回原行为；
  快照新字段是可选字段，旧快照读回不受影响。

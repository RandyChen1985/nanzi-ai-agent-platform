# 渐进式收尾：让模型在步数用尽前作答

## 背景

AgentScope 的 `MiddlewareBase` 暴露 7 个钩子，本项目实现了 3 个
（`on_model_call` / `on_check_permission` / `on_reply`），`on_reasoning` 一直空着。

缺口在「步数用尽」这一刻。`agent_max_iterations` 在数据库 seed 为 `20`
（`db-prod/V1-create_system_configs.sql:19`、`db-prod-pg/V0-baseline.sql:1374`），
代码兜底 `5`/`5`/`6`（assistant / knowledge / data），data 类另有硬上限
`DATA_QUERY_MAX_STEPS_CAP = 20`。无论配置多少，达到上限时框架发出
`ExceedMaxItersEvent`，我们的处理是：

```python
# app/services/ai/runtime/agentscope/event_stream.py:797
if event_type == "EXCEED_MAX_ITERS":
    yield {"content": AssistantPrompts.MAX_STEPS_REACHED}
    return
```

即输出一句「[系统提示] 达到最大执行步骤，停止执行。」
（`app/services/ai/executors/prompts.py:2506`），此前所有工具结果全部丢弃。
用户付了 20 轮工具调用的成本，得到一句模板话。

需要区分的是：**工具循环已有成熟防护**，本设计不重复建设。
`app/services/ai/runtime/tool_loop_detector.py`（280 行）已实现同参重复 5 次熔断、
全局 50 次熔断、ping-pong 6 次熔断、时间类工具阈值 2、浏览器观察类豁免，
经 `assistant_agent_runner.py:2155` 创建、`tools.py` 四处传参实际生效。
它管的是「无意义的重复」，管不到「合法的多步任务被总轮次截断」——
后者正是本设计针对的缺口。

## 目标

- 步数将尽时，让模型基于**已有**信息收尾，而不是让用户拿到模板话。
- 以**新增中间件**实现，不修改现有链路；出问题可通过配置整体关闭。

## 非目标

- 不做工具并行（框架 `_agent.py:2269` 已用 `asyncio.gather` 内建）。
- 不做工具超时（`tool_timeout.py` 已有，默认 180s，可配 1~3600）。
- 不做工具循环检测（`tool_loop_detector.py` 已有）。
- 不做工具耗时采集。曾设计过 `ToolMetricsMiddleware`，因暂无消费方而撤除，
  留待有明确分析需求时再做。
- 不做轮中 token 预算。框架自动压缩（`trigger_ratio` 默认 0.8）已防上下文爆炸；
  且压缩调用的 usage 在 `on_compress_context` 钩子中不可见
  （`_compress_context_impl` 就地消费 `res.usage`，未写入任何状态），
  预算判断会系统性偏低。
- 不改现有 `ExceedMaxItersEvent` 的处理——收尾成功时该事件不会出现；
  收尾失败（模型仍调工具）时旧逻辑照旧兜底。

## 方案：GracefulFinishMiddleware（`on_reasoning`）

### 触发条件

```text
remaining = agent.react_config.max_iters - agent.state.cur_iter
reserve   = 1 if max_iters <= 8 else 2

触发 = remaining <= reserve
       AND 最近一条 assistant 消息没有文本输出
```

`reserve` 随配置自适应：配 5 留 1（占 20%），配 20 留 2（占 10%）。
数据库 seed 为 20，实际走 `reserve = 2`。

**产出检测**的意图：若模型已经在本轮产出文本（通常意味着它在给答案），
不再打断；只有它仍在埋头调工具时才催收尾。这是减少误伤的关键——
单纯的步数判断会在模型正要输出结论的那一轮强行改成 `tool_choice=none`。

### 干预动作

1. 向 `agent.state.context` 追加一条 `HintBlock`（字段名是 `hint`，不是 `text`）：

   ```text
   你已用到 {cur_iter}/{max_iters} 步，接近本次任务的步数上限。
   请立即基于已获得的信息给出最终答复，不要调用任何工具。
   如果信息不完整，说明：已知什么、缺什么、建议用户下一步做什么。
   ```

2. 将转发给 `next_handler` 的 `kwargs["tool_choice"]` 改为 `ToolChoice(mode="none")`。

干预只作用于**当前这一次**推理调用，不写回 `agent` 的持久属性。

### 开关

新增系统配置项 `agent_graceful_finish_enabled`（默认 `true`）。

开关由中间件**在首次推理时懒加载并缓存**，`build_runtime_middlewares()` 保持同步签名。
最初设计为把构建函数改成 `async` 以便读配置，但核实后发现该函数被 5 个测试文件
同步调用（`test_agentscope_v2_0_6_features.py`、`test_runtime_middlewares_langfuse.py`、
`test_subagent_model_call_stats.py`、`test_forbidden_resource_bypass_paths.py`），
改签名会波及全部装配契约测试。改为懒加载后测试零改动。

读取失败或配置缺失一律按**开启**处理——该中间件是改善型能力，
但静默失效会掩盖问题，因此异常时记 warning 并保持开启。

前端在 `SystemConfig.vue` 中以胶囊开关呈现，位置紧随 `agent_max_iterations`。
注意：`tests/frontend/test_boolean_config_toggle_contract.py` 会从迁移 SQL 反推
所有布尔配置项，并要求分支体内出现字面量 `item.value === 'true'`，
因此不能写成 `(item.value ?? 'true') === 'true'`。

## 组件与依赖

| 组件 | 文件 | 职责 |
|---|---|---|
| `GracefulFinishMiddleware` | `app/services/ai/runtime/agentscope/middleware.py` | 步数将尽时注入提示并禁用工具 |
| 注册 | `agent_runtime.py: build_runtime_middlewares()` | 无条件挂载（开关在中间件内） |
| 配置迁移 | `db-prod/V199-*.sql`、`db-prod-pg/V99-*.sql` | seed `agent_graceful_finish_enabled` |
| 前端开关 | `frontend/src/views/SystemConfig.vue` | order 排序 + 胶囊开关分支 |

中间件顺序（`build_runtime_middlewares` 返回值由外到内）：

```text
1. ToolPermissionMiddleware         现有
2. BashSandboxParentLinkMiddleware  现有
3. ModelCallStatsMiddleware         现有（有会话时）
4. GracefulFinishMiddleware         新增（在追踪之前，让 span 记录到干预后的最终参数）
5. TracingMiddleware                现有
6. LangfuseContextMiddleware        现有
```

## 错误处理

| 场景 | 行为 |
|---|---|
| `agent.react_config` 或 `agent.state` 属性缺失 | 直接透传，不干预 |
| 读配置失败 | 按开启处理，记 warning |
| `max_iters` 为 0 或负数 | `remaining <= reserve` 立即成立，但产出检测仍会先判断 |
| 模型被禁用工具后仍尝试调用 | 框架拒绝并回注错误，旧逻辑不受影响 |

## 测试

`tests/ai/runtime/test_agent_middleware_graceful_finish.py`，7 个用例：

| 用例 | 验证点 |
|---|---|
| 剩余 1 步 + 无文本产出 | 注入 HintBlock，`tool_choice` 变为 `none`，提示含 `19/20` |
| 剩余 1 步 + 已有文本产出 | **不**干预 |
| 剩余充足 | 不干预，`next_handler` 参数原样 |
| 开关关闭 | 不干预，`_enabled` 为 False |
| 读配置异常 | 默认按开启处理 |
| `reserve` 缩放 | 5/8 → 1，9/20 → 2 |
| agent 结构异常 | 透传且不抛异常 |

不依赖真实 LLM / Redis / 数据库，均以 fake 对象驱动。

## 风险

| 风险 | 缓解 |
|---|---|
| 强制 `tool_choice=none` 与 HITL 确认流冲突，导致最后一轮卡住 | 需针对 `require_user_confirm` 场景单独验证；必要时在检测到未决确认时跳过干预 |
| 收尾过早，模型还没收集关键信息 | 产出检测 + `reserve` 随配置缩放 |
| 中间件顺序影响事件流 | 只改入参和 state，不改写事件内容 |

## 实施顺序

1. `middleware.py` 新增 `GracefulFinishMiddleware`
2. `agent_runtime.py` 注册（保持同步签名，开关在中间件内懒加载）
3. 迁移 SQL（MySQL V199 / PG V99）
4. 前端开关（order + 胶囊分支）
5. 契约测试
6. 人工验证：构造一个必然超出 `max_iters` 的任务，对比收尾前后的输出

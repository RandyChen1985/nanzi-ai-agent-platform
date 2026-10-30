# AgentScope 2.0.11 升级报告（含测试债务盘点）

> 升级日期：2026-10-29
> 分支：`dev-agentscope-mcp2.x`（基线 `f8e7de60`）
> 版本：`agentscope 2.0.9 → 2.0.11`，`mcp 1.29.1 → 2.3.0`
> 结论：**升级完成，现有功能行为不变；3 处行为变化需知情，2 处成本需确认**
> 本文件面向「独立验收」；评估过程与特性规划见 [agentscope_2.0.11_upgrade_assessment.md](./agentscope_2.0.11_upgrade_assessment.md)
> 部署后的手工回归清单见 [agentscope_2.0.11_acceptance_checklist.md](./agentscope_2.0.11_acceptance_checklist.md)（P0 三项 10 分钟覆盖 90% 风险）

---

## 1. 一页结论

| 维度 | 结论 |
|---|---|
| 代码改动 | 9 个源文件 + 14 个测试文件 + 1 个文档，`+544 / −236` 行（升级本身 `+265/−98`，其余是测试债务清理与 §4.5 的隐患修复） |
| 功能删除 | **无**。上游公共 API `+15 / −1`（删的是 `realtime.TruncationSupport`，未使用）；平台引用的 61 个符号全部存活 |
| 行为变化 | 3 处，见 §4 |
| 成本变化 | 2 处，见 §4.3 |
| 全量测试 | 升级后首跑 `11431 passed / 13 skipped / 62 xfailed / 5 failed`；**测试债务清理后 11450 passed / 13 skipped / 42 xfailed / 5 failed**（§5.1） |
| 那 5 个 failed | **存量问题**（`git stash` 撤掉本次改动后仍失败），非本次引入，见 §8.1 |
| 已知失败基线 | 62 条 → **41 条**（清理 21 条：20 条转为通过、1 条用例删除），零新增，见 §8.4 |
| 必须人工验收 | 3 项，见 §9 |

---

## 2. 版本与依赖变更

| 依赖 | 变更 |
|---|---|
| agentscope | 2.0.9 → **2.0.11** |
| mcp | 1.29.1 → **2.3.0**（2.0.11 起上游强制 `mcp>=2.0.0`，不能只升 agentscope） |
| mcp-types | 新增 2.3.0（mcp 2.x 的拆分包） |
| 新增运行时依赖 | `aiortc 1.15.0`、`av`、`aioice`、`pylibsrtp`、`pyopenssl`、`dnspython`、`ifaddr`、`google-crc32c` |
| certifi | 2026.7.22 → 2026.10.23 |

新增依赖来自上游 `service` extra 里的 WebRTC/实时语音支持，属于 `agentscope[service]` 的传导依赖。

---

## 3. 代码改动清单

### 3.1 源码（9 个）

| 文件 | 改动 |
|---|---|
| [requirements.txt](../../requirements.txt) | 两个版本锁 + 注释重写 |
| [platform_mcp.py](../../app/services/mcp/platform_mcp.py) | `FastMCP`→`MCPServer`；端点参数搬到新工厂 `platform_mcp_http_app()`；新增 `_PLATFORM_TRANSPORT_SECURITY` + `get_platform_transport_security()`；`AuthSettings(validate_token_resource=True)`；请求体 32 MiB / 会话空闲 1 h |
| [echo_server.py](../../app/services/mcp/echo_server.py) | 同上（`echo_mcp_http_app()` / `_ECHO_TRANSPORT_SECURITY`） |
| [public_url.py](../../app/services/mcp/public_url.py) | 运行期刷新改用各模块 getter |
| [main.py](../../app/main.py) | 两处 `mount` 改用工厂函数 |
| [mcp_service.py](../../app/api/portal/endpoints/mcp_service.py) | 在线调试改用 `platform_mcp_http_app()` |
| [mcp_client.py](../../app/services/ai/tools/mcp_client.py) | 新增 `_tool_input_schema()`：`input_schema`→`inputSchema`→`parameter_schema` 三级回退；**复审后补修**：`sse_client` 的 `httpx_client_factory` 改为返回 `httpx2.AsyncClient`（见 §12） |
| [workspace_container_mcp.py](../../app/services/ai/runtime/agentscope/workspace_container_mcp.py) | 内联 stdio 脚本兼容 mcp 1.x / 2.x |
| [workspace_ssh.py](../../app/services/ai/runtime/agentscope/workspace_ssh.py) | 同上；`_offload_data_block` 改产 `workspace:///`（原 `file://`） |
| [docker_template_patch.py](../../app/services/ai/runtime/agentscope/docker_template_patch.py) | uv 换源锚点支持两代模板；都不命中时打 warning |
| [build-k8s-sandbox-image.sh](../../k8s_deploy/build-k8s-sandbox-image.sh) | **复审后补修**：`BASE_REQS` 跟随上游 2.0.11（`mcp>=2.0.0` / `httpx2` / `certifi`），并让 Dockerfile 从 `BASE_REQS` 单源展开（见 §12） |

### 3.2 测试（8 个）

| 文件 | 改动 |
|---|---|
| [test_docker_template_patch.py](../../tests/ai/runtime/test_docker_template_patch.py) | 断言从固定 `.../uv/install.sh` 改为 `astral.sh/uv` 前缀 |
| [test_agentscope_workspace.py](../../tests/ai/runtime/test_agentscope_workspace.py) | 内联脚本打桩对象改为 `mcp.server.mcpserver.MCPServer` |
| [test_platform_mcp.py](../../tests/services/mcp/test_platform_mcp.py) | 用 `platform_mcp_http_app()` 构造被测应用 |
| [test_platform_mcp_contract.py](../../tests/api/test_platform_mcp_contract.py) | 源码契约断言同步 |
| [test_mcp_echo_server_api_contract.py](../../tests/api/test_mcp_echo_server_api_contract.py) | 同上 |
| [test_mcp_public_url_sync.py](../../tests/services/test_mcp_public_url_sync.py) | **新增**回归用例：`apply_public_url_to_mcp` 必须落到两个模块级实例 |
| [test_mcp_client_sse_transport.py](../../tests/services/mcp/test_mcp_client_sse_transport.py) | **复审后新增**：工厂必须返回 httpx2 客户端 + 真实 SSE 端点上的 `McpSseSession.connect()` 端到端握手（见 §12.1） |
| [test_k8s_sandbox_image_gateway_deps.py](../../tests/test_k8s_sandbox_image_gateway_deps.py) | **复审后新增**：预置镜像依赖清单必须等于上游 `_GATEWAY_BASE_REQUIREMENTS` + `GATEWAY_EXTRA_PIP`（见 §12.2） |

---

## 4. 影响面

### 4.1 三处行为变化

| 变化 | 触发场景 | 现状 | 需要的动作 |
|---|---|---|---|
| 并行工具取消改为 `raise CancelledError`（旧版吞掉后返回） | 用户点「停止」 | 平台 `normalize_runtime_error` 会归一化为 `RuntimeCancelledError`；12 个取消/恢复用例通过 | **人工点一次停止**（§9-2） |
| 危险路径检查改为先 `realpath` 解符号链接 | 沙箱内通过软链访问受限路径 | 安全收紧，方向正确 | 无；极端情况下原本能读的软链路径会被拒 |
| DataBlock 引用 `file://` → `workspace:///` | SSH 沙箱的媒体引用 | 已同步改 `workspace_ssh.py`；`file://` 原本指向远端绝对路径，平台侧本就无法解析 | 若有自建消费端需知晓 |

### 4.2 一处隐蔽修复（升级前会静默失效）

`mcp 2.x` 的 `MCPServer.settings` **不再持有** `TransportSecuritySettings`。升级前的写法：

```python
getattr(getattr(instance, "settings", None), "transport_security", None)  # → None
```

不会报错，但会让「系统配置里的对外地址」保存后**永久不生效**（MCP 白名单还是旧的）。已改为各模块的 getter，并加了回归用例锁死。

### 4.3 两处成本

| 成本 | 说明 | 可选项 |
|---|---|---|
| 后端镜像变大 | 8 个 WebRTC 相关依赖 | 若可去掉 `service` extra 的 WebRTC 部分可省，需要先核实上游 import 图 |
| 沙箱镜像必须重建 | 模板内容变了（多 `update-ca-certificates`、uv 安装方式变），`compute_image_tag()` 会算新 tag | 无，必须重建一次 |

### 4.4 兼容保险

沙箱内联 MCP 脚本写成：

```python
try:
    from mcp.server.mcpserver import MCPServer as _SandboxMCPServer
except ImportError:  # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as _SandboxMCPServer
```

沙箱网关的 mcp 大版本由镜像决定，旧镜像（mcp 1.x）也不会崩。

---

### 4.5 升级中发现并修复的平台侧隐患（Playground 在线调试必失败）

**问题**：`mcp 2.x` 的 `MCPServer.streamable_http_app()` 每次调用都会 `self._session_manager = <新建的 StreamableHTTPSessionManager>`。而只有经 `run()`（由 `platform_mcp_lifespan` 负责）启动过的 manager 才能处理请求。原来的工厂 `platform_mcp_http_app()` 每次调用都重建应用：

- `app/main.py` 挂载时创建一次（宿主 → 有 lifespan ✅）
- `mcp_service.test_playground_method()` 每次「在线调试」**再创建一次**（无 lifespan ❌）

**实测复现**（本机 ASGI 直连，非推测）：

```
① 宿主已挂载的 app            → 200 {"jsonrpc":"2.0","id":1,"result":{}}
② Playground 新建的 app       → RuntimeError: Task group is not initialized. Make sure to use run().
③ 再请求宿主已挂载的 app       → 200 {"jsonrpc":"2.0","id":1,"result":{}}
```

**修复**：`platform_mcp_http_app()` / `echo_mcp_http_app()` 改为模块级缓存（`_PLATFORM_HTTP_APP` / `_ECHO_HTTP_APP`）。修复后：

```
① 宿主已挂载的 app            → 200
   Playground 与宿主是同一实例  → True
   Playground 调用后 manager 变了吗 → False
② Playground（复用宿主 app）   → 200
③ 再请求宿主已挂载的 app       → 200
tools/call 走通分发层          → 200 {"result":{"content":[{"text":"Unknown tool: no_such_tool"}],"isError":true}}
```

**两处需要澄清的外部说法**（避免后续误判）：

| 说法 | 核实结论 |
|---|---|
| 「会把宿主正在运行的 `session_manager` 篡改掉，主应用一起崩」 | **不成立**。`streamable_http_app()` 内部用 `StreamableHTTPASGIApp(session_manager)` 闭包捕获了本次的 manager，属性被覆盖不影响已挂载应用。实测 ③ 仍 200 |
| 「管理员在线调试必崩（HTTP 500）」 | **现象对、状态码不对**。`test_playground_method` 有 `except Exception` 兜底，页面看到的是 `status: failed` + 「Task group is not initialized」错误文本，不是 500 |
| 「属性被覆盖完全无害」 | **也不成立**。`platform_mcp.session_manager` 确实被换成未 run 的新实例，任何后续读该属性的代码（重新进入 lifespan、二次挂载）都会踩到；缓存后该隐患消失 |

**回归测试**（2 条，锁两个不变量）：

- `tests/services/mcp/test_platform_mcp.py::test_platform_mcp_http_app_is_cached_singleton`
- `tests/services/mcp/test_mcp_echo_server.py::test_echo_mcp_http_app_is_cached_singleton`

### 4.6 确认卡「提交中」卡顿与输入框锁语义（本轮优化）

**现象**：点「本会话总是允许」后，卡片长时间停在「正在提交确认…」、三个按钮全灰。

**根因（时序，不是死锁）**：`resume.py` 里确认回执排在「重建 runner + 恢复执行上下文」之后才 yield：

```python
runner = build_agentscope_runner_from_pending(...)      # 装配工具集/技能/MCP
await restore_runner_execution_context(...)             # 若干次 DB 读取
yield {"type": "permission_result", ...}                # ← 回执才在这里发出
```

这段时间流里一个字节都没有，前端 `isSubmitting` 只在整个 SSE 流结束的 `finally` 才复位 → 卡片一直「提交中」。

**优化（2 处）**：

| 文件 | 改动 | 效果 |
|---|---|---|
| `app/services/ai/runtime/agentscope/resume.py` | `yield permission_result` 提到重建 runner **之前** | 用户点完立刻看到「已允许（本会话）」；回执本身不依赖 runner，执行语义不变 |
| `frontend/src/utils/agentscopeSseHandlers.ts` | 收到 `permission_result` 时同时 `isSubmitting = false` | 后续 Agent 跑几十秒也不会一直显示「提交中」 |

**输入框锁语义核实**（`AgentDebug.vue:5358` / `EmbedChat.vue:1399`）：

```
ChatInput :is-processing = isProcessing || remoteRunBusy || pendingPermissionLocking
          :is-submitting  = sendLocked
→ 内部 isInteractionLocked = isProcessing || isSubmitting
```

| 场景 | 是否锁 | 靠谁 |
|---|---|---|
| 卡片挂着、未确认 | 🔒 | `pendingPermissionLocking`（`status === "pending"`） |
| 点确认瞬间 | 🔒 | `isProcessing` + `pendingPermissionLocking` |
| 回执已到、Agent 执行中 | 🔒 | `isProcessing`（此时 `pendingPermissionLocking` 已为 false） |
| 本轮正常结束 | 🔓 | 两者都 false |
| 点「拒绝执行」/「放弃本轮」/ 卡片过期 | 🔓 | 卡片 `status` 被置为 rejected / expired |

**结论：正常流程不会提前释放输入框**，`isProcessing` 在点击时就置真，一直兜到流结束。

**三个既有缺口**（都不是本次升级引入，未修）：

1. **刷新 / 断线后**：确认卡只在收到 `permission_required` 事件时创建，刷新后**不会重建** → `pendingPermissionLocking` 与 `isProcessing` 都为 false → 输入框解锁，但后端挂起快照仍在（`resume.py` 注释明确说"刷新时快照仍然在"）。用户既看不到卡、也不知道那轮挂着，只能重发；旧快照最终过期。
2. **流异常提前结束**：`finally` 里 `isProcessing = (status === "pending")`，若此时 `status` 已变 `approved_session` → 解锁，但后端可能还在跑 → 可发新消息造成并发。
3. **后端没有「查询挂起确认」的接口**（`chat.py` 里没有 `list_pending` 之类），所以缺口 1 前端**做不到**自愈。

**验证**：`tests/ai/executors/test_chat_executor.py` → 39 passed；`vue-tsc --noEmit` → 0 error；前端契约 3539 passed。

---

MCP 相关测试：**179 passed / 1 xfailed**（`tests/services/mcp`、`test_mcp_public_url_sync`、3 个 API 契约、客户端重试与同步）。

---

## 5. 验证结果

### 5.1 全量

升级完成时（测试债务尚未清理）：

```
pytest tests -q
→ 11431 passed, 13 skipped, 62 xfailed, 5 failed  (572s)
```

测试债务清理后（本轮收尾）：

```
pytest tests -q
→ 11451 passed, 13 skipped, 41 xfailed, 5 failed  ②
```

| 指标 | 清理前 | 清理后 | 说明 |
|---|---|---|---|
| passed | 11431 | **11451** | +20：20 条用例从基线转为真实通过 |
| xfailed | 62 | **41** | 21 条移出基线（20 条修好 + 1 条用例删除） |
| failed | 5 | **5** | 无变化，全部是前端存量失败（§8.1），与本次升级无关 |
| skipped | 13 | 13 | 无变化 |

- xfailed 条数始终与 `tests/known_failures.txt` **完全一致**，说明基线机制正常、无新增失败
- 那 5 个 failed 见 §8.1（存量）

> ② 口径说明：全量**实测**的那一轮是 `11450 passed / 42 xfailed`（对应"20 条移出基线"）。之后又修好了 `test_data_agent_runner_reports_missing_reusable_result`（§8.4-C 末行），它由 xfailed 转为 passed，故推算为 `11451 / 41 xfailed`。这一条已单独验证通过（`1 passed`），未再整轮复跑。

### 5.2 分层

| 测试集 | 结果 |
|---|---|
| `pytest tests/ai -q` | 2845 passed / 32 xfailed |
| `pytest tests/api -q` | 1335 passed / 8 xfailed |
| `pytest tests/services -q` | 2376 passed / 11 skipped / 5 xfailed |
| `pytest tests/services/mcp tests/api/test_platform_mcp_contract.py -q` | 108 passed / 1 xfailed |
| `pytest tests/services/test_mcp_public_url_sync.py -q` | 4 passed |
| `pytest tests/ai/runtime/test_docker_template_patch.py -q` | 16 passed |
| `verify_agentscope_imports()` | `ok=True`，`missing=[]` |
| 渲染 Dockerfile 抽查 | uv 换源生效、`update-ca-certificates` 保留、`node:22-slim` 正常 |

### 5.3 未跑的部分

`vue-tsc --noEmit` **未跑**（本次未改前端任何文件）。如果需要，命令是：

```bash
cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc --noEmit -p tsconfig.app.json
```

---

## 6. 自动获得的能力增强（无需改代码）

筛选自上游 105 个 fix，只列与平台链路相关的。

| 增强 | 上游提交 | 实际收益 |
|---|---|---|
| 危险路径检查先解符号链接 | `eadb16f3` | 沙箱逃逸防护变强 |
| 删符号链接不跟随目标 | `168e7274` | 沙箱里删软链不会误删目标文件 |
| Write/Edit 权限建议里转义 glob 元字符 | `1a60e2d9` | 少一类「权限建议写歪导致过度放行」 |
| sed 备份后缀识别修正 | `c3249be8` | 只读判定更准，减少误判 |
| **上下文归档里的嵌套媒体也会落盘** | `34457377` | 多模态历史回放不再丢图 |
| 事件重放时保留 data block 身份 | `6642ed61` | 历史重放不会张冠李戴 |
| offload 空批次不写脏数据 | `9bdc4e8d` | 上下文归档更干净 |
| PDF 读取失败不再静默 | `58da57a5` | 排障可见 |
| provider 流关闭时 await 清理 | `2feb4439` | 长连接泄漏修复，长会话更稳 |
| 并发工具执行前保留取消 | `cbb18504` | §4.1 的取消语义 |
| skill 目录大小写冲突 / Docker 初始化失败清理 / `.mcp` 文件 BOM | `6423e502` / `d8283ff6` / `402c3664` | 工作区健壮性 |
| 无参工具调用 / schema 不注入 state / partial 函数注解 / deferred 结果 await | `3046c88d` / `f0177cf7` / `9b94a634` / `b768e08f` | 工具调用边界更可靠 |
| 出站 MCP 客户端走 SDK 工厂 | `0fd09de0` | 我们自建 httpx client 的拦截链路更规范 |

其余 60+ 条属于 TUI / console / Gemini / xAI / realtime 等无关面。

---

## 7. 待接入的新能力（代码已具备，默认关闭）

| 能力 | 状态 | 接入成本 |
|---|---|---|
| **MCP 工具进度流** | `MCPClient.stream_progress` 字段已存在，**默认 `False`** | 小：打开开关 + 前端加进度条 |
| 保留 MCP 结构化工具结果 | `7c198c5f` | 小 |
| OpenAI Chat / Responses API 二选一 | `7fded253` | 中：模型配置加枚举 |
| 预算中间件不阻断结构化输出 | `24aac5d1` | 与 P1 的 `ReplyBudgetControlMiddleware` 配套 |
| 新模型配置 / ASR 批处理 / realtime voice | `6cc84235` / `812eaecd` / `c3c1d7fa` | 大，独立特性 |

---

## 8. 测试失败全景与处置建议

### 8.1 全量跑出来的 5 个前端契约失败（已全部修好）

**这 5 条与升级无关**（`git stash` 撤掉本次改动后仍失败），根因是**前端实现早就演进过、契约没跟**：所有 busy 判定后来都并入了 `pendingPermissionLocking`，而契约还锁着旧字符串。

处置方式：按当前 `.vue` 实现更新断言，**保留全部原始约束**（busy 状态集合、先历史后快照的顺序）。

| nodeid | 原失败断言 | 处置 |
|---|---|---|
| `test_conversation_run_status_contract.py::test_both_chat_surfaces_refresh_remote_status_on_visibility_and_bind_busy_states` | `:is-processing="isProcessing \|\| remoteRunBusy \|\| recoveryLocking"` | 补 `pendingPermissionLocking`（EmbedChat / AgentDebug 各一处） |
| `test_conversation_run_status_contract.py::test_embed_chat_focuses_desktop_input_only_after_all_busy_states_clear` | focus 前的 busy 早退条件 | 补 `pendingPermissionLocking.value` |
| `test_conversation_run_status_contract.py::test_agent_debug_focuses_desktop_input_only_after_all_busy_states_clear` | 同上 | 同上 |
| `test_embed_run_recovery_contract.py::test_embed_chat_keeps_lock_truth_driving_recovery_while_composer_uses_busy` | `:is-processing` 与 `quickSendBlocked` 两处 | 补 `pendingPermissionLocking` |
| `test_conversation_switch_replay_rebind_contract.py::test_switching_conversation_restores_the_snapshot_after_history_loads` | 要求一行紧凑写法 `.then(() => restoreEmbedStreamSnapshot())` | 实现链尾补了 `catch/finally` 做骨架收敛 → 改为**顺序断言**：`fetchConversationHistory(false)` 必须出现在 `restoreEmbedStreamSnapshot()` 之前 |

结果：`pytest --confcutdir=tests/frontend tests/frontend` → **3539 passed / 0 failed**。

> 附带收益：这轮对齐顺带确认了「确认卡挂起时必须锁住输入框」这条约束在两个页面都成立，与 §4.6 的锁语义核实互相印证。

---

### 8.2 已知失败基线 62 条：按处置建议分组

先说总体结论：**用 `--strict-known-failures` 跑过一遍，没有任何一条因为升级到 2.0.11 而「自动修好」**，因此不存在「升级后可以立刻删掉的条目」。但按失败原因看，有 3 条确实属于「测试本身该重写/删除」，其余是替身、环境或断言问题。

#### ① 建议重写或删除（3 条）

| nodeid | 失败表现 | 判断 |
|---|---|---|
| `tests/ai/runtime/test_agentscope_v2_0_6_features.py::test_build_runtime_middlewares_includes_permission_and_stats` | `test_agentscope_v2_0_6_features.py:70: assert True is False` | 文件名即「2.0.6 特性快照」。我们已经经历 2.0.9 → 2.0.11 两轮升级，这类「按版本号命名的特性快照」天然腐化。**建议：重写为不绑版本号的「中间件装配契约」，或直接删除** |
| `tests/ai/runtime/test_agentscope_v2_0_6_features.py::test_load_injection_config_respects_system_flag` | `test_agentscope_v2_0_6_features.py:217: assert False` | 同上 |
| `tests/test_dev_sh_daemon_controls.py::test_dev_sh_daemon_start_persists_pid_file` | `❌ 未找到 Node.js，前端构建无法继续` | 这条**不是该删**，是缺环境：本机没装 Node.js。建议加 `pytest.mark.skipif(shutil.which("node") is None, ...)`，别留在基线里假装是断言问题 |

#### ② 修测试替身（5 条，功能仍存在，别删）

失败表现集中在 `StopIteration` / `RuntimeError: coroutine raised StopIteration` / `'coroutine' object has no attribute 'role'`：

| nodeid |
|---|
| `tests/ai/executors/test_chat_executor.py::test_agent_service_resumes_agentscope_ask_from_snapshot` |
| `tests/ai/executors/test_chat_executor.py::test_general_runner_emits_permission_required_for_agentscope_ask_tool` |
| `tests/ai/executors/test_chat_executor.py::test_general_runner_keeps_tool_preamble_as_process_narration` |
| `tests/ai/executors/test_chat_executor.py::test_permission_resume_restores_user_context_for_user_scoped_tools` |
| `tests/ai/executors/test_chat_executor.py::test_knowledge_runner_with_rag_but_no_citations_intercepts_hallucination` |

**根因**：`FakeModel` / `FakeFormatter` 是 2.0.6 时代写的，agentscope 2.x 里 `Msg.role`、formatter 等已改为属性/异步方法，替身仍返回 coroutine 或缺少属性。

其中 `test_general_runner_emits_permission_required_for_agentscope_ask_tool`（`test_chat_executor.py:1259`）的失败是断言少算了实现里新增的 `display_name: 'send_message'` 字段 —— 这条属于「改断言」。

**建议**：这 5 条测的是中断恢复、权限回填、幻觉拦截等核心逻辑，价值高。**修 `FakeModel`（async 化 + 补 formatter/role），不要删。**

#### ③ 修环境与夹具（1 条）

| nodeid | 失败表现 | 建议 |
|---|---|---|
| `tests/api/v1/test_fs_browser.py::test_list_auto_creates_user_uploads_dir` | `OSError: [Errno 66] Directory not empty: .../data/agent_workspaces/test_user__15888/uploads` | 夹具清理不彻底，改用 `tmp_path` 或加 `shutil.rmtree(ignore_errors=True)` |

#### ④ 适配框架版本（3 条，不是 agentscope 的问题）

| nodeid | 失败表现 |
|---|---|
| `tests/api/portal/test_skill_publication_routes.py::test_admin_publication_review_routes_exist` | `AttributeError: '_IncludedRouter' object has no attribute 'path'` |
| `tests/api/portal/test_skill_publication_routes.py::test_personal_publication_submit_route_exists` | 同上 |
| `tests/test_personal_skills_routes.py::test_platform_skill_list_is_readable_without_management_permission` | `StopIteration`（同类路由遍历） |

**根因**：`fastapi 0.141.1 / starlette 1.6.0` 里 `app.routes` 会混入 `_IncludedRouter` 包装对象，不再都有 `.path`。测试的「遍历 routes 找路径」写法要改（`getattr(r, "path", None)` 过滤）。

**注意**：这 3 条被登记为 `D-测试状态污染`，**分类是错的**，应改为 `A-断言过时` 或新增「框架版本适配」类。

#### ⑤ 改断言（50 条，按文件聚簇）

这些是「功能还在、输出文案/字段/次数变了」，改断言即可。

| 文件 | 条数 | 典型失败 |
|---|---|---|
| `tests/test_federated_executor.py` | 11 | `assert 'asset_owner' in ...`、`assert 2 == 1`、`assert '无权访问数据集' in ...`、`assert '[Security Error]' in ...` |
| `tests/ai/runners/test_data_agent_runner.py` | 14 | 断言漂移（13 条 A 类 + 1 条状态污染） |
| `tests/services/ai/test_router_service.py` | 3 | `'uncertain' == 'keep'`、`'agent-general' == 'agent-chatbi'` |
| `tests/ai/runners/test_auto_upgrade_federated.py` | 2 | 断言漂移 |
| `tests/ai/tools/test_data_api.py` | 3 | `assert ...`（含 1 条状态污染） |
| `tests/api/v1/test_chatbi_execute.py` | 2 | `TypeError: 'coroutine' object is not iterable`（mock 未 await） |
| `tests/ai/test_federated_subquery_gates.py` | 1 | 断言漂移 |
| `tests/ai/test_p1_optimizations.py` | 1 | 知识轮强制检索断言 |
| `tests/ai/test_request_semantics.py` | 1 | 语义/证据元数据字段 |
| `tests/ai/test_user_question_cancellation.py` | 1 | 取消前置断言 |
| `tests/ai/test_where_condition_sample_diagnostic.py` | 1 | 探针 schema 上下文 |
| `tests/ai/tools/test_session_status.py` | 1 | 上下文用量估算口径 |
| `tests/api/portal/test_scenario_templates.py` | 1 | 场景模板预检 |
| `tests/api/v1/test_chat_context_usage.py` | 1 | 上下文用量端点 |
| `tests/api/portal/test_logs_partitions.py` | 1 | 分区清理断言漂移 |
| `tests/services/test_quota_service.py` | 1 | `assert 1 == 500000`（配额批量解析逻辑变了） |
| `tests/services/mcp/test_platform_config.py` | 1 | 字段元组过时：实际已多出 `rate_limit_client_per_minute` / `rate_limit_user_per_minute` |
| `tests/test_dev_sh_python_bootstrap.py` | 1 | `assert 'DATABASE_TYPE: postgresql' in ...`（dev.sh 输出格式变了） |
| `tests/test_memory_ebbinghaus_consolidation.py` | 1 | 聚类合并断言 |
| `tests/test_memory_session_summary.py` | 1 | `Expected search_for_user to have been awaited once. Awaited 2 times.` |
| `tests/utils/test_sql_rewriter.py` | 1 | `assert "o.dept_code = 'D001'" in ...`（SQL 重写加限定名） |


另有 4 条登记为 `D-测试状态污染`（`test_data_agent_runner.py::test_sql_preflight_allows_table_in_user_permission_set`、`test_data_api.py::test_call_external_sql_api_error`、`test_skill_publication_routes.py` ×2）。前 2 条单独跑通过、合跑失败，符合「状态污染」描述；后 2 条实际是上面 ④ 的框架适配问题。

---

### 8.3 处理顺序（已执行）

1. ~~先修 3 条框架适配~~ → **已完成**（§8.4-A），且确认原分类「D-测试状态污染」是错的，实际是 starlette 1.6 的 `_IncludedRouter` 结构变化
2. ~~再修 5 条测试替身~~ → **已完成**（§8.4-B），除 `FakeModel` 外还补了权限/额度/知识库访问三处 DB 桩
3. ~~重写或删除 `test_agentscope_v2_0_6_features.py`~~ → **已完成**（§8.4-C），改成不绑版本号的中间件装配契约
4. 断言漂移批量处理 → **部分完成**：data_agent_runner 修掉 8 条，剩余 26 条待处理（§8.5-③）
5. 5 个前端契约 → **未开始**（§8.1 已有证据证明是存量问题，与本升级无关）
6. ~~给 `test_dev_sh_daemon_controls` 加 skipif~~ → **改为更好的方案**：在测试内自造假 `node`/`npm`，不依赖宿主机，也不需要 skip 掩盖

**执行中发现的新问题（升级报告之外，值得单独跟进）**：

- **联邦多源执行链路**：多数据集场景下第二个子查询不执行、无权限数据集静默跳过（§8.5-①）。这是本次盘点里唯一的「疑似功能回退」，且早于本次升级存在，建议单开 issue。
- **`known_failures.txt` 有 4 条 nodeid 与实际测试名不符**（`test_chat_executor.py` 里 4 条曾在 pytest 配置阶段告警「找不到对应用例」）。本轮已随对应条目一并清理；后续新增条目建议直接复制 pytest 输出的 nodeid，避免手写。

维护规则提醒（文件里自己写的）：**清单只增不减 = 治理失败**。

---

### 8.4 本轮已处置的 20 条（62 → 42）

处置原则：**只改测试、不改产品行为**。凡是「产品行为确实变了」的，一律把断言对齐到当前实现并注释原因；凡是「行为疑似回退」的，不动断言、留在基线并单独列出（见 §8.5）。

#### A. 框架/环境适配（4 条，已修好）

| nodeid | 根因 | 处置 |
|---|---|---|
| `tests/api/portal/test_skill_publication_routes.py::test_admin_publication_review_routes_exist` | starlette 1.6 的 `include_router()` 生成 `_IncludedRouter` 包装对象，**没有** `.path`/`.prefix` | 新增 `_route_paths()`：递归 `original_router.routes` 并拼接 `include_context.prefix` |
| `tests/api/portal/test_skill_publication_routes.py::test_personal_publication_submit_route_exists` | 同上 | 同上 |
| `tests/test_personal_skills_routes.py::test_platform_skill_list_is_readable_without_management_permission` | 同上（`next(route for route in app.routes ...)` 找不到） | 新增 `_find_route(app, path, method)` 递归查找 |
| `tests/test_dev_sh_daemon_controls.py::test_dev_sh_daemon_start_persists_pid_file` | 测试只伪造了 `uv`/`npm`，`dev.sh` 的 `check_node_environment` 找不到 `node` | 在 `_install_start_commands()` 里补假 `node`（`v22.12.0`）与带版本号的假 `npm`（`10.9.0`），自给自足、不依赖宿主机 |

#### B. 测试替身与权限桩（5 条，已修好）

| nodeid | 根因 | 处置 |
|---|---|---|
| `test_chat_executor.py::test_general_runner_keeps_tool_preamble_as_process_narration` | 未注册工具被 `ToolGuard` 拦成「⚠️ 工具调用已拦截」，不再是「调用工具: xxx」 | 工具日志判据同时接受两种标题 |
| `test_chat_executor.py::test_general_runner_emits_permission_required_for_agentscope_ask_tool` | `tool_call` 多了展示字段 `display_name` | 断言改为只校验 `id`/`name`/`args` 三元组 |
| `test_chat_executor.py::test_agent_service_resumes_agentscope_ask_from_snapshot` | ①`PermissionService.get_user_permissions` 查库失败 → fail-closed DENY ②会话授权查真实 Redis | 新增 `_permission_policy_stubs()`，用真实 `UserPermissionsResponse` 构造「无权限」响应 + `is_granted=False` |
| `test_chat_executor.py::test_permission_resume_restores_user_context_for_user_scoped_tools` | 同上，另加额度校验/知识库访问两处真实 DB 依赖 | 同上 + `check_before_call`、`get_knowledge_base_access` 打桩 |
| `test_data_agent_runner.py::test_data_agent_runner_context_action_can_answer_without_sql` | 12 个 `FakeModel(ChatModelBase)` 缺 `formatter`（AgentScope 2.0.9 起必需） | 批量补 `formatter = OpenAIChatFormatter()`，该条因此自动愈合 |

#### C. 断言对齐当前实现（11 条，已修好）

| nodeid | 差异 | 处置 |
|---|---|---|
| `test_agentscope_v2_0_6_features.py::test_load_injection_config_respects_system_flag` | 实现改走批量 `ConfigService.get_many`，测试只桩了 `get` | 改桩 `get_many` |
| `test_agentscope_v2_0_6_features.py::test_build_runtime_middlewares_includes_permission_and_stats` | 装配列表新增 `BashSandboxParentLinkMiddleware` 等，下标断言失效 | 重写为**版本无关的装配契约**：权限中间件必须在最外层、统计中间件按会话挂载 |
| `test_data_agent_runner.py::test_schema_keyword_planner_derives_keywords_from_intent_when_llm_keywords_invalid` | schema 检索关键词不再拼 `filter.phrase`（「上海区域」不是列名） | 断言对齐 |
| `test_data_agent_runner.py::test_data_agent_runner_tool_loop_fuse_triggers_on_global_limit` | 全局熔断阈值由 30 提到 `DEFAULT_GLOBAL_LIMIT=50` | 改为按常量循环，阈值再变也不会假性失败 |
| `test_data_agent_runner.py::test_sql_preflight_allows_unqualified_columns_for_single_table_to_avoid_dialect_false_positives` | **用例自己写错**：用不存在的列 `SSFB` 测「未限定列应放行」 | 改用真实存在的列 `BELONGTO` |
| `test_data_agent_runner.py::test_data_agent_runner_marks_no_authorized_schema_and_blocks_sql_before_schema` | 无授权是致命 schema 失败，流程立即终止，不再标记 `sql_before_schema` | 断言对齐 |
| `test_data_agent_runner.py::test_data_agent_runner_execute_repairs_sql_error_before_final_answer` | 日志标题变为「修正 SQL 执行错误」 | 语义匹配「修正 + SQL」 |
| `test_data_agent_runner.py::test_data_agent_runner_execute_rechecks_empty_sql_before_final_answer` | 日志标题变为「空结果筛选复核」 | 语义匹配「空结果 / 复核」 |
| `test_data_agent_runner.py::test_sql_preflight_blocks_unknown_alias_column_before_execution` | 预检把别名解析成真实表名（`"HRMRESOURCE"."SSFB"` 取代 `"R"."SSFB"`） | 断言只锁「该列 + identifier 无效」语义 |
| `test_data_agent_runner.py::test_data_agent_runner_execute_streams_agentscope_native_text` | data agent 工具集不含 `get_current_time`（时间由 InjectionConfig 注入） | 断言改为反向锁定 |
| `test_data_agent_runner.py::test_data_agent_runner_reports_missing_reusable_result` | **原判定有误**：不是死分支（`chatbi/turn_handlers.py` 里有两处真实调用），而是测试桩打偏——`_load_last_data_result_with_retry()` 读会话缓存，残留了其他用例写入的 `last_data_result`，于是走「有可复用结果」分支 | 直接桩 `runner._load_last_data_result_with_retry → None`，用例转为通过；该分支（`REUSE_PREVIOUS_RESULT` 无上次结果 → 澄清）因此重新有了回归保护 |

#### D. 删除用例（1 条）

`test_chat_executor.py::test_knowledge_runner_with_rag_but_no_citations_intercepts_hallucination`

- **为什么删**：它断言的是「命中幻觉 → 用固定文案拒答（`⚠️ 抱歉，在系统知识库中未检索到相关内容`）」，这条产品行为**已被有意替换**为「保留回答 + 追加事实来源风险提示（`category=grounding`）」。两个文案在当前代码里都已不存在。
- **覆盖没丢**：同语义已在 `tests/ai/runners/test_knowledge_hallucination_guard.py` 覆盖，且更完整——`test_hallucination_reflection_loop_corrects`、`test_hallucination_max_retries_retains_answer_with_soft_warning`、`test_hallucination_guard_is_disabled_by_default`、`test_stream_error_bypasses_knowledge_grounding_reflection` 四条都在跑。
- **它原本为什么一直失败**：用例没传 `execution_options={"grounding_enabled": True, "hallucination_check": True}`，审计默认关闭，走不到幻觉循环——即它连自己的前置条件都没满足。

---

### 8.5 剩余 41 条：分三类，其中 3 条建议人工过目

#### ① 联邦多源执行（11 条）——**疑似功能问题，不建议改断言**

`tests/test_federated_executor.py` 的 11 条呈现同一个信号，已用运行时探针确认（探针未保留）：

| 现象 | 证据 |
|---|---|
| 多数据集计划里 `sub_queries=2`，但**只有第一个子查询进入执行分支** | 探针只在 `user_ds` 打印，`hr_ds` 从不进入 |
| 「无权限数据集」用例里第二个数据集被**静默跳过**，没有任何拒绝提示 | `combined` 里既无「无权访问数据集」也无「已自动降级」 |
| 执行次数断言全线漂移 | `sql_exec_mock.await_count` 期望 4 实际 0；`generate_text.await_count` 期望 1/2 实际 2/3 |

- **安全面**：无权限的数据集**没有被查询**（探针证实未进入执行），所以不是越权读取；但「静默跳过」会让用户以为拿到的是完整联邦结果，属于**正确性/可观测性**问题。
- **为什么不改断言**：把「应拒绝」改成「跳过也行」等于用测试掩盖行为变化。建议先定位是什么门控（`validate_federated_subquery_before_execute` 返回 block 是最大嫌疑）挡住了第二个子查询，再决定是修实现还是改契约。
- 这 11 条**在 2.0.9 时代就已经在基线里**，不是本次升级引入。

#### ② data_agent_runner（4 条）——语义判断，建议逐条确认

| nodeid | 差异 | 建议 |
|---|---|---|
| `test_data_agent_runner_does_not_issue_derived_evidence_for_history_only_reuse` | 历史消息里的表格被计为本轮 `INTERNAL_DATA` 有效证据 | **证据台账语义**，涉及「无工具调用也能出数」的防幻觉边界，需产品确认 |
| `test_empty_diagnostic_sql_does_not_mark_business_sql_complete` | 空诊断 SQL 后 `diagnostic_sql_pending_final=True`（测试期望 False） | 需确认「诊断 SQL 空结果」是否应触发最终回答 |
| `test_data_agent_runner_synthesizes_after_repeated_sql_gate` | 重复 SQL 复用后仍跑到 `max_steps`，答案被截断 | 需确认 max_steps 与「复用首次结果」的优先级 |
| `test_sql_preflight_allows_table_in_user_permission_set` | `RuntimeError: got Future attached to a different loop` | 跨事件循环隐患（`asyncio.wait_for` 外层复用 loop 资源），需单独排查 |

#### ③ 其余 26 条（单点断言漂移）

分布在 `test_router_service.py`(3)、`test_data_api.py`(3)、`test_chatbi_execute.py`(2)、`test_auto_upgrade_federated.py`(2) 及 16 个文件各 1 条。处理方式与 §8.4-C 相同（对齐文案/字段/计数），**尚未处理**。

---


---

## 9. 必须人工验收的 3 项

| # | 项目 | 怎么验 | 通过标准 |
|---|---|---|---|
| 1 | **沙箱镜像重建** | 触发一次预构建，然后跑 `exec_command`、装一次技能、调一次 `sandbox` 工具 | 容器 gateway 能起、内联 MCP 工具可用 |
| 2 | **停止按钮** | 在界面发起一个长任务，点停止 | 草稿落库、确认卡回收、子代理状态正确、无残留 run |
| 3 | **外部 MCP 服务** | 接一个 SSE + 一个 Streamable HTTP 服务 | 工具列表同步、调用成功、超 4 MiB 的返回有明确报错。⚠️ SSE 这条在复审前**必然失败**（`httpx` vs `httpx2`，见 §12.1），修复后重测 |

---

## 10. 回滚

```text
1. requirements.txt：agentscope[service,storage,workspace]==2.0.9、mcp==1.29.1
2. revert 本次 9 个源文件改动（git diff 已备份为一份 patch）
3. 重建后端镜像
4. 沙箱镜像 tag 会再次变化，需重新预构建
```

无数据库迁移，无数据兼容负担。

---

## 11. 附：本次验证用到的关键命令

```bash
# 全量（含已知失败基线机制）
.venv/bin/python -m pytest tests -q

# 前端契约（必须带 confcutdir）
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q

# 检查基线里哪些已修好（该删的清单）
.venv/bin/python -m pytest --strict-known-failures $(grep -vE '^\s*#|^\s*$' tests/known_failures.txt | awk '{print $1}')

# 看基线内用例的真实失败（不 xfail）
.venv/bin/python -m pytest --run-known-failures -q <nodeid...>

# 运行时导入自检
.venv/bin/python -c "from app.services.ai.runtime.agentscope.imports import verify_agentscope_imports as v; print(v())"
```

---

## 12. 复审补记（2026-10-29）：两处必须修的缺陷（已修复）

> 上文 §1～§11 是升级实施当时的记录。提交前又做了一轮逐文件复审 + 真实运行验证，发现
> **两处当时漏掉、且现有测试覆盖不到**的问题。两处都已修复并补了会失败的回归用例
> （先写测试确认红，再改实现转绿）。

### 12.1 🔴 出站 SSE MCP 全线失效（`httpx` → `httpx2` 类型不匹配）

| 项 | 内容 |
|---|---|
| 症状 | 所有走 SSE 的外部 MCP 服务：工具同步失败、调用失败；日志里只有一行 `[MCP] Standard SSE unavailable for <id> (TypeError); falling back to Direct HTTP` |
| 根因 | mcp 2.x 把客户端传输整体切到 `httpx2`。`sse_client(httpx_client_factory=...)` 会把 `httpx2.Timeout` 传进工厂，而平台工厂仍返回 `httpx.AsyncClient`，httpx 在 TCP 连接阶段把 Timeout 当数字运算 → `TypeError: unsupported operand type(s) for +: 'float' and 'Timeout'` |
| 为什么升级时没发现 | 评估只核对了**符号存在性**（`sse_client` 在 2.x 仍在原路径、签名不变），没核对**回调参数类型**；异常又被 `McpSseSession.connect()` 的 `except Exception` 吞成 warning 后静默降级，任何测试都不会红 |
| 复现 | `tests/services/mcp/test_mcp_client_sse_transport.py`（先红后绿）：起一个最小 SSE MCP 服务，用平台真实 `McpSseSession.connect()`；修复前 `is_direct_http=True`，修复后 `False` 且 `tools/list` 正常 |
| 修复 | `mcp_client._create_no_redirect_mcp_http_client()` 返回 `httpx2.AsyncClient`（`follow_redirects=False` 显式保留：httpx2 默认即不跟随，mcp 2.x 传输层也只跟随同源）；`requirements.txt` 显式锁 `httpx2==2.12.0`（平台代码直接 import） |
| 影响面 | 只影响出站（外部 MCP 服务）；平台自身的 MCP 服务端、Echo、内联 stdio 沙箱工具不受影响 |

### 12.2 🔴 K8s 预置沙箱镜像依赖清单未跟随 2.0.11

| 项 | 内容 |
|---|---|
| 症状 | 按 `k8s_deploy/build-k8s-sandbox-image.sh` 重建预置镜像，构建期 smoke 自检 `import agentscope.mcp` 直接 `ModuleNotFoundError: No module named 'httpx2'`；若只补 httpx2 而保留 `mcp<2.0.0`，则 agentscope 2.0.11 的 `from mcp import Client` 导入即失败 |
| 根因 | agentscope 用 `--no-deps` 安装，网关 venv 的 `mcp` / `httpx2` / `certifi` 只能由脚本的 `BASE_REQS` 提供。该清单是**手工照抄**上游 `_GATEWAY_BASE_REQUIREMENTS` 的，2.0.11 把 `mcp<2.0.0` 放开成 `>=2.0.0` 并新增 `httpx2`、`certifi` 下限，脚本没跟上；且清单在脚本里存了两份（`BASE_REQS` 常量 + heredoc 字面量），`BASE_REQS` 根本没被使用 |
| 修复 | `BASE_REQS` 改为逐项对齐上游（`certifi>=2026.10.19` / `mcp>=2.0.0` / `uvicorn` / `fastapi` / `httpx` / `httpx2>=2.5.0`）+ 平台 `GATEWAY_EXTRA_PIP`；Dockerfile 安装行改为 `${BASE_REQS[*]}` **单源展开**，杜绝再次漂移 |
| 测试 | `tests/test_k8s_sandbox_image_gateway_deps.py`：与上游 `_GATEWAY_BASE_REQUIREMENTS` + `GATEWAY_EXTRA_PIP` 逐项比对、断言 mcp 约束允许 2.x、断言安装行单源展开、`--dry-run` 产物含 `mcp>=2.0.0`/`httpx2`；原 `test_k8s_deploy_docs_contract.py` 里 `assert "mcp<2.0.0" in script_source` 同步改成 `mcp>=2.0.0` |
| 后续动作 | **K8s 预置镜像必须重建一次**（Docker 策略与 K8s 冷启动 bootstrap 走的是上游常量，不受影响） |

### 12.3 复审确认无问题（不需要改动）

- **平台 MCP 服务端 / Echo**：`MCPServer` 迁移、`streamable_http_app()` 参数与路径、legacy `2025-06-18` 协议客户端、OAuth Bearer 全链路实测可用；
- **`platform_mcp_http_app()` 单例缓存**：解决了 Playground「二次请求顶掉未 run 的 session manager」，且 mcp 2.3.0 的 `TransportSecurityMiddleware` 每次请求读同一个 `settings` 对象 → 公网地址热刷新仍然生效；
- **结构化工具结果**：agentscope 2.0.11 新增「保留 `structured_content`」，但只在**没有 TextBlock** 时才追加，平台内联 stdio 工具都返回文本块，结果形态不变；
- **`tools/list` 分页**：`MCPServer.list_tools()` 仍一次性返回全部工具，不受 2.x 影响（平台侧 `MAX_REMOTE_TOOL_PAGES` 防护保持原样）；
- **沙箱内联 stdio MCP 脚本**：用 mcp 2.3.0 真实客户端跑通 `initialize` + `tools/list` + `tools/call`（`bash` 返回文本 + `structured_content`）；
- **Docker 模板补丁**：用 2.0.11 真实模板渲染，uv 安装段正确替换为 `pip install --index-url ... uv`，无悬空续行。

### 12.4 已知但本轮不修（记录在案）

- **`_looks_like_sse_endpoint()` 的探测代价**：它对 SSE 端点会做一次非流式 GET，必然等到 5s 读超时才返回「仍然尝试 SSE」，因此**每个 SSE 服务建连固定多 5s**（升级前就如此，与本次升级无关）。若后续要优化，改成 `client.stream()` + 只看响应头即可；
- **`validate_token_resource=True` 是新增强制**：修改 `APP_PUBLIC_URL` 之后，修改前签发的 Access Token 会立刻 401（需要重新授权）。安全方向正确，已在验收清单标注；
- **K8s 镜像里的 `_mcp_gateway_app.py` 模板副本偏旧**：缺上游 2.0.11 网关新增的 `runtime_headers` / `PUT /mcps/{name}/runtime-headers`，平台不使用该能力，暂不跟进。


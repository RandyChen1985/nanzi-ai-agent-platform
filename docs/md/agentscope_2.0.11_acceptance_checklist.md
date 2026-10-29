# AgentScope 2.0.11 升级验收清单

> 适用版本：`agentscope 2.0.9 → 2.0.11`，`mcp 1.29.1 → 2.3.0`
> 分支：`dev-agentscope-mcp2.x`
> 配套文档：[升级报告](./agentscope_2.0.11_upgrade_report.md) · [升级评估](./agentscope_2.0.11_upgrade_assessment.md)
> 用法：从「第 0 步」往下逐项勾选——本文件就是普通 Markdown，编辑器里把 `- [ ]` 改成 `- [x]` 即可（VSCode / Typora / GitHub 页面都能直接点）。**P0 三项只需 10 分钟，覆盖本次升级 90% 风险面**，时间紧就先只跑 P0。

---

## 风险地图：为什么测这些

| 升级面 | 改了什么 | 风险 |
|---|---|---|
| **mcp 1.x → 2.x** | `FastMCP` 改名 `MCPServer`；端点路径/JSON 响应/无状态会话/传输安全从构造函数挪到 `streamable_http_app()`；工具 schema 字段改 `input_schema` | 🔴 最高。入口路径、鉴权、生命周期全在这块 |
| **AgentScope 2.0.11** | 并行工具取消改为 `raise CancelledError`；危险路径先 `realpath`；DataBlock 引用 `file://` → `workspace:///` | 🟠 中。中断语义、沙箱媒体引用 |
| **Dockerfile 模板** | 多 `update-ca-certificates`、uv 安装方式变 | 🟠 中。镜像 tag 变化，不重建沙箱全挂 |
| **升级前的隐蔽 bug** | `MCPServer.settings` 不再持有 `TransportSecuritySettings`，旧的 `getattr` 写法静默返回 `None` | 🟡 低但阴。对外地址改了不生效 |
| **本轮修的平台隐患** | `platform_mcp_http_app()` 每次调用都新建 session manager，导致在线调试必失败 | 🟢 已修，需回归确认 |
| **复审补修（决定「能不能用」）** | ① 出站 SSE 的 `httpx_client_factory` 仍返回 `httpx.AsyncClient`，mcp 2.x 下必然 `TypeError` 并**静默降级 Direct HTTP**；② K8s 预置镜像脚本仍锁 `mcp<2.0.0` 且缺 `httpx2`/`certifi`，按它重建镜像会构建失败 | 🟢 已修（报告 §12）；必须重测 P1 的 SSE 项 + 重建 K8s 预置镜像 |

---

## 第 0 步：前置检查（5 分钟）

镜像 tag 变了，不重建的话所有沙箱功能都会假装“坏了”。

> ✅ **已通过**（2026-10-29）：沙箱镜像、K8s 预置镜像、后端重启、对外地址四项均确认无误。

- [x] **确认沙箱镜像已重建**
  - 入口：系统配置 → Docker 沙箱 → 查询预构建状态
  - 通过标准：显示 `prebuilt` 或 `reused`
  - 若显示未构建 → 点「预构建」，首次分钟级，等完成再继续
- [x] **确认 K8s 沙箱预置镜像已重建**（仅使用 K8s 沙箱策略时需要）
  - 命令：`cd k8s_deploy && ./build-k8s-sandbox-image.sh --version <新版本>`
  - 为什么：本次网关依赖清单从 `mcp<2.0.0` 改成 `mcp>=2.0.0` + `httpx2`/`certifi`，用旧脚本重建会在
    `import agentscope.mcp` 处直接失败（报告 §12.2）；旧镜像里的网关 venv 也与新 agentscope 不匹配
  - 通过标准：构建日志末尾的 `import docstring_parser; import agentscope.mcp; import agentscope.tool` 自检通过
- [x] **确认后端已重启**（加载了新依赖与新模板）
- [x] **确认对外访问地址（APP_PUBLIC_URL）正确**：系统配置 → 对外访问地址

> 如果这一步没过，P3 的沙箱相关项**不用测**，测了也必然失败，会浪费判断力。

---

## P0：必测三项（10 分钟，覆盖 90% 风险）

### P0-1 Platform MCP 在线调试 🔴

这是本轮刚修的 bug（升级前该功能**完全不可用**，必然报 `Task group is not initialized`）。

> ✅ **已通过**（2026-10-29）：探针返回 `status: success`，无 `Task group is not initialized`。

- [x] 生成 Token：MCP 服务台 → 「外部 Client」页签 → 生成 MCP Access Token → 复制
- [x] 发起调试：切到「能力与 Scope」→ 选任一方法（建议 `metadata_list_datasets`）→ 点调试 → 粘贴 Token → 点「发送探针请求」
- [x] **通过标准**
  - 返回 `status: success`，含 JSON-RPC `result`
  - 界面有延迟毫秒数
  - ❌ 不得出现 `Task group is not initialized`

### P0-2 Platform MCP 外部客户端直连 🔴

最容易踩的坑：**mount 前缀 + 内部 path 两段拼起来才是真实 URL**。

> ✅ **已通过**（2026-10-29）：Platform MCP 的 `initialize` / `tools/list` / `tools/call` / 错误 Token 401 全部符合预期。

| 服务 | 真实 endpoint |
|---|---|
| Platform MCP | `{你的域名}/mcp/platform` |
| Echo MCP | `{你的域名}/mcp/echo/mcp` |

- [x] `initialize` 成功

```bash
curl -sS -X POST "https://<你的域名>/mcp/platform" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual","version":"1.0"}}}'
```

- [x] `tools/list` 返回方法清单（受能力组开关限制，关掉的能力组不应出现）
- [x] `tools/call` 调一个真实方法成功（如 `metadata_list_datasets`）
- [x] 用**错误的 Token** 请求 → 401，不是 200
- [x] Echo MCP `{域名}/mcp/echo/mcp` 的 `echo` 工具可用

### P0-3 停止按钮 🔴

**本次唯一有真实行为变化的地方**：上游把「并行工具取消」从“吞掉后正常返回”改成 `raise CancelledError`。

> ✅ **已通过**（2026-10-29）：多工具并行任务中途停止，interrupted 状态、草稿落库、确认卡回收、子代理状态、无残留 run 五项均符合预期。

- [x] 用一个**会同时调 2 个以上工具**的问题发起长任务（单工具取消走不到新路径）
- [x] 流式输出中途点「停止」
- [x] **通过标准（5 条都要满足）**
  - [x] 前端显示 interrupted，不是 500 / 空白 / 卡住
  - [x] 草稿正常落库
  - [x] 权限确认卡被回收
  - [x] 子代理状态正确
  - [x] 后端无残留 run（停止后不再有该会话的写库/日志）

---

## P1：MCP 专项（15 分钟）

> ✅ **已通过**（2026-10-29）：对外地址热同步、SSE 与 Streamable HTTP 接入、超大返回报错、能力组开关、审计日志六项均符合预期。
> 其中 SSE 接入是报告 §12.1 的修复点，本次实测已能正常同步工具列表并调用（未再出现 `falling back to Direct HTTP`）。

- [x] **对外地址热同步**（升级前静默失效点，建议专门验一次）
  - 系统配置 → 改「对外访问地址」→ 保存 → 重启后端
  - 通过标准：MCP 的 Host/Origin 白名单跟着新地址走 —— 用旧 Host 请求应被 **400 / 421 拒绝**，用新 Host 正常
- [x] **外部 MCP 服务接入（SSE）**：（本轮修复项，报告 §12.1）MCP 工具集 → 新增 → 工具列表能同步、能调用
  - 排查：若日志出现 `[MCP] Standard SSE unavailable ... (TypeError); falling back to Direct HTTP`，
    说明 `httpx_client_factory` 又回到了 `httpx.AsyncClient`，SSE-only 服务必然不可用
  - 另注：SSE 服务的建连会比 Streamable HTTP 多约 5s（`_looks_like_sse_endpoint()` 的探测读超时，升级前既有行为）
- [x] **外部 MCP 服务接入（Streamable HTTP）**：同上
- [x] **超大返回**：找一个返回 > 4 MiB 的接口 → 应有**明确报错**，不是静默截断
- [x] **能力组开关**：MCP 服务台关掉某个能力组 → `tools/list` 里该组方法消失、直接调用被拒
- [x] **审计日志**：上面几次调用在「审计日志」页签里都有记录（方法名、状态码、耗时）

---

## P2：对话核心（20 分钟）

> ✅ **已通过**（2026-10-29）：流式输出、工具调用、权限确认卡（弹出/批准/拒绝）、会话切换与刷新快照恢复、中断后继续、长会话 token 统计六项均符合预期。

- [x] **普通对话流式**：文字逐字输出、无重复段落、无截断
- [x] **工具调用**：工具日志正常展开，参数与结果完整
- [x] **权限确认卡（ask）**
  - [x] 需确认的工具能弹出确认卡
  - [x] 批准 → 工具执行
  - [x] 拒绝 → 工具不执行，且给出提示
- [x] **会话切换 / 刷新恢复**
  - [x] 对话进行中切到别的会话再切回 → 内容完整
  - [x] 对话进行中刷新页面 → 快照恢复，busy 态正确（不会假装还在跑，也不会丢正在跑的）
- [x] **中断后继续**：停止后再发一条消息，上下文衔接正常
- [x] **长会话**：连续 10 轮以上，确认 token 统计没有异常累加（上游修了 `append_usage()` 深拷贝）

---

## P3：业务链路（30 分钟）

- [ ] **ChatBI 单数据集查询**：生成 SQL → 执行 → 表格/图表正常
- [ ] **ChatBI 多数据集联邦**：问一个跨两个数据集的问题
  - [ ] 子查询逐个执行（每个数据集都有执行日志）
  - [ ] 内存 join 出结果
  - [ ] 若某数据集无权限，应有**明确拒绝提示**（不是静默跳过）
- [ ] **SQL 报错修复**：故意问一个会报错的查询 → 能看到「修正 SQL」类日志
- [ ] **知识库问答**：绑定知识库的智能体能检索、能引用来源
  > 注意：幻觉拦截（grounding）默认关闭，**不需要**测「无引用时拒答」
- [ ] **沙箱工作区**（依赖第 0 步）
  - [ ] `exec_command` 能执行命令
  - [ ] 装一次技能成功
  - [ ] 调一次 sandbox 工具成功
  - [ ] 沙箱内读一个文件并回传（验证 DataBlock 的 `workspace:///` 引用）
- [ ] **智能体调试页**：完整跑一轮，事件流不缺段
- [ ] **嵌入式对话**（若在用）：widget 页面能正常对话、状态同步

---

## P4：低成本抽查（每项 1 分钟）

- [ ] 技能工作台：装一次技能、画廊能打开
- [ ] 记忆工作台：能看到一轮记忆记录
- [ ] 任务调度台：跑一个定时任务，状态更新正常
- [ ] 系统配置：随便改一项再改回来，能保存
- [ ] 用户/角色：登录、权限菜单显示正常

---

## 出问题时先看这四条日志

| 日志关键字 | 含义 | 处理方向 |
|---|---|---|
| `Task group is not initialized` | MCP app 单例缓存失效 | 检查 `platform_mcp_http_app()` 的 `_PLATFORM_HTTP_APP` 缓存是否生效（报告 §4.5） |
| `Standard SSE unavailable` | 出站 SSE 传输建连失败并降级 Direct HTTP | `httpx_client_factory` 必须是 `httpx2.AsyncClient`（报告 §12.1） |
| `未匹配到 uv 安装段` | Dockerfile 模板补丁没打上 | 镜像里 uv 还是旧装法 → 重建镜像；查 `docker_template_patch.py` 的 anchor |
| `[cross_dataset_enrich]` | 联邦查询的跨数据集 schema 补全 | 看是否命中关联配置、权限过滤是否过严 |

---

## 已知非问题（不要误判）

| 现象 | 说明 |
|---|---|
| 本地 `pytest tests` 有 5 个前端契约失败 | 升级前就存在（`git stash` 验证过），与部署无关 |
| 本地 `pytest tests` 有 41 条 xfailed | 已知失败基线，详见报告 §8.5 |
| 后端镜像比升级前大 | 上游 `service` extra 引入 8 个 WebRTC 相关依赖，见报告 §4.3 |
| 沙箱内 `file://` 引用变成 `workspace:///` | 上游改动，平台侧已同步，见报告 §4.1 |

---

## 验收结论

| 项目 | 结果 | 备注 |
|---|---|---|
| 第 0 步 前置检查 | ☑ 通过 ☐ 不通过 | 2026-10-29 |
| P0 必测三项 | ☑ 通过 ☐ 不通过 | 2026-10-29，三项全通过 |
| P1 MCP 专项 | ☑ 通过 ☐ 不通过 ☐ 未测 | 2026-10-29 |
| P2 对话核心 | ☑ 通过 ☐ 不通过 ☐ 未测 | 2026-10-29 |
| P3 业务链路 | ☐ 通过 ☐ 不通过 ☐ 未测 | |
| P4 抽查 | ☐ 通过 ☐ 不通过 ☐ 未测 | |

**执行人**：____________  **日期**：____________  **环境**：____________

**遗留问题**：

1.
2.
3.

---

## 附：常见失败对照表

| 现象 | 最可能原因 | 去哪查 |
|---|---|---|
| 在线调试报 `Task group is not initialized` | MCP app 未单例化 | 报告 §4.5 |
| `{域名}/mcp` 直接 404 | 漏了内部 path，应为 `/mcp/platform` | 本文 P0-2 |
| `{域名}/mcp/echo` 直接 404 | 漏了内部 path，应为 `/mcp/echo/mcp` | 本文 P0-2 |
| `tools/call` 返回 401 | Token 的 resource 与 `APP_PUBLIC_URL` 不匹配 | 系统配置 → 对外访问地址 |
| 改了对外地址但白名单没变 | 热同步没接上（升级前的隐蔽 bug） | 报告 §4.2 |
| 沙箱 `exec_command` 报错/超时 | 镜像未按新模板重建 | 本文第 0 步 |
| 停止后仍有输出 / 前端卡住 | CancelledError 归一化异常 | 报告 §4.1、§9-2 |
| 联邦查询只出了单个数据集 | 子查询执行链中断 | 报告 §8.5-① |

# AgentScope 2.0.9 → 2.0.11 升级评估与实施计划

> 评估日期：2026-10-28
> 评估基线：nanzi 锁定 `agentscope==2.0.9` ↔ 上游 `v2.0.11`（另抽查 `v2.0.11` 之后 10 个提交，HEAD=`e763b351`）
> 上游源码：`/Users/chenxiaolong/workspace/agentscope`（本地 clone，含全部 tag）
> 结论先行：**agentscope 这一层几乎零改动，但 2.0.11 把 `mcp` 从 `<2.0.0` 放开成 `>=2.0.0`，升级必然连带 MCP SDK 大版本迁移。建议拆成两步：先升 2.0.10（低风险、可单周完成），MCP 2.x 迁移单独排期，迁移完成后再升 2.0.11。**
>
> ✅ **已落地（2026-10-29）**：实际按「一步到位」执行，评估中的路线 A 未采用。
> 验收报告（改动清单、影响面、全量测试结果、62 条测试债务处置建议）见
> [agentscope_2.0.11_upgrade_report.md](./agentscope_2.0.11_upgrade_report.md)；
> 本文档 §15 保留实施记录，本文其余部分为升级前的评估结论。

---

## 1. 结论摘要

| 维度 | 结论 |
|---|---|
| 公开 API 兼容性 | ✅ 新增 15 条导出、**仅删除 1 条**（`agentscope.realtime.TruncationSupport`，平台未使用） |
| 平台引用符号 | ✅ 平台引用的 18 个模块、**61 个符号在 2.0.11 全部存在** |
| 私有 API 依赖 | ✅ 8 处私有依赖（`_make_dockerfile`、`_builtin._backend`、`_k8s._constants`、`_docker_backend` 等）全部存活且签名不变 |
| 平台行为契约 | 🟡 4 处行为变化需要复核：并行工具取消语义、DataBlock 落盘 `file://`→`workspace://`、路径判定改 `realpath`、MCP 工具字段名 `inputSchema`→`input_schema` |
| 强制联动 | ❌ **2.0.11 要求 `mcp>=2.0.0`**，与平台 `mcp==1.29.1` 直接冲突 |
| 新增依赖 | `httpx2>=2.5.0`、`certifi>=2026.10.19`；`service` extra 新增 `aiortc>=1.14` |
| 运维影响 | 🟡 沙箱镜像 tag **全量失效**（Dockerfile 模板变更：uv 版本化安装 + CA 更新），需重建 |
| 升级收益 | 🟡 134 个提交里 **105 个是 fix**，其中约 15 条精准命中平台现有链路（详见 §6） |
| **建议路线** | **A（推荐先做）**：`2.0.9 → 2.0.10`，不动 MCP，1–2 天；**B（随后）**：`2.0.10 → 2.0.11` + MCP 2.x 迁移，1–2 周 |

---

## 2. 评估方法

不依赖 release notes 的措辞，全部结论可复现：

1. **导出符号 diff**：对 `src/` 下每个 `__init__.py`，用 AST 解析 `__all__`，比较 `v2.0.9` ↔ `v2.0.11`。
2. **引用符号核查**：AST 解析平台 `app/`、`tests/` 全部 `from agentscope... import`，得到「模块 → 符号」集合，再逐个在上游 `v2.0.11` 源码里确认存在。
3. **私有 API 核查**：`git cat-file -e` 确认模块存活，`diff` 比对 `def`/`class`/`__init__` 签名。
4. **行为契约核查**：逐文件 `git diff v2.0.9..v2.0.11` 重点目录（`workspace/`、`tool/`、`agent/`、`message/`、`event/`、`middleware/`、`mcp/`、`skill/`）。
5. **平台使用面**：按封装目录（`app/services/ai/runtime/agentscope/`，39 个文件、21790 行）逐个确认职责与上游耦合点。

复现命令见 §14.2。

---

## 3. 变更全景

`v2.0.9..v2.0.11`：**134 个提交、370 个文件、+25269 / −2404 行**；类型分布 **105 fix / 17 feat / 4 chore / 3 test / 3 docs**——典型的「以修复为主的维护迭代」，不是一个功能版本。

| 目录 | 变更文件数 | 主要性质 |
|---|---|---|
| `model/` | 34 | 新模型（MiniMax、Volcengine、OpenAI Responses API）、usage 归一化、aclose |
| `realtime/` | 26 | 实时语音（DashScope/OpenAI/Gemini/xAI）、WebRTC 会话、断线续跑 |
| `workspace/` | 16 | Docker/K8s/E2B 修复、`workspace://` 落盘协议、uv 版本化 |
| `tool/` | 13 | 权限建议转义、symlink 判定、schema 推断修复 |
| `rag/` | 10 | 解析器增强（Word/Excel/PPT）、删除范围按 KB 收敛 |
| `tts/` + `asr/` | 9 + 8 | 语音合成/识别（平台不用） |
| `formatter/` | 8 | Gemini thought signature、xAI 媒体、Ollama file:// |
| `middleware/` | 6 | 预算不阻塞结构化输出、tracing 序列化 |
| `embedding/` | 6 | per-call dimensions 覆盖 |
| `classifier/` | 5 | OpenAI Decisions 分类器 |
| `app/` | 52 | Agent service：实时语音、channel、hub、会话事件游标 |

> 口径说明：总量按 `v2.0.9..v2.0.11`（134 提交）统计；目录级文件数按 `v2.0.9..HEAD`（含 10 个未发版提交，共 144 提交）统计，故两者相差 5 个文件。

> 说明：`sop/`、`pipeline/`、`classifier/`、`A2AAgent`、`ModelRouterMiddleware`、`RealtimeAgent` **在 2.0.9 里就已经存在**（2026-09 的 NEWS）。本次 2.0.10/2.0.11 并没有「新增大量能力」，真正的新增主要是 **服务端 WebRTC 实时语音**、**MCP 工具进度流**、**ASR 模块**、**OpenAI Decisions 分类器**。

---

## 4. 兼容性核查（硬证据）

### 4.1 公开 API：新增 15、删除 1

| 模块 | 变化 |
|---|---|
| `agentscope.realtime` | **−1**：`TruncationSupport`（自定义实时适配器才用得到，平台未接入） |
| `agentscope.classifier` | +2：`OpenAIClassifierModel`、`RefusalAnswer` |
| `agentscope.app._router` | +1：`realtime_router`；`_schema` +5（Realtime 请求/响应模型） |
| `agentscope.app._service` | +2：`RealtimeService`、`get_realtime_model` |
| `agentscope.app.storage` | +2：`AgentChatConfig`、`RealtimeModelConfig` |

平台未使用 `realtime`，**唯一删除项不影响我们**。

### 4.2 平台引用符号：61 个全部存在

脚本核查结果：`nanzi 引用的 agentscope 模块数: 18，符号数: 61` → `v2.0.11 中找不到的符号: 无`。

覆盖的模块：`permission`、`mcp`、`tool`、`middleware`、`message`、`workspace`、`skill`、`agent`、`state`、`event`、`model`、`exception`、`formatter`。

### 4.3 私有 API：8 处全部存活，签名不变

| 私有依赖 | 平台位置 | 2.0.11 状态 |
|---|---|---|
| `agentscope.workspace._docker._make_dockerfile`（`_read_template`、`render_dockerfile`、`prepare_build_context`） | `docker_template_patch.py:224`、`docker_prebuild.py:77` | ✅ 模块与函数都在，签名一致 |
| `agentscope.tool._builtin._backend`（`LocalBackend`、`ExecResult`） | `conversation_run_subprocess.py:32,52` | ✅ 签名一致 |
| `agentscope.workspace._k8s._constants.POD_WORKDIR` | `k8s_workspace.py:541`（有 ImportError 兜底） | ✅ 存在 |
| `agentscope.workspace._docker._docker_backend.DockerBackend` | `docker_workspace.py:36` | ✅ 存在 |
| `agentscope.tool._utils` | 多处 | ✅ 存在 |

`git diff` 对 `_make_dockerfile.py`、`_builtin/_backend.py` 的 `def`/`class`/`__init__` 行做 diff：**无输出**。

### 4.4 平台行为契约

| 契约 | 平台位置 | 上游变化 | 判断 |
|---|---|---|---|
| `PermissionBehavior` / `PermissionDecision` | `tools.py` 26 处、`workspace.py` 4 处、`middleware.py:812` | `permission/` **0 文件变更** | ✅ 无风险 |
| `MiddlewareBase` 钩子 | `middleware.py:469`（`ModelCallStatsMiddleware`）、`:746`（`ToolPermissionMiddleware`） | `_base.py` **零 diff** | ✅ 无风险 |
| `AgentState` 序列化 | `assistant_agent_runner.py:2227`、`resume_stream.py:38` | `state/` **0 变更** | ✅ 快照格式稳定 |
| `TracingMiddleware` | `agent_runtime.py:192` | 仅新增 MiniMax 属性映射 | ✅ 兼容 |
| `ToolChunk` / `ToolChoice` / `Toolkit` | `tools.py` 6 处、`models.py:411` | `_toolkit` 仅 `McpError`→`MCPError`；`ToolChunk` 过滤 `mcp_progress` | ✅ 兼容 |
| 事件类型（`UserConfirmResultEvent`、`ExternalExecutionResultEvent`） | `assistant_agent_runner.py:3589`、`resume_stream.py:115` | 新增 CUSTOM 事件文档；取消语义变化见 §5.1 | 🟡 需复核 |
| Workspace 子类（Local/Docker/K8s/E2B/自研 SSH） | `workspace.py`、`workspace_ssh.py` | DataBlock 落盘 URL 改 `workspace://` | 🟡 见 §5.2 |

### 4.5 平台完全未使用的上游模块

`realtime`、`tts`、`asr`、`rag`、`sop`、`pipeline`、`classifier`、`embedding`、`memory`、`a2a`、`app/*` 在 `app/ tests/` 中检索均为 **0 命中**（仅 `imports.py` 用字符串探测 `agentscope.app.storage`、`agentscope.app.message_bus`，不引符号）。

**含义**：升级的兼容风险集中在 `workspace` / `tool` / `agent` / `message` 四块；`app` 模块那 52 个文件的改动对我们完全无感。

---

## 5. 破坏性 / 行为变化清单（升级必须复核）

### 5.1 🔴 并行工具取消语义（`agent/_agent.py`）

- **2.0.9**：并行工具执行中收到取消时，先 flush 已排队的事件，然后 `asyncio.current_task().uncancel()` 并 `return`——**吞掉取消、正常返回**，调用方靠已 flush 的 `ToolResultEndEvent(state=INTERRUPTED)` 感知中断。
- **2.0.11**：同样 flush 已排队事件，但改为 **`raise` 重新抛出 `CancelledError`**。上游注释写明原因：在到达 toolkit 之前就被取消的调用**不会**产生 interrupted 结果，只能靠异常传播关闭。

（证据：`git diff v2.0.9..v2.0.11 -- src/agentscope/agent/_agent.py` 中 `asyncio.current_task().uncancel(); return` → `raise`）

**影响**：平台的 `event_stream.py`、`resume.py`、`stream_idle_guard.py`、`agent_delegate_tool.py` 的中断/恢复路径都建立在「事件流正常走到 INTERRUPTED」的假设上。升级后可能表现为：用户点停止 → 上层收到 `CancelledError` 而非「正常结束 + 中断事件」→ 草稿落库、确认卡回收、子代理状态（`SubAgentResultStatus.INTERRUPTED`）的判断分支出错。

**处置**：升级前先写一组「取消时事件流行为」的回归测试（当前行为断言），升级后跑；再决定是否需要在 `event_stream` 里把 `CancelledError` 归一化成中断事件。

### 5.2 🟡 DataBlock 落盘 URL：`file://` → `workspace://`

上游 `workspace/_base.py` 把内联 base64 的 `DataBlock`（含 tool result 与 hint）落盘后改用可移植的 `workspace:///<rel>` 引用，并新增 `workspace/_offload_protocol.py`。

**平台现状**：`workspace_ssh.py:1096` 仍产出 `file://{rel}`。

**影响**：自研 SSH workspace 的上下文归档在 2.0.11 下与上游读取端协议不一致——回放/读取媒体时可能取不到文件（`file://` 不再被统一解析）。

**处置**：把 `workspace_ssh.py` 的 URL 改成 `workspace:///{rel}`，并确认平台自己读取 DataBlock 的代码（`tool_result_context.py`、前端媒体预览）能识别新协议。

### 5.3 🟡 路径安全判定改 `realpath`

`tool/_builtin/_base.py` 的路径判定改为先解析符号链接再判断危险路径（`eadb16f3`），且 Write/Edit 的权限建议会转义 glob 元字符（`1a60e2d9`）。

**影响**：更安全，但会改变「符号链接指向 `/etc` 时是否拦截」的判定结果；平台 `tool_guard.py` 自己有一层路径校验，需要确认两层规则不冲突。

**处置**：跑平台现有的路径越权测试；补一条「符号链接指向敏感目录」的用例。

### 5.4 🟡 MCP 工具字段名：`inputSchema` → `input_schema`

2.0.11 的 MCP 适配层改用 MCP SDK 2.x 的模型字段（`input_schema`、`read_only_hint`）。

**平台现状**：
- `mcp_factory.py:159-164` 已同时读 `readOnlyHint` 与 `read_only_hint` → ✅ 天然兼容
- `mcp_client.py:921` **只读 `t.inputSchema`**（回退 `parameter_schema`）→ ❌ MCP 2.x 下会取不到 schema

**处置**：改为「`input_schema` → `inputSchema` → `parameter_schema`」三级回退。

### 5.5 其他（对我们无实际影响）

- `rag._knowledge.delete()` 增加 `metadata_filter`、`delete_document` 强制按 KB 范围删除 —— 平台用自研知识库，不用 `agentscope.rag`。
- `app/storage` 的 `context_config`/`react_config`/`invite_config` 折叠进 `chat_config` —— 平台不用 agentscope 的 app service。
- `POST /workspace/skill` 接口删除 —— 平台不用它的 HTTP 路由。
- `realtime` 删 `truncate()` 与 `TruncationSupport` —— 平台未接入实时语音。

---

## 6. 升级收益：命中平台现有链路的修复

以下 15 条是 2.0.9→2.0.11 中**直接对应平台已实现功能**的修复，属于「白拿」的收益（升级即生效，无需改代码）：

| # | 修复 | 上游提交 | 对应平台功能 |
|---|---|---|---|
| 1 | 事件回放保持 tool data-block 身份（合并同 id 分片、补 `block_id`） | `6642ed61` | 会话回放 `event_stream.py` / 前端时间线 |
| 2 | 消息 `append_usage()` 改为拷贝，避免共享引用被后续累加污染 | `13a855be` | token 用量统计（`ModelCallStatsMiddleware`） |
| 3 | tracing 把 pydantic 工具结果序列化为 JSON 而非 `repr` | `4f575d1a` | Langfuse span 属性 |
| 4 | 部分函数的注解/docstring 保留（`functools.partial` 工具） | `9b94a634` | 工具 schema 推断 |
| 5 | `_agent_state` 注入参数不再进入推断出的 function schema | `f0177cf7` | 工具 schema 净化 |
| 6 | Write/Edit 权限建议转义 glob 元字符 | `1a60e2d9` | 权限确认卡的路径展示 |
| 7 | 危险路径判定前先解析符号链接 | `eadb16f3` | 工具安全 |
| 8 | Docker workspace 初始化失败时清理容器 | `d8283ff6` | 沙箱生命周期 |
| 9 | `.mcp` 持久化文件按 `utf-8-sig` 读（BOM 容错） | `402c3664` | 沙箱 MCP 配置 |
| 10 | 技能目录大小写冲突解析 | `6423e502` | 技能装载 |
| 11 | `SKILL.md` 按 `utf-8-sig` 读取（BOM 保留 frontmatter） | `78c4ec47` | 技能元数据解析（平台自己也做了 BOM 容错） |
| 12 | 嵌套媒体从 context 归档 offload 出去 | `34457377` | 上下文归档体积 |
| 13 | 保留 MCP 结构化工具结果 | `7c198c5f` | MCP 工具结果展示 |
| 14 | 预算耗尽不再阻断结构化输出 | `24aac5d1` | 若接入预算中间件 |
| 15 | GoalPipeline 结构化输出重试设上限、TeamPipeline 失败后 join 其余 sibling | `e9450d08`、`8bd293b3` | 若接入 pipeline |

---

## 7. 新能力评估（值不值得接入）

> 重要前提：除「MCP 工具进度流」「ASR」「OpenAI Decisions 分类器」「WebRTC 实时语音」外，其余能力 **2.0.9 已具备**。所以下表回答的是「要不要在 nanzi 用起来」，而不是「升不升级才有」。

| 能力 | 上游形态 | 平台现状 | 价值 | 接入成本 | 结论 |
|---|---|---|---|---|---|
| **MCP 工具进度流** | `MCPClient(stream_progress=True)`，进度以 `CustomEvent(name="mcp_progress")` 抛出（`progress`/`total`/`message`/`tool_call_id`） | 工具调用只有「开始/结束」，长任务期间无进度 | 高：外部 MCP 工具跑几十秒时用户能看到进展 | 中：订阅事件 → SSE → 前端时间线；需 2.0.11（或 2.0.9 + 该 commit 不存在） | **P0，随 2.0.11 一起做** |
| **`ModelRouterMiddleware`** | 按分类器结果给每条回复选模型（`ChatModelCandidate` 列表） | 每个智能体固定模型 + 失败 fallback（`mark_model_fallback`） | 中高：简单问题走小模型可降本 | 低：中间件 + 一个分类器 | **P1，值得试点** |
| **`ReplyBudgetControlMiddleware`** | 单次回复的加权 token 预算，超预算强制收尾（`<system-reminder>` 提示） | 只有事后统计，无硬预算 | 中高：防单轮烧穿配额 | 低：中间件 + 配置 | **P1，值得试点** |
| **`classifier`（OpenAI Decisions / Jev）** | `ClassifierModelBase` + `OpenAIClassifierModel`，支持文本/图片分类 | 无通用分类器（意图识别是自研） | 中：配合 ModelRouter、内容分级 | 低-中 | **P2，与 Router 一起看** |
| **SOP** | `SOP` 定义 + `SOPEngine` 逐步推进 + `SOPRunState` 跨进程持久化（**实验性**） | 长任务靠确认卡 + 外部执行恢复，没有「步骤 + 验证门槛」模型 | 高：长任务可验证、可断点续跑，和平台的长任务场景天然契合 | 高：任务定义、状态存储、前端展示、与现有 resume 打通 | **P2，值得做 PoC，不建议直接上生产** |
| **TeamPipeline** | leader 委派给成员并发执行并汇总（`TeamMember`） | 已有「并行委派 1–4 个子智能体」（`agent_delegate_tool.py:1232`） | 低-中：我们已有并发委派，缺的是 leader 动态规划 | 中 | **P3，先对照评估，不急着换** |
| **A2A** | `A2AAgent`，与远端 A2A 协议 agent 对话 | 无跨平台互通 | 中：可作为「接入第三方 agent」的标准化通道 | 中 | **P3，有明确外部需求再做** |
| **Channels（飞书/钉钉/Discord）** | `app/channel/*`，依赖 app service 的 bus/storage | 无 IM 接入 | 中高（若有 IM 需求） | 高：依赖整个 app service 体系，无法只取 channel | **P3，需先决定是否引入 app service** |
| **Skill / MCP Hub** | `app/hub/*`：`SkillHubBase`、`ClawSkillHub`、`GitHubHub`，浏览→安装→加入 workspace | 技能广场是静态 `catalog.json` + 复制提示词 | 中：可把技能广场从「静态目录」升级为「在线 hub」 | 中：可只借鉴 `_card.py`/`_base.py` 的模型与解析，不引入整个 app | **P3，可借鉴不照搬** |
| **实时语音（realtime/tts/asr/WebRTC）** | `RealtimeAgent`、`RealtimeService`、WebRTC 会话、TTS/ASR 模型 | 纯文本 | 低（当前无语音需求） | 高（WebRTC 基础设施 + aiortc） | **不做** |
| **RAG 模块** | `agentscope.rag`（分块、解析、向量库） | 自研知识库 + 检索链路 | 低 | 高（替换成本） | **不做** |
| **app 服务模块** | FastAPI agent service 全家桶 | 平台自己就是这类服务 | 低（重叠） | 极高 | **不做**，只借鉴 hub/channel 的实现 |

---

## 8. 依赖影响与运维成本

### 8.1 依赖变化（`pyproject.toml`）

| 变更 | 说明 |
|---|---|
| `mcp<2.0.0` → **`mcp>=2.0.0`** | 与平台 `requirements.txt:42` 的 `mcp==1.29.1` 冲突，**必须一起迁移** |
| 新增 `httpx2>=2.5.0` | MCP 2.x 从 `httpx` 切到 `httpx2`；与现有 `httpx` 并存，镜像多一个包 |
| 新增 `certifi>=2026.10.19` | 剔除不受信 CA |
| `service` extra 新增 `aiortc>=1.14` | 平台用的正是 `[service,...]` extras，会装上 WebRTC 依赖（`av`、`cryptography`、`pylibsrtp` 等），镜像体积与构建时间增加 |
| `realtime` 移除 `sounddevice`、新增 `realtime-local` | 平台未装 `realtime` extra，无影响 |

### 8.2 沙箱镜像：tag 全量失效

`workspace/_docker/Dockerfile.template` 变更：

- uv 安装从 `curl https://astral.sh/uv/install.sh` 改为 `https://astral.sh/uv/{uv_version}/install.sh`（`UV_VERSION=0.13.4`）
- apt 段新增 `update-ca-certificates`

`compute_image_tag()` 对 Dockerfile 全文做 sha256 → **所有已预构建的沙箱镜像 tag 都会变**，升级后首次使用需全量重建（平台有预构建流程 `docker_prebuild.py`，但其内部参数不变，tag 由内容决定）。

**⚠️ 复审补记（K8s 预置镜像，已修）**：Docker 策略与 K8s 冷启动 bootstrap 都读上游
`_GATEWAY_BASE_REQUIREMENTS`，所以自动跟上了；但 **K8s 预置镜像脚本
（`k8s_deploy/build-k8s-sandbox-image.sh`）另有一份手工清单**，仍是 `mcp<2.0.0` 且没有 `httpx2`/`certifi`，
按它重建镜像会在构建期 `import agentscope.mcp` 处失败。已把该清单改为与上游逐项对齐，并让 Dockerfile 从
单一 `BASE_REQS` 展开；一致性由 `tests/test_k8s_sandbox_image_gateway_deps.py` 守护。详情见升级报告 §12.2。
**K8s 预置镜像需重建一次。**

### 8.3 平台自己的模板补丁需要跟进

`docker_template_patch.py:138-141` 的换源锚点是：

```python
uv_old = (
    " && curl -LsSf https://astral.sh/uv/install.sh \\\n"
    "        | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh"
)
```

2.0.11 的模板已变成 `curl -LsSf -o /tmp/uv-installer.sh ... https://astral.sh/uv/{uv_version}/install.sh`，**锚点失配**——不会报错（代码里有 `if uv_old in content`），但换源逻辑静默失效，构建时仍会走 astral.sh 官方脚本（国内网络下更慢、更易失败）。

**处置**：升级时同步更新锚点，并给这条规则加一个「锚点命中」的自检日志/测试（否则以后还会静默失效）。

---

## 9. 升级路线

### 9.1 路线 A：2.0.9 → 2.0.10（推荐先做）

- **不动 MCP**：2.0.10 的 `mcp` 约束仍是 `mcp<2.0.0`，与 `mcp==1.29.1` 兼容。
- 收益：§6 中除 MCP 相关的修复（1–13 大部分）、Docker 清理、事件回放、usage 拷贝等。
- 风险：`service` extra 会新增 `aiortc`（体积/构建时间）。
- 工期：1–2 天（含验证）。

### 9.2 路线 B：2.0.10 → 2.0.11 + MCP SDK 2.x 迁移

- 前置：完成 MCP 2.x 迁移（`FastMCP`→`MCPServer`、`ClientSession`+`sse_client`→`Client`、`mcp.types`→`mcp_types`、`AuthSettings.validate_token_resource`、`httpx2`、4 MiB 限制、`x-mcp-header` 校验）。
- 收益：MCP 工具进度流、MCP 2.x 协议能力、其余修复。
- 工期：1–2 周（MCP 迁移是大头）。

### 9.3 为什么不能一步到位

平台对 MCP SDK 1.x 的依赖是**直接 import**（服务端 `FastMCP` + `streamable_http_app`，客户端 `ClientSession` + `sse_client`），2.x 是破坏性重构。把「agentscope 升级」和「MCP 大版本迁移」绑在一个 PR 里，一旦出问题无法区分归因，回滚也很重。

**实测佐证（关键）**：`v2.0.10` 的 `_GATEWAY_BASE_REQUIREMENTS` 仍是 `mcp<2.0.0`，`pyproject.toml` 也未引入 `httpx2`/`certifi` —— 也就是说 2.0.10 的沙箱网关仍装 MCP 1.x，平台内联的 stdio MCP server（`workspace_container_mcp.py:102`、`workspace_ssh.py:1134`）不受影响。**这正是路线 A 可以先行的原因。**

### 9.4 MCP SDK 2.x 迁移详解（路线 B 的前置）

#### 9.4.1 硬破坏点：`mcp.server.fastmcp` 是一个「报错 shim」

解包 `mcp-2.3.0-py3-none-any.whl` 后，`mcp/server/fastmcp.py` 全文只有一段 `raise ModuleNotFoundError`，内容是「FastMCP 已改名 MCPServer，见迁移指南，或 pin `mcp<2`」。**任何 import 立即抛异常**，不是静默降级。

平台共 8 个文件 import mcp，其中 4 处走 `mcp.server.fastmcp`：

| 文件 | 用法 | 性质 |
|---|---|---|
| `app/services/mcp/platform_mcp.py:22` | `from mcp.server.fastmcp import Context, FastMCP`，`class PlatformFastMCP(FastMCP)` | 平台主进程，启动即崩 |
| `app/services/mcp/echo_server.py:13` | `from mcp.server.fastmcp import Context, FastMCP` | 平台主进程，启动即崩 |
| `app/services/ai/runtime/agentscope/workspace_container_mcp.py:102` | 内联 stdio server 脚本（`python -c` 在容器内执行） | 沙箱网关内运行 |
| `app/services/ai/runtime/agentscope/workspace_ssh.py:1134` | 同上（SSH 沙箱版） | 沙箱网关内运行 |

后两者是**字符串内联脚本**，改动会影响沙箱内的 Bash 工具可用性（脚本跑不起来 → `sandbox` MCP 注册失败 → 智能体无 Bash 工具）。

#### 9.4.2 实测兼容面（mcp 2.3.0）

| API | 2.3 状态 | 平台使用点 | 动作 |
|---|---|---|---|
| `mcp.server.fastmcp.FastMCP` | ❌ 移除（raise） | 上述 4 处 | 改 `from mcp.server.mcpserver import MCPServer` |
| `MCPServer.__init__` 的 `streamable_http_path` / `json_response` / `stateless_http` / `transport_security` | ❌ 不在构造参数里，**移到 `streamable_http_app()` 参数** | `platform_mcp.py:231-247`、`echo_server.py:261-269` 构造；`main.py:440-441` 调用 | 参数搬家 |
| `session_manager` | ✅ 保留（property） | `platform_mcp_lifespan`、`echo_mcp_lifespan` | 不改 |
| `mcp.server.transport_security.TransportSecuritySettings` | ✅ 保留 | `transport_security.py`、`public_url.py`、`echo_server.py` | 不改 |
| `mcp.server.auth.settings.AuthSettings` | ✅ 保留 | `platform_mcp.py:22` | 不改；建议显式 `validate_token_resource=True` |
| `mcp.server.auth.provider.AccessToken` | ✅ 保留 | `platform_oauth.py` | 不改 |
| `mcp.server.auth.middleware.auth_context.get_access_token` | ✅ 保留 | `platform_mcp.py` | 不改 |
| `mcp.client.sse.sse_client` | ✅ 保留（`mcp/client/sse.py:36`） | `mcp_client.py` | ⚠️ **复审订正**：调用点不用改，但 `httpx_client_factory` 必须返回 `httpx2.AsyncClient`（见下） |
| `mcp.ClientSession` | ✅ 保留（`mcp/client/session.py:388`） | `mcp_client.py` | 不改（可选迁到新 `Client`） |
| `mcp.types` | ✅ 保留为 `mcp_types` 的镜像命名空间 | `mcp_client.py` | 不改 |
| `mcp.client.client.Client` | ✅ 新增高层客户端 | 未使用 | 可选 |

**⚠️ 复审订正（2026-10-29，已修）**：本表只核对了「符号是否存在」，漏了**回调参数类型**。mcp 2.x 的
客户端传输整体换成了 `httpx2`，`sse_client(httpx_client_factory=...)` 会把 `httpx2.Timeout` 传进工厂；
平台工厂仍返回 `httpx.AsyncClient` 时，httpx 在 TCP 连接阶段抛
`TypeError: unsupported operand type(s) for +: 'float' and 'Timeout'`，被
`McpSseSession.connect()` 吞成 warning 后**静默降级 Direct HTTP**，SSE-only 的外部 MCP 服务因此完全不可用。
修复：工厂改返回 `httpx2.AsyncClient` + `requirements.txt` 显式锁 `httpx2==2.12.0`，回归用例
`tests/services/mcp/test_mcp_client_sse_transport.py`（真实 SSE 端点端到端）。详情见升级报告 §12.1。

**结论**：真正要改的是「`FastMCP` → `MCPServer`」+「构造参数搬到 `streamable_http_app()`」+「出站传输客户端改用
`httpx2`」三件事，其余 MCP API 在 2.3 仍然可用。

#### 9.4.3 默认值带来的行为变化（必须显式配置）

`MCPServer.streamable_http_app()` 的默认值：

| 参数 | 默认 | 对平台的影响 |
|---|---|---|
| `max_request_body_size` | 4 MiB | 平台 MCP 若承载大 payload（工具入参含长文本/附件）会 413，需按需调大 |
| `session_idle_timeout` | 30 分钟 | 空闲会话被回收，客户端需重连（长任务的中断/恢复要覆盖这个场景） |
| `max_sessions` | 有上限 | 超限返回 503，多租户高并发下需评估 |
| `max_sse_event_size` | 1 MiB | 大工具结果走 SSE 时可能被截断 |

依赖层面：`mcp 2.3` 要求 `httpx2>=2.10.0`、`pydantic>=2.12.0`、`starlette>=0.48.0`、`sse-starlette>=3.0.0`。平台 `pydantic>=2.0.0` 会被抬到 ≥2.12，需回归 Pydantic 2.12 的行为差异（模型校验、序列化告警）。

#### 9.4.4 迁移顺序建议

1. **先只升 mcp**（不升 agentscope）：`requirements.txt` 改 `mcp==2.3.0`，跑测试，收集崩点（预期：4 处 import + 2 处构造参数）。
2. 改 `platform_mcp.py`、`echo_server.py` 的 import 与构造/调用参数。
3. 改两段内联脚本（`workspace_container_mcp.py`、`workspace_ssh.py`），同步验证沙箱里 `sandbox` MCP 能起来、Bash 工具可用。
4. 显式配置 body size / idle timeout / max sessions。
5. 再升 agentscope 到 2.0.11（此时 `mcp>=2.0.0` 已满足）。

---

## 10. 实施步骤（路线 A 详版）

### 阶段 0：准备（0.5 天）

1. 从 `dev-agentscope` 切分支：`dev-agentscope-agentscope2.0.10`。
2. 跑基线：`pytest tests/ai tests/services/ai -q`、`tests/api/portal -k "mcp or skill" -q`，记录通过数与耗时（用于对比）。
3. 新建「取消语义」回归测试：断言当前「点停止 → 事件流以 INTERRUPTED 收尾」的行为（这是 2.0.11 会变、2.0.10 不变的关键差异，先固化）。

### 阶段 1：升版本（0.5 天）

1. `requirements.txt`：`agentscope[service,storage,workspace]==2.0.9` → `==2.0.10`。
2. 本地装：`pip install -r requirements.txt`（注意会带入 `aiortc`）。
3. 启动自检：`python -c "import agentscope; from agentscope.workspace._docker import _make_dockerfile; print('ok')"`。

### 阶段 2：跑测试（0.5 天）

- 全量：`pytest tests/ai tests/services/ai tests/api/portal -q`
- 重点：`tests/ai/runtime/test_docker_template_patch.py`、`test_docker_prebuild.py`、`tests/ai/tools/*`、`tests/frontend`（契约）
- `vue-tsc --noEmit`（前端无改动，仅确认未受影响）

### 阶段 3：镜像重建（0.5 天）

1. 触发预构建，确认新 tag 生成。
2. 用新镜像跑一次沙箱会话：`exec_command`、技能 `npx skills add`、MCP 工具调用。
3. 确认换源补丁是否仍生效（看构建日志里的 `pip install ... uv` 是否出现；若不出现，说明锚点已失配 → 参照 §8.3 修）。

### 阶段 4：回归与灰度（0.5 天）

- 按 §11 手工清单走一遍。
- 灰度：先单实例/单租户。

---

## 11. 验证方案

### 11.1 自动化

| 测试集 | 期望 |
|---|---|
| `pytest tests/ai -q` | 与基线一致（阶段 0 记录的数字） |
| `pytest tests/services/ai -q` | 与基线一致 |
| `pytest tests/api/portal -k "mcp or skill or sandbox" -q` | 全绿 |
| `pytest tests/frontend -q --confcutdir=tests/frontend` | 与基线一致（已知 5 个既有失败除外） |
| `vue-tsc --noEmit` | 0 错误 |

### 11.2 手工验收（按风险排序）

1. **沙箱生命周期**：新建会话 → `exec_command` → 停止 → 重启容器 → 文件仍在。
2. **取消语义**：长工具执行中点「停止」→ 确认前端状态、草稿落库、确认卡回收正确（这是 2.0.11 才会变的行为，2.0.10 用于建立基线）。
3. **技能安装**：技能广场复制提示词 → 沙箱 `npx skills add` → `import_skill_from_sandbox` → 个人库出现、徽标变「已在你的个人技能库」。
4. **MCP**：平台 `/mcp` 端点可被外部客户端连上；外部 MCP 服务（SSE/Streamable HTTP）可被平台调用。
5. **委派**：并行委派 1–4 个子代理 → 用量明细归入父会话（`tests/ai/test_subagent_model_call_stats.py` 的线上对应）。
6. **长任务/确认卡**：触发工具确认 → 批准 → 恢复流继续；刷新后恢复。
7. **上下文归档**：含图片/附件的会话 → 归档 → 回放能取到文件（对应 §5.2）。
8. **trace/用量**：Langfuse 有 span、用量与消息底部一致。

### 11.3 回滚

- 代码：`git revert` 版本号提交 + 重新 `pip install`。
- 镜像：旧 tag 仍在本地/仓库，回滚 `AGENTSCOPE` 版本后 tag 自动回到旧值。
- 数据：本次升级无 DB 变更、无迁移脚本。

---

## 12. 风险登记表

| # | 风险 | 等级 | 触发条件 | 缓解 |
|---|---|---|---|---|
| R1 | MCP 1.x→2.x 强绑定 | 🔴 高 | 升 2.0.11 | 拆两步；MCP 迁移单独 PR + 单独排期 |
| R2 | 并行工具取消语义变化 | 🟠 中高 | 升 2.0.11 | 阶段 0 先固化行为测试；必要时在 `event_stream` 归一化 `CancelledError` |
| R3 | `workspace://` 与自研 SSH workspace 的 `file://` 不一致 | 🟠 中 | 升 2.0.11 | 改 `workspace_ssh.py:1096` + 回放验证 |
| R4 | 换源补丁锚点静默失配 | 🟠 中 | 升 2.0.10/2.0.11（模板已变） | 更新 `uv_old` 锚点 + 加自检 |
| R5 | 沙箱镜像 tag 全量失效 | 🟡 低 | 任何升级 | 预留重建窗口，预热 |
| R6 | `aiortc` 增大镜像/构建时间 | 🟡 低 | 升 2.0.10（service extra） | 评估是否需要精简 extras；必要时向上下游反馈拆 extra |
| R7 | `mcp_client.py:921` 只读 `inputSchema` | 🟡 低（升级后必现） | 升 MCP 2.x | 三级回退兼容 |
| R8 | 路径判定改 `realpath` 影响越权判断 | 🟡 低 | 升 2.0.10/2.0.11 | 补符号链接用例 |

---

## 13. 待确认项

1. **ASR 模块**是否算正式能力：本次唯一新增顶层包，但没有对应 optional extra、也没在 `docs/NEWS.md` 宣布。
2. **`workspace://` 的读取端**：平台前端媒体预览与 `tool_result_context.py` 是否已支持该协议（需实测）。
3. **`aiortc` 是否真的必需**：我们只用 `service` extra 的 storage/router 能力，不用 WebRTC；可考虑向 AgentScope 反馈把 `aiortc` 拆成单独 extra（否则所有用 `[service]` 的项目都要背这个依赖）。
4. **2.0.11 之后的 10 个提交**（HEAD=`e763b351`）是否会在 2.0.12 修掉 R2/R3 相关问题——建议观察一个版本再定 2.0.11 的时间点。

---

## 14. 附录：证据与复现

### 14.1 关键提交

| 提交 | 说明 |
|---|---|
| `6c60853f` | `fix(mcp): support mcp 2.x Python SDK, unpin mcp<2.0.0` |
| `44b5d520` | `feat(mcp): support MCP SDK 2.x and tool progress streaming` |
| `c3c1d7fa` | `feat(app): support realtime voice conversation in agent service` |
| `6642ed61` | `fix(agent): preserve tool data-block identity during event replay` |
| `cbb18504` | `fix(agent): preserve cancellation before concurrent tool execution` |
| `13a855be` | `fix(message): keep a copy of the usage appended to a message` |
| `6423e502` | `fix(workspace): resolve case-insensitive skill directory collisions` |
| `402c3664` | `fix(workspace): read the persisted .mcp file as utf-8-sig` |
| `1a60e2d9` | `fix(tool): escape glob metacharacters in Write/Edit permission suggestions` |
| `eadb16f3` | `fix(tool): resolve symlinks before the dangerous-path check` |

### 14.2 复现命令

```bash
# 变更规模与类型分布
git log --oneline v2.0.9..v2.0.11 | wc -l
git diff --shortstat v2.0.9..v2.0.11
git log --oneline v2.0.9..v2.0.11 | sed -E 's/^[0-9a-f]+ //' | awk '{print $1}' | sort | uniq -c

# 依赖变化
git diff v2.0.9..v2.0.11 -- pyproject.toml

# 平台引用的符号是否健在（AST 解析 __all__ 与 import）
python3 /tmp/alls_diff.py
python3 /tmp/import_check.py

# 私有 API 签名
diff <(git show v2.0.9:src/agentscope/workspace/_docker/_make_dockerfile.py | grep -E "^def |^class ") \
     <(git show v2.0.11:src/agentscope/workspace/_docker/_make_dockerfile.py | grep -E "^def |^class ")

# 中间件基类
diff <(git show v2.0.9:src/agentscope/middleware/_base.py) \
     <(git show v2.0.11:src/agentscope/middleware/_base.py)   # 期望无输出

# 沙箱镜像模板
git diff v2.0.9..v2.0.11 -- src/agentscope/workspace/_docker/Dockerfile.template
```

### 14.3 平台侧需要跟进的文件

| 文件 | 事项 | 关联 |
|---|---|---|
| `requirements.txt` | 版本号与注释（含 mcp 联动说明） | 路线 A/B |
| `docs/md/agentscope_2.0.9_upgrade_guide.md` | 新增 2.0.11 章节（项目约定：改版本号须同步） | 路线 A/B |
| `app/services/ai/runtime/agentscope/docker_template_patch.py` | uv 换源锚点更新 + 命中自检 | R4 |
| `app/services/ai/runtime/agentscope/workspace_ssh.py` | DataBlock URL 改 `workspace://`；内联 MCP 脚本改 `MCPServer` | R3 / 9.4 |
| `app/services/ai/runtime/agentscope/workspace_container_mcp.py` | 内联 MCP 脚本改 `MCPServer` | 9.4 |
| `app/services/mcp/platform_mcp.py`、`echo_server.py` | `FastMCP`→`MCPServer`，构造参数搬到 `streamable_http_app()` | 9.4 |
| `app/main.py` | `streamable_http_app()` 显式传 body size / idle timeout | 9.4.3 |
| `app/services/ai/tools/mcp_client.py` | `input_schema` 三级回退 | R7 |
| `app/services/ai/runtime/agentscope/event_stream.py` | 取消语义归一化（视测试结果） | R2 |

---

## 15. 落地记录（2026-10-29，分支 `dev-agentscope-mcp2.x`）

实际执行的是「路线 B 一步到位」：直接升到 2.0.11 并同步完成 MCP 2.x 迁移（未走路线 A 的两步走）。新增特性（MCP 进度流、模型路由、预算中间件等）本轮一律不接入，目标是**现有功能行为不变**。

### 15.1 依赖变更

| 项 | 变更 |
|---|---|
| agentscope | 2.0.9 → **2.0.11** |
| mcp | 1.29.1 → **2.3.0**（连带 `mcp-types` 2.3.0） |
| 新增运行时依赖 | `aiortc` 1.15.0、`av`、`aioice`、`pylibsrtp`、`pyopenssl`、`dnspython`、`ifaddr`、`google-crc32c`（来自 `service` extra 的 WebRTC 支持） |
| certifi | 2026.7.22 → 2026.10.23 |

### 15.2 代码改动

| 文件 | 改动 | 原因 |
|---|---|---|
| `requirements.txt` | 两个版本锁 + 注释重写 | 2.0.11 要求 `mcp>=2.0.0` |
| `app/services/mcp/platform_mcp.py` | `FastMCP`→`MCPServer`；端点参数搬到新工厂 `platform_mcp_http_app()`；新增 `_PLATFORM_TRANSPORT_SECURITY` + `get_platform_transport_security()`；`AuthSettings(validate_token_resource=True)`；请求体 32 MiB / 会话空闲 1h | 2.x 构造签名变化；默认 4 MiB、30 min 对平台偏紧 |
| `app/services/mcp/echo_server.py` | 同上（`echo_mcp_http_app()` / `_ECHO_TRANSPORT_SECURITY`） | 同上 |
| `app/services/mcp/public_url.py` | 运行期刷新改用各模块 getter | **2.x 的 `MCPServer.settings` 不再持有该对象**，旧写法会静默变成空操作 |
| `app/main.py` | 两处 `mount` 改用工厂函数 | 端点参数不再在构造期 |
| `app/api/portal/endpoints/mcp_service.py` | 在线调试改用 `platform_mcp_http_app()` | 直接调 `streamable_http_app()` 会拿到默认 `/mcp`，请求 `/platform` 变 404 |
| `app/services/ai/tools/mcp_client.py` | 新增 `_tool_input_schema()`：`input_schema` → `inputSchema` → `parameter_schema` | 2.x 字段改名，旧名只剩兜底 |
| `app/services/ai/runtime/agentscope/workspace_container_mcp.py` | 内联 stdio 脚本 `try: mcpserver.MCPServer / except ImportError: fastmcp.FastMCP` | 沙箱网关的 mcp 版本由镜像决定，新旧镜像都要能起 |
| `app/services/ai/runtime/agentscope/workspace_ssh.py` | 同上；`_offload_data_block` 改产 `workspace:///`（原 `file://`） | 与上游 2.0.11 的 offload 协议一致；`file://` 指向远端绝对路径本就无法解析 |
| `app/services/ai/runtime/agentscope/docker_template_patch.py` | uv 换源锚点支持两代模板；两代都不命中时打 warning | 2.0.10+ 模板改成版本化安装脚本，旧锚点静默失配 |

### 15.3 测试改动（5 个文件）

| 文件 | 改动 |
|---|---|
| `tests/ai/runtime/test_docker_template_patch.py` | 断言从固定 `.../uv/install.sh` 改为 `astral.sh/uv` 前缀 |
| `tests/ai/runtime/test_agentscope_workspace.py` | 内联脚本打桩对象改为 `mcp.server.mcpserver.MCPServer` |
| `tests/services/mcp/test_platform_mcp.py` | 用 `platform_mcp_http_app()` 构造被测应用 |
| `tests/api/test_platform_mcp_contract.py` | 源码契约断言同步新写法 |
| `tests/api/test_mcp_echo_server_api_contract.py` | 同上 |
| `tests/services/test_mcp_public_url_sync.py` | **新增**用例：`apply_public_url_to_mcp` 必须更新到两个模块级实例（防 R-旧链路静默失效） |

### 15.4 验证结果

| 测试集 | 结果 |
|---|---|
| `pytest tests/ai -q` | **2845 passed / 32 xfailed**（与升级前基线一致） |
| `pytest tests/api -q` | **1335 passed / 8 xfailed**（初跑 2 个契约失败，均已随断言更新修复并复跑） |
| `pytest tests/services -q` | **2376 passed / 11 skipped / 5 xfailed** |
| `pytest tests/services/mcp tests/api/test_platform_mcp_contract.py -q` | **108 passed / 1 xfailed** |
| `pytest tests/services/test_mcp_public_url_sync.py -q` | **4 passed**（含新增回归用例） |
| `pytest tests/ai/runtime/test_docker_template_patch.py -q` | **16 passed** |
| `verify_agentscope_imports()` | `ok=True`，`missing=[]` |
| 渲染 Dockerfile 抽查 | uv 段已替换为 `pip install --index-url ... uv`、`update-ca-certificates` 保留、`node:22-slim` 正常 |

### 15.5 尚未验证（需要环境或人工）

1. **沙箱镜像重建**：tag 必变（模板内容变了），需在测试环境触发一次预构建并跑通 `exec_command` / 技能安装 / 内联 MCP（`sandbox` 工具）。
2. **真实「停止」按钮**：2.0.11 的并行工具取消改为 `raise CancelledError`；平台层有 `normalize_runtime_error` 兜底，但需要在界面上点一次停止，确认草稿落库、确认卡回收、子代理状态正确。
3. **外部 MCP 服务接入**：SSE / Streamable HTTP 各接一个真实服务（MCP 2.x 的重定向只跟同源、4 MiB 限制、30 min 会话）。
4. **`workspace://` 的前端渲染**：上游自己标注为 TODO，平台侧暂未消费该协议；SSH 沙箱的媒体引用会变成 `workspace:///data/...`。
5. **镜像体积/构建时长**：`aiortc`/`av` 为 `service` extra 新增，需确认是否可接受。

### 15.6 回滚

`requirements.txt` 两行改回 `agentscope==2.0.9` / `mcp==1.29.1`，回滚本次 9 个源文件的改动，重建后端镜像；沙箱镜像 tag 会再次变化。全程无数据库迁移。

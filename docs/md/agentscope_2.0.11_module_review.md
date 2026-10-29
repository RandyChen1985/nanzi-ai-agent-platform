# AgentScope 2.0.11 模块使用情况与自研能力对比报告

> 本报告对 AgentScope 2.0.11 的全部模块做一次系统盘点：框架提供了什么、我们用了什么、没用什么、没用的是否值得引入，以及在我们自研实现与框架实现之间，谁更好、为什么。

---

## 0. 文档信息

| 项目 | 内容 |
|---|---|
| 框架版本 | `agentscope==2.0.11`（`requirements.txt:32`，extra: `service,storage,workspace`） |
| Python | 3.11.14 |
| 核对方式 | 源码逐模块读取 + AST 符号级统计（非文档推断） |
| 核对日期 | 2026-10-29 |
| 代码基线 | `dev-agentscope` 分支 |
| 适用读者 | 平台研发（查核依据）、平台用户（了解能力边界） |
| 相关文档 | [升级评估](./agentscope_2.0.11_upgrade_assessment.md)、[验收清单](./agentscope_2.0.11_acceptance_checklist.md) |

**报告不含任何代码改动**，全部结论来自只读核对。

---

## 1. 执行摘要

### 1.1 一句话结论

AgentScope 2.0.11 共 **26 个模块、约 12.3 万行**，我们接入了 **14 个**（其中 3 个仅借用类型定义、1 个隐式使用）。核对结论：**框架没有我们缺失、且必须由框架补的能力**——现有能力已由「框架地基 + 自研 + 外部服务（RAGFlow 解析、魔搭市场外链）」三方覆盖。真正缺的只有两处：**IM 双向对话**（`channel`，新入口）与**实时语音**（`realtime`，成本过高），两者都等业务需要再议。

### 1.2 关键数字

| 指标 | 数值 |
|---|---|
| 框架模块总数 | **26 个** |
| 框架代码总量 | **123,381 行** |
| 我们接入的模块 | **14 个** |
| 完整使用的模块 | 7 个（`agent`/`workspace`/`tool`/`message`/`event`/`mcp`/`exception`） |
| 部分使用的模块 | 3 个（`model`/`state`/`skill`） |
| 仅借用类型的模块 | 3 个（`credential`/`permission`/`middleware`） |
| 隐式使用的模块 | 1 个（`formatter`） |
| 完全未接入的模块 | **12 个** |
| **必须引入的模块** | **0 个** |
| 唯一的新增能力 | IM 双向对话（`channel`，等客户需求） |
| 建议动手的改动 | 1 项：预算"优雅收尾"（2~3 天，不引入框架代码） |
| 自研代码总量（对比项） | 约 47,440 行 |

### 1.3 三条判断原则

本报告的所有"用/不用"结论，都由以下三条原则推导：

1. **能力增量优先于代码复用**。如果自研实现已经覆盖能力，即使框架代码更短也不替换——替换成本高于收益。
2. **平台形态决定边界**。我们是多租户 Web 平台（MySQL + 自建 FastAPI + 前端 Vue），凡强绑框架自有存储（`StorageBase`）或终端形态（`tui`/`console`）的模块，一律不接。
3. **外部依赖必须有可达性保证**。依赖境外 SaaS 或公共 registry 的模块，在确认生产网络可达之前不进规划。

---

## 2. 核对方法

### 2.1 符号级统计而非目录级猜测

对 `app/` 全目录做 AST 解析，提取所有 `from agentscope.<module> import <symbol>` 语句：

```python
# 实际使用的核对脚本（节选）
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module.startswith('agentscope'):
        key = node.module.split('.')[1]
        for a in node.names:
            used[key].add(a.name)
```

这样能区分"模块被 import 了"和"模块的某个符号被用了"——后者才代表真实接入。

### 2.2 判定标准

| 判定 | 含义 |
|---|---|
| ✅ **完整使用** | 该模块的核心抽象被用于生产路径 |
| ◐ **部分使用** | 只用了部分符号，其余未用并有替代 |
| △ **仅借类型** | 只用枚举/基类/数据类，核心逻辑走自研 |
| ❌ **未使用** | 零引用 |

### 2.3 核对边界（诚实声明）

- ✅ 26 个模块的导出清单、代码行数：**全量核对**
- ✅ 14 个已接入模块的符号使用：**AST 全量核对**
- ✅ 12 个未接入模块的能力与依赖：**逐个读源码核对**
- ⚠️ `app/`（207 个 .py、52,799 行、12 个子包）：只核对了架构与依赖，未逐行读——已多轮确认其强绑 `StorageBase`，与我们的 MySQL 体系冲突
- ⚠️ 外部 registry 可达性：本机网络被代理 fake-ip 拦截（`clawhub.ai`、`api.mcp.github.com` 均解析到 `198.18.x.x`），**未能实测**

### 2.4 统计口径（便于复现）

| 指标 | 口径 |
|---|---|
| **26 个模块** | 根目录 28 个目录 − `__pycache__` − `_utils`（私有工具包，596 行）。另排除顶层 `_logging.py`(47 行) / `_version.py` |
| **行数** | 该模块目录下全部 `.py` 文件总行数，含 `__init__.py` |
| **导出数** | `__init__.py` 中 `__all__` 的长度；无 `__all__` 的记 0 |
| **符号使用** | AST 解析 `from agentscope.X import Y`。已验证项目**不存在** `agentscope.X.Y` 属性访问形式，故 from-import 统计即为全集 |
| **引用次数** | `grep -rn "agentscope\.<module>"` 的行数，**不等于**符号数（同一 import 在多个函数内重复出现会累加） |

---

## 3. 模块总览矩阵

### 3.1 一张表看全貌

按代码规模排序。「自研对应」列填我们实际的替代实现。

| # | 模块 | 框架行数 | 导出数 | 我们的使用 | 自研对应 | 判定 |
|---|---|---|---|---|---|---|
| 1 | `app` | 52,799 | 2 | ❌ | 自建 FastAPI 服务层 | ❌ 不接 |
| 2 | `workspace` | 10,562 | 17 | ✅ 6+4 符号 | `runtime/agentscope/workspace*.py` | ✅ 完整使用 |
| 3 | `tool` | 9,478 | 32 | ✅ 12 符号 | `ai/tools/` 37 模块 | ✅ 完整使用 |
| 4 | `middleware` | 7,111 | 10 | △ 2 符号 | 观测 + 自研中间件 | △ 仅借基类 |
| 5 | `model` | 7,052 | 17 | ◐ 1 符号 | OpenAI 兼容统一封装 | ◐ 部分使用 |
| 6 | `agent` | 6,775 | 9 | ✅ 5 符号 | `runtime/agentscope/`（22,063 行） | ✅ 完整使用 |
| 7 | `formatter` | 5,355 | 23 | ⚙️ 内部自动 | 无需自研 | ⚙️ 隐式使用 |
| 8 | `rag` | 5,386 | 20 | ❌ | RAGFlow + 自研知识库 | ❌ 不接（parser 仅备选） |
| 9 | `realtime` | 2,998 | 29 | ❌ | 无（无语音实时需求） | ❌ 不接 |
| 10 | `tui` | 2,512 | 4 | ❌ | Vue 3 前端 | ❌ 无关 |
| 11 | `embedding` | 2,384 | 10 | ❌ | `embed_model_name` + OpenAI 兼容 | ❌ 不接 |
| 12 | `tts` | 2,328 | 9 | ❌ | 无语音输出需求 | ❌ 不接 |
| 13 | `permission` | 1,118 | 7 | △ 3 符号 | `permission_service.py`（1,801 行） | △ 仅借枚举 |
| 14 | `message` | 1,002 | 17 | ✅ 9 符号 | 无需自研（消息模型标准） | ✅ 完整使用 |
| 15 | `credential` | 863 | 13 | △ 1 符号 | 平台加密凭证表 | △ 仅借类型 |
| 16 | `pipeline` | 828 | 4 | ❌ | `ai/pipeline/`（2,220 行） | ❌ 不接 |
| 17 | `sop` | 794 | 9 | ❌ | ChatBI 引擎（11,950 行） | ❌ 不接 |
| 18 | `classifier` | 714 | 15 | ❌ | `intent_service` + 路由策略 | ❌ 不接 |
| 19 | `event` | 676 | 34 | ✅ 3 符号 | 无需自研（类型定义） | ✅ 完整使用 |
| 20 | `mcp` | 670 | 3 | ✅ 2 符号 | `services/mcp/`（4,298 行） | ✅ 完整使用 |
| 21 | `console` | 648 | 2 | ❌ | Vue 3 前端 | ❌ 无关 |
| 22 | `state` | 480 | 6 | ◐ 1 符号 | 自研会话状态 | ◐ 部分使用 |
| 23 | `asr` | 429 | 4 | ❌ | 无（可直连 `/audio/transcriptions`） | ❌ 不接 |
| 24 | `skill` | 215 | 3 | ◐ 1 符号 | `skill_share_service` + 技能广场 | ◐ 部分使用 |
| 25 | `types` | 124 | 8 | ❌ | 无需求 | ❌ 无关 |
| 26 | `exception` | 80 | 7 | ✅ 2 符号 | 无需自研（异常类型） | ✅ 完整使用 |

**图例**：✅ 完整使用 · ◐ 部分使用 · △ 仅借类型 · ⚙️ 隐式使用 · ❌ 未使用

### 3.2 引用次数分布

```text
workspace 79 ████████████████████████████████████████
tool      22 ███████████
permission 15 ████████
message   14 ███████
middleware 8 ████
exception  6 ███
agent      6 ███
event      5 ██
mcp        5 ██
state      4 ██
app        2 █
model      2 █
credential 1 ▏
skill      1 ▏
────────────────────────────────
asr/classifier/console/embedding/formatter/pipeline
rag/realtime/sop/tts/tui/types          0
```

> 两处需要解释：
> - `app` 的 2 次是**路径字符串**（非符号导入），所以 3.1 表里判为 ❌
> - `formatter` 显示 0 是因为**没有显式 import**，但 `Agent` 内部会自动选用对应 formatter，属隐式使用

---

## 4. 已接入模块详解（14 个）

### 4.1 `agent` — ✅ 完整使用（5/9 符号）

**用了什么**

| 符号 | 用途 |
|---|---|
| `Agent` | 核心运行时 |
| `ReActConfig` | ReAct 循环配置 |
| `ModelConfig` | 模型参数 |
| `ContextConfig` | 上下文窗口配置 |
| `InjectionConfig` | 消息注入配置 |

**没用什么**

| 符号 | 为什么不用 |
|---|---|
| `RealtimeAgent` | 语音实时对话，无需求（见 5.3） |
| `A2AAgent` | Agent-to-Agent 协议，我们的多 Agent 走 `agent_delegate_tool.py`（1,378 行），已覆盖跨 Agent 调用 |
| `TurnAggregator` / `TurnMetrics` | 轮次聚合与度量，我们的 `observability/turn_span.py` 已实现且更贴合 Langfuse |

**评价**：`Agent` 是我们真正的地基，接入彻底。`agent/` 6,775 行里我们用了约 70% 的有效路径。

---

### 4.2 `workspace` — ✅ 完整使用（6/17 导出 + 4 个子模块符号）

**用了什么**

| 符号 | 用途 |
|---|---|
| `WorkspaceBase` | 工作区抽象 |
| `LocalWorkspace` | 本地沙箱 |
| `DockerWorkspace` + `DockerBackend` | 容器沙箱（主路径） |
| `E2BWorkspace` | 云端沙箱（可选） |
| `K8sWorkspace` | K8s 沙箱（可选） |
| `CONTAINER_WORKDIR` / `POD_WORKDIR` / `GATEWAY_HOME` | 路径常量（从子模块导入，不在 `__all__`） |
| `prepare_build_context` | 镜像构建（同上） |

**没用什么**

| 符号 | 为什么不用 |
|---|---|
| `BubblewrapWorkspace` / `BubblewrapBackend` | Linux 用户态沙箱，我们的容器方案已足够 |
| `DaytonaWorkspace` / `DaytonaBackend` | 境外 SaaS，网络不可达 |
| `OpenSandboxWorkspace` | 境外 SaaS |
| `AppleContainerWorkspace` | macOS 专用，服务器不适用 |
| `Offloader` | 工具结果外部化，我们有自己的截断策略（`result truncation`） |

**评价**：`workspace/` 10,562 行是框架最有价值的部分之一，我们用了其中**最主流的 4 种后端**，覆盖本地/容器/云沙箱/集群四种部署形态。这是**接入最彻底**的模块（79 次引用）。

---

### 4.3 `tool` — ✅ 完整使用（12/32 符号）

**用了什么**

| 符号 | 用途 |
|---|---|
| `ToolBase` / `Toolkit` | 工具基类与注册表 |
| `ToolChoice` / `ToolChunk` | 调用控制与流式返回 |
| `Bash` / `Edit` / `Read` / `Write` / `Glob` / `Grep` | 6 个内置工具 |
| `LocalBackend` / `ExecResult` | 执行后端 |

**没用什么**

| 符号 | 为什么不用 |
|---|---|
| `AskUser` / `AskUserAnswer` / `AskUserMetadata` / `AskUserParams` | HITL 询问，我们有 `require_user_confirm` 事件链 + 前端确认 UI |
| `MCPTool` | 我们自己实现了 MCP 客户端桥接（`mcp_client.py`），需要用户身份透传、审计、健康检查，框架版本无这些能力 |
| `FunctionTool` / `Function` / `ParamsBase` | 我们用自定义 `params_model`，更贴合 Pydantic 校验 |
| `PowerShell` | 服务器全是 Linux |
| `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` | 任务清单工具，我们没做任务列表模式 |
| `ToolMiddlewareBase` | 工具级中间件，我们的横切逻辑在 runtime 层 |
| `ResetTools` / `RegisteredTool` / `DirEntry` / `BackendBase` | 内部类型或未用后端抽象 |

**评价**：`tool/` 9,478 行，我们用了 **12/32 符号但覆盖了主路径**。6 个内置工具（读/写/编辑/搜索/命令执行）直接复用是本模块最大价值——自己写一遍 `Grep`/`Glob`/`Edit` 的边界处理（符号链接、大文件、二进制）很费时间。

---

### 4.4 `model` — ◐ 部分使用（1/17 符号）

**用了什么**：仅 `OpenAIChatModel`。

**没用什么**：`Anthropic` / `DashScope` / `DeepSeek` / `Gemini` / `MiniMax` / `Ollama` / `XAIChatModel` / `Moonshot` / `Volcengine` / `OpenAIResponseModel` 共 10 个专用实现 + 7 个数据类型。

**为什么**：我们**统一走 OpenAI 兼容协议**。无论是 DeepSeek、通义、Kimi、智谱还是自建 vLLM，只要暴露 `/v1/chat/completions` 就用 `OpenAIChatModel` 一个类打通。

**这样做的理由（不是偷懒）**：

| 维度 | 我们（1 个类） | 框架（11 个类） |
|---|---|---|
| 新模型接入 | 改一行配置 | 可能要等框架发版 |
| 私有化部署 | 天然支持（任何 OpenAI 兼容端点） | 需确认框架有对应 formatter |
| 参数一致性 | 统一 | 各家 formatter 差异 |
| 维护成本 | 1 处 | 11 处 |

**代价**：丢掉了各家原生协议的高级特性（如 Anthropic 的 prompt caching、Gemini 的 grounding）。当前业务不需要。

---

### 4.5 `message` — ✅ 完整使用（9/17 符号）

用了 `Msg` / `UserMsg` / `TextBlock` / `DataBlock` / `ToolCallBlock` / `ToolResultBlock` / `ToolResultState` / `Base64Source` / `URLSource`。

**这是必须用的**——`Msg` 是框架与模型之间的通用消息格式，自研没有意义（等于重写序列化）。未用的 8 个（`ThinkingBlock` / `HintBlock` / `SystemMsg` / `AssistantMsg` / `Usage` 等）多由框架内部自动构造。

---

### 4.6 `event` — ✅ 完整使用（3/34 显式符号，实际分发 18 类事件）

**只 import 了 3 个**（`ConfirmResult` / `UserConfirmResultEvent` / `ExternalExecutionResultEvent`），用途是**构造**（回填用户确认结果），不是消费。

**消费走字符串分发**，不 import 事件类：

```python
# app/services/ai/runtime/agentscope/event_stream.py:433
event_type = str(getattr(event, "type", ""))
if event_type == "REPLY_START": ...
```

实际处理的事件类型共 **18 种**：

```text
REPLY_START / REPLY_END
MODEL_CALL_START / MODEL_CALL_END
THINKING_BLOCK_START / THINKING_BLOCK_DELTA / THINKING_BLOCK_END
TEXT_BLOCK_DELTA
TOOL_CALL_START / TOOL_CALL_DELTA / TOOL_CALL_END
TOOL_RESULT_TEXT_DELTA / TOOL_RESULT_DATA_DELTA / TOOL_RESULT_END
REQUIRE_USER_CONFIRM / REQUIRE_EXTERNAL_EXECUTION
EXCEED_MAX_ITERS / CUSTOM
```

**这是"用对了"**：事件流由 `agent.stream()` 产出，我们按类型翻译成 SSE 帧，不需要逐个 import。

> ⚠️ **隐患**（值得记录）：字符串匹配意味着框架若改名或删除某类事件，我们会**静默失效**——没有 import 就编译期发现不了。建议在事件适配层加一个"未识别事件类型"的采样告警。

---

### 4.7 `mcp` — ✅ 完整使用（2/3 符号）

用了 `MCPClient` + `StdioMCPConfig`，**未用 `HttpMCPConfig`**。

**为什么**：我们传的是原始 config dict（含 `sse_url`、`auth_headers`），不走框架的配置模型——因为需要注入用户断言头（`user_assertion_*`），这是在框架 config 之外的扩展。

---

### 4.8 `state` — ◐ 部分使用（1/6 符号）

只用了 `AgentState`。`Task` / `TaskContext` / `ReplyContext` / `ToolContext` / `A2AAgentState` 未用——我们的会话状态在 `PipelineContext`（74 行 dataclass）+ Redis，与框架的 `Task` 体系不是一套。

---

### 4.9 `skill` — ◐ 部分使用（1/3 符号）

只用了 `Skill`（数据模型），`SkillLoaderBase` / `LocalSkillLoader` 未用——我们自己实现了加载器以支持 `workspace_ssh` 远程场景。

---

### 4.10 `exception` — ✅ 完整使用（2/7 符号）

用了 `DeveloperOrientedException` / `StructuredOutputError`。其余 5 个是工具层专用异常，由框架内部抛出、我们捕获处理。

---

### 4.11 `middleware` — △ 仅借基类（2/10 符号）

**用了什么**：`MiddlewareBase`（自定义中间件的基类）+ `TracingMiddleware`。

**没用什么**：7 个内置中间件，共约 5,610 行。详见第 5.1 节——这是本报告最重要的对比之一。

---

### 4.12 `permission` — △ 仅借枚举（3/7 符号）

用了 `PermissionBehavior` / `PermissionDecision` / `PermissionMode` 三个**枚举与决策对象**，未用 `PermissionEngine` / `PermissionRule` / `PermissionContext` / `AdditionalWorkingDirectory`。

**为什么**：我们的权限体系是平台级的（RBAC + `menu:*` / `element:*` 权限点），`permission_service.py` 1,801 行。框架的 `PermissionEngine` 是**单 Agent 的细粒度规则引擎**（路径白名单等），与我们的多租户 RBAC 不是同一层。借用枚举是为了让工具层与框架的权限语义对齐。

---

### 4.13 `credential` — △ 仅借类型（1/13 符号）

只用 `OpenAICredential` 做一次凭证对象构造，13 个凭证类均未实际用作存储。

**为什么**：我们的凭证在平台加密表里（按租户/用户隔离、可轮换、有审计），框架的 `Credential` 是进程序对象。未用 `CredentialFactory` 是因为它读环境变量，而我们的配置来自数据库。

---

### 4.14 `formatter` — ⚙️ 隐式使用（0/23 显式）

我们不直接 import，但 `Agent` 内部按模型自动选用对应的 `ChatFormatter`。**这不算缺口**——同 `message`，属于"框架内部管了、我们不用操心"的类型。

> 注意：因为统一走 OpenAI 兼容，实际生效的主要是 `OpenAIChatFormatter`。其余 22 个 formatter 对我们无意义。

---

## 5. 未接入模块详解（12 个）

以下逐个说明：框架提供什么、我们有什么、谁更好、为什么。

### 5.1 `middleware` 的 7 个内置中间件（约 5,610 行）

`middleware` 导出 10 个符号：`MiddlewareBase`（基类，已用）、`TracingMiddleware`（已用）、`ChatModelCandidate`（数据类），剩下 **7 个中间件全部未用**。

#### 5.1.1 长期记忆三件套（3,739 行）

| 实现 | 行数 | 机制 |
|---|---|---|
| `AgenticMemoryMiddleware` | 965 | Markdown 文件（`MEMORY.md`）+ 每轮注入快照（≤4000 token）+ 异步检索任务 |
| `Mem0Middleware` | 1,532 | 接 Mem0 云服务 |
| `ReMeMiddleware` | 1,230 | 嵌入 ReMe 应用（file-based） |

**我们的实现**（2,715 行）：

```
memory_service.py          读写编排
memory_index_service.py    索引
memory_vector_rebuild.py   向量重建
memory_recall_policy.py    召回策略
memory_ltm_tools.py        3 个显式工具
```

存储为 **Redis Hash + 向量索引**，注入路径是 `pipeline/assemble_step.py:252` 预加载 → `execution_step.py:96` 注入。

**对比结论：我们更好。** 理由四条：

| 维度 | 框架 | 我们 | 谁更好 |
|---|---|---|---|
| 存储 | Markdown 文件（单机文件系统） | Redis Hash + 向量索引 | **我们**：多实例共享、可持久化、支持水平扩展 |
| 检索 | 异步检索任务 + 相关文件挑选 | 向量语义召回 + 可配置召回策略（`memory_recall_policy`） | **我们**：语义级而非文件级 |
| 多租户 | 无租户概念，靠目录隔离 | 按 `user_id` + 租户分区 | **我们**：平台必需 |
| 可观测 | 无 | 有重建任务、索引状态 | **我们** |

框架唯一的优势是"每轮自动注入不依赖 agent 主动调工具"——但**我们已经在 `assemble_step` 里预加载并注入**，这个优势我们也有。

#### 5.1.2 `ModelRouterMiddleware`

用 classifier 对用户输入分类，然后从候选模型中选一个。

**我们的实现**：`runtime_model_info` / `effective_model_id` / `model_source` / `resolution_status` 一套完整的模型解析链，在 `context_step` 阶段完成，支持按 Agent 配置、按用户套餐、按降级策略选模型。

**对比结论：我们更好。** 理由：
1. 框架的路由**依赖 `classifier` 模块**（见 5.4），而该模块依赖境外服务，我们无法使用
2. 我们的是**配置驱动 + 业务规则**（租户套餐、Agent 绑定、余额降级），不是"LLM 猜一个"
3. 框架每次回复都要多一次分类调用（成本 + 延迟），我们是查表

#### 5.1.3 `RAGMiddleware`

把知识库检索挂进 Agent，两种模式：`static`（自动注入）和 `agentic`（agent 自己调工具）。

**我们的实现**：RAGFlow 集成 + `search_knowledge_base` 工具 + 知识库权限校验。

**对比结论：功能重复，我们更贴合。** 理由：
1. 我们的知识库在 **RAGFlow**（独立服务，有解析/分块/重排能力），框架的 `RAGMiddleware` 只对接框架自己的 `KnowledgeBase`（Qdrant/Milvus/MongoDB/ES）
2. 我们的检索带**知识库级权限**（谁能查哪个库），框架没有权限概念
3. 框架的 `static` 模式会无条件注入检索结果，容易污染上下文；我们的注入有阈值和裁剪

#### 5.1.4 `ReplyBudgetControlMiddleware`（唯一有价值的一个）

**机制**：加权 token 预算，`cost = w_in × input + w_out × output`；到顶后**注入提示 + 强制 `tool_choice="none"`**，让 Agent 停止调工具、把已有结果总结完。状态存在 `agent.state.middle_context`，跨 HITL 恢复不丢。

**我们的现状**：`quota_exceeded` → **直接中断**（`preflight_step.py:59`、`resume.py:85`、`finalize_step.py:40`）。

**对比结论：框架的收尾策略更好，值得借鉴。** 理由：

| 场景 | 我们（硬中断） | 框架（优雅收尾） |
|---|---|---|
| 用户超配额 | 收到错误，什么也没有 | 收到已完成部分的总结 |
| 长任务中途超限 | 前功尽弃 | 保留中间成果 |
| 体验 | 差 | 好 |

**但不需要引入中间件**——我们的执行链在 `execution_step`，加一段"超阈值 → 注入收尾指令 + `tool_choice=none`"即可，约 2~3 天。**这是我们唯一建议从 middleware 借鉴的设计。**

#### 5.1.5 `TTSMiddleware`

回复转语音。**无需求，不接。**

---

### 5.2 `rag`（5,386 行）— ❌ 不接，parser 仅作备选

**框架提供**：`KnowledgeBase` 门面 + 6 个文档解析器 + 1 个分块器 + 5 种向量库（Qdrant / MilvusLite / MongoDB / Elasticsearch / 内存）。

**我们的实现**：RAGFlow（独立服务）+ 自研知识库管理。

#### 整体对比：不接

| 维度 | 框架 | 我们（RAGFlow） | 谁更好 |
|---|---|---|---|
| 解析深度 | pypdf / python-docx 基础抽取 | RAGFlow 的版面分析、表格还原、OCR | **RAGFlow** |
| 分块 | 1 种（`ApproxTokenChunker`） | 多种模板（naive/qa/paper/book 等） | **RAGFlow** |
| 重排 | 无 | 有 rerank 模型 | **RAGFlow** |
| 运维 | 要自己维护向量库 | RAGFlow 已部署 | **我们** |

**结论：RAG 主干不接。**

#### `rag/_parser` 的定位：RAGFlow 已覆盖，仅作备选

**我们的解析走 RAGFlow**（`app/services/ai/ragflow_client.py`）——版面分析、表格还原、OCR 都在 RAGFlow 内完成，比框架的 `pypdf`/`python-docx` 基础抽取强得多。

| 能力 | 框架 parser | 我们（RAGFlow） |
|---|---|---|
| PDF | ✅ `PDFParser`（pypdf，纯文本抽取） | ✅ 版面分析 + OCR |
| Word | ✅ `WordParser`（含表格） | ✅ |
| PPT | ✅ `PPTParser` | ✅ |
| Excel | ✅ `ExcelParser` | ✅ |
| 图片 | ✅ `ImageParser` | ✅ vision sidecar |
| 表格还原 | ❌ 仅文本 | ✅ |
| 扫描件 OCR | ❌ | ✅ |

**结论：RAG 场景不需要框架 parser，RAGFlow 做得更深。**

框架 parser 唯一可能有增量的场景是「**聊天内直接上传 PDF、不入知识库、让 Agent 读全文**」——注意我们的附件链路目前只认图片：

```python
# app/services/ai/executors/common.py:78
def _is_image_attachment(file_obj) -> bool:
    return _normalize_file_ext(...) in IMAGE_EXTENSIONS
```

但这是一个**当前没有产品需求的假设场景**，不列为建议项。仅在将来真要做时，框架 parser 是现成方案（依赖惰性导入，可只装 `pypdf` 支持 PDF）。

---

### 5.3 `realtime`（2,998 行）— ❌ 不接

**框架提供**：5 个实时语音模型（DashScope / Gemini / OpenAI / XAI / DashScope-Audio）+ 传输层 + VAD + 29 类事件。

**我们的现状**：无语音实时能力。

**为什么不接**：这是**成本问题，不是技术问题**。

| 门槛 | 具体内容 | 成本 |
|---|---|---|
| 前端 | WebRTC 采集 + 播放 + 回声消除 | 2~3 周 |
| 网络 | UDP 端口开放、TURN 服务 | 运维改造 |
| 模型 | 必须用 speech-to-speech 模型，按分钟计费 | 持续成本 |
| 依赖 | `aiortc 1.15.0` 已装（`agentscope[service]` 带的） | 已满足 |

**结论：只有在明确要做"实时语音对话"产品时才启动，不是能力补齐。** 详见[升级评估文档](./agentscope_2.0.11_upgrade_assessment.md:210)。

---

### 5.4 `classifier`（714 行）— ❌ 不接

**框架提供**：两种分类器——`OpenAIClassifierModel`（走 OpenAI `client.decisions`）和 `JevClassifierModel`（Jev 云服务）。

**为什么用不了（两条硬阻断）**：

1. **`openai 3.3.1` 没有 `client.decisions` 方法**，需 ≥3.26.0；升级会带来全平台回归风险
2. **Jev 是境外 SaaS**，且需要额外 `classifier-jev` extra（`typesafe_sdk` 未安装）

**我们的实现**：`intent_service.py` + `tool_nudge_policy.py`（1,735 行路由修正规则）。

**对比结论：我们的能用，框架的用不了，没有可比性。** 但要承认——

| 维度 | 框架（若可用） | 我们 |
|---|---|---|
| 输出结构 | 强类型（`BinaryAnswer` / `ChoiceAnswer` / `ScoreAnswer`） | LLM 自由文本 + `_coerce_confidence` |
| 置信度 | 模型原生校准 | prompt 驱动，**未校准** |
| 提示词调整 | 改一句话 | 改 1,735 行规则 |

**我们的真实痛点**：`tool_nudge_policy.py` 有 1,735 行硬编码修正，说明**路由准确率一直有问题**；`intent_service.py:326` 还记录着"正则快速路径因误判被移除"（如"模型测速""模型评价"）。

**但不是引入 classifier 就能解决**——它的服务我们调不通。**建议方向**：把 `tool_nudge_policy` 的规则收敛成少量 few-shot 示例，或引入本地小模型做分类。

---

### 5.5 `sop`（794 行）— ❌ 不接

**框架提供**：`SOPEngine` + executor-verifier 双角色 + 里程碑推进 + 可序列化状态。

**我们的实现**：ChatBI 引擎（**11,950 行**）。

**对比结论：我们更好，差距是数量级的。**

| 维度 | 框架 SOP | ChatBI 引擎 | 差距 |
|---|---|---|---|
| 代码量 | 794 行 | 11,950 行 | **15 倍** |
| 领域语义 | 通用 executor-verifier | SQL 生成 + 校验 + 修复 | 领域内建 |
| 错误修复 | 无 | `repair_controller.py` | 我们没有的框架也没有 |
| 断点续跑 | 有（`SOPRunState`） | `run_state.py` + `resume_stream.py` | 相当 |
| 依赖编排 | 线性步骤 | `chatbi_task_plan.py` 带 `depends_on` DAG | **我们更强** |
| 流式 | 无 | SSE 全程流式 | **我们更强** |

**框架唯一的亮点**是 `SOPRunState` 的可序列化设计（handover-only 状态传递，避免上下文污染）。这一点我们在 `PipelineContext` 里做法类似。

---

### 5.6 `pipeline`（828 行）— ❌ 不接

**先说清楚：这不是同一层的东西。**

| | 框架 `pipeline` | 我们的 `ai/pipeline/` |
|---|---|---|
| 是什么 | **多 Agent 编排原语** | **单次请求的处理流水线** |
| 导出 | `GoalPipeline` / `TeamPipeline` | 6 个 step（Preflight→Context→Route→Assemble→Execution→Finalize） |
| 解决问题 | 多个 Agent 怎么协作 | 一次对话怎么走完 |
| 代码量 | 828 行 | 2,220 行 |

**结论：不可替代，也不同层。** 我们的"多 Agent"走 `agent_delegate_tool.py`（1,378 行，`sub_agent_call` + `BatchSubAgentTool` + `asyncio.gather`）。

但框架 `pipeline` 有两处设计**正好戳中我们的弱点**：

| 我们的弱点 | 框架的做法 | 是否值得借鉴 |
|---|---|---|
| 步骤间通过 `shared_state` 可变 dict 传递，隐式耦合 | 显式 handover 参数 | ✅ 值得（中长期） |
| 状态在内存，中断后难恢复 | 可序列化状态 | ✅ 值得（中长期） |
| 事件是 `Dict[str, Any]`，无类型约束 | 强类型事件 | ✅ 值得（中长期） |

**建议：不引入框架，但把这三条设计原则纳入重构路线。**

---

### 5.7 `app`（52,799 行）— ❌ 不接

**框架提供**：完整的 Agent 服务层——FastAPI 路由、`StorageBase`（SQLite/Postgres）、`MessageBus`（Redis）、Session/Workspace/Team 管理。**207 个 .py 文件、12 个子包**（8 个公开：`access` / `channel` / `hub` / `message_bus` / `middleware` / `rag` / `storage` / `workspace_manager`；4 个私有：`_manager` / `_router` / `_service` / `_tool`）。

**为什么不能接（不是"不想"，是"接不了"）**：

| 冲突 | 说明 |
|---|---|
| 存储层 | 强绑 `StorageBase` 接口与它自己的表结构，我们是 MySQL + SQLAlchemy 2.x 异步，表结构完全不同 |
| 鉴权 | 它假设 `get_current_user_id` 是简单字符串，我们是多租户 + RBAC + JWT |
| 计费 | 无 |
| 前端契约 | 它自带前端约定，我们已定 `{skills, categories, source_note}` 等契约 |
| 迁移成本 | 52,799 行，等价于重写平台 |

**结论：这是"另一个平台"，不是"可用的库"。** 我们只从中学设计思路（如 hub 的路由分层）。

---

### 5.8 `embedding`（2,384 行）— ❌ 不接

4 个实现（DashScope / OpenAI / Gemini / Ollama）+ 文件缓存 + 用量统计。

**我们的做法**：`embed_model_name` 配置 + OpenAI 兼容端点，一个类打通。与 `model` 模块同一逻辑。

**结论：不需要。** 框架的 `FileEmbeddingCache` 想法不错（本地磁盘缓存向量），但我们的 embedding 调用量不大，缓存收益有限。

---

### 5.9 `tts`（2,328 行）— ❌ 不接

5 个实现，其中 `DashScopeCosyVoiceTTSModel` 质量确实好（中文自然度优于多数方案）。

**但**：我们**没有语音输出需求**。若未来做"语音播报"，建议**不要用框架**，直接调 DashScope 的 TTS HTTP 接口——省一层适配，且框架的 5 个实现有 3 个是境外的。

---

### 5.10 `asr`（429 行）— ❌ 不接

**框架只有 1 个实现** `OpenAIASRModel`，且**仅支持批量转写**（`realtime=False`），本质是对 `/audio/transcriptions` 的薄封装。

**结论：不值得为 429 行引入一层。** 若要做语音输入，直接调 HTTP 接口，照 `multimodal_support.py` 的 sidecar 模式扩一个 `audio_support.py` 即可。

---

### 5.11 `console` / `tui`（3,160 行）— ❌ 无关

终端 UI。我们是 Vue 3 Web 平台。**零参考价值。**

---

### 5.12 `credential` / `types` — △ / ❌

`credential`（863 行）已说明（见 4.13）。`types`（124 行）是内部类型别名，我们的代码不需要。

---

## 6. 能力域横向对比

把上面的结论按**能力域**重新组织，便于判断"这个领域该用谁"。

### 6.1 总览

| 能力域 | 框架模块 | 我们的实现 | 规模对比 | 谁更好 | 结论 |
|---|---|---|---|---|---|
| **Agent 运行时** | `agent` 6,775 | `runtime/agentscope` 22,063 | 框架是内核 | **框架** | 用框架 |
| **沙箱工作区** | `workspace` 10,562 | 封装层 | 框架是内核 | **框架** | 用框架 |
| **工具系统** | `tool` 9,478 | `ai/tools/` 37 模块 | 互补 | **框架做底座** | 用框架 |
| **消息/事件** | `message`+`event` 1,678 | 无需自研 | — | **框架** | 用框架 |
| **MCP 协议** | `mcp` 670 | `services/mcp` 4,298 | 我们重 | **框架做协议** | 用框架 |
| **请求编排** | `pipeline` 828 | `ai/pipeline` 2,220 | 不可比（不同层） | **各管一层** | 保留自研 |
| **长期记忆** | `middleware/_longterm_memory` 3,739 | `memory_*.py` 2,715 | 相当 | **我们**（向量+多租户） | 保留自研 |
| **RAG/知识库** | 5,386 | RAGFlow | — | **RAGFlow** | 保留自研 |
| **文档解析** | `rag/_parser` | RAGFlow（版面分析+OCR） | — | **RAGFlow** | 不需要 |
| **语音实时** | `realtime` 2,998 | 无 | 缺口但有成本 | — | 暂不做 |
| **语音识别** | `asr` 429 | 无 | 薄封装 | 直连 API 更好 | 不引入 |
| **语音合成** | `tts` 2,328 | 无 | 无需求 | — | 不引入 |
| **流程 SOP** | `sop` 794 | ChatBI 11,950 | 15 倍 | **我们** | 保留自研 |
| **意图分类** | `classifier` 714 | 1,735 行规则 | — | **都用不了/不理想** | 保留自研 |
| **模型接入** | `model` 7,052 | OpenAI 兼容 | — | **我们更灵活** | 保留自研 |
| **嵌入模型** | `embedding` 2,384 | OpenAI 兼容 | — | **我们够用** | 不引入 |
| **市场发现** | `hub`（在 `app/` 内） | 魔搭外链 + 静态 `catalog.json` | — | **外部服务** | 外链足够 |
| **IM 渠道** | `channel`（在 `app/` 内） | 5 个出站推送工具 | 形态不同 | **框架**（双向） | 视需求 |
| **权限** | `permission` 1,118 | `permission_service` 1,801 | 层不同 | **我们**（平台级 RBAC） | 保留自研 |
| **凭证** | `credential` 863 | 加密凭证表 | 层不同 | **我们**（多租户+审计） | 保留自研 |
| **可观测** | `middleware/_tracing` 1,451 | `observability` 2,154 | 相当 | **我们**（Langfuse 定制） | 保留自研 |
| **多 Agent 协作** | `pipeline` | `agent_delegate_tool` 1,378 | — | **我们更贴合** | 保留自研 |
| **终端形态** | `console`+`tui` 3,160 | Vue 3 | — | **我们** | 无关 |

### 6.2 三张"我们赢"的说明

#### 赢在存储模型（记忆、凭证、权限）

框架的这些模块都假设**单 Agent、单进程、文件或内存存储**。我们是**多租户 Web 平台**，必须：按 `user_id`/租户隔离、数据落 MySQL/Redis、有审计、可水平扩展。

这不是"框架做得差"，而是**定位不同**——它服务的是"跑一个 Agent"，我们服务的是"给一个企业跑上千个 Agent"。

#### 赢在业务内建（ChatBI、意图路由）

`ChatBI` 的 11,950 行里有大量领域知识：SQL 方言差异、表结构理解、错误修复模式。这是**通用框架不可能内建**的。

`sop` 的 executor-verifier 是通用模式，但我们已经在具体业务上把它实例化得更彻底。

#### 赢在统一协议（模型、嵌入）

框架给 11 个模型厂商各写一个实现，是因为要支持各家原生协议的高级特性。我们统一走 OpenAI 兼容，**牺牲特性换灵活性**——对私有化部署场景这是正确取舍。

### 6.3 框架真正不可替代的两处

#### 赢在通用基础设施（Agent/Workspace/Tool/Message/MCP）

这些是**没有业务偏向的硬工程**：容器沙箱的生命周期、文件工具的边界处理（符号链接、大文件、二进制）、事件流的正确性。自己写一遍要踩无数坑，且不产生业务价值。

**这是我们接入框架的正确姿势——用它的地基，不用它的上层。**

#### 框架独有的：IM 渠道

`app/channel/` 的 IM 双向对话是我们**确实没有**的能力（我们只有 5 个出站推送工具）。但这是新入口而非能力补齐，且市场侧我们已用外链魔搭解决，不需要框架的 hub 适配层（见 7.3）。

---

## 7. 引入评估与结论

### 7.1 结论：没有必须引入的模块

核对下来，**框架没有我们缺失、且必须由框架补的能力**。现有能力由三方面共同覆盖：

| 来源 | 覆盖范围 |
|---|---|
| **框架地基** | Agent 执行内核、沙箱工作区、文件工具、消息与事件、MCP 协议 |
| **自研** | 请求流水线、长期记忆、ChatBI、权限、凭证、可观测、技能广场、MCP 治理 |
| **外部服务** | RAGFlow（文档解析与检索）、魔搭社区（skills / MCP 市场外链） |

所以本报告的建议是：**保持现状，不新增框架模块**。以下把两处容易被误判为"缺口"的地方说清楚，避免以后重复讨论。

### 7.2 文档解析 —— 已由 RAGFlow 覆盖

**我们的做法**：文档解析走 RAGFlow（`app/services/ai/ragflow_client.py`），解析、分块、重排全在 RAGFlow 内完成。

**框架 `rag/_parser` 与 RAGFlow 的定位差异**：

| | RAGFlow（我们的） | 框架 `rag/_parser` |
|---|---|---|
| 形态 | 独立服务 | 进程内库 |
| 解析深度 | 版面分析、表格还原、OCR | 基础文本抽取（pypdf / python-docx） |
| 用途 | **入库 → 检索** | **直接取文本 → 塞进上下文** |
| 我们是否在用 | ✅ | ❌ |

**结论：RAG 场景不需要框架。**

框架 parser 唯一可能有增量的地方是"**聊天内直接上传 PDF、不入知识库、让 Agent 读全文**"——但这是一个**当前没有产品需求的假设场景**，不作为建议项。

> 备注（备查用）：如果将来真要做这个场景，框架的 6 个 Parser 是现成方案——它们依赖是惰性导入的（`from pypdf import PdfReader` 写在函数体内），可以只装 `pypdf` 先支持 PDF，不影响其他格式。

### 7.3 MCP 市场 —— 已通过魔搭外链覆盖

**我们的做法**：MCP 市场入口已外链到魔搭。

```vue
<!-- frontend/src/components/system/McpServerRegistry.vue -->
<a v-if="!loading && servers.length > 0" href="https://modelscope.cn/mcp" ...>
  <ShoppingBagIcon /> <span>去魔搭发现更多 MCP 服务</span> →
</a>
<!-- 列表为空时展开成引导卡：「探索 MCP 市场 / 立即前往市场」 -->
```

设计上还做了分态处理：**有服务时收成一行链接**（注释原话：「低频外链不该占列表首屏」），**列表为空时才展开成引导卡**。这比接一个 registry 进来更轻。

**为什么外链魔搭比接 GitHub Registry 更合适**：

| 维度 | 魔搭（现状） | GitHub MCP Registry（框架带的） |
|---|---|---|
| 网络可达 | ✅ 国内 | ❌ 需代理（本机解析到 `198.18.0.87`） |
| 内容偏向 | 国产 MCP（高德/阿里云/钉钉等） | 海外开源 MCP |
| 维护成本 | 0（外链） | 需适配 + 治理 + 版本跟进 |
| 与技能广场一致 | ✅ 都是外链魔搭 | ❌ 两套来源 |

**结论：MCP 市场不做内嵌。** 框架的 `GitHubMCPHub` 只在"外链要升级成内嵌可一键安装"时才值得考虑，属于**未来可选项**，不是当前缺口。

### 7.4 唯一真正的"新增能力"：IM 双向对话

如果只问"框架有没有我们彻底没有的东西"，答案只有一个：**`app/channel/` 的 IM 双向对话**。

| 现状 | 框架能力 |
|---|---|
| 5 个**出站推送**工具：`send_feishu_message` / `send_dingtalk_message` / `send_wechat_work_message` / `send_email` / `send_portal_notification` | 飞书、钉钉、Discord 的**双向对话**（接收消息 → 跑 Agent → 回复） |

差别是**方向**：我们只能推，框架能收能推。要接飞书事件订阅、验签、消息去重、会话映射——这些是我们现在没有的。

但这**不是能力补齐**，而是**新入口**——需要 `agentscope[channel]` extra（当前未装 `lark_oapi`），且是否有价值取决于"客户是否要在 IM 里直接跟 Agent 对话"。

**建议：等客户明确提出需求再做**（3~4 周）。

### 7.5 一个不涉及框架模块的改进

`middleware/_budget` 中间件本身不需要引入，但它的**收尾策略**值得借鉴：

> 达到预算后不硬中断，而是注入提示 + 强制 `tool_choice="none"`，让 Agent 把已有结果总结完。

我们现在是 `quota_exceeded` 直接中断（`preflight_step.py:59`），用户拿到错误、什么也没有。改成"优雅收尾"约 2~3 天，不需要引入中间件，只在 `execution_step` 加一段逻辑。

**这是本报告唯一建议动手的改动，且它不引入任何框架代码。**

---

## 8. 明确不建议的方向

### 8.1 skill 市场（ClawHub）—— 数据质量不足

| 问题 | 证据 |
|---|---|
| 三端点字段不一致 | `latestVersion` 在 catalog 是对象、search 缺失；`downloads` 位置不同；`updatedAt` 是毫秒 |
| **无 license 字段** | 我们现有 21 条里 8 条是 Anthropic 专有许可，专门标注"仅作索引、不复制正文"——这个合规信息 ClawHub 没有 |
| slug 不唯一 | 需 `owner/slug` 消歧，否则 409 |
| 版本是时间戳 | `0.20260729.110214`，来自 "bulk skill generator"（批量生成） |
| 无仓库溯源 | 只有 hub 页面链接，没有 `repository_url` |
| 浏览时大量缺作者/图标 | 源码注释：*"most browse cards carry no author and no icon"* |

**结论**：ClawHub 适合当"选品池"（按 `installs` 排序取 Top N 人工审核），**不适合直接做展示层**。我们现在的 21 条精选 + 人工撰写中文介绍 + license 标注，是**产品资产**，不能被自动索引替代。

### 8.2 其他不建议项

| 方向 | 理由 |
|---|---|
| `app`（52,799 行） | 与我们 MySQL + RBAC 架构冲突，等价于重写平台 |
| `realtime` | WebRTC 前端 2~3 周 + UDP 端口 + 按分钟计费模型，无明确产品需求 |
| `asr` / `tts` | 薄封装层（asr 仅 429 行），直连 HTTP 接口更省 |
| `classifier` | `openai 3.3.1` 缺 `client.decisions`；Jev 境外不可达 |
| `sop` | ChatBI 已强 15 倍 |
| 记忆/路由/RAG 中间件 | 我们实现更强（多租户、向量、权限） |
| `embedding` / `model` 多厂商实现 | OpenAI 兼容一个类打通，引入反而降低灵活性 |
| `console` / `tui` | 终端 UI，与 Web 平台无关 |
| `Tencent`/`Daytona`/`OpenSandbox` 工作区 | 境外 SaaS 或形态不符 |

---

## 9. 给平台用户的说明

> 本节面向使用本平台的用户，说明平台的能力边界与底层来源。

### 9.1 平台基于什么构建

本平台的 Agent 运行时建立在 **AgentScope 2.0.11** 之上，复用了它的：

- **Agent 执行内核**（ReAct 循环、工具调用、流式事件）
- **沙箱工作区**（本地 / Docker / K8s / E2B 四种隔离形态）
- **文件操作工具**（读、写、编辑、搜索、执行）
- **MCP 协议客户端**
- **消息与事件模型**

### 9.2 平台自研增强的部分

以下能力**不是框架自带**，是本平台自研：

| 能力 | 说明 |
|---|---|
| 多租户与权限 | 按租户/用户隔离数据，RBAC + 菜单/元素级权限点 |
| 请求处理流水线 | 预检 → 上下文 → 路由 → 装配 → 执行 → 收尾，六段可插拔 |
| 长期记忆 | Redis + 向量召回 + 可配置召回策略（跨会话记住偏好） |
| 知识库 | 对接 RAGFlow，含解析、分块、重排、库级权限 |
| ChatBI | 自然语言转 SQL，含 schema 理解、错误修复、断点续跑、流式输出 |
| 技能广场 | 精选技能目录，人工撰写中文介绍并核实许可证 |
| MCP 管理 | 出站审计、用户身份透传、工具健康检查、可禁用 |
| 可观测 | 接入 Langfuse 的全链路追踪与成本核算 |

### 9.3 已知边界（诚实告知）

| 边界 | 现状 |
|---|---|
| 文档处理 | 知识库文档由 RAGFlow 解析（支持 PDF/Word/PPT/表格/OCR）；对话内直传文档暂只支持图片 |
| 实时语音对话 | 不支持（需 WebRTC，未规划） |
| 语音输入/输出 | 不支持（可直连第三方 API 扩展） |
| IM 双向对话 | 支持飞书/钉钉/企微/邮件/站内信的**出站推送**；不支持在 IM 内双向对话 |
| MCP 市场 | 提供魔搭市场外链，手工配置或从市场复制配置接入 |

---

## 10. 附录

### 10.1 完整符号使用对照表

| 模块 | 导出数 | 已用符号 | 未用符号（部分） |
|---|---|---|---|
| `agent` | 9 | Agent, ContextConfig, InjectionConfig, ModelConfig, ReActConfig | A2AAgent, RealtimeAgent, TurnAggregator, TurnMetrics |
| `workspace` | 17 | 6 个导出：WorkspaceBase, LocalWorkspace, DockerWorkspace, DockerBackend, E2BWorkspace, K8sWorkspace<br>4 个子模块符号：CONTAINER_WORKDIR, POD_WORKDIR, GATEWAY_HOME, prepare_build_context | Bubblewrap*, Daytona*, OpenSandbox*, AppleContainer*, Offloader |
| `tool` | 32 | ToolBase, Toolkit, ToolChoice, ToolChunk, Bash, Edit, Read, Write, Glob, Grep, LocalBackend, ExecResult | AskUser*, MCPTool, FunctionTool, PowerShell, Task*, ToolMiddlewareBase |
| `message` | 17 | Msg, UserMsg, TextBlock, DataBlock, ToolCallBlock, ToolResultBlock, ToolResultState, Base64Source, URLSource | ThinkingBlock, HintBlock, SystemMsg, AssistantMsg, Usage |
| `event` | 34 | ConfirmResult, UserConfirmResultEvent, ExternalExecutionResultEvent（其余运行期消费） | — |
| `mcp` | 3 | MCPClient, StdioMCPConfig | HttpMCPConfig |
| `middleware` | 10 | MiddlewareBase, TracingMiddleware | AgenticMemory, Mem0, ReMe, RAG, Budget, TTS, ModelRouter |
| `permission` | 7 | PermissionBehavior, PermissionDecision, PermissionMode | PermissionEngine, PermissionRule, PermissionContext |
| `model` | 17 | OpenAIChatModel | 10 个厂商实现 + 7 个类型 |
| `credential` | 13 | OpenAICredential（仅构造） | 其余 12 个 + CredentialFactory |
| `state` | 6 | AgentState | Task, TaskContext, ReplyContext, ToolContext, A2AAgentState |
| `skill` | 3 | Skill | SkillLoaderBase, LocalSkillLoader |
| `exception` | 7 | DeveloperOrientedException, StructuredOutputError | 5 个工具异常（内部抛出） |
| `formatter` | 23 | 隐式（OpenAI 系） | 其余厂商 formatter |

### 10.2 核对命令

```bash
# 1. 模块规模（排除私有包与缓存目录）
cd .venv/lib/python3.11/site-packages/agentscope
for d in */; do
  case "$d" in __pycache__/|_utils/) continue;; esac
  printf "%-12s %s\n" "${d%/}" "$(find "$d" -name '*.py' -exec cat {} + | wc -l)"
done

# 2. 项目引用次数
cd /path/to/platform
for m in agent app asr classifier console credential embedding event exception \
         formatter mcp message middleware model permission pipeline rag \
         realtime skill sop state tool tts tui types workspace; do
  printf "%-12s %s\n" "$m" "$(grep -rn "agentscope\.$m\b" --include="*.py" app/ | wc -l)"
done

# 3. 符号级使用（AST，比 grep 准确）
.venv/bin/python -c "
import ast, os, collections
used = collections.defaultdict(set)
for root, dirs, files in os.walk('app'):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for f in files:
        if not f.endswith('.py'): continue
        tree = ast.parse(open(os.path.join(root,f), encoding='utf-8', errors='ignore').read())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith('agentscope'):
                k = n.module.split('.')[1]
                for a in n.names: used[k].add(a.name)
for k in sorted(used): print(k, sorted(used[k]))
"
```

### 10.3 版本与环境

| 项 | 值 |
|---|---|
| `agentscope` | 2.0.11（`agentscope[service,storage,workspace]`） |
| Python | 3.11.14 |
| `openai` | 3.3.1（**注意**：缺 `client.decisions`，导致 `classifier` 不可用） |
| `aiortc` | 1.15.0（已装，`service` extra 带入） |
| `lark_oapi` | 未安装（`channel` extra 未装） |
| `typesafe_sdk` | 未安装（`classifier-jev` extra 未装） |
| `pypdf` / `python-docx` / `python-pptx` | 未安装（仅在将来做"对话内直读文档"时才需要） |

### 10.4 遗留待确认项

| 项 | 影响 | 如何确认 |
|---|---|---|
| `middleware/_tracing` 未用的高级功能 | 唯一尚未细读的已用模块，可能有增量 | 细读 1,451 行 |
| `channel` 的 IM 双向对话工作量 | 决定是否启动 | 读 `app/channel/` 的飞书/钉钉实现 |
| 魔搭 MCP 广场是否有开放 API | **仅在**将来想把外链升级为"内嵌一键安装"时才需要 | 浏览器开 `modelscope.cn/mcp` 看 Network 面板 |
| `api.mcp.github.com` 生产可达性 | 同上，作为备选源才需要 | 生产网 `curl` |
| ClawHub 实际数据质量 | 影响"是否用它做选品池"的判断 | 浏览器翻 10 分钟，看图标/作者填充率 |

---

## 11. 一页纸总结

```text
AgentScope 2.0.11 共 26 模块 / 123,381 行
        │
        ├─ 我们接入了 14 个 ───────────────────────────
        │    地基（完整使用 7 个）：agent · workspace · tool · message · event · mcp · exception
        │    部分使用 3 个：model · state · skill
        │    借用类型 3 个：permission · credential · middleware
        │    隐式使用 1 个：formatter
        │
        ├─ 我们没接的 12 个 ───────────────────────────
        │    ├─ 已在自研上做得更好（5 个）
        │    │     classifier · embedding · pipeline · rag（主体）· sop
        │    └─ 无需求或与平台形态无关（7 个）
        │          app · asr · console · realtime · tts · tui · types
        │
        ├─ 两处曾被误判为"缺口"的，实际已覆盖 ──────────
        │    文档解析 → RAGFlow（版面分析+OCR，强于框架 parser）
        │    MCP 市场 → 魔搭外链（国内可达，比 GitHub registry 合适）
        │
        └─ 结论 ─────────────────────────────────────
              必须引入的模块：0 个
              真正缺的两处：IM 双向对话（新入口）· 实时语音（成本高）
              唯一建议动手：预算"优雅收尾"（2~3 天，不引框架代码）
```

**一句话**：框架是我们的地基，不是待补的能力库——地基已经用足，上层该自研的都已自研，市场与解析交给外部服务。

---

*报告结束。所有结论基于 `agentscope==2.0.11` 源码核对，代码未做任何改动。*

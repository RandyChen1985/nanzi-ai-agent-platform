# AgentScope 2.0.6 → 2.0.9 升级评估与验证清单

> 评估日期：2026-09-28（v2.0.9 发布当日）
> 评估基线：`v2.0.6` tag ↔ `v2.0.9` tag，以及 nanzi 本地 `.venv` 实装版 `2.0.7` ↔ `v2.0.9` 源码逐符号比对
> 结论：**兼容，可升级**；升级前须先消除版本漂移（见 §2）

---

## 1. 结论摘要

| 维度 | 结论 |
|---|---|
| 公开 API 兼容性 | ✅ 零删除。v2.0.6 → v2.0.9 全部 `__init__.py` 的 `__all__` 新增 **179 条**、删除 **0 条** |
| nanzi 导入符号 | ✅ 实际 import 的 **42 个符号**在 2.0.9 全部存在，无 ImportError 风险 |
| nanzi 私有 API 依赖 | ✅ 深入引用的 **8 个私有符号**全部存活且签名不变 |
| 破坏性变更 | ⚠️ 仅 2 处**私有**函数新增必填参数，nanzi 均不使用 |
| 升级收益 | ✅ 大量修复精准命中 nanzi 现有链路的真实缺陷 |
| 升级成本 | 🟡 沙箱镜像 tag 全量失效需重建；`1` 处行为变更需决策 |

---

## 2. 前置问题：依赖版本漂移（优先级高于升级本身）

升级前必须先解决这个问题，否则"升级"本身没有意义。

| 事实 | 位置 |
|---|---|
| 原依赖声明无上限：`agentscope[service,storage,workspace]>=2.0.6` | `requirements.txt` |
| 镜像构建直接 `pip install -r requirements.txt`，无 lock 文件 | `docker/Dockerfile` |
| 本地 `.venv` 实装 `2.0.7`（2026-08-26 安装） | `.venv/` |
| v2.0.8 发布于 09-08，v2.0.9 发布于 09-28 | — |

**后果**：镜像重建时间不同，装到的版本就不同（08-26 前 = 2.0.7、09-08 后 = 2.0.8、09-28 后 = 2.0.9），而本地开发环境停留在 2.0.7。**同一份代码在不同环境跑不同运行时，且这种漂移完全静默**——线上问题无法在本地复现。

**已处理**：`requirements.txt` 已改为精确锁定 `agentscope[service,storage,workspace]==2.0.9`。

> 后续任何改版本号的动作，请同步更新本文档。

---

## 3. 变更概览

`v2.0.6..v2.0.9` 共 **207 个非 merge 提交**，582 文件变更，**+74032 / −4828 行**。

| 版本 | 发布日 | 提交数 | 规模 |
|---|---|---|---|
| v2.0.7 | 08-24 | 49 | 291 文件，+25225 |
| v2.0.7.post1 | 08-28 | 补丁 | — |
| v2.0.8 | 09-08 | 56 | 227 文件，+21176 |
| v2.0.9 | 09-28 | 102 | 271 文件，+27913 |

类型分布：**138 fix / 42 feat** / 12 docs / 5 chore / 4 ci / 3 test / 3 refactor —— 以修复为主的成熟迭代。

**新增顶层模块**（2.0.6 完全没有）：`realtime/`（语音实时）、`pipeline/`（GoalPipeline/TeamPipeline）、`sop/`（标准作业程序）、`classifier/`、`tui/`、`console/`、`agent/_a2a_agent.py`、`middleware/_model_router.py`、`model/_minimax`、`model/_volcengine`。

主要新能力：workspace 池化与跨 agent 共享、技能按 agent 隔离、钉钉渠道与知识库工具、agent-team 主从委派、RAG 分块浏览与 LLM 重排、**agent 自主上下文压缩工具**、**外部执行结果 schema 契约**、实时语音（DashScope/OpenAI/Gemini/xAI）、`A2AAgent`、MiniMax M3、Volcengine Ark。

---

## 4. 兼容性核查证据

### 4.1 公开 API：零删除

逐个解析 `src/` 下所有 `__init__.py` 的 `__all__` 并 diff `v2.0.6` ↔ `v2.0.9`：

```
新增 179 条，删除 0 条
```

（口径说明：这里数的是 diff 中 `>` 前缀的条目数，即真正新增的导出名；不要用 diff 输出的总行数 241，那个数还包含 `5a6,9` 这类区间标记行。）

顶层 `agentscope/__init__.py` 仅改动了模块 docstring。

### 4.2 破坏性签名变更：仅 2 处，且均为私有函数

| 函数 | 变更 | 影响 nanzi |
|---|---|---|
| `app/_router/_session.py::_build_team_detail` | 新增必填参数 `access` | 否（不使用 app 层） |
| `formatter/_xai_formatter.py::_xai_user_args_from_blocks` | 新增必填参数 `supported_media_types` | 否 |

其余 22 处签名变更全部为**新增可选参数/可选 kwonly**，例如 `create_app(+enable_scheduler)`、`upsert_session(+origin=None)`、`AgentState.get_cache(+mtime=None)`。其中：

- `Agent.__init__` —— 签名**逐字未变**
- `ReActConfig` —— **未变**
- `agentscope.permission` 模块 —— **源码零改动**（nanzi 用了 12 处）
- `memory` / `exception` / `plan` / `skill` 模块 —— **零改动**

### 4.3 nanzi 导入符号逐个验证

对 nanzi `app/` 下实际 import 的符号做静态存在性校验，**结果：0 处缺失**。

```
tool:     Bash Edit Glob Grep Read Write ToolBase ToolChoice ToolChunk Toolkit
agent:    Agent ReActConfig ContextConfig InjectionConfig ModelConfig
message:  Msg UserMsg TextBlock DataBlock Base64Source URLSource
          ToolCallBlock ToolResultBlock ToolResultState
state:    AgentState          event: ConfirmResult UserConfirmResultEvent ExternalExecutionResultEvent
mcp:      MCPClient StdioMCPConfig        workspace: WorkspaceBase DockerWorkspace K8sWorkspace LocalWorkspace E2BWorkspace
permission: PermissionBehavior PermissionDecision
middleware: MiddlewareBase   skill: Skill   credential: OpenAICredential
model: OpenAIChatModel       exception: DeveloperOrientedException
```

### 4.4 私有 API 深度依赖核对（最高风险面）

nanzi 伸入了 agentscope 的私有模块，逐项核对 2.0.9 存续情况：

| nanzi 引用点 | 私有符号 | 2.0.9 状态 |
|---|---|---|
| `docker_template_patch.py` | `_make_dockerfile._read_template`（**monkeypatch**） | ✅ 存在，签名 `(name: str) -> str` 不变 |
| `docker_prebuild.py` | `CONTAINER_WORKDIR` / `GATEWAY_HOME` / `prepare_build_context` | ✅ 全在，签名逐字不变 |
| `docker_workspace.py` | `_docker_backend.DockerBackend` | ✅ 4 个公开方法签名不变 |
| `k8s_workspace.py` | `_k8s._constants.POD_WORKDIR` | ✅ 仍为 `"/workspace"` |
| `conversation_run_subprocess.py` | `tool._builtin._backend.LocalBackend` / `ExecResult` | ✅ 均存在 |

**尤其关键**：nanzi 的沙箱 Dockerfile 补丁依赖 `Dockerfile.template` 中的字面串 `curl ca-certificates ripgrep` 做替换，该串在 **v2.0.6 / 2.0.7 / 2.0.9 三个版本第 19 行完全一致**，补丁不会静默失效。

---

## 5. 升级收益（命中 nanzi 现有链路的修复）

### 5.1 沙箱 workspace 工具链（价值最高）

| Commit | 修复 |
|---|---|
| `9b356024` | Edit/Write 后刷新 read cache |
| `a40487a7` | read cache 改为 backend-aware，使**沙箱内 Read→Edit 生效**（此前会基于过期内容编辑文件） |
| `4acb042b` | 截断时保留 tool-result metadata 与 timestamp（nanzi 设 `tool_result_limit=65536`，必然触发截断） |
| `226daf01` / `0727cbf9` | Grep 分页排序稳定、glob 输出限界 |

### 5.2 权限与安全（nanzi 有 `ToolPermissionMiddleware` + 只读快路径预检）

- `ab4bfbea` 阻止破坏性 git 子命令以只读身份走快路径放行
- `5e6587b3` / `083cbd19` / `28fea7fd` sed 短选项/长选项/多 `-e` 表达式解析三连修
- `6b43b13a` `tee` 不再被判定为只读
- `31e0ec61` `ResetTools` 参数校验

**均属安全绕过类修复。**

### 5.3 Token / 用量统计（nanzi 有 `ModelCallStatsMiddleware`）

`63e64aef` 保留 prompt cache tokens、`15ac0029` 最终消息保留 reply usage、`ca908a38` 记录压缩 usage —— **直接影响计费与用量口径准确性**。

### 5.4 HITL 与外部执行（nanzi 用 `UserConfirmResultEvent` + `ExternalExecutionResultEvent`）

新增 `ToolBase.metadata_schema` 与 `ToolBase.check_external_result()`：外部执行器返回结果的 JSON Schema 契约校验，配合 `4c0547b0`「仅成功时校验 external result metadata」。**填补了 nanzi 待审核/外部执行链路的契约空白**。

### 5.5 中断与取消

`e92acef4` 中断清理后传播 cancellation、`128c2182` 中断的 chat response 上报 tracing。

### 5.6 MCP（nanzi 深度集成）

`28ff9fdd` `add_mcp` 返回注册后的 client、`cd72cac5` stateful client 可重连、`7c5c1143` 取消时清理连接、`b6e6906e` SSE transport 按 URL path 识别（query string 兼容）、`0b61e4f5` Docker workspace 运行时 MCP headers。

### 5.7 DeepSeek / 多模态（nanzi 主模型）

`604f8e75` 保留 DeepSeek 图片与 Moonshot 视频、`43deddca` 保留 tool 结果媒体、`20228d5c` history marker、`dda3973a` tool call 前累积的图片。

### 5.8 上下文与时间

`58014d4c` **压缩提示注入当前时间**（呼应平台"时间感知"特性）、`6c5c9eed` 达最大迭代生成最终总结、`e8937a93` 重复工具错误提示。

---

## 6. 风险与前置条件

| # | 风险 | 详情 | 处置 |
|---|---|---|---|
| 1 | **沙箱镜像 tag 全量失效** | `prepare_build_context` 的 `install_block` 由 `uv pip install "agentscope"` 改为 `uv pip install --no-deps "agentscope"`（`feee4634`），该文本进入 `compute_image_tag` 的 SHA256 → 所有已预构建镜像 tag 变化 | 升级后**必须重新预构建沙箱镜像**，旧缓存清理 |
| 2 | **容器内 `--no-deps` 导致工具链依赖缺失（已实际踩中）** | `_GATEWAY_BASE_REQUIREMENTS`（`mcp<2.0.0`/`uvicorn`/`fastapi`/`httpx`）确实未变，gateway 脚本自身也只 import `agentscope.mcp.MCPClient`；**但平台在容器内跑的是内置 Bash/Read/Write 等工具，会 import `agentscope.tool`**（`tool/_types→_utils` 需 `docstring_parser`；`_toolkit` 需 `jinja2`；`_builtin` 需 `aiofiles`/`tree_sitter`/`tree_sitter_bash`/`python-frontmatter`），远超 gateway 自身的 import 面。2.0.7 及以前靠 `uv pip install "agentscope"` 的依赖解析天然带上，改成 `--no-deps` 后丢失，表现为沙箱 Bash 报 `HTTP 500: No module named 'docstring_parser'`。上游 2.0.10dev 仍未修 | ✅ **已在平台侧修复**：把原 K8s 专用清单泛化为 `GATEWAY_EXTRA_PIP`，同时用于 Docker 的**运行时构造**与**预构建**两处（`extra_pip` 会写进 requirements.txt），不依赖上游 Dockerfile 文本 |
| 3 | **`tool_retries_limit=3` 新默认行为** | 同一工具+参数连续失败 3 次自动注入提示（`e8937a93`）。nanzi 未显式设置 → 升级后**自动生效**，会改变现有 prompt 与 token 消耗 | 需决策：接受，或在 config 中显式设定期望值 |
| 4 | `add_mcp` 契约变化 | 基类现返回 `MCPClient`（`28ff9fdd`），nanzi `SshWorkspace.add_mcp` 返回 `None` | 见 §7 技术债 A |
| 5 | `_close_mcp_instance` 条件收紧 | `is_stateful and is_connected` → `is_connected`（`140d6afe` 配套） | 回归 MCP 增删/关闭路径 |
| 6 | event 字段语义变化 | `DataBlockDeltaEvent.data` 由**必填**变可选并新增 `url`；`TextBlockEndEvent` 新增 `text`；`DataBlockStartEvent` 新增 `name` | 检查 nanzi 的 DataBlock 增量解析 |
| 7 | `ContextConfig`/`InjectionConfig` 迁移 | `context_buffer_ratio` 从 `InjectionConfig`（默认 0.2）迁至 `ContextConfig`（默认 0.2），前者标 deprecated 默认 `None`。**默认行为等价**，且 `agent_runtime.py` 未传该参数 | ✅ 无影响 |
| 8 | 依赖新增 | 基础依赖新增 `rich`、`pypdf`、`json_repair[schema]>=0.63.4`；`ripgrep>=14.1,<15` 仅在 `tools` extra（nanzi 不装该 extra） | ✅ 影响小；注意 ripgrep wheel 在 aarch64 的可得性（不涉及 nanzi） |
| 9 | agentscope 侧新增 alembic 迁移 | `0003_channels` / `0004_sops` | ✅ nanzi 未使用 agentscope 自带 `SQLStorage`，无需迁移 |
| 10 | `_scheduler_manager` 移除 3 个方法 | `register_schedule` / `remove_schedule` / `restore` | ✅ nanzi 使用自建 APScheduler |
| 11 | **版本发布时间** | v2.0.9 发布于评估当日，社区验证为零 | 建议先上测试环境观察，或等 `2.0.9.post1` |

---

## 7. 技术债（非本次升级引入）

### A. `SshWorkspace` 与 `WorkspaceBase` 契约偏离两处

`app/services/ai/runtime/agentscope/workspace_ssh.py`：

| 项 | 基类契约（2.0.7 与 2.0.9 相同） | nanzi 实现 | 说明 |
|---|---|---|---|
| `add_mcp` 签名 | `(mcp_client, *, agent_id=None, session_id=None)` | `(mcp_client)` | **2.0.7 起即偏离**，非 2.0.9 引入 |
| `add_mcp` 返回 | 2.0.7: `None` → 2.0.9: `MCPClient` | `None` | 2.0.9 起契约收紧 |

**当前无实际影响**：排查确认 `agentscope` 仅在**自己的 app 层**（`app/_router/_workspace.py`）调用 `workspace.add_mcp(...)`，而 nanzi 不使用 agentscope app 层；nanzi 内部 `app/`、`tests/` 中亦**零调用点**（`SshWorkspace` 用 `_restore_or_seed_mcps` 直接操作 `self._mcps` 绕过该接口）。

**因此本次未改动**——只对齐返回值而不实现 `agent_id`/`session_id` 分桶会制造"假对齐"（基类注释明确警告两者同为字符串、按位置传递会串到别的会话，nanzi 的 `_mcps` 是单列表结构）。若后续要接 agentscope app 层，需连带 `list_mcps` / `_save_mcp_file` / `_restore_or_seed_mcps` 一起做按 `(agent_id, session_id)` 分桶的重构。

### B. 时间戳工厂（可选收益）

2.0.9 起 `Msg` / `Event` / Block 的 `created_at` 统一走 `_generate_timestamp()`（默认实现 `datetime.now().isoformat()` 与旧行为**完全一致**，格式不变）。这意味着 nanzi 现在可以用 `set_timestamp_factory()` **全局统一 agentscope 实体时区**，强化现有"时间感知"能力——2.0.7 只能覆盖 Block。

---

## 8. 升级步骤与验证清单

### 阶段 0：消除版本漂移（已完成）

- [x] `requirements.txt` 锁定 `agentscope[service,storage,workspace]==2.0.9`
- [ ] 本地 `.venv` 重建或升级到 2.0.9，与 `requirements.txt` 对齐

### 阶段 1：本地验证

- [ ] 安装/升级到 2.0.9 后，先跑导入自检：`app/services/ai/runtime/agentscope/imports.py` 的 `verify_agentscope_imports()` 必须 `ok=True`
- [ ] 后端全量：`pytest`（记录 HEAD 基线失败集合，逐条比对**零新增**）
- [ ] 纯前端契约：`pytest --confcutdir=tests/frontend`
- [ ] 前端类型检查：`NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc --noEmit -p tsconfig.app.json`（注意 `tsconfig.json` 是 `"files": []` 引用壳，直接跑属假通过）

### 阶段 2：定向回归（按风险面）

- [ ] **沙箱 Read→Edit 链路**：在 Docker/SSH/K8s 沙箱内 Read 文件 → Edit 修改 → 验证不会基于过期内容编辑（对应 `a40487a7` / `9b356024`）
- [ ] **权限只读快路径**：验证 sed 多表达式、破坏性 git 子命令、`tee` 不再被判定为只读放行（对应 `ab4bfbea` / `5e6587b3` / `083cbd19` / `28fea7fd` / `6b43b13a`）
- [ ] **token 用量统计**：`ModelCallStatsMiddleware` 记录的 prompt cache tokens / reply usage 口径正确（对应 `63e64aef` / `15ac0029`）
- [ ] **MCP 增删与关闭**：连接、重连、取消清理、SSE transport 带 query string 的场景（对应 `cd72cac5` / `7c5c1143` / `b6e6906e`）
- [ ] **中断与恢复**：中断后取消正确传播、恢复链路无残留（对应 `e92acef4`）
- [ ] **HITL / 外部执行**：`UserConfirmResultEvent` 与 `ExternalExecutionResultEvent` 回填正常
- [ ] **DataBlock 事件解析**：确认不依赖 `data` 必填、是否需要支持新增的 `url`
- [ ] **DeepSeek 多模态**：图片经 tool 结果与 history 往返后不丢（对应 `604f8e75` / `43deddca`）
- [ ] **上下文压缩**：接近阈值时的注入与压缩行为符合预期
- [ ] 参考现成契约：`tests/frontend/test_agentscope_injection_config_contract.py`（覆盖本次有变更的 InjectionConfig）

### 阶段 3：镜像与行为决策

- [ ] 重新预构建沙箱镜像，清理旧 tag 缓存（**tag 必变**，见 §6-1）
- [ ] 决策 `tool_retries_limit`：接受默认 3，或显式配置（见 §6-3）

### 阶段 4：灰度与上线

- [ ] 测试环境部署，观察 agent 行为与 token 消耗曲线
- [ ] 生产灰度 → 全量

---

## 9. 回滚方案

运行时版本由 `requirements.txt` 单点控制，回滚成本低：

1. 将 `requirements.txt` 中 `agentscope[...]==2.0.9` 改回目标版本（如 `==2.0.7`）
2. 重建后端镜像
3. **沙箱镜像预构建缓存会再次失效，需重新预构建**（tag 再次变化）

**注意**：回滚不涉及数据库迁移（nanzi 未使用 agentscope 自带 storage），无数据兼容性负担。

---

## 10. 引用

- 上游仓库：`agentscope-ai/agentscope`
- 本评估基于 tag `v2.0.6` / `v2.0.7` / `v2.0.9` 的源码逐符号比对，未改动 agentscope 或 nanzi 任何运行时代码

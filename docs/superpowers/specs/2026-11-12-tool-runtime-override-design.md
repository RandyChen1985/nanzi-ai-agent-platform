# 工具级运行时参数改造设计（超时三级覆盖 · 温度收窄 · 移除执行模型）

- 日期：2026-11-12
- 原型：[`prototypes/2026-11-12-tool-runtime-override.html`](../../../prototypes/2026-11-12-tool-runtime-override.html)
- 关联：`openspec/changes/archive/2026-01-22-enable-tool-runtime-config/`（历史设计，未完成）

## 一、背景与现状核查

智能体「工具高级配置」弹窗当前提供三项参数：执行模型（Override Model）、温度（Temperature）、工具描述覆盖。本轮实测（同一份 `model_name + temperature + description_override` 分别走两条解析路径）结论：

| 参数 | 后端字段 | 现状 | 证据 |
|---|---|---|---|
| 执行模型 | `ToolConfigItem.model_name` | **不生效** | 仅在 `get_tools()` → `_configure_tool_runtime()` 里挂到 `_runtime_config`；`get_tools()` 在 `app/` 下已无调用者 |
| 温度 | `ToolConfigItem.temperature` | **不生效** | 同上；`_runtime_config` 唯一读者是 `_build_tool_observation()`，只填卡片/审计展示，不参与下发 |
| 工具描述覆盖 | `ToolConfigItem.description_override` | **生效** | `_configure_runtime_tool_spec()` 里 `replace(spec, description=...)`，进入模型可见 schema |
| 执行超时 | — | **缺失工具级** | 只有全局 `agent_max_toolcall_timeout` 与版本级 `toolcall_timeout_seconds` |

另外发现两个缺陷：

1. `engine_config_override` 只在注释里出现，无任何实现。
2. `apply_agent_tool_timeout()` 用同一个 `configured_timeout` 覆盖整批 spec 的 `timeout_seconds`，会**抹掉脚本工具注册时自带的超时**（`registry.py` 把 `script_tool.timeout_seconds` 写进 spec 后再被整批覆盖）。

### 为什么「执行模型」无处落地

AgentScope 运行链是单模型 agent：`assistant_agent_runner` 统一创建 `native_model`，工具是纯 Python callable，执行工具不产生新的 LLM 调用。因此「给某工具换个模型执行」在没有内部 LLM 调用的工具上没有承载点。真正有内部 LLM 调用的工具目前只有 `read_image`（走系统配置的默认多模态模型）等少数几个。

## 二、决策（已与用户确认）

| 项 | 决策 |
|---|---|
| 执行模型 | **移除**：弹窗删掉该输入项，`model_name` 字段保留解析以兼容历史版本 JSON，但不再展示、不再声称生效 |
| 温度 | **保留**，但只有「内部会自调 LLM」的工具可编辑；其余工具置灰并解释「该工具执行时不调用模型」 |
| 执行超时 | **新增工具级**，与全局级、智能体版本级形成三级覆盖；范围 1–3600 秒，留空跟随上层 |
| 超时边界 | **工具级只收紧、不放宽**（用户明确：「工具级上限锁不能超过系统级的吧」）：生效值 = `min(工具级, 上层生效值)`，任何一级都不能顶开上层 |

## 三、超时优先级

```
工具级 ToolConfigItem.timeout_seconds（1–3600，空 = 继承）
  ↓
工具自带 spec.timeout_seconds（脚本工具注册值，修复被抹平的缺陷）
  ↓
智能体版本级 ai_agent_versions.toolcall_timeout_seconds（1–86400，NULL = 继承）
  ↓
全局级 agent_max_toolcall_timeout（1–3600，默认 180）
```

规则：

- **按层取第一个有值的作为候选，再与上层上限取 `min`**：工具级只能收紧，不能放宽（`min(工具级, 上层生效值)`）；`tool_defined` 同理。
- 非法值（0、负数、非数字）在该级视为「未设置」，继续回落，不报错、不静默变 0。
- 最终值同时用于 `asyncio.wait_for` 真实下发与卡片/审计轨迹上报，并附来源层级（`tool` / `tool_defined` / `agent` / `global`）。

## 四、工具列表的「已配置」点亮

### 现状

工具卡片上的齿轮按钮（`.tool-action-btn`）只有 hover 变色，**没有任何已配置状态**；`AgentVersionEditorDrawer.vue` 里声明的 `getToolCustomConfig` prop 在模板中从未被使用，是死代码。因此配过高级项的工具在列表里与未配置的完全一样。

### 判定口径

`getToolAdvancedConfigSummary(toolName) → { count, parts[] }`，按「值 ≠ 默认」判定，**不计入**各有独立按钮的配置：

| 计入 | 判定 |
|---|---|
| 执行超时 | `timeout_seconds` 非空 |
| 温度 | `temperature` 非空且 ≠ 工具默认 0.0 |
| 工具描述覆盖 | 去空白后非空 |

不计入：`metadata_dataset_ids`（数据集按钮已点亮）、`delegable_agent_ids`（调度范围按钮已点亮）、渠道凭证（各自弹窗按钮已点亮）。

不用「键存在」判定：历史版本保存时总会写 `temperature: 0`，按键判定会让全部老工具亮起，失去提示价值。前端保存时应改为「只写改过的键」，后端字段保持 `Optional`，不做数据迁移。

### 展示

- 齿轮：有配置时加 `.tool-action-btn--active`（主色 + 白底），并在右上角挂项数角标（1–3）。
- 卡片内摘要行：`超时 600s · 描述覆盖` / `温度 0.3`，10px 主色，与弹窗内文案同源。
- 按钮 `title` 与 tooltip 由同一函数生成：`已配置 2 项：执行超时 600s、工具描述覆盖`。
- 只读态（已发布版本、分享页）同样点亮，不做权限差异。
- 弹窗里清空某项并保存 = 回到未配置，齿轮随之熄灭、角标减一。

## 五、改动清单

| 文件 | 改动 |
|---|---|
| `app/schemas/agent.py` | `ToolConfigItem` 增加 `timeout_seconds: Optional[StrictInt] = Field(None, ge=1, le=3600)`；`model_name` 保留但标注「不生效，仅兼容历史数据」 |
| `app/services/ai/runtime/agentscope/tool_timeout.py` | 新增 `resolve_tool_timeout(spec, tool_cfg, agent_timeout, global_timeout)`；`apply_agent_tool_timeout()` 接收 `tool_timeouts: Mapping[str, float]`，按 spec 逐个计算而非整批覆盖 |
| `app/services/ai/runners/assistant_agent_runner.py` | `_resolve_runtime_tools_from_config()` 归一 `self.config.tools` 为 `{tool_name: timeout}` 并传入；`_build_tool_observation()` 上报真实超时值与来源 |
| `app/services/ai/tools/registry.py` | 新增 `TOOLS_WITH_INTERNAL_LLM = {"read_image"}`；在 `get_runtime_tool()` 入口统一打 `supports_internal_llm`（不能只在有配置时打，否则标记随配置漂移）；新增 `_wrap_with_tool_temperature()` 把工具级温度压进 ContextVar；删除 `_configure_tool_runtime()` 与 `get_tools()` 里的 `_runtime_config` 注入（`get_tools()` 本身保留，历史兼容） |
| `app/core/context.py` | 新增 `current_tool_runtime_override` ContextVar，在工具 invoke 前后 set/reset |
| `app/services/ai/tools/system_executive_tools.py` | `read_image` 的 `temp_override=0.0` 改为读当前工具覆盖，缺省仍 0.0 |
| `frontend/src/components/agent/ToolRuntimeConfigModal.vue` | 删除 `ModelSelect` 与「执行模型」整块；新增超时数字输入 + 生效值/来源提示；温度按 `supportsInternalLlm` 置灰 |
| `frontend/src/api/agent.ts` | `ToolConfigItem` 类型补 `timeout_seconds` |
| `frontend/src/constants/toolCapabilities.ts`（新增） | `TOOLS_WITH_INTERNAL_LLM` 常量与 `toolSupportsInternalLlm()`，与后端同一份名单 |
| `frontend/src/components/agent/AgentVersionEditorDrawer.vue` | 齿轮按钮加 `tool-action-btn--active`、项数角标、卡片内摘要行；新增 prop `getToolAdvancedConfigSummary`，删除从未使用的 `getToolCustomConfig` |
| `frontend/src/views/AgentManagement.vue` | 新增 `getToolAdvancedConfigSummary()`（供齿轮/角标/title/摘要行取数）与 `toolTimeoutCeiling` / `toolTimeoutCeilingSource`（弹窗里的上层上限与来源），并把旧的 `getToolCustomConfig` 换掉 |

不新增数据库迁移：`tools` 已是 JSON 列，`ai_agent_versions.toolcall_timeout_seconds` 已存在。

## 六、测试

- 新增 `tests/ai/tools/test_tool_timeout_three_levels.py`：三级优先级、留空回落、非法值回落、脚本自带值位次、上限钳制。
- 改造 `tests/ai/test_tool_runtime_config.py`（原 `tests/ai/tools/`）：删除 `_runtime_config` 断言，改为断言「描述覆盖生效」「能力标记不随配置漂移」「温度只对内部 LLM 工具包装且调用后复位」。
- 改造 `tests/ai/runtime/test_effective_temperature.py`、`test_effective_model_identity.py`：断言卡片不再读工具级 `_runtime_config`（模型/温度口径与实际下发一致）。
- 改造 `tests/ai/runtime/test_agent_tool_timeout.py`：新增「工具自带超时 > 版本级」用例。
- 改造 `tests/frontend/test_model_select_contract.py`：由「工具弹窗用 ModelSelect」改为「工具弹窗已无模型下拉」。
- 前端契约（`pytest --confcutdir=tests/frontend`）：弹窗不再出现「执行模型」，出现超时输入与来源提示；纯函数工具温度控件 disabled。
- 新增 `tests/frontend/test_tool_advanced_badge_contract.py`：有配置时齿轮带 active + 角标、摘要行文案、`getToolCustomConfig` 死 prop 已移除。
- 验收：脚本工具配 600s 实测 600s 才超时；留空时与版本配置/全局一致；工具卡片与审计与真实下发一致；历史版本含 `model_name` 的 JSON 可正常加载。

## 七、已确认结论

1. 脚本工具自身超时排在「工具级配置之后、智能体版本之前」——保持，并顺手修掉整批覆盖的缺陷。
2. 工具级上限不设固定的 86400/3600 二选一，而是**跟随上层生效值**：表单仍限制 1–3600，运行时再按 `min(工具级, 上层)` 收口。

## 八、实现状态（2026-11-12 落地）

| 项 | 状态 |
|---|---|
| 后端三级超时解析与真实下发 | ✅ `resolve_tool_timeout()` / `apply_agent_tool_timeout()` / `collect_tool_timeouts()`；卡片与审计上报 `timeout_seconds` + `timeout_source` |
| 工具级温度只对内部自调 LLM 的工具生效 | ✅ `read_image` 读 ContextVar，缺省仍 0.0；纯函数工具不包装 |
| 执行模型下架 | ✅ 弹窗删除输入项并留一句「已移除」说明；`model_name` 仅兼容历史 JSON 解析，不再写回 |
| 齿轮点亮 + 角标 + 摘要行 | ✅ 按「值 ≠ 默认」判定，`temperature: 0` 不计入 |
| 保存只写改过的键 | ✅ 前端 `delete next.*` + 后端 `model_dump(exclude_none=True)` |
| 版本 diff 弹窗纳入超时 | ⚠️ 部分：`compareNamedCollections("tools", ...)` 拿整份工具条目做深比较，所以 `timeout_seconds` 等字段变化**确实会被判为 modified**；但展示是整段 JSON，没有字段级可读文案（不会写「超时 600s → 180s」）。要做可读版得单独给 tools 组加字段级 item |
| 数据库迁移 | 不需要（`tools` 是 JSON 列，`toolcall_timeout_seconds` 已存在） |

验证：`tests/ai` 全量 3288 passed；`tests/frontend` 契约全绿（新增 8 条）；类型检查 `vue-tsc -p tsconfig.app.json --noEmit` = **157（基线，0 新增）**。

> 口径纠正：在 `frontend/` 下直接 `vue-tsc --noEmit` 因为根 tsconfig 是 project references，**什么都不检查**（永远 0 error）。必须 `-p tsconfig.app.json` 或 `-b`。

## 九、自查发现并修掉的问题（2026-11-12 review）

| # | 问题 | 性质 | 修法 |
|---|---|---|---|
| 1 | `get_tools()` 删除配置注入时，把原实现里的 `copy.copy(tool)` 一起删了 → 从"返回副本"退化成"返回注册表共享实例"，调用方一改就污染全局 | **行为回退** | 恢复浅拷贝（`tools.append(copy.copy(tool))`），并说明这条保护与配置注入是两回事 |
| 2 | `ToolConfigItem.timeout_seconds` 带 `ge=1` 约束后，历史 JSON 里一个非法超时会让 **整条** 配置解析失败，连"描述覆盖"一起失效（界面配了、模型看旧描述） | 新引入的脆弱点 | 新增 `_parse_tool_config_dict()`：剔除非法 `timeout_seconds` 后重试，保住其余字段 |
| 3 | 知识库 runner 与 ChatBI（`data_tools`）也调 `apply_configured_agent_tool_timeout()`，但没传 `tool_timeouts` → 这两条链上"工具级超时配了不生效"，正是本轮要根治的病 | **功能缺口** | 两处都补 `collect_tool_timeouts(configured_tools)` |
| 4 | workspace 绑定出口用 `replace(rebound, ...)` 新造 spec，只搬运了 `timeout_seconds`，丢掉 `timeout_source` / `supports_internal_llm` → 卡片与审计来源退化成空 | 元数据丢失 | 绑定出口一并搬运这两个字段 |
| 5 | 弹窗按上层收口只用在"提示"上，保存的却是原始输入 → 提示"按 180s 生效"、列表摘要显示"600s"，界面与真实下发再次对不上 | **口径 bug** | 输入上限 `timeoutCeiling = min(3600, 上层值)`，`clampTimeout` 按它收口；摘要行也按 `min(配置值, 上层值)` 显示，被收口的标"（上限）" |
| 6 | 历史脏值 `timeout_seconds: 0` 会在输入框里显示 `0`（非法值） | 体验瑕疵 | 初始化时 `<= 0` 视为未配置（空框） |
| 7 | 契约测试 `test_model_select_contract` 仍断言工具弹窗里有 `ModelSelect` | 已随设计更新 | 改为断言"工具弹窗已无模型下拉"，并新增超时相关断言 |

已核实**没有**回归的三处（曾被怀疑）：

- `runtime_tool_spec_from_legacy_tool()` **不读** `tool.timeout_seconds` → MCP / 内置工具 spec 的 `timeout_seconds` 恒为 `None`，不会被误判成 `tool_defined` 而压过版本级配置。
- MCP 的 `resolve_mcp_tool_timeout()` 与委派的 `_resolve_delegation_timeout_seconds()` 只用于**工具内部**传输/子代理 deadline，不写进 `RuntimeToolSpec`。
- AgentScope 的模型可见 schema 来自 `RuntimeToolSpec.parameters_schema`（`self.input_schema = spec.parameters_schema`），**不是** `inspect.signature(callable)`；因此给 `read_image` 套一层 `async def wrapper(**kwargs)` 不会破坏工具定义。

## 十、实机验证发现的两个问题（2026-11-12 二次修复）

上线自测时给 `exec_command`（运行时即 AgentScope `Bash`）配了 10s，但时间线仍显示「972ms / 300s」。两处独立缺陷叠加造成"配了像没配"：

### 缺陷 A：配置名 ≠ 运行时 spec 名，工具级超时查不到

| 环节 | 名字 |
|---|---|
| 版本 JSON 里存 | `exec_command`（平台名，页面上的名字） |
| `get_runtime_tool()` 返回的 spec | `Bash`（AgentScope 原生名，`AGENTSCOPE_BUILTIN_TOOL_ALIASES` 映射） |
| MCP 工具 spec | `mcp__xxx_<hash>`（`build_model_tool_name` 的 model 化名） |

`collect_tool_timeouts()` 按配置名建表，`apply_agent_tool_timeout()` 却按 `spec.name` 查表 → 永远查不到 → 静默回落到版本级/全局级。**这是本轮功能在真机上完全不生效的根因**，单元测试只用同名工具，所以没抓出来（教训：测试必须覆盖"配置名与 spec 名不同"这一现实）。

修法（三层兜底）：

1. `get_runtime_tool()` 把配置名写进 `spec.metadata["_platform_tool_name"]`；
2. `_lookup_tool_timeout()` 查表顺序：平台配置名 → spec 名 → 别名反查（`AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(配置名) == spec.name`，用于 workspace 重新绑定后 metadata 丢失的场景）；
3. MCP 这类两者都不同的，靠第 1 层命中。

### 缺陷 B：界面分母取的是版本级快照，不知道工具级

挂起行「已耗时 / 上限」的分母来自 SSE `run_config.agent_max_toolcall_timeout`（版本级或全局），与单次工具的真实 limit 无关。即使后端把 Bash 收紧到 10s，界面仍写 300s。

修法：

- 后端 `event_stream.py` 在 `TOOL_CALL_START` 事件里带上该工具 spec 的 `timeout_seconds`（查不到就不带，前端回退快照，绝不编数字）；
- 前端 `processTimeline.ts` 透传 `timeout_seconds` 到时间线条目，`ChatExecutionTimeline.pendingToolTimeoutSeconds()` 优先用条目上的值、`props.toolTimeoutSeconds` 只做兜底；
- 与工具完成态上报的 `timeout_seconds` / `timeout_source`（第 五 节）保持同一口径。

### 新增测试

- `tests/ai/runtime/test_event_stream_observability.py`：`test_tool_call_start_carries_effective_tool_timeout`（分母随事件下发）、`test_tool_call_start_omits_timeout_when_unknown`（查不到不编数字）。
- `tests/frontend/test_tool_advanced_badge_contract.py`：`test_timeline_uses_per_call_timeout_before_run_snapshot`（字段透传 + 展示侧优先级）。
- 复现脚本（手工验证）：`ToolRegistry.get_runtime_tools([{"name": "exec_command", "timeout_seconds": 10}])` → spec 名 `Bash`、apply 后 `timeout_seconds == 10`、`timeout_source == "tool"`。

回归：`tests/ai`+`tests/api` **5022 passed**；`tests/frontend` **4880 passed / 1 skipped**；`tests/core`+`tests/services` **3493 passed**；`vue-tsc -p tsconfig.app.json --noEmit` **157（基线）**。

### 缺陷 C（二次修复时漏掉的第三层）：SSE 字段白名单把值截在门外

缺陷 A、B 修完后重启，DB 里工具步骤的 meta 已经是 `{"timeout_seconds": 10.0, "timeout_source": "tool"}`（后端确认生效），界面仍显示「962ms / 300s」。

原因：前端从 SSE chunk 到时间线条目要过 `syncProcessTimelineLog()`，它是**显式字段白名单**（逐字段 `data.x === undefined ? undefined : ...`）。上一轮只改了它内部调用的 `upsertTimelineLog` 与本文件的时间线类型，字段在进入之前就被这层丢掉了，条目上永远没有 `timeout_seconds` → 展示侧只能回退版本级快照。

修法：[agentscopeSseHandlers.ts](frontend/src/utils/agentscopeSseHandlers.ts:796) 补上映射，并沿用 `temperature` 的写法同时判 `undefined` 与 `null`（`Number(null) === 0` 会把"没配"显示成 0 秒上限）。

契约测试：`tests/frontend/test_tool_advanced_badge_contract.py::test_sse_log_mapping_forwards_timeout_field`。

**教训（值得记住）**：这条链一共三层「字段关卡」——后端 spec 上算对值 → 后端事件里带出去 → 前端白名单放进来 → 展示侧优先用它。任一层漏了，表现都是"界面没变化"，但根因位置完全不同。以后给工具调用加任何新展示字段，四层要一起过一遍，并且**用 DB/日志验证后端那一半，用产物 grep 验证前端那一半**，不要看到界面没变就回头改后端。

## 十一、外部 Review 核实与修复（2026-11-12 三轮）

外部 Review 提了 4 条，逐条核实**全部成立**，均已修：

| # | 问题 | 性质 | 修法 |
|---|---|---|---|
| 1 | `isTimeoutClamped` 拿 `clampTimeout()` 的返回值比上限，而该值已被压到上限以内 → 判断恒 false，琥珀色告警永不出现；用户填 600 被静默改成 180 且没有任何提示 | **真 Bug（我上一轮引入）** | 改用「用户原样输入」比较：`raw > timeoutCeiling`；新增 `ceilingHint` 区分「被上层限住」与「被 3600 形式上限限住」，避免上层配 7200 时文案自相矛盾 |
| 2 | 脚本工具内层 `ScriptToolRunner.resolve_timeout()` 仍按「脚本自带 / 全局-margin」算，`spec.timeout_seconds` 没传下去 → 工具级配 60s 而脚本自带 30s 时内层 30s 就杀；版本级放大到 600s 对无自带超时的脚本也无效 | **真 Bug（功能缺口，第五层关卡）** | `ScriptToolRunner` 新增 `tool_timeout_seconds`，`_bind_script_tool_spec` 传入 `spec.timeout_seconds`，内层优先用它并留 0.5s 余量（让内层先报可读超时，而不是外层协程被取消） |
| 3 | `ChatConfig.tools` 直接持有 `ToolConfigItem`，库里一个脏 `timeout_seconds` 会让整份配置校验失败 → 会话不可用；`_parse_tool_config_dict` 的容错根本走不到 | 真缺陷（防御不一致） | `ToolConfigItem` 加 `field_validator(mode="before")` 清洗：非整数 / ≤0 / >3600 → `None`，与 registry 侧口径一致 |
| 4a | 前端 `globalAgentToolcallTimeout` 初始 120，后端默认 180 | 瑕疵 | 改 180 并对齐注释 |
| 4b | 温度提示写「留空保存即恢复默认」，但控件是滑块，没有「留空」动作 | 瑕疵 | 改「调回 0.0 保存即恢复默认」 |
| 4c | `EmbedChat.vue` 降级路径（`msg.logs` / `buildLegacyProcessTimeline`）没写 `timeout_seconds` | 瑕疵 | 补字段 + 给 `LogEntry` 加类型，主路径与降级路径口径一致 |

### 关于「为什么两轮都没修干净」

同一个根因重复出现：**值算对了，但没有跟着字段走完全程**。这条链到现在一共五层关卡：

1. 后端 spec 上算出生效值（含别名 / MCP 名匹配）—— 缺陷 A
2. 后端事件里把值带出去 —— 缺陷 B
3. 前端白名单 `syncProcessTimelineLog()` 放进来 —— 缺陷 C
4. 展示侧优先用它、快照只兜底
5. **内层执行器真正按它执行** —— 本次缺陷 #2（脚本工具）

以后给工具调用加任何生效型参数，这五层必须逐层验证，且验证手段要分开：后端那一半查 DB / 日志，前端那一半 grep 构建产物，内层那一半看执行器自己的超时解析。

### 验证

`tests/ai` + `tests/api` **5025 passed**；`tests/frontend` **4885 passed / 1 skipped**；`vue-tsc -p tsconfig.app.json --noEmit` **157（基线，0 新增）**。
新增测试：`test_script_tool_runtime.py`（内层超时优先级 2 条）、`test_tool_runtime_config.py`（脏值清洗 1 条）、`test_tool_advanced_badge_contract.py`（告警判断 / 文案 / 默认值 / 降级路径 4 条）。

## 十二、第四轮：`msg.logs` 三个写入点（2026-11-12 收尾）

Review 又指出一处降级路径遗漏，核实成立：`msg.logs` 有**三个**写入口，上一轮只补了第一个。

| 写入点 | 场景 | 状态 |
|---|---|---|
| `msg.logs[existingIdx] = {...}` | 更新已有日志（工具完成态） | 上一轮已补 |
| `msg.logs.push({...})` | **首次推入 = `TOOL_CALL_START` 的 pending 态** | 本轮补上 |
| `AgentDebug.vue` 的 `msg.logs.push(log)` | 调试页同口径 | 本轮补上（含 `LogEntry` 类型） |

pending 行恰恰是唯一需要分母的状态（完成态已有实际耗时），漏这一处比漏更新那处更关键——只是主链路 `syncProcessTimelineLog` 覆盖了它，才没在页面上暴露。

契约测试改为**同时断言三个写入点**，避免以后只改一处：
`tests/frontend/test_tool_advanced_badge_contract.py::test_legacy_logs_path_also_carries_timeout`。

验证：`tests/frontend` 4885 passed / 1 skipped；`tests/ai` 相关 34 passed；`vue-tsc -p tsconfig.app.json --noEmit` 与基线零差异（157）。

## 十三、第五轮：四条边界的核实与处置（2026-11-12）

Review 的第二遍深挖提了 4 条边界问题，逐条核实：**3 条成立并已修，1 条属设计取舍，另有 1 个子项记录不修**。

| # | 问题 | 核实 | 处置 |
|---|---|---|---|
| 1 | 沙箱模式下 `_run_sandbox` 用 `timeout=timeout_seconds + 5`，内层永远比外层宽 → 外层先取消协程，用户看不到「脚本执行超时（上限 N 秒）」这句内层文案 | **成立**（既有行为，但与本轮的三级超时语义冲突） | 改为 `timeout=timeout_seconds`。内层值本身已含 0.5s 余量（`resolve_timeout`），必然先于外层触发，与本地分支口径一致 |
| 2 | `_coerce_timeout_seconds` 用 `int(str(value))`，JSON 里常见的 `10.0` 会被 `int("10.0")` 判成脏值洗成 `None` | **成立**（本轮新写的清洗逻辑自身的 bug） | 改先 `float()` 解析再判 `is_integer()`：`10.0 → 10`，`10.5 → None` |
| 3 | 部分内置工具自带固定内层超时（如 `system_http_request` 的 `httpx(timeout=30)`）：工具级往小调有效（外层掐断），往大写调无效 | **成立，但属既有设计** | **不修**：逐个改内置工具的内部超时不现实，且「工具级只能收紧」的语义下，放大本就该由上层预算决定。记录为已知限制 |
| 4a | ChatBI 的完成态 `AgentExecutionStep` / `log_payload` 没带 `timeout_seconds` / `timeout_source`，与 `AssistantAgentRunner._build_tool_observation` 不一致 | **成立** | 已补：完成态同样从 tools 里取生效值与来源 |
| 4b | 委派工具只给 `sub_agent_call` 配超时，自动补齐的 `sub_agent_batch_call`（字符串条目）不会继承 | **成立，设计合理** | **不修**：batch 是另一个工具，用户没给它配就是没配；强行复制会让「配置视图」与「实际生效」再次不一致 |

验证：`tests/ai` + `tests/api` **5026 passed**；`tests/frontend` 4885 passed / 1 skipped；`vue-tsc -p tsconfig.app.json --noEmit` 与基线零差异（157）。
新增测试：`test_sandbox_inner_timeout_not_looser_than_outer`、`_coerce_timeout_seconds` 的浮点用例。

## 十四、语义调整：工具级从「只能收紧」改为「逐级覆盖」（2026-11-12）

上线后讨论发现「工具级只能收紧」这条约束站不住，已改为**上层是默认值、工具级可覆盖**。

### 为什么改

1. **天花板本来就漏**：版本级 `toolcall_timeout_seconds` 可配到 86400，管理员把某版本设成 7200，该版本下所有工具都能跑 7200s——全局 3600 的"保护上限"在版本级那一层并不成立。
2. **版本级被迫一身兼两职**（默认值 + 天花板）：想给一个慢工具配 600s，就得把整个版本调到 ≥600s，于是该智能体下**所有工具**默认预算一起变大，把不想放大的也放大了。这恰恰废掉了工具级存在的意义。
3. **需求真实**：大文件处理、外部 HTTP 批处理、长脚本、大仓库检索，天生就慢。

### 新语义

| 层级 | 作用 | 范围 |
|---|---|---|
| 工具级 | **最高优先级**，配了就按它跑，可以超过上层 | 1–86400 |
| 工具自带（脚本） | 次优先 | 工具作者定义 |
| 版本级 | 默认值 | 1–86400 |
| 全局 | 兜底默认值 | 1–3600（默认 180） |

- 运行时**不再静默截断**：`resolve_tool_timeout` 去掉 `min(配置值, 上层值)`，直接返回配置值。
- 配置界面**只提示不阻止**：
  - 超过上层默认值 → 琥珀色提示「已超出{版本级/系统全局}的 Ns：该工具会独占更长运行时间，保存后按 X s 生效（其它工具不受影响）」
  - 超过表单硬上限 86400 → 提示会被截断（这是唯一真截断）
- 列表摘要显示真实配置值，超上层时补「·超上层」标记。

### 为什么保留 86400 这个硬上限

完全不设边界等于允许「无超时」（填 99999999 的工具永远不返回，会长期占住会话/worker）。86400 与版本级同口径，不引入第二套数字；真要更长的单次调用，说明是平台级决策，应走版本级默认值。

### 改动清单

- 后端：`tool_timeout.resolve_tool_timeout` 去掉 min；新增 `MAX_TOOL_TIMEOUT_SECONDS = 86400`；`ToolConfigItem.timeout_seconds` 范围 1–86400（含清洗逻辑）
- 前端：`ToolRuntimeConfigModal` 的 `timeoutCeiling`（按上层钳制）→ `upperTimeout`（仅默认值/提示）+ `isAboveUpper` + `isOverMax`；`MAX_TOOL_TIMEOUT` 3600 → 86400；摘要去掉 `Math.min`
- 测试：`test_tool_timeout_three_levels.py` 两个「cannot exceed」反转为「can exceed」；`test_agent_tool_timeout.py` 期望值更新；`test_tool_runtime_config.py` 范围断言更新；前端契约改为断言「不静默截断 + 越界提示」

## 十五、真正的天花板：整轮看门狗（2026-11-12）

第十四节放开工具级上限后，暴露出上层的另一道约束——`agent_chat_producer_hard_timeout_seconds`：

```python
# app/api/v1/endpoints/chat.py
await asyncio.wait_for(asyncio.shield(producer_task), timeout=hard_timeout)
except asyncio.TimeoutError:
    producer_task.cancel()        # 整个 producer 被取消
    await _release_locks_on_client_abort()
```

### 它与工具超时是**两个维度**

| | 工具级/版本级/全局级 | 整轮看门狗 |
|---|---|---|
| 管的范围 | 单次工具调用 | **整轮**：从发消息到收尾，含鉴权准备、每次模型调用、每次工具调用、落库收尾 |
| 超时后的表现 | `RuntimeTimeoutError` → 「工具执行超时（上限 N 秒）」 | 整个协程被 cancel → **整轮中断**，没有任何「工具超时」文案 |
| 计时起点 | 该工具开始执行时 | 用户发出消息时（**累计**） |

因此生效上限是：

```
实际能跑到 = min(工具级配置, 看门狗 − 本轮已耗时)
```

即使工具级配 500s（< 600s 看门狗），只要前面模型思考与其它工具花掉 200s，第 700s 照样被掐。

### 处置

1. **上限对齐**：`MAX_PRODUCER_HARD_TIMEOUT_SECONDS` 3600 → **86400**。不改这一项，工具级的长预算永远跑不完，放开工具级等于白放开。默认值仍 600，正常对话兜底时长不变——调大上限只影响「producer 卡死时最晚多久被强杀」。
2. **迁移只改描述**：`db-prod/V216` + `db-prod-pg/V116` 更新 description（配置页展示的是 DB 里的描述，改代码不改描述会让运维以为上限还是 3600）。不新增行、不动 value。
3. **配置界面提示**（方案 A，用户选定）：`/api/portal/agents/toolcall-timeout` 增加返回 `producer_hard_timeout_seconds`。**两层都要提示**：
   - **工具级**（`ToolRuntimeConfigModal`）：常驻展示「整轮看门狗 N s」，工具级超过它时给更强的琥珀色警告：**本轮会先被强制终止并释放会话，这个工具等不到自己超时**。
   - **智能体版本级**（`AgentVersionEditorDrawer`）：同样常驻展示 + 越界警告，并额外点明**「该版本下所有工具都会受影响」**——版本级是所有工具的默认值，踩中的概率比工具级更高，只提示工具级会让人误判成「是这个工具有问题」。顺带补「超过 86400 会失焦截断」的即时提示（原先只有 blur 才截断，输入 9999999 时界面一声不吭，看着像能存）。
   - 看门狗为 0（已关闭）时两层都不展示、不参与比较。
4. **不推荐关闭看门狗解决长超时**：关闭会失去「会话最终一定被释放」的兜底，producer 卡死时只能等会话锁 TTL 过期。

### 仍然存在的第三道限制（既有行为，未处理）

- 沙箱执行通道自身 600s（`SANDBOX_COMMAND_TIMEOUT_SECONDS`）：脚本工具在沙箱里配 1200s 仍被通道截断。
- 部分内置工具自带固定内层超时（`system_http_request` 的 `httpx(timeout=30)`）：收紧有效，放大会被其限制。

# API 工具「向导模式」设计

- 日期：2026-10-15
- 涉及页面：系统配置 › 工具管理 › API 工具注册表 › 添加/编辑工具
- 设计稿：`prototypes/2026-10-15-api-tool-wizard.html`（可交互）+ `prototypes/shots/*.png`
- 迁移：**零迁移**（不改表结构、不新增配置项、不新增权限）

## 1. 背景与现状

现有「添加/编辑工具」是一个弹窗，8 个字段平铺（`frontend/src/components/system/ToolRegistry.vue:637-738`）：
工具名称、HTTP 方法、URL 模板、描述、参数定义（JSON/可视化）、业务分组、Headers、启用。

对不熟悉 API 的平台管理员，有两个硬卡点：

1. **URL 模板不会写**：不知道 `{param}` 占位符是什么意思、query 参数该不该写进地址。
2. **参数定义不会写**：那是一段 JSON Schema，且运行时语义反直觉。

运行时对 `parameter_schema` 的实际解释（`app/services/ai/tools/generic_api.py`，本次设计必须尊重）：

| # | 事实 | 出处 |
|---|---|---|
| 1 | 兼容三种写法：标准 JSON Schema（读 `properties` + 顶层 `required`）、扁平 map（顶层即参数表，`type`/`required`/`$schema` 跳过）、极简 `"param": "描述"` | `generic_api.py:30-96` |
| 2 | **参数缺省是必填**（`param_def.get("required", True)`），与标准 JSON Schema 默认可选**相反** | `generic_api.py:63-80` |
| 3 | 标准形态下 `required: []` 表达不了「全部可选」——空数组是 falsy，会掉回默认必填分支，故非必填必须写内联 `required: false` | 同上 |
| 4 | `default` 只在非必填时被读取 | `generic_api.py:78` |
| 5 | URL 里 `{key}` 做路径替换；GET 其余参数 → query；POST/PUT/PATCH/DELETE 其余参数 → JSON body | `generic_api.py:127-183` |
| 6 | headers 原样透传；`trust_env=False`；超时 30s | `generic_api.py:151-183` |
| 7 | 表内工具一律被标记为只读（`tool.is_read_only = True`），但**请求会真实发出**，POST/PUT/DELETE 不拦 | `generic_api.py:104-107` |

另有**三处「HTTP 方法」清单互不一致**（本次一并收敛，见 §7）。

前端已有的相关能力与约束：

- `frontend/src/utils/toolSchema.ts`：`parseToolSchema` / `serializeToolSchema` 纯函数，只接管 `properties`/`required`，其余键原样保留，**保存时维持打开时的形态**（不把扁平写法强行规范化）。这是本特性唯一允许的参数数据逻辑来源。
- `tests/frontend/test_api_tool_schema_editor_contract.py`：18 条静态源码断言，其中 **10 条锚在 `ToolRegistry.vue` 的源码文本上**（如 `assert 'v-if="schemaEditorMode === \'json\'"' in source`）。
- `tests/frontend/tool_schema_roundtrip.mjs`：用 `node --experimental-strip-types` 真跑纯函数往返，`pytest` 里通过 subprocess 调用。

## 2. 目标与非目标

**目标**：让不熟悉 API 的平台管理员，能从「接口文档里的一条请求」开始，在 4 步引导内配出一个**试跑通过**的 HTTP 工具，全程不需要理解 `{param}` 与 JSON Schema。

**非目标（明确不做）**：

- 不支持 OpenAPI/Swagger 片段导入（用户已否决）。
- 不做「自然语言 → AI 生成配置」（用户已否决）。
- **不新增第二个数据模型**：向导不引入 `wizardForm`，它只是填 `toolForm` 的一种交互。
- **不改运行时对参数的解释方式**：缺省必填等反直觉语义保持原样，向导负责**解释**它，不负责改它。
- 零迁移、零新增配置项、零新增权限。

## 3. 决策记录

以下为本次对话中用户拍板或按推荐定下的决策：

| # | 决策 | 结论 |
|---|---|---|
| D1 | 向导的聪明程度 | **分步引导 + URL/curl 自动解析 + 保存前试跑**（不做 AI 生成） |
| D2 | 解析输入范围 | **完整 URL（含 query）+ curl 命令**；curl 只取 `-X` / `-H` / `-d`/`--data` / `--url`，不做完整 shell 语法解析 |
| D3 | 试跑对目标地址的限制 | **与运行时同权，不加地址限制**；私网/回环仅在 UI 提示；每次试跑写审计日志 |
| D4 | 向导适用范围 | **新建与编辑都能用**；编辑时预填已保存值 |
| D5 | 参数编辑区架构 | **抽共享 `ToolParamEditor.vue`**，表单 tab 与向导共用一套（约 8 条既有契约需迁移锚点，意图不变） |
| D6 | Headers 显示是否脱敏 | **不做显示层脱敏**：headers 明文落库、运行时原样透传，只在显示层打码会制造「已经安全了」的错觉，且用户改 token 时还得先解开。要做脱敏必须连存储一起加密，属另一话题 |
| D7 | HTTP 方法清单分裂 | **收敛**：补 PATCH、删永远筛不出结果的 HEAD/OPTIONS（见 §7） |

## 4. 方案总览

弹窗顶部增加分段切换，两个 tab **共用同一份 `toolForm`**：

```
添加新工具
[ 表单模式 | 向导模式 ]

向导 4 步（步骤条可点击回退，不强制线性）：
  ① 粘贴请求    URL 或 curl → 方法 / 地址 / 路径参数 / query / 请求体 / headers
  ② 起名与描述  工具名称、描述、业务分组（+ 描述写法提示）
  ③ 确认参数    自动生成的清单 + 落位标签 + 必填建议，可就地改
  ④ 试跑一次    填测试值 → preview → 状态码 / 耗时 / 响应，通过后保存
```

## 5. 组件划分

| 文件 | 性质 | 职责 | 关键接口 |
|---|---|---|---|
| `frontend/src/utils/toolImport.ts` | 新增·纯函数 | 解析 URL / curl，抽出方法、地址、路径参数、query、body、headers | `parseRequestInput(text: string): ParsedRequest \| { error: string }` |
| `frontend/src/utils/toolDraft.ts` | 新增·纯函数 | 解析结果 → 参数清单；判落位与必填建议；落位规则与运行时同源 | `buildDraftParams(parsed): DraftParam[]`、`resolveParamLocation(method, urlTemplate, name): 'path' \| 'query' \| 'body'` |
| `frontend/src/utils/toolMethods.ts` | 新增 | 平台支持的 HTTP 方法唯一来源（单一职责：只有清单与判定） | `SUPPORTED_HTTP_METHODS`、`isSupportedMethod(m)` |
| `frontend/src/components/system/ToolParamEditor.vue` | 新增·抽取 | 参数编辑区（JSON/可视化双模式、嵌套提示、增删行）。从 `ToolRegistry.vue:665-721` 抽出，行为不变；向导模式额外显示落位标签 | `v-model`（schema 字符串）、`mode: 'full' \| 'wizard'`、`locations?: Record<string, 'path'\|'query'\|'body'>` |
| `frontend/src/components/system/ApiToolTester.vue` | 新增 | 试跑面板：按 schema 生成输入表单 → 调 preview → 展示状态码/耗时/响应/请求摘要 | `props: { method, urlTemplate, headersStr, schemaStr }` |
| `frontend/src/components/system/ToolWizard.vue` | 新增 | 4 步壳：步骤状态、逐步校验、导航；只负责写 `toolForm`；第 ④ 步内嵌 `ApiToolTester` | `v-model`（整个 toolForm） |
| `frontend/src/components/system/ToolRegistry.vue` | 改动 | 加 tab；表单模式改用 `ToolParamEditor`；挂 `ToolWizard`；`saveTool()` 仍是唯一保存入口 | — |
| `app/services/ai/tools/generic_api.py` | 改动 | 抽出请求内核 `_perform_request`，`_execute_request` 改为调用它，**对外输出逐字节不变** | `_perform_request(config, params, *, raise_on_http_error=True) -> (response, elapsed_ms, final_url)` |
| `app/api/portal/endpoints/tools.py` | 改动 | 新增 `POST /preview` | 见 §8 |
| `app/schemas/tool.py` | 改动 | 新增 preview 的请求/响应模型 | 见 §8 |
| `frontend/src/api/tool.ts` | 改动 | 新增 `toolApi.preview(payload)` | — |

`SUPPORTED_HTTP_METHODS` 常量放在 `frontend/src/utils/toolMethods.ts`（前端唯一来源），后端运行时清单保持现状并用契约测试对齐（§7）。

## 6. 数据流

**单一数据源**：向导没有自己的模型。每一步离开时把结果写回 `toolForm`：

| 向导步 | 写回的字段 |
|---|---|
| ① | `method`、`url_template`、`headers_str` |
| ② | `name`、`description`、`group_name` |
| ③ | `parameter_schema_str`（经 `toolSchema.ts` 的 parse/serialize） |
| ④ | 不改 `toolForm`（测试值不落库） |

- 切到表单 tab：立刻看到向导填的全部内容（同一份 `toolForm`）。
- 切回向导：从 `toolForm` 反读，**不用粘贴框反推**。
- 粘贴框内容不落库、不持久化，它只是输入媒介。

**硬约束（防静默丢数据）**：第 ① 步改了地址后，第 ③ 步的参数清单**不自动重算**，只显示一条「参数可能已过期，点此重新生成」的提示，由用户点击。理由：用户可能已在第 ③ 步手改过参数名/描述/必填，自动重算会静默覆盖。这条有专门的守卫测试。

## 7. HTTP 方法清单收敛

| 位置 | 现状 | 改为 |
|---|---|---|
| 表单创建/编辑下拉（`ToolRegistry.vue:645-650`） | GET/POST/PUT/DELETE | `SUPPORTED_HTTP_METHODS`（含 PATCH） |
| 列表筛选器（`ToolRegistry.vue:206-209`） | 8 个（含 HEAD/OPTIONS） | `SUPPORTED_HTTP_METHODS` **+ 既存数据里真正出现过的方法**（去掉静态的 HEAD/OPTIONS） |
| 运行时（`generic_api.py:169`） | GET 特判 + POST/PUT/PATCH/DELETE；其余返回 `Unsupported method` | 不变 |

`SUPPORTED_HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']`。

筛选器**保留**「合并既有数据里出现过的方法」这一段（`ToolRegistry.vue:208` 的动态部分）：历史库里若存在 HEAD/OPTIONS 等异常数据（后端 `method` 无枚举校验，手工写库或旧版本可能造出来），删掉动态部分会让这些行**再也筛不出来**。因此收敛的是**静态清单**，不是数据驱动的补集。

- 补 PATCH 的依据：运行时真的支持它，而 `curl -X PATCH` 是接口文档里最常见的写法之一；不补则向导解析出的方法**无处安放**。
- 删静态 HEAD/OPTIONS 的依据：运行时对它们返回 `[Error] Unsupported method`，配了也永远失败；仓库既往把「永不生效的死配置留在表单里」视为缺陷（见 langfuse 那次对死配置的处理）。
- 后端 `method` 是裸 `str`（`app/schemas/tool.py:21`）、DB 是 `String(20)`，故本项零迁移。
- 契约测试钉住「前端 `SUPPORTED_HTTP_METHODS` == 运行时支持清单」与「静态清单不含 HEAD/OPTIONS」，防止两边再漂移。

## 8. 后端试跑端点

### 8.1 接口契约

```
POST /api/portal/tools/preview
权限：require_permission("element", "element:system:config_save")   # 与创建/更新工具同权
```

请求体 `SysApiToolPreviewRequest`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | `str` | 默认 `GET` |
| `url_template` | `str` | 必填 |
| `headers` | `Optional[Dict[str, str]]` | 默认 `{}` |
| `parameter_schema` | `Optional[Dict[str, Any]]` | 默认 `{}`；**只用于推导参数类型**，不参与请求 |
| `params` | `Dict[str, Any]` | 试跑测试值；**不落库** |

响应体 `SysApiToolPreviewResponse`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `bool` | 2xx 且请求确实发出 |
| `status_code` | `Optional[int]` | 有响应时才有值 |
| `elapsed_ms` | `int` | 耗时 |
| `body` | `str` | 响应体，截断至 `PREVIEW_BODY_LIMIT = 2000` 字符 |
| `body_truncated` | `bool` | 是否被截断 |
| `error_kind` | `Optional[str]` | `http_error` / `connection` / `timeout` / `invalid_url` / `unsupported_method` / `missing_param` |
| `message` | `str` | 人类可读的原因；`http_error` 时含状态码 |

**不落库**：preview 不写 `sys_api_tools`，不接收 `id`。前端在新建、编辑、改完未保存时都提交草稿 payload，因此只需要这一个端点、一套逻辑。

### 8.2 口径一致性（硬约束）

preview 必须与运行时走**同一段请求内核**，否则会出现「试跑通过、运行时失败」。做法：

```python
# app/services/ai/tools/generic_api.py
@staticmethod
async def _perform_request(
    config: SysApiTool,
    params: Dict[str, Any],
    *,
    raise_on_http_error: bool = True,
) -> tuple[httpx.Response, int, str]:
    """发一次请求的公共内核：路径替换 + 参数落位 + headers + 超时。

    返回 (response, elapsed_ms, final_url)。
    raise_on_http_error=True 时非 2xx 抛 RuntimeError（与运行时既有行为一致）；
    preview 传 False 以取得原始 status 与 body 用于分类展示。
    连接层异常原样抛出。
    """
```

- `_execute_request` 改为调用它，并**保留原有的输出格式化与异常文案**（`[Execution Error] ...`、204/空体返回 `{"success": True, "content": ""}`、JSON 走 `json.dumps(ensure_ascii=False)`）。契约测试钉住这一条。
- preview 用 `raise_on_http_error=False`，自己按 `status_code` 分类。

### 8.3 安全

| 项 | 处理 |
|---|---|
| 权限 | 与创建工具同权（`element:system:config_save`）。**不新增权限、不放宽** |
| SSRF | **不额外限制目标地址**（决策 D3）。依据：能创建工具的人本来就能让 agent 请求同一地址；加限制会造成「试跑通不过、运行时却能跑」的口径分裂，且内网自建 API 会被误伤 |
| 私网提示 | 前端用私网/回环判定（参照 `app/services/ai/browser/browser_policy.py:73-81` 的口径）在试跑面板醒目提示，但**不阻止** |
| 审计 | 无需额外代码：审计中间件（`app/core/middleware.py:115`）自动记录本请求的方法/路径/状态码/耗时/请求体/响应体；`/api/portal/tools/preview` 经前缀匹配命中 `FEATURE_MAP` 的「工具中心」。**实现后已端到端实测**（`test_preview_request_lands_in_audit_trail`）：中间件对全部 `/api/` 请求生效、无路径排除，试跑确实落库并归类正确 |
| 凭据脱敏 | 请求体里的 `headers.Authorization` 等敏感键由 `mask_sensitive_data`（`app/utils/masking.py`）抹为 `******` 后才落审计。**实现时实测发现原文写作时的判断过于乐观**：脱敏名单按**下划线**书写（`api_key`），而 HTTP 头习惯用**连字符**（`X-Api-Key`），两者不匹配 → `Authorization` 只因恰好被 FULL 名单精确命中才没事，`X-Api-Key` / `X-Auth-Token` 会**明文落进审计库**。已修：键名比对前归一化（`-`/空格 → `_`）、子串名单补 `auth_token`/`authtoken`、正则同步接受连字符；`Content-Type`/`Accept`/`X-Request-Id`/`cache_key`/`sort_key` 均未被误伤（参数化用例守着）。**已知边界（未改）**：非 JSON 的 `key: value`（冒号后带空格）文本形态仍不脱敏——既有行为，且不在审计主路径（审计落的是 JSON 请求体或 `&` 分隔的 query），若把值匹配放宽到允许空格会贪婪吞掉后续内容、反过来毁掉审计可用性 |
| 占位符未填 | 若 `url_template` 仍残留 `{xxx}`，直接返回 `error_kind='missing_param'`，**不发请求**（避免把带花括号的 URL 打出去） |
| 超时 | 沿用运行时的 30s |

## 9. 错误处理

| 场景 | 行为 |
|---|---|
| URL 非法 / curl 认不出 | 第 ① 步**原地**给出可操作提示，保留用户原文，不用 toast 打发 |
| 解析出的方法是 HEAD/OPTIONS | 第 ① 步明确提示「平台运行时不支持该方法」 |
| 解析出 POST/PUT/PATCH/DELETE | 第 ① 步橙色警告：平台把本表工具按只读使用，但**请求会真实发出**，请确认是查询语义 |
| 试跑 HTTP 4xx/5xx | `error_kind='http_error'`，展示状态码 + 响应体。文案强调「接口通了但拒绝了」 |
| 试跑连不上（DNS/拒绝/证书） | `error_kind='connection'`；超时 → `timeout`。文案强调「根本没连上」，与上一条的排查方向不同 |
| 占位符未填就试跑 | 前端拦住，后端也返回 `missing_param`（双保险） |
| JSON 参数定义非法 | 沿用现有行为：`switchSchemaMode` 非法时不切走；保存时 `saveTool` 报「参数定义格式错误」 |
| 保存失败 | 沿用现有 toast |

## 10. 测试策略

| 层 | 文件 | 覆盖 |
|---|---|---|
| Node 真跑 | `tests/frontend/tool_import_roundtrip.mjs`（新增） | curl 续行 `\`、`-X`、多 `-H`、`-d` JSON 体、`--url`、`--data`、引号转义、重复 query、无值 query、中文百分号编码、`{param}` 占位；URL 直粘；非法输入返回 error 而非抛异常 |
| Node 真跑 | `tests/frontend/tool_draft_roundtrip.mjs`（新增） | 落位判定（path/query/body）；必填建议（占位符必填、URL 带值的可选 + 默认值）；生成结果能被 `toolSchema.ts` 往返而不丢键 |
| 前端契约 | `tests/frontend/test_api_tool_wizard_contract.py`（新增） | tab 与 4 步存在；**不得出现 `wizardForm`**（单一数据源）；重新解析不覆盖已改参数；占位符未填拦住试跑；私网提示存在；POST 警告存在 |
| 前端契约 | `tests/frontend/test_api_tool_schema_editor_contract.py`（改） | 约 8 条锚点从 `ToolRegistry.vue` 迁到 `ToolParamEditor.vue`，**意图不变**；新增「两处共用同一组件」断言 |
| 前端契约 | `tests/frontend/test_api_tool_method_list_contract.py`（新增） | 表单下拉与筛选器都取自 `SUPPORTED_HTTP_METHODS`；清单 == 运行时支持清单；下拉无 HEAD/OPTIONS、含 PATCH |
| 后端 | `tests/api/portal/test_tool_preview.py`（新增） | 权限门禁（无权限 403）；草稿不落库（调用前后 `sys_api_tools` 行数不变）；`missing_param` 不发请求；错误分类（http_error / connection / timeout / unsupported_method）；响应截断与 `body_truncated`；`params` 不进 `sys_api_tools` |
| 后端 | `tests/api/portal/test_generic_api_runtime_contract.py`（新增） | 抽取 `_perform_request` 后运行时输出**逐字节不变**（2xx JSON、2xx 文本、204、非 2xx、连接失败五种情形的返回值与改动前一致） |
| 反向验证 | — | 每条新守卫做变异测试，逐条字节级还原校验（仓库惯例） |

新增/改动的前端测试基线记为**变动前**的实际数字（执行时先跑一次 `pytest tests/frontend -q` 与 `vue-tsc --noEmit` 取基线，写进 CHECKLIST）。

## 11. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 抽 `ToolParamEditor.vue` 触发 10 条既有契约迁移 | 高 | 只改锚点位置、不改意图；迁移后逐条跑通；新增「两处共用同一组件」断言防止后人再分叉 |
| `generic_api.py` 抽内核碰运行时核心链路 | 高 | `_perform_request` 默认参数保持原行为；专项契约锁定五种情形的输出逐字节不变 |
| 重新解析覆盖用户手改参数 | 中 | 不自动重算 + 守卫测试 |
| curl 解析覆盖不全（真实 curl 写法五花八门） | 中 | 明确只支持 `-X`/`-H`/`-d`/`--data`/`--url`；解析不出时保留原文并给出「请手动填写」的退化路径，**不猜** |
| 向导与表单两套交互让 `ToolRegistry.vue` 继续变胖 | 中 | 向导逻辑全在 `ToolWizard.vue`，`ToolRegistry.vue` 只加 tab 与挂载 |

## 12. 附：设计稿

- `prototypes/2026-10-15-api-tool-wizard.html`：可交互（点 tab 切换、点步骤条跳步）
- `prototypes/shots/wizard-step1.png` ~ `wizard-step4.png`、`form-mode.png`

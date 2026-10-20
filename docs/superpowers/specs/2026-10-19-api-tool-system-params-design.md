# API 工具「系统级参数」设计

- 日期：2026-10-19
- 涉及：`frontend/src/utils/toolSchema.ts`、`frontend/src/components/system/ToolParamEditor.vue`、`frontend/src/components/system/ToolSystemParamEditor.vue`（新增）、`frontend/src/components/system/ApiToolTester.vue`、`frontend/src/components/system/ToolRegistry.vue`、`frontend/src/components/system/ToolWizard.vue`、`app/services/ai/tools/system_params.py`（新增）、`app/services/ai/tools/generic_api.py`、`app/api/portal/endpoints/tools.py`、`app/schemas/tool.py`、相关测试与 `tests/CHECKLIST.md`

## 背景

通用 API 工具的参数定义存在 `sys_api_tools.parameter_schema`，目前支持标准 JSON Schema、扁平 map 和极简描述三种写法。模型看到的参数来自其中的 `properties`（或扁平 map 的普通键），运行时 `GenericApiToolFactory._perform_request` 负责：

1. 替换参数值里的 `{user.xxx}`；
2. 替换 URL 路径参数；
3. 合并 Headers；
4. 按方法把剩余参数放进 GET query 或 JSON body。

现有 `default` 只能做到「模型没传时兜底」：模型显式传一个非空值时，默认值不会覆盖它。对于申请人、操作人、租户这类**必须由平台决定、模型不得修改**的字段，现有参数模型无法表达。

本期不采用 Header 方案。目标是在 body / query / path 中支持一类**模型完全不可见、每次调用由平台强制注入**的参数。

## 需求

管理员在 API 工具编辑器中配置：

- 一个独立的「系统级参数」列表；
- 每项包含参数名、类型、固定值、可选描述；
- 固定值支持 `{user.user_name}`、`{user.id}`、`{user.dept_code}` 等现有用户变量；
- 模型生成的工具 schema 中**完全不出现**这些参数；
- 真正发请求前，平台强制把系统级参数合并进参数对象；
- 模型即使通过某种方式传了同名字段，也不能改变最终值；
- 系统级参数每次调用都必须存在，效果上等同于必填；
- 变量取不到时本次调用明确失败，不发送空值；
- 保存前试跑与智能体运行时使用同一套注入逻辑；
- 不新增数据库表或字段，仍存放在 `parameter_schema` JSON 中。

## 方案对比

| 方案 | 做法 | 评价 |
|---|---|---|
| A. 普通参数加 `x-nanzi-fixed-value` | 标记仍放在 `properties` 内，生成模型 schema 时过滤 | 改动小，但隐藏参数仍污染普通参数语义，后续容易泄漏，**不采用** |
| **B. 顶层 `x-nanzi-system-params` 扩展（选中）** | 系统级参数独立存放，编辑器单独一块区域，运行时强制合并 | 模型天然看不到；零表结构变更；配置和运行时边界清晰 |
| C. 数据库新增 `system_params` 字段 | 独立列、独立 API 字段 | 最显式，但要改 MySQL/PostgreSQL 迁移和保存链路，当前需求不值得 |

采纳 B。系统级参数不进入 `properties`，因此不需要在生成模型 schema 时做「过滤」这种容易出错的步骤。

## 设计

### 数据结构

`parameter_schema` 顶层新增可选键 `x-nanzi-system-params`，值为数组：

```json
{
  "type": "object",
  "properties": {
    "amount": {
      "type": "number",
      "description": "退款金额"
    }
  },
  "required": ["amount"],
  "x-nanzi-system-params": [
    {
      "name": "applicant",
      "type": "string",
      "value": "{user.user_name}",
      "description": "申请人，由平台注入"
    }
  ]
}
```

字段约束：

- `name`：必填，匹配 `[A-Za-z_][A-Za-z0-9_]*`，同一工具内不可重复；
- `type`：`string` / `integer` / `number` / `boolean`，默认 `string`；
- `value`：必填，非空字符串；支持用户变量，也支持普通字面量；
- `description`：可选，仅用于编辑和试跑提示，不发给模型；
- 系统级参数名不能与 `properties`（或扁平 map）里的普通参数重名；
- 若标准形态的顶层 `required` 数组包含系统级参数名，序列化时从模型 `required` 中移除，因为它不在模型可见的 `properties` 里。

未知的其他顶层 `x-*` 扩展继续原样保留，不能被编辑器丢掉。

### 前端：schema 解析与序列化

`frontend/src/utils/toolSchema.ts` 扩展：

```ts
export type SystemParam = {
  name: string
  type: 'string' | 'integer' | 'number' | 'boolean'
  value: string
  description: string
}
```

`ParsedToolSchema` 增加 `systemParams: SystemParam[]`：

- `parseToolSchema` 读取 `x-nanzi-system-params`，归一成 `SystemParam[]`；
- 该键从 `rest` 中移除，避免序列化时重复写入；
- `serializeToolSchema` 在所有普通参数之后写入该扩展；数组为空时删除该键；
- 序列化 `required` 时跳过系统级参数名；
- 扁平形态仍保持扁平形态，系统级参数作为顶层 `x-nanzi-system-params` 存在，不能变成普通参数。

新增 `validateSystemParams(systemParams, params)` 纯函数，返回错误数组：

- 名称为空、格式非法、重复；
- 与普通参数重名；
- 类型不在允许集合；
- `value` 为空。

扩展本身不是数组、条目不是对象等结构错误由后端解析时兜底。`ToolRegistry.saveTool` 在保存前调用该函数；有错误时用现有 toast 提示具体原因，不提交。

### 前端：编辑器

新增 `ToolSystemParamEditor.vue`，由 `ToolParamEditor.vue` 在普通参数列表下方渲染：

```text
系统级参数（模型不可见）                    [+ 添加]
┌──────────┬─────────┬──────────────────────┬──────────────┐
│ 参数名    │ 类型     │ 固定值                │ 操作          │
├──────────┼─────────┼──────────────────────┼──────────────┤
│ applicant│ string  │ {user.user_name} [变量]│ 删除          │
└──────────┴─────────┴──────────────────────┴──────────────┘
```

- 参数名、类型、固定值、描述均可编辑；
- 固定值复用现有用户变量选择器与变量清单；
- 区域下方对空名称、重名、空值给出即时提示（保存时仍逐项校验）；
- 区域标题旁提供说明：这些参数不会出现在模型工具定义里，调用前由平台强制写入；URL 含 `{参数名}` 走 path，否则 GET 走 query、其他方法走 JSON body；变量取不到会失败；
- 不显示「必填」复选框，改为「始终注入」的说明，避免和模型必填语义混淆；
- 普通参数的现有行为（必填、默认值、JSON/可视化切换）不变。

`ApiToolTester.vue`：

- 表单/向导内只保留紧凑的「打开试跑」入口；
- 点击后从右侧滑出抽屉，参数输入、系统级参数提示、请求日志与响应都在抽屉内完成，避免撑长工具编辑表单；
- 不渲染系统级参数的输入框；
- 在参数区下方显示只读提示：`系统级参数：applicant、tenant_id（平台自动注入，无需填写）`；
- 试跑请求只提交普通参数，系统级参数由后端按当前登录用户注入，确保试跑和运行时同口径；
- 试跑结果默认展开「实际发出的请求」，展示最终 method、URL、Query、Body 和脱敏后的 Headers；系统级参数会出现在这里，便于核对；
- 最终 URL、Query、请求体、Headers、响应体和失败信息都提供右上角悬浮复制按钮，复制成功显示「已复制」，失败可重试；剪贴板不可用时回退到 `execCommand`。

### 后端：解析与注入

新增 `app/services/ai/tools/system_params.py`，职责独立于 `generic_api.py`：

- `SYSTEM_PARAMS_KEY = "x-nanzi-system-params"`；
- `SystemParamSpec` 数据类；
- `parse_system_params(parameter_schema)`：兼容 JSON 字符串，校验结构并返回规格列表；非法配置抛 `SystemParamConfigError`；
- `validate_system_params(parameter_schema)`：供保存、试跑接口复用，返回/抛出明确错误；
- `apply_system_params(parameter_schema, params, variables)`：
  - 逐个系统级参数调用现有 `replace_user_placeholders`；
  - 变量缺失继续抛 `UserPlaceholderError`；
  - 强制覆盖 `params[name]`，模型传入的值无效；
  - 对字面量按 `type` 做基础类型转换（integer / number / boolean），转换失败抛配置错误；
  - 返回新的参数字典，不修改调用方对象。

`generic_api.py`：

- `_parse_parameter_schema` 的扁平分支跳过 `x-*` 顶层键，避免 `x-nanzi-system-params` 被当成普通参数暴露给模型；
- `_perform_request` 在 `_apply_user_placeholders` 之后、URL 路径替换之前调用 `apply_system_params`；
- `_perform_request` 支持可选的 `capture` 字典：在请求内核完成用户变量、系统参数、路径和 Headers 处理后，回填最终 method / URL / Query / Body / Headers；成功后优先用 `response.request` 覆盖为 httpx 实际发出的 URL 与 Headers；
- 因为系统级参数不在 `properties` 中，`create_tool` 生成的模型 `args_schema` 天然不包含它们；
- 系统级参数与普通参数重名时由校验直接拦截，避免运行时静默覆盖；保存/试跑/运行时使用同一套 `validate_system_params`。

### 保存与试跑校验

- `POST/PUT /api/portal/tools`：保存前调用 `validate_system_params`；非法配置返回 400，错误信息指出具体参数名和原因；
- `POST /api/portal/tools/preview`：同样先校验；非法配置返回 `error_kind = "invalid_parameter_schema"` 和可读 message；
- 试跑现有的「URL 占位符没填」预检必须把系统级参数排除：它们本来就不由试跑者填写，会由后端注入；
- 试跑继续使用 `_perform_request`，显式传入当前登录用户；系统级参数注入、路径替换、query/body 落位与运行时完全一致；
- `SysApiToolPreviewResponse` 增加 `request` 快照（method / url / headers / query / body）；Headers、Query、Body 与 URL 中的敏感信息按 `mask_sensitive_data` 脱敏；
- `SysApiToolPreviewResponse.error_kind` 增加 `invalid_parameter_schema`，`ApiToolTester` 增加对应展示分支。

### 错误处理

- 变量缺失：`UserPlaceholderError`，试跑返回 `missing_user_variable`，运行时本次工具调用失败；
- 配置结构非法：保存返回 400；试跑返回 `invalid_parameter_schema`；不会带着半套参数发请求；
- 类型转换失败：视为配置错误，明确提示是哪个系统级参数；
- 普通参数、Headers、默认值逻辑保持现状，不因本功能改变。

## 非目标

- 不做全局可复用的「系统参数库」，本期只在单个工具内配置；
- 不支持 Header 系统级参数，Header 继续用现有 `{user.xxx}` 替换；
- 不支持 object / array 类型的系统级参数，只支持标量；
- 不改变普通参数 `default` 的现有语义；
- 不新增数据库表或迁移。

## 测试计划

### 后端

- `create_tool` 生成的模型 schema 不包含系统级参数；
- 系统级参数注入到 JSON body，且覆盖模型传入的同名字段；
- GET 场景进入 query；URL 含 `{name}` 时进入 path；
- `{user.user_name}` 按当前用户替换；变量缺失抛 `UserPlaceholderError`；
- 字面量按 integer / number / boolean 转换，非法值给出配置错误；
- 保存/试跑校验拒绝重名、空值、非法名称、非法类型；
- 试跑接口返回 `invalid_parameter_schema` 时不再发 HTTP 请求。

### 前端

- `parseToolSchema` / `serializeToolSchema` 往返保留系统级参数和未知 `x-*` 扩展；
- 系统级参数不会出现在 `params` 或模型 `properties` 中；
- 标准形态下系统级参数名从 `required` 中移除；
- 扁平形态仍保持扁平，扩展不被当成普通参数；
- 编辑器能增删改系统级参数，变量选择器能写入用户变量；
- 保存前校验能拦截重名、空值、非法名称；
- 试跑区显示「平台自动注入」提示且不渲染系统级参数输入框；
- `vue-tsc --noEmit` 通过。

## 验收标准

1. 在工具编辑器中添加系统级参数 `applicant = {user.user_name}`，保存成功；
2. 模型工具定义里看不到 `applicant`；
3. 模型调用时只提供普通参数，最终 body/query/path 中一定包含当前用户的 `applicant`；
4. 即使模型试图传 `applicant`，最终值仍是当前用户；
5. 当前用户缺少 `user_name` 时调用失败并给出明确提示；
6. 试跑与运行时行为一致；
7. 老工具没有该扩展时行为完全不变。

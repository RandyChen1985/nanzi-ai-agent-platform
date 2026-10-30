# 工具管理 JSON 导出/导入设计（tool bundle v1）

- 日期：2026-10-30
- 范围：系统配置 → 工具管理（API 工具 / 脚本工具）的 JSON 导出与导入
- 目标：把一个或一批工具导出成 JSON 文件，发给其他人导入，减少「照着截图手工重建」

## 1. 问题与边界

现状：`sys_api_tools`（API 工具）与 `sys_script_tools`（脚本工具）各有独立 CRUD，没有任何导出/导入能力；
技能管理已有 `GET /{skill_id}/export` + `/import` 的先例，但工具配置更小、更适合纯 JSON。

不做：
- 带真实凭据导出、加密包、zip；
- 资源广场/场景模板分发；
- 工具版本号与回滚；
- 新表、新列、数据库迁移。

## 2. 文件契约

```json
{
  "schema": "nanzi.tool.bundle",
  "schema_version": 1,
  "exported_at": "2026-10-30T21:50:00+08:00",
  "exported_by": "chenxiaolong",
  "tools": [
    { "kind": "api", "name": "ttt", "method": "POST",
      "url_template": "https://api.example.com/v1/orders/{order_id}/refund",
      "headers": { "Content-Type": "application/json",
                   "Authorization": "${CREDENTIAL:authorization}" },
      "parameter_schema": {}, "signing_enabled": false, "signing_algorithm": "hmac-sha256",
      "group_name": "test", "is_active": true,
      "requires_credentials": ["Authorization"] },
    { "kind": "script", "name": "send_feishu_message", "display_name": "发送飞书消息",
      "source": "custom", "language": "python", "code": "…",
      "parameter_schema": {}, "requirements": [], "timeout_seconds": 30,
      "group_name": null, "is_active": true }
  ]
}
```

约定：
- `schema` 必须为 `nanzi.tool.bundle`；`schema_version` 必须 ≤ 1，更高版本直接拒绝并提示升级平台。
- 一个文件可同时包含两类工具（`tools` 数组按 `kind` 区分）。
- `api` 条目不导出 `id`、`signing_key_id`、`signing_secret_encrypted`、时间戳。
- `script` 条目不导出 `id`、`is_overridden`、`is_fallback`、时间戳；`source` 一律写 `custom`（内置脚本导出后按自定义工具流通）。
- `requires_credentials` 只用于界面提示，导入端不依赖它（以 headers 里的占位符为准）。

## 3. 凭据处理（永不导出）

- header 名大小写不敏感匹配：`authorization`、`cookie`、`proxy-authorization`、`www-authenticate`，
  或包含 `token` / `secret` / `password` / `api-key` / `apikey` / `credential` / `signature`。
- 命中即把值替换为 `${CREDENTIAL:<header 名小写>}`，原值绝不写进文件。
- `Content-Type`、`Accept`、`X-Tenant-Id` 等业务 header 保留原值。
- 签名密钥（`signing_secret_encrypted`）永不导出，`signing_key_id` 不导出。
- 脚本 `code` 为明文源码，导出前在界面提示「请自行确认源码里没有硬编码密钥」；平台不做自动改写（无法可靠识别）。

## 4. 导入冲突与落库语义

名字唯一性沿用平台现有口径（`_assert_name_available`）：不得与其他 API 工具、脚本工具、MCP 工具、
平台内置静态工具名、内置脚本名冲突。判重按名字跨类型进行，不按 `(kind, name)`。

预检逐条给出状态（不落库）：
- `new`：名字可用，可按原名新建；
- `conflict`：名字已被占用。`can_overwrite=true` 表示同类型且非内置（可覆盖）；
  `can_overwrite=false` 表示跨类型 / MCP / 平台内置工具 / 内置脚本（只能另存或跳过）。
  两种 conflict 都带 `suggested_name`（`name_2`、`name_3`… 且全平台不冲突）；
- `invalid`：该条字段校验失败（如 API 工具缺 `url_template`、脚本缺 `code`）。

每条决策：`create`（按原名新建，仅 new 可用）/ `skip`（默认）/ `overwrite` / `rename`（需 `rename_to`）。
没有给出决策的条目一律跳过，绝不默认写入。

覆盖语义（关键）：
- API 工具：更新 `description/method/url_template/headers/parameter_schema/signing_enabled/signing_algorithm/group_name/is_active`；
  **保留**目标平台已配置的签名密钥与 `signing_key_id`；headers 中若导入值是 `${CREDENTIAL:x}` 占位符，
  而目标行已有同名 header 的真实值，则保留目标值（避免覆盖把正在用的凭据冲掉）。
- 脚本工具：覆盖 `display_name/description/code/parameter_schema/requirements/timeout_seconds/group_name/is_active`；
  目标是内置脚本时预检即为 `invalid`，只能跳过或另存。
- 覆盖不改名（决策里 `overwrite` 不接受 `rename_to`）。

字段校验直接复用 `SysApiToolCreate` / `SysScriptToolCreate`，并额外拒绝空 `url_template`
（新建表单由前端拦空地址，导入没有表单可依）。

事务：整个导入一次事务。任一决策非法（索引越界、动作非法、create 撞名、另存名冲突、目标是内置脚本）→ 400 且一条不落。
端点本身不 commit，由 `get_db_session` 依赖统一提交/回滚。

## 5. 运行时兜底

`GenericApiToolFactory._perform_request` 在 header 替换之后、发请求之前检查：若 header 值仍以 `${CREDENTIAL:`
开头，抛 `UnresolvedCredentialError`（含工具名与 header 名）。试跑端把它归为 `error_kind="missing_credential"`。
理由：导出的工具可能被直接启用，必须给出可懂的错误而不是把占位符原样发给对方服务。

## 6. 界面

- API 工具 tab（`ToolRegistry.vue`）：标题行「分组管理」左侧加「导出 JSON（N）」「导入 JSON」。
  N = 勾选数；未勾选时 = 当前筛选结果数；为 0 时禁用。
- 脚本工具 tab（`ScriptToolList.vue`）：「+ 添加脚本工具」旁加同样两个按钮；勾选含内置脚本时，
  按 `source: custom` 导出，导出后用 toast 说明「其中 N 个内置脚本按自定义工具导出」。
- 导入弹窗（新组件 `ToolBundleImportModal.vue`）：选择/拖入文件 → 调预检 → 表格逐条（名称、类型、状态、动作）
  → 确认导入 → 结果摘要（新建/覆盖/跳过/失败原因）。
- 列表徽标：API 工具行 headers 值含 `${CREDENTIAL:` 时显示琥珀色「待补凭据」，hover 列出缺失的 header 名。
  不加数据库列，直接由现有字段推导。

## 7. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portal/tools/export?kind=api\|script\|all&ids=...` | 返回 attachment JSON，文件名 `nanzi-tools-YYYYmmdd-HHMM.json` |
| POST | `/api/portal/tools/import/precheck` | body `{bundle}`，只解析不落库 |
| POST | `/api/portal/tools/import` | body `{bundle, decisions:[{index, action: create\|skip\|overwrite\|rename, rename_to?}]}` |

权限：三个端点都用 `element:system:config_save`（导出的是配置，与写同权）。
文件上限 8MB，超限 413。审计由现有访问日志中间件按 `/api/portal/tools` 前缀自动记录，不新增审计表。

## 8. 测试

- 后端（`tests/api/portal/test_tool_bundle_contract.py`，`no_infrastructure`）：脱敏规则、bundle 构建、
  parse 分类、决策校验、header 合并、运行时 `UnresolvedCredentialError`、路由与权限契约。
- 前端（`tests/frontend/test_tool_bundle_ui_contract.py`）：`toolBundleApi` 三个方法、两个 tab 的按钮、
  导入弹窗文件存在、待补凭据徽标。
- `vue-tsc --noEmit` 通过。

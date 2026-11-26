# 平台技能 / 平台 MCP 页签开放为「登录可见 + 只读」设计

- 日期：2026-11-26
- 状态：已实现（2026-11-26，实现说明与验证结果见 `tests/CHECKLIST.md` 同日条目）
- 需求来源：用户
  > 「平台技能页签，改一下，没权限也显示，只是只能只读模式。平台 MCP 页签也一样，没权限也显示，只是只能只读模式。」

## 1. 目标与非目标

### 目标

1. 技能工作台的「平台技能」页签、MCP 工具集的「平台 MCP」页签，对**所有能进入页面的登录用户**可见，不再要求 `element:skills:admin` / `role == 'admin'`。
2. 无写权限时页面进入**只读模式**：可浏览，所有写入口（新建、编辑、删除、导入、启停、同步、发布、测试、凭据、审计、定义变更）**直接隐藏**（延续既有「按钮级隐藏」风格，不置灰）。
3. 只读可看的内容：
   - 技能：平台技能列表（分组、状态、描述、`forbidden` 标记）+ 详情抽屉（SKILL.md 预览、文件树、目录内文本文件只读查看）。
   - MCP：平台服务列表（名称/备注/分组/状态/工具数/发布数/同步时间）+ 工具清单（工具名、描述、参数、发布/可用状态）。
4. 管理员视角行为不变。

### 非目标（本次不做）

1. **不改菜单入口**：路由仍是 `menu:skills_management` / `menu:mcp_management`（`frontend/src/router/index.ts:291,297`）。页签可见性只作用于「已能进入页面的人」——用户模式下普通用户本来就能进；专业模式由管理员决定是否授予菜单。
2. **不改 `_ensure_server_control_access`**（`app/api/portal/endpoints/mcp.py:479-486`）：它有 10 处复用（执行/发布/凭据/清理/同步/日志），改它会连带放开写操作。改为新增只读专用判定。
3. **不放开 MCP 出站日志、工具执行测试、用例 curl、绑定智能体用量、定义变更**：全部保持 admin。
4. **不放开技能整包导出 / 单文件二进制下载 / 显式绑定反查**：`GET /{skill_id}/export`、`GET /{skill_id}/download`、`GET /bindings` 保持 admin。
5. **不做独立只读权限码**：本需求的口径是「登录即可读」，避免在角色树里再加一层需要配的开关。

## 2. 权限口径（改后）

| 能力 | 判定 | 说明 |
|---|---|---|
| 进入技能工作台 / MCP 工具集页面 | `menu:skills_management` / `menu:mcp_management` | 不变 |
| 看到「平台技能」页签 | 非 `personalOnly`（登录即可） | 个人中心/嵌入式入口 `personalOnly=true` 不出现 |
| 平台技能列表与详情只读 | 登录即可（后端 `require_api_key`） | 个人技能侧逻辑不动 |
| 平台技能写操作 | `element:skills:admin` | 不变 |
| 「待审核」「已发布」页签 | `element:skills:admin` | 只读意义不大，保持管理员专属 |
| 看到「平台 MCP」页签 | 非 `personalOnly`（登录即可） | 默认落点仍是「我的 MCP」 |
| 平台 MCP 列表与工具清单只读 | 登录即可（脱敏后） | 后端去掉 `role != admin → 403` |
| 平台 MCP 写操作 | `element:mcp_management:*` + 平台服务仍需 admin | 不变 |
| 审计日志 / 定义变更页签 | `role == admin` | 保持 |

## 3. 后端改动

### 3.1 技能（`app/api/portal/endpoints/skills.py`）

| 接口 | 现状 | 改法 |
|---|---|---|
| `GET /{skill_id}`（详情，约 578 行） | `skill_platform_admin` | → `require_api_key` |
| `GET /{skill_id}/files?path=`（读目录内文本文件，约 616 行） | `skill_platform_admin` | → `require_api_key`（保留 `EDITABLE_TEXT_EXTENSIONS` 白名单与 `validate_secure_skill_path`） |
| `GET /{skill_id}/preview`（约 480 行） | `skill_platform_admin`，但 docstring 已写「仅需 API Key」 | → `require_api_key`，消除文档与实现矛盾 |
| `GET /stats`、`GET ""`（列表） | `require_api_key` | 不动 |
| `GET /{skill_id}/export`、`/download`、`GET /bindings` | `skill_platform_admin` | **保持** |
| 其余写操作（group/create/files 写/upload/delete/import/rename/toggle/发布审核） | `skill_platform_admin` | **保持** |

加固：`validate_secure_skill_path`（约 134-166 行）目前用 `os.path.abspath` 校验，改为 `os.path.realpath`（与发布快照 `skill_publication_service.py` 一致），防止软链越界读。

### 3.2 MCP 列表脱敏（`app/api/portal/endpoints/mcp.py`）

1. `GET /servers`（约 928 行）去掉 `scope != "personal" and role != "admin" → 403`，改为登录可读；`scope` 仅允许 `global` / `personal`（非法值 400）。
2. 在 `list_mcp_servers` 的响应组装循环（约 1003-1019 行）对**非 admin** 追加脱敏：

| 字段 | 非管理员处置 | 理由 |
|---|---|---|
| `sse_url` | 置空字符串 | 内网地址，可能含 URL 里的临时 token；先例：`/api/portal/secrets/platform-targets` 也不返回 |
| `masked_auth_headers` | 置空 dict | 暴露鉴权 Header 名与形态 |
| `authorization_configured`、`auth_headers_configured` | 保留布尔 | 只表示「是否配了鉴权」，对排查有用，无泄露 |
| `owner_user_name`、`owner_display_name` | 置 None | 同事账号/真名 |
| `last_probe_error` | 置 None | 远端异常原文可能带主机名 |
| `user_assertion_header` / `_audience` / `_key_id` / `_issuer` | 置 None | 配置细节 |
| `user_assertion_enabled`、`credential_mode` | 保留 | 布尔/枚举，前端徽标要用 |
| `remark`、`group_name`、`enabled_status`、`server_name`、`tool_count`、`published_tool_count`、`stale_tool_count`、`last_sync_at`、`last_probe_at`、`last_probe_status`、`probe_failure_count`、`pending_change_count`、`id`、`scope` | 保留 | 只读要看的全部信息 |

3. 新增只读判定 `_ensure_server_read_access(server, user)`：`global` → 放行登录用户；`personal` → 仅 owner 或 admin。**只给** `GET /servers/{server_id}/tools`（约 1992 行，现 2003 走 control access）使用。
4. 其余接口保持不动：`/servers/{id}/usage`、`/servers/{id}/outbound-logs`、`/outbound-logs`、`/tools/{id}/curl`、`/changes`（`mcp_changes.py`）。

工具清单字段：`usage_count` 保留（纯计数，不含用户身份）；`credential_params` 保留（只暴露参数名）。

## 4. 前端改动

### 4.1 技能页（`frontend/src/views/SkillsManagement.vue`）

判定变量：
- `canManagePlatformSkills`（`element:skills:admin`）**保持为唯一写判据**。
- `canAccessPlatformWorkspace`（`!personalOnly && canManagePlatformSkills`）**语义收窄为「待审核 / 已发布两个管理分区」的准入**。
- 新增只读判据：直接使用 `!props.personalOnly`。

| 位置（约行） | 现状 | 改法 |
|---|---|---|
| 262-266 首屏落点 | 按 `canAccessPlatformWorkspace` | `!props.personalOnly ? 'global' : 'personal'` |
| 278-286 `watch(canAccessPlatformWorkspace)` | 不可用时强推 `personal` | 不可用时只把 `review` / `publications` 回落到 `global`，**不得**回 personal |
| 2210-2213 一级 Tab 行容器 | `!personalOnly && canManagePlatformSkills` | `!personalOnly` |
| 平台技能 tab | `v-if="canManagePlatformSkills"` | 去掉 v-if |
| 待审核 / 已发布 tab | `canManagePlatformSkills` | 保持 |
| 「单 Tab 胶囊」特例（2269-2278 行） | `!personalOnly && !canManagePlatformSkills && …` | 删除该特例 |
| 左侧 aside / 移动端 chips（2436、2448 行） | `activeWorkspaceTab === 'personal' && canManagePlatformSkills` | `activeWorkspaceTab === 'personal'` |
| 统计按钮（2548 行） | `!personalOnly` | 保持（后端 `/stats` 登录可读） |
| 新建技能 / 导入 / 空白新建（2560-2609 行） | 无条件 | 加 `activeScope === 'personal' \|\| canManagePlatformSkills` |
| 空态「新建 / 导入」（2658-2673 行） | 无条件 | 同上；只读平台视图文案改为「当前账号仅可浏览平台技能」 |
| 卡片启停开关 + 「⋯」按钮（2754-2778 行） | 无条件 | `skill.scope === 'personal' \|\| canManagePlatformSkills` |
| 卡片「绑定 N」按钮（2836-2847 行） | `!personalOnly && skill.scope !== 'personal'` | 再叠 `canManagePlatformSkills` |
| 表格「绑定」列与单元格（2862、2909-2925 行） | `!personalOnly && activeScope === 'global'` | 再叠 `canManagePlatformSkills` |
| 表格状态列（2929-2945 行） | toggle 无权限条件 | 保留「已启用/已禁用」文字，仅 toggle 加 `skill.scope === 'personal' \|\| canManagePlatformSkills` |
| 表格删除按钮（2971-2979 行） | 无条件 | 同上 |
| 「配置」按钮文案 | 恒为「配置」 | 无写权限时显示「查看」 |
| 右键上下文菜单容器（4082-4086 行） | `contextMenu.show` | 再叠 `activeSkillScope === 'personal' \|\| canManagePlatformSkills` |
| 文件树空白区右键（3658 行） | `@contextmenu.self` | 只读时不绑定/直接 return |
| `handleContextMenu` / `handleEmptyAreaContextMenu` | 无守卫 | 函数入口加只读 early return |
| 「⋯」菜单内导出/重命名/分组/删除（4176-4231 行） | 无权限条件 | 逐项加 `scope === 'personal' \|\| canManagePlatformSkills`（防其它路径误开） |
| 抽屉「编辑/预览」切换（3481-3504 行） | 无权限条件 | 只读时隐藏切换、强制 `editorMode='preview'` |
| 抽屉「保存」按钮（3506-3519 行） | 无权限条件 | 加只读隐藏 |
| `selectFileForEdit`（1505-1517 行） | 非 .md 强制 `editorMode='edit'` | 只读时恒 `preview` |
| 抽屉「新建文件 / 上传脚本」（3617-3629 行） | 无权限条件 | 加只读隐藏 |
| `SkillFileTree` 删除按钮 | 组件内渲染 | 组件加 `readonly` prop，只读时不渲染删除按钮与右键 |
| `saveFileContent` / `handleEditorKeydown` | 无守卫 | 函数入口加只读 early return |
| `fetchSkillBindings`（337-347 行） | `personalOnly` 早退 | 再加 `!canManagePlatformSkills` early return（后端 admin-only） |
| `onMounted`（2105-2112 行） | 一个 if 包住 4 个请求 | 拆两层：`fetchSkills()` 只要 `!personalOnly`；bindings/queue/publications 仍按 `canManagePlatformSkills` |

文案同步：
- 帮助弹窗（3113/3137/3161 行）与 `SkillFlowGuideBanner.vue` 第 4 步（75 行）：「平台技能」入口不再限定管理员，仅「待审核」「智能体中心」保留管理员文案。
- 流程横幅 `requiresPlatformAdmin` 只留第 3 步（待审核）与第 5 步。

### 4.2 MCP 页（`frontend/src/views/McpManagement.vue`）

| 位置（约行） | 现状 | 改法 |
|---|---|---|
| 68-70 `activeScope` 初始化 | 非 admin 落 personal | **保持**（默认落「我的 MCP」） |
| 82-100 `fetchServerCounts` | 非 admin 短路 | 去掉短路，两条都请求 |
| 249-251 一级 Tab 行 | `!personalOnly && isAdmin` | `!personalOnly` |
| 平台 MCP tab（254 行） | `v-if="isAdmin"` | 去掉 |
| 平台 registry 分支（388-394 行） | `v-else-if="isAdmin"` | `v-else` |
| 审计 / 定义变更 tab 与面板 | `isAdmin` | 保持 |
| 新增只读提示条 | 无 | `!personalOnly && !isAdmin && activeScope === 'global'` 时显示「平台 MCP 为只读，维护请联系管理员」 |
| 新增 `watch` 兜底 | 无 | 非 admin 而 `activeScope` 为 `audit`/`changes` 时复位到 `global` |
| `handleBannerAction`（177-183 行） | 直接 `openAddModal` | 只读时改为提示「无创建权限」，不打开向导 |

### 4.3 MCP 注册表（`frontend/src/components/system/McpServerRegistry.vue`）

`canSave`（90-93 行，global 时 `role === 'admin'`）**保持为唯一只读开关**；只读徽标（3355-3357 行）沿用。

| 位置（约行） | 现状 | 改法 |
|---|---|---|
| `selectServer` 内 `fetchServerUsage`（3212 行，函数 2626-2637） | 每次选中必发 → 403 弹错 | `canSave` 为假时跳过（用量恒 null，绑定智能体行自然不渲染） |
| `selectServer` 内 `fetchDefinitionChanges`（3211 行，函数 536-548） | 静默 403 | `canSave` 为假时跳过 |
| `fetchTools` | 依赖后端放开 | 后端放开后正常 |
| 定义变更提示条（3579）、卡片紫色徽标（3777）、「去发布 →」（3801）、服务级变更条（4045）、「定义已变更」chip（4136） | 仅 `canSyncMcp` | 叠 `canSave`（避免有码非 admin 点进抽屉 403） |
| 卡片启停 `Switch`（3704-3710 行） | 只读时可见且置灰 | 加 `v-if="canSave"`，保留状态圆点；`:disabled` 表达式**不改**（有契约断言） |
| 卡片「⋯」按钮（3711-3722 行） | 只读时点开是空菜单 | 加 `v-if="canSave"` |
| 服务禁用文案（3884 行） | 「工具暂不可测试、发布或下线」 | 只读语境改为「服务已禁用，工具不可用」 |
| 工具行参数 chip | 只显示参数名 | `title` 属性带完整 `parameter_schema`（不新增弹窗/面板） |

既有 12 处 `v-if="canSave"`（新建、全选、调用日志、同步、清理失效、变更抽屉、批量操作、工具 checkbox、测试/发布按钮、菜单项）无需改动，本次首次真正生效。

## 5. 测试计划

### 需要改的既有测试

| 文件 | 断言 | 改法 |
|---|---|---|
| `tests/frontend/test_mcp_management_scope_contract.py` | 12/16/17/21 行等 | 语义反转：平台页签对登录可见、registry 走 `v-else`、计数无条件请求；新增只读提示条断言 |
| `tests/frontend/test_personal_resource_tabs_contract.py` | 44/46/49/53 行 | 技能 tab 行改 `!personalOnly`；删除单 Tab 特例断言；aside 条件去掉 admin；MCP tab 行改 `!personalOnly` |
| `tests/frontend/test_skill_management_feedback_contract.py` | 174/186/189/190-196/206/240-244 行 | 按新落点、新 watcher（回落 `global`）、onMounted 拆分、帮助文案调整断言 |
| `tests/api/portal/test_mcp_management_auth.py` | `test_non_admin_cannot_list_global_mcp_servers`、`test_non_admin_cannot_read_global_server_tools` | 反转为「可读 + 字段脱敏」；usage / outbound-logs / 他人 personal 仍 403 的用例保留 |
| `tests/test_skills_management.py` | 只读列表用例 | 补路由依赖断言 |

### 需要新增的测试

1. `tests/api/portal/test_mcp_readonly_access.py`：非 admin 可列 global 且 `sse_url`/`masked_auth_headers`/`owner_*`/`last_probe_error` 被清空；非 admin 可读 global 工具清单；非 admin 读他人 personal 仍 403；usage / outbound-logs / curl 仍 403。
2. 技能后端依赖契约：`GET /{skill_id}`、`/{skill_id}/files`、`/{skill_id}/preview` 为 `require_api_key`；`/export`、`/download`、`/bindings` 仍为 `element:skills:admin`。
3. 前端只读契约：无 `element:skills:admin` 时平台技能写入口均为隐藏（源码级断言 `v-if` 条件）；MCP 只读提示条存在且 `selectServer` 的 usage/changes 请求受 `canSave` 保护。

### 验证命令

- `pytest --confcutdir=tests/frontend tests/frontend -q`
- `pytest tests/api/portal/test_mcp_management_auth.py tests/api/portal/test_mcp_readonly_access.py tests/test_skills_management.py -q`
- `cd frontend && npx vue-tsc --noEmit`
- 更新 `tests/CHECKLIST.md`

## 6. 风险与取舍

1. **平台 MCP 列表从 403 变为可读**：新增「所有登录用户可枚举平台集成」这一事实，已通过字段脱敏缓解；工具参数 schema 属 MCP 公开契约，保留。
2. **技能脚本/配置可读**：按用户决定放开全部文本类白名单（`.md/.txt/.py/.js/.ts/.json/.yaml/.yml/.ini/.conf/.sql/.sh`）。平台技能目录内若出现密钥/内部地址，将对该页所有登录用户可见——需要运维侧保证平台技能不含机密。
3. **布局变化**：技能页非管理员从「单 Tab 胶囊」变为「一级 Tab + 左侧 aside」，属可见的界面结构变更。
4. **已存在的口径冲突**：非 admin 角色即使被授予 `element:mcp_management:*`，`canSave` 仍为假（平台服务维护需 admin）。本次维持该行为，只在只读提示条里说明。
5. **性能**：技能列表/详情对全部登录用户开放后 QPS 上升（列表逐目录解析、详情递归扫盘）。本次不加缓存，后续如需要再评估短 TTL 缓存。

## 7. 验收清单

- [ ] 普通用户（无 `element:skills:admin`）进入技能工作台：默认落「平台技能」，可浏览列表与详情抽屉，看不到新建/导入/编辑/删除/启停/绑定/右键菜单/保存/上传。
- [ ] 普通用户看不到「待审核」「已发布」页签，且相关接口不被调用。
- [ ] 普通用户进入 MCP 工具集：默认落「我的 MCP」，能看到「平台 MCP」页签，可浏览服务与工具清单，页面显示「只读」提示；选中服务不再弹「获取 MCP 使用情况失败」。
- [ ] 普通用户看不到平台 MCP 的新建/编辑/删除/同步/测试/发布/调用日志/定义变更入口，审计与定义变更页签不可见。
- [ ] 管理员在两个页面的行为与改动前一致（含 Echo 测试、批量发布、凭据、日志）。
- [ ] 个人中心 / 嵌入式个人资源面板（`personalOnly`）行为完全不变。

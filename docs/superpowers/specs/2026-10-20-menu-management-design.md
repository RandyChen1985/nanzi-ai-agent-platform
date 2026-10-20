# 菜单管理（动态菜单 + 外链菜单）设计

- 日期：2026-10-20
- 状态：已评审通过，进入实施
- 关联迁移：`db-prod/V176-create_agent_menus.sql`、`db-prod-pg/V77-create_agent_menus.sql`

## 1. 背景

左侧菜单当前写死在 `frontend/src/views/Dashboard.vue` 的 `menuGroups` 常量里（7 个分组、25 个菜单项），
图标是模板里 20 多个 `v-else-if` 内联 SVG。运行时只有"可见性"是动态的：`hasMenuPerm()` 按
`user_info.permissions.menus` 里的 `menu:*` 码过滤，admin 全放行。

同一份菜单实际上有 4 份互不相干的清单，靠人工同步：

| 清单 | 位置 |
| --- | --- |
| 侧栏菜单（渲染源） | `frontend/src/views/Dashboard.vue` `menuGroups` |
| 前端路由（path/name/meta.perm） | `frontend/src/router/index.ts` |
| 权限树（角色管理可勾选项） | `frontend/src/constants/permissions.ts` `MENU_TREE` |
| 默认落地优先级 | `frontend/src/router/index.ts` `MENU_HOME_CANDIDATES` |
| 后端权限显示名 | `app/services/permission_service.py` `exact_permission_mapping` |

已知缺陷：

1. 权限码复用——「Token 统计」与「审计日志」共用 `menu:system:audit`；「运营分析」与「知识库管理」共用
   `menu:knowledge_management`，两组都无法单独授权。
2. 侧栏分组与权限树归属矛盾——「审计日志」侧栏在「日志&TOKEN分析」组，权限树却挂在 `menu:system` 下。
3. 任何菜单改名/排序/加外链都要改代码发版。
4. 前端契约测试直接断言菜单字符串字面量，改结构必撞测试。

## 2. 目标与非目标

### 目标

1. 左侧菜单全部入库，由 `ai_agent_menus` 单表驱动；侧栏、权限树、后端显示名共用同一数据源。
2. 系统管理下新增「菜单管理」页（角色管理之后），支持改名、图标、排序、显隐、启停、内嵌/新标签外链。
3. 支持外链菜单，`open_mode` 可选「新标签打开」与「站内 iframe 内嵌」。
4. 权限判定口径、后端 403 行为、现有用户可见菜单**保持不变**。
5. 拆掉两处复用的权限码，改为独立码，并用迁移复制旧授权，升级前后可见性一致。
6. `element:*` 功能点的归属与中文名一并入库，权限树完全由接口驱动（菜单管理页只读展示）。

### 非目标

1. 不改动任何业务页面的内容与接口权限校验（`require_permission("menu", ...)` 全部保持原样）。
2. 不做多语言、不做多租户菜单差异。
3. 不提供"新建内置页面菜单"的能力：页面类菜单的 `path` 必须来自已注册路由白名单。
4. 不允许删除内置菜单（只能停用/隐藏）；外链菜单可自由新增删除。
5. 不引入菜单草稿/发布流，保存即生效。

## 3. 数据模型

单表 `ai_agent_menus`，`parent_id` 自引用，`node_type` 区分四类节点。

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT PK AUTO_INCREMENT | |
| `parent_id` | BIGINT NULL | 分组为空；菜单项指向 group；element 指向所属 page |
| `node_type` | VARCHAR(16) NOT NULL | `group` / `page` / `external` / `element` |
| `code` | VARCHAR(64) NOT NULL UNIQUE | 不可变业务键；seed 幂等与外链定位都用它 |
| `permission_code` | VARCHAR(128) NULL UNIQUE | 授权码；公开菜单为 NULL |
| `label` | VARCHAR(64) NOT NULL | 显示名 |
| `icon` | VARCHAR(32) NULL | 内置图标白名单 key（见 6.2） |
| `path` | VARCHAR(128) NULL | `page` 专用，必须在路由白名单内 |
| `active_route_names` | VARCHAR(255) NULL | 逗号分隔的路由 name，用于详情页等"非精确 path"场景的高亮（如 `metadata/:id` 也点亮「元数据管理」） |
| `url` | VARCHAR(512) NULL | `external` 专用 |
| `open_mode` | VARCHAR(16) NULL | `new_tab` / `embed` |
| `sort_order` | INT NOT NULL DEFAULT 0 | 同级内升序 |
| `enabled` | TINYINT(1) NOT NULL DEFAULT 1 | 停用后不下发 |
| `desktop_only` | TINYINT(1) NOT NULL DEFAULT 0 | 移动端侧栏隐藏（沿用现有语义） |
| `is_landing` | TINYINT(1) NOT NULL DEFAULT 0 | 默认落地页；全局至多一条 |
| `builtin` | TINYINT(1) NOT NULL DEFAULT 1 | 内置项不可删除、不可改 `path`/`code` |
| `description` | VARCHAR(255) NULL | 备注 |
| `created_at` / `updated_at` | DATETIME NOT NULL | |

索引：`(parent_id, sort_order)`、`(permission_code)`、`(node_type)`。

### 为什么 `code` 与 `permission_code` 分开

- `code` 是身份：改了会丢配置、破坏 seed 幂等与外链定位。
- `permission_code` 是授权口径：外链可以按需调整 slug，同时迁移授权行。
- 内置 `page` / `element` 两者同值（如 `menu:dashboard`、`element:user:edit`）。
- 分组 `code='group:agent_platform'`，`permission_code=NULL`；「系统管理」分组例外，保留 `menu:system`。
- 外链 `code='ext:<uuid8>'`，`permission_code='menu:ext:<slug>'`。

### 分组与权限树的父子关系（已拍板）

采用**权限树跟随侧栏分组**，不加第二套父子关系。唯一视觉变化：「审计日志」在权限树中从
`menu:system` 下移到「日志&TOKEN分析」组下。授权数据按 id 平铺存储，判定逻辑与 403 行为不变。

分组节点在权限树中只作展示容器，不可单独勾选；`menu:system` 的历史授权行保留不动。

## 4. 种子数据与迁移

### 4.1 单一内置定义

`app/services/menu_seed.py` 里的 `BUILTIN_MENU_SEED` 是唯一的代码内定义（Python 结构）。
两个消费方：

1. `scripts/gen_menu_seed_sql.py` 从它生成 MySQL / PostgreSQL 两套幂等迁移 SQL；
2. `MenuService` 在**表尚未建立**时用它兜底返回 nav，保证"代码先上线、迁移后执行"期间侧栏不空。

禁止手工维护第二份菜单常量；SQL 由脚本生成后纳入版本控制。

### 4.2 迁移内容（约 91 行）

| 类型 | 数量 | 来源 |
| --- | --- | --- |
| `group` | 7 | 现有 `menuGroups[].title`（含 `menu:system`） |
| `page` | 26 | 现有 25 项（含无权限码的「我的工作台」）+ 新增「菜单管理」 |
| `element` | 60 | 现有 `MENU_TREE` 的 59 个 `element:*` + 新增 `element:menu:edit` |
| 合计 | 93 | |

幂等写法沿用 V174：`INSERT ... SELECT ... WHERE NOT EXISTS (code = ...)`。

### 4.3 权限码拆分（同一迁移内完成）

| 新码 | 复制来源 | 新增菜单 |
| --- | --- | --- |
| `menu:token_stats` | `menu:system:audit` | Token 统计 |
| `menu:knowledge_metrics` | `menu:knowledge_management` | 运营分析 |
| `menu:system:menus` | 仅授予 `admin` 角色所在全部角色？否——只登记资源，不自动授权 | 菜单管理 |
| `element:menu:edit` | 同上 | 菜单管理页所有写操作 |

复制规则：把来源码上 `enabled=1` 且 `(user_id, role_id)` 非空的授权行复制到新码，
`WHERE NOT EXISTS` 去重。这样升级前后可见性完全一致，之后管理员可单独收紧。

`menu:system:menus` 与 `element:menu:edit` **不自动授予任何非 admin 角色**，避免扩大权限面。

## 5. 后端接口与缓存

新增 `app/api/portal/endpoints/menus.py`，注册为 `/api/portal/menus`。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/nav?version=` | 登录即可 | 当前用户可见菜单树；version 未变时返回 `{"unchanged": true, "version": n}` |
| GET | `/tree` | `element:role:edit` 或 `menu:system:menus` | 权限树（menu + element，只读） |
| GET | `/` | `menu:system:menus` | 管理列表（含停用项、含 `builtin` 标记） |
| POST | `/` | `element:menu:edit` | 新建 `group` / `external` |
| PUT | `/{id}` | `element:menu:edit` | 编辑；内置项禁止改 `path`/`code`/`node_type` |
| DELETE | `/{id}` | `element:menu:edit` | 仅 `external` 且 `builtin=0`；同时清理其 `menu:ext:*` 授权行 |
| POST | `/reorder` | `element:menu:edit` | 批量更新同级 `sort_order` |
| POST | `/reset` | `element:menu:edit` | 内置项恢复默认（label/icon/sort/enabled/desktop_only），不动外链 |

校验规则（写操作统一）：

- `page.path` 必须在服务端路由白名单常量 `ALLOWED_PAGE_PATHS` 内（与前端一致，由 seed 同步生成）。
- `external.url` 只允许 `http` / `https`，拒绝 `javascript:` / `data:`。
- `permission_code` 全局唯一；不允许改成 `menu:`/`element:` 之外的前缀。
- `is_landing` 全局唯一，设置时清掉其它行。
- 防环：`parent_id` 不能指向自己或自己的后代；层级最多 3 层（group → page → element）。
- 删除/停用前不允许把某个 `group` 直接删掉（只允许停用）。

### 缓存与失效

- Redis `sys:menu:tree:v1`：全量节点 JSON（`decode_responses=True`），TTL 300s。
- Redis `sys:menu:version`：整数版本号；任何写操作 `INCR` 并 `DEL sys:menu:tree:v1`。
- nav 请求：先取版本号 → 与客户端 `version` 比对；相同则回 `unchanged`，否则取树（缓存或查库）后按当前用户
  `permissions.menus` 过滤下发。
- 表不存在（`ProgrammingError` / `OperationalError` 且命中"表/关系不存在"）时：使用 `BUILTIN_MENU_SEED`
  兜底，版本号固定 `0`，不写缓存。判据放在 `MenuService._table_ready()`，进程内缓存探测结论（与
  `permission_service._supports_agent_scope` 同一思路），但**不留永久 False**：每 60s 重试一次，迁移执行后自动切换。

### 写操作的审计

菜单增删改与恢复默认写入现有审计日志（复用 `app/services/audit_service`，`action` 前缀 `menu:`），
记录操作人、变更前后差异摘要。

## 6. 前端改造

### 6.1 菜单数据

- 新增 `frontend/src/composables/useNavigation.ts`：内存缓存 + `localStorage['nav_snapshot']`（`{version, groups}`）；
  并发请求去重；失败时回退快照，两者都没有则暴露 `error` 供侧栏显示错误态。
- `Dashboard.vue` 删除 `menuGroups` / `hasMenuPerm` / `filteredMenuGroups`，改用 `useNavigation()`；
  分组标题、图标、外链渲染全部走数据。
- 侧栏渲染规则：
  - `group` → 现有分组标题（可折叠），子项为空则不渲染该组。
  - `page` → `router-link`。
  - `external` + `new_tab` → `<a :href target="_blank" rel="noopener noreferrer">`，带外链角标图标。
  - `external` + `embed` → `router-link` 到 `/dashboard/external/:id`。
- 新增加载中骨架与失败重试态（失败时不能渲染空侧栏）。

### 6.2 图标组件

新增 `frontend/src/components/menu/MenuIcon.vue`，props `{ name: string }`，把现有 21 个内联 SVG 收进去，
未知 key 回退到通用方块图标。前端另存 `MENU_ICON_KEYS` 常量供菜单管理页下拉预览，与后端
`ALLOWED_ICON_KEYS` 由同一份 seed 定义生成。

### 6.3 菜单管理页

新增 `frontend/src/views/MenuManagement.vue`，路由 `/dashboard/menus`，挂系统管理组、角色管理之后。

- 左侧树（分组 → 菜单项）+ 右侧属性面板（比表格更适合层级数据）。
- 内置项可编辑：`label` / `icon` / `sort_order` / `enabled` / `desktop_only` / `is_landing` / `description`；
  `path`、`code`、`node_type` 只读展示。
- 外链项可编辑：上述全部 + `url` / `open_mode` / 自动生成的 `permission_code`（slug 可改）。
- 分组可改名、排序、启用/停用；可新增分组。
- 拖拽排序调 `/reorder`；保存成功后 `useNavigation().refresh(force)`。
- 底部「恢复默认」二次确认，只重置内置项。
- `element` 节点以只读方式展开在所属页面下，标注"功能点由代码控制，不可编辑"。

### 6.4 外链内嵌容器

新增 `frontend/src/views/ExternalMenuView.vue`，路由 `/dashboard/external/:id`（`meta.perm` 动态校验，见下）。

- 顶部工具条：菜单名、完整 URL、"新标签打开"、"刷新"。
- iframe 加 `sandbox` 与 `referrerpolicy`，加载 8s 未成功或立即触发 `onerror` 时展示兜底卡片：
  "该站点可能禁止被嵌入（X-Frame-Options / CSP frame-ancestors），请改用新标签打开"。
- 做不到可靠探测（跨域禁止读取 `contentDocument`），因此以超时 + 用户手动切换为主，文案要说明原因。
- 路由守卫：`meta.perm` 不能静态声明，守卫对 `external/:id` 走"该 id 在 nav 快照里且 `open_mode=embed`"校验，
  否则 `NoPermission`。

### 6.5 权限树接口化

- `frontend/src/constants/permissions.ts` 的 `MENU_TREE` 删除，保留 `PermissionNode` 类型与纯函数
  （`getMenuDescendantIds` 改为接收树参数）。
- `MenuAccessTree.vue` 新增必填 prop `tree: PermissionNode[]`，不再 import `MENU_TREE`。
- `Roles.vue`、`Users.vue` 在打开权限弹窗时请求 `GET /api/portal/menus/tree`；**树未加载成功前禁用保存**，
  并显示错误与重试按钮——空树提交等于清空全部菜单授权，这是本改动最高危的失败模式。
- `PersonalCenter.vue` 的 `MENU_TREE` 标签字典改为用 `/menus/tree` 返回的 `label` 构造。
- 后端 `permission_service.exact_permission_mapping` 的菜单部分改为查表（表不存在时回退现有字典）。

## 7. 路由与守卫

- 新增静态路由：`/dashboard/menus`（`meta.perm = 'menu:system:menus'`）、`/dashboard/external/:id`（动态校验）。
- `MENU_HOME_CANDIDATES` 删除，落地页改为：nav 快照中 `is_landing=1` 的可见项 → 否则第一个可见 `page` →
  否则 `PersonalCenter`。种子把「我的工作台」标记 `is_landing=1`，保持非 admin 登录后的现有落点。
- 守卫继续读 `localStorage.user_info.permissions.menus` 做静态页面的 `meta.perm` 校验；导航快照只用于落地页
  计算与外链校验，避免两套权限判断。

## 8. 兼容、风险与回滚

| 风险 | 处置 |
| --- | --- |
| 代码先部署、迁移未执行 | `MenuService` 表探测 + `BUILTIN_MENU_SEED` 兜底，侧栏与现在完全一致 |
| 权限树接口失败导致误清空授权 | 树加载失败禁用保存 + 显式错误态 |
| iframe 被目标站点拒绝 | 超时兜底卡片 + 一键新标签打开，文案说明原因 |
| 落地页漂移 | `is_landing` 显式标记，seed 保持「我的工作台」 |
| 权限码拆分导致可见性变化 | 迁移复制旧码授权行 |
| 菜单改名影响面包屑 | 面包屑继续用路由 `meta.title`，与菜单表解耦 |
| 回滚 | 表与 seed 不影响旧代码；旧代码不读表。回滚前端即可，无需回滚迁移 |

## 9. 测试

后端（pytest，需基础设施）：

- `tests/test_menu_service_*.py`：树构造、防环、层级上限、`is_landing` 唯一、外链 URL 协议校验、`reorder` 幂等。
- `tests/test_menu_api_contract.py`：四个接口的权限码、`unchanged` 语义、内置项禁止删除/改 path。
- `tests/test_menu_seed_generator.py`：生成脚本输出与仓库内 SQL 文件一致（防止两者漂移）。

前端契约（`pytest --confcutdir=tests/frontend`，无需基础设施）：

- `test_menu_management_contract.py`：页面挂载点、权限码、只读字段、恢复默认二次确认。
- `test_nav_composable_contract.py`：version 比对、快照回退、失败态。
- `test_external_menu_view_contract.py`：`sandbox`、超时兜底、新标签打开入口。
- 改造现有：`test_dashboard_permission_guard_contract.py`、`test_menu_access_tree_contract.py`、
  `test_permission_tree_ownership.py`（改为对账 seed 数据）、`test_permission_tree_three_levels_behavior.py`。

手工验收清单：

1. admin 侧栏 7 组 26 项与改造前逐项一致（含图标、顺序、移动端隐藏项）。
2. 非 admin 用户改造前后可见菜单集合完全相同。
3. 改名/排序/停用后刷新页面生效；外链两种打开方式均可用。
4. 角色管理勾选/取消/级联行为不变；树接口失败时无法保存并提示。

## 10. 实施顺序

1. `app/services/menu_seed.py`（内置定义）+ `scripts/gen_menu_seed_sql.py` → 生成 V176 / V77。
2. `app/models/menu.py`、`app/services/menu_service.py`（含表探测与兜底）。
3. `app/api/portal/endpoints/menus.py` + 注册 + 审计接入。
4. 前端 `useNavigation` / `MenuIcon` / `Dashboard` 改造 / 外链容器。
5. 前端 `MenuManagement.vue` + 路由 + 权限码。
6. 权限树接口化（`MenuAccessTree` / `Roles` / `Users` / `PersonalCenter` / 后端字典）。
7. 测试改造与新增、`tests/CHECKLIST.md` 更新、手工验收清单交付。

迁移脚本**不自动执行**，由用户在控制台按 `db-prod/` 或 `db-prod-pg/` 手工导入。上线顺序：先执行迁移，再部署代码
（兜底机制允许反向顺序，但不作为推荐路径）。

# 用户权限「界面访问」树形化 + 角色来源可视化

日期：2026-10-20
状态：已确认（用户逐项选择：缩进树+可折叠 / 节点内标注只读 / 不显示角色名 / 只改界面访问）

## 1. 背景与诉求

用户在「编辑用户 → 权限配置 → 界面访问」提出两点：

1. 当前是一张张卡片（每个菜单一张，内含「功能点权限」子项），层级关系靠卡片边界表达，不直观——希望改成树形。
2. 用户所属角色已经配置了权限，但编辑用户时**看不出哪些页面已由角色授予**，容易误以为这个用户没有权限。

## 2. 根因

`GET /api/portal/management/users/{id}/permissions` 同时返回：

- `permissions`：用户 ∪ 所属角色的**并集**（有效权限）
- `direct_permissions`：**仅用户直配**

前端 `Users.vue` 回显只取 `direct`（`permissionData`），而 `resolveUserPermissionEditState` 算出的 `inherited` **只覆盖 `forbidden_tools` / `forbidden_commands` / `forbidden_skills` 三个键**（`InheritedForbiddenLists`）。菜单（`menus`）与功能点（`elements`）的「并集 − 直配」从未计算，也没有任何渲染通道，于是角色授予的页面在树上既非勾选态、也无任何标注。

**结论：数据已具备，缺的是差集计算与展示。无需后端改动、无需迁移 SQL。**

## 3. 决策（与用户确认）

| 决策点 | 选择 |
|---|---|
| 树形态 | 缩进树 + 可折叠（一级菜单常驻，二级缩进 + 竖线连接） |
| 角色来源表现 | 节点内标注「角色」徽标 + 只读，点击提示去角色管理调整 |
| 是否显示具体角色名 | 不显示，只说「来自角色」（避免新增后端接口） |
| 范围 | 只改「界面访问」；「数据资产」页签与角色页权限弹窗不动 |

## 4. 数据层

改 `frontend/src/utils/permissionLists.ts`：

```ts
export interface UserPermissionEditState {
  direct: PermissionLists
  inherited: InheritedForbiddenLists   // 保持不变
  inheritedMenus: string[]             // 新增：并集 menus − 直配 menus
  inheritedElements: string[]          // 新增：并集 elements − 直配 elements
  usingEffectiveFallback: boolean
}
```

- 新增通用差集 `diffInheritedIds(effective: readonly string[], direct: readonly string[]): string[]`：保序、去重、过滤空串，与 `diffInheritedForbidden` 同口径。
- `diffInheritedForbidden` 可改为复用它，行为不变。
- `usingEffectiveFallback === true`（旧后端 / `direct_permissions` 形状非法）时，两个新字段为 `[]`——树退化为「全是直配」，与现状观感一致，绝不凭空造出只读项。

## 5. 勾选算法（入口与级联拆成两个显式动作）

> 2026-10-20 用户反馈后调整（原第 5 节「勾菜单 = 级联全选」已废弃）：旧实现在勾菜单时把**所有**
> 子项写入直配，导致「只勾菜单、不勾功能点」无法表达；取消某个功能点后父菜单又因自身在直配里
> 被判成「全勾」，再点一下反而清空全部。现在拆成两个动作。

加入继承后，级联仍必须「跳过已由角色授予的子项」，否则会造出冗余直配行：角色侧日后取消该权限，
用户这里仍保留（后端对 `menus` / `elements` 不做角色派生剔除，只对 `forbidden_*` 做，见
`permission_service.update_user_permissions`）。

新增两个纯函数（同文件）：

```ts
export interface MenuToggleResult { menus: string[]; elements: string[] }

/** 点菜单/页面复选框：只切入口。 */
export function applyMenuEntryToggle(
  menus: readonly string[],
  elements: readonly string[],
  menuId: string,
  descendantIds: readonly string[],
  inheritedMenus: readonly string[] = [],
  inheritedElements: readonly string[] = [],
): MenuToggleResult

/** 菜单行「全选 / 清空」：显式级联整棵子树。 */
export function applyMenuCascadeToggle(
  menus: readonly string[],
  elements: readonly string[],
  menuId: string,
  descendantIds: readonly string[],
  mode: 'select' | 'clear',
  inheritedMenus: readonly string[] = [],
  inheritedElements: readonly string[] = [],
): MenuToggleResult
```

规则：

- **入口勾选**（`menus`/`elements` 均未含 `menuId`）：只把 `menuId` 追加进 `menus`，**不碰功能点**。
- **入口取消**：移除 `menuId`，并清掉所有 `descendantIds` 的直配（无入口的孤儿功能点不可见也无从管理）。
- **入口由角色授予**：`applyMenuEntryToggle` 直接返回原值（继承项不能在用户直配侧取消，UI 另外弹提示）。
- **级联 select**：授予入口 + 所有 `descendantIds` 中**不在 inherited** 且尚未存在的 id；`menu:*` 进 `menus`，`element:*` 进 `elements`。
- **级联 clear**：只移除后代的直配，保留入口本身（取消入口是复选框的职责）。
- 全程保序去重，不修改入参。

## 6. 组件

新增 `frontend/src/components/user/MenuAccessTree.vue`（Users.vue 已 4000+ 行，抽出便于单测）。

| prop | 类型 | 含义 |
|---|---|---|
| `menus` / `elements` | `string[]` | 直配（唯一可提交集合） |
| `inheritedMenus` / `inheritedElements` | `string[]` | 角色来源（只读） |
| `disabled` | `boolean` | 权限未成功加载时整体禁用 |

| emit | 载荷 | 语义 |
|---|---|---|
| `toggle-menu` | `menuId` | 点击菜单/页面复选框（只切入口） |
| `toggle-element` | `elementId` | 点击功能点子项 |
| `toggle-menu-descendants` | `menuId, 'select' \| 'clear'` | 菜单行「全选 / 清空」按钮 |
| `inherited-blocked` | `id` | 点击「仅角色来源」节点（父层弹 toast） |

组件职责：树渲染、折叠状态（默认全展开、不持久化）、三态勾选、入口/级联两个动作、继承徽标。组件**不修改数据**，状态机留在父层，保证单一数据源。

## 7. 交互状态机

| 情形 | 渲染 | 点击 |
|---|---|---|
| 入口直配有 | 蓝色实心勾 | 取消入口（并清空其下直配子项） |
| 入口无直配、角色有 | 灰色实心勾 + `角色` 徽标 + 该行淡化 | emit `inherited-blocked`，不修改 |
| 都没有 | 空框 | 加入直配（只加本层入口） |
| 入口未勾、部分后代已授予 | indeterminate + `功能点 M/N` + `全选` | 只勾入口（后代保持） |
| 入口已勾、部分功能点已授予 | 实心勾 + `功能点 M/N` + `全选` | 取消入口 |
| 整棵子树全授予 | 实心勾 + `M/N` + `清空` | 清空后代直配（入口保留） |

判定口径（组件内）：

- `selfGranted(id) = direct 含 id || inherited 含 id`（复选框只看自身）
- `fullyGranted(node) = selfGranted(node) && 所有后代 fullyGranted`（决定「全选 / 清空」文案）
- `partial(node) = !fullyGranted(node) && (selfGranted(node) || 任一后代已授予)`
- 复选框 `checked = selfGranted`；`indeterminate = !selfGranted && partial`
- `selfInherited(node) = 自身不在 direct，但在 inherited`（只读边界收在节点自身：入口继承不锁死可单独补的功能点）
- 菜单无子项时：`checked` 只取决于自身授予状态


## 8. 保存路径（硬约束）

`permissionData` 仍只存直配，`buildUserPermissionPayload` 一行不改，继承项在类型上没有进入 payload 的通道。面板可以改观感，**绝不能把角色权限固化进用户直配**（此前的 F1 回归）。

## 9. 视觉

- 一级菜单行：chevron（折叠）+ 勾选框 + 名称 + `功能点 M/N` + `全选 / 清空`；纯继承时加 `角色` 徽标
- 二级：缩进 40px（移动端 24px），左侧 1px 竖线连接
- 顶部汇总条：`已授予 N 个菜单 · M 个功能点 · 其中角色授予 x / y`——诉求 2 的主要落点
- 徽标：`角色` 小胶囊 + `title` 完整说明；纯继承行 `bg-slate-50` 淡化
- 不做「全部展开/折叠」按钮（YAGNI）

## 10. 测试

| 层次 | 文件 | 内容 |
|---|---|---|
| 纯函数行为 | `tests/frontend/test_user_menu_inherited_scope_behavior.py`（新增） | 差集保序去重；`usingEffectiveFallback` 时为空；`applyMenuEntryToggle` 只切入口/取消清空直配子项/继承入口 no-op；`applyMenuCascadeToggle` 的 select 跳过继承项、clear 保留入口；不改入参 |
| 组件契约 | `tests/frontend/test_menu_access_tree_contract.py`（新增） | props/emits、折叠状态、`角色` 徽标、`inherited-blocked`、不使用 `permissionData`（组件无写通道）、`全选 / 清空` 与 `功能点 M/N` |
| 契约迁移 | `tests/frontend/test_permission_tree_cascade_contract.py`（修改） | 参数化项由 `Users.vue` 改为新组件文件；`Roles.vue` 保持 |
| 回归 | 既有 `test_user_permission_edit_state_behavior.py`、`test_user_direct_permissions_contract.py`、`test_roles_permission_scope_contract.py` 等 | 全量 `pytest --confcutdir=tests/frontend tests/frontend` |

TDD：每个新测试先 RED（功能不存在）再 GREEN；完成后做变异验证（去掉级联跳过 → 行为测试必须失败）。

## 11. 影响面与风险

- 仅用户页「界面访问」面板；角色页、数据资产页签、后端、数据库均不动
- 旧后端（无 `direct_permissions`）：不显示任何「角色」徽标，行为与现状一致
- 保存语义不变：只提交直配
- 汇总条与徽标都是纯展示，不参与提交

## 12. 历史脏数据修复：`menu:*` 被旧前端写成 element（2026-10-20 追加）

第 5 节改掉「勾菜单 = 级联」之后，**存量数据**里还留着旧实现写出的
`resource_type='element'`、`resource_id='menu:*'` 行（子菜单 id 被当成功能点保存）。
症状：用户管理里该页面显示「已勾选 + 角色徽标」，但侧栏菜单不出现、后端菜单鉴权 403 ——
因为侧栏 `hasMenuPerm()` 与 `require_permission("menu", ...)` 都只查 `menus`。

修复分四层，迁移执行前后表现一致：

| 层 | 位置 | 作用 |
|---|---|---|
| 读取兜底 | `PermissionService._aggregate_permissions` | `element` 行里的 `menu:*` 计入 `menus`，所有读接口（`/auth/me`、`/auth/permissions`、用户/角色权限回显）立即正确 |
| 写入归一 | `normalize_permission_resource_type()`（`update_user_permissions` / `update_role_permissions`） | 按 id 前缀纠正类型；同一 id 同时在 `menus` 与 `elements` 时只写一条，旧 bundle 不再制造新脏行 |
| 存量迁移 | `db-prod/V175-...sql`、`db-prod-pg/V76-...sql` | 先删「已有正确 menu 行」的重复 element 行，再把剩余 `element + menu:%` 改成 `menu`；只碰这两类行 |
| 缓存版本 | `_get_cache_key` v6 → v7 | 旧缓存 `elements` 里混着菜单 id，不升版会让升级后 TTL 内看起来没修好；失效逻辑连带清 v6 |

验证：`tests/core/test_permission_service.py` 的读取归位 / 写入归一用例，
`tests/test_menu_permission_resource_type_migration_contract.py` 的 5 项迁移契约，
以及缓存键版本断言（`test_permission_review_fixes.py`、`test_agent_scoped_restrictions.py`）。

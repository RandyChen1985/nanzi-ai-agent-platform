# 角色成员抽屉 实施计划

- 日期：2026-10-20
- 设计：`docs/superpowers/specs/2026-10-20-role-member-drawer-design.md`
- 执行方式：当前会话内联执行（用户已批准设计并要求直接开工）

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `app/api/portal/endpoints/roles.py` | 读权限放宽、`items` 扩字段、新增 remove 端点 |
| `frontend/src/components/system/RoleMembersDrawer.vue` | 抽屉：成员列表 / 搜索 / 分页 / 移出 / 启停 / 重置密码 |
| `frontend/src/views/Roles.vue` | 用户数入口按钮 + 抽屉挂载 + 计数刷新 |
| `frontend/src/views/Users.vue` | 读 `route.query.q` 预填搜索 |
| `tests/api/portal/test_role_member_remove.py` | 后端 remove 行为与权限 |
| `tests/frontend/test_role_members_drawer_contract.py` | 前端契约（组件存在、权限收敛、系统角色保护、深链） |
| `tests/CHECKLIST.md` | 手工验证清单补记 |

## Task 1 后端 remove 端点

**Files**: `app/api/portal/endpoints/roles.py`、`tests/api/portal/test_role_member_remove.py`

1. 写失败测试：幂等（二次 `removed=0, skipped=1`）、空数组、非成员、不误删其他角色关系、缓存失效被调用、权限码为 `element:role:edit`。
2. `.venv/bin/python -m pytest tests/api/portal/test_role_member_remove.py -q` → 期望 ImportError / AttributeError（端点不存在）。
3. 实现 `RemoveRoleUsersRequest` + `POST /{role_id}/users/remove`：先查实际存在的成员，再 `delete`，一次 commit，随后 `invalidate_cached_permissions_for_users`。
4. 重跑测试 → 全绿。

## Task 2 读权限与 items 字段

**Files**: 同上

1. 写失败测试：`get_role_users` 的依赖含 `require_any_permission`；响应 items 含 `email / last_login_at / role / role_names`；`role_names` 用一次 `IN` 查询（断言源码中不存在逐行查询）。
2. 跑测试 → 失败。
3. 改依赖与 items 构造。
4. 重跑 → 通过。

## Task 3 前端契约测试（先写）

**Files**: `tests/frontend/test_role_members_drawer_contract.py`

1. 断言 `RoleMembersDrawer.vue` 存在，且含：`element:role:edit` / `element:user:edit` 两处 `hasPermission`、`miniprogram_user` 保护、`/users/remove` 调用、`set-password` 调用。
2. 断言 `Roles.vue` 引入组件、用户数按钮绑定打开方法。
3. 断言 `Users.vue` 引用 `route.query.q`。
4. 跑 `pytest --confcutdir=tests/frontend` → 失败（文件不存在）。

## Task 4 抽屉组件

**Files**: 新建 `frontend/src/components/system/RoleMembersDrawer.vue`

- `props`: `modelValue: boolean`、`role: {id, name, code, user_count} | null`
- `emits`: `update:modelValue`、`changed`（成员数变化，父级刷新列表）
- 状态：`members`、`total`、`page`、`size=50`、`search`、`loading`、`loadError`、`selectedIds`、`actingId`
- 动作：`fetchMembers`、`debouncedSearch`、`removeMembers(ids)`、`toggleStatus(user)`、打开/提交密码弹窗
- 系统角色：`miniprogram_user` 移出禁用；`default/admin/system` 移出二次确认
- 权限：`canEditRole` 控制移出/添加；`canEditUser` 控制启停/密码/跳转

## Task 5 Roles.vue 接入

- 用户数改按钮（桌面 + 移动端），点击 `openMembersDrawer(role)`
- 挂载 `<RoleMembersDrawer v-model="showMembersDrawer" :role="membersRole" @changed="fetchRoles" />`

## Task 6 Users.vue 深链

- `useRoute()`，`onMounted` 时若有 `route.query.q` 则写入 `searchQuery` 并触发 `fetchUsers()`

## Task 7 验证

- `.venv/bin/python -m pytest tests/api/portal/test_role_member_remove.py tests/frontend/test_role_members_drawer_contract.py -q`
- `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q`（防回归）
- `npx vue-tsc --noEmit`（在 `frontend/` 下）
- 更新 `tests/CHECKLIST.md`

## 边界

- 不执行 `git commit`（用户全局指令）
- 不启动 `./dev.sh`，服务启停交由用户
- 不改表结构，不新增权限点

# 部门管理（树形组织 + 用户多部门归属）实施计划

> **给执行者**：本计划按 TDD 逐任务推进，可用 superpowers:executing-plans 批量执行并在检查点暂停。
> **本仓库约定**：Agent 不执行 `git commit`，计划中的 "Commit" 步骤留给用户（或经用户明确授权后执行）。
> 设计依据：`docs/superpowers/specs/2026-10-20-department-management-design.md`

**Goal:** 新增树形部门管理，支持用户多部门归属与主部门快照，并在用户列表提供部门维度筛选。

**Architecture:** 新增 `ai_agent_departments`（树，物化路径）与 `ai_agent_user_departments`（多对多，`is_primary`）两表，**不加外键**；所有写操作走 `DepartmentService` 统一维护 `users.dept_code` / `org_path` 快照。部门结构 + 成员在同一个管理页（左树 + 右侧 tabs），用户侧只加筛选器与部门选择器。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Pydantic 2；Vue 3 + TS + Tailwind；MySQL / PostgreSQL 双迁移。

---

## 文件结构

| 文件 | 职责 | 动作 |
| --- | --- | --- |
| `app/models/department.py` | 两个 ORM 模型 | 新建 |
| `app/models/__init__.py` | 导出新模型 | 改 |
| `app/services/department_service.py` | 树构建、path 重算、删除保护、成员关系、主部门同步 | 新建 |
| `app/api/portal/endpoints/departments.py` | 部门与成员接口 | 新建 |
| `app/api/portal/endpoints/management.py` | 用户侧：部门集合保存、列表过滤与字段、删除清理 | 改 |
| `app/main.py`（或路由注册处） | 挂载 departments 路由 | 改 |
| `app/services/menu_seed.py` | 3 个新菜单节点 + 白名单 + 图标 | 改 |
| `frontend/src/views/DepartmentManagement.vue` | 页面骨架 | 新建 |
| `frontend/src/components/department/DepartmentTree.vue` | 左树 | 新建 |
| `frontend/src/components/department/DepartmentInfoPanel.vue` | 部门信息表单 | 新建 |
| `frontend/src/components/department/DepartmentMembersPanel.vue` | 成员表 + 添加成员 | 新建 |
| `frontend/src/components/department/DepartmentPicker.vue` | 用户侧部门选择器 | 新建 |
| `frontend/src/views/Users.vue` | 替换自由输入框、加部门筛选 | 改 |
| `frontend/src/router/index.ts` | 路由注册 | 改 |
| `frontend/src/components/MenuIcon.vue` | `org` 图标 | 改 |
| `db-prod/V178-create_departments.sql`、`db-prod-pg/V79-create_departments.sql` | 建表 | 新建 |
| `V178` / `V79` 内的 `gen_menu_seed_sql:department-menu` 标记块 | 菜单 seed（与建表合并） | 脚本生成 |

---

## Task 1: 领域模型与迁移

**Files:**
- Create: `app/models/department.py`
- Modify: `app/models/__init__.py`
- Create: `db-prod/V178-create_departments.sql`, `db-prod-pg/V79-create_departments.sql`
- Test: `tests/api/portal/test_department_model_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/api/portal/test_department_model_contract.py
from app.models.department import Department, UserDepartment

def test_department_table_shape():
    assert Department.__tablename__ == "ai_agent_departments"
    cols = {c.name for c in Department.__table__.columns}
    assert {"id", "parent_id", "code", "name", "path", "sort_order",
            "status", "description", "created_at", "updated_at"} <= cols

def test_user_department_table_shape():
    assert UserDepartment.__tablename__ == "ai_agent_user_departments"
    cols = {c.name for c in UserDepartment.__table__.columns}
    assert {"id", "user_id", "dept_id", "is_primary", "created_at"} <= cols
    uniques = [c for c in UserDepartment.__table__.constraints
               if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in c.columns} == {"user_id", "dept_id"} for c in uniques)

def test_migrations_exist_without_foreign_keys():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    mysql = (root / "db-prod/V178-create_departments.sql").read_text(encoding="utf-8")
    pg = (root / "db-prod-pg/V79-create_departments.sql").read_text(encoding="utf-8")
    for sql in (mysql, pg):
        assert "ai_agent_departments" in sql
        assert "ai_agent_user_departments" in sql
        assert "FOREIGN KEY" not in sql.upper()   # 用户明确要求不加外键
```

- [ ] **Step 2: 运行确认失败**

`pytest tests/api/portal/test_department_model_contract.py -q` → ModuleNotFoundError

- [ ] **Step 3: 写模型**

`app/models/department.py` 用 `app.models.base.Base`（与 `app/models/user.py` 同源），
字段与 spec §4.1 一一对应。`path` 默认 `"/"`，`status` 默认 1，`created_at/updated_at`
用项目现有 `datetime.utcnow` 惯例（照抄 `app/models/permission.py` 的写法）。

- [ ] **Step 4: 写迁移**（内容见 spec §4.1，MySQL 用 `CREATE TABLE IF NOT EXISTS`，
  PG 用 `BIGSERIAL` + `TIMESTAMPTZ`，同样无外键）

- [ ] **Step 5: 跑测试** → 3 passed

---

## Task 2: 部门服务（树、path、删除保护）

**Files:**
- Create: `app/services/department_service.py`
- Test: `tests/api/portal/test_department_service.py`

- [ ] **Step 1: 写失败测试**（FakeSession，覆盖）

```python
async def test_create_department_computes_path():
    """新建子部门后 path = 父 path + id + '/'。"""
    # 父 id=1 path='/1/'，插入子部门 id=7 → path 应为 '/1/7/'
async def test_build_tree_nests_children_and_counts_direct_members():
    """children 递归挂载；member_count 只数直属成员，不含子部门。"""
async def test_delete_rejects_when_children_exist():
async def test_delete_rejects_when_members_exist():
async def test_update_code_syncs_member_dept_code_snapshot():
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现 `DepartmentService`**

```python
class DepartmentService:
    def __init__(self, db: AsyncSession): self.db = db

    async def create(self, *, name, code, parent_id, description="") -> Department:
        # 1) code 唯一校验 → ValueError("部门编码已存在")
        # 2) 父存在校验（parent_id 非空时）
        # 3) insert + flush 拿到 id
        # 4) 计算 path：父 path + f"{id}/"，根部门 f"/{id}/"
        # 5) update path，commit

    async def update(self, dept_id, *, name=None, code=None, description=None,
                     sort_order=None, status=None) -> Department:
        # 改 code 时：UPDATE users SET dept_code = 新 code
        #            WHERE id IN (SELECT user_id FROM ai_agent_user_departments
        #                         WHERE dept_id = :id AND is_primary = 1)

    async def delete(self, dept_id) -> None:
        # 有子部门 → ValueError("该部门下还有 N 个子部门，请先移空")
        # 有成员   → ValueError("该部门下还有 N 名成员，请先移出")
        # 否则 delete 部门行 + delete 该部门的关系行（应已为空，仍显式删）

    async def build_tree(self, include_disabled=True) -> dict
    async def sync_primary_snapshot(self, user_id) -> None
        # 读该用户 is_primary=1 的部门 → 写 users.dept_code / org_path
        # 无主部门 → 置 NULL
```

- [ ] **Step 4: 跑测试** → 全绿

---

## Task 3: 部门接口与权限

**Files:**
- Create: `app/api/portal/endpoints/departments.py`
- Modify: 路由注册处（`app/api/portal/api.py` 或 `app/main.py`，按现有 include_router 位置）
- Test: `tests/api/portal/test_departments_api.py`

- [ ] **Step 1: 写失败测试**：树读权限为 `require_any_permission`（源码断言）、
  DELETE 用 `element:dept:delete`、409 语义

- [ ] **Step 2: 实现 5 个结构接口**（见 spec §6.1）

- [ ] **Step 3: 跑测试**

---

## Task 4: 部门成员接口（增量语义）

**Files:**
- Modify: `app/api/portal/endpoints/departments.py`
- Test: `tests/api/portal/test_department_members.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_add_members_is_idempotent():
    """重复添加已存在的成员：不报错，added=0。"""
async def test_add_members_sets_primary_when_user_has_none():
async def test_remove_members_returns_removed_and_skipped():
async def test_remove_primary_member_promotes_remaining_by_path_order():
async def test_remove_last_member_clears_dept_code_snapshot():
```

- [ ] **Step 2: 实现 4 个成员接口**（`GET`/`POST`/`POST remove`/`POST primary`），
  添加成功后调用 `sync_primary_snapshot`

- [ ] **Step 3: 跑测试**

---

## Task 5: 用户侧（部门集合保存、列表过滤与字段）

**Files:**
- Modify: `app/api/portal/endpoints/management.py`
- Test: `tests/api/portal/test_user_departments.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_put_user_departments_rejects_primary_outside_set():
async def test_put_user_departments_clears_snapshot_when_empty():
async def test_list_users_filters_by_dept_id():
async def test_list_users_include_sub_uses_path_prefix():
async def test_list_users_dept_names_use_single_query():
    """断言本页用户的部门信息只发 1 条 IN 查询，不做 N+1。"""
async def test_delete_user_removes_department_relations():
```

- [ ] **Step 2: 实现** `PUT /management/users/{id}/departments`；
  `list_users` 增加 `dept_id` / `include_sub` 参数与 `dept_ids/dept_names/primary_dept_*` 字段；
  `delete_user` 显式 `delete(UserDepartment)`

- [ ] **Step 3: 跑测试**

---

## Task 6: 菜单 seed 与迁移

**Files:**
- Modify: `app/services/menu_seed.py`
- Update: `db-prod/V178-create_departments.sql`, `db-prod-pg/V79-create_departments.sql`（写入生成的标记块）
- Test: `tests/frontend/test_department_menu_contract.py`

- [ ] **Step 1: 写失败测试**：seed 含 3 个节点、`ALLOWED_PAGE_PATHS` 含路径、
  图标白名单含 `org`、迁移文件存在且含 `menu:system:departments`

- [ ] **Step 2: 改 seed**（3 个 `BuiltinMenuNode`，见 spec §7）

- [ ] **Step 3: 生成迁移**

```bash
.venv/bin/python scripts/gen_menu_seed_sql.py --help   # 先确认参数
# 生成后核对：新文件只含新增节点，不重复既有节点
```

- [ ] **Step 4: 跑测试**

---

## Task 7: 前端路由与图标

**Files:**
- Modify: `frontend/src/router/index.ts`, `frontend/src/components/MenuIcon.vue`
- Test: `tests/frontend/test_department_page_contract.py`

- [ ] **Step 1: 写失败测试**：路由存在且 `meta.perm === 'menu:system:departments'`；
  `MenuIcon.vue` 含 `org` 分支
- [ ] **Step 2: 实现** → 测试通过

---

## Task 8: 部门管理页

**Files:**
- Create: `frontend/src/views/DepartmentManagement.vue` + 3 个子组件
- Test: `tests/frontend/test_department_page_contract.py`（追加）

- [ ] **Step 1: 写失败契约测试**

```python
def test_write_actions_gated_by_dept_edit_permission():
    """写入口必须在 v-if="canEdit" 之后，读权限不足时整页只读。"""
def test_no_native_confirm_in_department_page():
    assert "window.confirm" not in source and "confirm(" not in source
def test_members_panel_uses_incremental_endpoints():
    assert "/members/remove" in source
```

- [ ] **Step 2: `DepartmentTree.vue`**：递归节点、`member_count` 徽标、右键菜单、↑↓ 排序
- [ ] **Step 3: `DepartmentInfoPanel.vue`**：表单 + 保存/删除（删除走 `ConfirmModal`）
- [ ] **Step 4: `DepartmentMembersPanel.vue`**：搜索 + 分页表 + 行操作 + 添加成员双栏弹窗
- [ ] **Step 5: `DepartmentManagement.vue`**：左树 + tabs 骨架、选中态、`canEdit` 判定
- [ ] **Step 6: 跑契约 + `compile_vue_templates.cjs`**

---

## Task 9: 用户编辑弹窗部门选择器

**Files:**
- Create: `frontend/src/components/department/DepartmentPicker.vue`
- Modify: `frontend/src/views/Users.vue`
- Test: `tests/frontend/test_user_department_picker_contract.py`

- [ ] **Step 1: 写失败测试**：`Users.vue` 不再含 `v-model="formData.dept_code"`；
  含 `DepartmentPicker`；提交时调用 `PUT /management/users/{id}/departments`
- [ ] **Step 2: 实现选择器**（树形多选 + 主部门单选，主部门只能是已选中的）
- [ ] **Step 3: 接入编辑弹窗**（新建用户走"先建后写关系"，失败提示重试）
- [ ] **Step 4: 跑契约 + vue-tsc**

---

## Task 10: 用户列表部门筛选

**Files:**
- Modify: `frontend/src/views/Users.vue`
- Test: `tests/frontend/test_department_page_contract.py`（追加）

- [ ] **Step 1: 写失败测试**：含 `dept_id`、`include_sub`、部门筛选器组件引用
- [ ] **Step 2: 实现筛选器 + 用户单元格副信息（主部门 `+N`）**
- [ ] **Step 3: 跑契约**

---

## Task 11: 全量回归与清单

- [ ] `pytest tests/api/portal -q`（记录通过数与既有失败）
- [ ] `pytest --confcutdir=tests/frontend tests/frontend -q`
- [ ] `node tests/frontend/compile_vue_templates.cjs`（0 problems）
- [ ] `vue-tsc -p tsconfig.app.json`（81 基线，改动文件零命中）
- [ ] 更新 `tests/CHECKLIST.md`
- [ ] 汇报改动文件与验证结果（不提交，等用户确认）

---

## 自检

- spec §4–§10 的每项要求都能对上任务：模型/服务/接口/成员/用户侧/菜单/页面/选择器/筛选/回归 ✓
- 无占位符：关键代码与方法签名已在任务中给出；重复性 CRUD 的字段以 spec 表格为准 ✓
- 类型一致：`sync_primary_snapshot`、`build_tree`、`is_primary`、`dept_ids/dept_names/
  primary_dept_id` 在各任务中命名一致 ✓

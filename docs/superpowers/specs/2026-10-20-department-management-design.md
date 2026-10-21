# 部门管理（树形组织 + 用户多部门归属）设计

日期：2026-10-20
状态：设计已与用户确认，进入实施

## 1. 背景

平台目前没有任何组织维度的数据：全库只有 `users.dept_code`（`VARCHAR(50)`）与
`users.org_path`（`VARCHAR(255)`）两个**自由文本**列，在用户编辑弹窗里是两个无校验的
输入框（`frontend/src/views/Users.vue` 约 963–981 行）。

这两个字段并非摆设，它们已经被当作**单值维度**喂给下游：

- `app/services/sql_query_execution_service.py`：`dept_code` / `org_path` 进入
  `agent_context.user_dimensions`，SQL rewriter 用它做行级过滤；
- `app/services/auth_service.py`：登录态、API Key 校验返回的 `user_info` 带这两个字段；
- `app/api/portal/endpoints/auth.py`、`mcp.py`、`saved_reports.py`、`chatbi.py`：读取并透传；
- `users` 列表接口 `GET /management/users` 返回它们。

因此"支持一个用户属于多个部门"不能简单地把单值改成多值——那会让上述所有单值消费方
（尤其是行级过滤）失效。

## 2. 目标

1. 新增「部门管理」菜单，提供树形组织维护（类似组织结构管理）。
2. 部门可添加/移除用户，一个用户可同时属于多个部门。
3. 用户编辑弹窗可选择部门（多选 + 主部门），替换现有自由文本输入。
4. 用户列表支持按部门维度筛选浏览。
5. 部门页可直接浏览与管理局内用户（成员视图放在部门管理页）。

## 3. 已确认的关键决策

| 决策 | 结论 | 理由 |
| --- | --- | --- |
| 外键 | **不加外键**，改为应用层清理 + 一致性巡检 | 用户明确要求；与 `ai_agent_user_role_relations`（V27 起无 FK）风格一致 |
| 主部门 | **保留**：关系表加 `is_primary`，主部门 code 同步到 `users.dept_code` | 保住行级过滤 / ChatBI / 报表分享等全部单值消费方，零改动 |
| 成员视图位置 | 部门管理页右侧 tabs（部门信息 / 成员） | 用户确认；避免 `Users.vue`（4208 行）继续膨胀 |
| 用户页 | 只加部门筛选器 + 副信息展示，不加新列 | 表格横向已拥挤 |
| 成员写入语义 | 增量添加 / 减法移除，**不用整集替换** | 部门成员无"一次编排整个部门"的场景，增量接口不存在并发覆盖 |

## 4. 数据模型

### 4.1 新表

```sql
CREATE TABLE IF NOT EXISTS ai_agent_departments (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  parent_id   BIGINT       NULL COMMENT '根部门为 NULL',
  code        VARCHAR(64)  NOT NULL COMMENT '业务键，与 users.dept_code 对齐',
  name        VARCHAR(100) NOT NULL,
  path        VARCHAR(512) NOT NULL DEFAULT '/' COMMENT '物化路径，如 /1/7/23/',
  sort_order  INT          NOT NULL DEFAULT 0,
  status      TINYINT      NOT NULL DEFAULT 1 COMMENT '0 停用 / 1 启用',
  description VARCHAR(255) NULL,
  created_at  DATETIME     NULL,
  updated_at  DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_departments_code (code),
  KEY idx_departments_parent (parent_id),
  KEY idx_departments_path (path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='部门（组织树）';

CREATE TABLE IF NOT EXISTS ai_agent_user_departments (
  id         BIGINT   NOT NULL AUTO_INCREMENT,
  user_id    BIGINT   NOT NULL,
  dept_id    BIGINT   NOT NULL,
  is_primary TINYINT  NOT NULL DEFAULT 0,
  created_at DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_departments (user_id, dept_id),
  KEY idx_user_departments_dept (dept_id),
  KEY idx_user_departments_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-部门归属（多对多）';
```

PostgreSQL 侧对应 `BIGSERIAL` / `VARCHAR(64)` / `TIMESTAMPTZ`，同样**不建外键**。

### 4.2 为什么没有外键：应用层必须补齐的三件事

去掉外键后，"用户已删除但关系还在"这类悬挂数据只能靠代码保证。上一轮角色关系
（`ai_agent_user_role_relations`）正是因为缺 FK 且删除路径漏删，沉淀出悬挂行，导致
「角色列表显示 1 人、点开 0 人」。本次必须做满：

1. `delete_user` 显式 `DELETE FROM ai_agent_user_departments WHERE user_id = :id`
   （与角色关系同一次提交，放在 `user.roles = []` 之后）；
2. `delete_department` 只允许删除**无子部门且无成员**的部门，因此不会产生
   "关系指向已删部门"；
3. 提供一致性巡检接口/脚本：`SELECT ... LEFT JOIN ... WHERE ... IS NULL`，能查出
   两类悬挂（指向不存在用户、指向不存在部门），并清理。上线后由运维按需执行。

### 4.3 与既有字段的关系

| 字段 | 角色 |
| --- | --- |
| `ai_agent_user_departments.is_primary` | 唯一真源：谁是主部门 |
| `users.dept_code` | **派生快照**：等于主部门的 `code`，由服务层写，不由前端直接写 |
| `users.org_path` | **派生快照**：等于主部门的 `path` |

## 5. 主部门同步规则（唯一真源 → 派生快照）

| 触发 | 行为 |
| --- | --- |
| `PUT /users/{id}/departments` 提交集合 | `primary_dept_id` 必须 ∈ 集合，否则 `400`；未指定且集合非空 → 取集合第一个 |
| `POST /departments/{id}/members` 添加成员 | 该用户当前无任何部门 → 本部门自动成为主部门；已有主部门则不动 |
| `POST /departments/{id}/members/primary` | 把该部门设为该用户主部门（必须已是成员） |
| `POST /departments/{id}/members/remove` 移出 | 若移出的正是主部门 → 从剩余部门里重新选一个当主部门（按 `dept.path` 排序取第一个）；没有剩余 → 清空快照 |
| 任何写操作成功后 | 重算并写回 `users.dept_code` / `users.org_path` |
| 部门改名/改编码 | 该部门下所有成员的 `dept_code` 快照需同步更新（改编码时） |

`org_path` 用部门物化路径（如 `/1/7/23/`）而非名称拼接：名称会变，路径不会。

### 5.1 数据权限过滤：从「只看主部门」到「支持多部门」

`users.dept_code` 是主部门快照（单值），所以原先 ChatBI / 固化报表 / 明细导出的行级规则
`dept_code = {user.dept_code}` 只能命中主部门——属于 A、B 两个部门的用户看不到 B 的数据。
这是**漏权**而非越权，但业务表现就是「我明明在 B 部门，却查不到 B 的数据」；
更麻烦的是它把「组织归属」和「数据归属」强行绑定成一个字段。

现在由 `app/services/user_dimension_service.py` 统一构造维度，一次给三个键：

| 键 | 含义 | 典型模板 |
| --- | --- | --- |
| `dept_code` | 主部门（语义不变） | `dept_code = {user.dept_code}` |
| `dept_codes` | 本人所属全部启用部门 | `dept_code IN ({user.dept_codes})` |
| `dept_codes_subtree` | 本人所属部门 + 其全部子孙 | `dept_code IN ({user.dept_codes_subtree})` |

规则：

- 序列渲染成 `'A', 'B'`（**不带括号**），括号由模板自己写；
- **空序列渲染成 `NULL`**：`IN ()` 是语法错误，而「没有任何部门」的正确语义是条件恒假
  ——既不能放行，也不能让整条查询炸掉（否则新员工还没分配部门时，ChatBI 会直接报错）；
- 停用部门不参与过滤：停用语义是「该部门不再使用」，仍参与会出现「部门已停用但数据还能被看到」；
- 部门查询失败时降级为「仅主部门」并 `rollback()`：代码可能先于迁移上线，此时库里也没有部门
  数据，不能让 ChatBI 整页 500；回滚是必须的，失败查询会让事务失效、拖垮同一 session 的后续查询。

三个消费点（`chatbi.py`、`saved_reports.py`、`chatbi_export.py`）原先**各复制了一份**
`_chatbi_user_dimensions()`（逐字一致）。现在统一调用该构造函数：否则加一个维度要改三处，
漏一处就会出现「ChatBI 能按多部门过滤、固化报表不能」这类最难排查的不一致。

**同时封掉的旁路**：`CreateUserRequest` / `UpdateUserRequest` 的 `dept_code` / `org_path`
收到即 `400` 并指路 `PUT /users/{id}/departments`。原先这两处直接写列，能绕过
`DepartmentService.sync_primary_snapshot()`，造成快照与关系表分叉；静默忽略更糟——调用方会
以为写成功了。内部 SSO 同步仍可传 `dept_code`（那是外部系统的部门标识，与部门树快照不是一回事）。

### 5.2 对外标识：id 与 code 双寻址（code 为业务主键）

存储只有自增 id 一个主键（改编码不必级联重写关联、索引也更小），但**对外一律可按 code 寻址**：

| 场景 | 写法 |
| --- | --- |
| 部门树 / 成员 / 子部门 | `GET /departments/{dept_ref}/members`（`{dept_ref}` 传 id 或 code） |
| 新建时的上级 | `{"parent_code": "EAST"}`（也可用 `parent_id`，两者只能给一个） |
| 同级排序 | `{"parent_code": "EAST", "ordered_codes": ["SH", "BJ"]}` |
| 用户归属 | `PUT /users/{id}/departments` 收 `{"dept_codes": ["EAST","SH"], "primary_dept_code": "EAST"}` |

规则：

- 解析口径：`dept_ref` 是纯数字按 id，否则按 code（`looks_like_department_id`）；
- 因此**编码不能是纯数字**（`validate_department_code` 直接拒绝）：否则 `/departments/42`
  无法判断是 id 还是编码。这条约束必须在录入时就提示，不能等 400；
- `code` 全局唯一（库唯一索引 + 服务层 `_code_taken` 双保险）；
- `code` **可修改**：改编码时同步刷新成员的主部门快照（`users.dept_code`），
  这是选择"存储用 id"换来的好处——改编码不需要重写整棵子树的 path。

**可读层级用派生字段 `code_path`**：落库的 `path` 是 `/1/7/`（id 拼接），对外展示与外部系统
对接需要可读层级，所以在返回时派生 `/ROOT/EAST/`（`compose_code_path`），**不落库**。
这样既有可读路径，又不用承担"改编码要级联重写子树 path"的成本。链路缺一环（部门被删）
时返回空串，宁可展示不出层级，也不拼一个缺一截的假路径。

**编码预检**：`GET /departments/code-available?code=&exclude_ref=` 返回
`{available, code, reason}`。前端输入停顿后与失焦时各查一次、提交前再兜底一次；
编辑场景必须带 `exclude_ref` 排除自身，否则永远提示"已被使用"。预检接口失败时**不阻塞提交**
（后端唯一约束是权威兜底）。

**删除部门不碰员工账号**：`delete()` 只删部门行与（必然为空的）关系行，不发任何
`ai_agent_users` 的写操作。成员的账号、角色、权限、其它部门归属都不受影响；唯一会变的是
`dept_code` / `org_path` 快照——因为被移出主部门的用户必须重新选一个主部门，这是
"快照跟随真源"的必然结果。有子部门或有成员时直接 409，不做级联。

## 6. 接口契约

统一前缀 `/api/portal`。

### 6.1 部门结构

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/departments/tree` | `menu:system:departments` 或 `element:dept:edit` | 返回树 + 每节点 `member_count`（直属） |
| POST | `/departments` | `element:dept:edit` | 新建 |
| PUT | `/departments/{id}` | `element:dept:edit` | 改名/编码/描述/排序/启停 |
| DELETE | `/departments/{id}` | `element:dept:delete` | 有子部门或成员 → `409` |
| POST | `/departments/reorder` | `element:dept:edit` | 同级批量排序 |

`GET /departments/tree` 响应：

```json
{
  "items": [
    {
      "id": 1, "parent_id": null, "code": "EAST", "name": "华东大区",
      "path": "/1/", "sort_order": 0, "status": 1, "description": null,
      "member_count": 3, "children": [ { "...": "递归" } ]
    }
  ],
  "total": 5
}
```

`DELETE` 拒绝时：

```json
{ "detail": "该部门下还有 2 个子部门，请先移空" }
{ "detail": "该部门下还有 5 名成员，请先移出" }
```

### 6.2 部门成员

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/departments/{id}/members` | 同 `tree` 读权限 | 分页 + 搜索；含 `is_primary`、用户状态 |
| POST | `/departments/{id}/members` | `element:dept:edit` | `{"user_ids": [...]}` 增量添加，幂等 |
| POST | `/departments/{id}/members/remove` | `element:dept:edit` | 只做减法，返回 `removed`/`skipped` |
| POST | `/departments/{id}/members/primary` | `element:dept:edit` | `{"user_id": 12}` 设为主部门 |

成员项字段与角色成员抽屉保持一致（`id/user_name/real_name/email/role/status/
last_login_at/dept_names`），便于复用前端行渲染逻辑。

### 6.3 用户侧

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| PUT | `/management/users/{id}/departments` | `element:user:edit` | `{"dept_ids": [1,2], "primary_dept_id": 2}` 整集替换（用户属性语义） |
| GET | `/management/users?dept_id=&include_sub=` | 现有 `menu:system:users` | 按部门过滤 |

`GET /management/users` 的 items 新增：

```json
"dept_ids": [1, 2],
"dept_names": ["华东大区", "上海分公司"],
"primary_dept_id": 1,
"primary_dept_name": "华东大区"
```

实现要求：一次 `IN` 查询批量取回全部 `user_id` 的部门，禁止逐行查询（与 `role_names` 同处理）。

## 7. 权限与菜单接入

`menu_seed.py`（唯一代码源）新增 3 个节点：

| code | 类型 | 父 | 说明 |
| --- | --- | --- | --- |
| `menu:system:departments` | page | `group:system` | 「部门管理」，path `/dashboard/departments`，图标 `org` |
| `element:dept:edit` | element | `menu:system:departments` | 新建/编辑/删除部门、增减成员 |
| `element:dept:delete` | element | `menu:system:departments` | 删除部门 |

同步修改：`ALLOWED_PAGE_PATHS` 增加 `/dashboard/departments`；`ALLOWED_ICON_KEYS`
增加 `org`；`frontend/src/components/MenuIcon.vue` 增加对应图标；`router/index.ts`
增加路由（`meta.perm: 'menu:system:departments'`）。

菜单数据由 `scripts/gen_menu_seed_sql.py` 生成，**不手写**，且与建表写在**同一份迁移**里
（一个功能一个迁移，与 `V176`/`V77` 同构）：

- `db-prod/V178-create_departments.sql`：建两张表 + 「部门管理」菜单与两个权限点；
- `db-prod-pg/V79-create_departments.sql`：同上（PostgreSQL 方言）。

种子部分被 `gen_menu_seed_sql:department-menu START/END` 标记包围，脚本只替换标记块、
不碰手写的建表 DDL；`--check` 会比对标记块内容与 `menu_seed.py`。

生成器的整体约定是**基线 + 增量**：

- 基线（`V176`/`V77`）建表 + 首批 92 个节点，已上线并冻结，生成器不再重写；
- 每批新增节点必须在 `INCREMENTAL_MIGRATIONS` 登记（并指定落到哪份迁移的标记块），
  `--check` 校验「基线 == 全部节点 − 已登记增量」，于是**任何未登记的新节点都会让校验失败**；
- 「部门管理」插在「角色管理」之后会顺推同级排序，因此本批还带两条条件 `UPDATE`
  （`AND sort_order = <旧值>`，管理员手工调过排序时跳过）。

读权限口径与角色成员一致：`require_any_permission(("menu","menu:system:departments"),
("element","element:dept:edit"))`，让只读管理员能看树与成员，但写操作严格要 `element:dept:edit`。

## 8. 前端设计

### 8.1 部门管理页 `frontend/src/views/DepartmentManagement.vue`

布局照搬 `MenuManagement.vue`：左树（`minmax(260px,340px)`）+ 右侧 tabs。

- 左树：分组式递归渲染，显示部门名 + **直属**人数徽标；右键菜单（新建子部门 / 停用 / 删除）；
  新建根部门按钮；同级 ↑↓ 排序。
- 左树标题右侧统计：`6 个部门 · 13 人`。**人数是去重口径**（后端 `COUNT(DISTINCT user_id)`），
  不能把各节点徽标相加——那是"人次"，一个人同时属于开发部和运维部会被计两次。
- 进入页面**自动选中第一个根部门**并展开一级子节点。刚进来对着一片空态，等于让用户自己
  猜下一步该点什么；而组织管理的第一动作几乎总是"从根往下看"。
- 右侧 tab 顺序：**成员在前、部门信息在后**，且选中部门后**默认落在成员页**。
  理由：这个页面的日常用途是"看这个部门有哪些人"，改名称/编码是低频动作；
  只有「新建部门」成功后仍显式切到「部门信息」（那时确实要补属性）。
- 右侧 tab「部门信息」：名称、编码、上级（只读展示）、排序、状态、描述 + 保存/删除。
  编码输入**失焦即查重**、内容停顿时防抖查重（`useDepartmentCodeAvailability`），
  不可用时输入框标红、保存按钮禁用。
- 右侧 tab「成员 (N)」：
  - 搜索 + 分页表格；工具栏有「含子部门」开关（仅当该部门有子部门时出现）：
    - 关闭（默认）= 直属成员，按"是否本部门主部门"排序；
    - 打开 = 整棵子树成员，按物化路径前缀取子部门 id，**按 user 去重**——一个人可能
      既在父部门又在子部门，不去重会在列表里出现两次；
    - 切换部门时自动关掉开关，避免"看着 A 部门却带着 B 的子树范围"。
  - 行操作：设为主部门 / 移出 / 启用禁用 / 重置密码 / ↗跳转用户管理；
  - 「添加成员」弹窗：搜索 + 全选/清空 + 卡片式候选行，落**增量**接口。
    候选行不只有"账号 + 姓名"，还要显示首字母头像、角色徽标、禁用标记、
    **该用户已在哪些部门**（`dept_names`）——同名、同前缀账号很常见，只给账号
    和姓名，管理员判断不出该加谁，也看不出"是不是已经加过了"。

**重置密码必须复用 `components/PasswordResetDialog.vue`**：用户管理页与部门管理页
两处都重置同一个接口的密码。此前部门页自己写了两个裸输入框——规则函数
（`checkPasswordPolicy`）虽然同一个，但等保三级的规则说明、实时 6 项合规指标、
输入框染色全缺，管理员会以为部门页的密码要求更宽松。**"看起来更松"的错觉比真的更松
更危险**，所以抽成组件、两处共用，组件内只做收集与校验，提交由调用方负责。
- 只读态：无 `element:dept:edit` 时整页只读（隐藏全部写按钮，字段 disabled），
  与菜单管理的只读实现同口径。

文件拆分（避免再造一个 4000 行文件）：

| 文件 | 职责 |
| --- | --- |
| `views/DepartmentManagement.vue` | 页面骨架、选中态、只读判定、tabs 切换 |
| `components/department/DepartmentTree.vue` | 左树渲染 + 右键菜单 + 排序按钮 |
| `components/department/DepartmentInfoPanel.vue` | 部门信息表单 |
| `components/department/DepartmentMembersPanel.vue` | 成员表 + 行操作 + 添加成员弹窗 |
| `components/PasswordResetDialog.vue` | 重置密码（等保三级唯一实现，部门页与用户页共用） |

### 8.2 用户编辑弹窗

`Users.vue`：把「部门代码」「组织全路径」两个输入替换为一个 `DepartmentPicker`
（树形多选 + 主部门单选），提交时调 `PUT /management/users/{id}/departments`。

新建用户时也允许选部门（先创建用户，再写关系；两次请求，失败时提示重试而非静默丢弃）。

### 8.3 用户列表

- 筛选区新增「部门」树形下拉 + 「含子部门」开关 → `dept_id` / `include_sub`。
- 不新增表格列：部门信息显示在「用户」单元格的副信息行（主部门名 + `+N`），
  `title` 属性给出完整列表。

### 8.4 字号档位（与用户管理 / 角色管理同档）

部门页最初把整张表压成 `text-xs`，加上 30 处 `text-[10px]`，看起来明显比其它管理页小一号
（用户实测反馈"感觉有点小"）。量化对比后确认不是错觉：

| 元素 | 用户 / 角色页 | 部门页（改前） | 现在 |
| --- | --- | --- | --- |
| 表格容器 | 不设字号 | `text-xs`（整表 12px） | 不设字号 |
| 单元格 | `text-sm` + `py-4` | 继承 12px + `py-2` | `text-sm` + `py-3` |
| 表头 | `text-xs uppercase tracking-wider` | 12px 粗体、无字距 | 同用户页 |
| 搜索框 | `text-sm` | `text-xs` | `text-sm` |
| 页标题 | `text-xl sm:text-2xl` | `text-lg` | 同用户页 |
| 页副标题 | `text-sm` | `text-xs` | `text-sm` |

统一后的档位约定：

- **正文**（表格单元格、树节点、搜索框、表单输入、页头副标题）：`text-sm`（14px）；
- **表头**：`text-xs` + `uppercase tracking-wider`——靠"小字号 + 大写 + 字距"做层级，
  而不是靠加粗把 12px 顶上去；
- **辅助信息**（邮箱、上次登录、人数徽标、行内操作、表单提示）：`text-xs` 或 `text-[11px]`；
- **不再使用 `text-[10px]`**：10px 的中文在 125% 缩放下几乎糊成一团，是最低可读线以下。

注意"不要无脑放大"：密集表格里所有文字都 14px 会糊成一片，辅助信息必须留在 12px 以下
才有主次。放大的只是**正文**与**页头**。

### 8.5 说明气泡 `components/InfoTooltip.vue`

**凡是"数字/范围可能与用户直觉不一致"的地方，必须给出解释，且用自定义气泡，不用原生 `title`。**

第一个实例是左树的「6 个部门 · 7 人」：树上各节点徽标是**直属**人数，1+4+3+0=8，与 7 对不上。
不解释就会被当成算错。气泡文案：「人数已按用户去重：一个人同时属于多个部门只计 1 次，
所以会少于各部门徽标相加的结果（徽标是各部门的直属人数）。」

不用原生 `title` 的原因：约 1 秒才出现、不能换行、样式不可控、**触摸设备根本不显示**。
这种"看不到就会误判"的解释，用原生 title 等于没写。

组件交互：悬停显示（桌面）+ 点击固定（触摸）+ 可聚焦按钮（键盘 Tab/Enter），
固定宽度让长文案在框内换行。

## 9. 边界与非目标

**边界**

- 本期只支持**同级排序**，不支持把部门拖到另一个父节点下（跨父移动要重算整棵子树 `path`
  并同步所有成员快照，单独一期更稳）。
- 部门被停用后：不出现在选择器与筛选中，已有成员关系保留，成员表里以「已停用」标记。
- 删除部门采用"移空才可删"，不做级联删除成员。

**非目标**

- 不做部门级数据权限（"部门负责人只能看本部门用户"）。现有权限模型只有 `menu:*` /
  `element:*`，没有数据范围概念，加进来要动权限服务与全部列表接口。
- 不做 SSO/三方同步自动建部门（现有同步只把 department 写进 `remark`）。
- 不做部门合并、组织架构导入导出。

## 10. 测试计划

**后端**（pytest，`pytestmark = pytest.mark.no_infrastructure` + FakeSession）

1. 树构建：`path` 正确、子节点挂在父下、`member_count` 只数直属。
2. 新建/改名：`code` 唯一冲突返回 `400`；改 `code` 时同步更新成员 `dept_code` 快照。
3. 删除保护：有子部门 → `409`；有成员 → `409`；空部门删除成功。
4. 成员增删：增量添加幂等（重复添加不报错、`added=0`）；移出返回 `removed`/`skipped`。
5. 主部门同步四条规则（见 §5 表）+ 移出主部门后的重选逻辑。
6. `PUT /users/{id}/departments`：`primary_dept_id` 不在集合内 → `400`；集合清空 → 快照置
   `NULL`。
7. `GET /management/users`：`dept_id` 过滤、`include_sub` 走子树、`dept_names` 一次 IN 查询
   （断言查询次数）。
8. 删除用户时显式清理部门关系（源码断言 + 行为断言）。
9. 悬挂巡检：两类悬挂都能被查出来。

**前端契约**（`tests/frontend`）

1. 菜单 seed 含 `menu:system:departments`、`element:dept:edit`、`element:dept:delete`，
   且 `ALLOWED_PAGE_PATHS` 含 `/dashboard/departments`。
2. 路由注册且 `meta.perm` 正确。
3. 部门页权限收敛：写按钮在 `v-if` 之后；无 `window.confirm`（项目零原生弹窗契约）。
4. 成员面板走增量接口，不出现 `/users` 整集替换调用。
5. 用户编辑弹窗不再有 `v-model="formData.dept_code"` 自由输入。
6. 用户列表有 `dept_id` 筛选与 `include_sub`。

**验证命令**

```bash
.venv/bin/python -m pytest tests/api/portal -q
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q
node tests/frontend/compile_vue_templates.cjs
cd frontend && NODE_OPTIONS=--max-old-space-size=6144 ./node_modules/.bin/vue-tsc --noEmit -p tsconfig.app.json
```

`vue-tsc` 基线为 **81 条既有错误**，改动文件必须零命中。

## 11. 迁移清单

| 库 | 文件 | 内容 |
| --- | --- | --- |
| MySQL | `db-prod/V178-create_departments.sql` | 建两表（无 FK） |
| MySQL | （并入 `V178`，无独立文件） | 部门菜单 3 个节点 + 2 条顺推排序，写在标记块里 |
| PostgreSQL | `db-prod-pg/V79-create_departments.sql` | 同上 |
| PostgreSQL | （并入 `V79`，无独立文件） | 同上 |

一个功能一个迁移：建表与它的菜单种子同文件，避免「同一个功能要按编号顺序执行两个脚本」。
全部幂等：建表用 `IF NOT EXISTS`，seed 用 `INSERT ... SELECT ... WHERE NOT EXISTS`（按 `code` 判重），
顺推排序的 `UPDATE` 带 `AND sort_order = <旧值>` 条件——重复执行结果一致，且不覆盖人工调整。
**不做数据回填**：现有 `dept_code` 是自由文本，无法可靠映射成树；上线后由管理员建树，
再用可选脚本按 `code` 精确匹配回填，匹配不上的列出来人工处理。

## 12. 实施顺序

1. 模型 + 迁移（两库）
2. 部门服务（树构建、path 重算、删除保护、成员关系、主部门同步）
3. 部门接口 + 权限
4. 用户侧接口（部门集合保存、列表过滤与字段）
5. 删除用户的清理 + 一致性巡检
6. 菜单 seed + 生成迁移 + 路由/图标/权限
7. 部门管理页（树 + 信息面板 + 成员面板）
8. 用户编辑弹窗部门选择器
9. 用户列表筛选与副信息
10. 全量回归 + CHECKLIST

## 13. 风险

| 风险 | 应对 |
| --- | --- |
| 无外键导致悬挂关系 | 删除路径显式清理 + 巡检 SQL + 契约测试锁定 |
| `dept_code` 快照与关系表不一致 | 所有写操作走同一服务方法 `sync_primary_snapshot()`，禁止在接口层直接改 |
| `Users.vue` 继续膨胀 | 部门选择器独立组件，表格逻辑不动 |
| 菜单 seed 与迁移不同步 | 迁移由脚本生成，契约测试对账 seed 与 SQL |
| 子树过滤性能 | `path` 前缀匹配 + `idx_departments_path`；成员表按 `dept_id` 直查 |

### 8.6 `org_path` 存编码路径，`departments.path` 仍存 id 路径

`users.org_path` 原先是 id 物化路径（`/1/2/`），有两个硬伤：人读不懂、模型只能照搬（"部门路径 `/1/`"），以及跨系统迁移时自增 id 会变。现改为**编码路径**（`/main/dev/`）。

| 字段 | 形式 | 用途 | 是否对外 |
| --- | --- | --- | --- |
| `departments.path` | `/1/2/` | 树结构依据，子树查询靠前缀匹配 | 否 |
| `users.org_path` | `/main/dev/` | 主部门快照，给外部系统 / 占位符 / 模型看 | 是 |

**为什么树结构不跟着用 code**：`path` 是结构依据，改一个部门的编码就得重建整棵子树的路径，code 写错还会把树搞乱；id 永不变，结构才稳。编码路径在读取时由 `code_path_map` 派生（`code_path` 字段），落库的 `users.org_path` 是派生结果的快照。

**级联规则**：`update(code=...)` 时，除刷新本部门成员的 `dept_code`，还要重算**子树内所有用户**的 `org_path`——凡主部门落在该子树内的用户，前缀都变了。

**历史数据**：尚未上生产，**不交付回填迁移**——生产库没有 id 形式的旧快照，为新库准备一个只为旧数据服务的脚本反而是负担。本地开发库可能残留旧值，重设一次该用户的主部门即会经 `sync_primary_snapshot` 刷新。已确认库内 431 个文本列**无任何** `{user.org_path}` 引用，现有 SQL 模板与行级规则不受影响。

**唯一写入方**：`users.dept_code` / `org_path` 只能由 `DepartmentService.sync_primary_snapshot()` 写。`AuthService.generate_api_key()` 虽保留这两个参数（仓库外可能仍有脚本按旧签名 import），但传值一律 `ValueError` 拒绝——它是建 User 行的唯一入口，放开就等于给快照开了条绕过关系表的旁路，将来有人塞进外部系统的部门标识，`org_path` 会冒出第三种格式。创建用户端点对这两个字段同样是显式 400，而不是静默忽略（忽略会让调用方以为写成功了）。

**对外影响**：`{user.org_path}` 与 MCP 用户断言里的值从 `/1/2/` 变为 `/main/dev/`。若有平台外系统按 id 路径做前缀匹配，需同步调整。

### 8.7 部门在界面上显示在哪里

`users.org_path`（编码路径 `/main/dev/`）与 `dept_code`（`main`）都是**机器格式**，界面上任何地方都不展示，只在 `{user.org_path}` 这类占位符与外部系统对接时出现。

给人看的一律是部门名：

| 位置 | 显示 | 数据来源 |
| --- | --- | --- |
| 用户管理列表 | 主部门名 + `+N`，hover 看全部 | `primary_dept_name` / `dept_names` |
| 用户编辑表单 | `DepartmentPicker` 多选 | `GET /departments/tree` |
| 个人中心「我的资料」 | 部门名徽章（主部门主色、兼职中性色），无部门时「未设置部门」 | `GET /auth/me` 的 `dept_name` / `dept_names` |
| 部门成员列表 | 行操作「编辑」→ 深链到用户管理并自动打开该用户 | `/dashboard/users?q=<账号>&edit=<user_id>` |

**为什么补个人中心**：智能体回答"我是什么部门的"时会指路「个人中心 → 我的资料」，而那里原本只有头像、姓名、角色、用户 ID、创建时间、上次登录——**没有任何部门字段**（`/me` 其实早返回了 `dept_code`/`org_path`，前端一处都没用）。普通用户进不了用户管理，等于无处自助确认部门归属。

**渲染约定**：部门名直接平铺成徽章，不折叠成 `+N` 后靠原生 `title` 展开——移动端 hover 不出来，看不到等于没有（与 §8.5 的 tooltip 规则一致）。
**为什么部门成员列表的「编辑」走深链**：用户编辑表单有 760+ 行模板，还牵着权限 tab、额度 tab、Agent 限定、API Key 等一串状态，抽成公用组件等于把用户管理重写一遍，且会长出两套逐渐分叉的编辑逻辑。深链让落点始终是唯一实现；`?q=` 先落搜索词、列表回来后再按 id 取那一行打开，拿到的就是列表同款完整字段（`dept_ids` / `role_ids`），不必另开详情接口。打开后立即 `router.replace` 抹掉 `edit`，否则刷新会重复弹窗。

### 8.8 三个易错边界（代码评审后核实）

**① 重选主部门按"层级深度"，不是按路径字符串。** `choose_primary_after_removal` 取 path 最短的那个，但直接对 `/1/2/` 做字典序比较是错的：`"/1/2/" < "/15/"` 成立，于是根部门 `15` 会输给二级部门 `2`。id 一旦到两位数就会真实发生，结果与"往上走一级"的意图相反。现按去掉空段后的段数排序，同层再按 id。

**② 「含子部门」视图里的操作只能作用于直属成员。** 父部门勾了「含子部门」后列表混着子树成员，而「设为主部门」「移出」调用的是当前部门的接口（按 `dept.id` 校验归属）：对只属于子部门的人，前者直接 400，后者移出 0 行、而列表仍含子树所以"看起来没反应"。因此成员列表返回 `is_direct`（复用已有的归属查询，零额外 SQL），前端据此不给这两个操作、批量移出也会过滤并提示。**禁用 / 重置密码 / 编辑 / ↗ 是用户级操作，与部门归属无关，保持可用。**

**③ `member_total` 恒为全平台去重人数，不受 `include_disabled` 影响。** 树节点可按需过滤停用部门，但"组织里到底有多少人"这类统计包含停用部门的成员更符合直觉。当前唯一传 `include_disabled=false` 的调用方是用户管理的部门筛选下拉，它不展示 `member_total`；部门管理页传 `true`，两者口径一致，故不做联动。

另外，行级规则里若把列表变量写进引号——`dept_code IN ('{user.dept_codes}')`——改写器现在会连同外层引号一起替换，避免拼出 `IN (''A', 'B'')` 这种语法错误；空列表仍渲染成 `NULL`（`IN ()` 是语法错误，而"没有任何部门"的语义是条件恒假）。

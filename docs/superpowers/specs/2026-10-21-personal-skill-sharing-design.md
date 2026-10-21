# 个人技能点对点分享设计

- 日期：2026-10-21
- 状态：待实现
- 相关代码：`app/api/portal/endpoints/personal_skills.py`、`app/services/skill_publication_service.py`、`frontend/src/views/SkillsManagement.vue`

## 1. 背景

「我的技能」目前只有两条对外路径：自己用，或通过「提交为平台技能」走审核后变成全员可见的平台技能。缺少中间档：把某个技能**只分享给指定的几个同事**，对方确认后在自己名下得到一份独立可用的副本。

这次在「我的技能」中加入点对点分享：分享人选人 + 附言发出邀请，接收人在「收到的分享」里接受或拒绝；接受后技能被复制到接收人的个人技能目录，双方之后各自演进、互不影响，数据库保留一条来源血缘用于追溯。

## 2. 目标

1. 个人技能卡片上提供「分享」入口，可选择一个或多个接收人并附一句说明。
2. 分享发出后立即生成内容快照，保证「接受到的内容 = 分享那一刻的内容」。
3. 接收人在「我的技能 → 收到的分享」中看到邀请，可接受（复制到自己的个人技能）或拒绝。
4. 分享人能看到每一条分享的状态，并能撤回尚未被接受的分享。
5. 接受落地时若目标 ID 已被占用，自动改用 `-2`、`-3` 后缀，不打断接受动作。
6. 关键节点通过站内通知触达对方（分享、接受、拒绝、撤回）。

## 3. 非目标

- 不支持按部门 / 角色 / 全员批量分享（与「发布到平台技能」职责重叠）。
- 不支持分享链接、二维码、跨平台导入。
- 不做副本与源技能的后续版本同步（副本是独立快照，需要新内容就再分享一次）。
- 不修改 `SKILL.md` 内容写入来源信息，来源只存数据库。
- 不做分享有效期自动过期、分享次数限制。

## 4. 方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A 独立 `skill_shares` 表 + 分享时快照 | 与发布链路解耦，接受时复制快照 | **采用** |
| B 扩展 `skill_publications` 加 `kind` | 少一张表，但发布/分享两套状态机混在一张表，现有待审核、已发布、下架、血缘查询都要加过滤 | 放弃 |
| C 不存快照，接受时现读源目录 | 省磁盘，但等待期间源技能被改/删会导致内容漂移，且跨用户读取路径出错面更大 | 放弃 |

选择 A 的理由：文件校验与复制逻辑与现有发布实现同源（`validate_snapshot_tree` / 树复制），语义单一，后续查询不需要到处加判别条件。

## 5. 数据模型

### 5.1 表 `skill_shares`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | VARCHAR(36) PK | 分享记录 ID（uuid4） |
| `source_user_id` | BIGINT NOT NULL | 分享人 |
| `source_skill_id` | VARCHAR(128) NOT NULL | 源个人技能 ID |
| `name` | VARCHAR(255) NOT NULL | 分享时的技能名快照 |
| `description` | TEXT NULL | 分享时的描述快照 |
| `message` | VARCHAR(500) NULL | 附言 |
| `target_user_id` | BIGINT NOT NULL | 接收人 |
| `status` | VARCHAR(32) NOT NULL DEFAULT 'PENDING' | `PENDING` / `ACCEPTED` / `REJECTED` / `WITHDRAWN` |
| `snapshot_path` | VARCHAR(1024) NULL | 快照目录；终态清理后置 NULL |
| `content_sha256` | CHAR(64) NOT NULL | 快照内容指纹 |
| `file_count` | INT NOT NULL DEFAULT 0 | 文件数 |
| `total_size` | BIGINT NOT NULL DEFAULT 0 | 总字节数 |
| `accepted_skill_id` | VARCHAR(128) NULL | 接受落地后的实际技能 ID（可能带 `-2` 后缀） |
| `created_at` | DATETIME NOT NULL | 创建/最近一次刷新时间 |
| `responded_at` | DATETIME NULL | 接受/拒绝/撤回时间 |

索引：

- `idx_skill_shares_source (source_user_id, source_skill_id, status)`
- `idx_skill_shares_target (target_user_id, status, created_at)`

> 不建唯一约束：同一对（分享人, 源技能, 接收人）允许存在多条历史记录；「同一时刻只保留一条 PENDING」由服务层保证（见 6.2）。
>
> **不建物理外键**（与 `ai_agent_user_departments` 同一惯例）：建了 RESTRICT 外键而 `delete_user` 又没有清理这张表，删除「分享过 / 接收过技能」的用户会直接撞 1451 报 500；而清理时还要顺带回收到 PENDING 分享的快照目录，只能放在应用层。`management.delete_user` 已显式处理两个方向的记录。

### 5.2 ORM 模型

新增 `app/models/skill_share.py`，`SkillShare(Base)`，字段与上表一一对应，`__tablename__ = "skill_shares"`。

### 5.3 迁移

- MySQL：`db-prod/V179-create_skill_shares.sql`
- PostgreSQL：`db-prod-pg/V80-create_skill_shares.sql`

两套独立编写，风格分别对齐 `db-prod/V110-create-skill-publications.sql` 与 `db-prod-pg/V10-create-skill-publications.sql`（表注释、列注释、索引命名一致）。只新增迁移文件，不改动历史迁移与现有库。

## 6. 状态机与业务规则

### 6.1 状态流转

```
PENDING --accept（仅接收人）--> ACCEPTED   写 accepted_skill_id，responded_at，删除快照
PENDING --reject（仅接收人）--> REJECTED   responded_at，删除快照
PENDING --withdraw（仅分享人）-> WITHDRAWN responded_at，删除快照
```

终态不可再流转；对终态记录执行 accept/reject/withdraw 返回 409。

### 6.2 重复分享

- 相同（分享人, 源技能, 接收人）存在 `PENDING` 记录时：**刷新**该记录（替换快照与 sha256、更新附言与 `created_at`），重新发送通知，不新增行。
- 已存在 `ACCEPTED` / `REJECTED` / `WITHDRAWN` 时：新建一条 `PENDING` 记录，历史保留。
- 一次请求里同一个 `target_user_id` 出现多次：去重后只处理一次。

### 6.3 校验

- 技能 ID 走 `skill_rename_service.validate_skill_id`（格式、长度、保留字）。
- 源技能必须存在且属于当前用户（兼容历史 `.seed` 布局，用 `resolve_personal_skill_dir` 定位）。
- 接收人必须存在且 `status == 1`（启用），不能是自己。
- 一次请求接收人数量上限 50，超出返回 400。
- 附言长度 ≤200 字（前后端一致），超出 400；空白附言存 NULL。

## 7. 文件与快照

### 7.1 目录

- 快照根目录：`{SKILLS_DIR 的父目录}/skill_share_snapshots/`（与 `skill_publication_snapshots/` 并列），权限 `0700`
- 每条分享一份：`skill_share_snapshots/{share_id}/`

### 7.2 生成（分享时）

1. `validate_snapshot_tree(source_path)`：必须有 `SKILL.md`；禁止隐藏文件、符号链接；单文件 ≤20MB、总大小 ≤20MB
2. 复制到 `share_root/.{share_id}-{random}/` 临时目录
3. 对临时目录再校验一次，sha256/文件数/总大小与源一致，否则视为复制期间被修改，失败退出
4. `os.replace` 原子改名为 `share_root/{share_id}`
5. 失败时清理临时目录，不留下半成品

刷新已有 PENDING 记录时：先写新快照到新临时目录，成功后替换旧快照目录（先 `os.replace` 旧目录到 `.old-*`，再放入新目录，最后删旧目录），避免刷新失败把旧快照删掉。

### 7.3 落地（接受时）

1. 校验快照仍存在且 `validate_snapshot_tree` 通过；失败返回 409「分享内容已损坏，请让分享人重新分享」
2. 计算目标技能 ID：`source_skill_id` 未被占用则直接用；否则依次尝试 `{id}-2`、`{id}-3`…（最多 50 次），并过 `validate_skill_id`
3. 在 `agent_workspaces/{目标用户}/skills/` 下建 staged 临时目录 → 复制快照 → 校验 sha256 → `os.replace` 到目标目录
4. 目标目录不得覆盖已存在目录（命名冲突已在上一步规避；`os.replace` 前再判一次存在性，存在则回退重新选号）
5. 成功后写 `status=ACCEPTED`、`accepted_skill_id`、`responded_at`，删除快照目录并置 `snapshot_path=NULL`
6. 落地失败（磁盘、权限等）保持 `PENDING`，返回 500，用户可重试

### 7.4 清理

- 拒绝、撤回、接受成功：立即删除快照
- 源技能被删除、分享人账号停用：不自动清理 `PENDING` 记录（分享内容已固化在快照里，仍可接受）
- 提供 `cleanup_orphan_share_snapshots()`：扫描快照根目录，删除不存在对应 `PENDING` 记录的目录（供运维脚本/定时任务调用，本次不挂定时器）

## 8. 后端接口

前缀 `/api/portal/skills/personal`，全部依赖 `require_api_key`，只操作当前登录用户自己的数据，不需要 `element:skills:admin`。

> 路由注册顺序：新增的 `/share-targets`、`/shares/received*` 必须在 `/{skill_id}` 系列路由**之前**注册，否则会被当作技能 ID 匹配。

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/share-targets?keyword=&limit=20` | — | `[{user_id, user_name, real_name, department}]`，按 `user_name`/`real_name` 模糊匹配，排除自己与 `status != 1`，最多 20 条；`department` 由 `users.dept_code` 关联 `ai_agent_departments` 解析为部门名，取不到为 `null` |
| POST | `/{skill_id}/shares` | `{target_user_ids: number[], message?: string}` | `{created: ShareItem[], refreshed: ShareItem[]}` |
| GET | `/shares/sent?status=&limit=&offset=` | — | `{items: ShareItem[], total: number, pending_count: number, status_counts: {PENDING, ACCEPTED, REJECTED, WITHDRAWN}}`；`status` 缺省返回全部状态，按创建时间倒序 |
| DELETE | `/shares/sent/{share_id}` | — | `{id, status}`（撤回 `PENDING`，仅分享人） |
| GET | `/shares/received?status=PENDING&limit=&offset=` | — | `{items: ReceivedShareItem[], pending_count: number}`；`status` 默认 `PENDING`，传 `all` 时返回全部状态（含已处理），`pending_count` 始终为未处理数量；`limit` 默认 100、上限 500（「全部」没有分页 UI，靠上限兜底） |
| POST | `/shares/received/{share_id}/accept` | — | `{share_id, accepted_skill_id, skill: SkillMeta}` |
| POST | `/shares/received/{share_id}/reject` | — | `{share_id, status}` |

`ShareItem`（分享人视角）：

```json
{
  "id": "uuid",
  "source_skill_id": "skill-test-minimal",
  "name": "极简测试技能",
  "message": "做周报用",
  "status": "PENDING",
  "target_user_id": 12,
  "target_user_name": "zhangsan",
  "target_real_name": "张三",
  "created_at": "2026-10-21T09:00:00",
  "responded_at": null,
  "accepted_skill_id": null
}
```

`ReceivedShareItem`（接收人视角）额外包含 `source_user_name`、`source_real_name`、`file_count`、`total_size`，不含 `snapshot_path`。

个人技能列表接口（`GET /api/portal/skills/personal`）为每个技能补充两个字段：

- `shared_from`：该技能由分享接受产生时返回 `{share_id, source_user_id, source_user_name, shared_at}`，否则 `null`
- `share_count`：该技能当前有效分享人数（`PENDING` + `ACCEPTED` 记录数），用于弹窗展示「已分享给 N 人」

带 `shared_from` 的技能仍按普通个人技能处理（可编辑、可再分享、可删除）。

## 9. 服务层

新增 `app/services/skill_share_service.py`：

- `share_skill(session, *, user, skill_id, target_user_ids, message) -> dict`
- `list_sent_shares(session, *, user, status=None, limit=100, offset=0) -> dict`
- `withdraw_share(session, *, user, share_id) -> dict`
- `list_received_shares(session, *, user, status="PENDING") -> dict`
- `accept_share(session, *, user, share_id) -> dict`
- `reject_share(session, *, user, share_id) -> dict`
- `search_share_targets(session, *, user, keyword, limit) -> list[dict]`
- `cleanup_orphan_share_snapshots() -> int`

复用：

- `validate_snapshot_tree`、新增公共 `copy_tree_contents`（由 `skill_publication_service._copy_tree_contents` 重命名导出，旧名保留为别名，避免影响现有调用）
- `PortalNotificationService.create` 发站内通知
- `get_user_personal_skills_dir` / `resolve_personal_skill_dir` 定位目录

异常：`ShareNotFoundError`、`ShareConflictError`、`SharePermissionError`，端点翻译成 404 / 409 / 403。

## 10. 前端交互

### 10.1 整体布局：我的技能内改为左菜单

「我的技能」内容区照搬图一「我的数据门户」的两栏结构：

```
<div class="grid grid-cols-1 md:grid-cols-[200px_minmax(0,1fr)]">
  <aside> 我的技能（分组标题）  我的技能 / 收到的分享 / 我的分享 </aside>
  <main> 当前菜单对应内容 </main>
</div>
```

- 左菜单参照 `DataPortalHome.vue`：`md:grid-cols-[200px_minmax(0,1fr)]`，aside 白底、`border-r border-gray-100`、分组标题带圆角图标块，选中项 `bg-blue-600 text-white`，未选中 `text-gray-600 hover:bg-white`
- 顶栏一级 Tab（平台技能 / 我的技能 / 待审核 / 已发布）保持不变；本方案**不新增**顶栏 Tab
- 左菜单只在 `activeScope === 'personal'` 时渲染；管理员切到「平台技能 / 待审核 / 已发布」仍是现有布局
- 菜单项：
  - `mine` 我的技能：现有技能列表（搜索、排序、视图切换、新建、导入、卡片操作）原样搬进右栏
  - `received` 收到的分享：未处理数量 `> 0` 时菜单项右侧显示数字徽标
  - `sent` 我的分享：我发出的全部分享，`PENDING` 数量可作徽标
- 窄屏（`< md`）：aside 隐藏，内容区顶部渲染横向胶囊 Tab（三项同文案），选中态 `bg-blue-600 text-white rounded-full`，与现有一级 Tab 视觉接近，不引入底部导航

### 10.2 分享入口与弹窗 `components/skill/SkillShareDialog.vue`

个人技能卡片的操作按钮组（下载 / 编辑 / 删除同一排）新增「分享」按钮，仅 `scope === 'personal'` 时显示，`title="分享给同事"`。

弹窗只负责发送：

- Props：`visible`、`skill`；Emits：`close`、`sent`
- 搜索框（防抖 300ms 调 `/share-targets`）+ 结果多选列表 + 已选 chips（可删）
- 附言 textarea（`maxlength=200`，右下角计数）
- 底部一行提示：「已分享给 {{ skill.share_count || 0 }} 人，查看我的分享 →」（点击关闭弹窗并切到 `sent` 菜单）
- 「发送分享」成功后 toast「已分享给 N 位同事」，清空已选并关闭；不在此弹窗内展示历史记录

### 10.3 收到的分享 `components/skill/ReceivedSharesPanel.vue`

- 顶部筛选：待处理（默认）/ 全部
- 卡片：来源人（姓名/用户名）、技能名、描述、附言引用块、文件数与大小、分享时间、「接受」「拒绝」按钮
- 接受成功：toast「已复制为 `xxx-2`」，卡片移出待处理列表，徽标减一，提供「去我的技能看看」按钮（切到 `mine`）
- 拒绝：二次确认后移出
- 空态：「暂时没有收到分享」；加载态骨架屏

### 10.4 我的分享 `components/skill/SentSharesPanel.vue`

- 顶部：状态筛选胶囊（全部 / 待接受 / 已接受 / 已拒绝 / 已撤回），各带数量
- 表格或列表行：技能名、接收人、附言（截断 tooltip 全文）、状态徽标、分享时间、处理时间
- 待接受的行有「撤回」按钮（二次确认）
- 列表行可点击跳转到对应技能（`mine` 菜单并定位到该技能卡片），技能已删除时该项禁用并提示
- 空态：「还没有分享过技能，去我的技能里点分享吧」+ 按钮切到 `mine`

### 10.5 状态映射

前端集中定义 `SHARE_STATUS_META`：`PENDING` 待接受（amber）、`ACCEPTED` 已接受（green）、`REJECTED` 已拒绝（gray）、`WITHDRAWN` 已撤回（gray），左菜单徽标、两个面板、弹窗提示共用这一份文案与颜色。

### 10.6 与路由/个人中心的联动

- 个人中心深链 `?tab=skills&skills_tab=received|sent|mine`：进入「我的技能」后按参数落点，未知值回落到 `mine`
- 站内通知点击使用同一套 `skills_tab` 参数（见第 11 节）
- 现有 `skill_id` 深链（打开编辑抽屉）行为保持不变，落点固定 `mine`

## 11. 通知

复用 `portal_notifications`：

| 时机 | 接收人 | category | 标题 |
| --- | --- | --- | --- |
| 分享发送/刷新 | 接收人 | `skill_share` | `{分享人} 向你分享了技能 {技能名}`，正文为附言 |
| 接受 | 分享人 | `skill_share_result` | `{接收人} 已接受你分享的 {技能名}`，正文含落地 ID |
| 拒绝 | 分享人 | `skill_share_result` | `{接收人} 拒绝了技能分享 {技能名}` |
| 撤回 | 接收人 | `skill_share_result` | `{分享人} 撤回了技能分享 {技能名}` |

`resource_type='skill_share'`，`resource_id=share_id`，`metadata` 存 `{share_id, skill_id, accepted_skill_id, open, target_tab}`，其中 `target_tab` 由后端决定（给接收人的邀请为 `received`，给分享人的接受/拒绝结果为 `sent`）。前端 `PortalNotificationBell` 命中 `resource_type='skill_share'` 时不再弹详情，直接跳 `/dashboard/personal?tab=skills&skills_tab=<target_tab>`。

## 12. 异常与边界

| 场景 | 处理 |
| --- | --- |
| 分享给自己 | 400「不能分享给自己」 |
| 接收人不存在 / 已停用 | 400，返回无效 ID 列表 |
| 源技能不存在 | 404 |
| 源技能不属于当前用户 | 403 |
| 源技能缺 `SKILL.md` / 含隐藏文件、符号链接 / 超限 | 400，带具体原因 |
| 未 PENDING 的分享再撤回/接受/拒绝 | 409 |
| 非接收人调用 accept/reject | 403 |
| 非分享人调用 withdraw | 403 |
| 快照丢失 / 校验失败 | 409「分享内容已损坏，请让分享人重新分享」 |
| 落地时磁盘错误 | 500，记录保持 PENDING，可重试 |
| 复制期间源技能被修改 | sha256 不一致，400「技能在分享过程中被修改，请重试」 |

## 13. 测试计划

### 13.1 后端 `tests/api/test_personal_skill_shares.py`

1. 分享成功后：记录为 PENDING、快照目录存在、接收人收到通知
2. 缺 `SKILL.md`、含隐藏文件、含符号链接、超过 20MB → 400
3. 分享给自己 / 接收人停用 / 接收人不存在 → 400
4. 重复分享同一 PENDING → 记录数不变、附言与 `created_at` 更新、快照哈希变化
5. 拒绝后再次分享 → 新建第二条记录
6. 撤回：仅分享人可撤回；撤回后快照被删除；重复撤回 409
7. 接受：落地到接收人目录、内容 sha256 与分享时一致、`accepted_skill_id` 正确、分享人收到通知
8. 接受时 ID 冲突 → `-2`；再冲突 → `-3`
9. 非接收人接受/拒绝 → 403；非分享人撤回 → 403
10. 快照被删除后接受 → 409；记录仍为 PENDING
11. 已接受/已拒绝记录不可再次接受 → 409
12. 列表接口 `shared_from` 与 `share_count` 正确返回
13. `GET /shares/sent` 按状态筛选、`status_counts`/`pending_count` 正确
14. 撤回不存在的 share / 非本人 share → 404 / 403

### 13.2 前端契约 `tests/frontend/test_skill_share_contract.py`

静态断言（沿用 `test_skill_publication_contract.py` 风格）：

- `SkillsManagement.vue` 中存在左菜单三项文案「我的技能」「收到的分享」「我的分享」、`md:grid-cols-[200px_minmax(0,1fr)]`、`skills_tab` 参数、`/api/portal/skills/personal/`、`share-targets`、`shares/sent`、`shares/received`
- `SkillShareDialog.vue` 存在 `target_user_ids`、`message`、`maxlength="200"`、`查看我的分享`
- `ReceivedSharesPanel.vue` 存在 `accept`、`reject`、`pending_count`
- `SentSharesPanel.vue` 存在 `shares/sent`、`撤回`、`status_counts`

### 13.3 手工验证

`tests/CHECKLIST.md` 增加：

- 两个账号互相分享、接受、拒绝、撤回
- 同名技能接受后出现 `-2`
- 分享后删除源技能，对方仍可接受
- 站内通知点击跳转到对应左菜单项（收到的分享 / 我的分享）
- 管理员与普通用户都能看到左菜单三项；窄屏下折叠为胶囊 Tab
- 个人中心深链 `?tab=skills&skills_tab=sent` 正确落点

### 13.4 回归

- `pytest tests/api/test_personal_skill_shares.py`
- `pytest tests/frontend/test_skill_share_contract.py tests/frontend/test_skill_publication_contract.py`
- `pytest --confcutdir=tests/frontend tests/frontend`
- `vue-tsc --noEmit`

## 14. 上线步骤

1. 应用迁移：`./db-prod/apply-sql.sh db-prod/V179-create_skill_shares.sql`，PostgreSQL 环境执行 `./db-prod-pg/apply-sql.sh db-prod-pg/V80-create_skill_shares.sql`（由用户在控制台执行）
2. 部署后端与前端
3. 用户自行运行 `./dev.sh` 验证

## 15. 风险与后续

- **快照磁盘占用**：终态即删，仅 PENDING 记录占空间；单人单技能单接收人上限 20MB，正常规模可控
- **通知重复**：刷新 PENDING 会再发一条通知，接收人可能看到多条同技能邀请，可接受（内容可能已更新）
- **并发接受**：同一 share 被并发接受两次，依靠 `status='PENDING'` 条件更新（`UPDATE ... WHERE status='PENDING'`）保证只有一次成功
- **删除用户**：`skill_shares` 不建物理外键，`delete_user` 显式清两个方向的记录并先回收 PENDING 快照目录；
  发布谱系（`skill_publications` / `skill_publication_versions`）同样需要处理——V180（MySQL）/ V81（PG）
  把 `source_user_id`、`submitted_by` 改为可空，删除用户时按「下架」语义处理（删物化目录、版本置 REVOKED /
  WITHDRAWN），记录与 `platform_skill_id` 保留、作者字段置 NULL，审核轨迹不丢
- 后续可扩展：副本「拉取源技能最新版」、分享给部门、分享模板化（附带使用说明）

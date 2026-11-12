# 智能体中心「分配给我」只读视图设计（2026-11-12）

## 1. 需求

用户反馈：管理员给某个普通用户分配了一些智能体，该用户**确实有权限访问**，但在智能体中心里看不到它们。

期望：

1. 智能体中心单独加一个 tab，让用户能看到被分配的智能体；
2. 卡片**只读**：不能复制、不能查看配置、不能编辑；
3. 该 tab 名为「分配给我」，**始终显示**（0 条时给空态说明）。

## 2. 现状核查（读码结论）

### 2.1 根因：两个列表用两套可见性规则

| 列表 | 使用场景 | 非管理员的可见条件 | 依据 |
| --- | --- | --- | --- |
| `AgentManagerService.list_agents` | 智能体中心（管理台） | 仅 `created_by == username` | `app/services/ai/agent_manager.py:836-842` |
| `AgentManagerService.list_allowed_agents` | 对话 / @提及 / EmbedChat | `is_system AND id IN perms.agents` **或** `created_by == username` | `app/services/ai/agent_manager.py:1117-1134` |
| `AgentManagerService._user_can_execute_agent` | 单个智能体的执行鉴权 | 同上口径 | `app/services/ai/agent_manager.py:1050-1067` |

`list_agents` 里的原注释把设计意图写得很明确：

```python
# 可见性：非管理员只看自己创建的。别人创建的智能体（无论是否系统级）都不出现：
# 系统智能体由管理员维护，普通用户既管不了，也不该看到它的提示词与工具配置——
# 复制接口会把整份配置带出来，看到即等于可复刻。
```

所以现象是**完全自洽的**，不是授权数据丢失：管理台从来没查过 `ResourcePermission` 那张表。

### 2.2 授权来源

- 入口：角色管理 / 用户管理的「智能体」页签；
- 候选集合：`select AIAgent.id, name, display_name where is_enabled == True and is_system == True`
  （`app/api/portal/endpoints/management.py:1876-1882`）；
- 落库：`ResourcePermission(resource_type='agent', resource_id=agent_id)`，可按 user 或 role 授予；
- 聚合：`PermissionService.get_user_permissions().permissions.agents`
  （`app/services/permission_service.py:1676-1678`）。

UI 上已有一句提示「此处仅配置**系统级**公共智能体权限」
（`frontend/src/views/Roles.vue:603-609`），与后端候选集一致。

### 2.3 一处顺带确认的无效数据

`ResourcePermission` 里若写入**非**系统级智能体的 id，使用侧同样不认
（`_user_can_execute_agent` 对非系统级只认 `created_by`），角色管理 UI 也不会列出它。
这类授权行不会生效，且不会有任何报错。本设计不处理它（见 §9），但实现时不要假设
"`permissions.agents` 里的 id 都是系统级"。

## 3. 为什么不是简单放开 `list_agents`

最省事的做法是去掉 `list_agents` 里的 `created_by` 判定。不采用，原因有三：

1. **会把配置面一起打开**。`list_agents` 是管理台列表，卡片上挂着编辑、版本、发布、
   删除、复制、分组、预览等入口，这些入口由 `element:agent:*` 权限与 `is_editable` 控制。
   放开可见性等于逐个入口重新确认权限，漏一个就是越权。
2. **复制是"看到即等于可复刻"**。2.1 的注释已经点明这个风险：复制接口会把整份配置带出来。
   用户明确要求"不能复制"。
3. **会让两个视图的语义打架**。`list_agents` 同时服务「全部」和「我的智能体」，
   `agentsMatchingFilters` 的归属过滤、批量操作、分组页签、管理筛选器都挂在上面
   （`frontend/src/views/AgentManagement.vue:1463-1471`、`:5044-5049`）。
   塞进"别人给我的"会让"我的"这个词失去边界。

## 4. 设计决定

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| 数据来源 | 新增 `GET /api/portal/agents/assigned` | 只读属性由后端定死，前端没有绕过空间；不污染 `list_agents` |
| 授权口径 | 直接复用 `permissions.agents` | 与 `_user_can_execute_agent` 同源，"能用"与"看得见"不再分叉 |
| tab 名称 | 分配给我 | 与「分享给我」并列时，一个"已给"一个"待接" |
| tab 位置 | 我的智能体 → **分配给我** → 分享给我 → 我分享的 → 专家团 | 顺序即语义：我能管的 → 我被给的 → 等我去接的 |
| 0 条时 | 始终显示，给空态说明 | 撑销授权后 tab 不会默默消失 |
| 管理员 | 不显示该 tab，接口返回空数组 | 管理员已有「全部智能体」，再来一份是重复视图 |

## 5. 后端契约

### 5.1 接口

```
GET /api/portal/agents/assigned
权限：menu:agent_management（与智能体中心页面一致，不新增权限点）
返回：List[AIAgentResponse]
```

### 5.2 口径

1. `is_admin` → 返回 `[]`。
2. 取 `PermissionService(session).get_user_permissions(uid).permissions.agents`；取不到
   （非数字 uid、异常）按空列表处理，与 `list_allowed_agents:1119-1126` 的容错一致。
3. 查询条件：

```python
AIAgent.id.in_(allowed_ids)
AND AIAgent.is_enabled == True
AND or_(AIAgent.created_by != username, AIAgent.created_by.is_(None))
```

- `is_enabled == True`：停用的智能体本来就执行不了（`resolve_embed_agent_access` 对
  `not agent.is_enabled` 直接抛 404），列出来只会让人以为系统坏了；
- `created_by != username OR IS NULL`：自己创建后被提升为系统级并授权的，已在「我的智能体」
  出现，不重复；`created_by` 为空的历史数据在 `list_agents` 里同样不出现，属于典型的
  "有授权看不见"，必须收进来。

4. 排序与 `list_allowed_agents` 保持一致：
   `main_first, sort_order desc, is_system desc, display_name`。

### 5.3 序列化

复用 `AIAgentResponse`，并在服务端**定死**三个字段：

```python
agent.is_editable = False
agent.can_edit_versions = False
agent.can_publish_version = False
```

这三个字段是安全边界，不交给前端判断。依据是 `AIAgentResponse.is_editable` 默认值为
`True`（`app/schemas/agent.py:236`）——默认值就是"可编辑"，任何一处漏算都会变成放行。

**不挂载**以下管理台专属数据：

- `model_issue` / `tool_issue`（运维诊断，对使用者是噪音，且会引起"我是不是不能用了"的误解）；
- `readiness_ready` / `readiness_missing`（同上）；
- `team_member_ids`（调度范围是管理者配置项）；
- `execution_count`（运营数字，`list_agents` 卡片也已不再展示）。

## 6. 前端结构

### 6.1 类型与 tab

- `AgentWorkspaceTab` 增加 `'assigned'`（`frontend/src/views/AgentManagement.vue:847`）；
- `workspaceTabs` 在「我的智能体」之后插入，且 `!isAdminViewer` 时才插入
  （参照 `:1207-1222` 现有写法）；
- `resolveInitialTab` 的允许列表加入 `'assigned'`（`:1198-1205`），**且必须过
  `isTabAvailableForRole()` 守卫**；
- `route.query.agents_tab` 的 watch 白名单同步加入（`:1235-1245`），走同一个守卫；
- 角标沿用「分享给我」的既有规则：**0 条时不显示数字**（0 没有行动价值，还会分心），
  但 tab 本身仍在（见 §4）。

**为什么 URL 落地要按角色守卫**：「分配给我」只对普通用户渲染，若管理员带
`?agents_tab=assigned` 进入，会落到一个「没有任何 tab 高亮 + 空列表 + 没有切回入口」
的页面。初始化和 watch 两条路径都要过守卫，漏一条就够复现。

### 6.2 数据获取

页面初始化时拉一次，与 `shareCounts` 同级；切 tab 不重拉，用户可通过页面刷新更新。
不在 `agents.value` 里合并——那是管理台列表，混入会让 `totalAgentCount`、分组页签、
管理筛选器全部算错。

### 6.3 卡片

只读变体，动作区**只保留一个**「进入对话」。其余入口（编辑、设置分组、版本、发布、
删除、复制、更多操作菜单）全部**不渲染**，而不是置灰——置灰会暗示"你可能有权"，反而引投诉。

跳转**新增独立函数**（如 `openAssignedChat`），复制 `openPreview`（`:3712-3733`）的取票
与跳转逻辑，但**不带 `preview=1`**：

- `preview=1` 是管理者的试跑语义，会强制干净临时会话（不续接、不写续接指针）；
- 使用者要的是正常对话，应续接自己的历史。

不得直接修改 `openPreview`——它仍服务管理台的「预览对话」，改掉会让管理员每次预览都
续接上次的临时会话。降级兜底同理：`/dashboard/chat?agent_id=<key>` 且**不带** `preview=1`。

卡片信息只展示：头像、显示名、描述、「平台提供」徽标。不展示版本号、工具数、就绪度。

### 6.4 搜索

页面级搜索框对所有视图保留（`:5023-5038` 已有此约定）。`assigned` 视图下按
`name / display_name / description` 过滤，placeholder 改为「搜索分配给我的智能体…」。

批量操作、网格切换、管理筛选器本就挂在 `workspaceTab === 'mine' || 'all'` 条件下
（`:5044-5049`），新 tab 天然不受影响，无需额外改动。

### 6.5 空态

必须写清"为什么是空的"，不能只写"暂无数据"。**三种情况要说三套话**：

| 情况 | 判定 | 文案方向 |
| --- | --- | --- |
| 请求失败 | `assignedAgentsError` | 分配列表加载失败 → 刷新重试 / 联系管理员确认授权 |
| 有数据但搜索无命中 | `assignedAgents.length > 0` | 没有匹配的智能体 → 换个关键词试试 |
| 真的没有分配 | 以上都不是 | 还没有智能体被分配给你。分配来的智能体可以直接对话，但不能修改配置。**如果你确认已被授权却看不到，可能是该智能体已被停用。** |

请求失败**不得**退化成第三套话：那会让用户拿着「还没有智能体被分配给你」去找管理员，
而管理员那边看到的却是「明明授权了」——两边互相否定，问题反而更难定位。

## 7. 与「分享给我」的区分

两个 tab 相邻，语义极易混淆，必须在界面上把差异说死：

| | 分配给我 | 分享给我 |
| --- | --- | --- |
| 含义 | 平台已授予**使用权限** | 别人发来的**待接收件** |
| 需要操作 | 不需要，直接能用 | 必须接受后才生成副本 |
| 结果 | 指向**同一个**智能体 | 生成**我拥有的副本** |
| 归属 | 仍属于创建者 | 接受后归我 |

「分配给我」的空态与「分享给我」的 badge 语义不能复用同一套说辞。分享 tab 的 badge
是"待处理数"，分配 tab 的 badge 是"可用总数"（参照 `:1213-1217` 的既有注释）。

## 8. 测试与验收

### 8.1 后端（pytest）

新增 `tests/api/portal/test_agent_assigned_list.py`（目录参照现有 `tests/api/portal/`）：

1. 非管理员拿到「被授权且非自己创建」的集合；
2. 自己创建并被授权的智能体**不出现**（避免与「我的智能体」重复）；
3. `is_enabled=False` 的被授权智能体不出现；
4. 管理员拿到空数组；
5. 返回项 `is_editable` / `can_edit_versions` / `can_publish_version` 恒为 `False`；
6. 无 `Agent` 权限行的用户拿到空数组，且不抛异常。

### 8.2 前端契约测试

新增 `tests/frontend/test_agent_assigned_view_contract.py`，写法参照
`tests/frontend/test_agent_share_contract.py`：

1. `workspaceTabs` 含 `assigned`，且带 `isAdminViewer` 守卫；
2. 只读卡片不渲染编辑 / 复制 / 删除入口；
3. 跳转 URL 不含 `preview=1`。

### 8.3 手工验收（必须做，见 §9.1）

普通账号被授权一个系统级智能体 → 智能体中心出现「分配给我」tab → 点「进入对话」
能正常建立会话并收到回复。

## 9. 风险与不做的事

### 9.1 唯一待实测的路径

「进入对话」依赖 `POST /api/v1/embed/tickets`。该接口用 `require_api_key`，**支持门户
Cookie 登录态**（`app/core/dependencies.py:73-80`，`portal_session` 优先于 `embed_session`），
所以已登录用户是通的；但它内部还会调 `_user_can_execute_agent`。实现时必须用一个
**只有智能体授权、没有其它菜单权限**的普通账号实测一次，这是本功能唯一的动作入口，
不通则整个设计无意义。

### 9.2 不做

- 不做"申请编辑权限"的流程：用户明确只要"能访问、只读"。
- 不处理 §2.3 的无效授权行（非系统级智能体写进 `ResourcePermission`）：属于独立的数据
  治理问题，混进来会让本次改动范围失控。
- 不改 `list_agents` 与 `list_allowed_agents` 的现有口径：它们各自有调用方与契约测试
  （`tests/frontend/test_agent_share_contract.py:163-191`）。
- 不新增数据库迁移：无表结构变更。

## 10. 模块边界

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 接口 | `app/api/portal/endpoints/agents.py` | 新增路由，只做权限声明与调用 |
| 服务 | `app/services/ai/agent_manager.py` | 新增 `list_assigned_agents(session, user)`，口径与只读字段在此定死 |
| 前端页面 | `frontend/src/views/AgentManagement.vue` | tab、独立数据源、只读卡片、空态 |

服务方法独立于 `list_agents`，不共享过滤分支——两者口径不同且未来会各自演化
（例如将来若支持分配非系统级智能体，只改 `list_assigned_agents` 一处）。

## 11. 实现后的修订（代码审查发现）

实现完成后跑了两路独立审查（后端 / 前端，均只读）。以下是把审查意见落进代码的改动，
按"为什么改"记录，避免后人看到代码与 §5/§6 原文不一致时反复猜测。

### 11.1 口径补 `is_system == True`（关键）

原实现只有 `id IN allowed_ids AND is_enabled AND (created_by != 我 OR IS NULL)`，
与 `_user_can_execute_agent` 并不同源——后者对**非**系统级智能体只认
`created_by == 自己`。复查后确认这不是理论风险：

`agent_system_publication_service.py` 在**下架（取消系统级）时故意保留**
`ResourcePermission` 行。也就是说「授权行存在、但已不是系统级」是产品自身路径
稳定产生的数据，不是脏数据。缺 `is_system` 会让这类智能体出现在「分配给我」里，
点进去被 `resolve_embed_agent_access` 拒掉 —— 正是本功能要消灭的"看得见用不了"。

已在查询里补 `AIAgent.is_system == True`，并在
`tests/api/portal/test_agent_assigned_list.py` 的 SQL 编译断言里锁住这一句
（否则后人删掉它，15 条测试仍全绿）。

### 11.2 「进入对话」按权限分流，而不是一律走 embed

原实现主路径统一签票跳 `/embed/chat`（第三方嵌入面：无门户外壳、无返回入口），
兜底才跳 `/dashboard/chat`，同一按钮两种落点，与平台惯例（`openTeamChat` 跳
`/dashboard/chat`）不一致。

两条路由的约束不同，必须分流而不是二选一：

| 落点 | 约束 | 适用 |
| --- | --- | --- |
| `/dashboard/chat?agent_id=` | 路由 `meta.perm: 'menu:ai_chat'` | 有对话菜单的用户（正常配置） |
| `/embed/chat?ticket=` | 无菜单要求，凭 ticket 校验可执行性 | 角色只被授了智能体、没有对话菜单 |

因此：`hasMenuPerm('menu:ai_chat')` 为真 → 平台内对话页；否则 → 签票进 embed 面。
两条路径都**不带** `preview=1`，且 `openPreview` 一行未改。

### 11.3 其余落地的审查意见

- **`user_name` 缺失时保守返回空**：`created_by != ''` 会把「别人创建的历史数据」
  全算成「不是我创建的」。
- **URL 落地按角色守卫**（`isTabAvailableForRole`）：`all` 只对管理员有效、
  `assigned` 只对普通用户有效，初始化和 `watch` 两条路径都过守卫。顺手把既有问题
  「普通用户带 `?agents_tab=all` 落到无高亮视图」一并修掉。
- **加载占位**：`!assignedAgentsLoaded` 时给出占位，避免请求回来后面板高度跳变。
- **失败态与空态分开**：见 §6.5，失败不得伪装成「还没有智能体被分配给你」。
- **无对话菜单时取票失败 → 明确提示**，不再静默跳一个会被路由守卫拦住的页面。
- **网格断点对齐主列表**（`gap-6 md:2 lg:3`），避免切 tab 时列数变化。

### 11.4 明确不做的（记录以免反复讨论）

- **不补 `attach_*` 系列**（`system_status` / `readiness_*` / `tool_count` /
  `published_version_number` / `created_by_name`）：当前面板不渲染这些字段。
  将来若把只读卡片换成管理台卡片，必须一并补上，否则「看不出配置异常」。
- **不抽公共函数合并「只读三字段」算法**：`list_agents` 对同场景算出的也是
  `(False, False, False)`，硬编码等价；但规则一变就会漂移，届时以本节为准。
- **不动分享视图的头部计数**：它同样显示「共 N 个」而列出的是待接收件，属既有行为。
- **接口不做 `/assigned` 的 URL 归一**：管理员带 `?agents_tab=assigned` 会回落
  「全部智能体」，行为正确，只是地址栏保留原 query。

# MCP 工具集 · 业务分组与左栏重排（实现说明）

日期：2026-11-12
原型：[`prototypes/2026-11-12-mcp-workspace-regroup.html`](../../prototypes/2026-11-12-mcp-workspace-regroup.html)
契约测试：[`tests/frontend/test_mcp_group_tabs_contract.py`](../../tests/frontend/test_mcp_group_tabs_contract.py)

## 问题

「MCP 工具集」的平台 MCP 页有 55 个服务，全部平铺在约 400px 宽的左栏里：

- 左栏工具栏已经两行（搜索+新建 / 仅运行中+排序+条数）；
- 未选中服务时右半屏只有一句「请在左侧选择一个 MCP 服务查看工具」，全页最大的一块面积在说废话；
- 没有任何分布视图：谁没启用、谁一个工具都没发布、谁远端已失效、谁超 7 天没同步，只能一条条点开看。

分组不是「再加一个筛选条件」，而是补上唯一缺失的那一层——**分布导航**：筛选回答「怎么排除」，分组回答「有哪些、各几个」。

## 存储：新增 `sys_mcp_servers.group_name`

表里有两个「看起来能当分组」的字段，都不能用：

| 字段 | 真实用途 | 为什么不行 |
| --- | --- | --- |
| `remark` VARCHAR(500) | 自由备注，新建向导里随手写的一句话，前端已拿它做关键词搜索 | 自由文本不是枚举式归属；用它当分组等于把备注当分类，改一次文案就换一次组 |
| `scope` global/personal | 归属**范围**与权限维度（非 admin 访问 global 直接 403） | 混用会让「个人服务」与「平台服务」再也无法按同一业务域聚合 |
| `credential_mode` / `auth_headers` / `user_assertion_*` | 服务怎么认我们 | 凭据链路，与导航无关 |
| `enabled_status` / `last_sync_at` | 运行状态 | 已经是现有的筛选与排序维度 |

迁移：`db-prod/V215-add-mcp-server-group-name.sql`、`db-prod-pg/V115-add-mcp-server-group-name.sql`。
可空、无默认；存量行落 NULL 按「未分组」统计，不回填。平台与个人服务共用同一列，页签计数按 scope 各自统计，不混成一个数字。

与 `tools.group_name`、`meta_datasets.group_name`（V213）、`ai_agents.group_name`（V214）同构，四处页签共用
`frontend/src/utils/toolGroupTabs.ts` + `ToolGroupTabs.vue` 与后端 `app/utils/group_name.py`。

## 接口：专用轻端点，不复用整表单 PUT

`PUT /api/portal/mcp/servers/{id}/group`（权限 `element:mcp_management:edit`）只写 `group_name` 一列。

为什么不复用现有的 `PUT /api/portal/mcp/servers/{id}`：

1. 它是**整表单覆盖**：`server_name` / `sse_url` 必填，前端每次靠 `buildServerPayload(server)` 回传全部字段
   （`remark` 就是因为这个才单独做了 `model_fields_set` 保护）；
2. **改名会迁移工具引用**：智能体是按**工具名**绑定的，服务改名时后端要同步改写引用。分组与名字无关，不该走进这条重活；
3. **服务启用时保存会触发一次 `sync_tools`**：真去外部拉工具列表并覆盖缓存。用户只是想归个类，不该顺手打一次远端。

配套：整表单 PUT 里分组同样按 `model_fields_set` 局部落库（凭据面板等表单不带这个字段），响应回填库里的真实值；
新端点返回与列表同形的响应（含三个工具计数），否则改完分组卡片上的「已发布 N」会瞬间变 0。
编辑/新建表单也带 `group_name` 并在 `openEditModal` 里回填——漏了就等于「编辑一次配置丢一次分组」。

## 前端

### 过滤拆两段（决定页签数字是否正确）

```
serversMatchingFilters   仅运行中 → 待办筛选 → 关键词（服务名 / 备注 / 地址）
filteredServers          再套业务分组 → 排序
```

`computeGroupTabs(serversMatchingFilters.value.map(...), {}, UNGROUPED_MCP_GROUP)` 的 filters 传空，
于是每个数字恒等于「切到这一组能看到的条数」。若只把关键词交给纯函数，勾上「仅运行中」就会看到「页签说 5、点进去 3」。

**TDZ 守卫**：分组块必须声明在 `serversMatchingFilters` 之后。`watch([() => props.scope, mcpGroupTabsData], …)`
在 setup 期就会同步求值 computed getter 收集依赖，写在前面会直接 `ReferenceError` → 内容区白屏。
`test_group_block_declared_after_filtered_rows` 把顺序钉住（vue-tsc 与模板编译都查不出这类错误）。

### 左栏

- **分组页签**：搜索行之下、筛选行之上，直接复用 `ToolGroupTabs`（顺序、未分组殿后、溢出收进「更多分组」全网一致）。
  400px 宽度只常显 3 个分组是这套方案的固有代价，横向空间就这么多。
- **收起开关**：筛选行右端一个文件夹图标按钮，状态存 `localStorage('mcp-console:group-tabs-visible')`；
  一个分组都没建时（`items.length > 2` 为 false）连同页签整行不出现。
- **卡片密度**：紧邻的表格图标按钮切换宽松/紧凑，存 `localStorage('mcp-console:card-density')`。
  紧凑模式收起备注与同步时间，55 个服务时一屏多看一倍；「一个都没发布」这类要紧提示不隐藏。
- **分组徽标**：卡片名字右侧，点一下只看该组；锁定某组时不再重复显示（整页都是这一组，重复 55 次等于噪音）。

### 右栏空态 → 总览

未选中服务时展示「分组分布」（按当前筛选结果算，未分组用灰色）+「值得处理」四项可点待办：
未启用 / 一个工具都没发布 / 远端已失效 / 超过 7 天未同步。点一条待办就把它变成左栏筛选，
筛选行出现可清除的 chip。待办参与过滤链，因此页签数字会跟着变——这才是「页签数字 = 点进去的条数」。

## 验证

- `pytest --confcutdir=tests/frontend tests/frontend -q` → 全绿（新增 29 条：探针真跑 `toolGroupTabs.ts` + 源码/后端契约）
- `pytest tests/api -k mcp -q` → 164 passed
- `vue-tsc -b --force` → 157 errors（基线，无新增）
- 探针 `tests/frontend/mcp_group_tabs_probe.cjs`：14 条样例服务，逐组核对「页签数字 = 该组条数」、
  关键词命中备注/地址、四类待办参与计数、空白/null/缺失归未分组

## 未做（下一步）

- 批量启用 / 批量禁用 / 批量立即同步：55 个服务里改一批时需要多选。
- 键盘 `↑` `↓` 切换服务、`/` 聚焦搜索。
- 方案 C（全宽表格 + 详情抽屉）：留给「服务破百」之后再评估。

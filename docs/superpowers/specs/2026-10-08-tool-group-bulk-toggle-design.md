# 工具能力步骤：分组一键展开 / 一键折叠 + 搜索命中自动展开

**日期：** 2026-10-08
**需求（用户原话）：** 「这里工具分组很多，包括 MCP 工具这里也是，能不能支持一键展开，一键折叠之类」

---

## 1. 目标

「版本配置 → 工具能力」步骤里，系统工具按能力域分组、MCP 工具按服务名分组，分组数量多，逐个点分组标题展开/收起很费事。为此提供批量展开/折叠，并解决「搜索命中折叠分组时只能看到分组标题、看不到命中工具」的问题。

## 2. 范围

**做：**

- 工具栏（页签按钮左侧）新增「全部展开 / 全部折叠」两个按钮；
- 搜索关键词非空时，命中的分组一律展开（只读覆盖，不写回状态）；
- 新增 19 项前端契约测试。

**明确不做（已与用户逐项确认）：**

- **不做 Skills 页签**（该页签没有分组）；
- **不跨页签批量**（只作用于当前页签，避免「在看系统工具却顺手改了 MCP」）；
- **不持久化折叠状态**（沿用「关闭/重置抽屉即回到默认」）；
- 不加展开动画、不记忆上次折叠状态。

## 3. 交互与作用域

| 决策 | 取值 | 理由 |
| --- | --- | --- |
| 按钮位置 | 搜索框与「已选」计数之后、页签分段控件之前（同一行右侧） | 与「页签」视觉同组，作用域一眼可辨；用细边框 button group 与右侧分段控件区分 |
| 作用域 | 当前页签的**全部分组**（MCP 含「平台 MCP / 我的 MCP」两个子 scope） | 若用 `filtered*` 视图，搜索时批量操作会被悄悄缩小范围，清空搜索后又冒出一堆展开项 |
| 按钮显隐 | `toolTab !== 'skills'` | Skills 页签没有分组 |
| 点击幂等 | 不置灰，重复点击无副作用 | 避免为「是否已全展开」再引入一套状态判断 |

## 4. 搜索覆盖（只读）

- 折叠判定：`v-show="!isStaticGroupCollapsed(group.label) || isSearching"`（MCP 同理），箭头朝向同步加 `|| isSearching`，保证图标与内容一致；
- `isSearching = computed(() => props.toolSearchQuery.trim().length > 0)`；
- **不写回** `collapsedStaticGroups` / `collapsedMcpGroups`：展示层覆盖即可，清空搜索自动恢复，无需保存快照；
- 「命中分组」直接复用现有的 `filteredGroupedTools` / `filteredGroupedMcpTools`（已滤掉不含匹配项的分组）；
- **搜索期禁用分组标题的折叠按钮**（`:disabled="isSearching"` + 说明性 `title`）：点击本会被展示层覆盖成「没反应」，却已真实改写折叠集合，清空搜索后会突然变成折叠（延迟生效的困惑）。

## 5. 实现点

| 文件 | 改动 |
| --- | --- |
| `frontend/src/views/AgentManagement.vue` | 新增 `expandAllStaticGroups` / `collapseAllStaticGroups` / `expandAllMcpGroups` / `collapseAllMcpGroups`；展开 = `new Set()`，折叠 = 全量 key（`groupedTools` 的 label、`groupedMcpTools` 的 serverName）；抽屉上新增 4 个事件绑定 |
| `frontend/src/components/agent/AgentVersionEditorDrawer.vue` | 4 个 emit 声明、`isSearching`、`emitBulkExpandGroups` / `emitBulkCollapseGroups`、工具栏两个按钮（`aria-label` + `focus-visible:ring`）、4 处折叠判定加搜索覆盖、2 处分组标题按钮搜索期禁用 |
| `tests/frontend/test_tool_group_bulk_toggle_contract.py` | 新增 19 项契约测试 |

## 6. 测试

TDD：先写契约 → RED（18 failed / 1 passed，唯一通过的是既有的 `resetVersionEditorUi` 重置语义护栏）→ 实现 → GREEN（19 passed）。

契约覆盖：

1. 4 个批量处理器存在，且「全部折叠」用**全量** key（反向断言不得出现 `filteredGroupedTools` / `filteredGroupedMcpTools`）；
2. 「全部展开」清空对应折叠集合；
3. 两个按钮渲染在「系统工具」页签之前，且容器带 `toolTab !== 'skills'` 判定；
4. 搜索覆盖出现在 static 与 MCP 两处折叠判定所在行；
5. 批量操作不清空搜索框；
6. 仍是内存态（无 `localStorage` / `sessionStorage`）；
7. `resetVersionEditorUi` 的重置语义不变。

## 7. 边界与风险

- 折叠状态是内存态：切页签保留、关闭/重置抽屉后回到默认（与改动前一致）；
- MCP 的「全部折叠」同时覆盖两个子 scope 的分组——切到「我的 MCP」会看到也是折叠的，这符合「全部」的语义，已在实现注释中写明；
- 搜索期间为强制展开态（箭头也显示为展开），这是有意的：搜索的第一诉求是看到结果；
  该状态下分组标题的折叠按钮被禁用，避免「点了没反应、清空搜索后突然折叠」；
- 若后续要给 MCP 子 scope 做「只展开当前 scope」，批量 key **绝不能**取 `currentScopeGroupedMcpTools`（搜索过滤后的视图），必须另取未过滤的分组源——该约束已写进 `AgentManagement.vue` 的代码注释。

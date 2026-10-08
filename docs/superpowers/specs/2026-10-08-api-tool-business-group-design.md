# API 工具业务分组（`sys_api_tools.group_name`）设计

**日期：** 2026-10-08
**需求（用户原话）：** 「这块如果数据多了后，管理起来有点麻烦，特别是展示啊啥的，看看是不是有哪里可以优化的，另外，这个配置的工具，在智能体配置那边是不是归集到其他个工具分组里面的呢，同样，如果有很多工具的情况下，用户反馈管理起来不太方便。看看 UI,UX 有没有什么可以优化的」
**用户补充约束：** 「我截屏的这些不是我们环境的，不能写死处理」——迁移**只加列、不 UPDATE 任何行**，代码里不得出现任何客户前缀或环境相关的硬编码。

---

## 1. 问题（已核实的根因）

1. **智能体配置的分组是按工具名关键字硬编码猜测的**（`AgentManagement.vue` 的 `groupedTools`）：`startsWith('browser_')`、`includes('knowledge')`、`includes('email')`……最后 `else` 兜底进「其他扩展工具」。内置工具命名规范能命中，而通过「API 工具注册表」登记的自定义业务接口几乎不可能命中 → **全部落进兜底组**，该组因此变成「所有自定义 API 工具的默认抽屉」（用户截图里 64/67）。
2. **数据层没有分组维度**：`sys_api_tools` 只有 `name / description / method / url_template / headers / parameter_schema / is_active`。内置工具（`send_wechat_work_message` 等）与该表自定义工具**共处一张表**（内置走 SQL 迁移登记），但都没有业务分组信息。

## 2. 范围

**做：**

- `sys_api_tools` 新增可空列 `group_name`（业务分组名，中文可）；
- 注册表页（系统配置 → 工具管理）：分组列、表单字段（复用历史值）、按分组筛选、**多选 + 批量设置分组**、**选中当前筛选结果**；
- 后端新增批量设置分组端点（避免 64 次 PUT）；
- 智能体配置「系统工具」页签：分组判定改为「`group_name` 优先 → 现有关键字组 → 兜底」，自定义分组排在兜底组之前；只读态（已发布版本）默认折叠自定义分组；
- 两套独立迁移脚本（`db-prod/` 与 `db-prod-pg/`，编号各自自增）与契约测试。

**明确不做（已与用户确认）：**

- **不做 `tags` 多标签**：分组树里一个工具只有一个位置，多标签会让同一工具在多组重复出现、批量操作语义翻倍；将来若有「跨域筛选」需求再加，那时它服务筛选、不服务分组；
- **不写任何数据预置迁移**（不按前缀 UPDATE 客户数据），存量归类由用户在页面上通过批量能力完成；
- 不做「按名称前缀自动分桶」（属另一方案，本次不采用）；
- 不加索引（表规模几十~几百行）；
- 不改内置工具的现有关键字分组结果、不改 MCP 工具按 server 的分组、不改运行时装工具名与别名契约。

## 3. 数据层

| 项 | 取值 |
| --- | --- |
| 列 | `group_name VARCHAR(64) NULL`，NULL = 未分组 |
| MySQL | `db-prod/V165-add_api_tool_group_name.sql`，沿用既有 `ADD COLUMN … COMMENT …` 风格 |
| PostgreSQL | `db-prod-pg/V66-add_api_tool_group_name.sql`，沿用 `ADD COLUMN IF NOT EXISTS`（幂等） |
| 兼容性 | 老数据全 NULL → 分组结果与改动前**完全一致**（回落路径不变） |

Schema（`app/schemas/tool.py`）：`SysApiToolBase` / `SysApiToolUpdate` / `SysApiToolResponse` 均加 `group_name: Optional[str] = Field(None, max_length=64)`，并统一「首尾去空白 + 空白串归一为 `None`」，避免产生看不见的空分组；超长由 Pydantic 报 422（不静默截断）。

## 4. 智能体配置侧的分组回落

```
有 group_name → 按它分组（label = 分组名，图标 🔌）
否则          → 走现有 10 个关键字组逻辑（内置工具行为逐字不变）
都不命中      → 「其他扩展工具」兜底（排在最后）
```

- 分组顺序：内置 10 组（保持原顺序）→ 自定义分组（工具数降序，同数量按名称）→ 兜底组；
- 折叠状态按 **label** 存储（改动前即如此），动态分组天然兼容，折叠逻辑不需要改；
- `groupedTools` 由 `Record<ToolGroupKey, ToolGroup>` 改为 `ToolGroup[]`（数组天然支持动态分组），同步 `filteredGroupedTools`；顺带修正 `getStaticGroupTools` 在 `Record` 上调用 `.find` 的隐患；
- 只读态（`canEditVersion === false`，如已发布版本）默认折叠自定义分组，只展开内置组并显示每组已选计数。

## 5. 测试

| 层 | 覆盖 |
| --- | --- |
| 后端 schema | 去空白、空白归一 `None`、超长 422、ORM → Response round-trip |
| 后端端点 | 批量设置分组（含清空为 NULL、非法 id、需要 `element:system:config_save` 权限） |
| 迁移 | MySQL / PG 编号独立自增、PG 幂等写法、只加列不 UPDATE 数据 |
| 前端契约 | 注册表页存在分组列/表单/筛选/多选批量入口；智能体配置「group_name 优先」且无 `group_name` 时回落结果与现状一致、不产生空分组；只读态折叠自定义分组 |

---

## 6. 实现补充与偏差（实现后回填）

**注册表页的最终交互**（比初稿更明确）：

- 「业务分组」列（空值显示 `—`）、表单字段（`datalist` 复用已有分组名）、按分组筛选（含「未分组」哨兵值 `UNGROUPED_FILTER`）；
- 归类主路径：勾选列 + 「选中当前筛选结果」+ 「批量设置分组」弹窗（留空即清除分组）。**通用**：先用搜索框筛出前缀，再全选结果设置分组，不依赖任何命名规范；
- 后端配套批量端点 `POST /api/portal/tools/batch-group`（避免 64 次 PUT），权限依赖抽成具名对象 `SYSTEM_CONFIG_SAVE_PERMISSION` 以便测试覆盖。

**只读态默认折叠**的实现：`isStaticGroupCollapsed` 在只读态（`canEditVersion === false`）下对业务分组返回「未手动展开即为折叠」，手动展开记在独立的 `expandedBusinessGroups`（`resetVersionEditorUi` 时清空），因此不会污染可编辑态的 `collapsedStaticGroups`；搜索期强制展开仍然优先生效。

**实现中发现的真实缺陷**：`allAvailableTools` 把 `dynamicTools` 映射为统一结构时漏传 `group_name` —— `vue-tsc` 报 `TS2339`，且即使类型放宽，分组也会静默失效（全部落回兜底组）。已修复，并补上「映射必须透传 `group_name`」的契约断言。

**迁移不更新 PG `V0-baseline.sql`**：该文件是历史结构快照，既有加列迁移（如 `V64-add-user-email.sql`）同样未回填，新环境按 `V0 → V66` 顺序执行即可（`IF NOT EXISTS` 保证幂等）。

**变异验证**：临时改坏 5 处（迁移偷加 `UPDATE` / 动态工具映射漏传 `group_name` / 关掉业务分组优先分支 / 删掉「批量设置分组」按钮 / 去掉空白归一化），5/5 被捕获；其中「批量设置分组」一条初次漏检（断言匹配到了弹窗标题的同名字样），已收紧为校验按钮本体。

**验证结果**：后端契约 16 passed；后端 `tests/api/portal` + `tests/ai/tools` 504 passed / 8 xfailed；前端契约全量 1506 passed / 1 skipped；`vue-tsc` 62 = 既有基线（改动文件零错误）；SFC 编译 0 错误。未执行数据库迁移。

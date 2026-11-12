# 元数据管理：业务分组页签与筛选（方案 A）设计说明

日期：2026-11-12
原型：[`prototypes/2026-11-12-metadata-dataset-group-tabs.html`](../prototypes/2026-11-12-metadata-dataset-group-tabs.html)
关联：工具注册表的同类能力见 `docs/superpowers/specs/2026-11-12-tool-group-tabs-design.md`

## 一、需求与结论

用户诉求：「元数据管理里面也支持这样的业务分组，包括搜索」——新建/编辑能填分组、顶部能按它筛、
页面能按分组多 Tab。原型确认后用户回复「新增字段，按 a」，即原型里的方案 A。

**核心结论：新增单值 `meta_datasets.group_name`，不复用已有的 `tags`。**

原因（读码得出，不是推测）：

| 字段 | 值的形态 | 谁在读 | 本次怎么处理 |
| --- | --- | --- | --- |
| `tags`（已有） | 多值 JSON | `services/ai/config.py` 的 `_format_dataset_menu` 拼进「已授权数据集目录」提示词 → 影响 ChatBI 选库/澄清/门户推荐；智能体绑定数据集的搜索也匹配它 | 不动。继续管「AI 认识这个数据集有什么属性」 |
| `group_name`（本次新增） | 单值 | 只有列表页的页签与筛选 | 新增。管「这个数据集归哪个业务域」 |

拿 `tags` 直接做页签会立刻出现三个问题（原型的方案 B 可复现）：
1. 一个数据集挂多个标签 → 出现在多个页签，**各页签计数相加大于总数**；
2. 没有「未分组」——没打标签的数据集永远只在「全部」里，找不到漏网项；
3. 标签是 AI 选库口径，拿来当导航会把「治理口径」与「导航口径」绑死，以后想改标签就动导航。

## 二、数据与接口

### 迁移

- MySQL：`db-prod/V213-add-dataset-group-name.sql`
- PostgreSQL：`db-prod-pg/V113-add-dataset-group-name.sql`

`VARCHAR(64) NULL`，无默认值、无回填：存量行落 NULL，前端按「未分组」统计。
两份都要幂等——MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，靠 `apply_sql.py` 忽略 1060；
PG 执行器整文件一个事务且不吞错误，所以用 `ADD COLUMN IF NOT EXISTS` + 可重复的 `COMMENT ON COLUMN`。

### Schema / 服务

- `app/schemas/metadata.py`：`DatasetBase`（创建 + 响应）、`DatasetUpdate`、`CloneDatasetRequest` 三处都要收
  —— 少一处就会出现「表单填了但存不进去」或「列表查得出来但改不了」。
- `app/services/metadata_service.py`：create / update / delete 的审计 `old_data` / `new_data` 都加 `group_name`
  —— 否则审计里看不出「谁把这个数据集挪到别的业务域了」。
- `app/services/dataset_clone_service.py`：复制数据集默认沿用源分组（`payload.group_name` 显式给了才覆盖）。
  复制出来的库通常仍属同一业务域，落成「未分组」等于让用户再归一次类。

## 三、前端

页面：`frontend/src/views/MetadataDatasets.vue`

- **复用共用件**：`utils/toolGroupTabs.ts` 的 `computeGroupTabs` / `toolGroupName` +
  `components/system/ToolGroupTabs.vue`。两个页面的页签顺序、计数口径、溢出方案（「更多分组」）因此完全一致。
- **两处形状差异在页面侧适配**，不污染公共模块：
  1. 数据集是 `status` 整型（1 = 启用）→ 页面映射成 `is_active` 布尔再传；
  2. 数据集搜索命中 `name / display_name / description` → 显式传 `searchFields`（工具的默认字段不含 `display_name`）。
- **一份状态**：页签 `v-model`、卡片分组徽标点击、列表分组标签引用同一个 `groupFilter`。
  点卡片上的徽标 = 点页签，不存在两套筛选。
- **不重复实现过滤**：页面只消费纯函数结果，源码契约直接断言 `matchesToolFilters` 不出现在页面里。
- **去重显示**：锁定某一组时，卡片与列表都不再逐条显示分组（每条都是这个组，纯重复信息）。
- **兜底**：选中分组被搜空或分组名被改名后回「全部」；用 `datasets.length > 0` 跳过首屏加载期，
  否则刚选的分组会在加载完成前被清掉。
- **表单**：新建与编辑都加「业务分组」输入，配 `datalist` 复用已有分组（避免「财务」「财务核算」并存）；
  提交前 `normalizeGroupName` 把空串 / 纯空格归一成 `null`，否则「空格分组」会变成一个真实页签。
- **分页**：切分组时回到第一屏（`visibleCount = PAGE_SIZE`）。

## 四、文案口径

「业务分组」的说明必须与「业务标签」明确分工，界面上两处 tooltip 各说各的：

- 业务分组：单选、只用于列表页分组页签与筛选、**不参与数据权限判定**、**不会写进 AI 提示词**；
  要让 AI 认识业务属性用「业务标签」。
- 业务标签：会随「已授权数据集目录」提供给 AI，影响 ChatBI 选库/澄清/门户推荐；不参与数据权限判定。

## 五、测试

`tests/frontend/test_metadata_dataset_group_tabs_contract.py`（22 条）+ `metadata_dataset_group_probe.cjs`（真跑 TS 模块）：

- 页签顺序与计数：全部 → 各组（命中数降序）→ 未分组（固定末位）；
- 状态筛选参与页签计数（整型 → bool 的映射链不断）；
- `searchFields` 真的生效：「交易事实」只出现在 display_name，带该字段命中 1、用默认字段命中 0；
- 关键词把某组搜空 → 该组页签消失；
- 全部有分组时不出现「未分组」；空串 / 空格 / null 都归入「未分组」；
- 页签「全部」上的数字 == 逐条 `matchesToolFilters` 命中数 == 各组之和（同源判定）；
- 源码契约：组件挂载、共用 `groupFilter`、锁定分组时隐藏徽标、表单两处字段 + datalist、提交归一、
  兜底 watch、文案分工；
- 后端契约：模型列、三处 schema、审计字段、复制沿用、两份迁移文件存在且写法幂等。

验证结果：前端契约测试 4747 passed / 1 skipped；`vue-tsc -b --force` 157 errors（与基线一致）。

## 六、不做的事

- 分组不改名、不合并、不删除（没有「分组管理」）；同名靠 `datalist` 候选引导。
- 分组不参与数据权限判定，也不进 AI 提示词（要让 AI 按业务域理解数据集，应改「已授权数据集目录」的拼接方式，
  那是另一件事，需单独评估对 ChatBI 的影响）。
- 表详情页（`MetadataTables.vue`）与权限总览页不做分组。

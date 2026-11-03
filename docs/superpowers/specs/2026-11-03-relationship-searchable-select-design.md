# 实体关系弹窗下拉支持搜索设计

- 日期：2026-11-03
- 状态：待评审
- 相关：`frontend/src/components/metadata/RelationshipList.vue`、`frontend/src/components/common/ModelSelect.vue`、`frontend/src/api/metadata.ts`

## 1. 背景与问题

「新建 / 编辑实体关系」弹窗有 4 个原生 `<select>`：源表、目标表、源关联字段、目标关联字段。目标表还按「当前数据集 / 跨数据集」分组展示。

原生 `<select>` 只能逐条滚动：表一多（尤其跨数据集全平台表列表），找一个表要翻很久；字段多时同理。用户希望这些下拉支持搜索。

平台里已有先例：模型选择器 `ModelSelect.vue` 用「触发器 + Teleport 浮层 + 搜索框」，证明这套交互在本项目里可维护、可测试。本次把同样的能力抽成与业务无关的通用组件，先用于这个弹窗。

## 2. 目标与非目标

### 目标

1. 新增通用组件 `SearchableSelect.vue`：支持输入过滤、分组标题、空选项、键盘导航、防裁剪浮层。
2. 弹窗内 4 个原生 `<select>` 全部替换为该组件。
3. 展示文案、分组、`v-model` 绑定的数据类型（表为 `number`，字段为 `string`）与现有逻辑完全兼容。
4. 搜索同时命中展示文案与补充关键词（中文业务名 `term`、物理字段名）。
5. 用契约测试 + SSR 渲染测试锁住能力，防止后续被改回原生 select。

### 非目标

- 不改 `applyJoinCondition`、跨数据集字段推断等业务逻辑。
- 不动 `ModelSelect.vue` 及使用它的版本配置 / 系统配置页面。
- 不替换本页之外（如元数据其它表单）的下拉。
- 不做虚拟滚动；单次候选规模是「数据集级表数量 / 单表字段数量」，常规几十到几百条，普通滚动足够。

## 3. 组件设计

### 3.1 文件与接口

新文件 `frontend/src/components/common/SearchableSelect.vue`（`<script setup lang="ts">`）。

```ts
export type SearchableSelectOption = {
  value: string | number
  label: string
  /** 只参与搜索、不展示的补充关键词，例如中文业务名、物理字段名 */
  keywords?: string[]
}

export type SearchableSelectGroup = {
  /** 分组标题；为空时不渲染标题 */
  label?: string
  options: SearchableSelectOption[]
}

props:
  modelValue: string | number | null
  groups: SearchableSelectGroup[]
  placeholder?: string          // 未选中时的触发器文案，默认「请选择...」
  disabled?: boolean
  allowEmpty?: boolean          // 字段选择需要空值
  emptyLabel?: string           // 空选项文案，默认「-- 请选择 --」
  emptyValue?: string | number | null  // 选中空选项时 emit 的值，默认 null；字段场景传 ""
  searchPlaceholder?: string    // 默认「搜索...」
  emptyText?: string            // 没有任何候选时的文案，默认「暂无可选项」；搜索无结果固定显示「无匹配项」
  ariaLabel?: string            // 触发器无障碍名称
  size?: 'md' | 'sm'            // md 对齐表选择框，sm 对齐字段选择框
```

事件：`update:modelValue`（选中或清空时触发一次）。

### 3.2 交互行为

- 触发器是 `<button role="combobox">`，展示当前选项 `label`；无值显示 `placeholder`（灰色）；右侧箭头随开合旋转。
- 点击打开浮层，浮层顶部固定一个搜索框并自动聚焦；输入即过滤（大小写不敏感）。
- 搜索匹配范围：`label` + `keywords` 任一命中即保留。
- `allowEmpty` 时空选项置顶且**不参与过滤**，避免用户以为「没得选」；点击后 emit `emptyValue`（默认 `null`，字段场景传 `""`）。`groups` 为空时不渲染空选项。
- 选中后关闭浮层并把焦点还给触发器；`Escape` 关闭；点击浮层外关闭。
- 键盘：`ArrowDown` / `ArrowUp` 在候选间移动并滚动到可视区（循环），`Enter` 选中当前高亮项，`Escape` 关闭；打开时若当前值有对应项，高亮它。
- 分组：有 `label` 的分组渲染标题（小号灰字），候选按分组顺序展开。
- 空态：搜索无结果时显示 `emptyText`；`groups` 全空时显示同一文案。
- 无障碍：`aria-haspopup="listbox"`、`aria-expanded`、`role="listbox"`、`role="option"` + `aria-selected`，触发器 `aria-label` 取对应字段名。

### 3.3 浮层与防裁剪

弹窗容器是 `overflow-hidden`，浮层必须在 `Teleport to="body"` 中渲染，并用 `position: fixed` 跟随触发器矩形定位：

- 宽度 = 触发器宽度；`z-index` 高于弹窗遮罩（沿用 ModelSelect 的 `10100`）。
- 下方空间不足且上方更宽裕时向上弹。
- `resize` / 捕获阶段 `scroll` 时重新定位；浮层内部滚动不重定位，避免列表抖动。

## 4. RelationshipList 接入

| 位置 | 数据来源 | 分组 | 空选项 |
|---|---|---|---|
| 源表 (Left) | `props.tables` | 无（单组） | 否 |
| 目标表 (Right) | 当前数据集 + `allTablesList` 中其它数据集 | 「当前数据集」「<数据集名> [跨数据集]」 | 否 |
| 源关联字段 | `sourceColumns` | 无（单组） | 是，`emptyLabel="-- 选择字段 --"` |
| 目标关联字段 | `targetColumns` | 无（单组） | 是，`emptyLabel="-- 选择字段 --"` |

- 表选项 `label` 与现有文案一致：`physical_name (term)`；字段选项：`physical_name [term]`。
- 中文名同时放进 `keywords`，保证输入中文也能命中。
- 字段下拉在 `columns.length === 0` 时保留可点开，浮层内显示「暂无可选字段」；不禁用，避免编辑跨库关系时连清空已选字段都做不到。
- 字段下拉传 `empty-value=""`，与 `sourceField` / `targetField` 的 `string` 类型保持一致，不引入 null 分支。
- `v-model` 继续绑定 `form.source_table_id` / `form.target_table_id` / `sourceField` / `targetField`，`@update:model-value` 后调用 `applyJoinCondition()`（等价于原 `@change`），保证关联条件自动拼接行为不变。
- 跨数据集分组过滤逻辑（排除已在当前数据集出现的表）原样搬进 `computed`，不改变 `allTablesList` 的请求时机。

## 5. 测试

1. **契约测试** `tests/frontend/test_relationship_searchable_select_contract.py`（纯源码断言，`pytest.mark.no_infrastructure`）：
   - 组件存在且包含 Teleport、`position: fixed`、搜索框、`ArrowDown/ArrowUp/Escape`、`role="listbox"/"option"`、分组渲染、空态文案。
   - 弹窗区块内 4 处 `<SearchableSelect`，且该区块不再出现 `<select`。
   - 目标表分组文案「当前数据集」「跨数据集」仍在；字段空选项 `-- 选择字段 --` 仍在。
   - 绑定仍是 `form.source_table_id` / `form.target_table_id` / `sourceField` / `targetField`。
2. **SSR 真渲染** `tests/frontend/searchable_select_ssr.cjs` + pytest 包装：编译真实 SFC，验证已选值文案、占位文案、空选项文案、禁用态、未展开时不产生浮层。
3. 验证命令：
   - `pytest --confcutdir=tests/frontend tests/frontend/test_relationship_searchable_select_contract.py`
   - `cd frontend && npx vue-tsc --noEmit`

## 6. 风险与取舍

- **行为差异**：原生 select 支持输入首字母跳转，自定义组件用搜索替代，功能更强，不构成回退。
- **浮层被弹窗遮罩盖住**：通过 Teleport + 高 z-index 规避；如果后续弹窗层级调整，需要同步 z-index（ModelSelect 是同一约定）。
- **字号/高度对齐**：表下拉用 `size="md"`（与现有 `text-sm py-2` 一致），字段下拉用 `size="sm"`（`text-xs py-1`），避免视觉跳动。

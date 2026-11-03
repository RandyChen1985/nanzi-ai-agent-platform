# 实体关系弹窗下拉支持搜索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「新建 / 编辑实体关系」弹窗的源表、目标表、源关联字段、目标关联字段下拉支持输入搜索，同时保留分组、空选项与既有绑定逻辑。

**Architecture:** 新建与业务无关的 `SearchableSelect.vue`（触发器 + Teleport 浮层 + 搜索框 + 键盘导航），在 `RelationshipList.vue` 中用 4 个组件实例替换原生 `<select>`；业务逻辑（`applyJoinCondition`、跨数据集列推断）不动。

**Tech Stack:** Vue 3 `<script setup lang="ts">`、Tailwind CSS 3、pytest 契约测试、`vue/compiler-sfc` + `@vue/server-renderer` SSR 渲染测试、`vue-tsc --noEmit`。

---

### Task 1: 通用可搜索下拉组件

**Files:**
- Create: `frontend/src/components/common/SearchableSelect.vue`

- [ ] **Step 1: 定义类型与 props**

```ts
export type SearchableSelectOption = {
  value: string | number
  label: string
  keywords?: string[]
}
export type SearchableSelectGroup = {
  label?: string
  options: SearchableSelectOption[]
}
```

props：`modelValue`、`groups`、`placeholder`、`disabled`、`allowEmpty`、`emptyLabel`、`emptyValue`、`searchPlaceholder`、`emptyText`、`ariaLabel`、`size`。emit `update:modelValue`。

- [ ] **Step 2: 实现过滤、定位、键盘与外部点击**

- 平铺候选并保留分组，搜索匹配 `label + keywords`（小写）。
- `allowEmpty` 时空选项置顶且不参与过滤；`groups` 为空时不渲染空选项。
- Teleport 到 body + `position: fixed`，宽度对齐触发器，下方不足时向上弹；`resize` / 捕获 `scroll` 重新定位，浮层内滚动不重定位。
- `ArrowDown` / `ArrowUp` 循环移动并 `scrollIntoView`，`Enter` 选中，`Escape` 关闭，点击外部关闭。
- 选中后 emit `update:modelValue`、关闭浮层、焦点还给触发器。

- [ ] **Step 3: 模板**

触发器 `<button role="combobox" aria-haspopup="listbox" :aria-expanded>`，展示选中 `label` 或灰色占位；`size="md"` 对应 `text-sm px-3 py-2`，`size="sm"` 对应 `text-xs px-2 py-1`。浮层含搜索框、分组标题、候选按钮（`role="option"` + `aria-selected`）、空态文案。

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

---

### Task 2: 接入实体关系弹窗

**Files:**
- Modify: `frontend/src/components/metadata/RelationshipList.vue`

- [ ] **Step 1: script 里组装候选项**

新增 `import SearchableSelect from "../common/SearchableSelect.vue"`，新增 computed：

- `sourceTableOptions`：`props.tables` → `label: "physical_name (term)"`，`keywords: [physical_name, term]`
- `targetTableGroups`：第一组「当前数据集」= `props.tables`；其余每组 `label: "<display_name || dataset_name> [跨数据集]"`，过滤掉已在当前数据集出现的表 id
- `sourceFieldOptions` / `targetFieldOptions`：`physical_name [term]`，`keywords` 含 `physical_name`、`term`
- `sourceFieldModel` / `targetFieldModel`：string 双向绑定（`update:model-value` 后写回 string 并调用 `applyJoinCondition()`）

- [ ] **Step 2: 替换模板**

四个 `<select>` 换成：

```vue
<SearchableSelect
  v-model="form.source_table_id"
  :groups="[{ options: sourceTableOptions }]"
  aria-label="源表"
  placeholder="选择源表..."
/>
<SearchableSelect
  v-model="form.target_table_id"
  :groups="targetTableGroups"
  aria-label="目标表"
  placeholder="选择目标表..."
/>
<SearchableSelect
  v-model="sourceFieldModel"
  :groups="[{ options: sourceFieldOptions }]"
  :allow-empty="true"
  empty-label="-- 选择字段 --"
  empty-value=""
  empty-text="暂无可选字段"
  aria-label="源关联字段"
  size="sm"
  placeholder="-- 选择字段 --"
/>
<SearchableSelect
  v-model="targetFieldModel"
  :groups="[{ options: targetFieldOptions }]"
  :allow-empty="true"
  empty-label="-- 选择字段 --"
  empty-value=""
  empty-text="暂无可选字段"
  aria-label="目标关联字段"
  size="sm"
  placeholder="-- 选择字段 --"
/>
```

保留 `关联字段` 小标题、`源表 (Left)` / `目标表 (Right)` 文案与网格布局。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

---

### Task 3: 测试

**Files:**
- Create: `tests/frontend/searchable_select_ssr.cjs`
- Create: `tests/frontend/test_relationship_searchable_select_contract.py`

- [ ] **Step 1: SSR 渲染脚本**

参照 `tests/frontend/model_select_ssr.cjs`：编译真实 SFC，分别渲染「已选值 / 占位 / 空选项 / 禁用 / 未展开」五种情形，输出 JSON：`showsSelectedLabel`、`showsPlaceholder`、`showsEmptyLabel`、`triggerDisabled`、`closedNoListbox`。

- [ ] **Step 2: 契约测试**

断言：

- `SearchableSelect.vue` 含 `Teleport to="body"`、`position: 'fixed'`、`searchPlaceholder`、`ArrowDown`、`ArrowUp`、`Escape`、`role="listbox"`、`role="option"`、分组标题渲染、空态文案。
- `RelationshipList.vue` 弹窗区块内 `<SearchableSelect` 出现 4 次且无 `<select`；仍含「当前数据集」「跨数据集」「-- 选择字段 --」；绑定 `form.source_table_id`、`form.target_table_id`、`sourceFieldModel`、`targetFieldModel`；`applyJoinCondition` 仍在。
- SSR 测试用 `test_searchable_select_ssr.py` 调用 node 脚本并断言 JSON。

- [ ] **Step 3: 运行**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_relationship_searchable_select_contract.py tests/frontend/test_searchable_select_ssr.py -v`
Expected: 全部 PASS

---

### Task 4: 整体验证

- [ ] `cd frontend && npx vue-tsc --noEmit` → 无新增错误
- [ ] `pytest --confcutdir=tests/frontend tests/frontend/test_relationship_searchable_select_contract.py tests/frontend/test_searchable_select_ssr.py -v`
- [ ] 回归 `pytest --confcutdir=tests/frontend tests/frontend/test_model_select_contract.py -v`

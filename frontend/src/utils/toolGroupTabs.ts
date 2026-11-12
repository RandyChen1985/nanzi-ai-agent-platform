/**
 * 工具注册表「业务分组页签」的纯计算：过滤口径、每组命中数、页签顺序。
 *
 * 为什么抽成纯函数：页签计数与列表过滤必须是**同一套**判定（关键词 / HTTP 方法 / 状态）。
 * 各写一份的话，改了一处另一处不动，就会出现「页签写着 6 个，切过去只有 2 个」这种
 * 说不清是数据错还是显示错的故障。抽出来之后可以在 Node 里直接跑断言，不用为了验证
 * 一个 computed 去挂整个组件。
 *
 * 两处共用：API 工具页（ToolRegistry.vue）与脚本工具页（ScriptToolList.vue）。
 * 两页的筛选维度不完全一样（脚本工具多一个「来源」），所以过滤条件由调用方以 `ToolFilterState` 传入。
 *
 * 注意两个「计数」是不同的东西，别混用：
 * - 本文件的 `computeGroupTabs` 是**筛选后**的命中数，给页签与筛选面板用；
 * - `groupFilterOptions`（组件内）是**全量**计数，给表单分组下拉与「分组管理」用
 *   ——那里问的是「这个组一共有多少工具」，跟着搜索框变就是错的。
 */

export interface GroupableTool {
  name?: string | null
  display_name?: string | null
  description?: string | null
  url_template?: string | null
  method?: string | null
  /** 脚本工具用「内置 / 自定义」区分来源，API 工具没有这个维度 */
  source?: string | null
  is_active?: boolean
  group_name?: string | null
}

/** 关键词参与匹配的字段；两类工具该搜的字段不同，由调用方指定 */
export type ToolSearchField = 'name' | 'display_name' | 'description' | 'url_template'

export const DEFAULT_SEARCH_FIELDS: readonly ToolSearchField[] = ['name', 'description', 'url_template']

export interface ToolFilterState {
  /** 关键词，内部会 trim + 转小写 */
  keyword?: string
  /** 'all' 或具体 HTTP 方法 */
  method?: string
  /** 'all' | 'active' | 'inactive' */
  status?: string
  /** 'all' | 'builtin' | 'custom'（仅脚本工具使用） */
  source?: string
  /** 关键词匹配哪些字段，缺省 name / description / url_template */
  searchFields?: readonly ToolSearchField[]
}

export interface GroupTabItem {
  key: string
  label: string
  count: number
}

export interface GroupTabsResult {
  total: number
  ungrouped: number
  /** `[分组名, 命中数]`，按命中数降序、同数量按中文名排序 */
  groups: Array<[string, number]>
  /** 可直接渲染的页签序列：全部 → 各组 → 未分组（未分组仅在 > 0 时出现，且固定末位） */
  items: GroupTabItem[]
}

export const ALL_GROUP_KEY = 'all'

/**
 * 关键词 / HTTP 方法 / 状态三层过滤，**不含业务分组**。
 *
 * 页签要回答的是「切到这一组，当前条件下还能看到几个」；计数里若把分组条件也算进去，
 * 未选中的组永远显示完整数量，点过去才发现是空的。
 */
export function matchesToolFilters(tool: GroupableTool, filters: ToolFilterState = {}): boolean {
  const keyword = String(filters.keyword || '').trim().toLowerCase()
  const fields = filters.searchFields?.length ? filters.searchFields : DEFAULT_SEARCH_FIELDS
  const matchesKeyword =
    !keyword ||
    fields.some((field) => String(tool[field] || '').toLowerCase().includes(keyword))

  const source = String(filters.source || ALL_GROUP_KEY)
  const matchesSource = source === ALL_GROUP_KEY || String(tool.source || '') === source

  const method = String(filters.method || ALL_GROUP_KEY)
  const matchesMethod = method === ALL_GROUP_KEY || String(tool.method || '').toUpperCase() === method

  const status = String(filters.status || ALL_GROUP_KEY)
  const matchesStatus =
    status === ALL_GROUP_KEY ||
    (status === 'active' && Boolean(tool.is_active)) ||
    (status === 'inactive' && !tool.is_active)

  return matchesKeyword && matchesMethod && matchesStatus && matchesSource
}

export function toolGroupName(tool: GroupableTool): string {
  return String(tool.group_name || '').trim()
}

/**
 * 聚合出页签数据。
 *
 * @param ungroupedKey 「未分组」页签的哨兵值，由组件传入（与筛选下拉共用同一个常量），
 *   这里不自己造一个，否则两处哨兵不一致时点页签等于没点。
 */
export function computeGroupTabs(
  tools: readonly GroupableTool[],
  filters: ToolFilterState = {},
  ungroupedKey = '__ungrouped__',
): GroupTabsResult {
  const counts = new Map<string, number>()
  let ungrouped = 0
  let total = 0

  for (const tool of tools) {
    if (!matchesToolFilters(tool, filters)) continue
    total += 1
    const name = toolGroupName(tool)
    if (!name) {
      ungrouped += 1
      continue
    }
    counts.set(name, (counts.get(name) || 0) + 1)
  }

  const groups: Array<[string, number]> = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-Hans-CN'),
  )

  const items: GroupTabItem[] = [{ key: ALL_GROUP_KEY, label: '全部', count: total }]
  for (const [name, count] of groups) items.push({ key: name, label: name, count })
  if (ungrouped > 0) items.push({ key: ungroupedKey, label: '未分组', count: ungrouped })

  return { total, ungrouped, groups, items }
}

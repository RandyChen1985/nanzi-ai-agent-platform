/**
 * 业务角色搜索的过滤口径。
 *
 * 为什么单独抽出来：用户弹窗原先只按 `name` 过滤，而老库里 `default` 角色的 name
 * 往往是「默认用户」之类的本地化文案、只有 code 是 `default`——搜 `de` 命中 code
 * 却匹配不到 name，列表就空了（角色管理页能搜到，因为后端 search 同时匹配两列）。
 *
 * 规则：name / code 任一包含关键词即保留；大小写不敏感；忽略前后空格；
 * 字段缺失（null / undefined）按空字符串处理，不让脏数据把整个下拉打挂。
 */

export interface SearchableRole {
  name?: string | null
  code?: string | null
}

export function filterBusinessRoles<T extends SearchableRole>(
  roles: readonly T[] | null | undefined,
  keyword: string | null | undefined,
): T[] {
  const list = Array.isArray(roles) ? roles : []
  const query = (keyword ?? '').trim().toLowerCase()
  if (!query) return [...list]

  return list.filter((role) => {
    const name = (role?.name ?? '').toLowerCase()
    const code = (role?.code ?? '').toLowerCase()
    return name.includes(query) || code.includes(query)
  })
}

export interface SelectedRoleSummary {
  /** 已勾选的角色数（以表单里的 id 为准，与当前搜索结果无关） */
  selectedCount: number
  /** 已勾选但不在当前列表里的数量：保存时会保留，界面上要说明，否则像是丢了 */
  missingCount: number
}

/**
 * 统计「已选角色」与「当前列表能看到多少」。
 *
 * 角色列表按创建时间倒序只回一页，老角色（如 default）经常不在第一页；
 * 编辑已有用户时，他的角色很可能一个都不在列表里，必须让界面说清楚。
 */
export function summarizeSelectedRoles(
  selectedIds: readonly (string | number)[] | null | undefined,
  roles: readonly { id?: string | number | null }[] | null | undefined,
): SelectedRoleSummary {
  const selected = Array.isArray(selectedIds) ? selectedIds : []
  const list = Array.isArray(roles) ? roles : []
  const visibleIds = new Set(list.map((role) => role?.id))
  const missingCount = selected.filter((id) => !visibleIds.has(id)).length
  return { selectedCount: selected.length, missingCount }
}

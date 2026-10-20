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

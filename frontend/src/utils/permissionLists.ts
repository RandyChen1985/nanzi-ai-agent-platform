/**
 * 用户权限的「直配 vs 角色继承」取数与提交口径——用户页的唯一来源。
 *
 * 为什么必须有这一层：`GET /users/{id}/permissions` 返回的 `permissions` 是
 * **用户 ∪ 所属角色** 的并集，而 `PUT` 写的是**用户直配**。用户页原先拿并集回显再整份
 * PUT 回去，于是角色级禁用被固化成用户级记录——角色页之后取消禁用，该用户仍被禁；
 * 反向操作（用户在用户页取消角色级禁用）无效却提示成功。
 *
 * 后端已新增 `direct_permissions`（仅直配）。这里的规则是：
 *   - 有 `direct_permissions`：可编辑 / 可提交 = 直配；角色来源 = 并集 − 直配（只读展示）；
 *   - 缺失或形状不对（旧后端 / 脏数据）：整块回退成 `permissions`，绝不当作"用户没有直配项"
 *     ——否则一保存就把真实配置整体清空。
 *
 * 所有列表一律容忍坏数据（null / 非数组 / 非字符串元素），按空数组处理：接口字段缺失
 * 不该让整页白屏，更不该让下一次保存把线上配置写坏。
 */

/** 与后端 `PermissionUpdate` 字段一一对应；顺序固定，便于测试与 payload 比对。 */
export interface PermissionLists {
  agents: string[]
  datasets: string[]
  metadata: string[]
  apis: string[]
  menus: string[]
  elements: string[]
  forbidden_tools: string[]
  forbidden_commands: string[]
  forbidden_skills: string[]
  models: string[]
}

export const PERMISSION_LIST_KEYS = [
  'agents',
  'datasets',
  'metadata',
  'apis',
  'menus',
  'elements',
  'forbidden_tools',
  'forbidden_commands',
  'forbidden_skills',
  'models',
] as const

/** 只有这三个键存在「用户直配 / 角色继承」的并集问题，其余字段没有两级作用域。 */
export const FORBIDDEN_LIST_KEYS = [
  'forbidden_tools',
  'forbidden_commands',
  'forbidden_skills',
] as const

export type ForbiddenListKey = (typeof FORBIDDEN_LIST_KEYS)[number]

/** 角色来源的禁用项：界面只读展示，永远不进 payload。 */
export type InheritedForbiddenLists = Record<ForbiddenListKey, string[]>

export interface UserPermissionEditState {
  /** 可编辑、且将作为 PUT payload 的直配项 */
  direct: PermissionLists
  /** 并集里有、直配里没有的禁用项（「来自角色」） */
  inherited: InheritedForbiddenLists
  /** 并集里有、直配里没有的菜单（「来自角色」，只读展示） */
  inheritedMenus: string[]
  /** 并集里有、直配里没有的功能点（「来自角色」，只读展示） */
  inheritedElements: string[]
  /** true = 后端没给可用的 `direct_permissions`，已回退成并集，此时无法区分继承 */
  usingEffectiveFallback: boolean
}

/** 归一成字符串数组：null / 非数组按空，元素统一 String 并保留原顺序（不去重，交由使用方决定）。 */
export function normalizePermissionList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item ?? '')).filter((item) => item.length > 0)
}

export function emptyPermissionLists(): PermissionLists {
  return {
    agents: [],
    datasets: [],
    metadata: [],
    apis: [],
    menus: [],
    elements: [],
    forbidden_tools: [],
    forbidden_commands: [],
    forbidden_skills: [],
    models: [],
  }
}

function emptyInheritedForbidden(): InheritedForbiddenLists {
  return { forbidden_tools: [], forbidden_commands: [], forbidden_skills: [] }
}

export function normalizePermissionLists(raw: unknown): PermissionLists {
  const source = (raw ?? {}) as Record<string, unknown>
  const result = emptyPermissionLists()
  PERMISSION_LIST_KEYS.forEach((key) => {
    result[key] = normalizePermissionList(source[key])
  })
  return result
}

/** 单个列表的「并集 − 直配」：保序去重，空串丢弃，直配里没有的并集项就是「来自角色」。 */
export function diffInheritedIds(
  effective: readonly string[] | null | undefined,
  direct: readonly string[] | null | undefined,
): string[] {
  const directSet = new Set((direct || []).map((item) => String(item ?? '')))
  const seen = new Set<string>()
  const inherited: string[] = []
  ;(effective || []).forEach((item) => {
    const id = String(item ?? '')
    if (!id || directSet.has(id) || seen.has(id)) return
    seen.add(id)
    inherited.push(id)
  })
  return inherited
}

/** 并集 − 直配，保序去重：直配里没有的并集项就是「来自角色」。 */
export function diffInheritedForbidden(
  effective: PermissionLists,
  direct: PermissionLists,
): InheritedForbiddenLists {
  const inherited = emptyInheritedForbidden()
  FORBIDDEN_LIST_KEYS.forEach((key) => {
    inherited[key] = diffInheritedIds(effective?.[key], direct?.[key])
  })
  return inherited
}

/**
 * 该候选是否「已由所属角色禁用」——这类项在用户直配区必须置灰。
 *
 * 后端 `update_user_permissions` 会把「已由角色派生」的项 `continue` 掉（F1 直配/继承
 * 隔离的有意设计：避免把继承项固化成用户直配而无法解绑）。前端若不提示，管理员勾选后
 * 会看到「保存成功、勾选消失」；更麻烦的是角色侧日后解禁时该用户会直接解禁，与
 * 「我专门在这个用户上配过」的预期相反。
 *
 * 判定要连 aliases 一起看：后端按提交的 id 逐个比对派生集合，别名命中同样会被丢弃；
 * 只比 `id` 会让「同时拦截」的别名组漏判。
 */
export function isInheritedForbiddenCandidate(
  id: unknown,
  aliases: readonly unknown[] | null | undefined,
  inherited: readonly unknown[] | null | undefined,
): boolean {
  const inheritedSet = new Set(
    (inherited || []).map((item) => String(item ?? '').trim()).filter(Boolean),
  )
  if (inheritedSet.size === 0) return false
  const ids = [id, ...(aliases || [])]
    .map((item) => String(item ?? '').trim())
    .filter(Boolean)
  return ids.some((item) => inheritedSet.has(item))
}

/**
 * 解析 `GET /users/{id}/permissions` 的响应体（`response.data`，不是 axios 的整个 response）。
 * `direct_permissions` 必须是**对象**才算有效：数组 / 字符串 / null 都按缺失处理。
 */
export function resolveUserPermissionEditState(payload: unknown): UserPermissionEditState {
  const source = (payload ?? {}) as Record<string, unknown>
  const effective = normalizePermissionLists(source.permissions ?? source)
  const directRaw = source.direct_permissions
  const hasDirect = typeof directRaw === 'object' && directRaw !== null && !Array.isArray(directRaw)
  if (!hasDirect) {
    return {
      direct: effective,
      inherited: emptyInheritedForbidden(),
      inheritedMenus: [],
      inheritedElements: [],
      usingEffectiveFallback: true,
    }
  }
  const direct = normalizePermissionLists(directRaw)
  return {
    direct,
    inherited: diffInheritedForbidden(effective, direct),
    inheritedMenus: diffInheritedIds(effective.menus, direct.menus),
    inheritedElements: diffInheritedIds(effective.elements, direct.elements),
    usingEffectiveFallback: false,
  }
}

export interface MenuToggleResult {
  menus: string[]
  elements: string[]
}

/**
 * 菜单勾选级联（纯函数，不修改入参）。
 *
 * 与旧实现的唯一区别：**勾选时跳过已由角色授予的子项**。后端对 menus / elements 不做
 * 角色派生剔除（只对 forbidden_* 做），若把继承项一起写成用户直配，角色侧日后取消该权限，
 * 这个用户仍会保留——而且管理员会误以为是自己配的。
 *
 * 取消时则清掉该菜单下所有直配子项：取消的语义是「这里不再有直配」，继承项本来就不在直配里。
 */
export function applyMenuToggle(
  menus: readonly string[] | null | undefined,
  elements: readonly string[] | null | undefined,
  menuId: string,
  descendantIds: readonly string[] | null | undefined,
  inheritedElements: readonly string[] | null | undefined = [],
  inheritedMenus: readonly string[] | null | undefined = [],
): MenuToggleResult {
  const nextMenus = [...(menus || [])]
  const nextElements = [...(elements || [])]
  const descendants = (descendantIds || []).map((id) => String(id ?? '')).filter(Boolean)
  // MENU_TREE 的子项混有两类 id：`menu:*` 子菜单（用户管理/角色管理/系统配置/审计日志）
  // 与 `element:*` 功能点。它们必须各回各家——旧实现一律推进 elements，导致子菜单
  // 存成 element 记录：勾了父菜单，子菜单仍显示未勾选，后端按 menu 类型校验也查不到。
  const inheritedSet = new Set(
    [...(inheritedElements || []), ...(inheritedMenus || [])]
      .map((id) => String(id ?? ''))
      .filter(Boolean),
  )
  const menuSet = new Set(nextMenus.map((id) => String(id ?? '')))
  const inheritedMenuSet = new Set(
    (inheritedMenus || []).map((id) => String(id ?? '')).filter(Boolean),
  )

  // 视觉上的「已授予」= 菜单直配 / 菜单由角色授予 / 全部子项都已授予（直配或继承）。
  // 三者任一成立时点击都必须是「取消」，否则会出现「看起来已勾选、点一下反而全选」的错位。
  const allChildrenGranted =
    descendants.length > 0 &&
    descendants.every((id) => nextElements.includes(id) || menuSet.has(id) || inheritedSet.has(id))
  const granted = menuSet.has(menuId) || inheritedMenuSet.has(menuId) || allChildrenGranted

  if (granted) {
    const descendantsSet = new Set(descendants)
    return {
      menus: nextMenus.filter((id) => id !== menuId && !descendantsSet.has(id)),
      elements: nextElements.filter((id) => !descendantsSet.has(id)),
    }
  }

  if (!menuSet.has(menuId)) nextMenus.push(menuId)
  descendants.forEach((id) => {
    if (inheritedSet.has(id)) return
    if (id.startsWith("menu:")) {
      if (!nextMenus.includes(id)) nextMenus.push(id)
      return
    }
    if (!nextElements.includes(id)) nextElements.push(id)
  })
  return { menus: nextMenus, elements: nextElements }
}

export interface UserPermissionPayloadOptions {
  /** 当前勾选的知识库；缺省时用直配里的 datasets */
  datasets?: string[] | null
  /** 已在 RAGFlow 失联的知识库（不可再授权，也不该继续留在 payload 里） */
  missingDatasetIds?: string[] | null
}

/**
 * 组装 PUT payload：只含直配项 + 显式补齐的数组字段。
 * 三个禁用键直接来自 `direct`——角色来源项在类型上就没有进入 payload 的通道。
 */
export function buildUserPermissionPayload(
  direct: PermissionLists,
  options: UserPermissionPayloadOptions = {},
): PermissionLists {
  const missing = new Set((options.missingDatasetIds || []).map((id) => String(id)))
  const datasets = (options.datasets ?? direct.datasets ?? []).map((id) => String(id))
  return {
    agents: [...(direct.agents || [])],
    datasets: datasets.filter((id) => !missing.has(id)),
    metadata: [...(direct.metadata || [])],
    apis: [...(direct.apis || [])],
    menus: [...(direct.menus || [])],
    elements: [...(direct.elements || [])],
    forbidden_tools: [...(direct.forbidden_tools || [])],
    forbidden_commands: [...(direct.forbidden_commands || [])],
    forbidden_skills: [...(direct.forbidden_skills || [])],
    models: [...(direct.models || [])],
  }
}

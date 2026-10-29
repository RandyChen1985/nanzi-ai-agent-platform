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

import type { PermissionNode } from '../constants/permissions'

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
 * 菜单自身（入口）的勾选切换（纯函数，不修改入参）。
 *
 * 菜单与功能点**解耦**：点菜单只授予「侧栏入口 / 页面入口」本身，不再自动把整棵子树的
 * 功能点写进直配。这样才能表达「能进这个页面，但页面里所有按钮都不给」这种组合——
 * 后端 `menu:*` 与 `element:*` 本来就是两个独立权限码，级联反而是前端强加的语义。
 *
 * 取消入口时同步清掉该菜单下所有直配子项：没有入口的孤儿功能点既不可见也无从管理，
 * 留在直配里只会在下次编辑时变成看不见的残留。继承项本来就不在直配里，无需处理。
 *
 * 自身由角色授予时不做任何改动：继承项无法在「用户直配」这一侧取消（由 UI 先拦截）。
 */
/**
 * 菜单分组码前缀。
 *
 * 分组（「系统管理」「智能体开发平台」…）在权限树里只是展示容器，没有可授权语义：
 * 一旦 `group:*` 被写进授权表，它既不会让任何菜单变可见，又会让角色权限页的
 * 勾选/半选状态失真。后端保存时同样会丢弃这类 id，这里是第一道拦截。
 */
const MENU_GROUP_ID_PREFIX = 'group:'

const isMenuGroupId = (id: string) => id.startsWith(MENU_GROUP_ID_PREFIX)

export function applyMenuEntryToggle(
  menus: readonly string[] | null | undefined,
  elements: readonly string[] | null | undefined,
  menuId: string,
  descendantIds: readonly string[] | null | undefined,
  inheritedMenus: readonly string[] | null | undefined = [],
  inheritedElements: readonly string[] | null | undefined = [],
): MenuToggleResult {
  const nextMenus = [...(menus || [])]
  const nextElements = [...(elements || [])]
  if (isMenuGroupId(menuId)) {
    // 分组自身不可勾选：直接返回，避免静默写入无意义授权
    return { menus: nextMenus, elements: nextElements }
  }
  const descendants = normalizeIds(descendantIds)
  const inherited = new Set([...normalizeIds(inheritedMenus), ...normalizeIds(inheritedElements)])
  const menuSet = new Set(normalizeIds(nextMenus))
  const elementSet = new Set(normalizeIds(nextElements))

  // 自身已直配（菜单表或误存进 elements 的旧数据）→ 取消；自身由角色授予 → 无操作。
  const selfDirect = menuSet.has(menuId) || elementSet.has(menuId)
  if (!selfDirect && inherited.has(menuId)) {
    return { menus: nextMenus, elements: nextElements }
  }

  if (selfDirect) {
    const descendantsSet = new Set(descendants)
    return {
      menus: nextMenus.filter((id) => id !== menuId && !descendantsSet.has(id)),
      elements: nextElements.filter((id) => id !== menuId && !descendantsSet.has(id)),
    }
  }

  return { menus: [...nextMenus, menuId], elements: nextElements }
}

/**
 * 找出某个权限节点到根节点的**祖先菜单链**（不含自身、不含 `group:*` 分组）。
 *
 * 授权表里只存折平的 id 集合，父子关系只能从权限树反查。深度优先、找到即返回，
 * 让调用方能把「功能点 → 所属页面菜单」这条链一次补齐。
 */
export function getAncestorMenuIds(
  tree: readonly PermissionNode[] | null | undefined,
  targetId: string,
): string[] {
  const want = String(targetId || '').trim()
  if (!want) return []

  const walk = (
    nodes: readonly PermissionNode[],
    trail: readonly string[],
  ): string[] | null => {
    for (const node of nodes || []) {
      if (!node) continue
      const id = String(node.id || '').trim()
      if (id === want) return [...trail]
      const found = walk(node.children || [], id ? [...trail, id] : trail)
      if (found) return found
    }
    return null
  }

  const trail = walk(tree || [], [])
  if (!trail) return []
  // 分组（`group:*`）没有可授权语义，后端会丢弃，这里也不往上传
  return trail.filter((id) => id.startsWith('menu:') && !isMenuGroupId(id))
}

/**
 * 勾选 / 取消单个功能点，并在授予时**补齐它的祖先菜单链**。
 *
 * 只授功能点、不授父菜单是「死权限」：侧栏没有入口，那个功能点永远点不到
 * （用户页/角色页的树里会显示成「仅分组」，看起来像权限没生效）。
 * 取消功能点时不回退父菜单——同一页面下可能还有别的功能点要留，父菜单该不该取消
 * 交给用户自己点，静默回收会造成另一种意外。
 */
export function applyElementToggle(
  menus: readonly string[] | null | undefined,
  elements: readonly string[] | null | undefined,
  elementId: string,
  tree: readonly PermissionNode[] | null | undefined,
  inheritedMenus: readonly string[] | null | undefined = [],
): MenuToggleResult {
  const nextMenus = [...(menus || [])]
  const nextElements = [...(elements || [])]
  const want = String(elementId || '').trim()
  if (!want) return { menus: nextMenus, elements: nextElements }

  const index = nextElements.indexOf(want)
  if (index >= 0) {
    nextElements.splice(index, 1)
    return { menus: nextMenus, elements: nextElements }
  }

  nextElements.push(want)
  const inherited = new Set(normalizeIds(inheritedMenus))
  for (const menuId of getAncestorMenuIds(tree, want)) {
    // 角色已经给了这个菜单就别写成直配：否则角色侧取消后该用户仍保留，
    // 管理员还会以为是自己的配置（同 applyMenuCascadeToggle 的 inherited 口径）。
    if (inherited.has(menuId)) continue
    if (!nextMenus.includes(menuId)) nextMenus.push(menuId)
  }
  return { menus: nextMenus, elements: nextElements }
}

/** 「全选 / 清空」按钮的模式。 */
export type MenuCascadeMode = 'select' | 'clear'

/**
 * 菜单下整棵子树的显式级联（纯函数，不修改入参）。
 *
 * 这是原来「勾菜单 = 全选」那套语义的显式化：只有点菜单行的「全选」按钮才级联，
 * 点复选框只切入口。`select` 同时授予入口（没有入口的功能点没有意义），并**跳过
 * 已由角色授予的子项**——后端对 menus / elements 不做角色派生剔除，把继承项写成
 * 用户直配后，角色侧取消该权限时这个用户仍会保留，管理员还会误以为是自己配的。
 * `clear` 只清后代直配，保留入口本身（取消入口是复选框的职责，两者不混）。
 */
export function applyMenuCascadeToggle(
  menus: readonly string[] | null | undefined,
  elements: readonly string[] | null | undefined,
  menuId: string,
  descendantIds: readonly string[] | null | undefined,
  mode: MenuCascadeMode,
  inheritedMenus: readonly string[] | null | undefined = [],
  inheritedElements: readonly string[] | null | undefined = [],
): MenuToggleResult {
  const nextMenus = [...(menus || [])]
  const nextElements = [...(elements || [])]
  const descendants = normalizeIds(descendantIds)
  // 权限树的子项混有两类 id：`menu:*` 子菜单（用户管理/角色管理/系统配置/审计日志）
  // 与 `element:*` 功能点。它们必须各回各家——旧实现一律推进 elements，导致子菜单
  // 存成 element 记录：勾了父菜单，子菜单仍显示未勾选，后端按 menu 类型校验也查不到。
  const inherited = new Set([...normalizeIds(inheritedMenus), ...normalizeIds(inheritedElements)])
  const descendantsSet = new Set(descendants)

  if (mode === 'clear') {
    return {
      menus: nextMenus.filter((id) => !descendantsSet.has(id)),
      elements: nextElements.filter((id) => !descendantsSet.has(id)),
    }
  }

  // 分组只级联其下子孙，绝不把 `group:*` 自身推进授权集合
  if (
    !isMenuGroupId(menuId)
    && !nextMenus.includes(menuId)
    && !nextElements.includes(menuId)
    && !inherited.has(menuId)
  ) {
    nextMenus.push(menuId)
  }
  descendants.forEach((id) => {
    if (inherited.has(id)) return
    if (id.startsWith('menu:')) {
      if (!nextMenus.includes(id)) nextMenus.push(id)
      return
    }
    if (!nextElements.includes(id)) nextElements.push(id)
  })
  return { menus: nextMenus, elements: nextElements }
}

function normalizeIds(ids: readonly string[] | null | undefined): string[] {
  return (ids || []).map((id) => String(id ?? '')).filter(Boolean)
}

export interface UserPermissionPayloadOptions {
  /** 当前勾选的知识库；缺省时用直配里的 datasets */
  datasets?: string[] | null
  /** 已在 RAGFlow 失联的知识库（不可再授权，也不该继续留在 payload 里） */
  missingDatasetIds?: string[] | null
  /**
   * 其他不该写进 payload 的知识库：目前是「个人私有」。
   * 私有库的授权在判定时会被直接跳过，提交上去只会留下一条永远不生效的记录。
   */
  excludedDatasetIds?: string[] | null
}

/**
 * 组装 PUT payload：只含直配项 + 显式补齐的数组字段。
 * 三个禁用键直接来自 `direct`——角色来源项在类型上就没有进入 payload 的通道。
 */
export function buildUserPermissionPayload(
  direct: PermissionLists,
  options: UserPermissionPayloadOptions = {},
): PermissionLists {
  const excluded = new Set([
    ...(options.missingDatasetIds || []).map((id) => String(id)),
    ...(options.excludedDatasetIds || []).map((id) => String(id)),
  ])
  const datasets = (options.datasets ?? direct.datasets ?? []).map((id) => String(id))
  return {
    agents: [...(direct.agents || [])],
    datasets: datasets.filter((id) => !excluded.has(id)),
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

// ---------------------------------------------------------------- 权限树的授予状态

/** 一组「直配 / 角色继承」集合，供树节点的三态判定使用。 */
export interface PermissionGrantSets {
  menus: readonly string[]
  elements: readonly string[]
  inheritedMenus: readonly string[]
  inheritedElements: readonly string[]
}

const includesAny = (ids: readonly string[] | null | undefined, id: string) =>
  Array.isArray(ids) && ids.includes(id)

/**
 * 节点自身（不含后代）是否已授予：自己直配或由角色授予。
 *
 * 这是复选框「勾上了没有」的唯一依据——菜单入口与功能点解耦后，勾菜单不再等于
 * 勾上它的全部功能点，否则「只给入口」永远无法表达，取消单个功能点也会被父级
 * 的「全选」状态掩盖（旧版父级因此看起来仍是全勾，点一下反而清空全部）。
 */
export function isPermissionNodeSelfGranted(
  node: PermissionNode,
  sets: PermissionGrantSets,
): boolean {
  return (
    includesAny(sets.menus, node.id) ||
    includesAny(sets.elements, node.id) ||
    includesAny(sets.inheritedMenus, node.id) ||
    includesAny(sets.inheritedElements, node.id)
  )
}

/**
 * 节点整棵子树（含自身）是否全部授予。
 *
 * 用于「全选 / 清空」按钮的文案与菜单行的覆盖度展示，**不再**作为复选框的勾选依据。
 */
export function isPermissionNodeGranted(
  node: PermissionNode,
  sets: PermissionGrantSets,
): boolean {
  if (!isPermissionNodeSelfGranted(node, sets)) return false
  return (node.children || []).every((child) => isPermissionNodeGranted(child, sets))
}

/** 节点自己或任意后代是否至少有一项授予（含继承）。 */
function hasAnyGrantIn(node: PermissionNode, sets: PermissionGrantSets): boolean {
  if (isPermissionNodeSelfGranted(node, sets)) return true
  return (node.children || []).some((child) => hasAnyGrantIn(child, sets))
}

/**
 * 节点自身是否「只由角色授予」（自身不在任何直配里）。
 *
 * 只读的边界必须收在**节点自身**：菜单入口由角色授予，不代表它的功能点也不可另配
 * （后端 `menu:*` 与 `element:*` 是两个独立权限码）。旧实现按整棵子树判断，会把
 * 「角色给了入口、想单独补一个按钮」这种正常配置一起锁死。
 */
export function isPermissionNodeSelfInherited(
  node: PermissionNode,
  sets: PermissionGrantSets,
): boolean {
  const selfDirect = includesAny(sets.menus, node.id) || includesAny(sets.elements, node.id)
  if (selfDirect) return false
  return (
    includesAny(sets.inheritedMenus, node.id) || includesAny(sets.inheritedElements, node.id)
  )
}

/** 部分选中：不是全选，但自己或某个后代已授予。 */
export function isPermissionNodePartiallySelected(
  node: PermissionNode,
  sets: PermissionGrantSets,
): boolean {
  const children = node.children || []
  if (children.length === 0) return false
  if (isPermissionNodeGranted(node, sets)) return false
  return hasAnyGrantIn(node, sets)
}

/** 已授予的叶子功能点数量（含角色继承），配 `countLeafElements` 展示「M/N」。 */
export function countGrantedLeafElements(
  node: PermissionNode,
  sets: PermissionGrantSets,
): number {
  return (node.children || []).reduce((total, child) => {
    if (child.id.startsWith('element:')) {
      return total + (isPermissionNodeSelfGranted(child, sets) ? 1 : 0)
    }
    return total + countGrantedLeafElements(child, sets)
  }, 0)
}

/** 叶子功能点数量（子菜单不计数），用于卡片右上角的「N 个功能点」。 */
export function countLeafElements(node: PermissionNode): number {
  return (node.children || []).reduce(
    (total, child) =>
      total + (child.id.startsWith('element:') ? 1 : countLeafElements(child)),
    0,
  )
}

/**
 * 「我的权限」只读树的数据构建。
 *
 * 权限清单页展示的是**用户实际拥有**的权限，而权限树（`GET /menus/catalog`）是全量菜单定义。
 * 所以这里做的是「取交集并保留层级」：只留下自身已授权、或后代有授权的节点。
 *
 * 父级未授权却仍然出现在树里是刻意的：只授 `menu:system:users` 而不授
 * `menu:system` 是合法组合（角色可以只给某个子页面）。此时父级不是权限，
 * 只是归类用的容器，用 `granted: false` 标出来，模板据此渲染成浅灰分组，
 * 避免让人误以为自己拥有该菜单。
 */
import type { PermissionNode } from '../constants/permissions'

export type GrantedPermissionKind = 'menu' | 'element'

export interface GrantedPermissionNode {
  id: string
  label: string
  kind: GrantedPermissionKind
  /** 该节点自身是否在用户的授权清单里（false = 仅作为分组容器出现） */
  granted: boolean
  /** 子树里**自身已授权**的功能点数量，供菜单显示「功能点 n」 */
  grantedElementCount: number
  children: GrantedPermissionNode[]
}

export interface GrantedPermissionView {
  nodes: GrantedPermissionNode[]
  /** 已授权、但权限树未收录的 id：后端新增权限码而树还没同步时不能丢项 */
  unmatchedMenus: string[]
  unmatchedElements: string[]
}

const isEmptyId = (value: unknown): boolean => typeof value !== 'string' || !value.trim()

/**
 * 按权限树的层级把授权 id 折叠成可见树。
 *
 * 纯函数、不修改入参，便于用 Node 直接跑行为断言；标签一律取自传入的树，
 * 未知 id 交给调用方用后端返回的 display_name 兜底。
 */
export function buildGrantedPermissionView(
  tree: PermissionNode[],
  menus: readonly string[],
  elements: readonly string[],
): GrantedPermissionView {
  const grantedMenus = new Set((menus || []).filter((id) => !isEmptyId(id)))
  const grantedElements = new Set((elements || []).filter((id) => !isEmptyId(id)))

  const known = new Set<string>()
  const collectKnown = (nodes: PermissionNode[]) => {
    for (const node of nodes) {
      known.add(node.id)
      collectKnown(node.children || [])
    }
  }
  collectKnown(tree)

  const buildNode = (node: PermissionNode): GrantedPermissionNode | null => {
    const kind: GrantedPermissionKind = node.id.startsWith('element:') ? 'element' : 'menu'
    const children = (node.children || [])
      .map((child) => buildNode(child))
      .filter((child): child is GrantedPermissionNode => child !== null)

    const granted = grantedMenus.has(node.id) || grantedElements.has(node.id)
    // 自身没授权、后代也没授权 → 这一支与本用户无关，整支剪掉
    if (!granted && children.length === 0) return null

    const grantedElementCount = children.reduce(
      (total, child) =>
        total + (child.kind === 'element' && child.granted ? 1 : 0) + child.grantedElementCount,
      0,
    )

    return { id: node.id, label: node.label, kind, granted, grantedElementCount, children }
  }

  return {
    nodes: tree
      .map((node) => buildNode(node))
      .filter((node): node is GrantedPermissionNode => node !== null),
    unmatchedMenus: [...grantedMenus].filter((id) => !known.has(id)),
    unmatchedElements: [...grantedElements].filter((id) => !known.has(id)),
  }
}

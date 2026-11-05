/**
 * 用户模式的侧栏菜单（写死，不读 /api/portal/menus/nav）。
 *
 * 设计背景：动态菜单有 6 个分组、26 个入口，把「智能体调试 / 接口调试台 / 组件调试台」
 * 这类开发者入口也混在里面，普通用户很难一眼找到「新建任务」。
 * 用户模式只保留 4 个入口 + 「更多」，页面本身全部复用现有路由。
 *
 * 权限策略（和动态菜单不同，刻意做了区分）：
 * - `onDenied: 'lock'`：没权限也保留位置、置灰、点击提示联系管理员。
 *   只给「知识库」用——用户需要知道平台有这个能力，否则会以为资料传不上去。
 * - `onDenied: 'hide'`（默认）：没权限直接不渲染，避免点进去被守卫弹回来。
 * - `adminOnly`：只给管理员看（系统管理）。
 *
 * 定时任务刻意不挂权限码：它只列当前用户自己建的任务，走 /dashboard/my-tasks，
 * 属于「人人都有」的能力；而 /dashboard/tasks（任务调度台，能看全平台任务）
 * 仍然要 menu:task_center。
 */

export type UserModeIconKey =
  | "squares_plus"
  | "agent_mgmt"
  | "clock"
  | "knowledge_base"
  | "folder"
  | "widget"
  | "dashboard"
  | "memory"
  | "token_stats"
  | "user_circle"
  | "system"

export interface UserModeMenuChild {
  key: string
  label: string
  path: string
  /** 缺省 = 无需权限 */
  permission?: string
  onDenied?: "lock" | "hide"
  adminOnly?: boolean
}

export interface UserModeMenuItem {
  key: string
  label: string
  icon: UserModeIconKey
  /** page = 普通路由；new-chat = 开一个新会话；group = 展开子菜单 */
  kind: "page" | "new-chat" | "group"
  path?: string
  permission?: string
  onDenied?: "lock" | "hide"
  adminOnly?: boolean
  badge?: string
  children?: UserModeMenuChild[]
  /**
   * 点击父项展开时，若当前不在该组任何子项上，自动进第一个子项。
   * 「专家·技能·连接器」用这个：用户点它就是想去专家中心，多一次点子项是多余的。
   * 「更多」不开：那是个收纳入口，直接跳到工作台会让人意外。
   */
  expandOpensFirst?: boolean
}

/** 带权限判定结果的菜单节点（组件直接渲染这个） */
export interface ResolvedUserModeItem extends UserModeMenuItem {
  locked: boolean
  children?: ResolvedUserModeChild[]
}

export interface ResolvedUserModeChild extends UserModeMenuChild {
  locked: boolean
}

export const USER_MODE_MENU: readonly UserModeMenuItem[] = [
  {
    key: "new-chat",
    label: "新建任务",
    icon: "squares_plus",
    kind: "new-chat",
    path: "/dashboard/chat",
    permission: "menu:ai_chat",
    onDenied: "hide",
  },
  {
    key: "experts",
    label: "专家 · 技能 · 连接器",
    icon: "agent_mgmt",
    kind: "group",
    expandOpensFirst: true,
    children: [
      {
        key: "expert",
        label: "专家",
        path: "/dashboard/agent-management",
        permission: "menu:agent_management",
      },
      {
        key: "skill",
        label: "技能",
        path: "/dashboard/skills",
        permission: "menu:skills_management",
      },
      {
        key: "connector",
        label: "连接器",
        // 连接器面向用户，打开 MCP 工具集页；MCP 服务台（/dashboard/mcp-service）留给专业模式
        path: "/dashboard/mcp",
        permission: "menu:mcp_service",
      },
    ],
  },
  {
    key: "tasks",
    label: "定时任务",
    icon: "clock",
    kind: "page",
    // 自己的任务，人人可见；全平台任务调度台另走 menu:task_center
    path: "/dashboard/my-tasks",
  },
  {
    key: "library",
    label: "知识库",
    icon: "knowledge_base",
    kind: "page",
    path: "/dashboard/knowledge-bases",
    permission: "menu:knowledge_management",
    // 唯一「置灰而不是隐藏」的入口
    onDenied: "lock",
  },
  {
    key: "workspace",
    label: "工作空间",
    icon: "folder",
    kind: "page",
    // 自己的 AI 工作目录：不需要菜单权限
    path: "/dashboard/workspace",
  },
  {
    key: "more",
    label: "更多",
    icon: "widget",
    kind: "group",
    children: [
      { key: "workbench", label: "我的工作台", path: "/dashboard/workbench" },
      {
        key: "token",
        label: "Token 消耗",
        path: "/dashboard/token-stats",
        permission: "menu:token_stats",
      },
      {
        key: "overview",
        label: "系统概览",
        // 概览页本身要 menu:dashboard：没这个权限的账号点了只会被守卫弹回来，
        // 所以按 hide 处理，不在这里露出来。
        path: "/dashboard",
        permission: "menu:dashboard",
      },
      { key: "personal", label: "个人中心", path: "/dashboard/personal" },
    ],
  },
]

export interface ResolveUserModeMenuOptions {
  /** 当前用户拥有的菜单权限码 */
  menus: readonly string[]
  isAdmin?: boolean
}

/**
 * 按权限把菜单「消化」成可直接渲染的结构。
 *
 * 规则：
 * - adminOnly 且不是管理员 → 丢弃
 * - 有 permission 但没权限：onDenied === 'lock' 保留并标记 locked，否则丢弃
 * - group：子项全部被过滤掉时整组丢弃（避免出现一个点了没反应的父项）
 */
export function resolveUserModeMenu(
  options: ResolveUserModeMenuOptions,
  menu: readonly UserModeMenuItem[] = USER_MODE_MENU,
): ResolvedUserModeItem[] {
  const menus = new Set(options.menus || [])
  const isAdmin = Boolean(options.isAdmin)

  const hasPerm = (permission?: string): boolean =>
    !permission || menus.has(permission) || isAdmin

  const resolveChild = (child: UserModeMenuChild): ResolvedUserModeChild | null => {
    if (child.adminOnly && !isAdmin) return null
    if (hasPerm(child.permission)) return { ...child, locked: false }
    if (child.onDenied === "lock") return { ...child, locked: true }
    return null
  }

  const resolved: ResolvedUserModeItem[] = []
  for (const item of menu) {
    if (item.adminOnly && !isAdmin) continue

    if (item.kind === "group") {
      const children = (item.children || [])
        .map(resolveChild)
        .filter((child): child is ResolvedUserModeChild => Boolean(child))
      if (!children.length) continue
      resolved.push({ ...item, locked: false, children })
      continue
    }

    if (hasPerm(item.permission)) {
      resolved.push({ ...item, locked: false })
    } else if (item.onDenied === "lock") {
      resolved.push({ ...item, locked: true })
    }
  }
  return resolved
}

/** 侧栏「任务（n）」列表一次显示多少条 */
export const USER_MODE_TASK_PAGE_SIZE = 6

/** 「更多 → 切换到专业模式」的固定 key，供组件 emit */
export const USER_MODE_SWITCH_MODE_KEY = "switch-mode"

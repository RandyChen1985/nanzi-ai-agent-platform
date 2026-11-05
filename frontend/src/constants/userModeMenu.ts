/**
 * 用户模式的侧栏菜单（写死，不读 /api/portal/menus/nav）。
 *
 * 设计背景：动态菜单有 6 个分组、26 个入口，把「智能体调试 / 接口调试台 / 组件调试台」
 * 这类开发者入口也混在里面，普通用户很难一眼找到「新建会话」。
 * 用户模式只保留 5 个一级入口（新建会话 / 专家·技能·连接器 / 定时任务 / 知识库 /
 * 工作空间）+ 「更多」，页面本身全部复用现有路由。
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
  | "rectangle_stack"
  | "dashboard"
  | "memory"
  | "token_stats"
  | "user_circle"
  | "system"

export interface UserModeMenuChild {
  key: string
  label: string
  path: string
  /** 子项图标（menuIcons 的 key）。有图标时缩进会相应收窄一点 */
  icon?: UserModeIconKey
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
    label: "新建会话",
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
    // 这三项在用户模式下不挂菜单权限：它们是浏览性质（有哪些专家 / 技能 / 连接器），
    // 真正的数据边界在各个页面的资源级过滤上。专业模式仍按 menu:* 控制，
    // 由路由的 `meta.userModeAllowed` 在守卫里区分——见 router/index.ts。
    children: [
      { key: "expert", label: "专家", icon: "agent_mgmt", path: "/dashboard/agent-management" },
      { key: "skill", label: "技能", icon: "skills", path: "/dashboard/skills" },
      // 连接器面向用户，打开 MCP 工具集页；MCP 服务台（/dashboard/mcp-service）留给专业模式
      { key: "connector", label: "连接器", icon: "link", path: "/dashboard/mcp" },
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
      { key: "workbench", label: "我的工作台", icon: "rectangle_stack", path: "/dashboard/workbench" },
      {
        key: "token",
        label: "Token 消耗",
        icon: "token_stats",
        path: "/dashboard/token-stats",
        permission: "menu:token_stats",
      },
      {
        key: "overview",
        label: "系统概览",
        icon: "dashboard",
        // 概览页本身要 menu:dashboard：没这个权限的账号点了只会被守卫弹回来，
        // 所以按 hide 处理，不在这里露出来。
        path: "/dashboard",
        permission: "menu:dashboard",
      },
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

/** 侧栏「会话列表（n）」每页条数：首屏 8 条，滚到底部自动再拉一页 */
export const USER_MODE_TASK_PAGE_SIZE = 8
/** 按视口高度自适应时的上限：再高也没必要一次拉满 */
export const USER_MODE_TASK_PAGE_SIZE_MAX = 20

/** 单条任务行高度（模板里是 h-9 = 36px） */
const TASK_ROW_HEIGHT = 36
/**
 * 侧栏里菜单 / 品牌行 / 用户卡 / 分页提示大约占掉的高度。
 * 只用于把「任务区剩余高度」换算成条数，宁可少算几条也不要多到撑出滚动条。
 */
const SIDEBAR_CHROME_HEIGHT = 520

/**
 * 任务列表每页条数：默认 8 条，高屏按剩余高度多拉几条。
 *
 * 写死 8 条在 1080p / 2K 上会留下大半屏空白，用户得先滚一次才知道还有更多；
 * 这里按视口高度估算可见行数，并夹在 [8, 20] 之间。视口高度拿不到时退回默认值。
 */
export function computeUserModeTaskPageSize(viewportHeight: number): number {
  const height = Number(viewportHeight)
  if (!Number.isFinite(height) || height <= 0) return USER_MODE_TASK_PAGE_SIZE
  const rows = Math.floor((height - SIDEBAR_CHROME_HEIGHT) / TASK_ROW_HEIGHT)
  return Math.min(
    USER_MODE_TASK_PAGE_SIZE_MAX,
    Math.max(USER_MODE_TASK_PAGE_SIZE, rows),
  )
}

/** 「更多 → 切换到专业模式」的固定 key，供组件 emit */
export const USER_MODE_SWITCH_MODE_KEY = "switch-mode"


/**
 * 用户模式能到达的全部路径（一级 + 子项）。
 *
 * 用来判断「切到用户模式时，当前页面还在不在新菜单里」——比如从专业模式的
 * 用户管理页切过来，用户模式根本没有那个入口，应该回落到新建会话页而不是
 * 停在一个「侧栏里找不到自己」的页面上。
 */
export function collectUserModePaths(menu: readonly UserModeMenuItem[] = USER_MODE_MENU): string[] {
  const paths: string[] = []
  for (const item of menu) {
    if (item.path) paths.push(item.path)
    for (const child of item.children || []) paths.push(child.path)
  }
  return paths
}

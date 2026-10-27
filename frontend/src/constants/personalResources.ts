import type { WorkbenchPersonalResource } from "@/types/workbench"

/** 与 app/services/workbench_home_service.py PERSONAL_RESOURCE_DEFS 对齐 */
export const PERSONAL_RESOURCE_DEFS = [
  // 前两项是资源入口：打开工作台最想先知道「我有什么能用」，
  // 所以排在记忆/Token 这些过程性数据之前。点击跳到工作台的资源广场 tab。
  { key: "knowledge", label: "我的知识库", unit: "个", tab: "square" },
  { key: "datasets", label: "我的数据集", unit: "个", tab: "square" },
  { key: "memory", label: "我的记忆", unit: "条", tab: "memory" },
  { key: "tokens", label: "我的 Token", unit: "本月", tab: "tokens" },
  { key: "data", label: "我的数据门户", unit: "份报表", tab: "data" },
  { key: "skills", label: "我的技能", unit: "个", tab: "skills" },
  { key: "mcp", label: "我的 MCP", unit: "个服务", tab: "mcp" },
  { key: "tasks", label: "我的任务", unit: "个", tab: "tasks" },
  { key: "inbox", label: "我的站内消息", unit: "条未读", tab: "inbox" },
] as const

export type PersonalResourceTab = Exclude<(typeof PERSONAL_RESOURCE_DEFS)[number]["tab"], "inbox">

/** 「我的资源」弹层 Tab：不含站内消息（点击改为打开铃铛面板），
 *  也不含广场两项——弹层是嵌入式对话里的浮层，跳不进工作台的资源广场 tab */
export const PERSONAL_RESOURCE_MODAL_TABS = PERSONAL_RESOURCE_DEFS.filter(
  (spec) => spec.key !== "inbox" && spec.tab !== "square",
)

/** Embed 欢迎页资源条不展示：记忆有侧栏入口，数据门户有能力卡入口 */
export const EMBED_WELCOME_HIDDEN_RESOURCE_KEYS = new Set(["memory", "data"])

/** Embed 欢迎页另不展示资源广场两项：嵌入式对话窗口里没有工作台 tab 可跳，
 *  点进去只会落到一个空壳页面 */
export const EMBED_WELCOME_HIDDEN_SQUARE_KEYS = new Set(["knowledge", "datasets"])

/** 工作台资源条不展示：顶栏铃铛已能看站内消息 */
export const WORKBENCH_HIDDEN_RESOURCE_KEYS = new Set(["inbox"])

export const OPEN_PORTAL_INBOX_EVENT = "nanzi:open-portal-inbox"

export function isInboxPersonalResource(item: { key?: string; tab?: string } | null | undefined): boolean {
  return item?.key === "inbox" || item?.tab === "inbox"
}

export function openPortalInboxPanel(): void {
  if (typeof window === "undefined") return
  window.dispatchEvent(new CustomEvent(OPEN_PORTAL_INBOX_EVENT))
}

export function filterEmbedWelcomePersonalResources(
  items: WorkbenchPersonalResource[],
): WorkbenchPersonalResource[] {
  return items.filter(
    (item) =>
      !EMBED_WELCOME_HIDDEN_RESOURCE_KEYS.has(item.key) &&
      !EMBED_WELCOME_HIDDEN_SQUARE_KEYS.has(item.key),
  )
}

export function filterWorkbenchPersonalResources(
  items: WorkbenchPersonalResource[],
): WorkbenchPersonalResource[] {
  return items.filter((item) => !WORKBENCH_HIDDEN_RESOURCE_KEYS.has(item.key))
}

/** home 尚未返回：静默占位，避免误显示「暂时无法获取」 */
export function personalResourcePlaceholderItems(): WorkbenchPersonalResource[] {
  return PERSONAL_RESOURCE_DEFS.map((spec) => ({
    key: spec.key,
    label: spec.label,
    value: 0,
    unit: spec.unit,
    tab: spec.tab,
    status: "empty" as const,
  }))
}

/** home 请求失败：显示 -- / 暂时无法获取 */
export function personalResourceFallbackItems(): WorkbenchPersonalResource[] {
  return PERSONAL_RESOURCE_DEFS.map((spec) => ({
    key: spec.key,
    label: spec.label,
    value: 0,
    unit: spec.unit,
    tab: spec.tab,
    status: "error" as const,
  }))
}

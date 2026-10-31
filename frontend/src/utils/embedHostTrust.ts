/**
 * 嵌入挂件与宿主之间的 postMessage 信任判定（纯函数，供 Node 单测）。
 *
 * 背景：`/embed/*` 以 `frame-ancestors *` 支持被**任意第三方站点** iframe 引用，
 * 而消息协议里带着 `INIT_CONFIG`（token / ticket / business_context）、`CLEAR_SESSION`、
 * `SEND_COMMAND` 这类高危指令。不校验来源时，任何把页面嵌进去的站点都能把会话切到
 * 自己的账号、注入业务上下文（最终进模型提示词）或重置别人的会话。
 *
 * 判定顺序（与前端调用方约定）：
 *   1. `source` 必须是直接父窗口或自身窗口——这一条不依赖任何配置，永远生效；
 *   2. origin 与自身同源 → 信任（门户壳的同源 iframe）；
 *   3. 票据下发了 `allowed_origins` → 必须命中白名单（含 `*`）；
 *   4. 未下发白名单 → 放行但标记 `unverified`，由调用方告警并提示接入方补配置。
 *
 * 第 4 条是刻意的向后兼容：存量接入可能从未传过 `allowed_origins`，直接拒绝会让
 * 所有挂件在白名单落地前失效。真正收紧的做法是后端 `embed_require_allowed_origins`
 * 开关（签发时强制），而不是前端静默改行为。
 */

/** 归一化为 `scheme://host[:port]`；空串/非法值返回空串。 */
export const parseOrigin = (value: string): string => {
  const raw = String(value ?? "").trim()
  if (!raw || raw === "null") return ""
  try {
    return new URL(raw).origin
  } catch {
    return ""
  }
}

export interface HostMessageTrustInput {
  /** `event.origin` */
  origin: string
  /** 调用方比较 `event.source === window.parent || event.source === window` 的结果 */
  fromParentWindow: boolean
  /** 当前页面自身 origin */
  selfOrigin: string
  /** 票据下发的白名单（已归一化，可能为空数组） */
  allowedOrigins: string[]
}

export interface HostMessageTrustResult {
  trusted: boolean
  /** 放行，但来源未经过白名单校验（调用方应告警一次） */
  unverified: boolean
}

export const evaluateHostMessageTrust = (
  input: HostMessageTrustInput,
): HostMessageTrustResult => {
  if (!input.fromParentWindow) return { trusted: false, unverified: false }
  const origin = parseOrigin(input.origin)
  if (!origin) return { trusted: false, unverified: false }

  if (input.selfOrigin && origin === input.selfOrigin) {
    return { trusted: true, unverified: false }
  }

  const allowed = (input.allowedOrigins || [])
    .map((item) => (String(item).trim() === "*" ? "*" : parseOrigin(String(item))))
    .filter(Boolean)

  if (allowed.length > 0) {
    return { trusted: allowed.includes("*") || allowed.includes(origin), unverified: false }
  }

  return { trusted: true, unverified: true }
}

/**
 * 回传宿主时使用的 targetOrigin。
 *
 * 不用 `"*"`：`INIT_SUCCESS` 带用户标识，通配会让「任何嵌了本页的站点」都读得到。
 * 顺序：命中 referrer 的白名单项 → 白名单首项 → referrer → 自身 origin。
 */
/**
 * 回传宿主时使用的 targetOrigin。
 *
 * 不用 `"*"`：`INIT_SUCCESS` 带用户标识，通配会让「任何嵌了本页的站点」都读得到。
 * 顺序：命中 referrer 的白名单项 → 白名单首项 → referrer → `"*"`。
 *
 * ⚠️ 最后一档必须是 `"*"`，**不能**回落到本页自身 origin。父窗口通常是跨域的，
 * 而 `document.referrer` 在宿主设置 `Referrer-Policy: no-referrer`（或部分刷新/沙箱
 * 场景）下为空——此时若把 targetOrigin 填成本页 origin，浏览器会**静默丢弃**整条消息，
 * 表现为 `NANZI_WIDGET_READY` 送不出去、宿主永远不发 INIT_CONFIG、挂件停在骨架屏。
 * 这是「为了安全牺牲可用性」的典型写法，必须用通配兜住。
 */
export const resolveHostTargetOrigin = (input: {
  allowedOrigins: string[]
  referrer: string
}): string => {
  const allowed = (input.allowedOrigins || []).filter(Boolean)
  const referrerOrigin = parseOrigin(input.referrer)

  if (allowed.length > 0 && !allowed.includes("*")) {
    if (referrerOrigin && allowed.includes(referrerOrigin)) return referrerOrigin
    // 白名单只有一项时能确定宿主就是它；**多项**且拿不到 referrer 时无法判断是哪一项，
    // 填首项会把消息发给错误的 origin（浏览器静默丢弃，表现为挂件收不到 INIT_SUCCESS）。
    // 这种「不确定」的情况退回通配，保证送达——通配的代价与「不确定来源」本来就等价。
    if (allowed.length === 1) return allowed[0] ?? "*"
    return "*"
  }
  if (referrerOrigin) return referrerOrigin
  return "*"
}

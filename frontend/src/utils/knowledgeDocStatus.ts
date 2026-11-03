/**
 * 文档解析状态的唯一判定口径。
 *
 * 原先这段逻辑写在 `KnowledgeBaseManagement.vue` 里，文档工作区组件拆出来后
 * 两处都要用——复制一份必然漂移（改了一边忘了另一边，界面上同一个文件在
 * 列表里「解析失败」、在抽屉里「解析完成」）。所以抽成纯函数，两边共用。
 */

export type DocStatus = 'parsed' | 'parsing' | 'failed' | 'unparsed'

export interface DocStatusSource {
  run?: string | number | null
  status?: string | number | null
  enabled?: boolean | number | string | null
}

/**
 * RAGFlow 把解析状态放在 `run` 字段：
 * "0"/UNSTART 未解析、"1"/DONE 完成、"2"/RUNNING 解析中、"3"/FAIL 失败。
 * 只有 `run` 完全缺失时才回落到 `status`——`status` 里的 0/1 表示「启用/停用」，
 * 当成解析状态会得出「停用的文档=未解析」这种错误结论。
 */
export function docStatusOf(doc?: DocStatusSource | null): DocStatus {
  if (!doc) return 'unparsed'

  const run = String(doc.run ?? '').toUpperCase()
  if (run === 'RUNNING' || run === '2') return 'parsing'
  if (run === 'DONE' || run === '1') return 'parsed'
  if (run === 'FAIL' || run === '3') return 'failed'
  if (run === 'UNSTART' || run === '0') return 'unparsed'

  if (doc.run === undefined || doc.run === null) {
    const status = String(doc.status ?? '').toUpperCase()
    if (status === 'COMPLETED' || status === 'PARSED') return 'parsed'
    if (status === 'RUNNING' || status === 'PARSING') return 'parsing'
    if (status === 'FAILED') return 'failed'
  }

  return 'unparsed'
}

export const DOC_STATUS_LABEL: Record<DocStatus, string> = {
  parsed: '解析完成',
  parsing: '解析中',
  failed: '解析失败',
  unparsed: '未解析',
}

/** 列表里用的短标签，比「解析完成」更适合窄列 */
export const DOC_STATUS_SHORT_LABEL: Record<DocStatus, string> = {
  parsed: '已完成',
  parsing: '解析中',
  failed: '解析失败',
  unparsed: '未解析',
}

export function docStatusLabel(doc?: DocStatusSource | null): string {
  return DOC_STATUS_LABEL[docStatusOf(doc)]
}

/**
 * 是否参与检索。RAGFlow 用 enabled=0/1，可能回数字也可能回字符串，
 * 两种都认，否则停用的文档仍显示「参与检索」。
 */
export function isDocEnabled(doc?: DocStatusSource | null): boolean {
  if (!doc) return true
  const enabled = doc.enabled
  return !(enabled === false || enabled === 0 || enabled === '0')
}

/** 状态在界面上的优先级：失败最该被看见，其次解析中 */
export function docStatusRank(doc?: DocStatusSource | null): number {
  switch (docStatusOf(doc)) {
    case 'failed':
      return 0
    case 'parsing':
      return 1
    case 'unparsed':
      return 2
    default:
      return 3
  }
}

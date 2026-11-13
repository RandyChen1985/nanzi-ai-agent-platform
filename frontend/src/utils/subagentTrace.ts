export interface SubagentTraceMeta {
  display_name?: string | null
  agent_name?: string | null
  run_id?: string | null
  parent_trace_id?: string | null
  child_trace_id?: string | null
  stop_reason?: string | null
  tool_filter?: string[] | null
  /** 这一轮是否接着该子代理上一轮的会话继续（后端 resume=true）。 */
  resume?: boolean
  /** 续聊序号（第几次续聊，从 1 起）；非续聊时为 0/缺失。 */
  resume_index?: number
  /**
   * 并行委派批次（`sub_agent_batch_call` 才会带）。
   *
   * 同一批次里几个子代理是**同时**跑的，但界面上原先只是一条条平铺，
   * 看起来像依次执行。头行标「并行 2/3」就能把这个关系表达出来，
   * 不必再套一层分组节点（那会把这几个容器降级成普通子步骤、丢掉它们自己的展开态）。
   */
  batch_id?: string
  batch_index?: number
  batch_total?: number
}

const asNonEmptyString = (value: unknown): string | null => {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  return normalized || null
}

/** 续聊序号归一化：后端可能给 2 / "2" / 0，只有正整数才值得显示。 */
const asBatchIndex = (value: unknown): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  const truncated = Math.trunc(parsed)
  return truncated >= 0 ? truncated : 0
}

const asResumeIndex = (value: unknown): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  const truncated = Math.trunc(parsed)
  return truncated > 0 ? truncated : 0
}

export const normalizeSubagentTraceMeta = (value: unknown): SubagentTraceMeta | undefined => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const source = value as Record<string, unknown>
  const toolFilter = Array.isArray(source.tool_filter)
    ? source.tool_filter.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : null
  const normalized: SubagentTraceMeta = {
    display_name: asNonEmptyString(source.display_name),
    agent_name: asNonEmptyString(source.agent_name),
    run_id: asNonEmptyString(source.run_id),
    parent_trace_id: asNonEmptyString(source.parent_trace_id),
    child_trace_id: asNonEmptyString(source.child_trace_id),
    stop_reason: asNonEmptyString(source.stop_reason),
    tool_filter: toolFilter,
    resume: source.resume === true,
    resume_index: asResumeIndex(source.resume_index),
    batch_id: asNonEmptyString(source.batch_id) ?? undefined,
    // 序号从 0 起：`asResumeIndex` 会把 0 判成空，batch_index 必须用独立的解析。
    batch_index: asBatchIndex(source.batch_index),
    batch_total: asResumeIndex(source.batch_total),
  }
  return Object.values(normalized).some((item) => item !== null && item !== undefined)
    ? normalized
    : undefined
}

export const subagentDisplayName = (meta?: SubagentTraceMeta): string =>
  meta?.display_name || meta?.agent_name || '子代理'

/**
 * 续聊标记：仅在真的续聊且序号有效时输出，首次委派不显示「续聊 #0」。
 *
 * 序号是**本轮请求内**的计数（后端每次请求重建计数），所以跨轮的第二次续聊也
 * 显示 #1。文案写成「本轮续聊」以免被读成「这个子代理历史上第几次被续聊」。
 */
export const subagentResumeLabel = (meta?: SubagentTraceMeta): string =>
  meta?.resume && meta?.resume_index ? ` · 本轮续聊 #${meta.resume_index}` : ''

export const formatSubagentTraceSummary = (meta?: SubagentTraceMeta): string => {
  if (!meta) return ''
  const label = subagentDisplayName(meta)
  const resume = subagentResumeLabel(meta)
  const tools = meta.tool_filter?.length ? ` · 工具：${meta.tool_filter.join('、')}` : ''
  const stopReason = meta.stop_reason ? ` · ${meta.stop_reason}` : ''
  return `${label}${resume}${tools}${stopReason}`
}


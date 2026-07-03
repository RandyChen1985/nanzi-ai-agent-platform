/**
 * Token 数量展示：大数用 K / M / B，小数以千分位显示。
 */

const COMPACT_THRESHOLD = 1_000_000

function trimDecimals(value: number, decimals = 2): string {
  return value.toFixed(decimals).replace(/\.?0+$/, '')
}

export function normalizeTokenCount(value: number | null | undefined): number {
  if (value == null || Number.isNaN(value)) return 0
  return Math.max(0, Math.round(value))
}

/** 完整千分位，如 1,234,567 */
export function formatTokenFull(value: number | null | undefined): string {
  return normalizeTokenCount(value).toLocaleString('zh-CN')
}

/** 紧凑展示：≥1M 用 M，≥1B 用 B，否则千分位 */
export function formatTokenCompact(value: number | null | undefined): string {
  const num = normalizeTokenCount(value)
  if (num >= 1_000_000_000) {
    return `${trimDecimals(num / 1_000_000_000)}B`
  }
  if (num >= 1_000_000) {
    return `${trimDecimals(num / 1_000_000)}M`
  }
  if (num >= 10_000) {
    return `${trimDecimals(num / 1_000, num >= 100_000 ? 0 : 1)}K`
  }
  return formatTokenFull(num)
}

/** 图表坐标轴：与紧凑格式一致 */
export function formatTokenAxis(value: number | null | undefined): string {
  return formatTokenCompact(value)
}

/** 紧凑格式与完整数字不同时，可展示副标题 */
export function shouldShowTokenFullHint(value: number | null | undefined): boolean {
  const num = normalizeTokenCount(value)
  return num >= COMPACT_THRESHOLD
}

/** 输入框下方提示：约合 X M / X B */
export function formatTokenInputHint(value: number | null | undefined): string {
  const num = normalizeTokenCount(value)
  if (num <= 0) return ''
  if (num >= 1_000_000_000) {
    return `约合 ${trimDecimals(num / 1_000_000_000)}B Token（${formatTokenFull(num)}）`
  }
  if (num >= 1_000_000) {
    return `约合 ${trimDecimals(num / 1_000_000)}M Token（${formatTokenFull(num)}）`
  }
  if (num >= 10_000) {
    return `约合 ${trimDecimals(num / 1_000, num >= 100_000 ? 0 : 1)}K Token（${formatTokenFull(num)}）`
  }
  return `共 ${formatTokenFull(num)} Token`
}

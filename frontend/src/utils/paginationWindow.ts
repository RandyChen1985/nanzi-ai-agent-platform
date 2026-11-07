/**
 * 分页的纯逻辑：页码窗口、越界兜底、每页条数记忆。
 *
 * 抽出来的原因和 `utils/pagination.ts` 里 `parsePageInput` 一样：
 * 知识库列表、库内文档列表、聊天资源抽屉都要分页，页码算法各写一份必然漂移
 * （尤其「总页数 ≤ 7 全显示、否则 1 … 当前±1 … 末页」这条规则）。
 *
 * 这里只放纯函数，组件里不留状态——`PaginationBar.vue` 的页码按钮直接由
 * `buildPageWindow` 生成，测试可以不开浏览器就锁住行为。
 */

/** 总页数：0 条也算 1 页，避免界面出现「第 1/0 页」 */
export function totalPagesOf(total: number, pageSize: number): number {
  const size = Math.max(1, Math.floor(Number(pageSize) || 1))
  const count = Math.max(0, Math.floor(Number(total) || 0))
  return Math.max(1, Math.ceil(count / size))
}

/** 把任意页码夹到 [1, totalPages] 内 */
export function clampPage(page: number, totalPages: number): number {
  const max = Math.max(1, Math.floor(Number(totalPages) || 1))
  const value = Math.floor(Number(page) || 1)
  return Math.min(Math.max(1, value), max)
}

/**
 * 页码窗口。
 * - 总页数 ≤ 7：全部显示；
 * - 否则：首尾页 + 当前页前后各一页，中间用 `'…'` 占位。
 * 返回的 `'…'` 是字符串，调用方据此渲染不可点的省略号。
 */
export function buildPageWindow(current: number, totalPages: number): Array<number | '…'> {
  const total = Math.max(1, Math.floor(Number(totalPages) || 1))
  const page = clampPage(current, total)
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }

  const nums = new Set<number>([1, total])
  for (let p = page - 1; p <= page + 1; p += 1) {
    if (p >= 1 && p <= total) nums.add(p)
  }
  const sorted = [...nums].sort((a, b) => a - b)
  const out: Array<number | '…'> = []
  sorted.forEach((n, i) => {
    // noUncheckedIndexedAccess 下 sorted[i-1] 是 number | undefined，
    // 直接参与减法会报 TS2532
    const prev = sorted[i - 1]
    if (prev !== undefined && n - prev > 1) out.push('…')
    out.push(n)
  })
  return out
}

/** 当前页对应的条目区间文案，空列表返回 `0-0` */
export function pageRangeOf(total: number, page: number, pageSize: number): { from: number; to: number } {
  const count = Math.max(0, Math.floor(Number(total) || 0))
  if (!count) return { from: 0, to: 0 }
  const size = Math.max(1, Math.floor(Number(pageSize) || 1))
  const current = clampPage(page, totalPagesOf(count, size))
  const from = (current - 1) * size + 1
  return { from, to: Math.min(count, current * size) }
}

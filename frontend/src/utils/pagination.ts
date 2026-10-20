/**
 * 分页跳转：把用户在页码输入框里敲的内容解析成合法目标页。
 *
 * 聊天日志与审计日志都有几百上千条，只靠上一页/下一页要翻几十次。
 * 解析规则（越界/空/乱输都兜住）收在这里，页面只负责绑定输入框和刷新。
 */

/**
 * @param raw        输入框原始值
 * @param currentPage 当前页（输入非法时保持不动）
 * @param totalPages  总页数（下限 1）
 * @returns 落在 [1, totalPages] 内的目标页
 */
export function parsePageInput(
  raw: unknown,
  currentPage: number,
  totalPages: number,
): number {
  const max = Math.max(1, Math.floor(Number(totalPages) || 1))
  const current = Math.min(Math.max(1, Math.floor(Number(currentPage) || 1)), max)

  // 只取开头的整数部分：'3.7' → 3，'abc' / '' → 无匹配
  const matched = String(raw ?? '').trim().match(/^-?\d+/)
  if (!matched) return current

  const parsed = Number(matched[0])
  if (!Number.isFinite(parsed)) return current

  return Math.min(Math.max(1, Math.floor(parsed)), max)
}

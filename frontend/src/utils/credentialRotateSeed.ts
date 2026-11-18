/**
 * 轮换弹窗「原结构骨架」的落位判定。
 *
 * 背景：结构化凭据的值不可读，所以轮换弹窗的结构是从后端异步取回来的骨架
 * （只有键与类型，没有值）。而弹窗一打开，编辑器会先补一行空白占位行让用户
 * 有地方填——于是骨架回来时表格里**已经有行了**。
 *
 * 曾经的写法是「已有行就不覆盖」，正好把骨架挡在门外：用户看到的是空表，
 * 一个 key 都没有（现场反馈就是「我的 user / pwd 两个 key 丢了」）。
 *
 * 规则：只有「还没被动过的一行空白占位」才允许被顶掉。只要用户动过手
 * （键名或值非空），或者表里已经有多行，就不动——那时覆盖等于吞掉输入。
 */

export interface SeedRowSnapshot {
  key: string
  value: string
}

/** 这一行是否还是「没动过的空白占位」 */
function isUntouchedPlaceholder(row: SeedRowSnapshot | undefined): boolean {
  if (!row) return false
  return !String(row.key ?? '').trim() && !String(row.value ?? '').trim()
}

/**
 * 骨架能不能接管当前表格。
 *
 * @param rows 当前表格里的行（只需 key / value 两个字段）
 * @param initialJson 后端给的骨架 JSON 文本；空串表示还没拿到
 */
export function canAdoptSkeleton(rows: SeedRowSnapshot[], initialJson: string): boolean {
  if (!String(initialJson ?? '').trim()) return false
  if (rows.length === 0) return true
  if (rows.length > 1) return false
  return isUntouchedPlaceholder(rows[0])
}

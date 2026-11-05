/**
 * 行级权限「条件文本 → 规则构建器」的反向解析。
 *
 * 背景：数据权限配置里，每条规则落库的只有一个 `condition` 字符串；可视化编辑器
 * 下方那排下拉框（目标表 / 选择字段 / 操作符 / 过滤值）绑的是临时字段 `_builder_*`，
 * 只在点「+」时单向拼成 SQL 片段追加进去，保存时 `_` 前缀的 key 会被清掉。
 * 所以重新打开配置时构建器永远是空的——这里负责把已有 condition 拆回四个框。
 *
 * 只覆盖「单条简单条件」：`[库.]表.字段 操作符 值`。
 * AND/OR 组合、子查询、函数、BETWEEN、`>=`/`<=`、多行表达式一律返回 null，
 * 调用方保持构建器为空，交给用户手动填——回填做不到无损时宁可不动。
 */

export type RowFilterBuilderParts = {
  table: string
  field: string
  op: string
  value: string
}

/** 与页面 OPERATORS 下拉保持一致的可选操作符。 */
export const SUPPORTED_OPERATOR_VALUES = ['=', '!=', 'IN', 'LIKE', '>', '<'] as const

const IDENT = '[A-Za-z_][A-Za-z0-9_]*'
/** 标识符两侧可能出现的引号/反引号。 */
const Q = '[`"\']'

// 组：1 = 可选的库名前缀，2 = 表名，3 = 字段名，4 = 操作符，5 = 过滤值
const CONDITION_RE = new RegExp(
  '^\\s*(?:' + Q + '?(' + IDENT + ')' + Q + '?\\s*\\.\\s*)?' +
    Q + '?(' + IDENT + ')' + Q + '?\\s*\\.\\s*' +
    Q + '?(' + IDENT + ')' + Q + '?\\s*' +
    '(!=|<>|>=|<=|=|>|<|\\bLIKE\\b|\\bIN\\b)\\s*(.+?)\\s*$',
  'i',
)

/** 把 `<>` 归一成 `!=`，不认识的操作符返回 null。 */
const normalizeOperator = (raw: string): string | null => {
  const upper = raw.toUpperCase()
  if (upper === '<>') return '!='
  if ((SUPPORTED_OPERATOR_VALUES as readonly string[]).includes(upper)) return upper
  return null
}

/**
 * 解析一条行级权限条件。
 * 解析不出「单条、四段式、下拉里选得到」的表达式时返回 null。
 */
export const parseRowFilterCondition = (condition: unknown): RowFilterBuilderParts | null => {
  if (typeof condition !== 'string') return null
  const text = condition.trim()
  if (!text) return null

  const matched = CONDITION_RE.exec(text)
  if (!matched) return null

  const op = normalizeOperator(matched[4])
  if (!op) return null

  const table = (matched[2] || '').trim()
  const field = (matched[3] || '').trim()
  const value = (matched[5] || '').trim()
  if (!table || !field || !value) return null

  // 多条件拼接、注释：交给用户手动拆，不猜
  if (/\b(and|or)\b/i.test(value)) return null
  if (value.includes('--')) return null

  if (op === 'IN') {
    if (!/^\(.*\)$/.test(value)) return null
    // IN 里塞子查询不是「简单条件」
    if (/\bselect\b/i.test(value)) return null
  } else if (/[()]/.test(value)) {
    return null
  }

  return { table, field, op, value }
}

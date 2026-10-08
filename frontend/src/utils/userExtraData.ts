/**
 * 用户「预留扩展数据 (JSON)」在可视化键值对与 JSON 文本之间的互转。
 *
 * 契约：**值一律是字符串**。
 *
 * 历史实现会对看起来像数字/布尔的值做类型推断（`Number(val)` / `true` / `false`），
 * 结果是第三方同步写入的雪花 ID（如 `"116573125898872954882"`）被 `Number()` 改写成
 * `116573125898872960000`（超出 IEEE-754 安全整数范围），既丢了精度又把类型从
 * string 变成 number，保存后再也无法还原。因此这里只做字符串搬运，绝不推断类型。
 *
 * 本模块零运行时依赖：契约测试用 `node --experimental-strip-types` 直接加载它跑行为验证。
 */

export interface ExtraDataPair {
  key: string
  value: string
}

/** 把值转成可读文本：字符串原样返回，其余按 JSON 字面量展示。 */
const stringifyValue = (value: unknown): string => {
  if (typeof value === 'string') return value
  if (value === null) return 'null'
  if (value === undefined) return ''
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value) ?? ''
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/**
 * 可视化键值对 → JSON 文本。
 *
 * - 值一律以字符串写入（引号包裹），不做数字/布尔推断；
 * - key 两端空白裁剪，空 key 忽略；
 * - 没有任何有效键值对时返回空串（与「清空扩展数据」的语义一致）。
 */
export function pairsToExtraDataJson(pairs: ExtraDataPair[] | null | undefined): string {
  const result: Record<string, string> = {}
  for (const pair of pairs || []) {
    const key = String(pair?.key ?? '').trim()
    if (!key) continue
    result[key] = stringifyValue(pair?.value)
  }
  return Object.keys(result).length > 0 ? JSON.stringify(result, null, 2) : ''
}

/** JSON 文本是否是一个合法的 JSON 对象（用于区分「写坏了」与「解析为空对象」）。 */
export function isExtraDataJsonObject(raw: unknown): boolean {
  const data = tryParse(raw)
  return !!data && typeof data === 'object' && !Array.isArray(data)
}

/**
 * JSON 文本 → 可视化键值对。
 *
 * 非法 JSON、非对象 JSON、空文本一律返回空数组而不抛错；
 * 历史遗留的非字符串值（数字/布尔/null/嵌套对象）统一转成字符串展示，
 * 保证可视化面板里不会出现 `NaN`/`[object Object]`。
 */
export function extraDataJsonToPairs(raw: unknown): ExtraDataPair[] {
  const data = tryParse(raw)
  if (!data || typeof data !== 'object' || Array.isArray(data)) return []
  return Object.entries(data as Record<string, unknown>).map(([key, value]) => ({
    key,
    value: stringifyValue(value),
  }))
}

function tryParse(raw: unknown): unknown {
  if (raw === null || raw === undefined) return null
  if (typeof raw !== 'string') return raw
  const text = raw.trim()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

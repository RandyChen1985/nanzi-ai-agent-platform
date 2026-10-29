/**
 * 脚本工具「试运行」参数构造：表单值 → stdin JSON。
 *
 * 为什么单独成文件：这里是纯数据转换，出错方式很具体（整数被当字符串、布尔 false
 * 被当成没填、数组被序列化成 "[a,b]"），而这几种错在界面上看起来都「成功」，
 * 只有脚本收到错误类型才会炸。所以它必须能被 Node 直接跑测试，不能埋在组件里。
 */
import type { SchemaParam } from './toolSchema'

export type PreviewField = SchemaParam

export type BuildResult = { params: Record<string, any>; error: string }

/** 枚举候选项：单选字段直接给 select，数组字段给多选组。 */
export function enumOf(field: PreviewField): string[] | null {
  const raw = field.extra?.enum
  return Array.isArray(raw) && raw.length ? raw.map((item) => String(item)) : null
}

export function itemEnumOf(field: PreviewField): string[] | null {
  const raw = field.extra?.items?.enum
  return Array.isArray(raw) && raw.length ? raw.map((item) => String(item)) : null
}

/** 长文本参数用多行框：单行 input 里贴一段工单文本是种折磨。 */
export function isMultiline(field: PreviewField): boolean {
  if (field.type === 'array' || field.type === 'object') return true
  if (field.type !== 'string') return false
  // 前缀匹配而不是包含匹配：避免 metadata / date 这类名字被误判成长文本，
  // 同时把 data / base64 / payload 这种「事实上的长字符串」也收进来。
  return (
    field.extra?.format === 'textarea' ||
    /^(text|content|body|sql|code|prompt|desc|data|payload|base64|raw)/i.test(field.name) ||
    (field.description || '').length > 40
  )
}

/** 表单初始值：能用默认值/枚举首项就用，剩下的留空等用户填。 */
export function defaultFor(field: PreviewField): any {
  const initial = enumOf(field) ? enumOf(field)![0] : field.defaultValue
  switch (field.type) {
    case 'boolean':
      return String(field.defaultValue) === 'true'
    case 'integer':
    case 'number':
      return initial === '' || initial === undefined ? '' : Number(initial)
    case 'array':
      return itemEnumOf(field) ? [] : (initial ?? '')
    case 'object':
      return initial ?? ''
    default:
      return initial ?? ''
  }
}

export function defaultValues(fields: PreviewField[]): Record<string, any> {
  const values: Record<string, any> = {}
  for (const field of fields) values[field.name] = defaultFor(field)
  return values
}

/** JSON 模式回填表单：把对象里的值按字段类型转成控件能接受的形态。 */
export function valuesFromObject(
  fields: PreviewField[],
  parsed: Record<string, any>,
): Record<string, any> {
  const values: Record<string, any> = {}
  for (const field of fields) {
    if (!(field.name in parsed)) {
      values[field.name] = defaultFor(field)
      continue
    }
    const value = parsed[field.name]
    if (field.type === 'array' || field.type === 'object') {
      values[field.name] = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    } else {
      values[field.name] = value
    }
  }
  return values
}

/**
 * 表单值 → 提交给脚本的参数对象。
 *
 * 三条约定：
 * - 布尔值**永远**提交（false 是有意义的取值，不能被当成「没填」）；
 * - 空白字符串按「没填」处理，必填则报错、非必填则省略（让脚本自己的默认值生效）；
 * - 数组允许「一行一个 / 逗号分隔」，不强迫人写 JSON。
 */
export function buildParamsFromForm(
  fields: PreviewField[],
  values: Record<string, any>,
): BuildResult {
  const params: Record<string, any> = {}
  const missing: string[] = []

  for (const field of fields) {
    const raw = values[field.name]

    if (field.type === 'boolean') {
      params[field.name] = Boolean(raw)
      continue
    }

    if (field.type === 'array' && itemEnumOf(field)) {
      const picked = Array.isArray(raw) ? raw : []
      if (!picked.length) {
        if (field.required) missing.push(field.name)
        continue
      }
      params[field.name] = picked
      continue
    }

    // 纯空白也算没填：与后端 runner 的必填判定保持同一口径
    if (raw === '' || raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim())) {
      if (field.required) missing.push(field.name)
      continue
    }

    if (field.type === 'integer' || field.type === 'number') {
      const numeric = Number(raw)
      if (Number.isNaN(numeric)) {
        return { params, error: `参数 ${field.name} 需要数字` }
      }
      params[field.name] = field.type === 'integer' ? Math.trunc(numeric) : numeric
      continue
    }

    if (field.type === 'array') {
      const text = String(raw).trim()
      try {
        const parsed = JSON.parse(text)
        params[field.name] = Array.isArray(parsed) ? parsed : [parsed]
      } catch {
        params[field.name] = text
          .split(/[\n,，]/)
          .map((item) => item.trim())
          .filter(Boolean)
      }
      continue
    }

    if (field.type === 'object') {
      try {
        params[field.name] = JSON.parse(String(raw))
      } catch {
        return { params, error: `参数 ${field.name} 需要合法 JSON 对象` }
      }
      continue
    }

    params[field.name] = raw
  }

  if (missing.length) {
    return { params, error: `请先填写必填参数：${missing.join('、')}` }
  }
  return { params, error: '' }
}

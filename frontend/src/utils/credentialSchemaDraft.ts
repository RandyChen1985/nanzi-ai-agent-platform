import type { CredentialField, CredentialSchema } from '../composables/useCredentialFields'

export const credentialFieldKinds = {
  string: '文本',
  integer: '整数',
  number: '小数',
  boolean: '布尔',
  enum: '枚举',
  array: '数组（文本）',
  object: '对象（嵌套）',
} as const

export type CredentialFieldKind = keyof typeof credentialFieldKinds
export type CredentialFieldDraft = {
  id: number
  name: string
  spec: CredentialField
  required: boolean
  enumText: string
  children: CredentialFieldDraft[]
}

let nextFieldId = 0

export function credentialFieldKind(field: CredentialField): CredentialFieldKind {
  return field.enum ? 'enum' : field.type
}

export function newCredentialField(
  name = '', field: CredentialField = { type: 'string' }, required = false,
): CredentialFieldDraft {
  const { properties, required: childKeys, ...spec } = field
  return {
    id: ++nextFieldId, name, spec, required, enumText: spec.enum?.join('\n') ?? '',
    children: properties ? credentialFieldRows({ properties, required: childKeys }) : [],
  }
}

export function credentialFieldRows(schema: Pick<CredentialSchema, 'properties' | 'required'>): CredentialFieldDraft[] {
  return Object.entries(schema.properties).map(([name, field]) =>
    newCredentialField(name, field, schema.required?.includes(name)),
  )
}

export function setCredentialFieldKind(row: CredentialFieldDraft, kind: CredentialFieldKind) {
  if (credentialFieldKind(row.spec) === kind) return
  const { title, description, writeOnly } = row.spec
  const spec: CredentialField = { type: kind === 'enum' ? 'string' : kind }
  if (title !== undefined) spec.title = title
  if (description !== undefined) spec.description = description
  if (writeOnly && kind !== 'enum') spec.writeOnly = true
  if (kind === 'array') spec.items = { type: 'string' }
  if (kind === 'enum') spec.enum = []
  row.spec = spec
  row.enumText = ''
  row.children = kind === 'object' ? [newCredentialField()] : []
}

function serializeFields(rows: CredentialFieldDraft[], parent = '') {
  const properties: Record<string, CredentialField> = {}
  const required: string[] = []
  for (const [index, row] of rows.entries()) {
    const name = row.name.trim()
    const path = parent ? parent + '.' + name : name
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(name) || ['credential_account', 'constructor', 'prototype'].includes(name)) {
      const label = parent ? parent + ' 的第 ' + (index + 1) + ' 个子字段' : '第 ' + (index + 1) + ' 个字段'
      throw new Error(label + '需要有效标识，以小写字母开头，可含数字和下划线')
    }
    if (Object.prototype.hasOwnProperty.call(properties, name)) throw new Error('字段标识重复：' + path)
    const field = { ...row.spec }
    if (field.enum) field.enum = row.enumText.split(/\r?\n/).filter(choice => choice.trim())
    if (field.type === 'object') Object.assign(field, serializeFields(row.children, path), { additionalProperties: false })
    properties[name] = field
    if (row.required) required.push(name)
  }
  return { properties, required }
}

export function buildCredentialSchema(schema: CredentialSchema, rows: CredentialFieldDraft[]): CredentialSchema {
  return { ...schema, ...serializeFields(rows) }
}

export function downloadCredentialSchema(schema: CredentialSchema, typeKey: string) {
  const text = JSON.stringify(schema, null, 2)
  const href = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = href
  link.download = (typeKey || 'credential') + '.schema.json'
  link.click()
  URL.revokeObjectURL(href)
}

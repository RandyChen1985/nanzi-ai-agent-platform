import { computed, ref } from 'vue'
import { isAxiosError } from 'axios'
import axios from '@/utils/axios'

export type FieldValue = string | number | boolean | string[] | CredentialValues
export interface CredentialValues {
  [name: string]: FieldValue
}

export type CredentialField = {
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object'
  title?: string
  description?: string
  writeOnly?: boolean
  format?: 'password'
  default?: FieldValue
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  enum?: string[]
  items?: { type: 'string' }
  properties?: Record<string, CredentialField>
  required?: string[]
  additionalProperties?: boolean
}

export type CredentialSchema = {
  type: 'object'
  properties: Record<string, CredentialField>
  required?: string[]
  additionalProperties?: boolean
  title?: string
  description?: string
  $schema?: string
  'x-icon'?: string
}

export type CredentialType = {
  type_key: string
  name: string
  enabled: boolean
  can_create: boolean
  creation_disabled_reason: string | null
  source: 'builtin' | 'custom' | 'legacy'
  revision: number
  latest_version: number
  version: number
  schema: CredentialSchema
}

export type CredentialMeta = {
  credential_format?: 'legacy' | 'structured_v1'
  type_key?: string
  type_version?: number
  account?: string
  value_revision?: number
  extra_fields?: Record<string, CredentialField>
  secret_fields_set?: string[]
}

export function credentialError(error: unknown, fallback: string): string {
  if (!isAxiosError(error)) return fallback
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  const message = error.response?.data?.message
  return typeof message === 'string' ? message : fallback
}

export function isStructuredType(typeKey: string) {
  return !!typeKey && typeKey !== 'scalar' && typeKey !== 'variable'
}

export async function listCredentialTypes(includeDisabled = false): Promise<CredentialType[]> {
  const response = await axios.get('/api/portal/credential-types', { params: { include_disabled: includeDisabled } })
  return response.data.data
}

export async function getCredentialVersion(typeKey: string, version: number): Promise<CredentialType> {
  const response = await axios.get('/api/portal/credential-types/' + encodeURIComponent(typeKey) + '/versions/' + version)
  return response.data.data
}

export function credentialFieldsComplete(
  schema: Pick<CredentialSchema, 'properties' | 'required'>, values: CredentialValues,
): boolean {
  for (const [name, field] of Object.entries(schema.properties)) {
    const value = values[name] ?? field.default
    if (schema.required?.includes(name) && (value === undefined || (typeof value === 'string' && !value.trim()))) {
      return false
    }
    if (field.type === 'object' && value !== undefined) {
      if (!value || typeof value !== 'object' || Array.isArray(value)
        || !credentialFieldsComplete({ properties: field.properties ?? {}, required: field.required }, value)) {
        return false
      }
    }
  }
  return true
}

export function useCredentialFields() {
  const types = ref<CredentialType[]>([])
  const typeKey = ref('')
  const account = ref('')
  const values = ref<Record<string, FieldValue>>({})
  const loading = ref(false)
  const error = ref('')
  const selected = computed(() => types.value.find(item => item.type_key === typeKey.value))
  const structured = computed(() => isStructuredType(typeKey.value))

  function clear() {
    values.value = {}
    account.value = ''
  }

  async function refresh() {
    loading.value = true
    error.value = ''
    types.value = []
    typeKey.value = ''
    clear()
    try {
      types.value = await listCredentialTypes()
      const creatable = types.value.filter(item => item.can_create)
      typeKey.value = creatable.find(item => item.type_key === 'scalar')?.type_key ?? creatable[0]?.type_key ?? ''
    } catch (err) {
      error.value = credentialError(err, '无法加载凭据类型')
    } finally {
      loading.value = false
    }
  }

  function complete() {
    if (!selected.value?.can_create) return false
    if (!structured.value) return true
    return !!account.value.trim() && credentialFieldsComplete(selected.value.schema, values.value)
  }

  function request(legacyValue: string) {
    const type = selected.value
    if (!type?.can_create) throw new Error('请选择可创建的凭据类型')
    if (!structured.value) {
      return { secret_type: type.type_key === 'variable' ? 'variable' : 'credential', value: legacyValue }
    }
    return {
      secret_type: 'credential',
      structured: { type_key: type.type_key, type_version: type.version, account: account.value, fields: values.value },
    }
  }

  return { types, typeKey, account, values, loading, error, selected, structured, refresh, clear, complete, request }
}

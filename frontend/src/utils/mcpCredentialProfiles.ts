import axios from '@/utils/axios'

export type SelectionMode = 'fixed' | 'infer' | 'ask'
export type McpCredentialProfile = {
  revision: number
  profile_id: string
  type_key: string
  type_version: number
  selection_mode: SelectionMode
  mappings: Record<string, string>
  model_result_fields: string[]
}
export type McpModelResultPolicy = {
  revision: number
  model_result_fields: string[]
  stale: boolean
  target: string
}
export type McpCredentialConfig = {
  profile: McpCredentialProfile | null
  stale: boolean
  model_tool_name?: string
  model_result_policy?: McpModelResultPolicy
  parameters: Array<{ name: string; type: string | string[] | null; required: boolean; sensitive: boolean; group_binding_required: boolean }>
}
export type CredentialCandidate = {
  id: string
  name: string
  account: string
  owner_scope: 'user' | 'system'
}
export type McpCredentialCandidates = {
  profile: McpCredentialProfile
  fixed_secret_id: string | null
  items: CredentialCandidate[]
}

/** 每行开始一个查询，也保留 RFC 9535 表达式内部的折行。 */
export function splitMcpResultPaths(text: string): string[] {
  const paths: string[] = []
  let query = ''
  let bracketDepth = 0
  let quote = ''
  let escaped = false

  for (const line of text.split(/\r\n|\r|\n/)) {
    const trimmed = line.trim()
    const startsSegment = trimmed.startsWith('.') || trimmed.startsWith('[')
    const continuation = query.startsWith('$') && (bracketDepth > 0 || quote !== '' || startsSegment)

    if (continuation) {
      query += '\n' + line
    } else if (trimmed) {
      if (query) paths.push(query.trim())
      query = trimmed
      bracketDepth = 0
      quote = ''
      escaped = false
    }
    if (!query.startsWith('$') || (!continuation && !trimmed)) continue

    for (const char of line) {
      if (escaped) {
        escaped = false
      } else if (quote) {
        if (char === '\\') escaped = true
        else if (char === quote) quote = ''
      } else if (char === '"' || char === "'") {
        quote = char
      } else if (char === '[') {
        bracketDepth += 1
      } else if (char === ']') {
        bracketDepth -= 1
      }
    }
  }
  if (query) paths.push(query.trim())
  return paths
}

export function credentialProfileUrl(toolId: string) {
  return '/api/portal/mcp/tools/' + encodeURIComponent(toolId) + '/credential-profile'
}

export async function getMcpCredentialConfig(toolId: string): Promise<McpCredentialConfig> {
  return (await axios.get(credentialProfileUrl(toolId))).data.data
}

export async function saveMcpModelResultFields(toolId: string, fields: string[], policy: McpModelResultPolicy) {
  const url = '/api/portal/mcp/tools/' + encodeURIComponent(toolId) + '/model-result-fields'
  return (await axios.put<{ data: McpModelResultPolicy; warnings?: { code: string; message: string }[] }>(url, {
    model_result_fields: fields,
    expected_revision: policy.revision,
    expected_target: policy.target,
  })).data
}

type CredentialCandidateDraft = Pick<McpCredentialProfile, 'type_key' | 'type_version'>
type DraftCredentialCandidates = Omit<McpCredentialCandidates, 'profile'> & { profile: McpCredentialProfile | null }

export function listMcpCredentialCandidates(toolId: string): Promise<McpCredentialCandidates>
export function listMcpCredentialCandidates(toolId: string, draft: CredentialCandidateDraft): Promise<DraftCredentialCandidates>
export async function listMcpCredentialCandidates(toolId: string, draft?: CredentialCandidateDraft): Promise<DraftCredentialCandidates> {
  return (await axios.get(credentialProfileUrl(toolId) + '/candidates', { params: draft })).data.data
}

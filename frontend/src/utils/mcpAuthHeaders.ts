/**
 * MCP「其他 Header」→ `auth_headers` / `auth_headers_patch` 的序列化规则。
 *
 * 抽成纯函数是为了能直接测这段判定。它以前内联在组件里，要求「值非空」才
 * 写进配置，于是「只绑凭据、不填值」的 Header 被整条丢掉——运行时
 * `apply_mcp_bindings` 只遍历服务配置里已存在的 Header 名，名字不在配置里
 * 绑定就永远查不到，而界面上显示的是「来自凭据 xxx」。
 */

export interface McpHeaderPair {
  key: string
  value: string
  existing?: boolean
  removed?: boolean
  changed?: boolean
}

export interface McpAuthHeaderPlan {
  /** 新建态的完整配置。 */
  headers: Record<string, string>
  /** 编辑态的差量：`null` = 删除，空串 = 保留一个由凭据中心填值的槽位。 */
  patch: Record<string, string | null>
}

/** Authorization 走独立开关，不能混进「其他 Header」（后端会 400）。 */
export const isAuthorizationHeader = (key: string): boolean =>
  String(key || '').trim().toLowerCase() === 'authorization'

export function planMcpAuthHeaders(
  pairs: readonly McpHeaderPair[],
  isBound: (name: string) => boolean,
): McpAuthHeaderPlan {
  const headers: Record<string, string> = {}
  const patch: Record<string, string | null> = {}

  for (const pair of pairs) {
    const key = String(pair?.key ?? '').trim()
    if (!key || isAuthorizationHeader(key)) continue

    // 删除优先：这一行已经被用户删掉了，不能因为还留着绑定就把空串写回去。
    if (pair.existing && pair.removed && pair.changed) {
      patch[key] = null
      continue
    }

    const value = String(pair?.value ?? '').trim()
    // **核心规则**：值空但绑了凭据时仍然保留这个 Header 名。
    // 运行时注入只遍历配置里已存在的 Header 名，名字不在配置里绑定就永远查不到。
    if (!value && !isBound(key)) continue

    headers[key] = value
    // 未落库的行两处都写：新建态读 headers，编辑态新增一行读 patch。
    if (!pair.existing || pair.changed) patch[key] = value
  }

  return { headers, patch }
}

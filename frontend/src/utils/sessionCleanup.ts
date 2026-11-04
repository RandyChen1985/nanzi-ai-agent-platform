/**
 * 登出 / 会话失效时的本地存储清理（零依赖）。
 *
 * 为什么单独成模块：
 * - `utils/axios.ts` 的 401 分支必须清存储，但它不能 import `userSession`：
 *   `userSession → useNavigation → utils/axios` 会形成循环依赖；
 * - 清理范围必须集中在一处。此前 `main.ts` 与 `utils/axios.ts` 各写一份
 *   `removeItem`，两份都漏掉了导航快照、MCP 会话令牌，以及整个 sessionStorage。
 *
 * 只清「属于某个身份」的数据。主题、侧栏折叠、列表视图模式等设备级偏好保留，
 * 否则换个账号整个界面会跳回默认值——那不是安全，是噪音。
 */

/** 侧栏菜单快照的存储键（`useNavigation` 与清理逻辑共用同一常量）。 */
export const NAV_SNAPSHOT_STORAGE_KEY = 'nav_snapshot_v1'

/** 精确匹配：这些键整体属于某个登录身份。 */
const USER_SCOPED_LOCAL_KEYS = [
  'user_info',
  // 历史遗留凭据副本（现版本凭据只走 HttpOnly Cookie，清掉存量即可）
  'api_key',
  'admin_token',
  'yovole_token',
  'yovole_embed_agent_avatar',
  NAV_SNAPSHOT_STORAGE_KEY,
  // MCP 服务台生成的会话令牌：明文 token + 过期时间，换号后不能留给下一个人
  'nanzi_mcp_recent_tokens',
  // 个人工作台固定的智能体
  'workbench_pinned_agent_ids',
  // 数据集能力菜单的折叠状态（按用户操作历史产生）
  'portal_collapsed_cards',
]

/** 前缀匹配：按用户产生的业务偏好 / 通知标记。 */
const USER_SCOPED_LOCAL_PREFIXES = [
  'knowledge_portal_',
  'knowledge_hallucination_check',
  'knowledge_ragflow_',
  'dataset_portal_',
  'nanzi.skillCreatedBanner.notified:',
]

const USER_SCOPED_SESSION_KEYS = ['nanzi.login.visual.initial-slide']

const USER_SCOPED_SESSION_PREFIXES = [
  // 嵌入页（EmbedPersonal）持久化的嵌入会话令牌
  'nzi_embed_personal_session_token',
  // 按用户 ID 记录的密码到期横幅关闭状态
  'dismiss_pwd_banner_',
]

function resolveStorage(kind: 'localStorage' | 'sessionStorage'): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window[kind] ?? null
  } catch {
    // 隐私模式 / 存储被禁用：读 window.localStorage 本身就可能抛错
    return null
  }
}

function matchingKeys(storage: Storage, prefixes: readonly string[]): string[] {
  const matched: string[] = []
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index)
      if (key && prefixes.some((prefix) => key.startsWith(prefix))) {
        matched.push(key)
      }
    }
  } catch {
    // 存储不可用时无需处理
  }
  return matched
}

function purge(
  kind: 'localStorage' | 'sessionStorage',
  keys: readonly string[],
  prefixes: readonly string[],
): void {
  const storage = resolveStorage(kind)
  if (!storage) return
  try {
    for (const key of keys) storage.removeItem(key)
    for (const key of matchingKeys(storage, prefixes)) storage.removeItem(key)
  } catch {
    // 配额 / 隐私模式：清理失败不应让登出流程中断
  }
}

/**
 * 清掉当前浏览器里「属于某个身份」的本地副本。
 *
 * 它不等同于登出：门户会话 Cookie 是 HttpOnly，JS 删不掉，真正的会话失效只能由
 * 后端 `POST /api/portal/auth/logout` 完成（或自然过期）。
 */
export function clearUserScopedStorage(): void {
  purge('localStorage', USER_SCOPED_LOCAL_KEYS, USER_SCOPED_LOCAL_PREFIXES)
  purge('sessionStorage', USER_SCOPED_SESSION_KEYS, USER_SCOPED_SESSION_PREFIXES)
}

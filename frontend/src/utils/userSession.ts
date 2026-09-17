/**
 * 本地会话写入的统一入口。
 *
 * 约定：localStorage.user_info 只保存身份与权限快照，绝不携带 API Key。
 * 因为 API Key 是长期有效凭据，一旦随 user_info 一起落盘，就等于在浏览器里
 * 留下多份可被脚本读取的副本（任何一次 XSS 都能带走）。API Key 只存放在
 * localStorage.api_key，作为明确的单一凭据位置。
 */

type UserInfoLike = Record<string, any>

/** 写入 user_info 快照（自动剔除 API Key 字段），返回真正落盘的字段。 */
export function persistUserInfo(userInfo: UserInfoLike | null | undefined): UserInfoLike | null {
  if (!userInfo) {
    return null
  }

  const snapshot: UserInfoLike = { ...userInfo }
  // 后端字段为 snake_case，同时防御历史遗留的 camelCase 写法
  delete snapshot.api_key
  delete snapshot.apiKey

  localStorage.setItem('user_info', JSON.stringify(snapshot))
  return snapshot
}

/** 写入 API Key 凭据；空值不写入，避免产生 "undefined"/"null" 这类无效凭据。 */
export function persistApiKey(apiKey: unknown): void {
  if (typeof apiKey === 'string' && apiKey.trim()) {
    localStorage.setItem('api_key', apiKey)
  }
}

/** 清空所有本地会话凭据与 Cookie，彻底重置为未登录态。 */
export function clearUserSession(): void {
  localStorage.removeItem('api_key')
  localStorage.removeItem('user_info')
  localStorage.removeItem('admin_token')
  localStorage.removeItem('yovole_token')
  document.cookie = 'admin_token=; path=/; max-age=0; samesite=lax'
}


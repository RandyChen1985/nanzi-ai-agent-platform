/**
 * 本地会话写入的统一入口。
 *
 * 约定：localStorage.user_info 只保存身份与权限快照，绝不携带 API Key。
 * 因为 API Key 是长期有效凭据，一旦随 user_info 一起落盘，就等于在浏览器里
 * 留下多份可被脚本读取的副本（任何一次 XSS 都能带走）。
 *
 * 门户会话凭据现已改由后端下发的 HttpOnly Cookie 承载，不再写入 localStorage；
 * 本模块只负责身份快照与统一的登出清理。
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

  // 顺手抹除旧版本残留在本地的凭据副本：这些键在改造前由登录/嵌入流程写入，
  // 如今已无任何写入方，但已登录的老用户浏览器里可能仍有存量。放在会话落盘的
  // 统一入口清理，可一次覆盖全部调用方（Login / Dashboard / Users / NoPermission）。
  // 注意：此处不动 admin_token Cookie——它是当前有效的会话凭据，由后端下发。
  localStorage.removeItem('api_key')
  localStorage.removeItem('admin_token')
  localStorage.removeItem('yovole_token')

  localStorage.setItem('user_info', JSON.stringify(snapshot))
  return snapshot
}

/** 清空所有本地会话凭据与 Cookie，彻底重置为未登录态。 */
export function clearUserSession(): void {
  localStorage.removeItem('api_key')
  localStorage.removeItem('user_info')
  localStorage.removeItem('admin_token')
  localStorage.removeItem('yovole_token')
  document.cookie = 'admin_token=; path=/; max-age=0; samesite=lax'
}


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
  // 注意：此处不动门户会话 Cookie（portal_session）——它是当前有效的凭据，由后端下发。
  // 下面 'admin_token' 是历史遗留的 **localStorage 键名**，与 Cookie 名无关。
  localStorage.removeItem('api_key')
  localStorage.removeItem('admin_token')
  localStorage.removeItem('yovole_token')

  localStorage.setItem('user_info', JSON.stringify(snapshot))
  return snapshot
}

/** 清空前端可清的本地会话副本（localStorage），重置为「本地无痕」状态。
 *
 * 注意：**这不等同于登出**。门户会话 Cookie（portal_session）是 HttpOnly，JS 既读不到
 * 也写不了，本函数无法清除它；调用后浏览器在服务端仍是登录态。真正的会话失效须由后端
 * `POST /api/portal/auth/logout` 完成（吊销 Redis 会话 + delete_cookie）。
 * 这里也不再写 `document.cookie` 删它——那种赋值是空操作，只会误导排查者。
 */
export function clearUserSession(): void {
  localStorage.removeItem('api_key')
  localStorage.removeItem('user_info')
  // 'admin_token' 是历史遗留的 localStorage 键名，保留原样以清理老版本残留
  localStorage.removeItem('admin_token')
  localStorage.removeItem('yovole_token')
}


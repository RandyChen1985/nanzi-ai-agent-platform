export const formatDate = (dateStr: string) => {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  // Less than 1 minute
  if (diff < 60000) {
    return "刚刚";
  }
  // Less than 1 hour
  if (diff < 3600000) {
    return `${Math.floor(diff / 60000)} 分钟前`;
  }
  // Less than 1 day
  if (diff < 86400000) {
    return `${Math.floor(diff / 3600000)} 小时前`;
  }
  // Less than 7 days
  if (diff < 604800000) {
    return `${Math.floor(diff / 86400000)} 天前`;
  }

  // Otherwise show date
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

/**
 * 「今天 19:47 / 昨天 19:47 / 11-09 19:47」。
 *
 * 后端返回的是带 `Z` 的 UTC ISO（见 `app/api/portal/endpoints/secrets.py::_iso`），
 * `new Date` 会正确转成本地时区。**不要**去掉时区标记后当本地时间解析——那是把
 * UTC 当成本地，在 UTC+8 下整整差 8 小时，19:47 用过的凭据会显示成「今天 11:47」。
 *
 * 兼容老的 `YYYY-MM-DD HH:MM:SS`（空格分隔）：这种字符串没有时区信息，
 * `new Date` 在 Safari 下还会直接返回 Invalid Date，所以先换成 `T`。
 */
export function shortTime(value: string | null | undefined): string {
  if (!value) return '从未'
  const date = new Date(value.includes('T') ? value : value.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return '—'
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  if (sameDay) return `今天 ${hh}:${mm}`
  if (date.toDateString() === yesterday.toDateString()) return `昨天 ${hh}:${mm}`
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${hh}:${mm}`
}

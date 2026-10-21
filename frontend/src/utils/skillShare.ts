/**
 * 个人技能分享的状态文案与配色。
 *
 * 左菜单徽标、「收到的分享」「我的分享」两个面板、分享弹窗共用这一份映射：
 * 此前发布状态在多个组件里各写一套 label/class，改一处漏一处就会文案不一致。
 */

export type SkillShareStatus = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'WITHDRAWN'

export interface SkillShareStatusMeta {
  label: string
  /** 徽标用：浅底 + 深字 */
  badgeClass: string
  /** 圆点用：纯色 */
  dotClass: string
}

export const SHARE_STATUS_META: Record<SkillShareStatus, SkillShareStatusMeta> = {
  PENDING: {
    label: '待接受',
    badgeClass: 'bg-amber-50 text-amber-700 border border-amber-200',
    dotClass: 'bg-amber-500',
  },
  ACCEPTED: {
    label: '已接受',
    badgeClass: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    dotClass: 'bg-emerald-500',
  },
  REJECTED: {
    label: '已拒绝',
    badgeClass: 'bg-gray-100 text-gray-600 border border-gray-200',
    dotClass: 'bg-gray-400',
  },
  WITHDRAWN: {
    label: '已撤回',
    badgeClass: 'bg-gray-100 text-gray-500 border border-gray-200',
    dotClass: 'bg-gray-300',
  },
}

export function shareStatusMeta(status?: string | null): SkillShareStatusMeta {
  const key = (status || '') as SkillShareStatus
  return SHARE_STATUS_META[key] || SHARE_STATUS_META.PENDING
}

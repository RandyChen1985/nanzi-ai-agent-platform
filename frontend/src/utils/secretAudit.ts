/**
 * 凭据变更动作的文案与配色。
 *
 * 变更记录原来所有动作都是一模一样的灰底白字，一屏十几条扫下来分不出「创建」和
 * 「删除」——而「谁把凭据删了」恰恰是这类审计最常被问的问题。
 *
 * 配色按**后果**分档，不是按字面：
 * - 新建/启用：正向，蓝/绿
 * - 修改/停用：中性，灰
 * - 轮换：需要留意（旧值立刻失效），琥珀
 * - 授权/绑定：扩权，紫/青
 * - 撤销/解绑：收权，橙
 * - 删除：不可恢复，红且加重
 *
 * 颜色只是辅助：文案本身已经说明了动作，色觉障碍用户不依赖颜色也能读懂。
 */

export const SECRET_ACTION_LABEL: Record<string, string> = {
  create: '创建',
  update: '修改',
  rotate: '轮换',
  delete: '删除',
  disable: '停用',
  enable: '启用',
  grant: '授权',
  revoke: '撤销授权',
  bind: '绑定',
  unbind: '解绑',
}

/** 兜底留给以后新增、但这里还没登记的动作，不能让标签变成透明底看不见字 */
export const SECRET_ACTION_FALLBACK_STYLE = 'bg-white text-gray-600 ring-gray-200'

export const SECRET_ACTION_STYLE: Record<string, string> = {
  create: 'bg-blue-50 text-blue-700 ring-blue-100',
  update: 'bg-slate-100 text-slate-600 ring-slate-200',
  rotate: 'bg-amber-50 text-amber-700 ring-amber-100',
  // 删除不可恢复，颜色之外再加粗，扫列表时第一眼就能挑出来
  delete: 'bg-rose-50 text-rose-700 ring-rose-200 font-semibold',
  disable: 'bg-gray-100 text-gray-600 ring-gray-200',
  enable: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
  grant: 'bg-violet-50 text-violet-700 ring-violet-100',
  revoke: 'bg-orange-50 text-orange-700 ring-orange-100',
  bind: 'bg-teal-50 text-teal-700 ring-teal-100',
  unbind: 'bg-orange-50 text-orange-600 ring-orange-100',
}

export function secretActionLabel(action: string): string {
  return SECRET_ACTION_LABEL[action] || action
}

export function secretActionStyle(action: string): string {
  return SECRET_ACTION_STYLE[action] || SECRET_ACTION_FALLBACK_STYLE
}
